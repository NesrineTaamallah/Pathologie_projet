import { useState, useEffect, useRef, useCallback } from 'react';
import client from '../api/client';
import { IconWave, IconSearch, IconCheckCircle, IconTarget } from '../components/Icons';

const CLASS_LABELS = {
  chapter: 'Chapitre',
  block: 'Regroupement',
  category: 'Catégorie',
};

const CLASS_TINTS = {
  chapter: { bg: 'var(--amber-tint)', fg: 'var(--amber)' },
  block: { bg: 'var(--slate-lighter)', fg: 'var(--slate)' },
  category: { bg: 'var(--teal-tint)', fg: 'var(--teal-deep)' },
};

function useDebouncedValue(value, delay) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(t);
  }, [value, delay]);
  return debounced;
}

export default function Cim11Tab() {
  const [query, setQuery] = useState('');
  const [pseudonyme, setPseudonyme] = useState('');
  const [resultats, setResultats] = useState([]);
  const [loading, setLoading] = useState(false);
  const [erreur, setErreur] = useState(null);
  const [historique, setHistorique] = useState([]);
  const [feedback, setFeedback] = useState(null);
  const [associatingId, setAssociatingId] = useState(null);
  const debouncedQuery = useDebouncedValue(query, 300);
  const abortRef = useRef(null);

  const rechercher = useCallback(async (q) => {
    if (!q || q.trim().length < 2) {
      setResultats([]);
      setErreur(null);
      return;
    }
    if (abortRef.current) abortRef.current.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    setErreur(null);
    try {
      const { data } = await client.get('/api/cim11/recherche', {
        params: { q, limit: 20 },
        signal: controller.signal,
      });
      setResultats(data.resultats || []);
    } catch (err) {
      if (err.name !== 'CanceledError' && err.code !== 'ERR_CANCELED') {
        setErreur('Erreur lors de la recherche CIM-11.');
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    rechercher(debouncedQuery);
  }, [debouncedQuery, rechercher]);

  useEffect(() => {
    if (!feedback) return;
    const t = setTimeout(() => setFeedback(null), 4500);
    return () => clearTimeout(t);
  }, [feedback]);

  async function chargerHistorique(p) {
    if (!p) { setHistorique([]); return; }
    try {
      const { data } = await client.get(`/api/cim11/patient/${encodeURIComponent(p)}`);
      setHistorique(data.resultats || []);
    } catch {
      setHistorique([]);
    }
  }

  async function associer(item) {
    if (!pseudonyme.trim()) {
      setFeedback({ type: 'error', text: "Renseignez le pseudonyme du patient avant d'associer un code." });
      return;
    }
    setAssociatingId(item.id);
    try {
      await client.post('/api/cim11/associer', {
        pseudonyme: pseudonyme.trim(),
        terme_source: query,
        cim11_id: item.id,
      });
      setFeedback({ type: 'success', text: `Code ${item.code || '—'} associé à ${pseudonyme.trim()}.` });
      chargerHistorique(pseudonyme.trim());
    } catch {
      setFeedback({ type: 'error', text: "Impossible d'associer ce code." });
    } finally {
      setAssociatingId(null);
    }
  }

  return (
    <div>
      <div style={{
        background: 'var(--card)', borderRadius: 14, border: '1px solid var(--line)', padding: 22,
      }}>
        {/* En-tête */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 18, flexWrap: 'wrap', gap: 12 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div style={{
              width: 32, height: 32, borderRadius: 9, background: 'var(--teal-tint)',
              color: 'var(--teal-deep)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
            }}>
              <IconWave size={16} />
            </div>
            <div>
              <h3 style={{ margin: 0, fontSize: 15.5, fontFamily: 'var(--font-display)' }}>CIM-11</h3>
              <p style={{ margin: '2px 0 0', fontSize: 12, color: 'var(--slate-soft)', maxWidth: 480 }}>
                Recherche de diagnostics CIM-11 tolérante aux fautes de frappe, accents manquants
                et ordre des mots.
              </p>
            </div>
          </div>

          <input
            value={pseudonyme}
            onChange={(e) => setPseudonyme(e.target.value)}
            onBlur={() => chargerHistorique(pseudonyme.trim())}
            placeholder="Pseudonyme patient (ex: SEP_MJ_001)"
            autoComplete="off"
            style={{
              padding: '9px 13px', borderRadius: 9, border: '1px solid var(--line)',
              fontSize: 12.5, fontFamily: 'var(--font-body)', color: 'var(--ink)',
              background: 'var(--paper)', minWidth: 220,
            }}
          />
        </div>

        {/* Barre de recherche */}
        <div style={{ position: 'relative', marginBottom: feedback ? 12 : 18 }}>
          <span style={{ position: 'absolute', left: 14, top: '50%', transform: 'translateY(-50%)', color: 'var(--slate-soft)' }}>
            <IconSearch size={15} />
          </span>
          <input
            autoFocus
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Rechercher un diagnostic… (ex: epilepsie, sclerose en plaque, 8A61)"
            autoComplete="off"
            style={{
              width: '100%', padding: '13px 16px 13px 40px', borderRadius: 11,
              border: '1px solid var(--line)', fontSize: 14.5, fontFamily: 'var(--font-body)',
              color: 'var(--ink)', background: 'var(--card)', boxSizing: 'border-box',
            }}
          />
          {loading && (
            <span style={{
              position: 'absolute', right: 16, top: '50%', transform: 'translateY(-50%)',
              fontSize: 11.5, color: 'var(--slate-soft)', fontWeight: 600,
            }}>
              Recherche…
            </span>
          )}
        </div>

        {/* Feedback */}
        {feedback && (
          <div style={{
            display: 'flex', alignItems: 'center', gap: 8, padding: '10px 14px', borderRadius: 10,
            marginBottom: 16, fontSize: 12.5, fontWeight: 600,
            background: feedback.type === 'success' ? 'var(--success-tint)' : 'var(--error-tint)',
            color: feedback.type === 'success' ? 'var(--success)' : 'var(--error)',
          }}>
            {feedback.type === 'success' && <IconCheckCircle size={14} />}
            {feedback.text}
          </div>
        )}

        {erreur && (
          <p style={{ color: 'var(--error)', fontSize: 12.5, marginBottom: 16 }}>{erreur}</p>
        )}

        {/* Résultats */}
        {resultats.length === 0 && !loading && query.trim().length >= 2 && !erreur && (
          <div style={{
            padding: '26px 16px', textAlign: 'center', color: 'var(--slate-soft)', fontSize: 13,
            border: '1px dashed var(--line)', borderRadius: 11,
          }}>
            Aucun résultat pour « {query} ».
          </div>
        )}

        {query.trim().length < 2 && historique.length === 0 && (
          <div style={{
            padding: '30px 16px', textAlign: 'center', color: 'var(--slate-soft)', fontSize: 13,
            border: '1px dashed var(--line)', borderRadius: 11,
          }}>
            Commencez à taper pour rechercher un diagnostic CIM-11.
          </div>
        )}

        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {resultats.map((item) => {
            const tint = CLASS_TINTS[item.class_kind] || { bg: 'var(--slate-lighter)', fg: 'var(--slate)' };
            return (
              <div
                key={item.id}
                style={{
                  display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 16,
                  padding: '14px 16px', borderRadius: 12, border: '1px solid var(--line)',
                  background: 'var(--paper)', flexWrap: 'wrap',
                }}
              >
                <div style={{ flex: '1 1 320px', minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4, flexWrap: 'wrap' }}>
                    {item.code && (
                      <span style={{
                        fontFamily: 'var(--font-mono, monospace)', fontSize: 11.5, fontWeight: 700,
                        background: 'var(--accent-tint)', color: 'var(--accent-deep)',
                        padding: '2px 9px', borderRadius: 999,
                      }}>
                        {item.code}
                      </span>
                    )}
                    <span style={{
                      fontSize: 10.5, fontWeight: 700, color: tint.fg, background: tint.bg,
                      padding: '2px 9px', borderRadius: 999, textTransform: 'uppercase', letterSpacing: 0.3,
                    }}>
                      {CLASS_LABELS[item.class_kind] || item.class_kind}
                    </span>
                  </div>
                  <p style={{ margin: 0, fontSize: 14.5, fontWeight: 700, color: 'var(--ink)' }}>{item.title}</p>
                  {item.definition && (
                    <p style={{
                      margin: '4px 0 0', fontSize: 12.5, color: 'var(--slate)', maxWidth: 620,
                      overflow: 'hidden', textOverflow: 'ellipsis', display: '-webkit-box',
                      WebkitLineClamp: 2, WebkitBoxOrient: 'vertical',
                    }}>
                      {item.definition}
                    </p>
                  )}
                </div>
                <button
                  onClick={() => associer(item)}
                  disabled={associatingId === item.id}
                  style={{
                    flexShrink: 0, padding: '9px 16px', borderRadius: 9, border: 'none',
                    background: 'var(--accent-deep)', color: '#fff', fontSize: 12.5, fontWeight: 700,
                    fontFamily: 'var(--font-body)', cursor: associatingId === item.id ? 'wait' : 'pointer',
                    opacity: associatingId === item.id ? 0.65 : 1,
                  }}
                >
                  {associatingId === item.id ? 'Association…' : 'Associer'}
                </button>
              </div>
            );
          })}
        </div>

        {/* Historique patient */}
        {pseudonyme.trim() && historique.length > 0 && (
          <div style={{ marginTop: 26, paddingTop: 20, borderTop: '1px solid var(--line)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
              <IconTarget size={14} color="var(--slate)" />
              <h4 style={{ margin: 0, fontSize: 13, fontFamily: 'var(--font-display)', color: 'var(--ink)' }}>
                Codes associés à {pseudonyme.trim()}
              </h4>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {historique.map((h) => (
                <div key={h.id} style={{
                  display: 'flex', gap: 10, alignItems: 'center', fontSize: 12.5,
                  padding: '9px 13px', borderRadius: 9, background: 'var(--paper)',
                  border: '1px solid var(--line)',
                }}>
                  <span style={{
                    fontFamily: 'var(--font-mono, monospace)', fontWeight: 700, color: 'var(--accent-deep)',
                    background: 'var(--accent-tint)', padding: '2px 8px', borderRadius: 999, fontSize: 11,
                  }}>
                    {h.code_cim11 || '—'}
                  </span>
                  <span style={{ color: 'var(--ink)' }}>{h.titre_cim11}</span>
                  <span style={{ marginLeft: 'auto', color: 'var(--slate-soft)', fontSize: 11 }}>
                    {new Date(h.date_creation).toLocaleDateString('fr-FR')}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}