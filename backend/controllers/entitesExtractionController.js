const pool = require('../config/db');
const {
  extraireEntitesMedicales,
  extraireEntitesMedicalesStreaming,
  chargerEtatInitialDepuisDB,
  TABLES_SEP,
  TABLES_EPR,
} = require('../utils/entitesExtractionClient');
const { logAccess } = require('../utils/accessLog');

/**
 * Récupère les chunks de texte transcrit d'un pseudonyme, avec leur date,
 * pour alimenter la fenêtre de récence (section 10 du notebook) et le
 * contexte ciblé (section 7).
 */
async function _chunksPourPseudonyme(pseudonyme) {
  const patientResult = await pool.query(
    `SELECT pseudonyme, registre FROM patients WHERE pseudonyme = $1`,
    [pseudonyme]
  );
  const patient = patientResult.rows[0];
  if (!patient) return null;

  const docsResult = await pool.query(
    `SELECT id, texte_transcrit, created_at
       FROM documents_bruts
      WHERE pathologie = $1 AND pseudonyme = $2 AND texte_transcrit IS NOT NULL
      ORDER BY created_at ASC`,
    [patient.registre, pseudonyme]
  );

  return {
    registre: patient.registre, // 'SEP' ou 'EPR'
    chunks: docsResult.rows.map((r) => ({
      texte: r.texte_transcrit,
      date: r.created_at ? new Date(r.created_at).toISOString() : null,
    })),
    documentIds: docsResult.rows.map((r) => r.id),
  };
}

/**
 * POST /api/extraction/entites
 * body: { pseudonyme }
 *
 * Extraction complète (toutes les tables SEP ou EPR) via le microservice.
 * IMPORTANT : contrairement à /api/coordonnees, il n'y a AUCUNE écriture des
 * champs extraits en base ici — le résultat est renvoyé au clinicien pour
 * relecture/correction dans l'interface uniquement. Seul un indicateur
 * booléen "entites_extraites" est mis à jour (pour piloter l'alerte
 * dashboard), jamais le contenu structuré.
 */
async function extraireEntites(req, res) {
  const { pseudonyme } = req.body;
  if (!pseudonyme) {
    return res.status(400).json({ error: "Le champ 'pseudonyme' est requis." });
  }

  try {
    const contexte = await _chunksPourPseudonyme(pseudonyme);
    if (!contexte) {
      return res.status(404).json({ error: 'Dossier introuvable.' });
    }
    if (contexte.chunks.length === 0) {
      return res.status(422).json({ error: 'Aucun texte transcrit disponible pour ce patient.' });
    }

    const resultat = await extraireEntitesMedicales(contexte.registre, contexte.chunks);

    await logAccess({
      userId: req.user?.sub,
      action: 'extraction_entites_medicales',
      success: true,
      req,
    });

    // Marque les documents comme "passés en revue par le pipeline d'entités"
    // -> fait disparaître l'alerte dashboard, sans toucher au contenu clinique.
    if (contexte.documentIds.length > 0) {
      await pool.query(
        `UPDATE documents_bruts SET entites_extraites = true WHERE id = ANY($1::int[])`,
        [contexte.documentIds]
      );
    }

    // CORRECTIF : ne PAS spreader "resultat" tel quel — sa forme est
    // { registre, patient_id, tables: {...}, a_verifier: [...],
    //   diagnostics_contexte: [...], diagnostics_ameliorations: [...] }.
    // Spreader directement transformait "tables", "a_verifier",
    // "diagnostics_contexte", "diagnostics_ameliorations" en 4 clés
    // top-level distinctes, que le frontend interprétait ensuite chacune
    // comme si c'était une table clinique à afficher (bug visible dans la
    // modale : sections "tables"/"a_verifier"/"diagnostics_contexte" au
    // lieu des vraies tables sep_identification_clinique, sep_irm, etc.).
    res.json({
      pseudonyme,
      registre: contexte.registre,
      tables: resultat.tables,
      a_verifier: resultat.a_verifier || [],
    });
  } catch (err) {
    console.error('Erreur extraireEntites :', err);
    res.status(502).json({ error: err.message || "Échec de l'extraction des entités médicales." });
  }
}

/**
 * GET /api/extraction/entites/non-extraits
 * Pour l'alerte dashboard : dossiers dont au moins un document transcrit
 * n'a pas encore été passé dans le pipeline d'entités.
 */
async function listeNonExtraits(req, res) {
  try {
    const result = await pool.query(
      `SELECT p.pseudonyme, p.registre, COUNT(*) AS documents_en_attente
         FROM documents_bruts d
         JOIN patients p ON p.pseudonyme = d.pseudonyme AND p.registre = d.pathologie
        WHERE d.texte_transcrit IS NOT NULL
          AND COALESCE(d.entites_extraites, false) = false
        GROUP BY p.pseudonyme, p.registre
        ORDER BY documents_en_attente DESC`
    );
    res.json({ total: result.rows.length, dossiers: result.rows });
  } catch (err) {
    console.error('Erreur listeNonExtraits :', err);
    res.status(500).json({ error: 'Erreur serveur.' });
  }
}

module.exports = { extraireEntites, listeNonExtraits, extraireEntitesDocument, enregistrerEntites };

/**
 * POST /api/extraction/entites-document
 * body: { document_id }
 *
 * Extraction "texte par texte" (UNE visite/consultation à la fois),
 * exactement le modèle demandé pour l'UI : on retourne à la fois le texte
 * source de CE document et les entités extraites, pour affichage côte à
 * côte + correction, comme ExtractionCoordonneesPanel. Utilise la route
 * streaming du microservice avec etat_initial = ce qui est déjà en base
 * (mémoire des visites précédentes), donc les champs déjà connus ne sont
 * ni dupliqués ni effacés par ce court texte.
 */
async function extraireEntitesDocument(req, res) {
  const { document_id } = req.body;
  if (!document_id) {
    return res.status(400).json({ error: "Le champ 'document_id' est requis." });
  }

  try {
    const docResult = await pool.query(
      `SELECT id, pseudonyme, pathologie, texte_transcrit
         FROM documents_bruts WHERE id = $1`,
      [document_id]
    );
    const doc = docResult.rows[0];
    if (!doc) return res.status(404).json({ error: 'Document introuvable.' });
    if (!doc.texte_transcrit) {
      return res.status(422).json({ error: 'Aucun texte transcrit pour ce document.' });
    }

    const registre = doc.pathologie; // 'SEP' ou 'EPR'
    const etatInitial = await chargerEtatInitialDepuisDB(pool, doc.pseudonyme, registre);

    const resultat = await extraireEntitesMedicalesStreaming(
      registre,
      [{ texte: doc.texte_transcrit }],
      { patientId: doc.pseudonyme, etatInitial }
    );

    await logAccess({ userId: req.user?.sub, action: 'extraction_entites_document', success: true, req });
    await pool.query(`UPDATE documents_bruts SET entites_extraites = true WHERE id = $1`, [document_id]);

    res.json({
      pseudonyme: doc.pseudonyme,
      registre,
      document_id: doc.id,
      texte: doc.texte_transcrit,       // <- affiché à côté des champs dans l'UI
      tables: resultat.tables,
      a_verifier: resultat.a_verifier || [],
    });
  } catch (err) {
    console.error('Erreur extraireEntitesDocument :', err);
    res.status(502).json({ error: err.message || "Échec de l'extraction." });
  }
}

// ---------------------------------------------------------------------------
// Persistance réelle (UPSERT) — équivalent, pour les entités cliniques, de
// ce que fait coordonneePatientController.js pour les coordonnées : le
// clinicien corrige à l'écran puis "Enregistrer" écrit vraiment en base.
// ---------------------------------------------------------------------------
const COLONNES_A_EXCLURE_ECRITURE = new Set(['id', 'pseudonyme']);

function _valeurPourColonne(v) {
  // Les colonnes SQL (BOOLEAN/DATE/NUMERIC/VARCHAR) ne connaissent pas le
  // sentinel "NA" du pipeline d'extraction -> on le range en NULL en base
  // (le texte source reste, lui, consultable si besoin de contexte).
  if (v === null || v === undefined || v === 'null' || v === 'NA') return null;
  return v;
}

function _champsUtiles(obj) {
  return Object.keys(obj || {}).filter(
    (k) => !k.startsWith('evidence_span_') && !k.startsWith('_') && !COLONNES_A_EXCLURE_ECRITURE.has(k)
  );
}

/**
 * POST /api/entites
 * body: { pseudonyme, registre, tables: { <nom_table>: {...} ou [...] } }
 *
 * "tables" doit être l'état COMPLET et corrigé (comme renvoyé par
 * extraireEntitesDocument, potentiellement modifié par le clinicien à
 * l'écran) — pas juste le delta de la visite. Stratégie d'écriture :
 *  - table non répétée (1-1 par patient)  -> INSERT ... ON CONFLICT UPDATE
 *  - table répétée (1-N)                  -> remplace entièrement les lignes
 *    de ce patient (DELETE puis ré-INSERT), car l'état reçu est déjà la
 *    liste complète et fusionnée (historique + cette visite).
 */
async function enregistrerEntites(req, res) {
  const { pseudonyme, registre, tables } = req.body;
  if (!pseudonyme || !registre || !tables) {
    return res.status(400).json({ error: "Les champs 'pseudonyme', 'registre' et 'tables' sont requis." });
  }
  const configTables = registre === 'SEP' ? TABLES_SEP : TABLES_EPR;

  const client = await pool.connect();
  try {
    await client.query('BEGIN');

    for (const [tableName, repetee] of Object.entries(configTables)) {
      const contenu = tables[tableName];
      if (contenu === undefined) continue; // table non renvoyée par le front -> on ne touche pas

      if (repetee) {
        await client.query(`DELETE FROM ${tableName} WHERE pseudonyme = $1`, [pseudonyme]);
        const occurrences = Array.isArray(contenu) ? contenu : [];
        for (const occ of occurrences) {
          const champs = _champsUtiles(occ);
          if (champs.length === 0) continue;
          const colonnes = ['pseudonyme', ...champs];
          const valeurs = [pseudonyme, ...champs.map((c) => _valeurPourColonne(occ[c]))];
          const placeholders = valeurs.map((_, i) => `$${i + 1}`).join(', ');
          await client.query(
            `INSERT INTO ${tableName} (${colonnes.join(', ')}) VALUES (${placeholders})`,
            valeurs
          );
        }
      } else {
        if (!contenu || typeof contenu !== 'object') continue;
        const champs = _champsUtiles(contenu);
        if (champs.length === 0) continue;
        const colonnes = ['pseudonyme', ...champs];
        const valeurs = [pseudonyme, ...champs.map((c) => _valeurPourColonne(contenu[c]))];
        const placeholders = valeurs.map((_, i) => `$${i + 1}`).join(', ');
        const misesAJour = champs.map((c) => `${c} = EXCLUDED.${c}`).join(', ');
        await client.query(
          `INSERT INTO ${tableName} (${colonnes.join(', ')}) VALUES (${placeholders})
           ON CONFLICT (pseudonyme) DO UPDATE SET ${misesAJour}`,
          valeurs
        );
      }
    }

    await client.query('COMMIT');
    await logAccess({ userId: req.user?.sub, action: 'enregistrement_entites_medicales', success: true, req });
    res.json({ ok: true });
  } catch (err) {
    await client.query('ROLLBACK');
    console.error('Erreur enregistrerEntites :', err);
    res.status(500).json({ error: err.message || "Échec de l'enregistrement." });
  } finally {
    client.release();
  }
}