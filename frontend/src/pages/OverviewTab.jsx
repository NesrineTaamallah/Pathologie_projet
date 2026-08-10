import { useState, useEffect, useCallback } from 'react';
import client from '../api/client';
import {
  IconUsers, IconShield, IconSearch, IconEyeOff, IconLock,
  IconCheckCircle, IconChart,
} from '../components/Icons';

const ACTION_LABELS = {
  CREATE_USER: 'Création de compte',
  CREATE_USER_EMAIL_FAILED: 'Création de compte (email échoué)',
  RESET_2FA: 'Réinitialisation 2FA',
  RESEND_TEMP_PASSWORD: 'Renvoi mot de passe temporaire',
  RESEND_TEMP_PASSWORD_EMAIL_FAILED: 'Renvoi mot de passe (email échoué)',
  UNLOCK_ACCOUNT: 'Déverrouillage de compte',
  DEACTIVATE_ACCOUNT: 'Désactivation de compte',
  REACTIVATE_ACCOUNT: 'Réactivation de compte',
};

const ACTION_COLORS = {
  CREATE_USER: '#0c2f5e',
  RESEND_TEMP_PASSWORD: '#5b9fe3',
  RESET_2FA: '#145aa3',
  UNLOCK_ACCOUNT: '#1d72c9',
  DEACTIVATE_ACCOUNT: '#c23b4e',
  REACTIVATE_ACCOUNT: '#1c7a52',
};

const ROLE_LABELS = { admin: 'Admins', clinicien: 'Cliniciens', chercheur: 'Chercheurs', statisticien: 'Statisticiens' };
const ROLE_COLORS = { admin: '#0c2f5e', clinicien: '#1d72c9', chercheur: '#5b9fe3', statisticien: '#145aa3' };

function actionLabel(action) {
  const base = action.split(':')[0];
  return ACTION_LABELS[base] || base;
}

function timeAgo(dateStr) {
  const diffMs = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return "à l'instant";
  if (mins < 60) return `il y a ${mins} min`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `il y a ${hours} h`;
  const days = Math.floor(hours / 24);
  return `il y a ${days} j`;
}

function dayLabel(dayStr) {
  return new Date(dayStr + 'T00:00:00').toLocaleDateString('fr-FR', { day: '2-digit', month: 'short' });
}



function SectionHeading({ Icon, title, subtitle }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, margin: '4px 0 -2px' }}>
      <div style={{
        width: 26, height: 26, borderRadius: 8, background: 'var(--primary-tint)',
        color: 'var(--primary-deep)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
      }}>
        <Icon size={14} />
      </div>
      <div>
        <p style={{ margin: 0, fontSize: 13.5, fontWeight: 700, color: 'var(--ink)', letterSpacing: -0.1 }}>{title}</p>
        {subtitle && <p style={{ margin: 0, fontSize: 11.5, color: 'var(--slate)' }}>{subtitle}</p>}
      </div>
    </div>
  );
}

function CardTitle({ children, hint }) {
  return (
    <div style={{ marginBottom: hint ? 2 : 0 }}>
      <h2 style={{ margin: 0 }}>{children}</h2>
      {hint && <p className="hint" style={{ marginTop: 4 }}>{hint}</p>}
    </div>
  );
}



function Donut({ segments, size = 132, thickness = 20 }) {
  const total = segments.reduce((s, x) => s + x.value, 0);
  const r = (size - thickness) / 2;
  const c = size / 2;
  const circumference = 2 * Math.PI * r;
  let offset = 0;

  if (total === 0) {
    return (
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        <circle cx={c} cy={c} r={r} fill="none" stroke="var(--line)" strokeWidth={thickness} />
      </svg>
    );
  }

  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} style={{ transform: 'rotate(-90deg)' }}>
      <circle cx={c} cy={c} r={r} fill="none" stroke="var(--line)" strokeWidth={thickness} />
      {segments.map((seg) => {
        const frac = seg.value / total;
        const dash = frac * circumference;
        const el = (
          <circle
            key={seg.label}
            cx={c}
            cy={c}
            r={r}
            fill="none"
            stroke={seg.color}
            strokeWidth={thickness}
            strokeDasharray={`${dash} ${circumference - dash}`}
            strokeDashoffset={-offset}
            strokeLinecap={segments.filter((s) => s.value > 0).length > 1 ? 'butt' : 'round'}
            style={{ transition: 'stroke-dasharray 0.6s ease' }}
          />
        );
        offset += dash;
        return el;
      })}
    </svg>
  );
}

function DonutCard({ title, hint, segments, centerLabel, centerValue }) {
  const total = segments.reduce((s, x) => s + x.value, 0);
  return (
    <div className="card" style={{ flex: '1 1 300px' }}>
      <CardTitle hint={hint}>{title}</CardTitle>
      <div style={{ display: 'flex', alignItems: 'center', gap: 24, marginTop: 16 }}>
        <div style={{ position: 'relative', width: 132, height: 132, flexShrink: 0 }}>
          <Donut segments={segments} />
          <div style={{
            position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column',
            alignItems: 'center', justifyContent: 'center',
          }}>
            <span style={{ fontFamily: 'var(--font-display)', fontSize: 26, fontWeight: 800, color: 'var(--ink)' }}>
              {centerValue ?? total}
            </span>
            <span style={{ fontSize: 10.5, color: 'var(--slate)' }}>{centerLabel ?? 'total'}</span>
          </div>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 9, flex: 1 }}>
          {segments.map((seg) => (
            <div key={seg.label} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
              <span style={{ width: 9, height: 9, borderRadius: 3, background: seg.color, flexShrink: 0 }} />
              <span style={{ color: 'var(--ink)', flex: 1 }}>{seg.label}</span>
              <span style={{ fontWeight: 700, color: 'var(--ink)' }}>{seg.value}</span>
              <span style={{ color: 'var(--slate)', fontSize: 11.5, minWidth: 34, textAlign: 'right' }}>
                {total > 0 ? Math.round((seg.value / total) * 100) : 0}%
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function Sparkline({ points, width = 220, height = 40, color = 'var(--primary)' }) {
  if (!points || points.length === 0) return null;
  const max = Math.max(...points, 1);
  const min = Math.min(...points, 0);
  const range = max - min || 1;
  const stepX = width / Math.max(points.length - 1, 1);
  const coords = points.map((v, i) => [i * stepX, height - ((v - min) / range) * (height - 6) - 3]);
  const linePath = coords.map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
  const areaPath = `${linePath} L${width},${height} L0,${height} Z`;
  const last = coords[coords.length - 1];

  return (
    <svg width="100%" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" style={{ display: 'block' }}>
      <path d={areaPath} fill={color} opacity="0.1" />
      <path d={linePath} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={last[0]} cy={last[1]} r="3" fill={color} />
    </svg>
  );
}

function StackedBar({ segments, height = 22 }) {
  const total = segments.reduce((s, x) => s + x.value, 0);
  return (
    <div>
      <div style={{
        display: 'flex', width: '100%', height, borderRadius: 8, overflow: 'hidden',
        border: '1px solid var(--line)', background: 'var(--paper)',
      }}>
        {total === 0
          ? <div style={{ flex: 1 }} />
          : segments.filter((s) => s.value > 0).map((seg) => (
            <div
              key={seg.label}
              title={`${seg.label}: ${seg.value}`}
              style={{
                width: `${(seg.value / total) * 100}%`,
                background: seg.color,
                transition: 'width 0.6s ease',
              }}
            />
          ))}
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '7px 18px', marginTop: 12 }}>
        {segments.map((seg) => (
          <div key={seg.label} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12.5 }}>
            <span style={{ width: 8, height: 8, borderRadius: 2, background: seg.color, flexShrink: 0 }} />
            <span style={{ color: 'var(--slate)' }}>{seg.label}</span>
            <span style={{ fontWeight: 700, color: 'var(--ink)' }}>{seg.value}</span>
          </div>
        ))}
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

  const totalsPerDay = days.map((_, i) => series.reduce((s, ser) => s + ser.values[i], 0));
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
            Aucune action enregistrée sur les 7 derniers jours
          </text>
        )}

        {days.map((d, i) => {
          const x = marginLeft + slot * i + (slot - barWidth) / 2;
          let yCursor = marginTop + plotH;
          const dayTotal = totalsPerDay[i];
          const rects = series.map((ser) => {
            const v = ser.values[i];
            if (v <= 0) return null;
            const h = (v / niceMax) * plotH;
            const y = yCursor - h;
            yCursor -= h;
            return (
              <rect
                key={ser.type}
                x={x}
                y={y}
                width={barWidth}
                height={Math.max(h, 0)}
                fill={ACTION_COLORS[ser.type] || 'var(--primary)'}
                style={{ cursor: 'pointer' }}
              >
                <title>{`${actionLabel(ser.type)} — ${dayLabel(d)} : ${v}`}</title>
              </rect>
            );
          });
          return (
            <g key={d}>
              {rects}
              {dayTotal > 0 && (
                <text x={x + barWidth / 2} y={marginTop + plotH - (dayTotal / niceMax) * plotH - 6} textAnchor="middle" fontSize="11" fontWeight="700" fill="var(--ink)">
                  {dayTotal}
                </text>
              )}
              <text x={x + barWidth / 2} y={height - 6} textAnchor="middle" fontSize="10.5" fill="var(--slate)">
                {dayLabel(d)}
              </text>
            </g>
          );
        })}
      </svg>
      {hasData && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '7px 18px', marginTop: 14 }}>
          {series.map((ser) => (
            <div key={ser.type} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12.5 }}>
              <span style={{ width: 8, height: 8, borderRadius: 2, background: ACTION_COLORS[ser.type] || 'var(--primary)', flexShrink: 0 }} />
              <span style={{ color: 'var(--slate)' }}>{actionLabel(ser.type)}</span>
              <span style={{ fontWeight: 700, color: 'var(--ink)' }}>{ser.values.reduce((a, b) => a + b, 0)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Gauge({ value, size = 116, thickness = 14, tone = 'success' }) {
  const color = { success: 'var(--success)', amber: 'var(--amber)', error: 'var(--error)', primary: 'var(--primary)' }[tone];
  const r = (size - thickness) / 2;
  const c = size / 2;
  const frac = Math.max(0, Math.min(1, (value ?? 0) / 100));
  const circumference = Math.PI * r;
  const dash = frac * circumference;

  const polarToXY = (angleDeg) => {
    const a = (angleDeg * Math.PI) / 180;
    return [c + r * Math.cos(a), c + r * Math.sin(a)];
  };
  const [x1, y1] = polarToXY(180);
  const [x2, y2] = polarToXY(0);

  return (
    <svg width={size} height={size / 2 + 12} viewBox={`0 0 ${size} ${size / 2 + 12}`}>
      <path d={`M${x1},${y1} A${r},${r} 0 0 1 ${x2},${y2}`} fill="none" stroke="var(--line)" strokeWidth={thickness} strokeLinecap="round" />
      <path
        d={`M${x1},${y1} A${r},${r} 0 0 1 ${x2},${y2}`}
        fill="none"
        stroke={color}
        strokeWidth={thickness}
        strokeLinecap="round"
        strokeDasharray={`${dash} ${circumference - dash}`}
        style={{ transition: 'stroke-dasharray 0.6s ease' }}
      />
      <text x={c} y={size / 2 - 2} textAnchor="middle" fontSize="24" fontWeight="800" fontFamily="var(--font-display)" fill="var(--ink)">
        {value === null ? '—' : `${value}%`}
      </text>
    </svg>
  );
}

function MiniBarChart({ buckets, height = 90 }) {
  const max = Math.max(...buckets.map((b) => b.value), 1);
  return (
    <div style={{ display: 'flex', alignItems: 'flex-end', gap: 14, height, marginTop: 8 }}>
      {buckets.map((b) => (
        <div key={b.label} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', flex: 1, height: '100%', justifyContent: 'flex-end' }}>
          <span style={{ fontSize: 12.5, fontWeight: 700, color: 'var(--ink)', marginBottom: 4 }}>{b.value}</span>
          <div style={{
            width: '100%',
            maxWidth: 46,
            height: `${Math.max((b.value / max) * (height - 34), b.value > 0 ? 6 : 2)}px`,
            background: b.color,
            borderRadius: '6px 6px 3px 3px',
            transition: 'height 0.6s ease',
          }} />
          <span style={{ fontSize: 10.5, color: 'var(--slate)', marginTop: 6, textAlign: 'center' }}>{b.label}</span>
        </div>
      ))}
    </div>
  );
}


function HeroStatCard({ label, value, Icon, tone = 'primary', onClick, expanded, hint, urgent }) {
  const palette = {
    primary: { bg: 'var(--primary-tint)', color: 'var(--primary-deep)' },
    success: { bg: 'var(--success-tint)', color: 'var(--success)' },
    amber:   { bg: 'var(--amber-tint)', color: 'var(--amber)' },
    error:   { bg: 'var(--error-tint)', color: 'var(--error)' },
  }[tone];
  return (
    <div
      className="card"
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
      onClick={onClick}
      style={{
        flex: '1 1 200px', minWidth: 180, display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start',
        cursor: onClick ? 'pointer' : 'default',
        background: urgent ? 'var(--error-tint)' : undefined,
        border: urgent ? '1.5px solid var(--error)' : expanded ? '1px solid var(--primary)' : undefined,
        boxShadow: urgent ? '0 0 0 4px rgba(193, 80, 61, 0.10)' : undefined,
      }}
    >
      <div>
        <p style={{ fontSize: 12, color: urgent ? 'var(--error)' : 'var(--slate)', margin: 0, fontWeight: urgent ? 700 : 400, display: 'flex', alignItems: 'center', gap: 6 }}>
          {urgent && <span style={{ display: 'inline-block', width: 7, height: 7, borderRadius: '50%', background: 'var(--error)' }} />}
          {label}
        </p>
        <p style={{ fontFamily: 'var(--font-display)', fontSize: 30, fontWeight: 800, margin: '8px 0 0', color: urgent ? 'var(--error)' : 'var(--ink)' }}>
          {value}
        </p>
        {hint && <p className="hint" style={{ marginTop: 6, color: urgent ? 'var(--error)' : undefined }}>{hint}</p>}
      </div>
      <div className="icon" style={{ width: 40, height: 40, borderRadius: 12, background: palette.bg, color: palette.color }}>
        <Icon size={18} />
      </div>
    </div>
  );
}

export default function OverviewTab({ onNavigateToLogs, onNavigateToUser, onNavigateToUsersFilter }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [showActiveBreakdown, setShowActiveBreakdown] = useState(false);
  const [notifyingDormant, setNotifyingDormant] = useState(false);
  const [notifyResult, setNotifyResult] = useState(null);
  const [retryingEmails, setRetryingEmails] = useState(false);
  const [retryResult, setRetryResult] = useState(null);
  const [notifyingMfa, setNotifyingMfa] = useState(false);
  const [mfaResult, setMfaResult] = useState(null);

  const fetchOverview = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const { data } = await client.get('/admin/overview');
      setData(data);
    } catch (err) {
      setError(err.response?.data?.error || "Erreur de chargement de la vue d'ensemble.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchOverview();
    const interval = setInterval(fetchOverview, 60000); 
    return () => clearInterval(interval);
  }, [fetchOverview]);

  const handleRetryFailedEmails = useCallback(async () => {
    setRetryingEmails(true);
    setRetryResult(null);
    try {
      const { data: result } = await client.post('/admin/users/retry-failed-emails');
      if (result.sent === 0 && result.failed === 0) {
        setRetryResult('Aucun email en échec à renvoyer.');
      } else if (result.failed > 0) {
        setRetryResult(`${result.sent} email(s) renvoyé(s), ${result.failed} échec(s) à nouveau.`);
      } else {
        setRetryResult(`${result.sent} email(s) renvoyé(s) avec succès.`);
      }
      fetchOverview();
    } catch (err) {
      setRetryResult(err.response?.data?.error || "Erreur lors du renvoi des emails.");
    } finally {
      setRetryingEmails(false);
    }
  }, [fetchOverview]);

  const handleNotifyMfa = useCallback(async () => {
    setNotifyingMfa(true);
    setMfaResult(null);
    try {
      const { data: result } = await client.post('/admin/users/notify-mfa-setup');
      if (result.sent === 0 && result.failed === 0) {
        setMfaResult('Tous les comptes actifs ont déjà la 2FA activée.');
      } else if (result.failed > 0) {
        setMfaResult(`${result.sent} guide(s) envoyé(s), ${result.failed} échec(s).`);
      } else {
        setMfaResult(`Guide 2FA envoyé à ${result.sent} compte(s).`);
      }
    } catch (err) {
      setMfaResult(err.response?.data?.error || "Erreur lors de l'envoi du guide.");
    } finally {
      setNotifyingMfa(false);
    }
  }, []);

  const handleNotifyDormant = useCallback(async () => {
    setNotifyingDormant(true);
    setNotifyResult(null);
    try {
      const { data: result } = await client.post('/admin/users/notify-dormant');
      setNotifyResult(
        result.failed > 0
          ? `${result.sent} email(s) envoyé(s), ${result.failed} échec(s).`
          : `${result.sent} email(s) de rappel envoyé(s).`
      );
    } catch (err) {
      setNotifyResult(err.response?.data?.error || "Erreur lors de l'envoi des rappels.");
    } finally {
      setNotifyingDormant(false);
    }
  }, []);

  if (loading && !data) {
    return <div className="card"><p className="subtitle">Chargement de la vue d'ensemble...</p></div>;
  }
  if (error) {
    return <div className="card"><p className="error">{error}</p></div>;
  }
  if (!data) return null;

  const lockedCount = data.lockedNow ?? 0;
  const activeAccounts = data.totalUsers - data.inactiveAccounts;
  const mfaRate = data.mfaAdoption && data.mfaAdoption.total > 0
    ? Math.round((data.mfaAdoption.enabled / data.mfaAdoption.total) * 100)
    : null;
  const emailTrendPoints = (data.emailHealth?.dailyTrend || []).map((d) => d.rate ?? 0);
  const tb = data.tempPasswordAgeBuckets || { h0_12: 0, h12_24: 0, h24_48: 0, expired: 0 };
  const neverLoggedInActive = data.neverLoggedInActive ?? data.neverLoggedIn;
  const sb = data.accountStatusBreakdown || {
    active: activeAccounts - neverLoggedInActive, neverLoggedIn: neverLoggedInActive,
    deactivated: data.inactiveAccounts, locked: data.lockedNow,
  };

  const roleSegments = Object.entries(data.roleCounts)
    .map(([key, value]) => ({ label: ROLE_LABELS[key] || key, value, color: ROLE_COLORS[key] || 'var(--primary)' }));

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 22 }}>
      {/* ---------- Section : vue rapide ---------- */}
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
        <HeroStatCard label="Utilisateurs Totaux" value={data.totalUsers} Icon={IconUsers} tone="primary" />
        <HeroStatCard
          label="Comptes Actifs"
          value={activeAccounts}
          Icon={IconCheckCircle}
          tone="success"
          expanded={showActiveBreakdown}
          hint="Cliquez pour le détail →"
          onClick={() => setShowActiveBreakdown((v) => !v)}
        />
        <HeroStatCard label="Comptes Désactivés" value={data.inactiveAccounts} Icon={IconLock} tone={data.inactiveAccounts > 0 ? 'amber' : 'primary'} />
        <HeroStatCard
          label="Comptes Verrouillés"
          value={lockedCount}
          Icon={IconLock}
          tone={lockedCount > 0 ? 'error' : 'primary'}
          urgent={lockedCount > 0}
          hint={lockedCount > 0 ? 'Cliquez pour le détail →' : undefined}
          onClick={lockedCount > 0 ? () => onNavigateToUsersFilter?.('locked') : undefined}
        />
      </div>

      {/* Détail des comptes actifs, affiché au clic sur la carte "Comptes Actifs" */}
      {showActiveBreakdown && (
        <div className="card" style={{ borderLeft: '3px solid var(--primary)' }}>
          <CardTitle hint="Parmi les comptes actifs.">Détail des comptes actifs</CardTitle>
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginTop: 12 }}>
            <button
              type="button"
              className="secondary"
              style={{ flex: '1 1 200px', textAlign: 'left', padding: '10px 14px' }}
              onClick={() => onNavigateToUsersFilter?.('connected')}
            >
              <span style={{ display: 'block', fontSize: 12, color: 'var(--slate)' }}>Déjà connectés</span>
              <span style={{ fontSize: 20, fontWeight: 800, color: 'var(--ink)' }}>{sb.active}</span>
            </button>
            <button
              type="button"
              className="secondary"
              style={{ flex: '1 1 200px', textAlign: 'left', padding: '10px 14px' }}
              onClick={() => onNavigateToUsersFilter?.('neverLoggedIn')}
            >
              <span style={{ display: 'block', fontSize: 12, color: 'var(--slate)' }}>Jamais connectés</span>
              <span style={{ fontSize: 20, fontWeight: 800, color: 'var(--ink)' }}>{sb.neverLoggedIn}</span>
            </button>
          </div>
        </div>
      )}

      {/* ---------- Section : population & statut des comptes ---------- */}
      <SectionHeading Icon={IconUsers} title="Population des comptes" subtitle="Qui a accès à la plateforme, et dans quel état" />
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
        <DonutCard title="Répartition des rôles" segments={roleSegments} centerLabel="utilisateurs" />
      </div>

      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
        <div
          className="card"
          style={{
            flex: '1 1 260px',
            borderLeft: data.dormantAccounts > 0 ? '3px solid var(--amber)' : '3px solid transparent',
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
            <div>
              <p style={{ fontSize: 12, color: 'var(--slate)', margin: 0 }}>Comptes actifs mais dormants (&gt; 60j)</p>
              <p style={{ fontFamily: 'var(--font-display)', fontSize: 30, fontWeight: 800, margin: '8px 0 0', color: 'var(--ink)' }}>
                {data.dormantAccounts}
              </p>
            </div>
            <div className="icon" style={{
              width: 40, height: 40, borderRadius: 12,
              background: data.dormantAccounts > 0 ? 'var(--amber-tint)' : 'var(--primary-tint)',
              color: data.dormantAccounts > 0 ? 'var(--amber)' : 'var(--primary-deep)',
            }}>
              <IconEyeOff size={18} />
            </div>
          </div>
          <p className="hint" style={{ marginTop: 10 }}>
            Comptes déjà connectés au moins une fois, mais sans connexion depuis plus de 60 jours — accès valide non surveillé.
          </p>
          <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
              <button
                type="button"
                className="secondary"
                style={{ width: 'auto', padding: '7px 14px', fontSize: 12.5 }}
                onClick={() => onNavigateToUsersFilter?.('dormant')}
              >
                Revue groupée (onglet Utilisateurs) →
              </button>
              <button
                type="button"
                className="secondary"
                style={{ width: 'auto', padding: '7px 14px', fontSize: 12.5 }}
                disabled={notifyingDormant || data.dormantAccounts === 0}
                onClick={handleNotifyDormant}
              >
                {notifyingDormant ? 'Envoi en cours...' : 'Envoyer un rappel par email →'}
              </button>
            </div>
            {notifyResult && (
              <p className="hint" style={{ marginTop: 8 }}>{notifyResult}</p>
            )}
        </div>

        <div className="card" style={{ flex: '1 1 260px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 20 }}>
            <Gauge value={mfaRate} tone={mfaRate === null ? 'primary' : mfaRate >= 80 ? 'success' : mfaRate >= 50 ? 'amber' : 'error'} />
            <div>
              <h2 style={{ margin: 0 }}>Adoption de la 2FA</h2>
              <p className="hint" style={{ marginTop: 6 }}>
                {data.mfaAdoption ? `${data.mfaAdoption.enabled} / ${data.mfaAdoption.total} comptes protégés` : '—'}
              </p>
            </div>
          </div>
          <button
            type="button"
            className="secondary"
            style={{ marginTop: 14, width: 'auto', padding: '7px 14px', fontSize: 12.5 }}
            disabled={notifyingMfa || (data.mfaAdoption && data.mfaAdoption.enabled === data.mfaAdoption.total)}
            onClick={handleNotifyMfa}
          >
            {notifyingMfa ? 'Envoi en cours...' : 'Envoyer le guide 2FA par email →'}
          </button>
          {mfaResult && (
            <p className="hint" style={{ marginTop: 8 }}>{mfaResult}</p>
          )}
        </div>
      </div>

      {/* ---------- Section : emails & mots de passe temporaires ---------- */}
      <SectionHeading Icon={IconShield} title="Emails & mots de passe temporaires" subtitle="Points de friction fréquents à l'onboarding" />
      <div className="card">
        <CardTitle>Statut du service email</CardTitle>
        <div style={{ display: 'flex', gap: 20, alignItems: 'center', marginTop: 14, flexWrap: 'wrap' }}>
          <Gauge
            value={data.emailHealth?.rate24h ?? null}
            tone={data.emailHealth?.rate24h === null ? 'primary' : data.emailHealth.rate24h >= 90 ? 'success' : data.emailHealth.rate24h >= 70 ? 'amber' : 'error'}
          />
          <div style={{ flex: '1 1 200px', minWidth: 180 }}>
            <p className="hint" style={{ margin: 0 }}>
              Taux de succès des envois (24h){data.emailHealth?.total24h ? ` — ${data.emailHealth.total24h} envoi(s)` : ''}
            </p>
            {data.emailStatus && (
              <p className="hint" style={{ marginTop: 6 }}>
                Dernier : <span className={`badge ${data.emailStatus.success ? 'badge-success' : 'badge-error'}`} style={{ marginLeft: 4 }}>
                  {data.emailStatus.success ? 'Réussi' : 'Échoué'}
                </span>
                <span style={{ marginLeft: 8 }}>{actionLabel(data.emailStatus.action)} — {timeAgo(data.emailStatus.at)}</span>
              </p>
            )}
          </div>
          {emailTrendPoints.length > 1 && (
            <div style={{ flex: '1 1 180px', minWidth: 160 }}>
              <p style={{ fontSize: 11, color: 'var(--slate)', margin: '0 0 4px' }}>Tendance 7 jours</p>
              <Sparkline points={emailTrendPoints} color="var(--primary)" />
            </div>
          )}
        </div>
        <button
          type="button"
          className="secondary"
          style={{ marginTop: 14, width: 'auto', padding: '7px 14px', fontSize: 12.5 }}
          onClick={() => onNavigateToLogs?.({ emailFailed: true })}
        >
          Voir les emails échoués →
        </button>
        <button
          type="button"
          className="secondary"
          style={{ marginTop: 14, marginLeft: 8, width: 'auto', padding: '7px 14px', fontSize: 12.5 }}
          disabled={retryingEmails}
          onClick={handleRetryFailedEmails}
        >
          {retryingEmails ? 'Renvoi en cours...' : 'Renvoyer les emails échoués →'}
        </button>
        {retryResult && (
          <p className="hint" style={{ marginTop: 8 }}>{retryResult}</p>
        )}
      </div>

      <div className="card">
        <CardTitle hint="Le mot de passe temporaire est valable 48h : au-delà, la première connexion échouera silencieusement.">
          Mots de passe temporaires en attente
        </CardTitle>
        <MiniBarChart
          buckets={[
            { label: '0–12h', value: tb.h0_12, color: 'var(--success)' },
            { label: '12–24h', value: tb.h12_24, color: 'var(--primary)' },
            { label: '24–48h', value: tb.h24_48, color: 'var(--amber)' },
            { label: 'Expiré', value: tb.expired, color: 'var(--error)' },
          ]}
        />
        {tb.expired > 0 && (
          <p className="hint" style={{ marginTop: 10 }}>
            {tb.expired} compte(s) ont probablement un mot de passe temporaire expiré.
          </p>
        )}
        {(tb.h0_12 + tb.h12_24 + tb.h24_48 + tb.expired) > 0 && (
          <button
            type="button"
            className="secondary"
            style={{ marginTop: 12, width: 'auto', padding: '7px 14px', fontSize: 12.5 }}
            onClick={() => onNavigateToUsersFilter?.('tempPassword')}
          >
            Revue groupée →
          </button>
        )}
      </div>

      {/* ---------- Section : activité admin ---------- */}
      <SectionHeading Icon={IconChart} title="Activité administrative" subtitle="Ce que les admins ont fait cette semaine, jour par jour" />
      <div className="card">
        <CardTitle>Historique des actions admin (7 derniers jours)</CardTitle>
        <div style={{ marginTop: 14 }}>
          <DailyStackedBarChart days={data.actionHistory7d?.days || []} series={data.actionHistory7d?.series || []} />
        </div>
        <button className="secondary" style={{ marginTop: 16 }} onClick={() => onNavigateToLogs?.({})}>
          Voir tous les logs →
        </button>
      </div>
    </div>
  );
}