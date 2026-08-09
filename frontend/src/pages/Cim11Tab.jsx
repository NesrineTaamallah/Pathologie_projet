import { useState, useEffect, useRef, useCallback } from 'react';
import client from '../api/client';
import { IconWave } from '../components/Icons';

const CLASS_LABELS = {
  chapter: 'Chapitre',
  block: 'Regroupement',
  category: 'Catégorie',
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
  const [selection, setSelection] = useState(null);
  const [historique, setHistorique] = useState([]);
  const [feedback, setFeedback] = useState(null);
  const debouncedQuery = useDebouncedValue(query, 300);
  const abortRef = useRef(null);

  const rechercher = useCallback(async (q) => {
    if (!q || q.trim().length < 2) {
      setResultats([]);
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
        setErreur("Erreur lors de la recherche CIM-11.");
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    rechercher(debouncedQuery);
  }, [debouncedQuery, rechercher]);

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
      setFeedback({ type: 'error', text: 'Renseignez le pseudonyme du patient avant d\u2019associer un code.' });
      return;
    }
    try {
      await client.post('/api/cim11/associer', {
        pseudonyme: pseudonyme.trim(),
        terme_source: query,
        cim11_id: item.id,
      });
      setFeedback({ type: 'success', text: `Code ${item.code || '—'} (${item.title}) associé à ${pseudonyme}.` });
      chargerHistorique(pseudonyme.trim());
    } catch {
      setFeedback({ type: 'error', text: "Impossible d'associer ce code." });
    }
  }

  return (
    <div style={{ maxWidth: 920, margin: '0 auto', padding: '24px 8px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 4 }}>
        <IconWave size={22} />
        <h2 style={{ fontFamily: 'var(--font-display)', fontSize: 22, margin: 0 }}>CIM-11</h2>
      </div>
      <p style={{ color: 'var(--muted, #64748b)', marginTop: 4, marginBottom: 20, fontSize: 14 }}>
        Recherchez un diagnostic (nom, code, ou description approximative). La recherche tolère
        les fautes de frappe, les accents manquants et l'ordre des mots (ex. « epilsie »,
        « épilepsie focal », « focale epilepsie »).
      </p>

      <div style={{ display: 'flex', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
        <input
          type="text"
          placeholder="Pseudonyme patient (ex: SEP_MJ_001)"
          value={pseudonyme}
          onChange={(e) => setPseudonyme(e.target.value)}
          onBlur={() => chargerHistorique(pseudonyme.trim())}
          style={{
            flex: '1 1 220px', padding: '10px 12px', borderRadius: 10,
            border: '1px solid var(--border, #dbe3ee)', fontSize: 14,
          }}
        />
      </div>

      <div style={{ position: 'relative', marginBottom: 18 }}>
        <input
          autoFocus
          type="text"
          placeholder="Rechercher un diagnostic CIM-11… (ex: epilepsie, sclerose en plaque, 8A61)"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          style={{
            width: '100%', padding: '14px 16px', borderRadius: 12,
            border: '1px solid var(--border, #dbe3ee)', fontSize: 16,
            boxShadow: '0 1px 3px rgba(15,30,60,0.06)',
          }}
        />
        {loading && (
          <span style={{ position: 'absolute', right: 16, top: 16, fontSize: 12, color: '#94a3b8' }}>
            Recherche…
          </span>
        )}
      </div>

      {feedback && (
        <div
          style={{
            padding: '10px 14px', borderRadius: 10, marginBottom: 14, fontSize: 13.5,
            background: feedback.type === 'success' ? '#ecfdf5' : '#fef2f2',
            color: feedback.type === 'success' ? '#047857' : '#b91c1c',
            border: `1px solid ${feedback.type === 'success' ? '#a7f3d0' : '#fecaca'}`,
          }}
        >
          {feedback.text}
        </div>
      )}

      {erreur && <p style={{ color: '#b91c1c', fontSize: 13.5 }}>{erreur}</p>}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {resultats.length === 0 && !loading && query.trim().length >= 2 && (
          <p style={{ color: '#94a3b8', fontSize: 14 }}>Aucun résultat pour « {query} ».</p>
        )}
        {resultats.map((item) => (
          <div
            key={item.id}
            style={{
              display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              padding: '12px 16px', borderRadius: 12, border: '1px solid var(--border, #e5eaf1)',
              background: '#fff',
            }}
          >
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                {item.code && (
                  <span style={{
                    fontFamily: 'monospace', fontSize: 12.5, fontWeight: 700,
                    background: '#eef4ff', color: '#1d4ed8', padding: '2px 8px', borderRadius: 6,
                  }}>
                    {item.code}
                  </span>
                )}
                <span style={{
                  fontSize: 11, color: '#64748b', textTransform: 'uppercase', letterSpacing: 0.4,
                }}>
                  {CLASS_LABELS[item.class_kind] || item.class_kind}
                </span>
              </div>
              <p style={{ margin: '4px 0 0', fontSize: 15, fontWeight: 600 }}>{item.title}</p>
              {item.definition && (
                <p style={{
                  margin: '4px 0 0', fontSize: 13, color: '#64748b', maxWidth: 640,
                  overflow: 'hidden', textOverflow: 'ellipsis', display: '-webkit-box',
                  WebkitLineClamp: 2, WebkitBoxOrient: 'vertical',
                }}>
                  {item.definition}
                </p>
              )}
            </div>
            <button
              onClick={() => associer(item)}
              style={{
                flexShrink: 0, padding: '8px 14px', borderRadius: 8, border: 'none',
                background: 'var(--accent, #1d4ed8)', color: '#fff', fontSize: 13, fontWeight: 600,
                cursor: 'pointer',
              }}
            >
              Associer
            </button>
          </div>
        ))}
      </div>

      {pseudonyme.trim() && historique.length > 0 && (
        <div style={{ marginTop: 30 }}>
          <h3 style={{ fontSize: 15, marginBottom: 10 }}>
            Codes CIM-11 associés à {pseudonyme.trim()}
          </h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {historique.map((h) => (
              <div key={h.id} style={{
                display: 'flex', gap: 10, alignItems: 'center', fontSize: 13.5,
                padding: '8px 12px', borderRadius: 8, background: '#f8fafc',
              }}>
                <span style={{ fontFamily: 'monospace', fontWeight: 700, color: '#1d4ed8' }}>
                  {h.code_cim11 || '—'}
                </span>
                <span>{h.titre_cim11}</span>
                <span style={{ marginLeft: 'auto', color: '#94a3b8', fontSize: 12 }}>
                  {new Date(h.date_creation).toLocaleDateString('fr-FR')}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
