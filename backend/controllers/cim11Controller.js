const pool = require('../config/db');

/**
 * GET /api/cim11/recherche?q=epilsie&limit=15
 *
 * Recherche floue sur les libellés CIM-11 (et synonymes).
 * Tolère :
 *  - fautes de frappe / accents manquants (epilsie -> épilepsie) via pg_trgm
 *  - ordre des mots inversé (ex: "focale epilepsie" vs "épilepsie focale")
 *    en découpant la requête en mots-clés et en les combinant en ET logique
 *    sur search_text (qui contient titre + définition)
 *  - correspondance directe par code (ex: "8A61")
 *
 * NB : seules les entités possédant un code CIM-11 réel (typiquement
 * class_kind = 'category') sont retournées. Les regroupements/chapitres
 * (block, chapter) qui n'ont pas de code assignable sont filtrés, pour
 * que le clinicien n'associe toujours qu'un vrai diagnostic codé.
 */
async function rechercherCim11(req, res) {
  const client = await pool.connect();
  try {
    const q = (req.query.q || '').trim();
    const limit = Math.min(parseInt(req.query.limit, 10) || 15, 50);

    if (!q) {
      return res.json({ resultats: [] });
    }

    const codeMatch = /^[a-zA-Z0-9.]{2,10}$/.test(q);

    const mots = q
      .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
      .toLowerCase()
      .split(/\s+/)
      .filter(Boolean);

    // Seuils de similarité : suffisamment permissifs pour tolérer les fautes
    // de frappe courantes, mais assez stricts pour éviter le bruit (résultats
    // sans rapport). SET LOCAL n'a d'effet que dans une transaction explicite,
    // d'où le BEGIN.
    const SEUIL = 0.3;
    await client.query('BEGIN');
    await client.query(`SET LOCAL pg_trgm.similarity_threshold = ${SEUIL}`);
    await client.query(`SET LOCAL pg_trgm.word_similarity_threshold = ${SEUIL}`);

    const wordConditions = mots
      .map((_, i) => `word_similarity(immutable_unaccent(lower($${i + 2})), cim11_codes.search_text) > ${SEUIL}`)
      .join(' OR ');

    const params = [q, ...mots];

    // Filtre code IS NOT NULL / non vide : on ne garde que les entités
    // codées (class_kind = 'category' dans la pratique). Pas de filtre
    // strict par seuil en WHERE sinon : on calcule un score combiné pour
    // toutes les lignes candidates codées, puis on trie et on prend les N
    // meilleures, pour qu'une requête abîmée ("sleco") remonte quand même
    // le résultat codé le plus proche ("sclérose en plaques").
    const sql = `
      SELECT
        id, chapter, code, title, class_kind, parent_code, uri, definition,
        GREATEST(
          similarity(immutable_unaccent(lower(title)), immutable_unaccent(lower($1))),
          word_similarity(immutable_unaccent(lower($1)), search_text),
          word_similarity(search_text, immutable_unaccent(lower($1)))
        ) AS score
      FROM cim11_codes
      WHERE
        code IS NOT NULL AND code <> ''
        AND (
          ${codeMatch ? 'lower(code) = lower($1) OR' : ''}
          immutable_unaccent(lower(title)) % immutable_unaccent(lower($1))
          OR search_text % immutable_unaccent(lower($1))
          OR (${wordConditions})
        )
      ORDER BY
        (lower(code) = lower($1)) DESC,
        (class_kind = 'category') DESC,
        score DESC NULLS LAST
      LIMIT $${mots.length + 2}
    `;
    params.push(limit);

    let { rows } = await client.query(sql, params);

    // Filet de sécurité : si même avec les seuils abaissés rien ne matche
    // (terme extrêmement différent de tout titre CIM-11), on renvoie quand
    // même les N titres codés les plus proches, sans filtre WHERE sur le
    // score, pour toujours proposer une recommandation au clinicien plutôt
    // qu'un résultat vide — mais toujours uniquement des entités codées.
    if (rows.length === 0) {
      const { rows: fallbackRows } = await client.query(
        `SELECT id, chapter, code, title, class_kind, parent_code, uri, definition,
                similarity(immutable_unaccent(lower(title)), immutable_unaccent(lower($1))) AS score
         FROM cim11_codes
         WHERE code IS NOT NULL AND code <> ''
         ORDER BY (class_kind = 'category') DESC, score DESC
         LIMIT $2`,
        [q, Math.min(limit, 8)]
      );
      rows = fallbackRows;
    }

    await client.query('COMMIT');
    res.json({ resultats: rows });
  } catch (err) {
    try { await client.query('ROLLBACK'); } catch (_) { /* noop */ }
    console.error('Erreur recherche CIM-11 :', err);
    res.status(500).json({ message: 'Erreur lors de la recherche CIM-11' });
  } finally {
    client.release();
  }
}

/**
 * GET /api/cim11/code/:code
 * Détail d'un code précis
 */
async function detailCim11(req, res) {
  try {
    const { code } = req.params;
    const { rows } = await pool.query(
      'SELECT * FROM cim11_codes WHERE lower(code) = lower($1) LIMIT 1',
      [code]
    );
    if (rows.length === 0) return res.status(404).json({ message: 'Code introuvable' });
    res.json(rows[0]);
  } catch (err) {
    console.error('Erreur détail CIM-11 :', err);
    res.status(500).json({ message: 'Erreur serveur' });
  }
}

/**
 * POST /api/cim11/associer
 * body: { pseudonyme, terme_source, cim11_id, entite_extraite_id? }
 * Associe un code CIM-11 (choisi par le clinicien, éventuellement suite à
 * une extraction automatique) à un patient/document.
 */
async function associerCim11(req, res) {
  try {
    const { pseudonyme, terme_source, cim11_id, entite_extraite_id } = req.body;
    if (!pseudonyme || !terme_source || !cim11_id) {
      return res.status(400).json({ message: 'pseudonyme, terme_source et cim11_id sont requis' });
    }
    const { rows: codeRows } = await pool.query(
      'SELECT code, title FROM cim11_codes WHERE id = $1',
      [cim11_id]
    );
    if (codeRows.length === 0) return res.status(404).json({ message: 'Code CIM-11 introuvable' });

    const { rows } = await pool.query(
      `INSERT INTO entites_cim11
        (entite_extraite_id, pseudonyme, terme_source, cim11_id, code_cim11, titre_cim11, valide_par_clinicien)
       VALUES ($1, $2, $3, $4, $5, $6, TRUE)
       RETURNING *`,
      [entite_extraite_id || null, pseudonyme, terme_source, cim11_id, codeRows[0].code, codeRows[0].title]
    );
    res.status(201).json(rows[0]);
  } catch (err) {
    console.error('Erreur association CIM-11 :', err);
    res.status(500).json({ message: 'Erreur serveur' });
  }
}

/**
 * GET /api/cim11/patient/:pseudonyme
 * Liste des codes CIM-11 déjà associés à un patient
 */
async function listeCim11Patient(req, res) {
  try {
    const { pseudonyme } = req.params;
    const { rows } = await pool.query(
      'SELECT * FROM entites_cim11 WHERE pseudonyme = $1 ORDER BY date_creation DESC',
      [pseudonyme]
    );
    res.json({ resultats: rows });
  } catch (err) {
    console.error('Erreur liste CIM-11 patient :', err);
    res.status(500).json({ message: 'Erreur serveur' });
  }
}

module.exports = {
  rechercherCim11,
  detailCim11,
  associerCim11,
  listeCim11Patient,
};