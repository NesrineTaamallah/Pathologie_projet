"""
Microservice d'extraction d'entités médicales (SEP/EPR).

Toute la logique métier (schémas, prompts, chunking, contexte ciblé, vote,
décomposition AE, fenêtre de récence, vérification ciblée, orchestration)
provient telle quelle du notebook berrasmi-final-v3-9dossiers.ipynb.
Seul l'appel au LLM (section 4 du notebook, ex-chargement local Qwen3-8B
via outlines/transformers) est remplacé par un appel HTTP au serveur Qwen
déjà démarré sur localhost:8003 (même modèle, autre point d'entrée).
"""

import json
import time
import gc
import re
import datetime
import itertools
import unicodedata
import difflib
from pathlib import Path
from collections import Counter

import yaml
import requests
from rapidfuzz import fuzz as rf_fuzz
from jinja2 import Environment
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# CORRECTIF : HAVE_RAPIDFUZZ etait reference (lignes _find_evidence, _similarity)
# mais jamais defini -> NameError des qu'un champ extrait a une vraie valeur
# non-nulle (le bug ne se voit PAS quand tout est null, d'ou son invisibilite
# dans les premiers tests). rapidfuzz est importe sans garde try/except
# au-dessus donc s'il est present (il l'est, cf. pip show), il est utilisable.
HAVE_RAPIDFUZZ = True

app = FastAPI(title="Entités médicales — service d'extraction")

# ---------------------------------------------------------------------------
# Config serveur Qwen local (déjà démarré, port 8003)
# ---------------------------------------------------------------------------
QWEN_API_URL = "http://localhost:8003/v1/chat/completions"
QWEN_MODEL_NAME = "Qwen/Qwen3-8B"   # doit correspondre exactement au nom déclaré côté serveur vLLM/TGI
QWEN_TIMEOUT_S = 120
QWEN_MAX_RETRIES = 3

# CORRECTIF : ces deux constantes étaient utilisées (extraire_table_avec_vote,
# extraire_table_repetee_standard, extraire_liste_ae_deux_etapes, verifier_table)
# mais jamais définies -> NameError silencieusement avalé par les blocs
# "except Exception: occs = []" -> toutes les tables répétées et toutes les
# vérifications ciblées retournaient [] systématiquement, quel que soit le texte.
GREEDY_SAMPLER = "greedy"
REPEATED_SAMPLER = "multinomial"

# ---------------------------------------------------------------------------
# ## 1. Paramètres (section 1 du notebook, sans les chemins Kaggle/OUTPUT_DIR)
# ---------------------------------------------------------------------------
MAX_NEW_TOKENS = 768
MAX_CONTEXTE_TABLE_CHARS = 8000
RETRIEVAL_KEEP_HEAD = 2
RETRIEVAL_KEEP_TAIL = 2
RETRIEVAL_MIN_MATCHES = 2
N_VOTES_REPEATED = 3
VOTE_DEDUP_THRESHOLD = 80
VOTE_TABLES_EPR = {"epr_eeg", "epr_imagerie", "epr_genetique", "epr_type_crise", "epr_frequence_crises"}
VOTE_TABLES_SEP = {"sep_irm", "sep_biologie_lcr"}
RECENCE_FRACTION = 1 / 3
RECENCE_MIN_CHUNKS = 2
VERIFICATION_TABLES_EPR = {"epr_eeg", "epr_imagerie", "epr_frequence_crises", "epr_type_crise", "epr_genetique"}
VERIFICATION_TABLES_SEP = set()

# ## 2. Config — schémas d'extraction (SEP : 11 tables, EPR : 18 tables)
# 
# Inchangé par rapport au notebook précédent : `mots_cles` (contexte ciblé, point 4)
# et `priorite_recente` (point 8) restent la source de vérité utilisée aussi par les
# nouvelles briques (sections 8-11) — aucune duplication de config.



SEP_SCHEMA_YAML = """
# =============================================================================
# Schema d'extraction NER - Registre SEP pediatrique
# Source : Questions_REGISTRE_IA.docx (Tableau 1) + dictionnaire_donnees_registres_v2.md
# NE PAS extraire : age_diagnostic_mois, delai_diagnostic_mois (colonne generee),
#                    age, date_inclusion (saisie manuelle / administrative)
# Convention valeurs : null = non mentionne | "NA" = explicitement non applicable
#                       | false/"Non" = negation clinique confirmee
# =============================================================================

- table: sep_identification_clinique
  repetee: false
  max_tokens: 400
  description: Identification clinique de base du patient SEP
  mots_cles:
  - âgé
  - âgée
  - ans,
  - adolescent
  - adolescente
  - patient
  - patiente
  - sexe
  - garçon
  - fille
  - gouvernorat
  - originaire
  priorite_recente: false
  champs:
  - na_possible: false
    nom: age_premier_symptome_mois
    type: numerique
    bounds:
    - 0
    - 216
  - na_possible: false
    nom: sexe
    type: categoriel
    valeurs:
    - M
    - F
  - description: Nom de lieu/gouvernorat mentionné tel quel dans le texte, si présent (sera résolu vers
      gouvernorats_reference en post-traitement)
    na_possible: true
    nom: gouvernorat_mention_texte
    type: texte
  exemples:
  - type: positif
    texte: Il s'agit d'un patient âgé de 15 ans... facteur de mauvais pronostic le sexe masculin.
    sortie:
      age_premier_symptome_mois: 'null'
      sexe: M
      gouvernorat_mention_texte: 'null'
  - type: negatif
    texte: Admission d'un patient pour bilan neurologique. Aucune précision d'âge ni de sexe dans ce paragraphe.
    sortie:
      age_premier_symptome_mois: 'null'
      sexe: 'null'
      gouvernorat_mention_texte: 'null'
- table: sep_antecedents
  repetee: false
  max_tokens: 500
  description: Antécédents familiaux et personnels avant le 1er épisode
  mots_cles:
  - antécédent
  - antécédents
  - familia
  - familiaux
  - consanguin
  - consanguinité
  - vaccination
  - vacciné
  - infection
  - grippal
  - rhinorrhée
  - syndrome grippal
  priorite_recente: false
  champs:
  - na_possible: false
    nom: atcd_familiaux_auto_immuns_neuro
    type: booleen
  - description: Premier degré vs autre, si atcd_familiaux_auto_immuns_neuro = true
    na_possible: true
    nom: atcd_familiaux_precision
    type: texte
  - na_possible: false
    nom: consanguinite_parentale
    type: booleen
  - na_possible: true
    nom: consanguinite_degre
    type: texte
  - description: Facteur déclenchant potentiel (checklist en texte libre)
    na_possible: true
    nom: infections_vaccinations_avant_1er_episode
    type: texte
  exemples:
  - type: positif
    texte: Pas d'antécédents familiaux de pathologie neurologique ou psychiatrique. Issu d'un mariage
      consanguin.
    sortie:
      atcd_familiaux_auto_immuns_neuro: 'false'
      atcd_familiaux_precision: NA
      consanguinite_parentale: 'true'
      consanguinite_degre: 'null'
      infections_vaccinations_avant_1er_episode: 'null'
  - type: negatif
    texte: Le dossier ne mentionne aucun antécédent familial ou personnel.
    sortie:
      atcd_familiaux_auto_immuns_neuro: 'null'
      atcd_familiaux_precision: 'null'
      consanguinite_parentale: 'null'
      consanguinite_degre: 'null'
      infections_vaccinations_avant_1er_episode: 'null'
- table: sep_presentation_initiale
  repetee: false
  max_tokens: 350
  description: Type et évolution du tout premier événement clinique
  mots_cles:
  - histoire de la maladie
  - premier épisode
  - 1er épisode
  - 1ère poussée
  - première poussée
  - installation
  - remonte à
  - début de la maladie
  priorite_recente: false
  champs:
  - na_possible: false
    nom: type_premier_evenement
    type: categoriel
    valeurs:
    - névrite optique
    - myélite
    - tronc cérébral
    - polysymptomatique
    - autre
  - na_possible: false
    nom: recuperation_complete
    type: categoriel
    valeurs:
    - Oui
    - Non
    - Partielle
  exemples:
  - type: positif
    texte: Paresthésie hémicorps droit avec aggravation progressive, amélioration au bout de 8 jours puis
      récidive.
    sortie:
      type_premier_evenement: polysymptomatique
      recuperation_complete: Partielle
  - type: negatif
    texte: Le compte-rendu ne décrit pas le tout premier événement clinique du patient.
    sortie:
      type_premier_evenement: 'null'
      recuperation_complete: 'null'
- table: sep_poussees
  repetee: true
  max_tokens: 750
  description: Chaque poussée documentée dans le dossier (table longitudinale)
  mots_cles:
  - poussée
  - poussées
  - récidive
  - aggravation
  - rechute
  - bolus
  - corticoïde
  - corticoïdes
  - méthylprednisolone
  - solumédrol
  - solumédrole
  priorite_recente: false
  champs:
  - na_possible: false
    nom: date_poussee
    type: date
  - na_possible: false
    nom: type_localisation
    type: texte
  - na_possible: false
    nom: traitement_poussee
    type: categoriel
    valeurs:
    - corticoïdes
    - échanges plasmatiques
    - autre
    - aucun
  - na_possible: false
    nom: sequelle_post_poussee
    type: booleen
  - description: NA si sequelle_post_poussee = false
    na_possible: true
    nom: edss_associe
    type: numerique
    bounds:
    - 0
    - 10
  exemples:
  - type: positif
    texte: Histoire de la maladie remonte au 14 mai 2026. Bolus de solumédrole débuté le 30 mai 2026.
      Score EDSS à la sortie à 2,5, hémiparésie droite persistante.
    sortie:
    - date_poussee: '2026-05-14'
      type_localisation: polysymptomatique (sensitivo-motrice)
      traitement_poussee: corticoïdes
      sequelle_post_poussee: 'true'
      edss_associe: '2.5'
  - type: negatif
    texte: Patient suivi pour SEP depuis 2020, traitement stable par interféron, aucune nouvelle poussée
      rapportée.
    sortie: []
- table: sep_edss_visites
  repetee: true
  max_tokens: 450
  description: Score EDSS à chaque visite mentionnée (courbe évolutive)
  mots_cles:
  - EDSS
  priorite_recente: false
  champs:
  - na_possible: false
    nom: date_visite
    type: date
  - description: 0 à 10
    na_possible: false
    nom: score_edss
    type: numerique
    bounds:
    - 0
    - 10
  exemples:
  - type: positif
    texte: Score EDSS A6 à l'admission le 30 mai 2026. Score EDSS à la sortie à 2,5 le 3 juin 2026.
    sortie:
    - date_visite: '2026-05-30'
      score_edss: '6'
    - date_visite: '2026-06-03'
      score_edss: '2.5'
  - type: negatif
    texte: Le dossier ne rapporte aucun score EDSS chiffré.
    sortie: []
- table: sep_evolution
  repetee: false
  max_tokens: 400
  description: Forme évolutive globale de la maladie
  mots_cles:
  - forme évolutive
  - rémitante
  - récurrente
  - remittante
  - secondairement progressive
  - SEP-RR
  - SEP RR
  - hautement active
  - agressive
  - conversion
  - progressive d'emblée
  priorite_recente: true
  champs:
  - na_possible: false
    nom: forme_evolutive
    type: categoriel
    valeurs:
    - RR
    - SP
    - progressive d'emblée
  - na_possible: true
    nom: severite
    type: categoriel
    valeurs:
    - Hautement active
    - agressive
    - standard
  - description: NA si le patient reste en forme RR pure
    na_possible: true
    nom: date_conversion_sp
    type: date
  exemples:
  - type: positif
    texte: Sclérose en plaques rémitantes récurrentes, indication à un traitement de première ligne.
    sortie:
      forme_evolutive: RR
      severite: 'null'
      date_conversion_sp: NA
  - type: negatif
    texte: Diagnostic de maladie inflammatoire du SNC en cours d'exploration, forme évolutive non encore
      statuée.
    sortie:
      forme_evolutive: 'null'
      severite: 'null'
      date_conversion_sp: 'null'
- table: sep_irm
  repetee: true
  max_tokens: 1300
  description: Chaque IRM cérébro-médullaire réalisée
  mots_cles:
  - IRM
  - scanner
  - gadolinium
  - gado
  - T1
  - T2
  - FLAIR
  - flair
  - hypersignal
  - lésion
  - lésions
  - black hole
  - cérébro-médullaire
  - cérébro-médulaire
  priorite_recente: false
  champs:
  - na_possible: false
    nom: date_examen
    type: date
  - na_possible: true
    nom: nb_lesions_t2
    type: numerique
    bounds:
    - 0
    - 100
  - description: Citer le passage du compte-rendu IRM tel quel (extractif, pas de reformulation)
    na_possible: false
    nom: cr_irm_texte
    type: texte
  - na_possible: false
    nom: localisation_peri_ventriculaire
    type: booleen
  - na_possible: false
    nom: localisation_juxta_corticale
    type: booleen
  - na_possible: false
    nom: localisation_sous_tentorielle
    type: booleen
  - na_possible: false
    nom: localisation_moelle
    type: booleen
  - na_possible: false
    nom: prise_contraste_gd
    type: booleen
  - description: NA si prise_contraste_gd = false
    na_possible: true
    nom: nb_lesions_rehaussees
    type: numerique
    bounds:
    - 0
    - 100
  - description: NA si 1ère IRM du dossier
    na_possible: true
    nom: nouvelles_lesions_vs_irm_anterieure
    type: booleen
  - na_possible: false
    nom: atrophie_cerebrale_medullaire
    type: booleen
  exemples:
  - type: positif
    texte: IRM cérébro-médullaire faite le 29 mai 2026 montrant des anomalies périventriculaires et pontiques,
      prise de contraste homogène au gadolinium. Étage médullaire sans anomalie.
    sortie:
    - date_examen: '2026-05-29'
      nb_lesions_t2: 'null'
      cr_irm_texte: anomalies périventriculaires et pontiques, prise de contraste homogène
      localisation_peri_ventriculaire: 'true'
      localisation_juxta_corticale: 'null'
      localisation_sous_tentorielle: 'true'
      localisation_moelle: 'false'
      prise_contraste_gd: 'true'
      nb_lesions_rehaussees: 'null'
      nouvelles_lesions_vs_irm_anterieure: NA
      atrophie_cerebrale_medullaire: 'null'
  - type: negatif
    texte: Aucune imagerie cérébrale n'a été réalisée à ce jour pour ce patient.
    sortie: []
- table: sep_biologie_lcr
  repetee: true
  max_tokens: 850
  description: Résultats biologiques du LCR (ponction lombaire) et anticorps sanguins associés
  mots_cles:
  - LCR
  - ponction lombaire
  - 'PL '
  - bandes oligoclonales
  - index IgG
  - IEPP
  - kappa
  - anticorps
  - MOG
  - AQP4
  - anti-nucléaire
  - iso-électrofocalisation
  - isofocalisation
  priorite_recente: false
  champs:
  - na_possible: false
    nom: date_prelevement
    type: date
  - na_possible: true
    nom: bandes_oligoclonales
    type: categoriel
    valeurs:
    - Positif
    - Négatif
  - na_possible: true
    nom: index_chaines_kappa
    type: categoriel
    valeurs:
    - Positif
    - Négatif
  - na_possible: true
    nom: index_igg
    type: numerique
    bounds:
    - 0
    - 50
  - na_possible: true
    nom: anticorps_type
    type: categoriel
    valeurs:
    - NMO-IgG/MOG
    - AQP4
    - AAN
    - autres
    - aucun
  - description: Rempli seulement si anticorps_type = autres
    na_possible: true
    nom: anticorps_autre_texte
    type: texte
  exemples:
  - type: positif
    texte: Étude du LCR le 30 mai 2026. Résultat des anti-MOG, sang et LCR négatifs, anti-aquaporine 4
      négatif.
    sortie:
    - date_prelevement: '2026-05-30'
      bandes_oligoclonales: 'null'
      index_chaines_kappa: 'null'
      index_igg: 'null'
      anticorps_type: autres
      anticorps_autre_texte: anti-MOG et anti-AQP4 négatifs
  - type: negatif
    texte: Aucune ponction lombaire n'a été réalisée pour ce patient.
    sortie: []
- table: sep_potentiels_evoques
  repetee: true
  max_tokens: 750
  description: Potentiels évoqués (visuels, somesthésiques, auditifs)
  mots_cles:
  - potentiel évoqué
  - potentiels évoqués
  - PEV
  - PES
  - PEA
  priorite_recente: false
  champs:
  - na_possible: false
    nom: date_examen
    type: date
  - na_possible: true
    nom: pev
    type: categoriel
    valeurs:
    - Normal
    - Anormal
  - na_possible: true
    nom: pes
    type: categoriel
    valeurs:
    - Normal
    - Anormal
  - na_possible: true
    nom: pea
    type: categoriel
    valeurs:
    - Normal
    - Anormal
  - description: NA si tous les PE sont normaux
    na_possible: true
    nom: anomalie_texte
    type: texte
  exemples:
  - type: positif
    texte: Potentiels évoqués visuels normaux.
    sortie:
    - date_examen: 'null'
      pev: Normal
      pes: NA
      pea: NA
      anomalie_texte: NA
  - type: negatif
    texte: Aucun potentiel évoqué n'a été demandé pour ce patient.
    sortie: []
- table: sep_traitement_fond
  repetee: true
  max_tokens: 900
  description: Chaque ligne de traitement de fond prescrite (molécule, dates, switch)
  mots_cles:
  - traitement de fond
  - interféron
  - avonex
  - glatiramer
  - glatiramère
  - glitaxone
  - rituximab
  - natalizumab
  - fingolimod
  - switch
  - 1ère ligne
  - 2ème ligne
  - anti-CD20
  - tysabri
  priorite_recente: false
  champs:
  - description: ex. interféron, natalizumab, fingolimod, glatiramère, rituximab...
    na_possible: false
    nom: molecule
    type: texte
  - na_possible: true
    nom: ligne_therapeutique
    type: categoriel
    valeurs:
    - 1ère ligne
    - 2ème ligne
    - 3ème ligne
  - na_possible: false
    nom: date_debut
    type: date
  - description: NA si traitement toujours en cours
    na_possible: true
    nom: date_fin
    type: date
  - na_possible: true
    nom: motif_switch
    type: categoriel
    valeurs:
    - échec
    - effet indésirable
    - choix
    - aucun
  - na_possible: true
    nom: effets_indesirables
    type: texte
  - na_possible: true
    nom: observance
    type: categoriel
    valeurs:
    - Oui
    - Non
    - Partielle
  exemples:
  - type: positif
    texte: Décision de mettre le patient sous acétate de glatiramer en traitement de première ligne.
    sortie:
    - molecule: acétate de glatiramère
      ligne_therapeutique: 1ère ligne
      date_debut: 'null'
      date_fin: NA
      motif_switch: NA
      effets_indesirables: 'null'
      observance: 'null'
  - type: negatif
    texte: Le patient n'a reçu aucun traitement de fond à ce jour.
    sortie: []
- table: sep_suivi
  repetee: false
  max_tokens: 550
  description: Statut au dernier suivi documenté dans le dossier
  mots_cles:
  - suivi
  - consultation
  - statut
  - perdu de vue
  - décédé
  - impact scolaire
  - cognitif
  - scolarisé
  - scolarité
  - stable
  - dernière consultation
  - dernier bilan
  priorite_recente: true
  champs:
  - na_possible: false
    nom: date_dernier_suivi
    type: date
  - na_possible: false
    nom: statut_dernier_suivi
    type: categoriel
    valeurs:
    - Stable
    - Perdu de vue
    - Décédé
  - na_possible: true
    nom: score_edss_dernier
    type: numerique
    bounds:
    - 0
    - 10
  - na_possible: false
    nom: impact_scolaire_cognitif
    type: booleen
  - na_possible: true
    nom: impact_precision
    type: texte
  - description: NA si non testable selon l'âge
    na_possible: true
    nom: score_cognitif
    type: numerique
    bounds:
    - 0
    - 200
  exemples:
  - type: positif
    texte: 'Examen à la sortie : hémiparésie droite, stable. Score EDSS à la sortie à 2,5.'
    sortie:
      date_dernier_suivi: 'null'
      statut_dernier_suivi: Stable
      score_edss_dernier: '2.5'
      impact_scolaire_cognitif: 'null'
      impact_precision: 'null'
      score_cognitif: 'null'
  - type: negatif
    texte: Aucune information de suivi n'est disponible dans ce dossier.
    sortie:
      date_dernier_suivi: 'null'
      statut_dernier_suivi: 'null'
      score_edss_dernier: 'null'
      impact_scolaire_cognitif: 'null'
      impact_precision: 'null'
      score_cognitif: 'null'
"""



EPR_SCHEMA_YAML = """
# =============================================================================
# Schema d'extraction NER - Registre epilepsie pharmacoresistante (EPR)
# Source : Questions_REGISTRE_IA.docx (Tableau 2) + dictionnaire_donnees_registres_v2.md
# NE PAS extraire : age_diagnostic_pharmacoresistance_mois (saisie manuelle clinicien),
#                    frequence_normalisee_mois (colonne generee), nb_ae_essayes (vue calculee)
# epr_regression_developpementale N'EST PAS dans le docx source -> ecartee du pipeline.
# Convention valeurs : null = non mentionne | "NA" = explicitement non applicable
#                       | false/"Non" = negation clinique confirmee
# =============================================================================

- table: epr_identification_clinique
  repetee: false
  max_tokens: 300
  description: Identification clinique de base du patient épileptique
  mots_cles:
  - âgé
  - âgée
  - ans,
  - âge de début
  - début des crises
  - début à
  - premier signe
  priorite_recente: false
  champs:
  - na_possible: false
    nom: age_debut_crises_mois
    type: numerique
    bounds:
    - 0
    - 216
  exemples:
  - type: positif
    texte: L'histoire de la maladie remonte à janvier 2020, soit à l'âge de 1 an et 9 mois.
    sortie:
      age_debut_crises_mois: '21'
  - type: negatif
    texte: Le dossier ne précise pas l'âge de début des crises.
    sortie:
      age_debut_crises_mois: 'null'
- table: epr_antecedents
  repetee: false
  max_tokens: 600
  description: Antécédents périnataux, familiaux et développementaux avant les crises
  mots_cles:
  - antécédent
  - antécédents
  - périnatal
  - périnataux
  - prématur
  - souffrance
  - consanguin
  - consanguinité
  - familia
  - développement psychomoteur
  - grossesse
  - accouchement
  priorite_recente: false
  champs:
  - na_possible: false
    nom: atcd_perinataux
    type: booleen
  - description: souffrance, prématurité...
    na_possible: true
    nom: atcd_perinataux_precision
    type: texte
  - na_possible: false
    nom: consanguinite_parentale
    type: booleen
  - na_possible: true
    nom: consanguinite_degre
    type: texte
  - na_possible: false
    nom: atcd_familiaux_epilepsie
    type: booleen
  - description: lien de parenté précis
    na_possible: true
    nom: atcd_familiaux_lien
    type: texte
  - na_possible: false
    nom: developpement_psychomoteur_avant_crises
    type: categoriel
    valeurs:
    - Normal
    - Retard
  exemples:
  - type: positif
    texte: Issu d'un mariage consanguin lointain, les grands-parents sont cousins. Épilepsie chez un cousin
      paternel. Développement psychomoteur normal jusqu'à l'âge de 1 an et 9 mois.
    sortie:
      atcd_perinataux: 'false'
      atcd_perinataux_precision: 'null'
      consanguinite_parentale: 'true'
      consanguinite_degre: lointain (grands-parents cousins)
      atcd_familiaux_epilepsie: 'true'
      atcd_familiaux_lien: cousin paternel
      developpement_psychomoteur_avant_crises: Normal
  - type: negatif
    texte: Aucun antécédent familial ou périnatal n'est rapporté dans ce dossier.
    sortie:
      atcd_perinataux: 'null'
      atcd_perinataux_precision: 'null'
      consanguinite_parentale: 'null'
      consanguinite_degre: 'null'
      atcd_familiaux_epilepsie: 'null'
      atcd_familiaux_lien: 'null'
      developpement_psychomoteur_avant_crises: 'null'
- table: epr_type_crise
  repetee: true
  max_tokens: 650
  description: Type sémiologique de chaque crise décrite (peut évoluer dans le temps)
  mots_cles:
  - crise
  - crises
  - focale
  - généralisée
  - tonico-clonique
  - spasme
  - absence
  - myoclon
  - atonique
  - sémiologie
  priorite_recente: false
  champs:
  - na_possible: false
    nom: date_observation
    type: date
  - na_possible: false
    nom: type_crise_ilae2017
    type: categoriel
    valeurs:
    - Focale
    - Généralisée
    - Inconnue
  - description: ex. tonique, atonique, myoclonique, absence...
    na_possible: true
    nom: sous_type
    type: texte
  - description: fièvre, privation de sommeil, vaccination...
    na_possible: true
    nom: facteurs_declenchants
    type: texte
  exemples:
  - type: positif
    texte: Installation d'une rupture de conscience brève avec hypotonie des 4 membres et confusion post-critique,
      dans un contexte d'apyrexie.
    sortie:
    - date_observation: 'null'
      type_crise_ilae2017: Focale
      sous_type: altération de conscience, hypotonie, confusion post-critique
      facteurs_declenchants: 'null'
  - type: negatif
    texte: Le dossier ne décrit pas la sémiologie des crises.
    sortie: []
- table: epr_frequence_crises
  repetee: true
  max_tokens: 850
  description: Chaque fréquence de crises rapportée en consultation (courbe évolutive)
  mots_cles:
  - fréquence
  - crises par
  - crises/jour
  - crises/mois
  - crises/semaine
  - quotidienne
  - hebdomadaire
  - par jour
  - par mois
  - par semaine
  priorite_recente: false
  champs:
  - na_possible: false
    nom: date_rapport
    type: date
  - na_possible: true
    nom: periode_debut
    type: date
  - na_possible: true
    nom: periode_fin
    type: date
  - description: valeur brute telle que rapportée
    na_possible: false
    nom: frequence_crises
    type: numerique
    bounds:
    - 0
    - 1000
  - na_possible: false
    nom: unite_frequence
    type: categoriel
    valeurs:
    - crises/mois
    - crises/jour
    - crises/semaine
  - na_possible: true
    nom: duree_moyenne_min
    type: numerique
    bounds:
    - 0
    - 180
  exemples:
  - type: positif
    texte: Il faisait 3 crises par jour fin 2022, sans amélioration malgré le traitement.
    sortie:
    - date_rapport: '2022'
      periode_debut: 'null'
      periode_fin: 'null'
      frequence_crises: '3'
      unite_frequence: crises/jour
      duree_moyenne_min: 'null'
  - type: negatif
    texte: Aucune fréquence de crises n'est rapportée dans ce dossier.
    sortie: []
- table: epr_examen
  repetee: true
  max_tokens: 1600
  description: Examen neurologique et général à une date donnée
  mots_cles:
  - examen neurologique
  - examen général
  - éveil
  - réactivité
  - tonus
  - marche
  - réflexe
  - réflexes
  - poids
  - taille
  - périmètre crânien
  - dysmorphie
  - force musculaire
  priorite_recente: false
  champs:
  - na_possible: false
    nom: date_examen
    type: date
  - na_possible: true
    nom: neuro_eveil_reactivite
    type: texte
  - na_possible: true
    nom: neuro_langage
    type: texte
  - na_possible: true
    nom: neuro_comportement
    type: texte
  - na_possible: true
    nom: neuro_marche
    type: texte
  - na_possible: true
    nom: neuro_station_debout
    type: texte
  - na_possible: true
    nom: neuro_tonus
    type: texte
  - na_possible: true
    nom: neuro_force_motrice
    type: texte
  - na_possible: true
    nom: neuro_reflexes
    type: texte
  - na_possible: true
    nom: neuro_mouvements_anormaux
    type: texte
  - na_possible: true
    nom: poids_kg
    type: numerique
    bounds:
    - 0
    - 150
  - na_possible: true
    nom: taille_cm
    type: numerique
    bounds:
    - 0
    - 200
  - na_possible: true
    nom: perimetre_cranien_cm
    type: numerique
    bounds:
    - 0
    - 70
  - na_possible: true
    nom: lesions_cutanees
    type: texte
  - na_possible: true
    nom: deformations_osseuses
    type: texte
  - na_possible: true
    nom: dysmorphie_faciale
    type: texte
  exemples:
  - type: positif
    texte: Langage enfantin limité à phrases simples, marche autonome stable, pas de déficit moteur, tonus
      normal, réflexes présents et symétriques.
    sortie:
    - date_examen: 'null'
      neuro_eveil_reactivite: 'null'
      neuro_langage: langage enfantin limité à phrases simples
      neuro_comportement: 'null'
      neuro_marche: marche autonome stable
      neuro_station_debout: 'null'
      neuro_tonus: tonus normal
      neuro_force_motrice: pas de déficit moteur
      neuro_reflexes: réflexes présents et symétriques
      neuro_mouvements_anormaux: 'null'
      poids_kg: 'null'
      taille_cm: 'null'
      perimetre_cranien_cm: 'null'
      lesions_cutanees: 'null'
      deformations_osseuses: 'null'
      dysmorphie_faciale: 'null'
  - type: negatif
    texte: Aucun examen neurologique n'est décrit dans ce passage du dossier.
    sortie: []
- table: epr_etiologie
  repetee: true
  max_tokens: 900
  description: 'Chaque étiologie discutée/retenue pour l''épilepsie (1 à N par patient, ILAE 2017 autorise
    les étiologies combinées). Exactement une entrée doit avoir etiologie_principale = true : celle retenue
    comme diagnostic principal par le clinicien.

    '
  mots_cles:
  - étiologie
  - éthiologie
  - structurelle
  - génétique
  - métabolique
  - infectieuse
  - immune
  - inconnue
  - dysplasie
  - sclérose hippocampique
  - cause
  priorite_recente: true
  champs:
  - na_possible: false
    nom: categorie_etiologique
    type: categoriel
    valeurs:
    - Structurelle
    - Génétique
    - Métabolique
    - Infectieuse
    - Immune
    - Inconnue
  - description: true uniquement pour l'étiologie retenue comme diagnostic final/le plus probable
    na_possible: false
    nom: etiologie_principale
    type: booleen
  - description: si categorie = Structurelle
    na_possible: true
    nom: detail_lesion_structurelle
    type: texte
  - description: si categorie = Génétique
    na_possible: true
    nom: detail_gene_mute
    type: texte
  - description: si categorie = Métabolique
    na_possible: true
    nom: detail_maladie_metabolique
    type: texte
  - description: si categorie = Infectieuse
    na_possible: true
    nom: detail_facteur_infectieux
    type: texte
  - description: si categorie = Immune
    na_possible: true
    nom: detail_maladie_auto_immune
    type: texte
  exemples:
  - type: positif
    texte: Une cause structurelle est la plus probable, hypoplasie du tegmentum pontique à l'IRM. Origine
      génétique discutée, panel PAGEM demandé.
    sortie:
    - categorie_etiologique: Structurelle
      etiologie_principale: 'true'
      detail_lesion_structurelle: hypoplasie du tegmentum pontique
      detail_gene_mute: 'null'
      detail_maladie_metabolique: 'null'
      detail_facteur_infectieux: 'null'
      detail_maladie_auto_immune: 'null'
  - type: negatif
    texte: L'étiologie de l'épilepsie n'a pas encore été discutée dans ce dossier.
    sortie: []
- table: epr_pharmacoresistance
  repetee: false
  max_tokens: 300
  description: Statut de pharmacorésistance déclaré dans le dossier
  mots_cles:
  - pharmacorésistance
  - pharmacorésistant
  - pharmaco-résistant
  - antiépileptique
  - échec
  priorite_recente: true
  champs:
  - description: Oui/Non selon définition ILAE = échec de 2 AE adaptés et bien tolérés
    na_possible: false
    nom: statut_pharmacoresistance_confirme
    type: booleen
  exemples:
  - type: positif
    texte: Donc c'est une épilepsie pharmacorésistante.
    sortie:
      statut_pharmacoresistance_confirme: 'true'
  - type: negatif
    texte: Le patient répond bien au traitement de première intention, aucune mention de pharmacorésistance.
    sortie:
      statut_pharmacoresistance_confirme: 'false'
- table: epr_liste_ae
  repetee: true
  max_tokens: 750
  description: Chaque antiépileptique (AE) essayé, avec dose/durée/réponse
  mots_cles:
  - Depakine
  - Dépakine
  - Levetiracetam
  - Keppra
  - Lamictal
  - Rivotril
  - Tegretol
  - Trileptal
  - Vigabatrin
  - Sabril
  - phénobarbital
  - Gardénal
  - mg/kg
  - antiépileptique
  - 'AE '
  - traitement antiépileptique
  priorite_recente: false
  champs:
  - na_possible: false
    nom: nom_ae
    type: texte
  - na_possible: true
    nom: dose
    type: texte
  - na_possible: true
    nom: duree
    type: texte
  - description: crises libres / réduction % / échec
    na_possible: true
    nom: reponse
    type: texte
  - na_possible: true
    nom: motif_echec
    type: categoriel
    valeurs:
    - Inefficacité
    - Effet indésirable
    - aucun
  exemples:
  - type: positif
    texte: Mis sous Dépakine pendant un an sans amélioration. Switché vers Tegretol pendant 3 mois, inefficace.
    sortie:
    - nom_ae: Dépakine (valproate)
      dose: 'null'
      duree: 1 an
      reponse: échec
      motif_echec: Inefficacité
    - nom_ae: Tegretol (carbamazépine)
      dose: 'null'
      duree: 3 mois
      reponse: échec
      motif_echec: Inefficacité
  - type: negatif
    texte: Le patient n'a reçu aucun traitement antiépileptique à ce jour.
    sortie: []
- table: epr_eeg
  repetee: true
  max_tokens: 900
  description: Chaque EEG réalisé (intercritique et/ou vidéo)
  mots_cles:
  - EEG
  - pointe
  - pointes-ondes
  - onde lente
  - ralentissement focal
  - foyer
  - tracé
  - intercritique
  priorite_recente: false
  champs:
  - na_possible: false
    nom: date_eeg
    type: date
  - na_possible: false
    nom: eeg_intercritique
    type: categoriel
    valeurs:
    - Normal
    - Anormal
  - description: NA si eeg_intercritique = Normal
    na_possible: true
    nom: type_anomalie
    type: texte
  - description: NA si eeg_intercritique = Normal
    na_possible: true
    nom: localisation_foyer
    type: texte
  - na_possible: false
    nom: eeg_video_realise
    type: booleen
  - description: NA si eeg_video_realise = false
    na_possible: true
    nom: date_eeg_video
    type: date
  - na_possible: true
    nom: type_crise_enregistree
    type: texte
  exemples:
  - type: positif
    texte: Un EEG a été fait le 3 septembre 2020, montrant 3 crises électriques dans les régions fronto-temporales
      surtout gauche.
    sortie:
    - date_eeg: '2020-09-03'
      eeg_intercritique: Anormal
      type_anomalie: décharges épileptiques lors de l'endormissement
      localisation_foyer: fronto-temporal gauche
      eeg_video_realise: 'null'
      date_eeg_video: 'null'
      type_crise_enregistree: 'null'
  - type: negatif
    texte: Aucun EEG n'a été réalisé pour ce patient à ce jour.
    sortie: []
- table: epr_imagerie
  repetee: true
  max_tokens: 650
  description: Chaque IRM cérébrale réalisée dans le bilan épilepsie
  mots_cles:
  - IRM
  - scanner
  - lésion
  - lésions
  - T1
  - T2
  - FLAIR
  - flair
  - cérébrale
  priorite_recente: false
  champs:
  - na_possible: false
    nom: date_examen
    type: date
  - na_possible: false
    nom: irm_cerebrale
    type: categoriel
    valeurs:
    - Normal
    - Anormal
  - description: NA si irm_cerebrale = Normal
    na_possible: true
    nom: type_lesion
    type: texte
  - description: citer le compte-rendu tel quel
    na_possible: true
    nom: cr_detaille_texte
    type: texte
  exemples:
  - type: positif
    texte: Une IRM le 8 septembre 2020 a été sans anomalies.
    sortie:
    - date_examen: '2020-09-08'
      irm_cerebrale: Normal
      type_lesion: NA
      cr_detaille_texte: IRM sans anomalies
  - type: negatif
    texte: Aucune imagerie cérébrale n'a été réalisée pour ce patient.
    sortie: []
- table: epr_genetique
  repetee: true
  max_tokens: 650
  description: Chaque test génétique réalisé (panel, WES...)
  mots_cles:
  - gène
  - génétique
  - variant
  - mutation
  - ACMG
  - panel
  - WES
  - PAGEM
  - exome
  priorite_recente: false
  champs:
  - description: panel épilepsie, WES, gène ciblé...
    na_possible: false
    nom: gene_teste
    type: texte
  - na_possible: true
    nom: variant_identifie
    type: texte
  - na_possible: true
    nom: classification_acmg
    type: categoriel
    valeurs:
    - Classe I
    - Classe II
    - Classe III
    - Classe IV
    - Classe V
  - na_possible: true
    nom: mode_transmission
    type: categoriel
    valeurs:
    - AR
    - AD
    - lié X
    - de novo
  exemples:
  - type: positif
    texte: Staff PAGEM, indication PAGEM retenue pour recherche génétique (STXBP1, SCN2A, DEPDC5...).
    sortie:
    - gene_teste: Panel PAGEM (épilepsie génétique)
      variant_identifie: 'null'
      classification_acmg: 'null'
      mode_transmission: 'null'
  - type: negatif
    texte: Aucun test génétique n'a été demandé pour ce patient.
    sortie: []
- table: epr_bilan_prechirurgical
  repetee: true
  max_tokens: 550
  description: Chaque bilan préchirurgical réalisé avant décision
  mots_cles:
  - PET
  - IRM fonctionnelle
  - bilan pré-chirurgical
  - bilan prechirurgical
  - éligibilité
  - éligible
  - vidéo-EEG prolongé
  priorite_recente: false
  champs:
  - na_possible: false
    nom: date_bilan
    type: date
  - na_possible: true
    nom: eligibilite_chirurgie
    type: booleen
  - description: PET, IRM fonctionnelle, etc.
    na_possible: true
    nom: type_bilan_realise
    type: texte
  exemples:
  - type: positif
    texte: 'Bilan préchirurgical réalisé le 12 mars 2024 : PET scan et IRM fonctionnelle, éligibilité
      à la chirurgie discutée en staff.'
    sortie:
    - date_bilan: '2024-03-12'
      eligibilite_chirurgie: 'null'
      type_bilan_realise: PET scan, IRM fonctionnelle
  - type: negatif
    texte: Aucun bilan préchirurgical n'a été envisagé pour ce patient.
    sortie: []
- table: epr_chirurgie
  repetee: true
  max_tokens: 650
  description: Acte chirurgical réalisé (distinct du bilan préchirurgical)
  mots_cles:
  - chirurgie
  - opéré
  - opérée
  - résection
  - intervention chirurgicale
  priorite_recente: false
  champs:
  - na_possible: false
    nom: chirurgie_realisee
    type: booleen
  - description: NA si chirurgie_realisee = false
    na_possible: true
    nom: date_chirurgie
    type: date
  - description: NA si chirurgie_realisee = false
    na_possible: true
    nom: type_chirurgie
    type: texte
  - description: NA si chirurgie_realisee = false
    na_possible: true
    nom: evolution_post_chirurgie
    type: categoriel
    valeurs:
    - Persistance des crises
    - Rémission totale
    - Diminution de la fréquence des crises
  exemples:
  - type: positif
    texte: Le patient n'a pas eu de chirurgie à ce jour, dossier encore en cours d'exploration étiologique.
    sortie:
    - chirurgie_realisee: 'false'
      date_chirurgie: NA
      type_chirurgie: NA
      evolution_post_chirurgie: NA
  - type: negatif
    texte: La question d'une chirurgie n'est pas abordée dans ce dossier.
    sortie: []
- table: epr_alternatives_therapeutiques
  repetee: true
  max_tokens: 550
  description: Chaque alternative thérapeutique tentée (régime cétogène, VNS)
  mots_cles:
  - cétogène
  - régime
  - stimulation du nerf vague
  - VNS
  priorite_recente: false
  champs:
  - na_possible: false
    nom: type_alternative
    type: categoriel
    valeurs:
    - régime cétogène
    - VNS
  - na_possible: false
    nom: date_debut
    type: date
  - na_possible: true
    nom: reponse
    type: texte
  exemples:
  - type: positif
    texte: Discussion d'un régime cétogène en cas d'échec du traitement médicamenteux actuel.
    sortie:
    - type_alternative: régime cétogène
      date_debut: 'null'
      reponse: 'null'
  - type: negatif
    texte: Aucune alternative thérapeutique (régime cétogène, VNS) n'a été envisagée pour ce patient.
    sortie: []
- table: epr_bilan_orthophonique
  repetee: true
  max_tokens: 450
  description: Chaque bilan orthophonique réalisé
  mots_cles:
  - orthophonie
  - orthophonique
  - langage
  priorite_recente: false
  champs:
  - na_possible: false
    nom: date_bilan
    type: date
  - na_possible: false
    nom: bilan_orthophonique_texte
    type: texte
  exemples:
  - type: positif
    texte: 'Bilan orthophonique réalisé le 5 février 2024 : retard de langage modéré, prise en charge
      proposée.'
    sortie:
    - date_bilan: '2024-02-05'
      bilan_orthophonique_texte: retard de langage modéré
  - type: negatif
    texte: Aucun bilan orthophonique n'a été réalisé pour ce patient.
    sortie: []
- table: epr_bilan_neuropsy
  repetee: true
  max_tokens: 900
  description: Chaque bilan neuropsychologique réalisé
  mots_cles:
  - neuropsychologique
  - neuropsy
  - QI
  - TSA
  - TDAH
  - comportement
  - sphinctérien
  - sphincterien
  - sommeil
  - intellectuel
  priorite_recente: false
  champs:
  - na_possible: false
    nom: date_bilan
    type: date
  - na_possible: true
    nom: qi
    type: numerique
    bounds:
    - 0
    - 200
  - na_possible: false
    nom: troubles_comportement
    type: booleen
  - description: TSA / TDAH
    na_possible: false
    nom: troubles_psy_associes
    type: categoriel
    valeurs:
    - Oui
    - Non
    - NA
  - na_possible: false
    nom: troubles_sphincteriens
    type: categoriel
    valeurs:
    - Oui
    - Non
    - NA
  - na_possible: false
    nom: troubles_sommeil
    type: booleen
  - description: NA si troubles_sommeil = false
    na_possible: true
    nom: type_trouble_sommeil
    type: texte
  exemples:
  - type: positif
    texte: Compléter par un bilan neuropsychologique. Forte présomption d'une déficience intellectuelle.
    sortie:
    - date_bilan: 'null'
      qi: 'null'
      troubles_comportement: 'true'
      troubles_psy_associes: 'null'
      troubles_sphincteriens: 'null'
      troubles_sommeil: 'null'
      type_trouble_sommeil: 'null'
  - type: negatif
    texte: Aucun bilan neuropsychologique n'a été réalisé ni demandé pour ce patient.
    sortie: []
- table: epr_bilan_ergotherapique
  repetee: true
  max_tokens: 450
  description: Chaque bilan ergothérapique réalisé
  mots_cles:
  - ergothérapie
  - ergothérapique
  - motricité fine
  priorite_recente: false
  champs:
  - na_possible: false
    nom: date_bilan
    type: date
  - na_possible: false
    nom: bilan_ergotherapique_texte
    type: texte
  exemples:
  - type: positif
    texte: 'Bilan ergothérapique réalisé le 10 mars 2024 : difficultés de motricité fine notées.'
    sortie:
    - date_bilan: '2024-03-10'
      bilan_ergotherapique_texte: difficultés de motricité fine
  - type: negatif
    texte: Aucun bilan ergothérapique n'a été réalisé pour ce patient.
    sortie: []
- table: epr_suivi
  repetee: false
  max_tokens: 350
  description: Statut au dernier suivi documenté dans le dossier
  mots_cles:
  - suivi
  - libre de crise
  - libre de crises
  - épilepsie active
  - perdu de vue
  - durée de suivi
  - dernière consultation
  - va bien
  - dernier contrôle
  priorite_recente: true
  champs:
  - na_possible: false
    nom: statut_dernier_suivi
    type: categoriel
    valeurs:
    - Libre de crises
    - Épilepsie active
    - Perdu de vue
  - na_possible: true
    nom: duree_suivi_mois
    type: numerique
    bounds:
    - 0
    - 600
  exemples:
  - type: positif
    texte: Va bien depuis la dernière hospitalisation, pas de crise depuis 3 jours.
    sortie:
      statut_dernier_suivi: Libre de crises
      duree_suivi_mois: 'null'
  - type: negatif
    texte: Aucune information de suivi n'est disponible dans ce dossier.
    sortie:
      statut_dernier_suivi: 'null'
      duree_suivi_mois: 'null'
"""



TABLES_SEP = yaml.safe_load(SEP_SCHEMA_YAML)
TABLES_EPR = yaml.safe_load(EPR_SCHEMA_YAML)
TABLES_BY_NAME = {t["table"]: t for t in TABLES_SEP + TABLES_EPR}
print(f"SEP : {len(TABLES_SEP)} tables chargees")
print(f"EPR : {len(TABLES_EPR)} tables chargees")

for label, tables in [("SEP", TABLES_SEP), ("EPR", TABLES_EPR)]:
    n_prio = sum(1 for t in tables if t.get("priorite_recente"))
    n_rep = sum(1 for t in tables if t.get("repetee"))
    print(f"  {label}: {n_prio} table(s) priorite_recente=true, {n_rep} table(s) repetee(s)")

# ## 3. Template de prompt unique (partagé SEP/EPR) + construction des schémas JSON
# ## 3. Template de prompt unique (partagé SEP/EPR) + construction des schémas JSON
# 
# Inchangé (règles 9 et 10 = points 4 et 8). Les nouvelles briques (sections 8-11)
# réutilisent `build_prompt`/`build_json_schema` telles quelles — aucune duplication
# de logique de prompt.



TEMPLATE_STR = """
Tu es un assistant d'extraction d'information clinique pour un registre médical pédiatrique.
Tu dois extraire UNIQUEMENT les informations relatives à la table "{{ table }}" : {{ description }}

RÈGLES STRICTES (à respecter absolument) :
1. N'invente RIEN. Si une information n'est pas explicitement mentionnée dans le texte : mets la
   chaîne littérale "null" (le mot null entre guillemets, PAS le type JSON null).
2. Si la question ne se pose explicitement PAS pour ce patient (cas cliniquement écarté ou non applicable,
   ex. "reste en forme RR" pour une date de conversion SP, ou "EEG normal" pour un type d'anomalie) :
   mets la chaîne littérale "NA".
3. Si le texte contient une négation clinique confirmée (ex. "pas de chirurgie réalisée") : mets "true" ou "false"
   pour les champs booléens, sinon décris la négation en texte.
4. Ne confonds jamais ces trois cas — c'est critique pour les analyses statistiques en aval.
5. IMPORTANT — "Inconnue"/"Indéterminé" n'est PAS un synonyme de "null" : ces valeurs ne doivent être
   utilisées QUE si le texte indique EXPLICITEMENT que le clinicien n'a pas pu déterminer l'information
   (ex. "type de crise non déterminé", "étiologie indéterminée"). Si le sujet n'est simplement PAS
   abordé dans le texte, utilise "null" — jamais "Inconnue" par défaut ou par prudence.
6. Ne recopie AUCUN champ "evidence_span" — ce n'est plus demandé ici, la traçabilité est calculée
   séparément après ta réponse. Concentre tout ton budget de génération sur les valeurs elles-mêmes.
7. Pour les champs de type "texte" demandant un compte-rendu (ex. cr_irm_texte, cr_detaille_texte,
   bilan_orthophonique_texte) : recopie le passage pertinent tel quel, ne résume pas.
{% if repetee %}
8. Cette table est RÉPÉTÉE : le dossier peut contenir plusieurs occurrences (dates différentes).
   Retourne une LISTE d'objets, un objet par occurrence identifiée dans le texte.
   S'il n'y a AUCUNE occurrence mentionnée, retourne une liste vide [].
{% else %}
8. Cette table n'a qu'UNE seule occurrence par patient. Retourne un objet unique (pas une liste).
{% endif %}
9. Le texte ci-dessous a été présélectionné pour cette table : il peut contenir des coupures marquées
   "[...section non pertinente pour cette table, omise...]". Ce marqueur signale un passage du dossier
   retiré parce qu'il ne concerne pas cette table précise — ce n'est PAS une interruption réelle du
   suivi du patient, ni un changement de patient. Traite chaque section restante comme un extrait
   valide et daté du même dossier patient, même si elle n'est pas contiguë aux autres.
{% if priorite_recente %}
10. RÈGLE DE PRIORITÉ TEMPORELLE : si ce dossier mentionne des statuts, diagnostics ou conclusions
    CONTRADICTOIRES à des dates différentes (ex. "épilepsie active" puis plus tard "libre de crises
    depuis 3 mois" ; ou une étiologie écartée puis reconsidérée), retiens TOUJOURS l'information
    correspondant à la date la PLUS RÉCENTE mentionnée dans le texte. Une mention antérieure qui a
    été révisée ou dépassée par un événement plus tardif ne doit plus être considérée comme l'état
    actuel du patient.
{% endif %}

CHAMPS À EXTRAIRE :
{% for champ in champs %}
- {{ champ.nom }} (type: {{ champ.type }}{% if champ.valeurs %}, valeurs possibles: {{ champ.valeurs }}{% endif %}{% if champ.na_possible %}, "NA" possible{% endif %})
  {%- if champ.description %}
  → {{ champ.description }}
  {%- endif %}
{% endfor %}

{% if exemples %}
EXEMPLES (few-shot) :
{% for ex in exemples %}
--- Exemple {{ loop.index }} ({{ ex.type }}) ---
TEXTE : "{{ ex.texte }}"
JSON attendu : {{ ex.sortie_json }}
{% endfor %}
{% endif %}

Réponds STRICTEMENT en JSON valide, sans aucun texte avant ou après, conforme au schéma fourni.

--- TEXTE DU DOSSIER PATIENT (contexte ciblé pour cette table) ---
{{ texte }}
--- FIN DU TEXTE ---

JSON :
"""



_env = Environment(trim_blocks=True, lstrip_blocks=True)
_TEMPLATE = _env.from_string(TEMPLATE_STR)

# NOTE : outlines (guided JSON) ne supporte pas "type" en liste (ex. ["string","null"]).
# On encode donc le "non mentionne" comme la chaine litterale "null" (enum/string).

def build_prompt(table_cfg, texte_dossier):
    exemples_render = []
    for ex in table_cfg.get("exemples", []):
        sortie_wrap = {table_cfg["table"]: ex["sortie"]}
        exemples_render.append({"type": ex["type"], "texte": ex["texte"],
                                 "sortie_json": json.dumps(sortie_wrap, ensure_ascii=False)})
    return _TEMPLATE.render(
        table=table_cfg["table"], description=table_cfg["description"],
        repetee=table_cfg["repetee"], champs=table_cfg["champs"],
        exemples=exemples_render, texte=texte_dossier,
        priorite_recente=table_cfg.get("priorite_recente", False),
    )


def _champ_to_json_schema(champ):
    if champ["type"] == "booleen":
        valeurs = ["true", "false"]
    elif champ["type"] == "categoriel" and champ.get("valeurs"):
        valeurs = list(champ["valeurs"])
        if champ.get("na_possible"):
            valeurs = valeurs + ["NA"]
    else:
        valeurs = None
    if valeurs is not None:
        return {"enum": valeurs + ["null"]}
    return {"type": "string"}


def build_json_schema(table_cfg, force_min_items=False):
    """force_min_items=True (NOUVEAU) : ajoute 'minItems': 1 au schema d'une
    table repetee -> contrainte structurelle qui empeche outlines de fermer
    le tableau JSON a []. A n'utiliser QUE quand on sait deja, par le
    contexte cible (point 4), qu'il existe des chunks pertinents pour cette
    table (cf. section 8) - sinon on forcerait une hallucination d'objet."""
    properties, required = {}, []
    for champ in table_cfg["champs"]:
        properties[champ["nom"]] = _champ_to_json_schema(champ)
        required.append(champ["nom"])
    object_schema = {"type": "object", "properties": properties,
                      "required": required, "additionalProperties": False}
    if table_cfg["repetee"]:
        array_schema = {"type": "array", "items": object_schema}
        if force_min_items:
            array_schema["minItems"] = 1
        return {"type": "object",
                "properties": {table_cfg["table"]: array_schema},
                "required": [table_cfg["table"]], "additionalProperties": False}
    return {"type": "object", "properties": {table_cfg["table"]: object_schema},
            "required": [table_cfg["table"]], "additionalProperties": False}

# ## 4. Chargement du LLM (Qwen3-8B, 4-bit) — une seule fois, réutilisé pour SEP et EPR

# ---------------------------------------------------------------------------
# ## 4. Appel LLM — REMPLACE la section 4 du notebook (plus de chargement
# local du modèle : on appelle le serveur Qwen déjà démarré sur :8003).
# Signature IDENTIQUE à l'originale, donc les sections 5-12 ci-dessous
# (collées telles quelles du notebook) fonctionnent sans modification.
# ---------------------------------------------------------------------------

def _appliquer_chat_template_sans_reflexion(prompt_texte: str) -> dict:
    return {
        "messages": [{"role": "user", "content": prompt_texte}],
        "chat_template_kwargs": {"enable_thinking": False},
    }


def _appel_qwen_api(prompt_texte: str, json_schema: dict, max_tokens: int, temperature: float) -> dict:
    payload = {
        "model": QWEN_MODEL_NAME,
        "max_tokens": max_tokens,
        "temperature": temperature,
        # response_format (pas guided_json) : c'est ce que comprend notre
        # endpoint /v1/chat/completions local (extraction_service.py, meme
        # modele deja charge en memoire — pas de second serveur/telechargement).
        "response_format": {"type": "json_object", "schema": json_schema},
        **_appliquer_chat_template_sans_reflexion(prompt_texte),
    }
    resp = requests.post(QWEN_API_URL, json=payload, timeout=QWEN_TIMEOUT_S)
    resp.raise_for_status()
    data = resp.json()
    contenu = data["choices"][0]["message"]["content"]
    return json.loads(contenu)


def llm_extract(prompt, json_schema, max_tokens=MAX_NEW_TOKENS, repetee=False, sampler=None):
    if sampler == "greedy":
        temperature = 0.0
    elif sampler == "multinomial":
        temperature = 0.3
    else:
        temperature = 0.3 if repetee else 0.0

    derniere_erreur = None
    for tentative in range(QWEN_MAX_RETRIES):
        try:
            return _appel_qwen_api(prompt, json_schema, max_tokens, temperature)
        except (requests.RequestException, json.JSONDecodeError, KeyError, IndexError) as exc:
            derniere_erreur = exc
            if tentative < QWEN_MAX_RETRIES - 1:
                time.sleep(1.5 * (tentative + 1))
    raise RuntimeError(f"Échec de l'appel au serveur Qwen (localhost:8003) après {QWEN_MAX_RETRIES} tentatives : {derniere_erreur}")


# ## 5. Post-traitement (cast, evidence_span, validation déterministe, étiologie principale unique)
# 
# Inchangé — c'est la correction 4 (post-traitement déterministe) du point de
# vue de l'encadrante, déjà solide dans la version précédente.



def _cast_scalar(value, champ_type):
    if value is None or value == "null":
        return None
    if value == "NA":
        return "NA"
    if champ_type == "booleen":
        return value == "true"
    if champ_type == "numerique":
        try:
            f = float(value)
            return int(f) if f.is_integer() else f
        except (TypeError, ValueError):
            return None
    return value


def cast_raw_table(payload, table_cfg):
    champ_types = {c["nom"]: c["type"] for c in table_cfg["champs"]}
    def _cast_obj(obj):
        casted = dict(obj)
        for nom, ctype in champ_types.items():
            if nom in casted:
                casted[nom] = _cast_scalar(casted[nom], ctype)
        return casted
    if table_cfg["repetee"]:
        return [_cast_obj(o) for o in (payload or [])]
    return _cast_obj(payload) if payload else payload


def _find_evidence(value, texte, seuil=70):
    if value is None or value == "NA" or not isinstance(value, (str, int, float)):
        return None
    value_str = str(value).strip()
    if len(value_str) < 2:
        return None
    segments = [s.strip() for s in re.split(r"[\n.]", texte) if len(s.strip()) > 3]
    if not segments:
        return None
    if HAVE_RAPIDFUZZ:
        best_seg, best_score = None, 0
        for s in segments:
            score = rf_fuzz.partial_ratio(value_str.lower(), s.lower())
            if score > best_score:
                best_seg, best_score = s, score
        return best_seg[:250] if best_score >= seuil else None
    best_seg, best_ratio = None, 0.0
    for s in segments:
        r = difflib.SequenceMatcher(None, value_str.lower(), s.lower()).quick_ratio()
        if r > best_ratio:
            best_seg, best_ratio = s, r
    return best_seg[:250] if best_ratio >= seuil / 100 else None


def attach_evidence(payload, table_cfg, texte):
    champ_noms = [c["nom"] for c in table_cfg["champs"]]
    def _attach_obj(obj):
        out = dict(obj)
        for nom in champ_noms:
            out[f"evidence_span_{nom}"] = _find_evidence(out.get(nom), texte) or ""
        return out
    if table_cfg["repetee"]:
        return [_attach_obj(o) for o in (payload or [])]
    return _attach_obj(payload) if payload else payload


def _is_valid_calendar_date(val):
    try:
        datetime.date.fromisoformat(val)
        return True
    except (ValueError, TypeError):
        return False


def _cross_field_checks(table_name, obj, prefix):
    issues = []
    if table_name in ("sep_antecedents", "epr_antecedents"):
        cons, degre = obj.get("consanguinite_parentale"), obj.get("consanguinite_degre")
        if cons is False and degre not in (None, "NA"):
            issues.append(f"{prefix}incoherence : consanguinite_parentale=false mais consanguinite_degre={degre!r} rempli")
        if cons is True and degre is None:
            issues.append(f"{prefix}consanguinite_parentale=true mais consanguinite_degre non precise")
    if table_name == "sep_irm":
        if obj.get("prise_contraste_gd") is False and obj.get("nb_lesions_rehaussees") not in (None, "NA"):
            issues.append(f"{prefix}incoherence : prise_contraste_gd=false mais nb_lesions_rehaussees={obj.get('nb_lesions_rehaussees')!r} rempli")
    if table_name == "sep_evolution":
        if obj.get("forme_evolutive") == "RR" and obj.get("date_conversion_sp") not in (None, "NA"):
            issues.append(f"{prefix}incoherence : forme_evolutive=RR mais date_conversion_sp={obj.get('date_conversion_sp')!r} rempli")
    if table_name == "epr_eeg":
        if obj.get("eeg_intercritique") == "Normal" and obj.get("type_anomalie") not in (None, "NA"):
            issues.append(f"{prefix}incoherence : eeg_intercritique=Normal mais type_anomalie={obj.get('type_anomalie')!r} rempli")
    if table_name == "epr_imagerie":
        if obj.get("irm_cerebrale") == "Normal" and obj.get("type_lesion") not in (None, "NA"):
            issues.append(f"{prefix}incoherence : irm_cerebrale=Normal mais type_lesion={obj.get('type_lesion')!r} rempli")
    if table_name == "epr_chirurgie":
        if obj.get("chirurgie_realisee") is False:
            for cn in ("date_chirurgie", "type_chirurgie", "evolution_post_chirurgie"):
                if obj.get(cn) not in (None, "NA"):
                    issues.append(f"{prefix}incoherence : chirurgie_realisee=false mais {cn}={obj.get(cn)!r} rempli")
    return issues


def _validate_object(obj, table_cfg, texte, issues, prefix=""):
    table_name = table_cfg["table"]
    for champ in table_cfg["champs"]:
        nom = champ["nom"]
        val = obj.get(nom)
        if val == "NA" and not champ.get("na_possible"):
            issues.append(f"{prefix}{nom} : valeur NA recue mais non autorisee par la config")
        valeurs = champ.get("valeurs")
        if val == "Inconnue" and valeurs and "Inconnue" not in valeurs:
            issues.append(f"{prefix}{nom} = Inconnue : valeur non prevue par la config, a verifier "
                           "(le LLM a peut-etre utilise Inconnue au lieu de null)")
        if valeurs and val not in (None, "NA") and val not in valeurs:
            issues.append(f"{prefix}{nom} = {val!r} : valeur hors du vocabulaire controle {valeurs}")
        bounds = champ.get("bounds")
        if bounds and isinstance(val, (int, float)) and not isinstance(val, bool):
            lo, hi = bounds
            if not (lo <= val <= hi):
                issues.append(f"{prefix}{nom} = {val} : hors bornes plausibles [{lo}, {hi}] - probable erreur d'unite ou hallucination")
        if champ["type"] == "date" and isinstance(val, str) and val != "NA" and not _is_valid_calendar_date(val):
            issues.append(f"{prefix}{nom} = {val!r} : date invalide (jour/mois doivent correspondre a un jour calendaire reel)")
        if champ["type"] == "booleen" and val is None:
            issues.append(f"{prefix}{nom} : booleen non determine (null) - verifier si le texte contient "
                           "une negation/affirmation explicite qui aurait du donner true/false")
    issues.extend(_cross_field_checks(table_name, obj, prefix))


def validate_result(raw, table_cfg, texte):
    table_name = table_cfg["table"]
    issues = []
    payload = raw.get(table_name)
    if table_cfg["repetee"]:
        payload = payload or []
        for i, obj in enumerate(payload):
            _validate_object(obj, table_cfg, texte, issues, prefix=f"[occurrence {i}] ")
    else:
        if payload:
            _validate_object(payload, table_cfg, texte, issues)
    return {table_name: payload}, issues


def enforce_unique_etiologie_principale(etiologies):
    """Correction 4 : une seule etiologie_principale=true, celle de la derniere
    occurrence marquee (le prompt/point 8 privilegie deja la conclusion la
    plus tardive ; ceci est un filet de securite deterministe)."""
    if not etiologies:
        return etiologies, None
    principales = [e for e in etiologies if e.get("etiologie_principale") is True]
    if len(principales) == 1:
        return etiologies, None
    if len(principales) == 0:
        return etiologies, "Aucune etiologie marquee principale - a trancher manuellement"
    # Plus d'une principale -> ne garder que la derniere (ordre = ordre d'extraction)
    last_idx = max(i for i, e in enumerate(etiologies) if e.get("etiologie_principale") is True)
    for i, e in enumerate(etiologies):
        if e.get("etiologie_principale") is True and i != last_idx:
            e["etiologie_principale"] = False
    return etiologies, f"{len(principales)} etiologies marquees principale simultanement -> seule la derniere a ete conservee (a verifier manuellement)"

# ## 6. Chargement des dossiers patients — en CHUNKS horodatés

# ## 6. Chargement des dossiers patients — en CHUNKS horodatés



def join_chunks(chunks: list) -> str:
    return "\n".join(c["text"] for c in chunks)


# ## 7. Contexte ciblé par table (point 4)



def retrieve_chunks_for_table(chunks, table_cfg, keep_head=RETRIEVAL_KEEP_HEAD,
                               keep_tail=RETRIEVAL_KEEP_TAIL, min_matches=RETRIEVAL_MIN_MATCHES):
    n = len(chunks)
    mots_cles = table_cfg.get("mots_cles") or []

    if not mots_cles or n == 0:
        return chunks, {"filtre": False, "n_total": n, "n_retenus": n, "n_matches_mots_cles": n,
                         "raison": "pas de mots-cles definis pour cette table -> dossier entier"}

    mots_cles_low = [m.lower() for m in mots_cles]
    idx_matches = {i for i, c in enumerate(chunks) if any(m in c["text"].lower() for m in mots_cles_low)}

    if len(idx_matches) < min_matches:
        return chunks, {"filtre": False, "n_total": n, "n_retenus": n, "n_matches_mots_cles": len(idx_matches),
                         "raison": f"seulement {len(idx_matches)} chunk(s) matche(nt) les mots-cles "
                                   f"(< min_matches={min_matches}) -> repli sur le dossier entier"}

    idx_head = set(range(min(keep_head, n)))
    idx_tail = set(range(max(0, n - keep_tail), n))
    idx_final = sorted(idx_matches | idx_head | idx_tail)
    diag = {"filtre": True, "n_total": n, "n_retenus": len(idx_final), "n_matches_mots_cles": len(idx_matches),
            "raison": f"{len(idx_matches)} chunk(s) pertinent(s) par mots-cles "
                      f"+ {len((idx_head | idx_tail) - idx_matches)} chunk(s) tete/queue de securite"}
    return [chunks[i] for i in idx_final], diag


def assemble_contexte(chunks_selectionnes):
    if not chunks_selectionnes:
        return ""
    parts, prev_idx = [], None
    for c in chunks_selectionnes:
        if prev_idx is not None and c["idx"] != prev_idx + 1:
            parts.append("[...section non pertinente pour cette table, omise...]")
        parts.append(c["text"])
        prev_idx = c["idx"]
    return "\n".join(parts)


def _tronquer_si_besoin(texte, max_chars=MAX_CONTEXTE_TABLE_CHARS):
    if len(texte) <= max_chars:
        return texte
    moitie = max_chars // 2
    return texte[:moitie] + "\n[...section intermédiaire tronquée...]\n" + texte[-moitie:]

# ## 8. Vote/ensembling pour les tables répétées (correction 1' + point 5' — GAPrompt-like)

# ## 8. Vote/ensembling pour les tables répétées (correction 1' + point 5' — GAPrompt-like)
# 
# **Problème initial (doc diagnostic EPR)** : `greedy()` referme trop souvent une
# table répétée vers `[]` dès la moindre incertitude sur le 1er objet, même quand
# le contexte ciblé (point 4) a trouvé des chunks pertinents. `multinomial(T=0.3)`
# seul réduit le problème sans le garantir.
# 
# **Solution retenue (inspirée de l'ensembling GAPrompt — plusieurs générations à
# température non nulle, fusionnées, plutôt qu'une seule sortie)** :
# 1. Générer la table **`N_VOTES_REPEATED` fois** en `multinomial`.
# 2. Fusionner les occurrences des différents runs par **similarité floue** (`rapidfuzz`) :
#    deux occurrences issues de runs différents sont considérées comme "la même"
#    si leur représentation JSON dépasse `VOTE_DEDUP_THRESHOLD`% de similarité,
#    et sont alors fusionnées champ par champ par **vote majoritaire** (valeur la
#    plus fréquente à travers les runs qui ont produit cette occurrence).
# 3. Si au moins 1 run sur `N_VOTES_REPEATED` produit une liste non vide, le
#    résultat final n'est PAS vide — ça élimine mécaniquement le collapse vers
#    `[]` par excès de prudence d'un run isolé.
# 4. En complément, si le contexte ciblé a trouvé au moins `RETRIEVAL_MIN_MATCHES`
#    chunks pertinents pour cette table (`n_matches_mots_cles >= RETRIEVAL_MIN_MATCHES`),
#    on ajoute `minItems: 1` à la contrainte structurelle du schema JSON pour les
#    runs de vote — contrainte dure qui empêche `[]`, à combiner avec le vote (qui,
#    lui, gère la qualité/le contenu, pas seulement la présence).
# 
# 
# **MISE À JOUR — vote restreint à un sous-ensemble de tables.** Le vote sur les
# ~17 tables répétées est le poste de coût GPU le plus élevé du notebook (3x plus
# d'appels que les autres tables), pour un gain concentré sur un petit nombre de
# tables selon le diagnostic F1 (`epr_eeg`, `epr_imagerie`/`sep_irm`,
# `sep_biologie_lcr`, `epr_genetique` — celles où le LLM produisait `[]` malgré des
# chunks pertinents identifiés). Le vote est donc maintenant limité à
# `VOTE_TABLES_EPR`/`VOTE_TABLES_SEP` (section 1) ; les autres tables répétées
# utilisent `extraire_table_repetee_standard` (1 seul appel, pas de fusion). Pour
# revenir au vote sur toutes les tables répétées (comportement d'origine),
# ajoutez simplement les tables manquantes à ces deux ensembles.
# 



def _dict_signature(obj, exclude_prefixes=("evidence_span_", "_")):
    """Represente un objet (occurrence) sous forme de chaine pour le fuzzy-match,
    en excluant les champs meta (evidence_span_*, _note...) qui ne doivent pas
    peser dans la comparaison de similarite."""
    items = sorted((k, str(v)) for k, v in obj.items()
                    if not any(k.startswith(p) for p in exclude_prefixes))
    return json.dumps(items, ensure_ascii=False)


def _similarity(obj_a, obj_b):
    sa, sb = _dict_signature(obj_a), _dict_signature(obj_b)
    if HAVE_RAPIDFUZZ:
        return rf_fuzz.token_set_ratio(sa, sb)
    return 100 * difflib.SequenceMatcher(None, sa, sb).quick_ratio()


def _majority_merge(cluster_objs):
    """Fusionne un cluster d'occurrences 'equivalentes' (issues de runs differents)
    par vote majoritaire champ par champ. En cas d'egalite, garde la valeur du
    1er objet du cluster (ordre = ordre des runs, run 1 sert de priorite)."""
    all_keys = set()
    for o in cluster_objs:
        all_keys.update(o.keys())
    merged = {}
    for k in all_keys:
        vals = [o.get(k) for o in cluster_objs if k in o]
        counts = Counter(str(v) for v in vals)
        val_str, _ = counts.most_common(1)[0]
        merged[k] = next(v for v in vals if str(v) == val_str)
    return merged


def fuzzy_merge_occurrences(runs_occurrences, threshold=VOTE_DEDUP_THRESHOLD):
    """runs_occurrences : liste de listes d'occurrences (1 sous-liste par run).
    Retourne la liste fusionnee/dedupliquee (clustering glouton par similarite)."""
    flat = [obj for run in runs_occurrences for obj in run]
    clusters = []  # liste de listes d'objets "equivalents"
    for obj in flat:
        placed = False
        for cluster in clusters:
            if _similarity(obj, cluster[0]) >= threshold:
                cluster.append(obj)
                placed = True
                break
        if not placed:
            clusters.append([obj])
    return [_majority_merge(c) for c in clusters]


def extraire_table_avec_vote(table_cfg, texte_table, n_matches_mots_cles):
    """Remplace un simple appel llm_extract() pour une table repetee : genere
    N_VOTES_REPEATED fois, fusionne par vote flou. Retourne (payload, meta) ou
    meta documente ce qui s'est passe (utile pour les diagnostics, section 15)."""
    table_name = table_cfg["table"]
    force_min_items = n_matches_mots_cles >= RETRIEVAL_MIN_MATCHES
    schema = build_json_schema(table_cfg, force_min_items=force_min_items)
    max_tokens = table_cfg.get("max_tokens", MAX_NEW_TOKENS)

    runs = []
    n_vides = 0
    for _ in range(N_VOTES_REPEATED):
        try:
            raw = llm_extract(build_prompt(table_cfg, texte_table), schema,
                               max_tokens=max_tokens, repetee=True, sampler=REPEATED_SAMPLER)
            occs = raw.get(table_name) or []
        except Exception:
            occs = []
        if not occs:
            n_vides += 1
        runs.append(occs)

    fused = fuzzy_merge_occurrences(runs) if any(runs) else []
    meta = {"table": table_name, "n_votes": N_VOTES_REPEATED, "n_runs_vides": n_vides,
            "n_occurrences_par_run": [len(r) for r in runs], "n_occurrences_fusionnees": len(fused),
            "min_items_force": force_min_items}
    return fused, meta

def extraire_table_repetee_standard(table_cfg, texte_table):
    """Tables repetees NON listees dans VOTE_TABLES_* : 1 seul appel
    multinomial(T=0.3), sans vote ni minItems force -> comportement identique
    a la version simple (point 4 + point 8), pour garder le cout GPU sous
    controle sur les tables ou le vote n'a pas montre d'effet mesure."""
    table_name = table_cfg["table"]
    schema = build_json_schema(table_cfg)  # force_min_items=False (defaut)
    max_tokens = table_cfg.get("max_tokens", MAX_NEW_TOKENS)
    try:
        raw = llm_extract(build_prompt(table_cfg, texte_table), schema,
                           max_tokens=max_tokens, repetee=True, sampler=REPEATED_SAMPLER)
        occs = raw.get(table_name) or []
    except Exception:
        occs = []
    meta = {"table": table_name, "methode": "repetee_standard_sans_vote", "n_occurrences": len(occs)}
    return occs, meta


# ## 9. Décomposition en 2 étapes de `epr_liste_ae` (correction 2')

# ## 9. Décomposition en 2 étapes de `epr_liste_ae` (correction 2')
# 
# **Problème initial** : un seul prompt avec 9 médicaments dans le même contexte
# fait mélanger le LLM entre les lignes (doses/durées recopiées d'un médicament
# sur un autre — cf. diagnostic EPR, `epr_liste_ae` F1=0,16, la table qui "tue"
# le F1 global).
# 
# **Solution** : au lieu d'un seul appel sur toute la table,
# 1. **Étape 1** — lister uniquement les **noms** d'antiépileptiques mentionnés
#    (schéma JSON minimal : `{"noms": ["...", ...]}`), sur le contexte ciblé
#    habituel de `epr_liste_ae` (mots-clés génériques : Depakine, Levetiracetam...).
# 2. **Étape 2** — pour **chaque nom trouvé**, reconstruire un contexte ciblé
#    **spécifique à ce médicament** (réutilise `retrieve_chunks_for_table` avec
#    le nom du médicament comme mot-clé additionnel) et faire un appel dédié,
#    court, qui extrait dose/durée/réponse/motif_echec pour CE médicament SEUL.
# 
# Le LLM ne voit donc jamais 9 médicaments à la fois dans le même prompt — la
# confusion inter-lignes n'a structurellement plus l'occasion de se produire.
# 



import unicodedata

# Dictionnaire marque -> DCI canonique (correctif A, patch F1 EPR).
# Cause racine des 42 FP observes sur epr_liste_ae : Qwen3-8B liste souvent le
# meme medicament deux fois (une fois sous son nom commercial, une fois sous
# sa DCI) malgre l'instruction du prompt de ne le lister qu'une fois -- limite
# documentee des LLM sur l'equivalence marque/generique (RABBITS, Gallifant et
# al., EMNLP Findings 2024). On ne compte donc plus sur le LLM seul : on
# canonicalise en Python, deterministe, avant l'etape 2.
# A COMPLETER si votre corpus complet mentionne d'autres AE non couverts ici
# (voir le script d'audit propose pour lister les noms reellement presents).
ALIAS_AE = {
    "depakine": "valproate de sodium (Dépakine)", "valproate": "valproate de sodium (Dépakine)",
    "tegretol": "carbamazépine (Tégrétol)", "carbamazepine": "carbamazépine (Tégrétol)",
    "taver": "carbamazépine (Tégrétol)",  # variante ASR observee
    "trileptal": "oxcarbazépine (Trileptal)", "oxcarbamazepine": "oxcarbazépine (Trileptal)",
    "oxcarbazepine": "oxcarbazépine (Trileptal)",
    "lamictal": "lamotrigine (Lamictal)", "lamotrigine": "lamotrigine (Lamictal)",
    "keppra": "lévétiracétam (Levet/Keppra)", "levet": "lévétiracétam (Levet/Keppra)",
    "levetiracetam": "lévétiracétam (Levet/Keppra)",
    "urbanyl": "clobazam (Urbanyl)", "clobazam": "clobazam (Urbanyl)", "frisium": "clobazam (Urbanyl)",
    "rivotril": "clonazépam (Rivotril)", "clonazepam": "clonazépam (Rivotril)",
    "ribotril": "clonazépam (Rivotril)",  # variante ASR observee (audit 4 dossiers EPR)
    "epitomax": "topiramate (Epitomax)", "topiramate": "topiramate (Epitomax)",
    "gardenal": "phénobarbital (Gardénal)", "phenobarbital": "phénobarbital (Gardénal)",
    "sabril": "vigabatrin (Sabril)", "vigabatrin": "vigabatrin (Sabril)",
    "dilantin": "phénytoïne (Dilantin)", "phenytoine": "phénytoïne (Dilantin)",
    "vimpat": "lacosamide (Vimpat)", "lacosamide": "lacosamide (Vimpat)",
    "valium": "diazépam (Valium)", "diazepam": "diazépam (Valium)",
}

def _normaliser_nom_ae(nom):
    s = unicodedata.normalize("NFKD", nom.lower()).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z]", "", s)
    for cle, canon in ALIAS_AE.items():
        if cle in s or s in cle:
            return canon
    return nom.strip()  # inconnu du dictionnaire -> garde tel quel, pas de perte d'info


_NOMS_AE_SCHEMA = {
    "type": "object",
    "properties": {"noms": {"type": "array", "items": {"type": "string"}}},
    "required": ["noms"], "additionalProperties": False,
}

_NOMS_AE_PROMPT_TMPL = """Tu es un assistant d'extraction d'information clinique.
Liste UNIQUEMENT les noms des médicaments antiépileptiques (AE) mentionnés dans
le texte ci-dessous (ex. Dépakine, Levetiracetam, Tegretol, Rivotril...).
N'inclus PAS les corticoïdes (Solupred, Cortef) ni les traitements non
antiépileptiques. N'invente aucun nom. Si un médicament est mentionné plusieurs
fois sous des noms différents (nom commercial / DCI), ne le liste qu'UNE fois,
avec le nom le plus complet rencontré (ex. "Dépakine (valproate)").
Si aucun antiépileptique n'est mentionné, retourne une liste vide.

--- TEXTE DU DOSSIER ---
{texte}
--- FIN DU TEXTE ---

Réponds STRICTEMENT en JSON valide : {{"noms": [...]}}
JSON :
"""

_AE_DETAIL_CHAMPS = [c for c in TABLES_BY_NAME["epr_liste_ae"]["champs"] if c["nom"] != "nom_ae"]
_AE_DETAIL_SCHEMA = {
    "type": "object",
    "properties": {c["nom"]: _champ_to_json_schema(c) for c in _AE_DETAIL_CHAMPS},
    "required": [c["nom"] for c in _AE_DETAIL_CHAMPS], "additionalProperties": False,
}

_AE_DETAIL_PROMPT_TMPL = """Tu es un assistant d'extraction d'information clinique.
Le texte ci-dessous concerne UN SEUL médicament antiépileptique : "{nom_ae}".
Extrais UNIQUEMENT les informations relatives à CE médicament (ignore toute
mention d'un AUTRE médicament si elle apparaît dans le texte).
Règles : "null" (chaîne littérale) = non mentionné pour ce médicament ; "NA" =
non applicable. N'invente rien.

CHAMPS À EXTRAIRE :
- dose (type: texte, "null" possible) → dose/posologie rapportée pour "{nom_ae}"
- duree (type: texte, "null" possible) → durée du traitement rapportée pour "{nom_ae}"
- reponse (type: texte, "null" possible) → réponse clinique rapportée pour "{nom_ae}" (crises libres / réduction / échec)
- motif_echec (type: categoriel, valeurs possibles: ['Inefficacité', 'Effet indésirable', 'aucun'], "null" possible) → motif d'arrêt/échec de CE médicament si applicable

--- TEXTE DU DOSSIER (contexte ciblé sur "{nom_ae}") ---
{texte}
--- FIN DU TEXTE ---

Réponds STRICTEMENT en JSON valide, conforme au schéma.
JSON :
"""


def _retrieve_chunks_pour_medicament(chunks_dossier, table_cfg, nom_ae):
    """Reutilise retrieve_chunks_for_table en AJOUTANT le nom du medicament
    (mots significatifs de nom_ae) aux mots-cles de la table -> contexte
    cible SPECIFIQUE a ce medicament, pas seulement 'antiepileptique' en general."""
    mots_specifiques = [w.strip("()") for w in re.split(r"[\s,/()]+", nom_ae) if len(w.strip("()")) >= 4]
    table_cfg_specifique = dict(table_cfg)
    table_cfg_specifique["mots_cles"] = list(table_cfg.get("mots_cles") or []) + mots_specifiques
    return retrieve_chunks_for_table(chunks_dossier, table_cfg_specifique)


def extraire_liste_ae_deux_etapes(chunks_dossier, table_cfg):
    """Correction 2' : remplace l'appel standard pour epr_liste_ae par une
    extraction en 2 etapes (noms -> details par medicament). Retourne
    (occurrences, meta) au meme format que extraire_table_avec_vote.

    NOTE (bascule Qwen3) : les 2 etapes passent maintenant par llm_extract()
    (au lieu d'appeler outlines.generate.json directement comme avant) afin de
    beneficier du template de chat centralise (format_prompt_chat, section 4) -
    un seul point de controle pour tous les appels LLM du notebook."""
    # --- Etape 1 : lister les noms ---
    chunks_table, diag1 = retrieve_chunks_for_table(chunks_dossier, table_cfg)
    texte_table = _tronquer_si_besoin(assemble_contexte(chunks_table))
    prompt_noms = _NOMS_AE_PROMPT_TMPL.format(texte=texte_table)
    try:
        noms = llm_extract(prompt_noms, _NOMS_AE_SCHEMA, max_tokens=300,
                            repetee=False, sampler=REPEATED_SAMPLER).get("noms", [])
    except Exception:
        noms = []
    noms_bruts = [n.strip() for n in noms if n and n.strip()]
    noms, _vus = [], set()
    for _n in noms_bruts:
        _canon = _normaliser_nom_ae(_n)
        if _canon not in _vus:
            _vus.add(_canon)
            noms.append(_canon)  # correctif A : canonicalisation marque/DCI avant l'etape 2

    # --- Etape 2 : details par medicament, contexte cible sur CE medicament ---
    occurrences, details_par_med = [], []
    for nom_ae in noms:
        chunks_med, diag_med = _retrieve_chunks_pour_medicament(chunks_dossier, table_cfg, nom_ae)
        texte_med = _tronquer_si_besoin(assemble_contexte(chunks_med))
        prompt_med = _AE_DETAIL_PROMPT_TMPL.format(nom_ae=nom_ae, texte=texte_med)
        try:
            detail = llm_extract(prompt_med, _AE_DETAIL_SCHEMA, max_tokens=400,
                                  repetee=False, sampler=GREEDY_SAMPLER)
        except Exception:
            detail = {c["nom"]: None for c in _AE_DETAIL_CHAMPS}
        detail = {k: _cast_scalar(v, next(c["type"] for c in _AE_DETAIL_CHAMPS if c["nom"] == k))
                  for k, v in detail.items()}
        occ = {"nom_ae": nom_ae, **detail}
        occurrences.append(occ)
        details_par_med.append({"nom_ae": nom_ae, "n_chunks_specifiques": diag_med["n_retenus"]})

    meta = {"table": "epr_liste_ae", "methode": "decomposition_2_etapes",
            "n_noms_ae_identifies": len(noms), "noms_ae": noms, "details_par_medicament": details_par_med}
    return occurrences, meta


# ## 10. Fenêtre de récence pour les tables `priorite_recente` (correction 3')

# ## 10. Fenêtre de récence pour les tables `priorite_recente` (correction 3')
# 
# **Problème initial** : la règle 10 du template ("retiens toujours le plus
# récent") est une instruction molle — le diagnostic EPR montre que le LLM
# retient parfois le **premier** statut rencontré malgré cette règle
# (`epr_suivi` : "Libre de crises" précoce au lieu de "Épilepsie active" final).
# 
# **Solution déterministe, sans logique de parsing de dates fragile** : les
# chunks sont déjà chronologiquement ordonnés (`idx`). Pour une table
# `priorite_recente=true` **non répétée** (`sep_evolution`, `sep_suivi`,
# `epr_pharmacoresistance`, `epr_suivi`), on lance une **2e extraction** sur une
# **fenêtre restreinte au dernier tiers chronologique** des chunks retenus par le
# contexte ciblé (point 4) — le LLM ne peut alors physiquement plus "voir" les
# statuts anciens, donc ne peut plus s'y raccrocher. Si cette extraction "fenêtre
# récente" diffère de l'extraction "contexte complet" ET n'est pas vide, elle
# **prime** sur cette dernière.
# 



def extraire_avec_fenetre_recente(chunks_dossier, table_cfg, resultat_contexte_complet, texte_contexte_complet):
    """table_cfg doit avoir priorite_recente=true et repetee=false.
    Retourne (resultat_final, meta)."""
    table_name = table_cfg["table"]
    chunks_table, _ = retrieve_chunks_for_table(chunks_dossier, table_cfg)
    n = len(chunks_table)
    k = max(RECENCE_MIN_CHUNKS, int(n * RECENCE_FRACTION))
    chunks_recents = chunks_table[-k:] if n > 0 else []

    if len(chunks_recents) == n:
        # La fenetre couvre deja tout le contexte -> pas de 2e appel utile
        return resultat_contexte_complet, {"table": table_name, "fenetre_appliquee": False,
                                            "raison": "fenetre de recence == contexte complet (dossier trop court)"}

    texte_recent = _tronquer_si_besoin(assemble_contexte(chunks_recents))
    schema = build_json_schema(table_cfg)
    try:
        raw = llm_extract(build_prompt(table_cfg, texte_recent), schema,
                           max_tokens=table_cfg.get("max_tokens", MAX_NEW_TOKENS), repetee=False)
        resultat_recent = cast_raw_table(raw.get(table_name), table_cfg)
    except Exception as exc:
        return resultat_contexte_complet, {"table": table_name, "fenetre_appliquee": False,
                                            "raison": f"echec extraction fenetre recente: {exc}"}

    resultat_recent_non_vide = bool(resultat_recent) and any(
        v not in (None, "NA") for v in resultat_recent.values())
    diverge = resultat_recent != resultat_contexte_complet

    meta = {"table": table_name, "fenetre_appliquee": True,
            "n_chunks_fenetre": len(chunks_recents), "n_chunks_total": n,
            "resultat_contexte_complet": resultat_contexte_complet,
            "resultat_fenetre_recente": resultat_recent,
            "divergence_detectee": diverge}

    if diverge and resultat_recent_non_vide:
        meta["decision"] = "fenetre_recente_retenue (diverge du contexte complet, priorite a la plus recente)"
        return resultat_recent, meta
    meta["decision"] = "contexte_complet_conserve (pas de divergence exploitable)"
    return resultat_contexte_complet, meta

# ## 11. Vérification ciblée — two-pass léger (point 5')

# ## 11. Vérification ciblée — two-pass léger (point 5')
# 
# **Principe retenu (volontairement différent d'une régénération complète)** :
# la 2e passe ne redemande PAS tout le JSON — elle reçoit le JSON déjà extrait
# + le même contexte ciblé, et doit seulement **confirmer ou corriger** chaque
# champ contre le texte, jamais réinventer. C'est plus court (moins de tokens de
# sortie), plus contraint (le schéma JSON est le même, donc pas de dérive de
# format), et documenté comme réduisant les hallucinations sans le coût d'une
# double génération complète.
# 
# Appliqué uniquement aux tables listées dans `VERIFICATION_TABLES_EPR` /
# `VERIFICATION_TABLES_SEP` (tables EPR historiquement à F1 bas) — pas aux 29
# tables, pour rester dans un budget GPU raisonnable sur Kaggle gratuit.
# 



_VERIF_PROMPT_TMPL = """Tu es un assistant de VÉRIFICATION d'une extraction clinique déjà réalisée.
Voici le JSON précédemment extrait pour la table "{table}", et le texte source
sur lequel il a été construit. Pour CHAQUE champ de CHAQUE occurrence :
- Si la valeur est explicitement soutenue par le texte : garde-la telle quelle.
- Si la valeur n'est PAS explicitement soutenue par le texte (invention, extrapolation,
  confusion avec une autre occurrence/un autre patient) : corrige-la en "null" (non
  mentionné) ou "NA" (non applicable) selon le cas, comme demandé par les règles
  originales de la table ci-dessous.
- Ne supprime PAS une occurrence entière sauf si RIEN dans le texte ne la soutient.
- N'AJOUTE aucune occurrence qui ne serait pas déjà dans le JSON fourni.

RAPPEL DES RÈGLES DE LA TABLE :
{regles}

JSON À VÉRIFIER :
{json_a_verifier}

--- TEXTE SOURCE ---
{texte}
--- FIN DU TEXTE ---

Réponds STRICTEMENT avec le JSON corrigé (même structure), sans commentaire.
JSON :
"""


def _regles_courtes(table_cfg):
    lignes = [f"- {c['nom']} (type: {c['type']}"
              + (f", valeurs: {c['valeurs']}" if c.get('valeurs') else "")
              + (", NA possible" if c.get('na_possible') else "") + ")"
              for c in table_cfg["champs"]]
    return "\n".join(lignes)


def verifier_table(table_cfg, payload_extrait, texte_table):
    """Point 5' : passe de verification ciblee. payload_extrait est deja
    caste (types Python natifs) ; on le re-serialise en litteraux "null"/"NA"/
    "true"/"false" attendus par le schema JSON contraint (coherence avec le
    reste du pipeline), puis on re-caste le resultat corrige."""
    table_name = table_cfg["table"]
    if not payload_extrait:
        return payload_extrait, {"table": table_name, "verification_appliquee": False,
                                  "raison": "rien a verifier (payload vide)"}

    def _to_litteral(v, ctype):
        if v is None:
            return "null"
        if v == "NA":
            return "NA"
        if ctype == "booleen":
            return "true" if v else "false"
        return str(v)

    champ_types = {c["nom"]: c["type"] for c in table_cfg["champs"]}
    def _obj_to_litteral(obj):
        return {k: _to_litteral(v, champ_types.get(k, "texte")) for k, v in obj.items()
                if not k.startswith("evidence_span_") and not k.startswith("_")}

    if table_cfg["repetee"]:
        payload_litteral = [_obj_to_litteral(o) for o in payload_extrait]
    else:
        payload_litteral = _obj_to_litteral(payload_extrait)

    json_a_verifier = json.dumps({table_name: payload_litteral}, ensure_ascii=False, indent=2)
    prompt = _VERIF_PROMPT_TMPL.format(table=table_name, regles=_regles_courtes(table_cfg),
                                        json_a_verifier=json_a_verifier, texte=texte_table)
    schema = build_json_schema(table_cfg)
    max_tokens = table_cfg.get("max_tokens", MAX_NEW_TOKENS)
    try:
        raw = llm_extract(prompt, schema, max_tokens=max_tokens, repetee=table_cfg["repetee"], sampler=GREEDY_SAMPLER)
        corrige = cast_raw_table(raw.get(table_name), table_cfg)
    except Exception as exc:
        return payload_extrait, {"table": table_name, "verification_appliquee": False,
                                  "raison": f"echec verification: {exc}"}

    diverge = corrige != payload_extrait
    meta = {"table": table_name, "verification_appliquee": True, "divergence_detectee": diverge,
            "avant": payload_extrait, "apres": corrige}
    return corrige, meta

# ## 12. Extraction — orchestration complète (fusion de toutes les briques)

# ## 12. Extraction — orchestration complète (fusion de toutes les briques)
# 
# Pour chaque table, dans l'ordre :
# 1. Contexte ciblé (point 4).
# 2. Cas spécial `epr_liste_ae` -> décomposition 2 étapes (section 9), le reste
#    de la boucle standard est court-circuité pour cette table.
# 3. Sinon, extraction standard : **vote** (section 8) si `repetee=true`, **fenêtre
#    de récence** (section 10) si `priorite_recente=true` et `repetee=false`.
# 4. Cast, evidence_span, validation déterministe (section 5, inchangé).
# 5. **Vérification ciblée** (section 11) si la table est dans `VERIFICATION_TABLES_*`.
# 6. En fin de dossier EPR : correction 4 (`etiologie_principale` unique).
# 



def extraire_un_dossier(chunks_dossier, tables_config, registre_label):
    resultats, erreurs, diagnostics_contexte, meta_ameliorations = {}, [], [], []
    verification_tables = VERIFICATION_TABLES_EPR if registre_label == "EPR" else VERIFICATION_TABLES_SEP

    for i, table_cfg in enumerate(tables_config, 1):
        table_name = table_cfg["table"]

        # --- Point 4 : contexte ciblé pour CETTE table ---
        chunks_table, diag = retrieve_chunks_for_table(chunks_dossier, table_cfg)
        texte_table = _tronquer_si_besoin(assemble_contexte(chunks_table))
        diag["table"] = table_name
        diagnostics_contexte.append(diag)

        statut_filtre = "filtré" if diag["filtre"] else "dossier entier"
        print(f"  ({i}/{len(tables_config)}) [{registre_label}] {table_name} "
              f"— contexte {statut_filtre} : {diag['n_retenus']}/{diag['n_total']} chunks "
              f"({len(texte_table)} car.)")

        try:
            # --- Correctif C (patch F1 EPR) : si le filtrage par mots-cles n'a
            # trouve AUCUNE correspondance reelle sur une table repetee, on
            # impose [] directement plutot que de risquer une hallucination
            # sur le repli "dossier entier" (cause des FP sur epr_chirurgie,
            # epr_alternatives_therapeutiques, epr_bilan_orthophonique...) ---
            if table_cfg["repetee"] and table_name != "epr_liste_ae" and diag["n_matches_mots_cles"] == 0:
                print(f"      -> 0 mot-cle trouve : extraction sautee, [] impose "
                      f"(evite l'hallucination sur repli dossier entier)")
                raw_table = []
                meta = {"table": table_name, "methode": "force_vide_zero_match"}
                meta_ameliorations.append(meta)
                raw_table = attach_evidence(raw_table, table_cfg, texte_table)
                valide, issues = validate_result({table_name: raw_table}, table_cfg, texte_table)
                resultats[table_name] = valide[table_name]
                if issues:
                    erreurs.append({"table": table_name, "issues": issues})
                continue

            # --- Cas special : decomposition 2 etapes (correction 2') ---
            if table_name == "epr_liste_ae":
                occs, meta = extraire_liste_ae_deux_etapes(chunks_dossier, table_cfg)
                print(f"      -> decomposition 2 etapes : {meta['n_noms_ae_identifies']} AE identifie(s) : {meta['noms_ae']}")
                raw_table = occs

            # --- Tables repetees : vote SEULEMENT sur VOTE_TABLES_* (cout GPU
            # cible sur les tables ou le diagnostic F1 a montre un effet reel) ---
            elif table_cfg["repetee"]:
                vote_tables = VOTE_TABLES_EPR if registre_label == "EPR" else VOTE_TABLES_SEP
                if table_name in vote_tables:
                    occs, meta = extraire_table_avec_vote(table_cfg, texte_table, diag["n_matches_mots_cles"])
                    print(f"      -> vote x{meta['n_votes']} : {meta['n_occurrences_par_run']} occ/run -> "
                          f"{meta['n_occurrences_fusionnees']} fusionnee(s) ({meta['n_runs_vides']} run(s) vide(s))")
                else:
                    occs, meta = extraire_table_repetee_standard(table_cfg, texte_table)
                    print(f"      -> extraction repetee standard (hors vote) : {meta['n_occurrences']} occurrence(s)")
                raw_table = cast_raw_table(occs, table_cfg)  # les occurrences sont deja des dicts "bruts" (str)

            # --- Tables non repetees ---
            else:
                schema = build_json_schema(table_cfg)
                raw = llm_extract(build_prompt(table_cfg, texte_table), schema,
                                   max_tokens=table_cfg.get("max_tokens", MAX_NEW_TOKENS), repetee=False)
                raw_table = cast_raw_table(raw.get(table_name), table_cfg)
                meta = {"table": table_name, "methode": "standard"}

                # --- Fenetre de recence (correction 3') ---
                if table_cfg.get("priorite_recente"):
                    raw_table, meta_recence = extraire_avec_fenetre_recente(
                        chunks_dossier, table_cfg, raw_table, texte_table)
                    meta = {**meta, "fenetre_recente": meta_recence}
                    if meta_recence.get("divergence_detectee"):
                        print(f"      -> fenetre de recence : divergence detectee -> {meta_recence['decision']}")

            meta_ameliorations.append(meta)

            # --- Post-traitement standard (section 5, inchange) ---
            raw_table = attach_evidence(raw_table, table_cfg, texte_table)
            valide, issues = validate_result({table_name: raw_table}, table_cfg, texte_table)
            resultats[table_name] = valide[table_name]

            # --- Verification ciblee (point 5') ---
            if table_name in verification_tables:
                corrige, meta_verif = verifier_table(table_cfg, resultats[table_name], texte_table)
                meta_ameliorations.append(meta_verif)
                if meta_verif.get("divergence_detectee"):
                    print(f"      -> verification ciblee : correction(s) appliquee(s)")
                    corrige = attach_evidence(corrige, table_cfg, texte_table)
                    _, issues2 = validate_result({table_name: corrige}, table_cfg, texte_table)
                    resultats[table_name] = corrige
                    issues = issues2

            if issues:
                erreurs.append({"table": table_name, "issues": issues})

        except Exception as exc:
            erreurs.append({"table": table_name, "issues": [f"exception: {exc}"]})
            resultats[table_name] = [] if table_cfg["repetee"] else None

    # --- Correction 4 : etiologie_principale unique ---
    if registre_label == "EPR" and "epr_etiologie" in resultats:
        resultats["epr_etiologie"], etio_issue = enforce_unique_etiologie_principale(resultats["epr_etiologie"])
        if etio_issue:
            erreurs.append({"table": "epr_etiologie", "issues": [etio_issue]})

    return {"registre": registre_label, "patient_id": None, "tables": resultats,
            "a_verifier": erreurs, "diagnostics_contexte": diagnostics_contexte,
            "diagnostics_ameliorations": meta_ameliorations}

# ## 13. Extraction du dossier SEP -> `sep_extraction.json`

# ---------------------------------------------------------------------------
# API HTTP — appelée par le backend Node (entitesExtractionClient.js)
# ---------------------------------------------------------------------------

class ChunkIn(BaseModel):
    texte: str
    date: str | None = None


class ExtractionRequest(BaseModel):
    registre: str                 # "SEP" ou "EPR"
    chunks: list[ChunkIn]


@app.post("/extract-entites")
def extract_entites(req: ExtractionRequest):
    if req.registre not in ("SEP", "EPR"):
        raise HTTPException(400, "registre doit être 'SEP' ou 'EPR'.")
    if not req.chunks:
        raise HTTPException(422, "Aucun chunk de texte fourni.")

    chunks_dossier = [{"texte": c.texte, "date": c.date} for c in req.chunks]
    tables_config = TABLES_SEP if req.registre == "SEP" else TABLES_EPR

    try:
        resultat = extraire_un_dossier(chunks_dossier, tables_config, req.registre)
    except NameError as exc:
        raise HTTPException(
            500,
            f"Service mal configuré : une fonction/config du notebook n'a pas été collée ({exc}).",
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Échec de l'extraction : {exc}")
    finally:
        gc.collect()

    return resultat


@app.get("/health")
def health():
    try:
        r = requests.get("http://localhost:8003/health", timeout=3)
        qwen_ok = r.status_code == 200
    except requests.RequestException:
        qwen_ok = False
    return {"service": "ok", "qwen_8003": qwen_ok}

# =============================================================================
# =============================================================================
# ## EXTENSION STREAMING — logique du notebook streaming_dedup_corrige.ipynb
# =============================================================================
# Tout ce qui suit reprend, sans rien omettre, la logique du notebook
# streaming (template incrémental état-déjà-connu, fusion à verrous durs,
# filtre mots-clés, passage de rattrapage, vérification ciblée finale),
# adaptée pour réutiliser telles quelles les fonctions déjà définies
# ci-dessus (llm_extract, build_json_schema, cast_raw_table, attach_evidence,
# validate_result, verifier_table, TABLES_SEP/EPR/TABLES_BY_NAME,
# VERIFICATION_TABLES_SEP/EPR, enforce_unique_etiologie_principale,
# _tronquer_si_besoin, MAX_NEW_TOKENS, MAX_CONTEXTE_TABLE_CHARS,
# GREEDY_SAMPLER, REPEATED_SAMPLER — pas de doublon, pas de rechargement de
# modèle : le LLM appelé est le même serveur Qwen local sur le port 8003).
#
# AJOUT (mémoire base de données) : la route /extract-entites-streaming
# accepte un champ optionnel `etat_initial` = les valeurs déjà enregistrées
# en base pour ce patient (à envoyer depuis entitesExtractionClient.js côté
# Node, qui a déjà accès à Postgres). Ces valeurs deviennent le POINT DE
# DÉPART de l'état streaming : le LLM les voit comme "déjà connu" dès le
# premier chunk, exactement comme s'il s'agissait de chunks déjà traités
# lors d'une visite précédente — ça évite les doublons/contradictions avec
# les données déjà existantes du dossier patient.
# =============================================================================

# --- Paramètres streaming (section 1 du notebook) ---
TAILLE_FENETRE_GLISSANTE = 2  # chunk courant + 1 précédent
ACTIVER_VOTE_STREAMING = False
N_VOTES_STREAMING = 2


# NOTE : join_chunks() existe déjà plus haut dans ce fichier (section 6 du
# service "dossier complet") mais opère sur des chunks {"texte": ...} alors
# que le pipeline streaming utilise {"text": ...} (clé reprise telle quelle
# du notebook) -> variante dédiée avec un nom distinct pour ne rien casser.
def join_chunks_streaming(chunks: list) -> str:
    return "\n".join(c["text"] for c in chunks)


# ---------------------------------------------------------------------------
# ## Template incrémental (section 7 du notebook) — inchangé
# ---------------------------------------------------------------------------
INCR_TEMPLATE_STR = """
Tu es un assistant d'extraction d'information clinique pour un registre médical pédiatrique,
en cours de DICTÉE EN DIRECT (transcription automatique par segments de ~30 secondes).

Tu dois mettre à jour la table "{{ table }}" : {{ description }}

--- INFORMATIONS DÉJÀ CONNUES POUR CETTE TABLE (extraites des segments précédents de CE patient) ---
{{ etat_existant_json }}
--- FIN DES INFORMATIONS DÉJÀ CONNUES ---

RÈGLES STRICTES (à respecter absolument) :
1. Le nouveau segment ci-dessous peut REFORMULER une information déjà listée ci-dessus (le médecin
   répète ou reformule un point déjà dicté). Dans ce cas, NE CRÉE PAS de nouvelle entrée en double —
   ignore cette répétition.
2. Si le nouveau segment apporte un DÉTAIL SUPPLÉMENTAIRE sur une entrée déjà connue (ex. précise la
   dose d'un médicament déjà listé, ou la date d'un examen déjà mentionné sans date), renvoie cette
   entrée MISE À JOUR avec le champ complété — pas une entrée en plus.
3. Ne renvoie QUE les entrées NOUVELLES ou MISES À JOUR par CE segment précis — jamais la liste
   complète déjà connue si ce segment ne la concerne pas.
4. N'invente rien. "null" (chaîne littérale) = non mentionné dans ce segment. "NA" = explicitement
   non applicable. Ne mets "Inconnue" que si le texte l'indique explicitement.
{% if repetee %}
5. Retourne une LISTE (peut être vide []) des entrées nouvelles ou mises à jour par ce segment.
{% else %}
5. Retourne l'information si CE segment la mentionne, sinon "null" pour chaque champ.
{% endif %}
{% if priorite_recente %}
6. Ce segment est CHRONOLOGIQUEMENT PLUS RÉCENT que toute information déjà connue ci-dessus — si ce
   segment contredit une information déjà connue (ex. un statut de suivi différent), donne la valeur
   telle que CE segment la décrit maintenant, sans te soucier de la contradiction : c'est attendu,
   c'est une mise à jour, pas une erreur.
{% endif %}

CHAMPS DE LA TABLE :
{% for champ in champs %}
- {{ champ.nom }} (type: {{ champ.type }}{% if champ.valeurs %}, valeurs possibles: {{ champ.valeurs }}{% endif %}{% if champ.na_possible %}, "NA" possible{% endif %})
{% endfor %}

--- NOUVEAU SEGMENT À ANALYSER (+ 1 segment précédent pour le contexte) ---
{{ texte_fenetre }}
--- FIN DU SEGMENT ---

Réponds STRICTEMENT en JSON valide, sans aucun texte avant ou après, conforme au schéma fourni.
JSON :
"""

_incr_env = Environment(trim_blocks=True, lstrip_blocks=True)
_INCR_TEMPLATE = _incr_env.from_string(INCR_TEMPLATE_STR)


def _valeur_vers_litteral(v, champ_type):
    if v is None:
        return "null"
    if v == "NA":
        return "NA"
    if champ_type == "booleen":
        return "true" if v else "false"
    return str(v)


def _etat_vers_litteral(table_cfg, etat_table):
    champ_types = {c["nom"]: c["type"] for c in table_cfg["champs"]}

    def _obj(o):
        return {k: _valeur_vers_litteral(v, champ_types.get(k, "texte"))
                 for k, v in o.items() if not k.startswith("evidence_span_")}

    if table_cfg["repetee"]:
        return [_obj(o) for o in (etat_table or [])]
    return _obj(etat_table) if etat_table else {c["nom"]: "null" for c in table_cfg["champs"]}


def build_prompt_incremental(table_cfg, etat_table, texte_fenetre):
    etat_json = json.dumps({table_cfg["table"]: _etat_vers_litteral(table_cfg, etat_table)},
                            ensure_ascii=False, indent=2)
    return _INCR_TEMPLATE.render(
        table=table_cfg["table"], description=table_cfg["description"],
        repetee=table_cfg["repetee"], champs=table_cfg["champs"],
        priorite_recente=table_cfg.get("priorite_recente", False),
        etat_existant_json=etat_json, texte_fenetre=texte_fenetre,
    )


# ---------------------------------------------------------------------------
# ## Fusion à verrous durs (section 8 du notebook) — inchangé
# ---------------------------------------------------------------------------
import re
import unicodedata

ALIAS_AE = {
    "depakine": "valproate de sodium (Dépakine)", "valproate": "valproate de sodium (Dépakine)",
    "tegretol": "carbamazépine (Tégrétol)", "carbamazepine": "carbamazépine (Tégrétol)",
    "taver": "carbamazépine (Tégrétol)",
    "trileptal": "oxcarbazépine (Trileptal)", "oxcarbamazepine": "oxcarbazépine (Trileptal)",
    "oxcarbazepine": "oxcarbazépine (Trileptal)",
    "lamictal": "lamotrigine (Lamictal)", "lamotrigine": "lamotrigine (Lamictal)",
    "keppra": "lévétiracétam (Levet/Keppra)", "levet": "lévétiracétam (Levet/Keppra)",
    "levetiracetam": "lévétiracétam (Levet/Keppra)",
    "urbanyl": "clobazam (Urbanyl)", "clobazam": "clobazam (Urbanyl)", "frisium": "clobazam (Urbanyl)",
    "rivotril": "clonazépam (Rivotril)", "clonazepam": "clonazépam (Rivotril)",
    "ribotril": "clonazépam (Rivotril)",
    "epitomax": "topiramate (Epitomax)", "topiramate": "topiramate (Epitomax)",
    "gardenal": "phénobarbital (Gardénal)", "phenobarbital": "phénobarbital (Gardénal)",
    "sabril": "vigabatrin (Sabril)", "vigabatrin": "vigabatrin (Sabril)",
    "dilantin": "phénytoïne (Dilantin)", "phenytoine": "phénytoïne (Dilantin)",
    "vimpat": "lacosamide (Vimpat)", "lacosamide": "lacosamide (Vimpat)",
    "valium": "diazépam (Valium)", "diazepam": "diazépam (Valium)",
}


def _normaliser_nom_ae(nom):
    s = unicodedata.normalize("NFKD", nom.lower()).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z]", "", s)
    for cle, canon in ALIAS_AE.items():
        if cle in s or s in cle:
            return canon
    return nom.strip()


def _champs_date(table_cfg):
    return [c["nom"] for c in table_cfg["champs"] if c["type"] == "date"]


def _champs_categoriels(table_cfg):
    return [c["nom"] for c in table_cfg["champs"] if c["type"] == "categoriel"]


def _meme_occurrence(table_cfg, existante, nouvelle):
    """Verrous durs : jamais la même occurrence si un champ date ou
    catégoriel non-null diffère entre les deux (leçon vise-70-v6)."""
    for champ in _champs_date(table_cfg) + _champs_categoriels(table_cfg):
        va, vb = existante.get(champ), nouvelle.get(champ)
        if va not in (None, "NA", "null") and vb not in (None, "NA", "null") and str(va) != str(vb):
            return False
    return True


def fusionner_occurrences_repetee(table_cfg, etat_liste, nouvelles_occs):
    etat_liste = list(etat_liste or [])
    table_name = table_cfg["table"]

    for nouvelle in nouvelles_occs:
        if table_name == "epr_liste_ae" and "nom_ae" in nouvelle:
            nouvelle["nom_ae"] = _normaliser_nom_ae(str(nouvelle.get("nom_ae") or ""))
            match_idx = next((i for i, e in enumerate(etat_liste)
                               if _normaliser_nom_ae(str(e.get("nom_ae") or ""))
                               == nouvelle["nom_ae"]), None)
        else:
            match_idx = next((i for i, e in enumerate(etat_liste)
                               if _meme_occurrence(table_cfg, e, nouvelle)), None)

        if match_idx is None:
            etat_liste.append(nouvelle)
        else:
            for k, v in nouvelle.items():
                if v not in (None, "null"):
                    etat_liste[match_idx][k] = v
    return etat_liste


def fusionner_champs_simples(table_cfg, etat_dict, nouveau_dict):
    etat_dict = dict(etat_dict or {c["nom"]: None for c in table_cfg["champs"]})
    priorite_recente = table_cfg.get("priorite_recente", False)
    for k, v in (nouveau_dict or {}).items():
        if v in (None, "null"):
            continue
        if priorite_recente or etat_dict.get(k) in (None, "null", "NA"):
            etat_dict[k] = v
    return etat_dict


# ---------------------------------------------------------------------------
# ## Extraction incrémentale — 1 appel LLM par (table, chunk pertinent)
#    (section 9 du notebook) — réutilise llm_extract() de ton service
# ---------------------------------------------------------------------------
def table_concernee_par_chunk(chunk_texte, table_cfg):
    """Test 0-coût (pas de LLM) : ce chunk contient-il un mot-clé de la
    table ? Décide si on déclenche un appel LLM pour cette table."""
    mots_cles = [m.lower() for m in (table_cfg.get("mots_cles") or [])]
    if not mots_cles:
        return True
    return any(m in chunk_texte.lower() for m in mots_cles)


def extraire_incremental(table_cfg, etat_table, texte_fenetre):
    table_name = table_cfg["table"]
    prompt = build_prompt_incremental(table_cfg, etat_table, texte_fenetre)
    schema = build_json_schema(table_cfg)
    max_tokens = min(table_cfg.get("max_tokens", MAX_NEW_TOKENS), MAX_NEW_TOKENS)

    if ACTIVER_VOTE_STREAMING and table_cfg["repetee"]:
        runs = []
        for _ in range(N_VOTES_STREAMING):
            try:
                raw = llm_extract(prompt, schema, max_tokens=max_tokens, repetee=True, sampler=REPEATED_SAMPLER)
                runs.append(raw.get(table_name) or [])
            except Exception:
                runs.append([])
        payload = [occ for run in runs for occ in run]
    else:
        # Sampler différencié : multinomial pour les tables répétées (listes),
        # greedy pour les objets uniques — évite le collapse silencieux vers []
        # (bug corrigé dans le notebook, section 9).
        sampler_a_utiliser = REPEATED_SAMPLER if table_cfg["repetee"] else GREEDY_SAMPLER
        try:
            raw = llm_extract(prompt, schema, max_tokens=max_tokens,
                               repetee=table_cfg["repetee"], sampler=sampler_a_utiliser)
            payload = raw.get(table_name)
            if table_cfg["repetee"]:
                payload = payload or []
        except Exception:
            payload = [] if table_cfg["repetee"] else {c["nom"]: None for c in table_cfg["champs"]}

    return cast_raw_table(payload, table_cfg)


# ---------------------------------------------------------------------------
# ## Orchestration streaming (section 11 du notebook) — inchangé
# ---------------------------------------------------------------------------
def extraire_dossier_streaming(chunks, tables_config, registre_label, etat_initial=None,
                                appliquer_rattrapage=False, appliquer_verification=False):
    """etat_initial (optionnel) : donnees deja enregistrees en base pour ce
    patient (visites precedentes, saisie manuelle...), au format
    {nom_table: {...} ou [...]}. Sert de POINT DE DEPART -> le LLM les voit
    comme "deja connu" des le 1er chunk de CETTE visite, exactement comme un
    chunk deja traite (meme mecanisme, section 7/8 du notebook).

    IMPORTANT (cas d'usage "petit audio par visite") : par defaut
    appliquer_rattrapage=False et appliquer_verification=False. Ces deux
    passages du notebook original ont ete concus pour un dossier COMPLET
    traite en une fois ; ici, chaque appel ne recoit que le texte d'UNE
    visite (quelques chunks de ~30s). Si on les laissait actives par
    defaut :
    - le rattrapage forcerait un appel LLM sur les ~25 tables non
      mentionnees a CHAQUE visite (cout inutile, cette visite ne les
      concerne simplement pas) ;
    - la verification comparerait TOUT l'etat (y compris les valeurs
      venues de etat_initial, remontant a des visites anterieures) au
      texte de cette seule visite -> tout ce qui n'y est pas repete
      serait a tort marque "non soutenu par le texte" et efface.
    Utilise ces flags uniquement pour un appel de fin de dossier complet
    (ex. cloture finale d'un patient, tout le texte cumule en entree)."""
    etat = {t["table"]: ([] if t["repetee"] else {c["nom"]: None for c in t["champs"]})
            for t in tables_config}
    if etat_initial:
        for t in tables_config:
            table_name = t["table"]
            val_bdd = etat_initial.get(table_name)
            if not val_bdd:
                continue
            if t["repetee"]:
                etat[table_name] = fusionner_occurrences_repetee(t, etat[table_name], list(val_bdd))
            else:
                etat[table_name] = fusionner_champs_simples(t, etat[table_name], val_bdd)
    journal = []
    n_appels_llm_total = 0
    texte_dossier_complet = join_chunks_streaming(chunks)

    for pos, chunk in enumerate(chunks):
        chunk_precedent = chunks[pos - 1] if pos > 0 else None
        texte_fenetre = (chunk_precedent["text"] + "\n" + chunk["text"]) if chunk_precedent else chunk["text"]
        texte_fenetre = _tronquer_si_besoin(texte_fenetre, MAX_CONTEXTE_TABLE_CHARS)

        for table_cfg in tables_config:
            table_name = table_cfg["table"]
            if not table_concernee_par_chunk(chunk["text"], table_cfg):
                continue

            avant = etat[table_name]
            nouvelles = extraire_incremental(table_cfg, avant, texte_fenetre)
            n_appels_llm_total += N_VOTES_STREAMING if (ACTIVER_VOTE_STREAMING and table_cfg["repetee"]) else 1

            if table_cfg["repetee"]:
                apres = fusionner_occurrences_repetee(table_cfg, avant, nouvelles)
                n_ajouts = len(apres) - len(avant)
            else:
                apres = fusionner_champs_simples(table_cfg, avant, nouvelles)
                n_ajouts = sum(1 for k in apres if apres.get(k) != (avant or {}).get(k))
            etat[table_name] = apres

            if n_ajouts != 0 or (table_cfg["repetee"] and nouvelles):
                journal.append({"chunk_idx": chunk["idx"], "table": table_name,
                                 "n_occurrences_renvoyees_par_llm": len(nouvelles) if table_cfg["repetee"] else None,
                                 "delta_etat": n_ajouts})

    # Passage de rattrapage : DESACTIVE par defaut (voir docstring). Utile
    # seulement en fin de dossier complet, pas visite par visite.
    if appliquer_rattrapage:
        tables_jamais_declenchees = [t for t in tables_config
                                      if not any(j["table"] == t["table"] for j in journal)]
        for table_cfg in tables_jamais_declenchees:
            table_name = table_cfg["table"]
            avant = etat[table_name]
            nouvelles = extraire_incremental(table_cfg, avant, texte_dossier_complet)
            n_appels_llm_total += 1

            if table_cfg["repetee"]:
                apres = fusionner_occurrences_repetee(table_cfg, avant, nouvelles)
                n_ajouts = len(apres) - len(avant)
            else:
                apres = fusionner_champs_simples(table_cfg, avant, nouvelles)
                n_ajouts = sum(1 for k in apres if apres.get(k) != (avant or {}).get(k))
            etat[table_name] = apres

            if n_ajouts != 0 or (table_cfg["repetee"] and nouvelles):
                journal.append({"chunk_idx": None, "table": table_name,
                                 "n_occurrences_renvoyees_par_llm": len(nouvelles) if table_cfg["repetee"] else None,
                                 "delta_etat": n_ajouts})

    # Post-traitement final : evidence_span + validation déterministe (réutilise ton service)
    resultats, erreurs = {}, []
    for table_cfg in tables_config:
        table_name = table_cfg["table"]
        payload = attach_evidence(etat[table_name], table_cfg, texte_dossier_complet)
        valide, issues = validate_result({table_name: payload}, table_cfg, texte_dossier_complet)
        resultats[table_name] = valide[table_name]
        if issues:
            erreurs.append({"table": table_name, "issues": issues})

    # Vérification ciblée finale : DESACTIVEE par defaut (voir docstring).
    # Si activée, restreinte aux SEULES tables réellement touchées par CETTE
    # visite (journal) — jamais aux tables qui n'ont que des valeurs héritées
    # de etat_initial, pour ne jamais effacer une donnée d'une visite
    # antérieure sous prétexte qu'elle n'est pas répétée dans ce court texte.
    if appliquer_verification:
        verification_tables = VERIFICATION_TABLES_EPR if registre_label == "EPR" else VERIFICATION_TABLES_SEP
        tables_touchees_cette_visite = {j["table"] for j in journal}
        for table_name in verification_tables & tables_touchees_cette_visite:
            table_cfg = TABLES_BY_NAME[table_name]
            corrige, meta_verif = verifier_table(table_cfg, resultats[table_name], texte_dossier_complet)
            if meta_verif.get("divergence_detectee"):
                corrige = attach_evidence(corrige, table_cfg, texte_dossier_complet)
                _, issues2 = validate_result({table_name: corrige}, table_cfg, texte_dossier_complet)
                resultats[table_name] = corrige
            n_appels_llm_total += 1

    if registre_label == "EPR" and "epr_etiologie" in resultats:
        resultats["epr_etiologie"], etio_issue = enforce_unique_etiologie_principale(resultats["epr_etiologie"])
        if etio_issue:
            erreurs.append({"table": "epr_etiologie", "issues": [etio_issue]})

    return {"registre": registre_label, "patient_id": None, "tables": resultats,
            "a_verifier": erreurs, "journal_streaming": journal, "n_appels_llm_total": n_appels_llm_total}


# ---------------------------------------------------------------------------
# ## Nouvelle route API — même contrat d'entrée que /extract-entites
# ---------------------------------------------------------------------------
class ChunkInStreaming(BaseModel):
    texte: str
    date: str | None = None


class ExtractionRequestStreaming(BaseModel):
    registre: str
    chunks: list[ChunkInStreaming]
    patient_id: str | None = None
    # Donnees deja en base pour ce patient, a fournir par le backend Node
    # (il a deja acces a Postgres) : {nom_table: {...} ou [...]}. Sert de
    # "memoire" injectee au LLM des le depart -> pas de duplication/contradiction
    # avec ce qui a deja ete saisi lors d'une visite precedente.
    etat_initial: dict | None = None
    # False (defaut) = usage normal, un appel par petite visite/consultation :
    #   rattrapage et verification finale desactives (voir docstring de
    #   extraire_dossier_streaming - eviter d'effacer les donnees de visites
    #   anterieures faute d'etre repetees dans le court texte de CETTE visite).
    # True = a utiliser seulement pour un appel de cloture sur le texte
    #   cumule de tout le dossier (rare, ex. relecture finale avant export).
    finaliser: bool = False


@app.post("/extract-entites-streaming")
def extract_entites_streaming(req: ExtractionRequestStreaming):
    if req.registre not in ("SEP", "EPR"):
        raise HTTPException(400, "registre doit être 'SEP' ou 'EPR'.")
    if not req.chunks:
        raise HTTPException(422, "Aucun chunk de texte fourni.")

    # Adapte les clés {texte,date} du contrat existant vers {idx,text}
    # attendues par extraire_dossier_streaming (repris du notebook).
    chunks_dossier = [{"idx": i, "text": c.texte} for i, c in enumerate(req.chunks)]
    tables_config = TABLES_SEP if req.registre == "SEP" else TABLES_EPR

    try:
        resultat = extraire_dossier_streaming(
            chunks_dossier, tables_config, req.registre,
            etat_initial=req.etat_initial,
            appliquer_rattrapage=req.finaliser,
            appliquer_verification=req.finaliser,
        )
    except NameError as exc:
        raise HTTPException(500, f"Service mal configuré : import manquant ({exc}).")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Échec de l'extraction streaming : {exc}")

    resultat["patient_id"] = req.patient_id
    return resultat


# NOTE — comment fournir `etat_initial` (mémoire base de données) :
# Côté Node (entitesExtractionClient.js), avant d'appeler cette route :
#   1. SELECT * des tables sep_*/epr_* deja enregistrees pour ce patient_id
#      (les memes tables que schema_registre.sql, memes noms de colonnes que
#      les champs des schemas YAML ci-dessus)
#   2. Construire { "sep_identification_clinique": {...}, "sep_edss_visites": [...], ... }
#      (objet pour les tables non repetees, liste d'objets pour les tables repetees)
#   3. L'envoyer dans le corps de la requete comme `etat_initial`
# Le LLM verra alors ces valeurs comme "deja connu" des le premier chunk
# (meme prompt/regles que pour l'etat accumule au fil des chunks), et ne les
# dupliquera pas / completera les champs manquants au lieu de recreer une
# entree, exactement comme demande.
#
# NOTE — "vrai" live token-par-chunk (optionnel, pour plus tard) :
# Cette route recalcule tout l'etat streaming a chaque appel HTTP (a partir
# de etat_initial + tous les chunks envoyes). Pour un vrai live ou un seul
# NOUVEAU chunk arrive a la fois sans renvoyer tout l'historique, il faudrait
# persister l'etat intermediaire entre deux appels (memoire process ou table
# Postgres dediee) et exposer une route /extract-entites-streaming/chunk qui
# ne traite qu'un seul nouveau chunk a la fois. Dis-moi si tu veux cette
# version plus tard.