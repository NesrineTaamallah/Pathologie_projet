import { useState, useEffect } from 'react';
import client from '../api/client';
import { IconRefresh, IconAlert, IconCheckCircle } from './Icons';

/**
 * Panel d'extraction des ENTITÉS MÉDICALES, "texte par texte" : une seule
 * visite/consultation (un document_id, ex. un audio de ~2-3 min transcrit)
 * à la fois — pas tout le dossier d'un coup.
 *
 * Repris du même modèle que ExtractionCoordonneesPanel.jsx :
 *  - le texte source est affiché à côté des champs extraits (le clinicien
 *    voit d'où vient chaque valeur, comme les evidence_span mais avec le
 *    texte complet sous les yeux) ;
 *  - "Enregistrer" écrit VRAIMENT en base (POST /api/entites), contrairement
 *    à l'ancienne ExtractionEntitesModal qui ne faisait que "marquer comme
 *    relu" sans persister.
 *
 * La "mémoire base de données" (etat_initial) est gérée côté serveur
 * (backend/utils/entitesExtractionClient.js::chargerEtatInitialDepuisDB) :
 * les tables reçues ici contiennent déjà les valeurs des visites
 * précédentes fusionnées avec celles de CE texte.
 */
export default function ExtractionEntitesPanel({
  documentId,
  label = 'Extraire les entités médicales',
  autoStart = false,
  onSaved = null,
}) {
  const [texte, setTexte] = useState('');
  const [pseudonyme, setPseudonyme] = useState(null);
  const [registre, setRegistre] = useState(null);
  const [tables, setTables] = useState(null); // { nom_table: {...} ou [...] }
  const [aVerifier, setAVerifier] = useState([]);
  const [extracting, setExtracting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);

  async function lancerExtraction() {
    setExtracting(true);
    setError('');
    setSaved(false);
    try {
      const { data } = await client.post('/api/extraction/entites-document', { document_id: documentId });
      setTexte(data.texte || '');
      setPseudonyme(data.pseudonyme);
      setRegistre(data.registre);
      setTables(data.tables || {});
      setAVerifier(data.a_verifier || []);
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

  function updateChampObjet(tableKey, champ, valeur) {
    setTables((t) => ({ ...t, [tableKey]: { ...t[tableKey], [champ]: valeur } }));
    setSaved(false);
  }

  function updateChampListe(tableKey, index, champ, valeur) {
    setTables((t) => {
      const liste = [...t[tableKey]];
      liste[index] = { ...liste[index], [champ]: valeur };
      return { ...t, [tableKey]: liste };
    });
    setSaved(false);
  }

  function supprimerOccurrence(tableKey, index) {
    setTables((t) => {
      const liste = [...t[tableKey]];
      liste.splice(index, 1);
      return { ...t, [tableKey]: liste };
    });
    setSaved(false);
  }

  async function enregistrer() {
    if (!pseudonyme || !registre) {
      setError('Aucun dossier cible pour enregistrer ces entités.');
      return;
    }
    setSaving(true);
    setError('');
    try {
      await client.post('/api/entites', { pseudonyme, registre, tables });
      setSaved(true);
      onSaved?.(tables);
    } catch (err) {
      setError(err.response?.data?.error || "Échec de l'enregistrement.");
    } finally {
      setSaving(false);
    }
  }

  if (!tables) {
    return (
      <div>
        {!autoStart && (
          <button type="button" onClick={lancerExtraction} disabled={extracting}
            style={{
              width: 'auto', margin: 0, padding: '8px 14px', display: 'inline-flex', alignItems: 'center', gap: 7,
              borderRadius: 9, border: '1.5px solid var(--teal)', background: 'var(--teal-tint)',
              color: 'var(--teal-deep)', fontWeight: 600, fontSize: 12.5, opacity: extracting ? 0.7 : 1,
            }}>
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
      display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14,
      border: '1px solid var(--line)', borderRadius: 12, background: 'var(--paper)', padding: 14,
      alignItems: 'start',
    }}>
      {/* --- Colonne gauche : texte source (lecture) --- */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, position: 'sticky', top: 14 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <p style={{ margin: 0, fontSize: 11, fontWeight: 700, color: 'var(--slate)', textTransform: 'uppercase', letterSpacing: 0.3 }}>
            Texte transcrit — {pseudonyme}{registre ? ` · ${registre}` : ''}
          </p>
          <button type="button" onClick={lancerExtraction} disabled={extracting} title="Relancer l'extraction"
            style={{ width: 'auto', margin: 0, padding: 4, background: 'transparent', border: 'none', boxShadow: 'none', color: 'var(--slate-soft)' }}>
            <IconRefresh size={13} />
          </button>
        </div>
        <div style={{
          padding: '10px 12px', borderRadius: 8, border: '1.5px solid var(--line)', background: 'var(--card)',
          fontSize: 12.5, lineHeight: 1.5, whiteSpace: 'pre-wrap', maxHeight: 520, overflowY: 'auto',
        }}>
          {texte || <span className="hint">Texte vide.</span>}
        </div>
      </div>

      {/* --- Colonne droite : entités extraites (édition) --- */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {Object.entries(tables).map(([tableKey, contenu]) => (
          <TableBloc
            key={tableKey}
            tableKey={tableKey}
            contenu={contenu}
            onChampObjet={(champ, val) => updateChampObjet(tableKey, champ, val)}
            onChampListe={(idx, champ, val) => updateChampListe(tableKey, idx, champ, val)}
            onSupprimer={(idx) => supprimerOccurrence(tableKey, idx)}
          />
        ))}

        {error && (
          <p className="error" style={{ margin: 0, fontSize: 11.5, display: 'flex', alignItems: 'center', gap: 5 }}>
            <IconAlert size={12} /> {error}
          </p>
        )}

        <div style={{ display: 'flex', gap: 8, paddingTop: 6 }}>
          <button type="button" onClick={enregistrer} disabled={saving}
            style={{
              width: 'auto', margin: 0, padding: '8px 14px', display: 'inline-flex', alignItems: 'center', gap: 6,
              borderRadius: 9, border: 'none', background: 'var(--teal)', color: '#fff',
              fontWeight: 600, fontSize: 12.5, opacity: saving ? 0.7 : 1,
            }}>
            {saved ? <><IconCheckCircle size={13} /> Enregistré</> : saving ? 'Enregistrement…' : 'Enregistrer'}
          </button>
        </div>
      </div>
    </div>
  );
}

function objetEntierementVide(obj) {
  if (!obj || typeof obj !== 'object') return true;
  return Object.entries(obj).every(([k, v]) => {
    if (k.startsWith('evidence_span_') || k.startsWith('_')) return true;
    return v === null || v === 'null';
  });
}

function TableBloc({ tableKey, contenu, onChampObjet, onChampListe, onSupprimer }) {
  const estListe = Array.isArray(contenu);
  const totalementVide = estListe ? contenu.length === 0 : objetEntierementVide(contenu);
  return (
    <div style={{ borderRadius: 10, border: '1.5px solid var(--line)', background: 'var(--card)', overflow: 'hidden' }}>
      <div style={{ padding: '8px 12px', background: 'var(--paper)', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ fontSize: 12, fontWeight: 700 }}>{tableKey}</span>
        {estListe && <span style={{ fontSize: 10.5, color: 'var(--slate-soft)' }}>{contenu.length} occurrence{contenu.length > 1 ? 's' : ''}</span>}
        {totalementVide && (
          <span style={{ fontSize: 10, fontWeight: 600, color: '#8a6d1a', background: '#fdf6e6', border: '1px solid #f0c36d', borderRadius: 999, padding: '2px 7px', marginLeft: 'auto' }}>
            Rien détecté
          </span>
        )}
      </div>
      <div style={{ padding: '8px 12px', display: 'flex', flexDirection: 'column', gap: 8 }}>
        {estListe
          ? (contenu.length === 0
              ? <p className="hint" style={{ margin: 0, fontSize: 11.5 }}>Aucune occurrence — ajoutable manuellement si besoin.</p>
              : contenu.map((occ, idx) => (
                  <OccurrenceForm key={idx} occurrence={occ}
                    onChange={(champ, val) => onChampListe(idx, champ, val)}
                    onSupprimer={() => onSupprimer(idx)} />
                )))
          : <OccurrenceForm occurrence={contenu} onChange={onChampObjet} />}
      </div>
    </div>
  );
}

function OccurrenceForm({ occurrence, onChange, onSupprimer }) {
  const champs = Object.keys(occurrence || {}).filter((k) => !k.startsWith('_') && !k.startsWith('evidence_span_'));
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6, paddingBottom: 6, borderBottom: onSupprimer ? '1px dashed var(--line)' : 'none' }}>
      {champs.map((champ) => {
        const valeurBrute = occurrence[champ];
        const estVide = valeurBrute === 'null' || valeurBrute == null;
        return (
          <div key={champ} style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            <label style={{ fontSize: 10.5, fontWeight: 700, color: 'var(--ink)' }}>{champ}</label>
            <input
              value={estVide ? '' : valeurBrute}
              placeholder={valeurBrute === 'null' ? 'non mentionné' : valeurBrute === 'NA' ? 'NA' : '—'}
              onChange={(e) => onChange(champ, e.target.value)}
              style={{ padding: '5px 8px', borderRadius: 6, border: '1.5px solid var(--line)', fontSize: 11.5 }}
            />
          </div>
        );
      })}
      {onSupprimer && (
        <button type="button" onClick={onSupprimer}
          style={{ width: 'auto', alignSelf: 'flex-end', margin: 0, padding: '3px 8px', borderRadius: 6, border: '1px solid var(--line)', background: 'transparent', color: 'var(--slate-soft)', fontSize: 10 }}>
          Retirer
        </button>
      )}
    </div>
  );
}