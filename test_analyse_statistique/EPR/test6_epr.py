"""
============================================================================
ANALYSE : Consanguinité parentale x Étiologie génétique confirmée (mode AR)
Registre : Épilepsie pharmacorésistante
Test principal demandé par l'encadrante : Chi²  |  + corrections méthodologiques
============================================================================

Corrections apportées suite à la revue méthodologique :

  1. INDÉPENDANCE DES OBSERVATIONS (jumeaux / fratrie, ex. EPR_MBH_001 et
     EPR_MBH_002) : les patients sont regroupés par "famille" (heuristique
     sur le pseudonyme, cf. section 5) puis :
       (a) analyse principale = 1 seule observation par famille (dédoublonnée)
       (b) analyse de sensibilité = GEE avec structure de corrélation
           intra-famille (cov_struct=Exchangeable), sur TOUTES les
           observations, pour vérifier que la conclusion tient malgré la
           non-indépendance.

  2. DUPLICATION DES LIGNES : la CTE `etiologie_toute` utilise désormais
     DISTINCT ON (pseudonyme) pour ne garder qu'une ligne par patient même
     si plusieurs lignes `etiologie_principale = TRUE` existent par erreur
     de saisie. Le nombre de patients concernés est compté et rapporté
     (transparence), pas juste silencieusement corrigé.

  3. TEST DE TENDANCE (Cochran-Armitage) : ajouté en complément du Chi² r×c,
     pour tester l'effet dose-réponse attendu par la littérature tunisienne
     (Non < 3e degré < 2e degré < 1er degré de consanguinité).

  4. FLUX D'INCLUSION (style STROBE) : nombre de patients exclus pour
     consanguinité manquante (NULL/'NA') et pour absence d'étiologie
     principale codée, rapportés explicitement.

  5. GRAPHE DE PROPORTIONS corrigé : pourcentage d'étiologie AR confirmée
     par groupe de consanguinité (plus lisible cliniquement que des barres
     empilées à 100 %).

Tables / colonnes utilisées (noms exacts du dictionnaire de données) :
    patients(pseudonyme, registre)
    epr_antecedents(pseudonyme, consanguinite_parentale, consanguinite_degre)
    epr_etiologie(pseudonyme, categorie_etiologique, etiologie_principale)
    epr_genetique(pseudonyme, mode_transmission)

Dépendances :
    pip install pandas scipy numpy sqlalchemy psycopg2-binary matplotlib statsmodels
"""

import re
import sys
from datetime import datetime

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy import stats

# ============================================================================
# 0. CONFIGURATION — À MODIFIER : mets juste tes identifiants PostgreSQL ici
# ============================================================================

USE_DEMO_DATA = False   # False = se connecte à ta vraie base ; True = données simulées

DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "dbname": "nom_de_ta_base",
    "user": "ton_utilisateur",
    "password": "ton_mot_de_passe",
}

REGISTRE_EPILEPSIE = "EPRLEPSIE"  # valeur exacte de patients.registre (à vérifier/corriger)

# Fichiers de sortie
RAPPORT_TXT = "rapport_analyse_chi2.txt"
CSV_RESULTATS = "resultats_chi2_consanguinite_etiologie.csv"
GRAPHIQUE_CONTINGENCE = "graphique_contingence.png"
GRAPHIQUE_PROPORTIONS = "graphique_proportions_AR.png"
GRAPHIQUE_TENDANCE = "graphique_tendance_degre.png"


# ============================================================================
# 1. CLASSE "TEE" — duplique tout ce qui est print() vers l'écran ET le .txt
# ============================================================================

class Tee:
    def __init__(self, fichier):
        self.terminal = sys.stdout
        self.fichier = fichier

    def write(self, message):
        self.terminal.write(message)
        self.fichier.write(message)

    def flush(self):
        self.terminal.flush()
        self.fichier.flush()


fichier_rapport = open(RAPPORT_TXT, "w", encoding="utf-8")
sys.stdout = Tee(fichier_rapport)

print("=" * 70)
print("RAPPORT D'ANALYSE — CONSANGUINITÉ x ÉTIOLOGIE GÉNÉTIQUE AR")
print(f"Généré le : {datetime.now().strftime('%d/%m/%Y à %H:%M')}")
print("=" * 70)
print()


# ============================================================================
# 2. REQUÊTES SQL (noms exacts de ta base)
# ============================================================================

# --- 2a. Requête principale d'extraction ---------------------------------
# Priorité 5 (convention NULL/'NA') : on exclut les deux du champ
# consanguinite_parentale avant tout test statistique.
# Correction 2 : DISTINCT ON (pseudonyme) dans etiologie_toute pour ne
# jamais dupliquer un patient si plusieurs lignes "principale=TRUE" existent.
REQUETE_SQL = """
WITH patients_epr AS (
    SELECT pseudonyme
    FROM patients
    WHERE registre = %(registre)s
),

consanguinite AS (
    SELECT
        pseudonyme,
        consanguinite_parentale,
        consanguinite_degre
    FROM epr_antecedents
    WHERE consanguinite_parentale IS NOT NULL
      AND consanguinite_parentale::text <> 'NA'
),

etiologie_toute AS (
    -- Correction 2 : DISTINCT ON garantit 1 ligne par patient, même en cas
    -- d'anomalie de saisie (plusieurs "etiologie_principale = TRUE").
    -- Voir REQUETE_QC_SQL pour le décompte des patients concernés.
    SELECT DISTINCT ON (pseudonyme)
        pseudonyme, categorie_etiologique
    FROM epr_etiologie
    WHERE etiologie_principale = TRUE
    ORDER BY pseudonyme, id
),

etiologie_ar AS (
    SELECT DISTINCT et.pseudonyme
    FROM etiologie_toute et
    INNER JOIN epr_genetique g
            ON g.pseudonyme = et.pseudonyme
    WHERE et.categorie_etiologique = 'Génétique'
      AND g.mode_transmission = 'AR'
      -- AND g.classification_acmg IN ('Classe IV', 'Classe V')  -- filtre optionnel
)

SELECT
    p.pseudonyme,
    c.consanguinite_parentale,
    c.consanguinite_degre,
    CASE WHEN ar.pseudonyme IS NOT NULL THEN 'AR_confirmee' ELSE 'Autre_etiologie' END
        AS groupe_etiologique
FROM patients_epr p
INNER JOIN consanguinite   c  ON c.pseudonyme  = p.pseudonyme
INNER JOIN etiologie_toute et ON et.pseudonyme = p.pseudonyme
LEFT JOIN  etiologie_ar    ar ON ar.pseudonyme = p.pseudonyme;
"""

# --- 2b. Requête de contrôle qualité / flux d'inclusion (Correction 4) ----
REQUETE_QC_SQL = """
SELECT
    (SELECT COUNT(*) FROM patients WHERE registre = %(registre)s)
        AS n_total_registre,

    (SELECT COUNT(*) FROM patients p
     INNER JOIN epr_antecedents a ON a.pseudonyme = p.pseudonyme
     WHERE p.registre = %(registre)s
       AND (a.consanguinite_parentale IS NULL OR a.consanguinite_parentale::text = 'NA'))
        AS n_exclus_consanguinite_manquante,

    (SELECT COUNT(*) FROM patients p
     WHERE p.registre = %(registre)s
       AND NOT EXISTS (
           SELECT 1 FROM epr_etiologie e
           WHERE e.pseudonyme = p.pseudonyme AND e.etiologie_principale = TRUE
       ))
        AS n_exclus_sans_etiologie_principale,

    (SELECT COUNT(*) FROM (
        SELECT pseudonyme FROM epr_etiologie
        WHERE etiologie_principale = TRUE
        GROUP BY pseudonyme HAVING COUNT(*) > 1
     ) t)
        AS n_patients_etiologie_principale_multiple
;
"""


# ============================================================================
# 3. RÉCUPÉRATION DES DONNÉES (réelles ou démo)
# ============================================================================

def charger_donnees_reelles():
    from sqlalchemy import create_engine, text

    url = (
        f"postgresql+psycopg2://{DB_CONFIG['user']}:{DB_CONFIG['password']}"
        f"@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['dbname']}"
    )
    engine = create_engine(url)

    with engine.connect() as conn:
        df = pd.read_sql(text(REQUETE_SQL), conn, params={"registre": REGISTRE_EPILEPSIE})
        qc = pd.read_sql(text(REQUETE_QC_SQL), conn, params={"registre": REGISTRE_EPILEPSIE})

    return df, qc.iloc[0].to_dict()


def generer_donnees_demo(seed=42):
    """
    Données simulées reproduisant volontairement les problèmes réels signalés :
    - patients avec consanguinité non renseignée (NULL) -> exclus
    - patients sans étiologie principale codée -> exclus
    - patients avec étiologie principale dupliquée (anomalie de saisie) -> comptés
    - familles de taille 2 (jumeaux/fratrie, ex. EPR_Fxxxx_001 / _002) qui
      partagent le même génome -> même statut clinique simulé
    """
    rng = np.random.default_rng(seed)

    n_total_registre = 300
    taux_manquant_consanguinite = 0.06
    taux_sans_etiologie = 0.08
    taux_etiologie_dupliquee = 0.03
    taux_familles_taille2 = 0.08  # ~8% de fratries/jumeaux

    unites = []
    total = 0
    while total < n_total_registre:
        taille = 2 if (rng.random() < taux_familles_taille2 and n_total_registre - total >= 2) else 1
        unites.append(taille)
        total += taille

    lignes = []
    for i, taille in enumerate(unites, start=1):
        code_famille = f"EPR_F{i:04d}"

        # Le statut clinique "vrai" est déterminé UNE FOIS par famille
        # (des jumeaux partagent le même génome -> même étiologie).
        groupe_famille = rng.choice(["AR_confirmee", "Autre_etiologie"], p=[0.30, 0.70])
        p_oui = 0.65 if groupe_famille == "AR_confirmee" else 0.15
        if rng.random() < p_oui:
            consang_famille = "Oui"
            degre_famille = rng.choice(["1er degré", "2e degré", "3e degré"], p=[0.6, 0.3, 0.1])
        else:
            consang_famille = "Non"
            degre_famille = "NA"

        for membre in range(1, taille + 1):
            pseudonyme = f"{code_famille}_{membre:03d}"
            consanguinite_val = consang_famille
            degre_val = degre_famille

            if rng.random() < taux_manquant_consanguinite:
                consanguinite_val = None
                degre_val = None

            lignes.append({
                "pseudonyme": pseudonyme,
                "consanguinite_parentale": consanguinite_val,
                "consanguinite_degre": degre_val,
                "groupe_etiologique_brut": groupe_famille,
                "a_etiologie_principale": rng.random() >= taux_sans_etiologie,
                "a_etiologie_dupliquee": rng.random() < taux_etiologie_dupliquee,
            })

    df_brut = pd.DataFrame(lignes)

    qc = {
        "n_total_registre": len(df_brut),
        "n_exclus_consanguinite_manquante": int(df_brut["consanguinite_parentale"].isna().sum()),
        "n_exclus_sans_etiologie_principale": int((~df_brut["a_etiologie_principale"]).sum()),
        "n_patients_etiologie_principale_multiple": int(df_brut["a_etiologie_dupliquee"].sum()),
    }

    df_filtre = df_brut[
        df_brut["consanguinite_parentale"].notna() & df_brut["a_etiologie_principale"]
    ].copy()
    df_filtre = df_filtre.rename(columns={"groupe_etiologique_brut": "groupe_etiologique"})
    df_filtre = df_filtre[
        ["pseudonyme", "consanguinite_parentale", "consanguinite_degre", "groupe_etiologique"]
    ].reset_index(drop=True)

    return df_filtre, qc


# ============================================================================
# 4. TEST DE TENDANCE DE COCHRAN-ARMITAGE (Correction 3)
# ============================================================================

def test_tendance_cochran_armitage(df, col_categorie, ordre_scores, col_binaire):
    """
    ordre_scores : dict {categorie: score_ordinal_croissant}
    col_binaire  : colonne 0/1 (1 = évènement, ex. AR_confirmee)
    Retourne None si le test est dégénéré (pas de variance).
    """
    lignes = []
    for cat, score in ordre_scores.items():
        sous = df[df[col_categorie] == cat]
        n_i = len(sous)
        r_i = int(sous[col_binaire].sum())
        lignes.append((cat, score, n_i, r_i))

    N = sum(n_i for _, _, n_i, _ in lignes)
    R = sum(r_i for _, _, _, r_i in lignes)
    if N == 0 or R == 0 or R == N:
        return None

    p_barre = R / N
    T = sum(score * (r_i - n_i * p_barre) for _, score, n_i, r_i in lignes)
    somme_n_x2 = sum(n_i * score ** 2 for _, score, n_i, _ in lignes)
    somme_n_x = sum(n_i * score for _, score, n_i, _ in lignes)
    S = somme_n_x2 - (somme_n_x ** 2) / N
    var_T = p_barre * (1 - p_barre) * S
    if var_T <= 0:
        return None

    z = T / np.sqrt(var_T)
    chi2_tendance = z ** 2
    p_valeur = 2 * (1 - stats.norm.cdf(abs(z)))

    tableau = pd.DataFrame(lignes, columns=["categorie", "score", "n", "n_AR_confirmee"])
    tableau["pourcentage_AR"] = (tableau["n_AR_confirmee"] / tableau["n"].replace(0, np.nan) * 100).round(1)

    return {"tableau": tableau, "Z": z, "chi2": chi2_tendance, "ddl": 1, "p_value": p_valeur}


# ============================================================================
# 5. PIPELINE PRINCIPAL
# ============================================================================

try:
    # ------------------------------------------------------------------
    # 5.1 Chargement des données + flux d'inclusion (Correction 4)
    # ------------------------------------------------------------------
    if USE_DEMO_DATA:
        df, qc = generer_donnees_demo()
        print(">> Mode DÉMO activé (mets USE_DEMO_DATA = False pour ta vraie base)\n")
    else:
        df, qc = charger_donnees_reelles()

    print("=" * 70)
    print("FLUX D'INCLUSION DES PATIENTS (style STROBE)")
    print("=" * 70)
    print(f"Patients dans le registre épilepsie                          : {qc['n_total_registre']}")
    print(f"  (-) exclus : consanguinité non renseignée (NULL/'NA')      : {qc['n_exclus_consanguinite_manquante']}")
    print(f"  (-) exclus : aucune étiologie principale codée             : {qc['n_exclus_sans_etiologie_principale']}")
    print(f"Patients inclus dans l'analyse (avant gestion des familles)  : {len(df)}")
    print(f"\n[Anomalie de saisie signalée, non-excluante] patients avec "
          f">1 ligne 'etiologie_principale = TRUE' : {qc['n_patients_etiologie_principale_multiple']} "
          f"(dédoublonnés automatiquement par DISTINCT ON, voir requête SQL)\n")

    # ------------------------------------------------------------------
    # 5.2 Détection de la structure familiale (Correction 1)
    # ------------------------------------------------------------------
    # Heuristique : le pseudonyme encode la famille avec un suffixe numérique
    # de membre (ex. EPR_MBH_001 / EPR_MBH_002 -> famille "EPR_MBH").
    # /!\ À VALIDER avec l'encadrante : ceci est une heuristique de nommage,
    # pas un vrai identifiant de fratrie en base. Idéalement, ajouter une
    # colonne dédiée `famille_id` / `fratrie_id` au schéma.
    df["famille_id"] = df["pseudonyme"].apply(lambda x: re.sub(r"_\d+$", "", x))

    tailles_familles = df.groupby("famille_id").size()
    familles_multiples = tailles_familles[tailles_familles > 1]

    print("=" * 70)
    print("STRUCTURE FAMILIALE DÉTECTÉE (heuristique sur le pseudonyme)")
    print("=" * 70)
    print(f"Nombre de familles avec >1 patient inclus : {len(familles_multiples)}")
    print(f"Nombre de patients concernés (non indépendants) : {familles_multiples.sum()}")
    if len(familles_multiples) > 0:
        print("Exemples de familles multiples :")
        print(df[df["famille_id"].isin(familles_multiples.index)]
              [["pseudonyme", "famille_id", "consanguinite_parentale", "groupe_etiologique"]]
              .sort_values("famille_id").head(10).to_string(index=False))
    print()

    df["est_AR_confirmee"] = (df["groupe_etiologique"] == "AR_confirmee").astype(int)
    df["consanguinite_bin"] = (df["consanguinite_parentale"] == "Oui").astype(int)

    # Analyse principale = 1 seule observation par famille (garde la première
    # par ordre de pseudonyme, choix arbitraire mais déterministe et documenté)
    df_dedup = (
        df.sort_values("pseudonyme")
          .drop_duplicates(subset="famille_id", keep="first")
          .reset_index(drop=True)
    )
    print(f"N avant dédoublonnage familial : {len(df)}")
    print(f"N après dédoublonnage familial (analyse principale) : {len(df_dedup)}\n")

    # ------------------------------------------------------------------
    # 5.3 ANALYSE PRINCIPALE : Chi² sur données dédoublonnées (1/famille)
    # ------------------------------------------------------------------
    table_contingence = pd.crosstab(df_dedup["consanguinite_parentale"], df_dedup["groupe_etiologique"])
    table_contingence = table_contingence.reindex(index=["Oui", "Non"], columns=["AR_confirmee", "Autre_etiologie"])

    print("=" * 70)
    print("TABLEAU DE CONTINGENCE (analyse principale, 1 observation/famille)")
    print("=" * 70)
    print(table_contingence)
    print()

    chi2, p_value, ddl, effectifs_attendus = stats.chi2_contingency(table_contingence, correction=True)
    effectifs_attendus_df = pd.DataFrame(
        effectifs_attendus, index=table_contingence.index, columns=table_contingence.columns
    )

    print("=" * 70)
    print("EFFECTIFS THÉORIQUES ATTENDUS SOUS H0")
    print("=" * 70)
    print(effectifs_attendus_df.round(2))
    condition_validite = (effectifs_attendus >= 5).all()
    print(f"\nCondition Ei >= 5 respectée : {condition_validite}\n")

    print("=" * 70)
    print("RÉSULTAT DU TEST CHI² (correction de Yates)")
    print("=" * 70)
    print(f"Chi² = {chi2:.3f}   ddl = {ddl}   p-value = {p_value:.4g}")
    alpha = 0.05
    if p_value < alpha:
        print(f"\n=> p < {alpha} : association statistiquement significative.")
    else:
        print(f"\n=> p >= {alpha} : pas d'association significative détectée.")

    if not condition_validite:
        print("\n" + "=" * 70)
        print("Ei < 5 dans au moins une case -> test exact de Fisher recommandé")
        print("=" * 70)
        odds_ratio_fisher, p_value_fisher = stats.fisher_exact(table_contingence)
        print(f"OR (Fisher) = {odds_ratio_fisher:.3f}   p-value (Fisher) = {p_value_fisher:.4g}")

    a = table_contingence.loc["Oui", "AR_confirmee"]
    b = table_contingence.loc["Oui", "Autre_etiologie"]
    c = table_contingence.loc["Non", "AR_confirmee"]
    d = table_contingence.loc["Non", "Autre_etiologie"]
    odds_ratio = (a * d) / (b * c)
    se_log_or = np.sqrt(1 / a + 1 / b + 1 / c + 1 / d)
    ic95_bas = np.exp(np.log(odds_ratio) - 1.96 * se_log_or)
    ic95_haut = np.exp(np.log(odds_ratio) + 1.96 * se_log_or)

    print("\n" + "=" * 70)
    print("ODDS RATIO (analyse principale, données dédoublonnées)")
    print("=" * 70)
    print(f"OR = {odds_ratio:.2f}   IC95% = [{ic95_bas:.2f} ; {ic95_haut:.2f}]")

    n_total_dedup = table_contingence.to_numpy().sum()
    cramers_v = np.sqrt(chi2 / (n_total_dedup * (min(table_contingence.shape) - 1)))
    print("\n" + "=" * 70)
    print("CRAMÉR'S V")
    print("=" * 70)
    print(f"V = {cramers_v:.3f}  (repère : ~0.1 faible / ~0.3 modérée / ~0.5+ forte)")

    # ------------------------------------------------------------------
    # 5.4 ANALYSE DE SENSIBILITÉ : GEE (structure de corrélation familiale)
    # ------------------------------------------------------------------
    # Utilise TOUTES les observations (y compris les jumeaux/fratrie), mais
    # corrige les erreurs-types pour la corrélation intra-famille au lieu
    # de simplement en supprimer une partie. Objectif : vérifier que la
    # conclusion du Chi² "naïf" tient toujours une fois l'indépendance
    # correctement modélisée.
    print("\n" + "=" * 70)
    print("ANALYSE DE SENSIBILITÉ : GEE (structure familiale, Exchangeable)")
    print("=" * 70)
    resultat_gee = None
    try:
        import statsmodels.api as sm
        import statsmodels.formula.api as smf

        modele_gee = smf.gee(
            "est_AR_confirmee ~ consanguinite_bin",
            groups="famille_id",
            data=df,
            family=sm.families.Binomial(),
            cov_struct=sm.cov_struct.Exchangeable(),
        )
        resultat_gee = modele_gee.fit()

        coef = resultat_gee.params["consanguinite_bin"]
        p_gee = resultat_gee.pvalues["consanguinite_bin"]
        ic_gee = resultat_gee.conf_int().loc["consanguinite_bin"]
        or_gee = np.exp(coef)
        or_gee_bas, or_gee_haut = np.exp(ic_gee[0]), np.exp(ic_gee[1])

        print(f"N (toutes observations, y compris jumeaux/fratrie) = {len(df)}")
        print(f"OR (GEE, corrigé structure familiale) = {or_gee:.2f}   "
              f"IC95% = [{or_gee_bas:.2f} ; {or_gee_haut:.2f}]")
        print(f"p-value (GEE) = {p_gee:.4g}")
        print(f"\n=> Comparaison : OR naïf (dédoublonné) = {odds_ratio:.2f} (p={p_value:.4g})  "
              f"vs  OR GEE (corrigé) = {or_gee:.2f} (p={p_gee:.4g})")
        if (p_value < alpha) == (p_gee < alpha):
            print("   Conclusion cohérente entre les deux approches.")
        else:
            print("   /!\\ Conclusion DIVERGENTE entre les deux approches — "
                  "à investiguer avant toute interprétation clinique.")
    except Exception as err:
        print(f"GEE non calculé (erreur : {err}). "
              f"Vérifier l'installation de statsmodels et la taille de l'échantillon.")

    # ------------------------------------------------------------------
    # 5.5 TEST DE TENDANCE DE COCHRAN-ARMITAGE (Correction 3)
    # ------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("TEST DE TENDANCE DE COCHRAN-ARMITAGE (effet dose-réponse)")
    print("=" * 70)
    print("H0 : pas de tendance linéaire du % d'étiologie AR selon le degré de parenté")
    print("Ordre croissant : Non (0) < 3e degré (1) < 2e degré (2) < 1er degré (3)\n")

    df_dedup["categorie_ordinale"] = np.where(
        df_dedup["consanguinite_parentale"] == "Non", "Non", df_dedup["consanguinite_degre"]
    )
    ordre_scores = {"Non": 0, "3e degré": 1, "2e degré": 2, "1er degré": 3}

    n_categorie_manquante = df_dedup["categorie_ordinale"].isna().sum()
    if n_categorie_manquante > 0:
        print(f"(NB : {n_categorie_manquante} patients consanguins sans degré précisé, exclus du test de tendance)\n")

    resultat_ca = test_tendance_cochran_armitage(
        df_dedup.dropna(subset=["categorie_ordinale"]),
        "categorie_ordinale", ordre_scores, "est_AR_confirmee",
    )

    if resultat_ca is not None:
        print(resultat_ca["tableau"].to_string(index=False))
        print(f"\nZ = {resultat_ca['Z']:.3f}   Chi² tendance = {resultat_ca['chi2']:.3f}   "
              f"ddl = {resultat_ca['ddl']}   p-value = {resultat_ca['p_value']:.4g}")
        if resultat_ca["p_value"] < alpha:
            print("\n=> Tendance dose-réponse statistiquement significative : le % d'étiologie AR "
                  "augmente avec la proximité du lien de parenté des parents.")
        else:
            print("\n=> Pas de tendance dose-réponse significative détectée sur cet échantillon.")
    else:
        print("Test de tendance non calculable (effectifs insuffisants ou dégénérés).")

    # ------------------------------------------------------------------
    # 5.6 GRAPHES
    # ------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("GRAPHES GÉNÉRÉS")
    print("=" * 70)

    # Graphe 1 : effectifs observés (analyse principale dédoublonnée)
    fig, ax = plt.subplots(figsize=(7, 5))
    table_contingence.plot(kind="bar", ax=ax, color=["#2E86AB", "#A23B72"])
    ax.set_title("Consanguinité parentale selon l'étiologie génétique\n(1 observation/famille)")
    ax.set_xlabel("Consanguinité parentale")
    ax.set_ylabel("Nombre de patients")
    ax.legend(title="Groupe étiologique")
    plt.xticks(rotation=0)
    plt.tight_layout()
    plt.savefig(GRAPHIQUE_CONTINGENCE, dpi=150)
    plt.close(fig)
    print(f"- {GRAPHIQUE_CONTINGENCE} : effectifs observés (barres)")

    # Graphe 2 (CORRECTION 5) : % d'étiologie AR par groupe de consanguinité
    prop = df_dedup.groupby("consanguinite_parentale")["est_AR_confirmee"].mean().reindex(["Oui", "Non"]) * 100
    effectifs = df_dedup.groupby("consanguinite_parentale").size().reindex(["Oui", "Non"])

    fig, ax = plt.subplots(figsize=(6, 5))
    barres = ax.bar(prop.index, prop.values, color=["#2E86AB", "#A23B72"], width=0.5)
    for rect, (idx, val) in zip(barres, prop.items()):
        ax.text(rect.get_x() + rect.get_width() / 2, val + 1.5,
                 f"{val:.1f}%\n(n={effectifs[idx]})", ha="center", fontsize=10)
    ax.set_ylabel("% d'étiologie AR confirmée")
    ax.set_xlabel("Consanguinité parentale")
    ax.set_title("Pourcentage d'étiologie AR confirmée\nselon la consanguinité parentale")
    ax.set_ylim(0, min(100, prop.max() + 20))
    plt.tight_layout()
    plt.savefig(GRAPHIQUE_PROPORTIONS, dpi=150)
    plt.close(fig)
    print(f"- {GRAPHIQUE_PROPORTIONS} : % AR confirmée par groupe (barres, cliniquement parlant)")

    # Graphe 3 : effet dose-réponse (Cochran-Armitage)
    if resultat_ca is not None:
        tab = resultat_ca["tableau"]
        fig, ax = plt.subplots(figsize=(7, 5))
        ax.plot(tab["categorie"], tab["pourcentage_AR"], marker="o", linewidth=2, color="#F18F01")
        for x, y, n in zip(tab["categorie"], tab["pourcentage_AR"], tab["n"]):
            ax.annotate(f"{y:.1f}%\n(n={n})", (x, y), textcoords="offset points",
                        xytext=(0, 10), ha="center", fontsize=9)
        ax.set_ylabel("% d'étiologie AR confirmée")
        ax.set_xlabel("Degré de consanguinité parentale")
        ax.set_title("Effet dose-réponse : % AR selon le degré de consanguinité\n"
                      f"(Cochran-Armitage, p={resultat_ca['p_value']:.3g})")
        ax.set_ylim(0, min(100, tab["pourcentage_AR"].max() + 20))
        plt.tight_layout()
        plt.savefig(GRAPHIQUE_TENDANCE, dpi=150)
        plt.close(fig)
        print(f"- {GRAPHIQUE_TENDANCE} : tendance dose-réponse par degré")

    # ------------------------------------------------------------------
    # 5.7 EXPORT CSV DES RÉSULTATS
    # ------------------------------------------------------------------
    lignes_resultats = [
        ("N_avant_dedoublonnage", len(df)),
        ("N_apres_dedoublonnage", len(df_dedup)),
        ("Chi2", chi2), ("ddl", ddl), ("p_value_chi2", p_value),
        ("OR", odds_ratio), ("IC95_bas", ic95_bas), ("IC95_haut", ic95_haut),
        ("Cramers_V", cramers_v),
    ]
    if resultat_gee is not None:
        lignes_resultats += [
            ("OR_GEE_corrige_famille", or_gee),
            ("IC95_bas_GEE", or_gee_bas), ("IC95_haut_GEE", or_gee_haut),
            ("p_value_GEE", p_gee),
        ]
    if resultat_ca is not None:
        lignes_resultats += [
            ("Chi2_tendance_CochranArmitage", resultat_ca["chi2"]),
            ("p_value_tendance", resultat_ca["p_value"]),
        ]

    resultats = pd.DataFrame(lignes_resultats, columns=["indicateur", "valeur"])
    resultats.to_csv(CSV_RESULTATS, index=False)

    print("\n" + "=" * 70)
    print("FICHIERS GÉNÉRÉS")
    print("=" * 70)
    print(f"- {RAPPORT_TXT}\n- {CSV_RESULTATS}\n- {GRAPHIQUE_CONTINGENCE}\n- {GRAPHIQUE_PROPORTIONS}")
    if resultat_ca is not None:
        print(f"- {GRAPHIQUE_TENDANCE}")

    print("\n" + "=" * 70)
    print("FIN DU RAPPORT")
    print("=" * 70)

finally:
    sys.stdout = sys.__stdout__
    fichier_rapport.close()
    print(f"\nAnalyse terminée. Rapport complet disponible dans : {RAPPORT_TXT}")
