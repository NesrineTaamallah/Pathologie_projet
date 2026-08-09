const pool = require('../config/db');
const { decrypt } = require('../utils/cryptoUtils');
const { extraireDonneesPatient } = require('../utils/extractionClient');
const { logAccess } = require('../utils/accessLog');


const CHAMPS_COORDONNEE = [
  'numero_dossier', 'nom_prenom', 'date_naissance', 'adresse', 'origine',
  'telephone', 'cin', 'num_cnam', 'nom_prenom_pere', 'nom_prenom_mere',
  'frere', 'soeur', 'autre_antecedent',
];


const CHAMPS_SIMPLES = [
  'numero_dossier', 'nom_prenom', 'date_naissance', 'adresse', 'origine',
  'telephone', 'cin', 'num_cnam', 'nom_prenom_pere', 'nom_prenom_mere',
];


const CHAMPS_MULTI = ['frere', 'soeur', 'autre_antecedent'];

function _normaliser(valeur) {
  return (valeur || '').trim().toLowerCase();
}


function fusionnerChampMulti(existant, nouveau) {
  const items = [
    ...String(existant || '').split(',').map((s) => s.trim()).filter(Boolean),
    ...String(nouveau || '').split(',').map((s) => s.trim()).filter(Boolean),
  ];
  const vus = new Set();
  const dedupliques = [];
  for (const item of items) {
    const cle = _normaliser(item);
    if (!vus.has(cle)) {
      vus.add(cle);
      dedupliques.push(item);
    }
  }
  return dedupliques.join(', ');
}

function fusionnerAvecExistant(extraction, existant) {
  const fusion = {};
  for (const champ of CHAMPS_SIMPLES) {
    const valeurExtraite = String(extraction[champ] || '').trim();
    fusion[champ] = valeurExtraite || (existant && existant[champ]) || '';
  }
  for (const champ of CHAMPS_MULTI) {
    fusion[champ] = fusionnerChampMulti(existant && existant[champ], extraction[champ]);
  }
  return fusion;
}


async function _texteConcatenePourPseudonyme(pseudonyme) {
  const patientResult = await pool.query(
    `SELECT pseudonyme, registre FROM patients WHERE pseudonyme = $1`,
    [pseudonyme]
  );
  const patient = patientResult.rows[0];
  if (!patient) return null;

  const docsResult = await pool.query(
    `SELECT texte_transcrit FROM documents_bruts
      WHERE pathologie = $1 AND pseudonyme = $2 AND texte_transcrit IS NOT NULL
      ORDER BY created_at ASC`,
    [patient.registre, pseudonyme]
  );

  return docsResult.rows.map((r) => r.texte_transcrit).filter(Boolean).join('\n\n');
}


async function _coordonneeExistante(pseudonyme) {
  const result = await pool.query(`SELECT * FROM coordonnee_patient WHERE pseudonyme = $1`, [pseudonyme]);
  const row = result.rows[0];
  if (!row) return null;
  const decrypted = {};
  for (const champ of CHAMPS_COORDONNEE) {
    decrypted[champ] = row[champ] ? decrypt(row[champ]) : '';
  }
  return decrypted;
}


async function extraireCoordonneesPatient(req, res) {
  const { pseudonyme, texte, document_id } = req.body;

  if (!pseudonyme && !texte && !document_id) {
    return res.status(400).json({ error: "Fournir 'pseudonyme', 'document_id' ou 'texte'." });
  }

  try {
    let texteAAnalyser = texte;
    let existant = null;
    let pseudonymeEffectif = pseudonyme || null;
    let numeroDossierConnu = null;

    if (document_id) {
      const docResult = await pool.query(
        `SELECT texte_transcrit, pseudonyme, numero_dossier FROM documents_bruts WHERE id = $1`,
        [document_id]
      );
      const doc = docResult.rows[0];
      if (!doc) {
        return res.status(404).json({ error: 'Document introuvable.' });
      }
      texteAAnalyser = doc.texte_transcrit;
      pseudonymeEffectif = doc.pseudonyme;
      numeroDossierConnu = doc.numero_dossier ? doc.numero_dossier.trim() : null;
      if (pseudonymeEffectif) {
        existant = await _coordonneeExistante(pseudonymeEffectif);
      }
    } else if (pseudonyme) {
      texteAAnalyser = await _texteConcatenePourPseudonyme(pseudonyme);
      if (texteAAnalyser === null) {
        return res.status(404).json({ error: 'Dossier introuvable.' });
      }
      existant = await _coordonneeExistante(pseudonyme);
      const docsResult = await pool.query(
        `SELECT numero_dossier FROM documents_bruts WHERE pseudonyme = $1 AND numero_dossier IS NOT NULL LIMIT 1`,
        [pseudonyme]
      );
      if (docsResult.rows[0]?.numero_dossier) {
        numeroDossierConnu = docsResult.rows[0].numero_dossier.trim();
      }
    }

    if (!texteAAnalyser || !texteAAnalyser.trim()) {
      return res.status(422).json({ error: 'Aucun texte transcrit disponible — impossible d\'extraire.' });
    }

    const extraction = await extraireDonneesPatient(texteAAnalyser);
    const fusion = fusionnerAvecExistant(extraction, existant);
    if (!fusion.numero_dossier && numeroDossierConnu) {
      fusion.numero_dossier = numeroDossierConnu;
    }

    await logAccess({
      userId: req.user?.sub,
      action: 'extraction_patient',
      success: true,
      req,
    });

    res.json({ pseudonyme: pseudonymeEffectif, document_id: document_id || null, ...fusion });
  } catch (err) {
    console.error('Erreur extraireCoordonneesPatient :', err);
    res.status(502).json({ error: err.message || "Échec de l'extraction." });
  }
}

module.exports = { extraireCoordonneesPatient };