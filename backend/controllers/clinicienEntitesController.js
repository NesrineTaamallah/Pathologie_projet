const pool = require('../config/db');
const { normalizedSql } = require('../utils/clinicienSql');



const REQUETES = {
  suiviEnRetard: async () => {
    const sql = `
      SELECT pseudonyme, 'SEP' AS registre, date_dernier_suivi AS derniere_info,
             statut_dernier_suivi AS statut
      FROM sep_suivi s
      WHERE ${normalizedSql('s.statut_dernier_suivi')} NOT IN ('perdu de vue', 'decede')
        AND s.date_dernier_suivi IS NOT NULL
        AND s.date_dernier_suivi < now() - interval '6 months'

      UNION ALL

      SELECT s.pseudonyme, 'EPR' AS registre, act.derniere_activite AS derniere_info,
             s.statut_dernier_suivi AS statut
      FROM epr_suivi s
      LEFT JOIN (
        SELECT p.pseudonyme,
               GREATEST(
                 MAX(fc.date_rapport), MAX(tc.date_observation), MAX(eeg.date_eeg),
                 MAX(im.date_examen), MAX(bp.date_bilan), MAX(bn.date_bilan),
                 MAX(bo.date_bilan), MAX(be.date_bilan), MAX(ch.date_chirurgie),
                 MAX(alt.date_debut)
               ) AS derniere_activite
        FROM patients p
        LEFT JOIN epr_frequence_crises fc ON fc.pseudonyme = p.pseudonyme
        LEFT JOIN epr_type_crise tc ON tc.pseudonyme = p.pseudonyme
        LEFT JOIN epr_eeg eeg ON eeg.pseudonyme = p.pseudonyme
        LEFT JOIN epr_imagerie im ON im.pseudonyme = p.pseudonyme
        LEFT JOIN epr_bilan_prechirurgical bp ON bp.pseudonyme = p.pseudonyme
        LEFT JOIN epr_bilan_neuropsy bn ON bn.pseudonyme = p.pseudonyme
        LEFT JOIN epr_bilan_orthophonique bo ON bo.pseudonyme = p.pseudonyme
        LEFT JOIN epr_bilan_ergotherapique be ON be.pseudonyme = p.pseudonyme
        LEFT JOIN epr_chirurgie ch ON ch.pseudonyme = p.pseudonyme
        LEFT JOIN epr_alternatives_therapeutiques alt ON alt.pseudonyme = p.pseudonyme
        WHERE p.registre = 'EPR'
        GROUP BY p.pseudonyme
      ) act ON act.pseudonyme = s.pseudonyme
      WHERE ${normalizedSql('s.statut_dernier_suivi')} NOT IN ('perdu de vue', 'decede')
        AND act.derniere_activite IS NOT NULL
        AND act.derniere_activite < now() - interval '6 months'
      ORDER BY derniere_info ASC NULLS FIRST
    `;
    return pool.query(sql);
  },

  irmAncienne: async () => pool.query(`
    SELECT p.pseudonyme, 'SEP' AS registre, MAX(i.date_examen) AS derniere_info,
           NULL::text AS statut
    FROM patients p
    LEFT JOIN sep_irm i ON i.pseudonyme = p.pseudonyme
    WHERE p.registre = 'SEP'
    GROUP BY p.pseudonyme
    HAVING MAX(i.date_examen) IS NULL
        OR MAX(i.date_examen) < now() - interval '12 months'
    ORDER BY derniere_info ASC NULLS FIRST
  `),

  traitementsEcheance: async () => pool.query(`
    SELECT pseudonyme, 'SEP' AS registre, date_fin AS derniere_info,
           molecule AS statut
    FROM sep_traitement_fond
    WHERE date_fin IS NOT NULL
      AND date_fin BETWEEN now() AND now() + interval '30 days'
    ORDER BY date_fin ASC
  `),

  eegAncien: async () => pool.query(`
    SELECT p.pseudonyme, 'EPR' AS registre, MAX(e.date_eeg) AS derniere_info,
           NULL::text AS statut
    FROM patients p
    LEFT JOIN epr_eeg e ON e.pseudonyme = p.pseudonyme
    WHERE p.registre = 'EPR'
    GROUP BY p.pseudonyme
    HAVING MAX(e.date_eeg) IS NULL
        OR MAX(e.date_eeg) < now() - interval '12 months'
    ORDER BY derniere_info ASC NULLS FIRST
  `),

  bilanMultidisciplinaireAbsent: async () => pool.query(`
    SELECT p.pseudonyme, 'EPR' AS registre, NULL::date AS derniere_info,
           NULL::text AS statut
    FROM patients p
    LEFT JOIN epr_bilan_neuropsy bn ON bn.pseudonyme = p.pseudonyme
    LEFT JOIN epr_bilan_orthophonique bo ON bo.pseudonyme = p.pseudonyme
    LEFT JOIN epr_bilan_ergotherapique be ON be.pseudonyme = p.pseudonyme
    WHERE p.registre = 'EPR'
      AND bn.pseudonyme IS NULL AND bo.pseudonyme IS NULL AND be.pseudonyme IS NULL
  `),

  
  transitionAdulte: async () => pool.query(`
    SELECT e.pseudonyme, e.registre, NULL::date AS derniere_info,
           e.statut
    FROM (
      SELECT
        p.pseudonyme,
        p.registre,
        p.age + EXTRACT(YEAR FROM age(now(), COALESCE(p.date_inclusion, now()))) AS age_estime,
        COALESCE(ss.statut_dernier_suivi, es.statut_dernier_suivi) AS statut
      FROM patients p
      LEFT JOIN sep_suivi ss ON ss.pseudonyme = p.pseudonyme
      LEFT JOIN epr_suivi es ON es.pseudonyme = p.pseudonyme
      WHERE p.age IS NOT NULL
    ) e
    WHERE e.age_estime BETWEEN 16 AND 18
      AND ${normalizedSql('e.statut')} NOT IN ('perdu de vue', 'decede')
    ORDER BY e.pseudonyme
  `),

  
  activiteMaladieSep: async () => pool.query(`
    WITH edss_ordered AS (
      SELECT pseudonyme, score_edss, date_visite,
             ROW_NUMBER() OVER (PARTITION BY pseudonyme ORDER BY date_visite DESC) AS rn
      FROM sep_edss_visites
      WHERE score_edss IS NOT NULL
    ),
    edss_progression AS (
      SELECT a.pseudonyme, (a.score_edss - b.score_edss) AS delta, b.score_edss AS score_reference
      FROM edss_ordered a
      JOIN edss_ordered b ON b.pseudonyme = a.pseudonyme AND b.rn = 2
      WHERE a.rn = 1
    ),
    irm_recente AS (
      SELECT DISTINCT ON (pseudonyme) pseudonyme, date_examen, nouvelles_lesions_vs_irm_anterieure
      FROM sep_irm
      ORDER BY pseudonyme, date_examen DESC
    ),
    derniere_poussee AS (
      SELECT pseudonyme, MAX(date_poussee) AS derniere_poussee FROM sep_poussees GROUP BY pseudonyme
    )
    SELECT p.pseudonyme, 'SEP' AS registre,
           GREATEST(dp.derniere_poussee, irm.date_examen) AS derniere_info,
           CASE
             WHEN dp.derniere_poussee >= now() - interval '12 months' THEN 'Poussée récente (< 12 mois)'
             WHEN irm.nouvelles_lesions_vs_irm_anterieure AND irm.date_examen >= now() - interval '12 months'
               THEN 'Nouvelle(s) lésion(s) IRM'
             WHEN edp.delta IS NOT NULL AND (
                    (edp.score_reference <= 5.5 AND edp.delta >= 1)
                 OR (edp.score_reference > 5.5 AND edp.delta >= 0.5)
                  ) THEN 'Progression EDSS confirmée'
           END AS statut
    FROM patients p
    JOIN sep_suivi s ON s.pseudonyme = p.pseudonyme
    LEFT JOIN derniere_poussee dp ON dp.pseudonyme = p.pseudonyme
    LEFT JOIN irm_recente irm ON irm.pseudonyme = p.pseudonyme
    LEFT JOIN edss_progression edp ON edp.pseudonyme = p.pseudonyme
    WHERE p.registre = 'SEP'
      AND ${normalizedSql('s.statut_dernier_suivi')} NOT IN ('perdu de vue', 'decede')
      AND (
        (dp.derniere_poussee >= now() - interval '12 months')
        OR (irm.nouvelles_lesions_vs_irm_anterieure AND irm.date_examen >= now() - interval '12 months')
        OR (edp.delta IS NOT NULL AND (
              (edp.score_reference <= 5.5 AND edp.delta >= 1)
           OR (edp.score_reference > 5.5 AND edp.delta >= 0.5)
        ))
      )
    ORDER BY derniere_info DESC NULLS LAST
  `),

  
  pharmacoresistanceSansEvaluationEpr: async () => pool.query(`
    SELECT p.pseudonyme, 'EPR' AS registre, NULL::date AS derniere_info,
           'Pharmacorésistance confirmée (ILAE), aucun bilan pré-chirurgical' AS statut
    FROM patients p
    JOIN epr_pharmacoresistance epr ON epr.pseudonyme = p.pseudonyme
    LEFT JOIN epr_bilan_prechirurgical bp ON bp.pseudonyme = p.pseudonyme
    WHERE p.registre = 'EPR'
      AND epr.statut_pharmacoresistance_confirme = TRUE
      AND bp.pseudonyme IS NULL
    ORDER BY p.pseudonyme
  `),

  
  identiteManquante: async () => pool.query(`
    SELECT DISTINCT p.pseudonyme, p.registre, NULL::date AS derniere_info,
           NULL::text AS statut
    FROM patients p
    JOIN documents_bruts d
      ON d.pseudonyme = p.pseudonyme
     AND d.texte_transcrit IS NOT NULL
     AND TRIM(d.texte_transcrit) <> ''
     AND d.coordonnees_extraites = false
    ORDER BY p.pseudonyme
  `),

  // Entités médicales : uniquement les dossiers dont les coordonnées sont déjà
  // validées (coordonnees_extraites = true) et dont au moins un document
  // transcrit n'est pas encore passé par le pipeline d'entités.
  entitesNonExtraites: async () => pool.query(`
    SELECT DISTINCT p.pseudonyme, p.registre, NULL::date AS derniere_info,
           NULL::text AS statut
    FROM patients p
    JOIN documents_bruts d
      ON d.pseudonyme = p.pseudonyme
     AND d.texte_transcrit IS NOT NULL
     AND TRIM(d.texte_transcrit) <> ''
     AND d.coordonnees_extraites = true
     AND COALESCE(d.entites_extraites, false) = false
    ORDER BY p.pseudonyme
  `),
};

const LABELS = {
  suiviEnRetard: 'Suivi actif mais point de suivi > 6 mois',
  irmAncienne: 'SEP sans IRM depuis > 12 mois',
  traitementsEcheance: 'Traitements SEP à échéance (30 j)',
  eegAncien: 'EPR sans EEG depuis > 12 mois',
  bilanMultidisciplinaireAbsent: 'EPR sans aucun bilan multidisciplinaire',
  transitionAdulte: 'Transition ado → adulte (16-18 ans)',
  identiteManquante: 'Fiches sans extraction des données',
  entitesNonExtraites: 'Fiches sans extraction des entités médicales',
  activiteMaladieSep: 'SEP — évidence d\'activité de la maladie (NEDA-3)',
  pharmacoresistanceSansEvaluationEpr: 'EPR — pharmacorésistance ILAE sans bilan pré-chirurgical',
};

async function getListePatientsAlerte(req, res) {
  const { type } = req.params;
  const runner = REQUETES[type];
  if (!runner) {
    return res.status(400).json({ error: "Type d'alerte inconnu." });
  }
  try {
    const result = await runner();
    res.json({
      type,
      label: LABELS[type],
      total: result.rowCount,
      patients: result.rows.map((r) => ({
        pseudonyme: r.pseudonyme,
        registre: r.registre,
        derniereInfo: r.derniere_info instanceof Date ? r.derniere_info.toISOString().slice(0, 10) : r.derniere_info,
        statut: r.statut,
      })),
    });
  } catch (err) {
    console.error('Erreur getListePatientsAlerte :', err);
    res.status(500).json({ error: 'Erreur serveur.' });
  }
}

module.exports = { getListePatientsAlerte };