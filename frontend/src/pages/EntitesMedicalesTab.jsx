import { useEffect, useMemo, useState, Fragment } from 'react';
import client from '../api/client';
import useDragScroll from '../hooks/useDragScroll';
import { SectionHeading } from '../components/DashboardWidgets';
import LineChartSVG from '../components/ClinicalChart';
import AjouterPatientWizard from './AjouterPatientWizard';
import ExtractionModal from '../components/ExtractionModal';
import ExtractionEntitesModal from '../components/ExtractionEntitesModal';
import {
  IconEye, IconPlus, IconSearch, IconFolder, IconAlert,
  IconUsers, IconHistory, IconActivity, IconTarget, IconHeart, IconWave,
  IconArrowLeft, IconShield, IconGlobe, IconKey, IconRefresh, IconLock, IconEyeOff,
  IconUpload, IconDownload,
} from '../components/Icons';

const REGISTRE_STYLE = {
  SEP: { bg: 'var(--teal-tint)', fg: 'var(--teal-deep)' },
  EPR: { bg: 'var(--amber-tint)', fg: 'var(--amber)' },
};

function RegistreBadge({ registre }) {
  const style = REGISTRE_STYLE[registre] || { bg: 'var(--line)', fg: 'var(--slate)' };
  return (
    <span style={{
      display: 'inline-block', fontSize: 11, fontWeight: 700, padding: '3px 10px',
      borderRadius: 999, background: style.bg, color: style.fg, letterSpacing: 0.3,
    }}>
      {registre}
    </span>
  );
}

function fmtDate(d) {
  if (!d) return '—';
  return new Date(d).toLocaleDateString('fr-FR');
}

function CompletudeCell({ value }) {
  const pct = Number.isFinite(value) ? value : 0;
  const color = pct >= 80 ? 'var(--success, #1c7a52)' : pct >= 50 ? 'var(--amber, #c8790f)' : 'var(--error, #c23b4e)';
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 90 }}>
      <div style={{ flex: 1, height: 6, borderRadius: 999, background: 'var(--line)', overflow: 'hidden' }}>
        <div style={{ width: `${pct}%`, height: '100%', background: color, borderRadius: 999 }} />
      </div>
      <span style={{ fontSize: 11.5, fontWeight: 700, color, minWidth: 30, textAlign: 'right' }}>{pct}%</span>
    </div>
  );
}

function humanizeKey(key) {
  const clean = key.replace(/_/g, ' ');
  return clean.charAt(0).toUpperCase() + clean.slice(1);
}

function formatValue(v) {
  if (v === null || v === undefined || v === '') return '—';
  if (typeof v === 'boolean') return v ? 'Oui' : 'Non';
  if (typeof v === 'string' && /^\d{4}-\d{2}-\d{2}/.test(v)) return fmtDate(v);
  
  if (typeof v === 'number' || (typeof v === 'string' && v.trim() !== '' && !Number.isNaN(Number(v)))) {
    const n = typeof v === 'number' ? v : Number(v);
    return n.toLocaleString('fr-FR', { maximumFractionDigits: 2 });
  }
  return String(v);
}

function FieldsGrid({ entries }) {
  const filtered = entries.filter(([k]) => !['id', 'pseudonyme'].includes(k));
  if (filtered.length === 0) return null;
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: '14px 18px' }}>
      {filtered.map(([k, v]) => (
        <div key={k} style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
          <p style={{ margin: 0, fontSize: 11, fontWeight: 600, color: 'var(--slate)', textTransform: 'uppercase', letterSpacing: 0.3 }}>
            {humanizeKey(k)}
          </p>
          <div style={{
            padding: '9px 11px', border: '1.5px solid var(--line)', borderRadius: 9,
            background: 'var(--paper)', fontSize: 13.5, color: 'var(--ink)',
          }}>
            {formatValue(v)}
          </div>
        </div>
      ))}
    </div>
  );
}


const IDENTIFICATION_TABLE_MAP = {
  SEP: {
    date_inclusion: 'patients', age: 'patients',
    sexe: 'sep_identification_clinique', gouvernorat_code: 'sep_identification_clinique',
    date_diagnostic: 'sep_identification_clinique', age_diagnostic_mois: 'sep_identification_clinique',
    age_premier_symptome_mois: 'sep_identification_clinique',
  },
  EPR: {
    date_inclusion: 'patients', age: 'patients',
    age_debut_crises_mois: 'epr_identification_clinique',
    age_diagnostic_pharmacoresistance_mois: 'epr_identification_clinique',
  },
};


function EditableFieldsGrid({ entries, pseudonyme, registre, onFieldSaved }) {
  const tableMap = IDENTIFICATION_TABLE_MAP[registre] || {};
  const filtered = entries.filter(([k]) => !['id', 'pseudonyme', 'registre'].includes(k));
  const [editingKey, setEditingKey] = useState(null);
  const [draft, setDraft] = useState('');
  const [savingKey, setSavingKey] = useState(null);
  const [rowError, setRowError] = useState({ key: null, message: '' });

  if (filtered.length === 0) return null;

  function startEdit(k, currentValue) {
    setEditingKey(k);
    setDraft(currentValue === null || currentValue === undefined ? '' : currentValue);
    setRowError({ key: null, message: '' });
  }

  function cancelEdit() {
    setEditingKey(null);
    setDraft('');
  }

  async function confirmSave(k) {
    const table = tableMap[k];
    if (!table) return; 
    const confirmed = window.confirm(
      `Confirmer la modification du champ "${humanizeKey(k)}" ?\n\nNouvelle valeur : ${draft || '(vide)'}`
    );
    if (!confirmed) return;

    setSavingKey(k);
    setRowError({ key: null, message: '' });
    try {
      const res = await client.patch(`/api/dossiers/${pseudonyme}/champ`, {
        table, colonne: k, valeur: draft,
      });
      onFieldSaved?.(k, res.data.valeur);
      setEditingKey(null);
      setDraft('');
    } catch (err) {
      setRowError({ key: k, message: err.response?.data?.error || "Échec de l'enregistrement." });
    } finally {
      setSavingKey(null);
    }
  }

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: '14px 18px' }}>
      {filtered.map(([k, v]) => {
        const editable = !!tableMap[k];
        const isEditing = editingKey === k;
        return (
          <div key={k} style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
            <p style={{ margin: 0, fontSize: 11, fontWeight: 600, color: 'var(--slate)', textTransform: 'uppercase', letterSpacing: 0.3 }}>
              {humanizeKey(k)}
            </p>

            {isEditing ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                <input
                  autoFocus
                  type={k.startsWith('date_') ? 'date' : 'text'}
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  
                  onKeyDown={(e) => { if (e.key === 'Enter') e.preventDefault(); }}
                  style={{
                    padding: '8px 10px', border: '1.5px solid var(--teal)', borderRadius: 9,
                    fontSize: 13.5, background: 'var(--paper)', color: 'var(--ink)',
                  }}
                />
                <div style={{ display: 'flex', gap: 6 }}>
                  <button
                    type="button"
                    onClick={() => confirmSave(k)}
                    disabled={savingKey === k}
                    style={{
                      flex: 1, padding: '6px 8px', borderRadius: 7, border: 'none',
                      background: 'var(--teal)', color: '#fff', fontSize: 12, fontWeight: 600,
                      cursor: savingKey === k ? 'default' : 'pointer', opacity: savingKey === k ? 0.7 : 1,
                    }}
                  >
                    {savingKey === k ? 'Enregistrement…' : 'Enregistrer'}
                  </button>
                  <button
                    type="button"
                    onClick={cancelEdit}
                    disabled={savingKey === k}
                    style={{
                      flex: 1, padding: '6px 8px', borderRadius: 7, border: '1.5px solid var(--line)',
                      background: 'var(--card)', color: 'var(--slate)', fontSize: 12, fontWeight: 600, cursor: 'pointer',
                    }}
                  >
                    Annuler
                  </button>
                </div>
                {rowError.key === k && (
                  <p style={{ margin: 0, fontSize: 11, color: 'var(--error, #c23b4e)' }}>{rowError.message}</p>
                )}
              </div>
            ) : (
              
              <div style={{ position: 'relative' }}>
                <div style={{
                  padding: '9px 34px 9px 11px', border: '1.5px solid var(--line)', borderRadius: 9,
                  background: 'var(--paper)', fontSize: 13.5, color: 'var(--ink)',
                }}>
                  {formatValue(v)}
                </div>
                {editable && (
                  <button
                    type="button"
                    onClick={() => startEdit(k, v)}
                    title="Modifier ce champ"
                    style={{
                      
                      position: 'absolute', top: 5, right: 5, width: 20, height: 20,
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      border: 'none', borderRadius: 5, background: 'var(--teal-tint)',
                      cursor: 'pointer', padding: 0, color: 'var(--teal-deep)', fontSize: 11, lineHeight: 1,
                    }}
                  >
                    ✎
                  </button>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}


function FichierField({ table, rowId, pseudonyme, cheminFichier, nomFichierOriginal, onUploaded }) {
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState('');
  const inputId = `fichier-${table}-${rowId}`;

  async function handleFileChange(e) {
    const file = e.target.files?.[0];
    e.target.value = ''; 
    if (!file) return;

    setUploading(true);
    setError('');
    try {
      const body = new FormData();
      body.append('table', table);
      body.append('id', rowId);
      body.append('fichier', file);
      const res = await client.post(`/api/dossiers/${pseudonyme}/entite-fichier`, body, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      onUploaded?.(res.data);
    } catch (err) {
      setError(err.response?.data?.error || "Échec de l'envoi du document.");
    } finally {
      setUploading(false);
    }
  }

  function handleDownload() {
    window.open(`http://localhost:4000/api/dossiers/entite-fichier/${table}/${rowId}/telecharger`, '_blank');
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
      <p style={{ margin: 0, fontSize: 11, fontWeight: 600, color: 'var(--slate)', textTransform: 'uppercase', letterSpacing: 0.3 }}>
        Document
      </p>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        {cheminFichier ? (
          <button
            type="button"
            onClick={handleDownload}
            title={nomFichierOriginal || 'Voir le document'}
            style={{
              flex: 1, display: 'flex', alignItems: 'center', gap: 6, padding: '8px 10px',
              border: '1.5px solid var(--line)', borderRadius: 9, background: 'var(--paper)',
              fontSize: 13, color: 'var(--teal-deep)', cursor: 'pointer', overflow: 'hidden',
            }}
          >
            <IconDownload size={14} />
            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {nomFichierOriginal || 'Voir le document'}
            </span>
          </button>
        ) : (
          <span style={{
            flex: 1, padding: '9px 11px', border: '1.5px solid var(--line)', borderRadius: 9,
            background: 'var(--paper)', fontSize: 13.5, color: 'var(--slate-soft)',
          }}>
            Aucun document
          </span>
        )}

        <label
          htmlFor={inputId}
          title={cheminFichier ? 'Remplacer le document' : 'Ajouter un document'}
          style={{
            width: 34, height: 34, minWidth: 34, display: 'flex', alignItems: 'center', justifyContent: 'center',
            border: 'none', borderRadius: 9, background: 'var(--teal-tint)', color: 'var(--teal-deep)',
            cursor: uploading ? 'default' : 'pointer', opacity: uploading ? 0.6 : 1,
          }}
        >
          <IconUpload size={15} />
        </label>
        <input
          id={inputId}
          type="file"
          accept="application/pdf,image/png,image/jpeg,image/tiff,image/webp"
          onChange={handleFileChange}
          disabled={uploading}
          style={{ display: 'none' }}
        />
      </div>
      {uploading && <p style={{ margin: 0, fontSize: 11, color: 'var(--slate)' }}>Envoi en cours…</p>}
      {error && <p style={{ margin: 0, fontSize: 11, color: 'var(--error, #c23b4e)' }}>{error}</p>}
    </div>
  );
}

function GenericEditableFieldsGrid({ entries, table, rowId, pseudonyme, onFieldSaved }) {
  
  const filtered = entries.filter(([k]) => !['id', 'pseudonyme', 'registre', 'created_at', 'nom_fichier_original'].includes(k));
  const nomFichierOriginal = entries.find(([k]) => k === 'nom_fichier_original')?.[1];
  const [editingKey, setEditingKey] = useState(null);
  const [draft, setDraft] = useState('');
  const [savingKey, setSavingKey] = useState(null);
  const [rowError, setRowError] = useState({ key: null, message: '' });

  if (filtered.length === 0) return null;

  function startEdit(k, currentValue) {
    setEditingKey(k);
    setDraft(currentValue === null || currentValue === undefined ? '' : currentValue);
    setRowError({ key: null, message: '' });
  }

  function cancelEdit() {
    setEditingKey(null);
    setDraft('');
  }

  async function confirmSave(k) {
    const confirmed = window.confirm(
      `Confirmer la modification du champ "${humanizeKey(k)}" ?\n\nNouvelle valeur : ${draft || '(vide)'}`
    );
    if (!confirmed) return;

    setSavingKey(k);
    setRowError({ key: null, message: '' });
    try {
      const res = await client.patch(`/api/dossiers/${pseudonyme}/champ`, {
        table, colonne: k, valeur: draft, id: rowId,
      });
      onFieldSaved?.(k, res.data.valeur);
      setEditingKey(null);
      setDraft('');
    } catch (err) {
      setRowError({ key: k, message: err.response?.data?.error || "Échec de l'enregistrement." });
    } finally {
      setSavingKey(null);
    }
  }

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: '14px 18px' }}>
      {filtered.map(([k, v]) => {
        const isEditing = editingKey === k;

        
        if (k === 'chemin_fichier') {
          return (
            <FichierField
              key={k}
              table={table}
              rowId={rowId}
              pseudonyme={pseudonyme}
              cheminFichier={v}
              nomFichierOriginal={nomFichierOriginal}
              onUploaded={(payload) => {
                onFieldSaved?.('chemin_fichier', payload.chemin_fichier);
                onFieldSaved?.('nom_fichier_original', payload.nom_fichier_original);
              }}
            />
          );
        }

        return (
          <div key={k} style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
            <p style={{ margin: 0, fontSize: 11, fontWeight: 600, color: 'var(--slate)', textTransform: 'uppercase', letterSpacing: 0.3 }}>
              {humanizeKey(k)}
            </p>

            {isEditing ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                <input
                  autoFocus
                  type={k.startsWith('date_') ? 'date' : 'text'}
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') e.preventDefault(); }}
                  style={{
                    padding: '8px 10px', border: '1.5px solid var(--teal)', borderRadius: 9,
                    fontSize: 13.5, background: 'var(--paper)', color: 'var(--ink)',
                  }}
                />
                <div style={{ display: 'flex', gap: 6 }}>
                  <button
                    type="button"
                    onClick={() => confirmSave(k)}
                    disabled={savingKey === k}
                    style={{
                      flex: 1, padding: '6px 8px', borderRadius: 7, border: 'none',
                      background: 'var(--teal)', color: '#fff', fontSize: 12, fontWeight: 600,
                      cursor: savingKey === k ? 'default' : 'pointer', opacity: savingKey === k ? 0.7 : 1,
                    }}
                  >
                    {savingKey === k ? 'Enregistrement…' : 'Enregistrer'}
                  </button>
                  <button
                    type="button"
                    onClick={cancelEdit}
                    disabled={savingKey === k}
                    style={{
                      flex: 1, padding: '6px 8px', borderRadius: 7, border: '1.5px solid var(--line)',
                      background: 'var(--card)', color: 'var(--slate)', fontSize: 12, fontWeight: 600, cursor: 'pointer',
                    }}
                  >
                    Annuler
                  </button>
                </div>
                {rowError.key === k && (
                  <p style={{ margin: 0, fontSize: 11, color: 'var(--error, #c23b4e)' }}>{rowError.message}</p>
                )}
              </div>
            ) : (
              <div style={{ position: 'relative' }}>
                <div style={{
                  padding: '9px 34px 9px 11px', border: '1.5px solid var(--line)', borderRadius: 9,
                  background: 'var(--paper)', fontSize: 13.5, color: 'var(--ink)',
                }}>
                  {formatValue(v)}
                </div>
                <button
                  type="button"
                  onClick={() => startEdit(k, v)}
                  title="Modifier ce champ"
                  style={{
                    position: 'absolute', top: 5, right: 5, width: 20, height: 20,
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    border: 'none', borderRadius: 5, background: 'var(--teal-tint)',
                    cursor: 'pointer', padding: 0, color: 'var(--teal-deep)', fontSize: 11, lineHeight: 1,
                  }}
                >
                  ✎
                </button>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}


function SectionContent({ data, tables, pseudonyme, onSaved, depth = 0 }) {
  if (!data || (typeof data === 'object' && Object.keys(data).length === 0)) {
    return <p className="hint" style={{ margin: 0 }}>Aucune donnée extraite pour cette section.</p>;
  }

  const entries = Object.entries(data);
  const simple = entries.filter(([, v]) => v === null || typeof v !== 'object');
  const nestedObjects = entries.filter(([, v]) => v && typeof v === 'object' && !Array.isArray(v));
  const nestedArrays = entries.filter(([, v]) => Array.isArray(v));

  const selfTable = tables?.self;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      {selfTable ? (
        <GenericEditableFieldsGrid
          entries={simple}
          table={selfTable.table}
          rowId={selfTable.singleton ? undefined : data.id}
          pseudonyme={pseudonyme}
          onFieldSaved={onSaved}
        />
      ) : (
        <FieldsGrid entries={simple} />
      )}

      {nestedObjects.map(([k, v]) => (
        <div key={k}>
          <p style={{ margin: '0 0 6px', fontSize: 12.5, fontWeight: 700, color: 'var(--ink)' }}>{humanizeKey(k)}</p>
          <SectionContent
            data={v}
            tables={tables?.[k] ? { self: tables[k] } : undefined}
            pseudonyme={pseudonyme}
            onSaved={onSaved}
            depth={depth + 1}
          />
        </div>
      ))}

      {nestedArrays.map(([k, arr]) => {
        const arrTable = tables?.[k];
        return (
          <div key={k}>
            <p style={{ margin: '0 0 6px', fontSize: 12.5, fontWeight: 700, color: 'var(--ink)' }}>
              {humanizeKey(k)} <span style={{ color: 'var(--slate-soft)', fontWeight: 500 }}>({arr.length})</span>
            </p>
            {arr.length === 0 ? (
              <p className="hint" style={{ margin: 0 }}>Aucun élément.</p>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {arr.map((item, i) => (
                  <div key={item.id ?? i} style={{
                    border: '1px solid var(--line)', borderRadius: 10, padding: '10px 12px', background: 'var(--paper)',
                  }}>
                    {arrTable ? (
                      <GenericEditableFieldsGrid
                        entries={Object.entries(item)}
                        table={arrTable.table}
                        rowId={item.id}
                        pseudonyme={pseudonyme}
                        onFieldSaved={onSaved}
                      />
                    ) : (
                      <FieldsGrid entries={Object.entries(item)} />
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}


const SECTION_TABLES_SEP = {
  antecedents: { self: { table: 'sep_antecedents', singleton: true } },
  presentation: { self: { table: 'sep_presentation_initiale', singleton: true } },
  poussees: { poussees: { table: 'sep_poussees', singleton: false } },
  handicap: {
    evolution: { table: 'sep_evolution', singleton: true },
    edssVisites: { table: 'sep_edss_visites', singleton: false },
  },
  irm: { irm: { table: 'sep_irm', singleton: false } },
  biologie: { biologieLcr: { table: 'sep_biologie_lcr', singleton: false } },
  pe: { potentielsEvoques: { table: 'sep_potentiels_evoques', singleton: false } },
  traitement: { traitementFond: { table: 'sep_traitement_fond', singleton: false } },
  suivi: { self: { table: 'sep_suivi', singleton: true } },
};

const SECTION_TABLES_EPR = {
  antecedents: { self: { table: 'epr_antecedents', singleton: true } },
  semiologie: {
    typeCrise: { table: 'epr_type_crise', singleton: false },
    frequenceCrises: { table: 'epr_frequence_crises', singleton: false },
  },
  examen: { examen: { table: 'epr_examen', singleton: false } },
  etiologie: { etiologie: { table: 'epr_etiologie', singleton: false } },
  regression: { self: { table: 'epr_regression_developpementale', singleton: true } },
  eeg: { eeg: { table: 'epr_eeg', singleton: false } },
  imagerie: { imagerie: { table: 'epr_imagerie', singleton: false } },
  genetique: { genetique: { table: 'epr_genetique', singleton: false } },
  pharmacoresistance: { listeAe: { table: 'epr_liste_ae', singleton: false } },
  chirurgie: {
    bilanPrechirurgical: { table: 'epr_bilan_prechirurgical', singleton: false },
    chirurgie: { table: 'epr_chirurgie', singleton: false },
  },
  alternatives: { alternatives: { table: 'epr_alternatives_therapeutiques', singleton: false } },
  multidisciplinaire: {
    bilanOrthophonique: { table: 'epr_bilan_orthophonique', singleton: false },
    bilanNeuropsy: { table: 'epr_bilan_neuropsy', singleton: false },
    bilanErgotherapique: { table: 'epr_bilan_ergotherapique', singleton: false },
  },
  suivi: { self: { table: 'epr_suivi', singleton: true } },
};


const TABS_SEP = [
  { key: 'identification', label: 'Identification', Icon: IconUsers, group: 'Clinique', get: (d) => stripIdent(d.identification) },
  { key: 'antecedents', label: 'Antécédents', Icon: IconHistory, group: 'Clinique', get: (d) => d.antecedents },
  { key: 'presentation', label: 'Présentation initiale', Icon: IconActivity, group: 'Clinique', get: (d) => d.evolutionClinique?.presentationInitiale },
  { key: 'poussees', label: 'Poussées', Icon: IconAlert, group: 'Clinique', get: (d) => ({ poussees: d.evolutionClinique?.poussees || [] }) },
  { key: 'handicap', label: 'Handicap & évolution', Icon: IconChartIcon, group: 'Clinique', get: (d) => ({ evolution: d.evolutionClinique?.evolution, edssVisites: d.evolutionClinique?.edssVisites || [] }) },
  { key: 'irm', label: 'Imagerie (IRM)', Icon: IconTarget, group: 'Paraclinique', get: (d) => ({ irm: d.imagerie?.irm || [] }) },
  { key: 'biologie', label: 'Biologie / LCR', Icon: IconShield, group: 'Paraclinique', get: (d) => ({ biologieLcr: d.imagerie?.biologieLcr || [] }) },
  { key: 'pe', label: 'Potentiels évoqués', Icon: IconWave, group: 'Paraclinique', get: (d) => ({ potentielsEvoques: d.imagerie?.potentielsEvoques || [] }) },
  { key: 'traitement', label: 'Traitement de fond', Icon: IconHeart, group: 'Traitement', get: (d) => ({ traitementFond: d.traitements?.traitementFond || [] }) },
  { key: 'suivi', label: 'Suivi', Icon: IconRefresh, group: 'Suivi', get: (d) => d.suivi },
];

const TABS_EPR = [
  { key: 'identification', label: 'Identification', Icon: IconUsers, group: 'Clinique', get: (d) => stripIdent(d.identification) },
  { key: 'antecedents', label: 'Antécédents', Icon: IconHistory, group: 'Clinique', get: (d) => d.antecedents },
  { key: 'semiologie', label: 'Sémiologie critique', Icon: IconActivity, group: 'Clinique', get: (d) => ({ typeCrise: d.evolutionClinique?.typeCrise || [], frequenceCrises: d.evolutionClinique?.frequenceCrises || [] }) },
  { key: 'examen', label: 'Examen', Icon: IconEye, group: 'Clinique', get: (d) => ({ examen: d.evolutionClinique?.examen || [] }) },
  { key: 'etiologie', label: 'Étiologie (ILAE)', Icon: IconKey, group: 'Clinique', get: (d) => ({ etiologie: d.evolutionClinique?.etiologie || [] }) },
  { key: 'regression', label: 'Régression développementale', Icon: IconAlert, group: 'Clinique', get: (d) => d.evolutionClinique?.regressionDeveloppementale },
  { key: 'eeg', label: 'EEG', Icon: IconWave, group: 'Paraclinique', get: (d) => ({ eeg: d.imagerie?.eeg || [] }) },
  { key: 'imagerie', label: 'Imagerie cérébrale', Icon: IconTarget, group: 'Paraclinique', get: (d) => ({ imagerie: d.imagerie?.imagerie || [] }) },
  { key: 'genetique', label: 'Génétique', Icon: IconGlobe, group: 'Paraclinique', get: (d) => ({ genetique: d.imagerie?.genetique || [] }) },
  { key: 'pharmacoresistance', label: 'Pharmacorésistance', Icon: IconShield, group: 'Traitement', get: (d) => ({ listeAe: d.traitements?.listeAe || [] }) },
  { key: 'chirurgie', label: 'Bilan pré-chir. & chirurgie', Icon: IconChartIcon, group: 'Traitement', get: (d) => ({ bilanPrechirurgical: d.traitements?.bilanPrechirurgical || [], chirurgie: d.traitements?.chirurgie || [] }) },
  { key: 'alternatives', label: 'Alternatives thérapeutiques', Icon: IconRefresh, group: 'Traitement', get: (d) => ({ alternatives: d.traitements?.alternatives || [] }) },
  { key: 'multidisciplinaire', label: 'Bilan multidisciplinaire', Icon: IconHeart, group: 'Suivi', get: (d) => ({ bilanOrthophonique: d.suivi?.bilanOrthophonique || [], bilanNeuropsy: d.suivi?.bilanNeuropsy || [], bilanErgotherapique: d.suivi?.bilanErgotherapique || [] }) },
  { key: 'suivi', label: 'Suivi', Icon: IconRefresh, group: 'Suivi', get: (d) => ({ statutDernierSuivi: d.suivi?.statut_dernier_suivi, dureeSuiviMois: d.suivi?.duree_suivi_mois }) },
];

const GROUP_ORDER = ['Clinique', 'Paraclinique', 'Traitement', 'Suivi'];

function IconChartIcon(props) { return <IconTarget {...props} />; }

function stripIdent(ident) {
  if (!ident) return ident;
  const { pseudonyme: _p, registre: _r, ...rest } = ident;
  return rest;
}

function HorizontalNav({ tabs, active, onChange }) {
  const byGroup = GROUP_ORDER
    .map((g) => ({ group: g, items: tabs.filter((t) => t.group === g) }))
    .filter((g) => g.items.length > 0);

  return (
    <div style={{
      display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 2,
      borderBottom: '1.5px solid var(--line)', marginBottom: 18, paddingBottom: 2,
    }}>
      {byGroup.map(({ group, items }, gi) => (
        <div key={group} style={{ display: 'flex', alignItems: 'center', gap: 2 }}>
          {gi > 0 && (
            <span style={{ width: 1, height: 18, background: 'var(--line)', margin: '0 8px' }} />
          )}
          {items.map(({ key, label, Icon }) => {
            const isActive = active === key;
            return (
              <button
                key={key}
                type="button"
                onClick={() => onChange(key)}
                title={group}
                style={{
                  width: 'auto', margin: 0, padding: '9px 12px 11px',
                  background: 'transparent', border: 'none', borderRadius: 0, boxShadow: 'none',
                  borderBottom: isActive ? '2.5px solid var(--teal)' : '2.5px solid transparent',
                  color: isActive ? 'var(--teal-deep)' : 'var(--slate)',
                  fontSize: 12.5, fontWeight: isActive ? 700 : 600, whiteSpace: 'nowrap',
                  display: 'inline-flex', alignItems: 'center', gap: 6,
                }}
              >
                <Icon size={13} />
                {label}
              </button>
            );
          })}
        </div>
      ))}
    </div>
  );
}

function SuiviChart({ registre, data }) {
  if (registre === 'EPR') {
    const rows = data.evolutionClinique?.frequenceCrises || [];
    const points = rows
      .filter((r) => r.frequence_normalisee_mois !== null && r.frequence_normalisee_mois !== undefined)
      .map((r) => ({ date: r.date_rapport, value: Number(r.frequence_normalisee_mois) }));
    const baseline = points.length > 0 ? points[0].value : null;
    const yMax = Math.max(4, ...points.map((p) => p.value), baseline ? baseline * 1.1 : 0);

    return (
      <div style={{ marginBottom: 22 }}>
        <p style={{ margin: '0 0 4px', fontSize: 12.5, fontWeight: 700, color: 'var(--ink)' }}>
          Fréquence des crises dans le temps (crises / mois, normalisée)
        </p>
        <p className="hint" style={{ margin: '0 0 10px' }}>
          Indicateur de suivi standard en épileptologie — comparaison à la fréquence de base pour juger la réponse thérapeutique (seuil « répondeur » : réduction ≥ 50 %).
        </p>
        <LineChartSVG
          points={points}
          yMin={0}
          yMax={Math.ceil(yMax)}
          unit="/mois"
          color="var(--amber, #c8790f)"
          referenceLines={baseline ? [
            { y: baseline, label: `Fréquence de base (${baseline}/mois)`, color: 'var(--slate)' },
            { y: baseline / 2, label: 'Seuil répondeur (-50 %)', color: 'var(--error, #c23b4e)' },
          ] : []}
          emptyLabel="Aucune fréquence de crises rapportée pour ce patient."
        />
      </div>
    );
  }

  const rows = data.evolutionClinique?.edssVisites || [];
  const points = rows
    .filter((r) => r.score_edss !== null && r.score_edss !== undefined)
    .map((r) => ({ date: r.date_visite, value: Number(r.score_edss) }));

  return (
    <div style={{ marginBottom: 22 }}>
      <p style={{ margin: '0 0 4px', fontSize: 12.5, fontWeight: 700, color: 'var(--ink)' }}>
        Score EDSS dans le temps (échelle de handicap, 0–10)
      </p>
      <p className="hint" style={{ margin: '0 0 10px' }}>
        Indicateur de suivi standard en neurologie de la SEP — un score par visite, avec les jalons cliniques repères EDSS 4 et EDSS 6.
      </p>
      <LineChartSVG
        points={points}
        yMin={0}
        yMax={10}
        yTicks={[0, 2, 4, 6, 8, 10]}
        unit=""
        color="var(--teal-deep, #145aa3)"
        referenceLines={[
          { y: 4, label: 'EDSS 4 — périmètre de marche limité', color: 'var(--amber, #c8790f)' },
          { y: 6, label: 'EDSS 6 — aide à la marche nécessaire', color: 'var(--error, #c23b4e)' },
        ]}
        emptyLabel="Aucun score EDSS enregistré pour ce patient."
      />
    </div>
  );
}


function IdentityReveal({ pseudonyme }) {
  const [nom, setNom] = useState(null); 
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  async function confirm() {
    if (!password) return;
    setSubmitting(true);
    setError('');
    try {
      const res = await client.post('/api/coordonnees/reveal', { pseudonyme, password });
      setNom(res.data.nom_prenom || 'Non renseigné');
      setOpen(false);
      setPassword('');
    } catch (err) {
      setError(err.response?.data?.error || 'Mot de passe incorrect.');
    } finally {
      setSubmitting(false);
    }
  }

  if (nom) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
        <p style={{ margin: 0, fontSize: 13.5, fontWeight: 700, color: 'var(--ink)' }}>{nom}</p>
        <button
          type="button"
          onClick={() => setNom(null)}
          title="Masquer le nom"
          style={{ width: 'auto', margin: 0, padding: 4, background: 'transparent', border: 'none', boxShadow: 'none', color: 'var(--slate-soft)' }}
        >
          <IconEyeOff size={14} />
        </button>
      </div>
    );
  }

  return (
    <div>
      <button
        type="button"
        onClick={() => { setOpen(true); setError(''); }}
        style={{
          width: 'auto', margin: 0, padding: '5px 10px', display: 'inline-flex', alignItems: 'center', gap: 6,
          borderRadius: 8, border: '1.5px dashed var(--line)', background: 'var(--paper)',
          color: 'var(--slate)', fontSize: 11.5, fontWeight: 600,
        }}
      >
        <IconLock size={12} />
        Afficher nom &amp; prénom
      </button>

      {open && (
        <div style={{
          marginTop: 8, padding: 10, borderRadius: 10, border: '1px solid var(--line)', background: 'var(--paper)',
          display: 'flex', flexDirection: 'column', gap: 8,
        }}>
          <p className="hint" style={{ margin: 0, fontSize: 11 }}>
            Confirmez votre mot de passe pour déverrouiller l'identité — accès journalisé.
          </p>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && confirm()}
            placeholder="Mot de passe"
            autoComplete="new-password"
            data-lpignore="true"
            data-1p-ignore="true"
            readOnly
            onFocus={(e) => e.target.removeAttribute('readonly')}
            style={{ padding: '7px 10px', borderRadius: 8, border: '1.5px solid var(--line)', fontSize: 12.5, background: 'var(--card)' }}
          />
          {error && <p className="error" style={{ margin: 0, fontSize: 11 }}>{error}</p>}
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              type="button"
              onClick={confirm}
              disabled={submitting || !password}
              style={{ width: 'auto', margin: 0, padding: '6px 12px', fontSize: 11.5 }}
            >
              {submitting ? 'Vérification…' : 'Confirmer'}
            </button>
            <button
              type="button"
              className="secondary"
              onClick={() => { setOpen(false); setPassword(''); setError(''); }}
              style={{ width: 'auto', margin: 0, padding: '6px 12px', fontSize: 11.5 }}
            >
              Annuler
            </button>
          </div>
        </div>
      )}
    </div>
  );
}


function QuickStats({ registre, data }) {
  if (registre === 'EPR') {
    const rows = data.evolutionClinique?.frequenceCrises || [];
    const last = rows[rows.length - 1];
    const statut = data.suivi?.statut_dernier_suivi;
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, fontSize: 12.5 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', color: 'var(--slate)' }}>
          <span>Dernière fréquence</span>
          <span style={{ color: 'var(--ink)', fontWeight: 600 }}>
            {last ? `${formatValue(last.frequence_normalisee_mois)}/mois` : '—'}
          </span>
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', color: 'var(--slate)' }}>
          <span>Statut au dernier suivi</span>
          <span style={{ color: 'var(--ink)', fontWeight: 600, textAlign: 'right' }}>{formatValue(statut)}</span>
        </div>
      </div>
    );
  }
  const edss = data.evolutionClinique?.edssVisites || [];
  const lastEdss = edss[edss.length - 1];
  const statut = data.suivi?.statut_dernier_suivi;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, fontSize: 12.5 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', color: 'var(--slate)' }}>
        <span>Dernier EDSS</span>
        <span style={{ color: 'var(--ink)', fontWeight: 600 }}>{lastEdss ? formatValue(lastEdss.score_edss) : '—'}</span>
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', color: 'var(--slate)' }}>
        <span>Statut au dernier suivi</span>
        <span style={{ color: 'var(--ink)', fontWeight: 600, textAlign: 'right' }}>{formatValue(statut)}</span>
      </div>
    </div>
  );
}


function DossierView({ pseudonyme, onBack }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [activeTab, setActiveTab] = useState('identification');

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError('');
    setActiveTab('identification');
    client.get(`/api/dossiers/${pseudonyme}`)
      .then((res) => { if (!cancelled) setData(res.data); })
      .catch(() => { if (!cancelled) setError('Impossible de charger ce dossier.'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [pseudonyme]);

  const registre = data?.identification?.registre;
  const tabs = useMemo(() => (registre === 'EPR' ? TABS_EPR : TABS_SEP), [registre]);
  const activeDef = tabs.find((t) => t.key === activeTab) || tabs[0];
  const sectionTables = (registre === 'EPR' ? SECTION_TABLES_EPR : SECTION_TABLES_SEP)[activeTab];

  
  function reloadDossier() {
    client.get(`/api/dossiers/${pseudonyme}`).then((res) => setData(res.data)).catch(() => {});
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <button
        type="button"
        className="secondary"
        style={{ width: 'auto', margin: 0, display: 'inline-flex', alignItems: 'center', gap: 8, padding: '8px 14px', fontSize: 12.5 }}
        onClick={onBack}
      >
        <IconArrowLeft size={14} />
        Retour aux dossiers
      </button>

      {loading && <p className="hint">Chargement du dossier…</p>}
      {error && <p className="error">{error}</p>}

      {!loading && !error && data && (
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(220px, 260px) 1fr', gap: 20, alignItems: 'start' }}>
          {/* Bandeau patient, à gauche — identité + chiffres clés uniquement (la navigation par catégories est passée en horizontal, plus de doublon ici) */}
          <div className="card" style={{ position: 'sticky', top: 16, display: 'flex', flexDirection: 'column', gap: 14 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <div style={{
                width: 42, height: 42, borderRadius: 11, background: 'var(--teal-tint)', color: 'var(--teal-deep)',
                display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
              }}>
                <IconFolder size={19} />
              </div>
              <div style={{ minWidth: 0 }}>
                <p style={{ margin: 0, fontFamily: 'var(--font-mono)', fontSize: 13.5, fontWeight: 700, color: 'var(--ink)', wordBreak: 'break-all' }}>
                  {pseudonyme}
                </p>
                {registre && <RegistreBadge registre={registre} />}
              </div>
            </div>

            <IdentityReveal pseudonyme={pseudonyme} />

            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, fontSize: 12.5 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', color: 'var(--slate)' }}>
                <span>Date d'inclusion</span>
                <span style={{ color: 'var(--ink)', fontWeight: 600 }}>{fmtDate(data.identification?.date_inclusion)}</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', color: 'var(--slate)' }}>
                <span>Âge</span>
                <span style={{ color: 'var(--ink)', fontWeight: 600 }}>{formatValue(data.identification?.age)}</span>
              </div>
            </div>

            <div style={{ borderTop: '1px solid var(--line)', paddingTop: 12 }}>
              <p style={{ margin: '0 0 8px', fontSize: 11, fontWeight: 700, color: 'var(--slate)', textTransform: 'uppercase', letterSpacing: 0.3 }}>
                Suivi en un coup d'œil
              </p>
              <QuickStats registre={registre} data={data} />
            </div>
          </div>

          {/* Contenu — pleine largeur restante : onglets horizontaux (seule navigation par catégories) + contenu de la catégorie active */}
          <div className="card" style={{ minHeight: 420 }}>
            <HorizontalNav tabs={tabs} active={activeTab} onChange={setActiveTab} />
            <h3 style={{ margin: '0 0 14px', fontSize: 15, fontFamily: 'var(--font-display)' }}>{activeDef.label}</h3>
            {activeTab === 'suivi' && <SuiviChart registre={registre} data={data} />}
            {activeTab === 'identification' ? (
              <EditableFieldsGrid
                entries={Object.entries(activeDef.get(data) || {})}
                pseudonyme={pseudonyme}
                registre={registre}
                onFieldSaved={(k, v) => setData((d) => ({ ...d, identification: { ...d.identification, [k]: v } }))}
              />
            ) : (
              <SectionContent
                data={activeDef.get(data)}
                tables={sectionTables}
                pseudonyme={pseudonyme}
                onSaved={reloadDossier}
              />
            )}
          </div>
        </div>
      )}
    </div>
  );
}


export default function EntitesMedicalesTab({ alertType, onConsumed }) {
  const dossiersScrollRef = useDragScroll();
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [viewPseudo, setViewPseudo] = useState(null);
  const [showAdd, setShowAdd] = useState(false);

  const [alertFilter, setAlertFilter] = useState(null); 
  const [alertLabel, setAlertLabel] = useState('');
  const [alertLoading, setAlertLoading] = useState(false);
  const [alertError, setAlertError] = useState('');

  
  const [ajoutPatient, setAjoutPatient] = useState(null);
  const [ajoutLoading, setAjoutLoading] = useState(null); 
  const [ajoutError, setAjoutError] = useState('');
  const [extractRow, setExtractRow] = useState(null);
  const [extractEntitesRow, setExtractEntitesRow] = useState(null);

  async function ouvrirAjoutDocument(row) {
    setAjoutError('');
    setAjoutLoading(row.pseudonyme);
    try {
      const res = await client.get(`/api/dossiers/${row.pseudonyme}/documents`);
      const numeroDossier = res.data.numero_dossier;
      if (!numeroDossier) {
        setAjoutError("Impossible de retrouver le numéro de dossier pour ce patient.");
        return;
      }
      const verif = await client.get('/api/dossiers/verifier', {
        params: { pathologie: row.registre, numero_dossier: numeroDossier },
      });
      setAjoutPatient({
        pseudonyme: row.pseudonyme,
        pathologie: row.registre,
        numero_dossier: numeroDossier,
        date_diagnostic: verif.data.date_diagnostic,
        date_inclusion: verif.data.date_inclusion,
      });
      setShowAdd(true);
    } catch {
      setAjoutError("Échec de la préparation de l'ajout de document.");
    } finally {
      setAjoutLoading(null);
    }
  }

  function loadDossiers() {
    setLoading(true);
    client.get('/api/dossiers')
      .then((res) => setRows(res.data))
      .catch(() => setError('Impossible de charger la liste des dossiers.'))
      .finally(() => setLoading(false));
  }

  useEffect(loadDossiers, []);

  useEffect(() => {
    if (!alertType) return;
    setAlertLoading(true);
    setAlertError('');
    client.get(`/api/clinicien/entites/alerte/${alertType}`)
      .then((res) => {
        setAlertFilter(new Set((res.data.patients || []).map((p) => p.pseudonyme)));
        setAlertLabel(res.data.label || '');
      })
      .catch(() => setAlertError("Impossible de charger la liste des patients pour cette alerte."))
      .finally(() => {
        setAlertLoading(false);
        onConsumed?.();
      });
  }, [alertType]); 

  const bySearch = rows.filter((r) => r.pseudonyme.toLowerCase().includes(search.toLowerCase()));
  const filtered = alertFilter ? bySearch.filter((r) => alertFilter.has(r.pseudonyme)) : bySearch;

 
  if (viewPseudo) {
    return <DossierView pseudonyme={viewPseudo} onBack={() => setViewPseudo(null)} />;
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <SectionHeading Icon={IconFolder} title="Entités Médicales" subtitle="Dossiers et entités extraites par le NER" />

      {alertLoading && (
        <p className="hint" style={{ margin: 0 }}>Chargement de l'alerte…</p>
      )}
      {alertError && <p className="error" style={{ margin: 0 }}>{alertError}</p>}
      {alertFilter && !alertLoading && (
        <div className="card" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, borderLeft: '3px solid var(--amber, #C98A2C)' }}>
          <span style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 13.5, color: 'var(--ink)' }}>
            <IconAlert size={16} />
            <strong>{alertFilter.size}</strong> patient(s) concerné(s) par « {alertLabel} ».
          </span>
          <button
            type="button"
            className="secondary"
            style={{ width: 'auto', margin: 0, padding: '6px 12px', fontSize: 12 }}
            onClick={() => setAlertFilter(null)}
          >
            Voir tous les dossiers
          </button>
        </div>
      )}

      <div className="card">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16, flexWrap: 'wrap', gap: 12 }}>
          <div>
            <h3 style={{ margin: 0, fontSize: 15.5, fontFamily: 'var(--font-display)' }}>Dossiers</h3>
            <p style={{ margin: '2px 0 0', fontSize: 12, color: 'var(--slate-soft)' }}>
              Champs structurés extraits par le NER, dossier par dossier.
            </p>
          </div>
          <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
            <div style={{ position: 'relative' }}>
              <span style={{ position: 'absolute', left: 11, top: '50%', transform: 'translateY(-50%)', color: 'var(--slate-soft)' }}>
                <IconSearch size={14} />
              </span>
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Rechercher un pseudonyme..."
                autoComplete="off"
                name="dossier-search"
                readOnly
                onFocus={(e) => e.target.removeAttribute('readonly')}
                style={{ width: 220, padding: '9px 12px 9px 32px', borderRadius: 10, border: '1.5px solid var(--line)', fontSize: 12.8, background: 'var(--paper)' }}
              />
            </div>
            <button
              type="button"
              style={{ width: 'auto', margin: 0, display: 'inline-flex', alignItems: 'center', gap: 8, padding: '9px 16px' }}
              onClick={() => setShowAdd(true)}
            >
              <IconPlus size={15} color="#fff" />
              Ajouter
            </button>
          </div>
        </div>

        {error && <p className="error">{error}</p>}
        {loading && <p style={{ fontSize: 13, color: 'var(--slate)' }}>Chargement…</p>}
        {!loading && !error && filtered.length === 0 && (
          <p style={{ fontSize: 13, color: 'var(--slate)' }}>Aucun dossier trouvé.</p>
        )}

        {!loading && filtered.length > 0 && (
          <div
            ref={dossiersScrollRef}
            style={{ overflowX: 'auto', borderRadius: 10, cursor: 'grab' }}
          >
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.8 }}>
              <thead>
                <tr style={{ textAlign: 'left', color: 'var(--slate)', fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.3, borderBottom: '1px solid var(--line)' }}>
                  <th style={{ padding: '11px 10px' }}>Pseudonyme</th>
                  <th style={{ padding: '11px 10px' }}>Registre</th>
                  <th style={{ padding: '11px 10px' }}>Date d'inclusion</th>
                  <th style={{ padding: '11px 10px' }}>Dernière visite</th>
                  <th style={{ padding: '11px 10px' }}>Complétude</th>
                  <th style={{ padding: '11px 10px' }}></th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((r) => (
                  <Fragment key={r.pseudonyme}>
                    <tr style={{ borderBottom: extractRow === r.pseudonyme ? 'none' : '1px solid var(--line)' }}>
                      <td style={{ padding: '11px 10px', fontFamily: 'var(--font-mono)' }}>{r.pseudonyme}</td>
                      <td style={{ padding: '11px 10px' }}><RegistreBadge registre={r.registre} /></td>
                      <td style={{ padding: '11px 10px' }}>{fmtDate(r.date_inclusion)}</td>
                      <td style={{ padding: '11px 10px' }}>{fmtDate(r.derniere_visite)}</td>
                      <td style={{ padding: '11px 10px' }}><CompletudeCell value={r.completude} /></td>
                      <td style={{ padding: '11px 10px' }}>
                        <div style={{ display: 'flex', gap: 6 }}>
                          <button
                            onClick={() => setViewPseudo(r.pseudonyme)}
                            style={{
                              width: 'auto', margin: 0, display: 'flex', alignItems: 'center', gap: 6, padding: '6px 10px',
                              borderRadius: 10, border: '1.5px solid var(--line)', background: 'var(--card)',
                              color: 'var(--teal-deep)', fontSize: 11.5, fontWeight: 600,
                            }}
                          >
                            <IconEye size={13} />
                            Voir
                          </button>
                          <button
                            onClick={() => ouvrirAjoutDocument(r)}
                            disabled={ajoutLoading === r.pseudonyme}
                            title="Ajouter un document (audio/scan) à ce dossier existant"
                            style={{
                              width: 'auto', margin: 0, display: 'flex', alignItems: 'center', gap: 6, padding: '6px 10px',
                              borderRadius: 10, border: '1.5px solid var(--line)', background: 'var(--card)',
                              color: 'var(--teal-deep)', fontSize: 11.5, fontWeight: 600,
                              opacity: ajoutLoading === r.pseudonyme ? 0.6 : 1,
                            }}
                          >
                            <IconPlus size={13} />
                            {ajoutLoading === r.pseudonyme ? '…' : 'Ajouter'}
                          </button>
                          <button
                            onClick={() => setExtractRow(r.pseudonyme)}
                            title="Extraire les coordonnées de ce patient depuis les documents transcrits"
                            style={{
                              width: 'auto', margin: 0, display: 'flex', alignItems: 'center', gap: 6, padding: '6px 10px',
                              borderRadius: 10,
                              border: `1.5px solid ${extractRow === r.pseudonyme ? 'var(--teal)' : 'var(--line)'}`,
                              background: extractRow === r.pseudonyme ? 'var(--teal-tint)' : 'var(--card)',
                              color: 'var(--teal-deep)', fontSize: 11.5, fontWeight: 600,
                            }}
                          >
                            <IconRefresh size={13} />
                            Extraire coordonnées
                          </button>
                          <button
                            onClick={() => setExtractEntitesRow(r.pseudonyme)}
                            disabled={r.coordonnees_en_attente > 0}
                            title={r.coordonnees_en_attente > 0
                              ? 'Validez d\'abord les coordonnées de ce patient'
                              : 'Extraire les entités médicales (tables cliniques) de ce patient'}
                            style={{
                              width: 'auto', margin: 0, display: 'flex', alignItems: 'center', gap: 6, padding: '6px 10px',
                              borderRadius: 10,
                              border: `1.5px solid ${extractEntitesRow === r.pseudonyme ? 'var(--teal)' : 'var(--line)'}`,
                              background: extractEntitesRow === r.pseudonyme ? 'var(--teal-tint)' : 'var(--card)',
                              color: r.coordonnees_en_attente > 0 ? 'var(--slate-soft)' : 'var(--teal-deep)',
                              fontSize: 11.5, fontWeight: 600,
                              opacity: r.coordonnees_en_attente > 0 ? 0.55 : 1,
                              cursor: r.coordonnees_en_attente > 0 ? 'not-allowed' : 'pointer',
                            }}
                          >
                            <IconRefresh size={13} />
                            Extraire entités
                            {r.entites_en_attente > 0 && (
                              <span style={{
                                minWidth: 16, height: 16, padding: '0 4px', borderRadius: 999,
                                background: 'var(--error, #b3261e)', color: '#fff', fontSize: 9.5,
                                fontWeight: 700, display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                              }}>
                                {r.entites_en_attente}
                              </span>
                            )}
                          </button>
                        </div>
                      </td>
                    </tr>
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {extractRow && (
          <ExtractionModal
            pseudonyme={extractRow}
            onClose={() => setExtractRow(null)}
            onAllDone={() => setExtractRow(null)}
          />
        )}
        {extractEntitesRow && (
          <ExtractionEntitesModal
            pseudonyme={extractEntitesRow}
            onClose={() => setExtractEntitesRow(null)}
            onReviewed={() => setExtractEntitesRow(null)}
          />
        )}
        {ajoutError && (
          <p style={{ fontSize: 12, color: 'var(--error)', display: 'flex', alignItems: 'center', gap: 6, marginTop: 10 }}>
            <IconAlert size={13} /> {ajoutError}
          </p>
        )}
      </div>

      {showAdd && (
        <AjouterPatientWizard
          
          key={ajoutPatient ? `ajout-${ajoutPatient.pseudonyme}` : 'nouveau'}
          existingPatient={ajoutPatient}
          onSwitchToAjout={(doublon) => setAjoutPatient({
            pseudonyme: doublon.pseudonyme,
            pathologie: doublon.pathologie,
            numero_dossier: doublon.numero_dossier,
            date_diagnostic: doublon.date_diagnostic,
            date_inclusion: doublon.date_inclusion,
          })}
          onVoirDossier={(pseudonyme) => {
            
            setShowAdd(false);
            setAjoutPatient(null);
            setViewPseudo(pseudonyme);
          }}
          onClose={() => { setShowAdd(false); setAjoutPatient(null); }}
          onCreated={() => loadDossiers()}
        />
      )}
    </div>
  );
}