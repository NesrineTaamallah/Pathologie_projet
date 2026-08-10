const pool = require('../config/db');



const SEUIL_SIMILARITE = 0.5;


async function trouverMeilleurCodeCim11(terme, options = {}) {
  const texte = (terme || '').trim();
  if (!texte || texte === 'null' || texte === 'NA') return null;

  const seuil = options.seuil ?? SEUIL_SIMILARITE;
  const filtreChapitre = options.chapterPrefix
    ? 'AND c.chapter = $3'
    : '';
  const params = [texte, seuil];
  if (options.chapterPrefix) params.push(options.chapterPrefix);

  const sql = `
    WITH candidats AS (
      SELECT c.id AS cim11_id, c.code, c.title AS titre,
             similarity(c.search_text, immutable_unaccent(lower($1))) AS score
        FROM cim11_codes c
       WHERE c.class_kind = 'category'
         AND c.code IS NOT NULL AND c.code <> ''
         ${filtreChapitre}

      UNION ALL

      SELECT c.id AS cim11_id, c.code, c.title AS titre,
             similarity(s.search_text, immutable_unaccent(lower($1))) AS score
        FROM cim11_synonymes s
        JOIN cim11_codes c ON c.id = s.cim11_id
       WHERE c.class_kind = 'category'
         AND c.code IS NOT NULL AND c.code <> ''
         ${filtreChapitre}
    )
    SELECT cim11_id, code, titre, score
      FROM candidats
     WHERE score >= $2
     ORDER BY score DESC
     LIMIT 1;
  `;

  const { rows } = await pool.query(sql, params);
  if (rows.length === 0) return null;

  const r = rows[0];
  return {
    cim11_id: r.cim11_id,
    code: r.code,
    titre: r.titre,
    score: Number(r.score),
  };
}


async function enregistrerMatchCim11({ pseudonyme, termeSource, match }) {
  if (!match) return null;
  const { rows } = await pool.query(
    `INSERT INTO entites_cim11
       (pseudonyme, terme_source, cim11_id, code_cim11, titre_cim11, score_similarite)
     VALUES ($1, $2, $3, $4, $5, $6)
     RETURNING id`,
    [pseudonyme, termeSource, match.cim11_id, match.code, match.titre, match.score.toFixed(3)]
  );
  return rows[0].id;
}

module.exports = { trouverMeilleurCodeCim11, enregistrerMatchCim11, SEUIL_SIMILARITE };
