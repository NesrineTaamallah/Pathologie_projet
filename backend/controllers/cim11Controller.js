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
 */
async function rechercherCim11(req, res) {
  try {
    const q = (req.query.q || '').trim();
    const limit = Math.min(parseInt(req.query.limit, 10) || 15, 50);

    if (!q) {
      return res.json({ resultats: [] });
    }

    // Recherche directe par code (insensible à la casse)
    const codeMatch = /^[a-zA-Z0-9.]{2,10}$/.test(q);

    // On découpe la requête en mots pour gérer l'ordre des mots inversé
    const mots = q
      .normalize('NFD').replace(/[\u0300-\u036f]/g, '') // enlève les accents côté JS aussi
      .toLowerCase()
      .split(/\s+/)
      .filter(Boolean);

    // Condition : chaque mot doit apparaître (approximativement) dans search_text,
    // combinée avec un score de similarité globale sur la requête complète
    const wordConditions = mots
      .map((_, i) => `unaccent(lower($${i + 2})) <% cim11_codes.search_text`)
      .join(' OR ');

    const params = [q, ...mots];

    const sql = `
      SELECT
        id, chapter, code, title, class_kind, parent_code, uri, definition,
        GREATEST(
          similarity(unaccent(lower(title)), unaccent(lower($1))),
          word_similarity(unaccent(lower($1)), search_text)
        ) AS score
      FROM cim11_codes
      WHERE
        ${codeMatch ? 'lower(code) = lower($1) OR' : ''}
        unaccent(lower(title)) % unaccent(lower($1))
        OR search_text % unaccent(lower($1))
        ${mots.length > 1 ? `OR (${wordConditions})` : ''}
      ORDER BY
        (lower(code) = lower($1)) DESC,
        score DESC NULLS LAST
      LIMIT $${mots.length + 2}
    `;
    params.push(limit);

    const { rows } = await pool.query(sql, params);

    // Repli : si aucun résultat via trigram (terme trop différent), on tente
    // une recherche ILIKE partielle sur chaque mot significatif (>=3 lettres)
    let resultats = rows;
    if (resultats.length === 0) {
      const motsSignificatifs = mots.filter((m) => m.length >= 3);
      if (motsSignificatifs.length > 0) {
        const ilikeConds = motsSignificatifs
          .map((_, i) => `unaccent(lower(title)) ILIKE '%' || unaccent(lower($${i + 1})) || '%'`)
          .join(' OR ');
        const { rows: rows2 } = await pool.query(
          `SELECT id, chapter, code, title, class_kind, parent_code, uri, definition, 0.1 AS score
           FROM cim11_codes WHERE ${ilikeConds} LIMIT $${motsSignificatifs.length + 1}`,
          [...motsSignificatifs, limit]
        );
        resultats = rows2;
      }
    }

    res.json({ resultats });
  } catch (err) {
    console.error('Erreur recherche CIM-11 :', err);
    res.status(500).json({ message: 'Erreur lors de la recherche CIM-11' });
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
