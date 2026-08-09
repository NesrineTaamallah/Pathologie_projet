import { useState, useEffect } from 'react';
import client from '../api/client';
import { IconUsers, IconChart, IconWave, IconAlert, IconRefresh } from '../components/Icons';
import {
  GOUVERNORAT_PALETTE, normalizeKey, pctLabel, monthLabel, dayLabel,
  SectionHeading, CardTitle, HeroStatCard, DonutCard, StackedBar,
} from '../components/DashboardWidgets';
const REGISTRE_COLORS = { sep: '#175F69', epr: '#C98A2C' };

const ACTIVITY_SERIES = [
  { key: 'fiches_consultees', label: 'Fiches consultées', color: '#0B3D5C' },
  { key: 'analyses_lancees', label: 'Analyses lancées', color: '#1E7BB8' },
  { key: 'transcriptions_audio', label: 'Transcriptions audio', color: '#6BB6E3' },
  { key: 'extractions_ocr', label: 'Extractions OCR', color: '#5B4B8A' },
  { key: 'extractions_patient', label: 'Extractions patient', color: '#8677B3' },
  { key: 'exports_patients', label: 'Exports patients', color: '#2F8F6E' },
  { key: 'dossiers_consultes', label: 'Dossiers consultés', color: '#67B79A' },
  { key: 'documents_televerses', label: 'Documents téléversés', color: '#A9D6C6' },
];


const STATUT_LABELS = {
  'stable': 'Stable',
  'perdu de vue': 'Perdu de vue',
  'decede': 'Décédé',
  'libre de crises': 'Libre de crises',
  'epilepsie active': 'Épilepsie active',
};
const STATUT_COLORS = {
  'stable': 'var(--success)',
  'libre de crises': 'var(--success)',
  'perdu de vue': 'var(--warning, orange)',
  'epilepsie active': 'var(--error)',
  'decede': 'var(--slate)',
};

function statutLabel(statut) {
  if (!statut) return 'Non renseigné';
  return STATUT_LABELS[normalizeKey(statut)] || statut;
}
function statutColor(statut) {
  return STATUT_COLORS[normalizeKey(statut)] || 'var(--line)';
}


function InclusionsLineChart({ months, width = 640, height = 220 }) {
  if (!months || months.length === 0) return null;

  const marginLeft = 28;
  const marginBottom = 26;
  const marginTop = 16;
  const plotW = width - marginLeft - 8;
  const plotH = height - marginTop - marginBottom;

  const maxVal = Math.max(...months.map((m) => Math.max(m.sep, m.epr)), 1);
  const niceMax = Math.max(4, Math.ceil(maxVal / 4) * 4);
  const stepX = plotW / Math.max(months.length - 1, 1);

  const gridLines = [0, 0.25, 0.5, 0.75, 1].map((f) => ({
    y: marginTop + plotH * (1 - f),
    label: Math.round(niceMax * f),
  }));

  function pathFor(key) {
    return months
      .map((m, i) => {
        const x = marginLeft + stepX * i;
        const y = marginTop + plotH - (m[key] / niceMax) * plotH;
        return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(' ');
  }

  const hasData = months.some((m) => m.sep > 0 || m.epr > 0);

  return (
    <div>
      <svg width="100%" viewBox={`0 0 ${width} ${height}`} style={{ display: 'block', overflow: 'visible' }}>
        {gridLines.map((g) => (
          <g key={g.label}>
            <line x1={marginLeft} x2={width - 4} y1={g.y} y2={g.y} stroke="var(--line)" strokeWidth="1" />
            <text x={marginLeft - 8} y={g.y + 3} fontSize="10" fill="var(--slate)" textAnchor="end">{g.label}</text>
          </g>
        ))}

        {!hasData && (
          <text x={marginLeft + plotW / 2} y={marginTop + plotH / 2} textAnchor="middle" fontSize="12.5" fill="var(--slate)">
            Aucune inclusion enregistrée sur les 12 derniers mois
          </text>
        )}

        {hasData && (
          <>
            <path d={pathFor('sep')} fill="none" stroke={REGISTRE_COLORS.sep} strokeWidth="2.5" strokeLinejoin="round" strokeLinecap="round" />
            <path d={pathFor('epr')} fill="none" stroke={REGISTRE_COLORS.epr} strokeWidth="2.5" strokeLinejoin="round" strokeLinecap="round" />
            {months.map((m, i) => {
              const x = marginLeft + stepX * i;
              return (
                <g key={m.month}>
                  <circle cx={x} cy={marginTop + plotH - (m.sep / niceMax) * plotH} r="3" fill={REGISTRE_COLORS.sep} />
                  <circle cx={x} cy={marginTop + plotH - (m.epr / niceMax) * plotH} r="3" fill={REGISTRE_COLORS.epr} />
                </g>
              );
            })}
          </>
        )}

        {months.map((m, i) => {
          const x = marginLeft + stepX * i;
          return (
            <text key={m.month} x={x} y={height - 6} textAnchor="middle" fontSize="10.5" fill="var(--slate)">
              {monthLabel(m.month)}
            </text>
          );
        })}
      </svg>
      <div style={{ display: 'flex', gap: 18, marginTop: 10 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12.5 }}>
          <span style={{ width: 8, height: 8, borderRadius: 2, background: REGISTRE_COLORS.sep }} />
          <span style={{ color: 'var(--slate)' }}>SEP</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12.5 }}>
          <span style={{ width: 8, height: 8, borderRadius: 2, background: REGISTRE_COLORS.epr }} />
          <span style={{ color: 'var(--slate)' }}>EPR</span>
        </div>
      </div>
    </div>
  );
}


function DailyStackedBarChart({ days, series, width = 640, height = 230 }) {
  const n = days.length;
  if (n === 0) return null;

  const marginLeft = 28;
  const marginBottom = 26;
  const marginTop = 22;
  const plotW = width - marginLeft - 8;
  const plotH = height - marginTop - marginBottom;

  const totalsPerDay = days.map((d) => series.reduce((s, ser) => s + d[ser.key], 0));
  const rawMax = Math.max(...totalsPerDay, 1);
  const niceMax = Math.max(5, Math.ceil(rawMax / 5) * 5);
  const hasData = totalsPerDay.some((v) => v > 0);

  const gridLines = [0, 0.25, 0.5, 0.75, 1].map((f) => ({
    y: marginTop + plotH * (1 - f),
    label: Math.round(niceMax * f),
  }));

  const slot = plotW / n;
  const barWidth = Math.min(38, slot * 0.5);

  return (
    <div>
      <svg width="100%" viewBox={`0 0 ${width} ${height}`} style={{ display: 'block', overflow: 'visible' }}>
        {gridLines.map((g) => (
          <g key={g.label}>
            <line x1={marginLeft} x2={width - 4} y1={g.y} y2={g.y} stroke="var(--line)" strokeWidth="1" />
            <text x={marginLeft - 8} y={g.y + 3} fontSize="10" fill="var(--slate)" textAnchor="end">{g.label}</text>
          </g>
        ))}

        {!hasData && (
          <text x={marginLeft + plotW / 2} y={marginTop + plotH / 2} textAnchor="middle" fontSize="12.5" fill="var(--slate)">
            Aucune activité enregistrée sur les 7 derniers jours
          </text>
        )}

        {days.map((d, i) => {
          const x = marginLeft + slot * i + (slot - barWidth) / 2;
          let yCursor = marginTop + plotH;
          const dayTotal = totalsPerDay[i];
          const rects = series.map((ser) => {
            const v = d[ser.key];
            if (v <= 0) return null;
            const h = (v / niceMax) * plotH;
            const y = yCursor - h;
            yCursor -= h;
            return (
              <rect key={ser.key} x={x} y={y} width={barWidth} height={Math.max(h, 0)} fill={ser.color} style={{ cursor: 'pointer' }}>
                <title>{`${ser.label} — ${dayLabel(d.day)} : ${v}`}</title>
              </rect>
            );
          });
          return (
            <g key={d.day}>
              {rects}
              {dayTotal > 0 && (
                <text x={x + barWidth / 2} y={marginTop + plotH - (dayTotal / niceMax) * plotH - 6} textAnchor="middle" fontSize="11" fontWeight="700" fill="var(--ink)">
                  {dayTotal}
                </text>
              )}
              <text x={x + barWidth / 2} y={height - 6} textAnchor="middle" fontSize="10.5" fill="var(--slate)">
                {dayLabel(d.day)}
              </text>
            </g>
          );
        })}
      </svg>
      {hasData && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '7px 18px', marginTop: 14 }}>
          {series.map((ser) => (
            <div key={ser.key} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12.5 }}>
              <span style={{ width: 8, height: 8, borderRadius: 2, background: ser.color, flexShrink: 0 }} />
              <span style={{ color: 'var(--slate)' }}>{ser.label}</span>
              <span style={{ fontWeight: 700, color: 'var(--ink)' }}>{days.reduce((a, d) => a + d[ser.key], 0)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}


export default function OverviewTabClinicien({ onAlerteClick }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    client.get('/api/clinicien/overview')
      .then((res) => setData(res.data))
      .catch(() => setError("Impossible de charger la vue d'ensemble."))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return <p className="hint" style={{ padding: '40px 0', textAlign: 'center' }}>Chargement…</p>;
  }
  if (error || !data) {
    return <p className="hint" style={{ padding: '40px 0', textAlign: 'center', color: 'var(--error)' }}>{error || 'Erreur inattendue.'}</p>;
  }

  const t = data.totals;

  const statutSepSegments = data.comparatifSuivi.sep.map((s) => ({ label: statutLabel(s.statut), value: s.count, color: statutColor(s.statut) }));
  const statutEprSegments = data.comparatifSuivi.epr.map((s) => ({ label: statutLabel(s.statut), value: s.count, color: statutColor(s.statut) }));

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>

      {/* =====================================================================
          1. VUE GLOBALE — les deux pathologies confondues, en un coup d'œil
      ===================================================================== */}
      <SectionHeading Icon={IconUsers} title="Vue globale" subtitle="Les deux pathologies confondues" />
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
        <HeroStatCard label="Patients inclus au total" value={t.total_patients} />
        <HeroStatCard label="Registre SEP" value={t.total_sep} />
        <HeroStatCard label="Registre EPR" value={t.total_epr} />
        <HeroStatCard label="Nouvelles inclusions ce mois" value={t.inclusions_ce_mois} />
      </div>

      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
        <DonutCard
          title="Répartition par sexe"
          segments={data.sexeRepartition.map((s, i) => ({ label: s.sexe, value: s.count, color: GOUVERNORAT_PALETTE[i % GOUVERNORAT_PALETTE.length] }))}
          centerLabel="patients"
        />
        <DonutCard
          title="Répartition par tranche d'âge"
          hint={data.ageEstimeApproximatif ? "Âge estimé (âge à l'inclusion + temps écoulé) — le schéma ne contient pas de date de naissance, valeur approximative." : undefined}
          segments={data.ageRepartition.map((a, i) => ({ label: a.tranche, value: a.count, color: GOUVERNORAT_PALETTE[i % GOUVERNORAT_PALETTE.length] }))}
          centerLabel="patients"
        />
      </div>

      <div className="card">
        <CardTitle>Inclusions mensuelles (12 derniers mois)</CardTitle>
        <div style={{ marginTop: 14 }}>
          <InclusionsLineChart months={data.inclusionsByMonth} />
        </div>
      </div>


      <SectionHeading Icon={IconRefresh} title="Alertes extraction" subtitle="Documents et entités médicales pas encore extraits" />
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
        <HeroStatCard
          label="Patients avec document en attente d'extraction"
          value={data.fichesIdentite.patients_avec_extraction_en_attente}
          onClick={data.fichesIdentite.patients_avec_extraction_en_attente > 0 ? () => onAlerteClick?.('identiteManquante') : undefined}
        />
        <HeroStatCard
          label="Patients avec entités médicales en attente d'extraction"
          value={data.fichesEntites.patients_avec_entites_en_attente}
          hint={data.fichesEntites.patients_avec_entites_en_attente > 0 ? "Coordonnées déjà validées, entités pas encore extraites" : undefined}
          onClick={data.fichesEntites.patients_avec_entites_en_attente > 0 ? () => onAlerteClick?.('entitesNonExtraites') : undefined}
        />
      </div>

      {/* =====================================================================
          2. ALERTES — patients à revoir en priorité (remonté juste après
             la vue globale, pour que ce soit la première chose visible
             après les chiffres clés — avant les graphiques et comparatifs).
      ===================================================================== */}
      <SectionHeading Icon={IconAlert} title="Alertes de suivi" subtitle="Cliquez une carte pour voir la liste des patients concernés " />
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0, 1fr))', gap: 16 }}>
        <HeroStatCard
          label="Suivi actif mais point de suivi > 6 mois"
          value={data.alertes.suiviEnRetard}
          hint={data.alertes.suiviEnRetard > 0 ? 'À recontacter' : undefined}
          onClick={() => onAlerteClick?.('suiviEnRetard')}
        />
        <HeroStatCard
          label="SEP sans IRM depuis > 12 mois"
          value={data.alertes.irmAncienne}
          onClick={() => onAlerteClick?.('irmAncienne')}
        />
        <HeroStatCard
          label="Traitements SEP à échéance (30 j)"
          value={data.alertes.traitementsEcheance}
          onClick={() => onAlerteClick?.('traitementsEcheance')}
        />
        <HeroStatCard
          label="EPR sans EEG depuis > 12 mois"
          value={data.alertes.eegAncien}
          hint={data.alertes.eegAncien > 0 ? "Inclut les patients jamais explorés en EEG" : undefined}
          onClick={() => onAlerteClick?.('eegAncien')}
        />
        <HeroStatCard
          label="EPR sans aucun bilan multidisciplinaire"
          value={data.alertes.bilanMultidisciplinaireAbsent}
          hint="Ni neuropsy, ni orthophonie, ni ergothérapie renseignés."
          onClick={() => onAlerteClick?.('bilanMultidisciplinaireAbsent')}
        />
        <HeroStatCard
          label="Transition ado → adulte (16-18 ans)"
          value={data.alertes.transitionAdulte}
          hint={data.alertes.transitionAdulte > 0 ? "Patients en suivi actif à préparer au relais médecine adulte." : undefined}
          onClick={() => onAlerteClick?.('transitionAdulte')}
        />
      </div>

      {/* =====================================================================
          1ter. ALERTES CLINIQUES — basées sur des critères de référence en
          neurologie (NEDA-3 pour la SEP, définition ILAE 2010 pour la
          pharmacorésistance EPR), distinctes des alertes de suivi/délai
          ci-dessus qui portent sur des dates administratives.
      ===================================================================== */}
      <SectionHeading
        Icon={IconAlert}
        title="Alertes cliniques"
        subtitle="Basées sur des critères de référence en neurologie (NEDA-3, ILAE) — cliquez une carte pour voir la liste des patients concernés"
      />
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
        <HeroStatCard
          label="SEP — évidence d'activité de la maladie"
          value={data.alertes.activiteMaladieSep}
          hint={data.alertes.activiteMaladieSep > 0
            ? "Critère NEDA-3 : poussée < 12 mois, progression EDSS confirmée, ou nouvelle(s) lésion(s) IRM. Réévaluer le traitement de fond."
            : "Critère NEDA-3 (No Evidence of Disease Activity)."}
          onClick={() => onAlerteClick?.('activiteMaladieSep')}
        />
        <HeroStatCard
          label="EPR — pharmacorésistance sans bilan pré-chirurgical"
          value={data.alertes.pharmacoresistanceSansEvaluationEpr}
          hint={data.alertes.pharmacoresistanceSansEvaluationEpr > 0
            ? "Définition ILAE 2010 (échec de 2 antiépileptiques bien conduits) atteinte : l'ILAE recommande une évaluation chirurgicale sans délai."
            : "Définition ILAE de la pharmacorésistance."}
          onClick={() => onAlerteClick?.('pharmacoresistanceSansEvaluationEpr')}
        />
      </div>

      {/* =====================================================================
          1bis. QUALITÉ DE SUIVI — indicateur agrégé "% patients à jour"
          Inspiré des indicateurs standard des registres SEP de référence
          (EDSS/IRM tous les 6-12 mois) : complète les alertes individuelles
          plus haut par un baromètre de cohorte, en un coup d'œil.
      ===================================================================== */}
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
        <HeroStatCard
          label="SEP — patients à jour (EDSS + IRM < 12 mois)"
          value={pctLabel(data.suiviQualite?.sep?.aJour, data.suiviQualite?.sep?.total)}
          hint={`${data.suiviQualite?.sep?.total ?? 0} patient(s) en suivi actif sur ${t.total_sep} au total dans le registre SEP (hors perdus de vue / décédés).`}
        />
        <HeroStatCard
          label="EPR — patients à jour (EEG + fréquence crises < 12 mois)"
          value={pctLabel(data.suiviQualite?.epr?.aJour, data.suiviQualite?.epr?.total)}
          hint={`${data.suiviQualite?.epr?.total ?? 0} patient(s) en suivi actif sur ${t.total_epr} au total dans le registre EPR (hors perdus de vue / décédés).`}
        />
      </div>

      {/* =====================================================================
          3. COMPARATIF SEP vs EPR — pour situer les deux registres l'un
             par rapport à l'autre sans se plonger dans le détail de chacun
      ===================================================================== */}
      <SectionHeading Icon={IconChart} title="Comparatif SEP / EPR" subtitle="Statut de suivi de chaque registre, côte à côte" />
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
        <div className="card" style={{ flex: '1 1 320px' }}>
          <CardTitle>Statut de suivi — SEP</CardTitle>
          <div style={{ marginTop: 16 }}><StackedBar segments={statutSepSegments} /></div>
        </div>
        <div className="card" style={{ flex: '1 1 320px' }}>
          <CardTitle>Statut de suivi — EPR</CardTitle>
          <div style={{ marginTop: 16 }}><StackedBar segments={statutEprSegments} /></div>
        </div>
      </div>

      {/* =====================================================================
          4. ACTIVITÉ RÉCENTE DU CLINICIEN
      ===================================================================== */}
      <SectionHeading Icon={IconWave} title="Votre activité récente" subtitle="Vos actions des 7 derniers jours, jour par jour" />
      <div className="card">
        <CardTitle>Historique de vos actions (7 derniers jours)</CardTitle>
        <div style={{ marginTop: 14 }}>
          <DailyStackedBarChart days={data.recentActivity} series={ACTIVITY_SERIES} />
        </div>
      </div>
    </div>
  );
}