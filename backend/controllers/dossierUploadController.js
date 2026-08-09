const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');
const { Agent } = require('undici');
const pool = require('../config/db');
const { logAccess } = require('../utils/accessLog');
const { genererPseudonyme } = require('../utils/pseudonymUtils');
const { decrypt } = require('../utils/cryptoUtils');

const TYPES_DOCUMENT = ['visite', 'admission', 'prelevement_sang', 'eeg', 'emg', 'irm', 'autre'];
const TYPES_ENTREE = ['audio', 'scan', 'video'];

const UPLOAD_DIR = process.env.UPLOAD_DIR || path.join(__dirname, '..', 'uploads');
const WHISPER_SCRIPT = path.join(__dirname, '..', 'scripts', 'whisper_transcribe.py');
const PADDLEOCR_SCRIPT = path.join(__dirname, '..', 'scripts', 'paddleocr_transcribe.py');
const PYTHON_BIN = process.env.PYTHON_BIN || 'python3';
const PADDLEOCR_PYTHON_BIN = process.env.PADDLEOCR_PYTHON_BIN || PYTHON_BIN;


const WHISPER_SERVICE_URL = process.env.WHISPER_SERVICE_URL || 'http://127.0.0.1:8001';
const PADDLEOCR_SERVICE_URL = process.env.PADDLEOCR_SERVICE_URL || 'http://127.0.0.1:8002';


const LONG_INFERENCE_DISPATCHER = new Agent({
  headersTimeout: 32 * 60 * 1000, 
  bodyTimeout: 32 * 60 * 1000,
  connectTimeout: 32 * 60 * 1000,
  keepAliveTimeout: 32 * 60 * 1000,
});

fs.mkdirSync(UPLOAD_DIR, { recursive: true });


async function transcribeAudioViaService(audioPath) {
  const absolutePath = path.resolve(audioPath);
  const res = await fetch(`${WHISPER_SERVICE_URL}/transcribe`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ audio_path: absolutePath }),
    dispatcher: LONG_INFERENCE_DISPATCHER,
  });

  const data = await res.json().catch(() => null);
  if (!res.ok) {
    throw new Error(data?.detail || `Service de transcription : erreur HTTP ${res.status}`);
  }
  if (!data || typeof data.text !== 'string') {
    throw new Error('Réponse du service de transcription illisible.');
  }
  return { text: data.text, words: Array.isArray(data.words) ? data.words : [] };
}


function transcribeAudioSpawn(audioPath) {
  return new Promise((resolve, reject) => {
    const proc = spawn(PYTHON_BIN, [WHISPER_SCRIPT, '--audio', audioPath, '--json'], {
      env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
    });

    let stdout = '';
    let stderr = '';
    proc.stdout.on('data', (d) => { stdout += d.toString('utf8'); });
    proc.stderr.on('data', (d) => { stderr += d.toString('utf8'); });

    proc.on('error', (err) => {
      reject(new Error(`Impossible de lancer Python (${PYTHON_BIN}) : ${err.message}`));
    });

    proc.on('close', (code) => {
      if (code !== 0) {
        console.error('whisper_transcribe.py stderr :', stderr);
        return reject(new Error('Échec de la transcription audio.'));
      }
      try {
        const parsed = JSON.parse(stdout.trim().split('\n').pop());
        if (parsed.error) return reject(new Error(parsed.error));
        resolve({ text: parsed.text, words: Array.isArray(parsed.words) ? parsed.words : [] });
      } catch (e) {
        reject(new Error('Réponse de transcription illisible.'));
      }
    });
  });
}


async function transcribeAudio(audioPath) {
  try {
    return await transcribeAudioViaService(audioPath);
  } catch (err) {
    if (err.cause?.code === 'ECONNREFUSED' || err.message.includes('fetch failed')) {
      console.warn(
        `[whisper] Service HTTP injoignable sur ${WHISPER_SERVICE_URL} ` +
        `(pas démarré ?) — repli sur spawn() (plus lent). ` +
        `Démarrez le service avec : uvicorn whisper_service:app --port 8001`
      );
      return transcribeAudioSpawn(audioPath);
    }
    throw err;
  }
}

async function ocrScanViaService(filePath) {
  const absolutePath = path.resolve(filePath);
  const res = await fetch(`${PADDLEOCR_SERVICE_URL}/ocr`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ input_path: absolutePath }),
    dispatcher: LONG_INFERENCE_DISPATCHER,
  });

  const data = await res.json().catch(() => null);
  if (!res.ok) {
    throw new Error(data?.detail || `Service OCR : erreur HTTP ${res.status}`);
  }
  if (!data || typeof data.markdown !== 'string') {
    throw new Error('Réponse du service OCR illisible.');
  }
  return data.markdown;
}


function ocrScanSpawn(filePath) {
  return new Promise((resolve, reject) => {
    const proc = spawn(PADDLEOCR_PYTHON_BIN, [PADDLEOCR_SCRIPT, '--input', filePath, '--json'], {
      env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
    });

    let stdout = '';
    let stderr = '';
    proc.stdout.on('data', (d) => { stdout += d.toString('utf8'); });
    proc.stderr.on('data', (d) => { stderr += d.toString('utf8'); });

    proc.on('error', (err) => {
      reject(new Error(`Impossible de lancer Python (${PADDLEOCR_PYTHON_BIN}) : ${err.message}`));
    });

    proc.on('close', (code) => {
      if (code !== 0) {
        console.error('paddleocr_transcribe.py stderr :', stderr);
        return reject(new Error("Échec de l'OCR du document scanné."));
      }
      try {
        const parsed = JSON.parse(stdout.trim().split('\n').pop());
        if (parsed.error) return reject(new Error(parsed.error));
        resolve(parsed.markdown);
      } catch (e) {
        reject(new Error('Réponse OCR illisible.'));
      }
    });
  });
}

async function ocrScan(filePath) {
  try {
    return await ocrScanViaService(filePath);
  } catch (err) {
    
    console.warn('[paddleocr] Erreur fetch réelle :', err);
    if (err.cause) console.warn('[paddleocr] err.cause :', err.cause);

    if (err.cause?.code === 'ECONNREFUSED' || err.message.includes('fetch failed')) {
      console.warn(
        `[paddleocr] Service HTTP injoignable sur ${PADDLEOCR_SERVICE_URL} ` +
        `(pas démarré ?) — repli sur spawn() (plus lent). ` +
        `Démarrez le service avec : uvicorn paddleocr_service:app --port 8002`
      );
      return ocrScanSpawn(filePath);
    }
    throw err;
  }
}


async function verifierDossier(req, res) {
  const { pathologie, numero_dossier } = req.query;

  if (!pathologie || !numero_dossier) {
    return res.status(400).json({ error: 'pathologie et numero_dossier requis.' });
  }
  if (!['SEP', 'EPR'].includes(pathologie)) {
    return res.status(400).json({ error: 'Pathologie invalide.' });
  }

  try {
    const numeroNormalise = String(numero_dossier).trim().toUpperCase();

    
    const docResult = await pool.query(
      `SELECT numero_dossier FROM documents_bruts
        WHERE pathologie = $1 AND UPPER(TRIM(numero_dossier)) = $2
        LIMIT 1`,
      [pathologie, numeroNormalise]
    );

    let pseudonyme = null;
    if (docResult.rows[0]) {
      pseudonyme = genererPseudonyme(pathologie, docResult.rows[0].numero_dossier);
    } else {
      
      const coordResult = await pool.query(
        `SELECT pseudonyme, numero_dossier FROM coordonnee_patient WHERE numero_dossier IS NOT NULL`
      );
      const match = coordResult.rows.find((r) => {
        try {
          return decrypt(r.numero_dossier).trim().toUpperCase() === numeroNormalise;
        } catch {
          return false;
        }
      });
      if (match) pseudonyme = match.pseudonyme;
    }

    if (!pseudonyme) {
      return res.json({ existe: false });
    }

    const patientResult = await pool.query(
      `SELECT pseudonyme, registre, date_inclusion FROM patients WHERE pseudonyme = $1`,
      [pseudonyme]
    );
    const patient = patientResult.rows[0];
    
    if (!patient || patient.registre !== pathologie) {
      return res.json({ existe: false });
    }

    const identificationTable = pathologie === 'SEP'
      ? 'sep_identification_clinique'
      : 'epr_identification_clinique';
    const identResult = await pool.query(
      `SELECT date_diagnostic FROM ${identificationTable} WHERE pseudonyme = $1`,
      [patient.pseudonyme]
    );

    res.json({
      existe: true,
      pseudonyme: patient.pseudonyme,
      pathologie: patient.registre,
      numero_dossier: numero_dossier.trim(),
      date_diagnostic: identResult.rows[0]?.date_diagnostic || null,
      date_inclusion: patient.date_inclusion,
    });
  } catch (err) {
    console.error('Erreur verifierDossier :', err);
    res.status(500).json({ error: 'Erreur serveur.' });
  }
}


async function creerDossier(req, res) {
  const {
    numero_dossier, pathologie, date_diagnostic, date_inclusion, type_document, type_entree,
    
    pseudonyme_existant,
  } = req.body;
  const fichier = req.file;

  if (!numero_dossier || !pathologie || !date_diagnostic || !date_inclusion || !type_document || !type_entree) {
    return res.status(400).json({ error: 'Champs requis manquants.' });
  }
  if (!['SEP', 'EPR'].includes(pathologie)) {
    return res.status(400).json({ error: 'Pathologie invalide.' });
  }
  if (!TYPES_DOCUMENT.includes(type_document)) {
    return res.status(400).json({ error: 'Type de document invalide.' });
  }
  if (!TYPES_ENTREE.includes(type_entree)) {
    return res.status(400).json({ error: "Type d'entrée invalide." });
  }
  if (!fichier) {
    return res.status(400).json({ error: 'Fichier manquant.' });
  }

  
  let pseudonyme = null;
  if (pseudonyme_existant) {
    const check = await pool.query(
      `SELECT pseudonyme FROM patients WHERE pseudonyme = $1 AND registre = $2`,
      [pseudonyme_existant, pathologie]
    );
    if (check.rows[0]) {
      pseudonyme = check.rows[0].pseudonyme;
    }
  }
  if (!pseudonyme) {
    
    pseudonyme = genererPseudonyme(pathologie, numero_dossier);
  }

  let texte_transcrit = null;
  let mots_confiance = null; 
  let statut = 'en_attente';

  if (type_entree === 'audio') {
    try {
      const transcription = await transcribeAudio(fichier.path);
      texte_transcrit = transcription.text;
      mots_confiance = transcription.words;
      statut = 'transcrit';
      await logAccess({ userId: req.user.sub, action: 'transcription_audio', success: true, req });
    } catch (err) {
      console.error('Erreur transcription :', err);
      statut = 'erreur_transcription';
      await logAccess({ userId: req.user.sub, action: 'transcription_audio', success: false, req });
    }
  } else if (type_entree === 'scan') {
    try {
      texte_transcrit = await ocrScan(fichier.path);
      statut = 'transcrit';
      await logAccess({ userId: req.user.sub, action: 'extraction_ocr', success: true, req });
    } catch (err) {
      console.error('Erreur OCR :', err);
      statut = 'erreur_transcription';
      await logAccess({ userId: req.user.sub, action: 'extraction_ocr', success: false, req });
    }
  } else if (type_entree === 'video') {
    // Vidéo brute (ex. enregistrement EEG vidéo) : simplement stockée et
    // rattachée au dossier, sans transcription ni OCR — le clinicien la
    // visionne/télécharge telle quelle depuis le dossier du patient.
    statut = 'stocke';
    await logAccess({ userId: req.user.sub, action: 'ajout_video', success: true, req });
  }

  try {
    const docResult = await pool.query(
      `INSERT INTO documents_bruts
         (numero_dossier, pathologie, date_diagnostic, type_document, type_entree,
          chemin_fichier, nom_fichier_original, texte_transcrit, mots_confiance, statut, pseudonyme)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
       RETURNING id`,
      [
        numero_dossier, pathologie, date_diagnostic, type_document, type_entree,
        fichier.path, fichier.originalname, texte_transcrit,
        mots_confiance ? JSON.stringify(mots_confiance) : null,
        statut, pseudonyme,
      ]
    );

    
    await pool.query(
      `INSERT INTO patients (pseudonyme, registre, date_inclusion)
       VALUES ($1, $2, $3)
       ON CONFLICT (pseudonyme) DO NOTHING`,
      [pseudonyme, pathologie, date_inclusion]
    );

    
    const identificationTable = pathologie === 'SEP'
      ? 'sep_identification_clinique'
      : 'epr_identification_clinique';
    await pool.query(
      `INSERT INTO ${identificationTable} (pseudonyme, date_diagnostic)
       VALUES ($1, $2)
       ON CONFLICT (pseudonyme) DO NOTHING`,
      [pseudonyme, date_diagnostic]
    );

    await logAccess({ userId: req.user.sub, action: 'dossier_document_creer', success: true, req });

    res.status(201).json({
      document_id: docResult.rows[0].id,
      numero_dossier,
      pseudonyme,
      statut,
      texte_transcrit,
      mots_confiance,
    });
  } catch (err) {
    console.error('Erreur creerDossier :', err);
    res.status(500).json({ error: 'Erreur serveur.' });
  }
}


async function corrigerTexteTranscrit(req, res) {
  const { id } = req.params;
  const { texte_transcrit } = req.body;

  if (typeof texte_transcrit !== 'string') {
    return res.status(400).json({ error: 'texte_transcrit manquant ou invalide.' });
  }

  try {
    const result = await pool.query(
      `UPDATE documents_bruts
         SET texte_transcrit = $1, statut = 'valide'
       WHERE id = $2
       RETURNING id, numero_dossier, pathologie, pseudonyme, type_document, texte_transcrit, statut`,
      [texte_transcrit, id]
    );

    if (result.rows.length === 0) {
      return res.status(404).json({ error: 'Document introuvable.' });
    }

    const doc = result.rows[0];
    
    const pseudonyme = doc.pseudonyme || genererPseudonyme(doc.pathologie, doc.numero_dossier);

    
    const marqueur = `doc#${doc.id}`;
    const entete = `--- ${doc.type_document || 'document'} · ${marqueur} · ${new Date().toLocaleDateString('fr-FR')} ---`;
    const nouveauBloc = `${entete}\n${texte_transcrit}`;

    const patientResult = await pool.query(`SELECT detaille FROM patients WHERE pseudonyme = $1`, [pseudonyme]);
    const detailleActuel = patientResult.rows[0]?.detaille || '';
    const blocs = detailleActuel ? detailleActuel.split('\n\n') : [];
    const indexExistant = blocs.findIndex((b) => b.startsWith('---') && b.split('\n')[0].includes(marqueur));

    let nouveauDetaille;
    if (!texte_transcrit.trim()) {
      
      nouveauDetaille = indexExistant >= 0
        ? blocs.filter((_, i) => i !== indexExistant).join('\n\n')
        : detailleActuel;
    } else if (indexExistant >= 0) {
      blocs[indexExistant] = nouveauBloc;
      nouveauDetaille = blocs.join('\n\n');
    } else {
      nouveauDetaille = detailleActuel ? `${detailleActuel}\n\n${nouveauBloc}` : nouveauBloc;
    }

    await pool.query(`UPDATE patients SET detaille = $1 WHERE pseudonyme = $2`, [nouveauDetaille, pseudonyme]);

    await logAccess({ userId: req.user.sub, action: 'dossier_document_corriger', success: true, req });

    res.json({ ...doc, pseudonyme });
  } catch (err) {
    console.error('Erreur corrigerTexteTranscrit :', err);
    res.status(500).json({ error: 'Erreur serveur.' });
  }
}


async function getDocumentsByPseudonyme(req, res) {
  const { pseudonyme } = req.params;

  try {
    const patientResult = await pool.query(
      `SELECT pseudonyme, registre FROM patients WHERE pseudonyme = $1`,
      [pseudonyme]
    );
    const patient = patientResult.rows[0];
    if (!patient) {
      return res.status(404).json({ error: 'Dossier introuvable.' });
    }

    const docsResult = await pool.query(
      `SELECT id, numero_dossier, pseudonyme, type_document, type_entree, nom_fichier_original,
              texte_transcrit, mots_confiance, statut, created_at
         FROM documents_bruts
        WHERE pathologie = $1
        ORDER BY created_at DESC`,
      [patient.registre]
    );

    const documents = docsResult.rows
      
      .filter((d) => (d.pseudonyme
        ? d.pseudonyme === pseudonyme
        : genererPseudonyme(patient.registre, d.numero_dossier) === pseudonyme))
      .map((d) => ({
        id: d.id,
        numero_dossier: d.numero_dossier,
        type_document: d.type_document,
        type_entree: d.type_entree,
        nom_fichier_original: d.nom_fichier_original,
        texte_transcrit: d.texte_transcrit,
        
        mots_confiance: d.mots_confiance,
        statut: d.statut,
        created_at: d.created_at,
      }));

    let numero_dossier = documents[0]?.numero_dossier || null;
    if (!numero_dossier) {
      const coordResult = await pool.query(
        `SELECT numero_dossier FROM coordonnee_patient WHERE pseudonyme = $1`,
        [pseudonyme]
      );
      const chiffre = coordResult.rows[0]?.numero_dossier;
      if (chiffre) {
        try {
          numero_dossier = decrypt(chiffre).trim();
        } catch (e) {
          console.error('Erreur déchiffrement numero_dossier (coordonnee_patient) :', e);
        }
      }
    }

    await logAccess({ userId: req.user.sub, action: 'dossier_documents_bruts_view', success: true, req });

    res.json({ pseudonyme, numero_dossier, documents });
  } catch (err) {
    console.error('Erreur getDocumentsByPseudonyme :', err);
    res.status(500).json({ error: 'Erreur serveur.' });
  }
}


async function getDocumentsNonExtraits(req, res) {
  const { pseudonyme } = req.params;

  try {
    const patientResult = await pool.query(
      `SELECT pseudonyme, registre FROM patients WHERE pseudonyme = $1`,
      [pseudonyme]
    );
    const patient = patientResult.rows[0];
    if (!patient) {
      return res.status(404).json({ error: 'Dossier introuvable.' });
    }

    const docsResult = await pool.query(
      `SELECT id, numero_dossier, pseudonyme, type_document, type_entree, nom_fichier_original,
              texte_transcrit, mots_confiance, statut, created_at
         FROM documents_bruts
        WHERE pathologie = $1
          AND texte_transcrit IS NOT NULL
          AND TRIM(texte_transcrit) <> ''
          AND coordonnees_extraites = false
        ORDER BY created_at ASC`,
      [patient.registre]
    );

    const documents = docsResult.rows
      .filter((d) => (d.pseudonyme
        ? d.pseudonyme === pseudonyme
        : genererPseudonyme(patient.registre, d.numero_dossier) === pseudonyme))
      .map((d) => ({
        id: d.id,
        type_document: d.type_document,
        type_entree: d.type_entree,
        nom_fichier_original: d.nom_fichier_original,
        texte_transcrit: d.texte_transcrit,
        mots_confiance: d.mots_confiance,
        statut: d.statut,
        created_at: d.created_at,
      }));

    res.json({ pseudonyme, documents });
  } catch (err) {
    console.error('Erreur getDocumentsNonExtraits :', err);
    res.status(500).json({ error: 'Erreur serveur.' });
  }
}

async function telechargerFichier(req, res) {
  const { id } = req.params;

  try {
    const result = await pool.query(
      `SELECT chemin_fichier, nom_fichier_original FROM documents_bruts WHERE id = $1`,
      [id]
    );
    const doc = result.rows[0];
    if (!doc || !fs.existsSync(doc.chemin_fichier)) {
      return res.status(404).json({ error: 'Fichier introuvable.' });
    }

    await logAccess({ userId: req.user.sub, action: 'dossier_document_telecharger', success: true, req });

    res.download(doc.chemin_fichier, doc.nom_fichier_original || path.basename(doc.chemin_fichier));
  } catch (err) {
    console.error('Erreur telechargerFichier :', err);
    res.status(500).json({ error: 'Erreur serveur.' });
  }
}

module.exports = {
  verifierDossier,
  creerDossier,
  corrigerTexteTranscrit,
  getDocumentsByPseudonyme,
  getDocumentsNonExtraits,
  telechargerFichier,
};