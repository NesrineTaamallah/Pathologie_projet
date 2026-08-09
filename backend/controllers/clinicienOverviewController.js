const pool = require('../config/db');
const { normalizedSql } = require('../utils/clinicienSql');
const { decrypt } = require('../utils/cryptoUtils');
const { logAccess } = require('../utils/accessLog');


function parseDateNaissance(raw) {
  if (!raw) return null;
  const iso = /^(\d{4})-(\d{2})-(\d{2})$/.exec(raw);
  if (iso) return new Date(Date.UTC(+iso[1], +iso[2] - 1, +iso[3]));
  const fr = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(raw);
  if (fr) return new Date(Date.UTC(+fr[3], +fr[2] - 1, +fr[1]));
  return null;
}


function todayTunisISO() {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Africa/Tunis' }).format(new Date());
}

function ageEnAnnees(dateNaissance, reference = new Date()) {
  let age = reference.getUTCFullYear() - dateNaissance.getUTCFullYear();
  const moisJourDepasses =
    reference.getUTCMonth() > dateNaissance.getUTCMonth() ||
    (reference.getUTCMonth() === dateNaissance.getUTCMonth() &&
      reference.getUTCDate() >= dateNaissance.getUTCDate());
  if (!moisJourDepasses) age -= 1;
  return age;
}

function trancheDe(age) {
  if (age === null || age === undefined) return 'Non renseigné';
  if (age < 2) return '0-2 ans';
  if (age < 6) return '2-6 ans';
  if (age < 12) return '6-12 ans';
  if (age < 18) return '12-18 ans';
  return '18 ans et +';
}


async function getClinicienOverview(req, res) {
  try {
    const [
      totals,
      sexeRepartition,
      fichesIdentite,
      fichesEntites,
      inclusionsByMonth,
      statutSuiviSep,
      statutSuiviEpr,
      alertesSuiviEnRetard,
      alertesIrmAncienne,
      alertesTraitementsEcheance,
      alertesEegAncien,
      alertesBilanMultidisciplinaireAbsent,
      alertesTransitionAdulte,
      alertesActiviteMaladieSep,
      alertesPharmacoresistanceSansEvaluationEpr,
      recentActivity,
      suiviQualite,
    ] = await Promise.all([
      pool.query(`
        SELECT
          COUNT(*)::int AS total_patients,
          COUNT(*) FILTER (WHERE registre = 'SEP')::int AS total_sep,
          COUNT(*) FILTER (WHERE registre = 'EPR')::int AS total_epr,
          COUNT(*) FILTER (WHERE date_inclusion >= date_trunc('month', now() AT TIME ZONE 'Africa/Tunis'))::int AS inclusions_ce_mois
        FROM patients
      `),

      
      pool.query(`
        SELECT
          COALESCE(NULLIF(TRIM(sexe), ''), 'Non renseigné') AS sexe,
          COUNT(*)::int AS count
        FROM (
          SELECT ic.sexe FROM patients p
          JOIN sep_identification_clinique ic ON ic.pseudonyme = p.pseudonyme
          WHERE p.registre = 'SEP'
          UNION ALL
          SELECT ic.sexe FROM patients p
          JOIN epr_identification_clinique ic ON ic.pseudonyme = p.pseudonyme
          WHERE p.registre = 'EPR'
        ) both_registres
        GROUP BY 1
      `),

      
      pool.query(`
        SELECT
          COUNT(DISTINCT p.pseudonyme)::int AS total_patients,
          COUNT(DISTINCT p.pseudonyme) FILTER (WHERE d.id IS NULL)::int AS patients_sans_extraction_en_attente,
          COUNT(DISTINCT p.pseudonyme) FILTER (WHERE d.id IS NOT NULL)::int AS patients_avec_extraction_en_attente
        FROM patients p
        LEFT JOIN documents_bruts d
          ON d.pseudonyme = p.pseudonyme
         AND d.texte_transcrit IS NOT NULL
         AND TRIM(d.texte_transcrit) <> ''
         AND d.coordonnees_extraites = false
      `),

      
      pool.query(`
        SELECT
          COUNT(DISTINCT p.pseudonyme) FILTER (WHERE d.id IS NOT NULL)::int AS patients_avec_entites_en_attente
        FROM patients p
        LEFT JOIN documents_bruts d
          ON d.pseudonyme = p.pseudonyme
         AND d.texte_transcrit IS NOT NULL
         AND TRIM(d.texte_transcrit) <> ''
         AND d.coordonnees_extraites = true
         AND COALESCE(d.entites_extraites, false) = false
      `),

      pool.query(`
        SELECT
          to_char(m, 'YYYY-MM') AS month,
          COALESCE(COUNT(p.pseudonyme) FILTER (WHERE p.registre = 'SEP'), 0)::int AS sep,
          COALESCE(COUNT(p.pseudonyme) FILTER (WHERE p.registre = 'EPR'), 0)::int AS epr
        FROM generate_series(
          date_trunc('month', now()) - interval '11 months',
          date_trunc('month', now()),
          interval '1 month'
        ) m
        LEFT JOIN patients p
          ON date_trunc('month', p.date_inclusion) = m
        GROUP BY m
        ORDER BY m
      `),

      pool.query(`
        SELECT COALESCE(statut_dernier_suivi, 'Non renseigné') AS statut, COUNT(*)::int AS count
        FROM sep_suivi
        GROUP BY statut_dernier_suivi
      `),

      pool.query(`
        SELECT COALESCE(statut_dernier_suivi, 'Non renseigné') AS statut, COUNT(*)::int AS count
        FROM epr_suivi
        GROUP BY statut_dernier_suivi
      `),

      pool.query(`
        SELECT COUNT(*)::int AS count FROM (
          SELECT s.pseudonyme
          FROM sep_suivi s
          WHERE ${normalizedSql('s.statut_dernier_suivi')} NOT IN ('perdu de vue', 'decede')
            AND s.date_dernier_suivi IS NOT NULL
            AND s.date_dernier_suivi < now() - interval '6 months'

          UNION ALL

          SELECT s.pseudonyme
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
        ) alertes
      `),

      
      pool.query(`
        SELECT COUNT(*)::int AS count
        FROM patients p
        LEFT JOIN sep_irm i ON i.pseudonyme = p.pseudonyme
        WHERE p.registre = 'SEP'
        GROUP BY p.pseudonyme
        HAVING MAX(i.date_examen) IS NULL
            OR MAX(i.date_examen) < now() - interval '12 months'
      `).then((r) => ({ rows: [{ count: r.rowCount }] })),

      pool.query(`
        SELECT COUNT(*)::int AS count
        FROM sep_traitement_fond
        WHERE date_fin IS NOT NULL
          AND date_fin BETWEEN now() AND now() + interval '30 days'
      `),

      
      pool.query(`
        SELECT COUNT(*)::int AS count
        FROM patients p
        LEFT JOIN epr_eeg e ON e.pseudonyme = p.pseudonyme
        WHERE p.registre = 'EPR'
        GROUP BY p.pseudonyme
        HAVING MAX(e.date_eeg) IS NULL
            OR MAX(e.date_eeg) < now() - interval '12 months'
      `).then((r) => ({ rows: [{ count: r.rowCount }] })),

      
      pool.query(`
        SELECT COUNT(*)::int AS count
        FROM patients p
        LEFT JOIN epr_bilan_neuropsy bn ON bn.pseudonyme = p.pseudonyme
        LEFT JOIN epr_bilan_orthophonique bo ON bo.pseudonyme = p.pseudonyme
        LEFT JOIN epr_bilan_ergotherapique be ON be.pseudonyme = p.pseudonyme
        WHERE p.registre = 'EPR'
          AND bn.pseudonyme IS NULL AND bo.pseudonyme IS NULL AND be.pseudonyme IS NULL
      `),

      
      pool.query(`
        SELECT COUNT(*)::int AS count
        FROM (
          SELECT
            p.pseudonyme,
            p.age + EXTRACT(YEAR FROM age(now(), COALESCE(p.date_inclusion, now()))) AS age_estime,
            COALESCE(ss.statut_dernier_suivi, es.statut_dernier_suivi) AS statut
          FROM patients p
          LEFT JOIN sep_suivi ss ON ss.pseudonyme = p.pseudonyme
          LEFT JOIN epr_suivi es ON es.pseudonyme = p.pseudonyme
          WHERE p.age IS NOT NULL
        ) e
        WHERE e.age_estime BETWEEN 16 AND 18
          AND ${normalizedSql('e.statut')} NOT IN ('perdu de vue', 'decede')
      `),

      
      pool.query(`
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
        SELECT COUNT(*)::int AS count
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
      `),

      
      pool.query(`
        SELECT COUNT(*)::int AS count
        FROM patients p
        JOIN epr_pharmacoresistance epr ON epr.pseudonyme = p.pseudonyme
        LEFT JOIN epr_bilan_prechirurgical bp ON bp.pseudonyme = p.pseudonyme
        WHERE p.registre = 'EPR'
          AND epr.statut_pharmacoresistance_confirme = TRUE
          AND bp.pseudonyme IS NULL
      `),

      pool.query(
        `SELECT
           d::date AS day,
           COUNT(*) FILTER (WHERE al.action LIKE 'coordonnee_patient_reveal%')::int AS fiches_consultees,
           COUNT(*) FILTER (WHERE al.action LIKE 'analyse_statistique%')::int AS analyses_lancees,
           COUNT(*) FILTER (WHERE al.action = 'transcription_audio')::int AS transcriptions_audio,
           COUNT(*) FILTER (WHERE al.action = 'extraction_ocr')::int AS extractions_ocr,
           COUNT(*) FILTER (WHERE al.action = 'extraction_patient')::int AS extractions_patient,
           COUNT(*) FILTER (WHERE al.action = 'export_patients')::int AS exports_patients,
           COUNT(*) FILTER (WHERE al.action = 'dossier_view')::int AS dossiers_consultes,
           COUNT(*) FILTER (WHERE al.action = 'dossier_document_creer')::int AS documents_televerses
         FROM generate_series(
           $2::date - interval '6 days',
           $2::date,
           interval '1 day'
         ) d
         LEFT JOIN access_logs al
           ON (al.created_at AT TIME ZONE 'Africa/Tunis')::date = d::date
           AND al.user_id = $1
           AND (
             al.action LIKE 'coordonnee_patient_reveal%'
             OR al.action LIKE 'analyse_statistique%'
             OR al.action IN (
               'transcription_audio', 'extraction_ocr', 'extraction_patient',
               'export_patients', 'dossier_view', 'dossier_document_creer'
             )
           )
         GROUP BY d
         ORDER BY d`,
        [req.user.sub, todayTunisISO()]
      ),

      
      pool.query(`
        WITH sep_actifs AS (
          SELECT s.pseudonyme
          FROM sep_suivi s
          WHERE ${normalizedSql('s.statut_dernier_suivi')} NOT IN ('perdu de vue', 'decede')
        ),
        sep_a_jour AS (
          SELECT a.pseudonyme
          FROM sep_actifs a
          JOIN (SELECT pseudonyme, MAX(date_visite) AS d FROM sep_edss_visites GROUP BY pseudonyme) edss
            ON edss.pseudonyme = a.pseudonyme AND edss.d >= now() - interval '12 months'
          JOIN (SELECT pseudonyme, MAX(date_examen) AS d FROM sep_irm GROUP BY pseudonyme) irm
            ON irm.pseudonyme = a.pseudonyme AND irm.d >= now() - interval '12 months'
        ),
        epr_actifs AS (
          SELECT s.pseudonyme
          FROM epr_suivi s
          WHERE ${normalizedSql('s.statut_dernier_suivi')} NOT IN ('perdu de vue', 'decede')
        ),
        epr_a_jour AS (
          SELECT a.pseudonyme
          FROM epr_actifs a
          JOIN (SELECT pseudonyme, MAX(date_eeg) AS d FROM epr_eeg GROUP BY pseudonyme) eeg
            ON eeg.pseudonyme = a.pseudonyme AND eeg.d >= now() - interval '12 months'
          JOIN (SELECT pseudonyme, MAX(date_rapport) AS d FROM epr_frequence_crises GROUP BY pseudonyme) fc
            ON fc.pseudonyme = a.pseudonyme AND fc.d >= now() - interval '12 months'
        )
        SELECT
          (SELECT COUNT(*) FROM sep_actifs)::int AS sep_actifs_total,
          (SELECT COUNT(*) FROM sep_a_jour)::int AS sep_a_jour,
          (SELECT COUNT(*) FROM epr_actifs)::int AS epr_actifs_total,
          (SELECT COUNT(*) FROM epr_a_jour)::int AS epr_a_jour
      `),
    ]);

    
    const ageRows = await pool.query(`
      SELECT p.pseudonyme, p.age, cp.date_naissance
      FROM patients p
      LEFT JOIN coordonnee_patient cp ON cp.pseudonyme = p.pseudonyme
    `);

    const tranchesCount = {};
    let dateNaissanceUtilisees = 0;

    for (const row of ageRows.rows) {
      let age = null;
      const dateNaissance = row.date_naissance ? parseDateNaissance(decrypt(row.date_naissance)) : null;

      if (dateNaissance) {
        age = ageEnAnnees(dateNaissance);
        dateNaissanceUtilisees += 1;
      } else if (row.age !== null && row.age !== undefined) {
        age = row.age; 
      }

      const tranche = trancheDe(age);
      tranchesCount[tranche] = (tranchesCount[tranche] || 0) + 1;
    }

    const ageRepartitionRows = Object.entries(tranchesCount).map(([tranche, count]) => ({ tranche, count }));

    if (dateNaissanceUtilisees > 0) {
      await logAccess({
        userId: req.user.sub,
        action: 'coordonnee_patient_reveal_bulk_age',
        success: true,
        req,
      });
    }

    res.json({
      totals: totals.rows[0],
      sexeRepartition: sexeRepartition.rows,
      ageRepartition: ageRepartitionRows,
      ageEstimeApproximatif: false, 
      fichesIdentite: fichesIdentite.rows[0],
      fichesEntites: fichesEntites.rows[0],
      inclusionsByMonth: inclusionsByMonth.rows,
      comparatifSuivi: {
        sep: statutSuiviSep.rows,
        epr: statutSuiviEpr.rows,
      },
      alertes: {
        suiviEnRetard: alertesSuiviEnRetard.rows[0]?.count ?? 0,
        irmAncienne: alertesIrmAncienne.rows[0]?.count ?? 0,
        traitementsEcheance: alertesTraitementsEcheance.rows[0]?.count ?? 0,
        eegAncien: alertesEegAncien.rows[0]?.count ?? 0,
        bilanMultidisciplinaireAbsent: alertesBilanMultidisciplinaireAbsent.rows[0]?.count ?? 0,
        transitionAdulte: alertesTransitionAdulte.rows[0]?.count ?? 0,
        activiteMaladieSep: alertesActiviteMaladieSep.rows[0]?.count ?? 0,
        pharmacoresistanceSansEvaluationEpr: alertesPharmacoresistanceSansEvaluationEpr.rows[0]?.count ?? 0,
      },
      recentActivity: recentActivity.rows.map((r) => ({
        day: r.day instanceof Date ? r.day.toISOString().slice(0, 10) : r.day,
        fiches_consultees: r.fiches_consultees,
        analyses_lancees: r.analyses_lancees,
        transcriptions_audio: r.transcriptions_audio,
        extractions_ocr: r.extractions_ocr,
        extractions_patient: r.extractions_patient,
        exports_patients: r.exports_patients,
        dossiers_consultes: r.dossiers_consultes,
        documents_televerses: r.documents_televerses,
      })),
      suiviQualite: {
        sep: { aJour: suiviQualite.rows[0]?.sep_a_jour ?? 0, total: suiviQualite.rows[0]?.sep_actifs_total ?? 0 },
        epr: { aJour: suiviQualite.rows[0]?.epr_a_jour ?? 0, total: suiviQualite.rows[0]?.epr_actifs_total ?? 0 },
      },
    });
  } catch (err) {
    console.error('Erreur getClinicienOverview :', err);
    res.status(500).json({ error: 'Erreur serveur.' });
  }
}

module.exports = { getClinicienOverview };