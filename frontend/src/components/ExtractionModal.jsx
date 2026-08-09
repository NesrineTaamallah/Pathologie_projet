import { useEffect, useState } from 'react';
import client from '../api/client';
import { CHAMPS } from './ExtractionCoordonneesPanel';
import { IconRefresh, IconCheckCircle, IconAlert, IconX } from './Icons';

const TYPE_DOCUMENT_LABELS = {
  visite: 'Visite',
  admission: 'Admission',
  prelevement_sang: 'Prélèvement sanguin',
  eeg: 'EEG',
  emg: 'EMG',
  irm: 'IRM',
  autre: 'Autre',
};

function fmtDate(d) {
  if (!d) return '—';
  const date = new Date(d);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleDateString('fr-FR');
}

const STATUT_STYLE = {
  nouveau:    { label: 'Nouveau',                       bg: '#e6f4ea', fg: '#1e7e34', border: '#a6d9b3' },
  confirme:   { label: 'Confirmé',                      bg: '#eef2ff', fg: '#3949ab', border: '#c5cef5' },
  conflit:    { label: '⚠ Diffère de la base',           bg: '#fdecea', fg: '#b3261e', border: '#f3b8b3' },
  existant:   { label: 'Déjà en base (non mentionné ici)', bg: '#f1f3f4', fg: '#5f6368', border: '#dcdde0' },
  verrouille: { label: '🔒 Verrouillé (base conservée)',  bg: '#f1f3f4', fg: '#5f6368', border: '#dcdde0' },
  vide:       null,
};

// Alignés avec le backend : ces champs restent éditables même s'ils existent déjà.
const CHAMPS_TOUJOURS_MODIFIABLES = ['telephone', 'adresse', 'num_cnam', 'cin'];


export default function ExtractionModal({ pseudonyme, onClose, onAllDone }) {
  const [documents, setDocuments] = useState(null); 
  const [loadError, setLoadError] = useState('');
  const [extracting, setExtracting] = useState(false);
  const [results, setResults] = useState({}); 
  const [errors, setErrors] = useState({}); 
  const [saving, setSaving] = useState({}); 
  const [saved, setSaved] = useState({}); 
  const [pseudonymeResolu, setPseudonymeResolu] = useState(pseudonyme);

  useEffect(() => {
    client.get(`/api/dossiers/${pseudonyme}/documents-non-extraits`)
      .then((res) => setDocuments(res.data.documents || []))
      .catch(() => setLoadError('Impossible de charger les documents de ce patient.'));
  }, [pseudonyme]);

  async function extraireTout() {
    if (!documents || documents.length === 0) return;
    setExtracting(true);
    setErrors({});
    
    for (const doc of documents) {
      if (results[doc.id]) continue; 
      try {
        const res = await client.post('/api/extraction/patient', { document_id: doc.id });
        const { pseudonyme: pseudonymeRenvoye, document_id: _d, ...rest } = res.data;
        if (pseudonymeRenvoye) setPseudonymeResolu(pseudonymeRenvoye);
        setResults((r) => ({ ...r, [doc.id]: rest }));
      } catch (err) {
        setErrors((e) => ({ ...e, [doc.id]: err.response?.data?.error || "Échec de l'extraction." }));
      }
    }
    setExtracting(false);
  }

  function updateField(docId, key, value) {
    setResults((r) => ({ ...r, [docId]: { ...r[docId], [key]: value } }));
    setSaved((s) => ({ ...s, [docId]: false }));
  }

  async function valider(doc) {
    if (!pseudonymeResolu) {
      setErrors((e) => ({ ...e, [doc.id]: 'Aucun dossier cible pour enregistrer ces coordonnées.' }));
      return;
    }
    setSaving((s) => ({ ...s, [doc.id]: true }));
    setErrors((e) => ({ ...e, [doc.id]: '' }));
    try {
      const { _statuts, _valeurs_existantes, ...valeurs } = results[doc.id];
      await client.post('/api/coordonnees', {
        pseudonyme: pseudonymeResolu,
        document_id: doc.id,
        ...valeurs,
      });
      setSaved((s) => ({ ...s, [doc.id]: true }));
      setDocuments((docs) => {
        const reste = docs.filter((d) => d.id !== doc.id);
        if (reste.length === 0) onAllDone?.();
        return reste;
      });
    } catch (err) {
      setErrors((e) => ({ ...e, [doc.id]: err.response?.data?.error || "Échec de l'enregistrement." }));
    } finally {
      setSaving((s) => ({ ...s, [doc.id]: false }));
    }
  }

  const toutExtrait = documents && documents.length > 0 && documents.every((d) => results[d.id]);

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, background: 'rgba(18,42,48,.55)', backdropFilter: 'blur(2px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 50, padding: 20,
        overflowY: 'auto',
      }}
    >
      <div onClick={(e) => e.stopPropagation()} style={{
        background: 'var(--card)', borderRadius: 16, width: 720, maxWidth: '100%', maxHeight: '86vh',
        minHeight: 0, overflowY: 'auto', overscrollBehavior: 'contain', WebkitOverflowScrolling: 'touch',
        padding: '22px 24px 24px', boxShadow: '0 20px 50px -10px rgba(18,42,48,.35)',
        display: 'flex', flexDirection: 'column', gap: 14,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, flexShrink: 0 }}>
          <div>
            <h3 style={{ margin: 0, fontSize: 16, fontFamily: 'var(--font-display)' }}>Extraire les données</h3>
            <p style={{ margin: '4px 0 0', fontSize: 12, color: 'var(--slate)', fontFamily: 'var(--font-mono)' }}>{pseudonyme}</p>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <button
              type="button"
              onClick={extraireTout}
              disabled={extracting || !documents || documents.length === 0 || toutExtrait}
              style={{
                width: 'auto', margin: 0, padding: '8px 14px', display: 'inline-flex', alignItems: 'center', gap: 7,
                borderRadius: 9, border: '1.5px solid var(--teal)',
                background: toutExtrait ? 'var(--paper)' : 'var(--teal)',
                color: toutExtrait ? 'var(--slate)' : '#fff',
                fontWeight: 600, fontSize: 12.5,
                opacity: (extracting || !documents || documents.length === 0) ? 0.7 : 1,
              }}
            >
              <IconRefresh size={13} />
              {extracting ? 'Extraction en cours…' : toutExtrait ? 'Tout extrait' : 'Extraire'}
            </button>
            <button
              type="button"
              onClick={onClose}
              title="Fermer"
              style={{ width: 'auto', margin: 0, padding: 6, background: 'transparent', border: 'none', boxShadow: 'none', color: 'var(--slate-soft)' }}
            >
              <IconX size={16} />
            </button>
          </div>
        </div>

        {loadError && <p className="error" style={{ margin: 0, fontSize: 12, flexShrink: 0 }}>{loadError}</p>}
        {documents === null && !loadError && (
          <p className="hint" style={{ margin: 0, fontSize: 12.5, flexShrink: 0 }}>Chargement des documents…</p>
        )}
        {documents && documents.length === 0 && (
          <p className="hint" style={{ margin: 0, fontSize: 12.5, flexShrink: 0 }}>
            Tous les documents de ce patient ont déjà été extraits.
          </p>
        )}

        {documents && documents.map((doc) => (
          <div key={doc.id} style={{
            borderRadius: 12, border: '1.5px solid var(--line)', background: 'var(--paper)', overflow: 'hidden',
            flexShrink: 0,
          }}>
            <div style={{ padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 8, flexShrink: 0 }}>
              <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 10 }}>
                <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--ink)' }}>
                  {TYPE_DOCUMENT_LABELS[doc.type_document] || doc.type_document}
                  <span style={{ fontWeight: 400, color: 'var(--slate-soft)' }}> · {doc.type_entree === 'audio' ? 'Audio' : 'Scan'}</span>
                </span>
                <span style={{ fontSize: 11, color: 'var(--slate-soft)' }}>Ajouté le {fmtDate(doc.created_at)}</span>
              </div>

              {doc.texte_transcrit && (
                <p style={{
                  margin: 0, fontSize: 12.5, lineHeight: 1.6, color: 'var(--slate)',
                  background: 'var(--card)', border: '1px solid var(--line)', borderRadius: 8, padding: 10,
                  maxHeight: 160, minHeight: 0, flexShrink: 0, overflowY: 'auto', overscrollBehavior: 'contain', whiteSpace: 'pre-wrap',
                }}>
                  {doc.texte_transcrit}
                </p>
              )}

              {errors[doc.id] && (
                <p className="error" style={{ margin: 0, fontSize: 11.5, display: 'flex', alignItems: 'center', gap: 5 }}>
                  <IconAlert size={12} /> {errors[doc.id]}
                </p>
              )}
            </div>

            {results[doc.id] && (
              <div style={{ borderTop: '1px solid var(--line)', padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 10, background: 'var(--card)' }}>
                <p style={{ margin: 0, fontSize: 11, fontWeight: 700, color: 'var(--slate)', textTransform: 'uppercase', letterSpacing: 0.3 }}>
                  Données extraites — à vérifier
                </p>

                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                  {CHAMPS.map(({ key, label: champLabel, multi }) => {
                    const statut = results[doc.id]._statuts?.[key];
                    const style = STATUT_STYLE[statut];
                    const ancienneValeur = results[doc.id]._valeurs_existantes?.[key];
                    const valeur = results[doc.id][key] || '';
                    const verrouille = !CHAMPS_TOUJOURS_MODIFIABLES.includes(key) && statut === 'verrouille';
                    return (
                      <div key={key} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                        <label style={{ fontSize: 11, fontWeight: 600, color: 'var(--slate)', display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                          {champLabel}
                          {multi && <span style={{ fontWeight: 400, color: 'var(--slate-soft)' }}> (virgules)</span>}
                          {style && (
                            <span style={{
                              fontSize: 9.5, fontWeight: 700, padding: '1px 6px', borderRadius: 20,
                              background: style.bg, color: style.fg, border: `1px solid ${style.border}`,
                              textTransform: 'none', letterSpacing: 0,
                            }}>
                              {style.label}
                            </span>
                          )}
                          {verrouille && (
                            <span title="Non modifiable" style={{ fontSize: 11, color: 'var(--slate-soft)' }}>🔒</span>
                          )}
                        </label>
                        <input
                          value={valeur}
                          onChange={(e) => !verrouille && updateField(doc.id, key, e.target.value)}
                          readOnly={verrouille}
                          placeholder="—"
                          style={{
                            padding: '7px 9px', borderRadius: 8,
                            border: `1.5px solid ${statut === 'conflit' ? '#f3b8b3' : 'var(--line)'}`,
                            fontSize: 12, boxSizing: 'border-box',
                            background: verrouille ? 'var(--line)' : 'var(--paper)',
                            color: verrouille ? 'var(--slate)' : 'inherit',
                            cursor: verrouille ? 'not-allowed' : 'text',
                          }}
                        />
                        {statut === 'conflit' && ancienneValeur && (
                          <span style={{ fontSize: 10.5, color: '#b3261e' }}>
                            Valeur en base : {ancienneValeur} — vérifier manuellement avant correction
                          </span>
                        )}
                      </div>
                    );
                  })}
                </div>

                <div style={{ display: 'flex', gap: 8, marginTop: 2 }}>
                  <button
                    type="button"
                    onClick={() => valider(doc)}
                    disabled={saving[doc.id]}
                    style={{
                      width: 'auto', margin: 0, padding: '7px 13px', display: 'inline-flex', alignItems: 'center', gap: 6,
                      borderRadius: 8, border: 'none', background: 'var(--teal)', color: '#fff',
                      fontWeight: 600, fontSize: 12, opacity: saving[doc.id] ? 0.7 : 1,
                    }}
                  >
                    {saved[doc.id] ? <><IconCheckCircle size={12} /> Enregistré</> : saving[doc.id] ? 'Enregistrement…' : 'Valider'}
                  </button>
                </div>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}