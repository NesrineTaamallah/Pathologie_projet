const axios = require('axios');

// Port du microservice entities_extraction_service.py (FastAPI).
// À côté de tes services existants : Whisper:8001, PaddleOCR:8002, Qwen (LLM):8003.
const ENTITES_SERVICE_URL = process.env.ENTITES_SERVICE_URL || 'http://localhost:8004';

async function extraireEntitesMedicales(registre, chunks) {
  const { data } = await axios.post(
    `${ENTITES_SERVICE_URL}/extract-entites`,
    { registre, chunks },
    { timeout: 10 * 60 * 1000 } // extraction multi-tables + vote + vérification : peut être long
  );
  return data;
}

// --- Streaming (chunk par chunk, avec memoire des donnees deja en base) ---
// etatInitial (optionnel) : { nom_table: {...} ou [...] }, construit a partir
// des lignes deja enregistrees pour ce patient (voir chargerEtatInitialDepuisDB
// plus bas). patientId (optionnel) : juste renvoye tel quel dans la reponse.
// finaliser (optionnel, defaut false) : voir docstring cote Python — a ne
// passer a true QUE pour une cloture finale sur le texte cumule de tout le
// dossier, jamais pour une visite normale.
async function extraireEntitesMedicalesStreaming(registre, chunks, { patientId, etatInitial, finaliser } = {}) {
  const { data } = await axios.post(
    `${ENTITES_SERVICE_URL}/extract-entites-streaming`,
    { registre, chunks, patient_id: patientId, etat_initial: etatInitial, finaliser: !!finaliser },
    { timeout: 10 * 60 * 1000 }
  );
  return data;
}

// ---------------------------------------------------------------------------
// Chargement de l'état déjà connu depuis Postgres ("mémoire" du patient)
// ---------------------------------------------------------------------------
// Mapping table -> répétée (1-N, donc liste) ou non (1-1, donc objet), tel
// que défini dans backend/config/schema_registre.sql. Les noms de colonnes
// de chaque table correspondent EXACTEMENT aux noms de champs des schémas
// YAML (SEP_SCHEMA_YAML / EPR_SCHEMA_YAML) — vérifié colonne par colonne.
const TABLES_SEP = {
  sep_identification_clinique: false,
  sep_antecedents: false,
  sep_presentation_initiale: false,
  sep_poussees: true,
  sep_edss_visites: true,
  sep_evolution: false,
  sep_irm: true,
  sep_biologie_lcr: true,
  sep_potentiels_evoques: true,
  sep_traitement_fond: true,
  sep_suivi: false,
};

const TABLES_EPR = {
  epr_identification_clinique: false,
  epr_antecedents: false,
  epr_type_crise: true,
  epr_frequence_crises: true,
  epr_examen: true,
  epr_etiologie: true,
  epr_pharmacoresistance: false,
  epr_liste_ae: true,
  epr_eeg: true,
  epr_imagerie: true,
  epr_genetique: true,
  epr_bilan_prechirurgical: true,
  epr_chirurgie: true,
  epr_alternatives_therapeutiques: true,
  epr_bilan_orthophonique: true,
  epr_bilan_neuropsy: true,
  epr_bilan_ergotherapique: true,
  epr_suivi: false,
};

// Colonnes techniques à ne jamais renvoyer au LLM (pas des champs cliniques).
const COLONNES_EXCLUES = new Set(['id', 'pseudonyme', 'delai_diagnostic_mois', 'frequence_normalisee_mois']);

function _ligneVersChamps(ligne) {
  const out = {};
  for (const [k, v] of Object.entries(ligne)) {
    if (COLONNES_EXCLUES.has(k)) continue;
    out[k] = v === null ? null : v; // pg renvoie deja bool/number/string corrects, dates en 'YYYY-MM-DD'
  }
  return out;
}

/**
 * Construit etatInitial pour un patient donné, à partir de ce qui est déjà
 * en base (visites précédentes, saisie manuelle...). À appeler avant chaque
 * extraction streaming, et passer le résultat en `etatInitial`.
 *
 * @param {import('pg').Pool} pool - le pool déjà configuré (backend/config/db.js)
 * @param {string} pseudonyme - clé primaire de la table `patients`
 * @param {'SEP'|'EPR'} registre
 * @returns {Promise<Object>} { nom_table: {...} ou [...] }
 */
async function chargerEtatInitialDepuisDB(pool, pseudonyme, registre) {
  const tables = registre === 'SEP' ? TABLES_SEP : TABLES_EPR;
  const etatInitial = {};

  for (const [table, repetee] of Object.entries(tables)) {
    const { rows } = await pool.query(
      `SELECT * FROM ${table} WHERE pseudonyme = $1`,
      [pseudonyme]
    );
    if (rows.length === 0) continue;

    if (repetee) {
      etatInitial[table] = rows.map(_ligneVersChamps);
    } else {
      etatInitial[table] = _ligneVersChamps(rows[0]); // PRIMARY KEY(pseudonyme) -> 1 seule ligne possible
    }
  }
  return etatInitial;
}

module.exports = {
  extraireEntitesMedicales,
  extraireEntitesMedicalesStreaming,
  chargerEtatInitialDepuisDB,
  TABLES_SEP,
  TABLES_EPR,
};