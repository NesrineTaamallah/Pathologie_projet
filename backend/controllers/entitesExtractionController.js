const pool = require('../config/db');
const { extraireEntitesMedicales } = require('../utils/entitesExtractionClient');
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

    res.json({ pseudonyme, registre: contexte.registre, ...resultat });
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

module.exports = { extraireEntites, listeNonExtraits };
