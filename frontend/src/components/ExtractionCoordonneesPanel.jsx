import { useState, useEffect } from 'react';
import client from '../api/client';
import { IconRefresh, IconCheckCircle, IconAlert } from './Icons';


export const CHAMPS = [
  { key: 'numero_dossier', label: 'Numéro de dossier', readonly: true },
  { key: 'nom_prenom', label: 'Nom et prénom', readonly: true },
  { key: 'date_naissance', label: 'Date de naissance' },
  { key: 'adresse', label: 'Adresse' },
  { key: 'origine', label: 'Origine', readonly: true },
  { key: 'telephone', label: 'Téléphone' },
  { key: 'cin', label: 'CIN' },
  { key: 'num_cnam', label: 'N° CNAM' },
  { key: 'nom_prenom_pere', label: 'Nom et prénom du père' },
  { key: 'nom_prenom_mere', label: 'Nom et prénom de la mère' },
  { key: 'frere', label: 'Frère(s)', multi: true },
  { key: 'soeur', label: 'Sœur(s)', multi: true },
  { key: 'autre_antecedent', label: 'Autres antécédents', multi: true },
];


export default function ExtractionCoordonneesPanel({
  pseudonyme = null,
  texte = null,
  documentId = null,
  pseudonymeCible = null,
  label = 'Extraire données patient',
  autoStart = false,
  onValidated = null,
  onCancel = null,
}) {
  const [fields, setFields] = useState(null); 
  const [extracting, setExtracting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  const [pseudonymeResolu, setPseudonymeResolu] = useState(pseudonyme || pseudonymeCible);

  async function lancerExtraction() {
    setExtracting(true);
    setError('');
    setSaved(false);
    try {
      const body = documentId ? { document_id: documentId } : (pseudonyme ? { pseudonyme } : { texte });
      const res = await client.post('/api/extraction/patient', body);
      const { pseudonyme: pseudonymeRenvoye, document_id: _d, ...rest } = res.data;
      if (pseudonymeRenvoye) setPseudonymeResolu(pseudonymeRenvoye);
      setFields(rest);
    } catch (err) {
      setError(err.response?.data?.error || "Échec de l'extraction.");
    } finally {
      setExtracting(false);
    }
  }

  useEffect(() => {
    if (autoStart) lancerExtraction();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function updateField(key, value) {
    setFields((f) => ({ ...f, [key]: value }));
    setSaved(false);
  }

  async function valider() {
    if (!pseudonymeResolu) {
      setError('Aucun dossier cible pour enregistrer ces coordonnées.');
      return;
    }
    setSaving(true);
    setError('');
    try {
      await client.post('/api/coordonnees', {
        pseudonyme: pseudonymeResolu,
        ...(documentId ? { document_id: documentId } : {}),
        ...fields,
      });
      setSaved(true);
      onValidated?.(fields);
    } catch (err) {
      setError(err.response?.data?.error || "Échec de l'enregistrement.");
    } finally {
      setSaving(false);
    }
  }

  if (!fields) {
    return (
      <div>
        {!autoStart && (
          <button
            type="button"
            onClick={lancerExtraction}
            disabled={extracting}
            style={{
              width: 'auto', margin: 0, padding: '8px 14px', display: 'inline-flex', alignItems: 'center', gap: 7,
              borderRadius: 9, border: '1.5px solid var(--teal)', background: 'var(--teal-tint)',
              color: 'var(--teal-deep)', fontWeight: 600, fontSize: 12.5,
              opacity: extracting ? 0.7 : 1,
            }}
          >
            <IconRefresh size={13} />
            {extracting ? 'Extraction en cours…' : label}
          </button>
        )}
        {autoStart && extracting && (
          <p className="hint" style={{ margin: 0, fontSize: 11.5, display: 'flex', alignItems: 'center', gap: 6 }}>
            <IconRefresh size={12} /> Extraction en cours…
          </p>
        )}
        {error && (
          <p className="error" style={{ margin: '8px 0 0', fontSize: 11.5, display: 'flex', alignItems: 'center', gap: 5 }}>
            <IconAlert size={12} /> {error}
          </p>
        )}
      </div>
    );
  }

  return (
    <div style={{
      display: 'flex', flexDirection: 'column', gap: 10, padding: 14,
      border: '1px solid var(--line)', borderRadius: 12, background: 'var(--paper)',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <p style={{ margin: 0, fontSize: 11, fontWeight: 700, color: 'var(--slate)', textTransform: 'uppercase', letterSpacing: 0.3 }}>
          Données extraites — à vérifier
        </p>
        <button
          type="button"
          onClick={lancerExtraction}
          disabled={extracting}
          title="Relancer l'extraction"
          style={{ width: 'auto', margin: 0, padding: 4, background: 'transparent', border: 'none', boxShadow: 'none', color: 'var(--slate-soft)' }}
        >
          <IconRefresh size={13} />
        </button>
      </div>

      {CHAMPS.map(({ key, label: champLabel, multi, readonly }) => (
        <div key={key} style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <label style={{ fontSize: 11.5, fontWeight: 600, color: 'var(--slate)' }}>
            {champLabel}
            {multi && <span style={{ fontWeight: 400, color: 'var(--slate-soft)' }}> (séparés par des virgules)</span>}
          </label>
          {readonly ? (
            <div style={{
              padding: '10px 12px', borderRadius: 8, border: '1.5px solid var(--line)',
              fontSize: 12.5, lineHeight: 1.5, background: 'var(--card)', color: 'var(--slate)',
              whiteSpace: 'pre-wrap',
            }}>
              {fields[key] || <span style={{ color: 'var(--slate-soft)' }}>—</span>}
            </div>
          ) : (
            <input
              value={fields[key] || ''}
              onChange={(e) => updateField(key, e.target.value)}
              placeholder="—"
              style={{
                padding: '8px 10px', borderRadius: 8, border: '1.5px solid var(--line)',
                fontSize: 12.5, background: 'var(--card)', boxSizing: 'border-box',
              }}
            />
          )}
        </div>
      ))}

      {error && (
        <p className="error" style={{ margin: 0, fontSize: 11.5, display: 'flex', alignItems: 'center', gap: 5 }}>
          <IconAlert size={12} /> {error}
        </p>
      )}

      <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
        <button
          type="button"
          onClick={valider}
          disabled={saving}
          style={{
            width: 'auto', margin: 0, padding: '8px 14px', display: 'inline-flex', alignItems: 'center', gap: 6,
            borderRadius: 9, border: 'none', background: 'var(--teal)', color: '#fff',
            fontWeight: 600, fontSize: 12.5, opacity: saving ? 0.7 : 1,
          }}
        >
          {saved ? <><IconCheckCircle size={13} /> Enregistré</> : saving ? 'Enregistrement…' : 'Valider'}
        </button>
        <button
          type="button"
          className="secondary"
          onClick={() => { setFields(null); setError(''); setSaved(false); onCancel?.(); }}
          style={{ width: 'auto', margin: 0, padding: '8px 14px', fontSize: 12.5 }}
        >
          Annuler
        </button>
      </div>
    </div>
  );
}