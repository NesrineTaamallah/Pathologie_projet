import { useState, useEffect } from 'react';
import client from '../api/client';
import {
  IconX, IconArrowLeft, IconArrowRight, IconUpload, IconCheckCircle,
  IconAlert, IconFolder, IconHeart, IconActivity,
} from '../components/Icons';
import ExtractionCoordonneesPanel from '../components/ExtractionCoordonneesPanel';

const TYPES_DOCUMENT = [
  { value: 'visite', label: 'Visite' },
  { value: 'admission', label: 'Admission' },
  { value: 'prelevement_sang', label: 'Prélèvement sanguin' },
  { value: 'eeg', label: 'EEG' },
  { value: 'emg', label: 'EMG' },
  { value: 'irm', label: 'IRM' },
  { value: 'autre', label: 'Autre' },
];

const STEPS = ['Dossier', 'Document', 'Entrée', 'Confirmation'];

const initialForm = {
  numero_dossier: '',
  pathologie: '',
  date_diagnostic: '',
  date_inclusion: '',
  type_document: '',
  type_entree: '',
};


export default function AjouterPatientWizard({ onClose, onCreated, existingPatient = null, onSwitchToAjout = null, onVoirDossier = null }) {
  const isAjoutDocument = !!existingPatient;
  const STEPS_ACTIVES = isAjoutDocument ? STEPS.slice(1) : STEPS;
  const FIRST_STEP = isAjoutDocument ? 1 : 0;

  const [step, setStep] = useState(FIRST_STEP);
  const [form, setForm] = useState(() => (isAjoutDocument
    ? {
        ...initialForm,
        numero_dossier: existingPatient.numero_dossier || '',
        pathologie: existingPatient.pathologie || '',
        date_diagnostic: existingPatient.date_diagnostic || '',
        date_inclusion: existingPatient.date_inclusion || '',
      }
    : initialForm));
  const [file, setFile] = useState(null);
  const [dragOver, setDragOver] = useState(false);
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState(null);
  const [texteCorrige, setTexteCorrige] = useState('');
  const [validating, setValidating] = useState(false);
  const [showToast, setShowToast] = useState(false);
  
  const [texteValide, setTexteValide] = useState(false);

  
  const [audioUrl, setAudioUrl] = useState(null);
  useEffect(() => {
    if (file && form.type_entree === 'audio') {
      const url = URL.createObjectURL(file);
      setAudioUrl(url);
      return () => URL.revokeObjectURL(url);
    }
    setAudioUrl(null);
  }, [file, form.type_entree]);

  
  const [doublon, setDoublon] = useState(null); 
  const [checkingDoublon, setCheckingDoublon] = useState(false);

  function update(field, value) {
    setForm((f) => ({ ...f, [field]: value }));
    setError('');
    if (field === 'numero_dossier' || field === 'pathologie') setDoublon(null);
  }

  async function checkDoublon() {
    if (isAjoutDocument) return;
    
    const numeroInterroge = form.numero_dossier.trim();
    const pathologieInterrogee = form.pathologie;
    if (!numeroInterroge || !pathologieInterrogee) return;
    setCheckingDoublon(true);
    try {
      const res = await client.get('/api/dossiers/verifier', {
        params: { pathologie: pathologieInterrogee, numero_dossier: numeroInterroge },
      });
      
      if (form.numero_dossier.trim() !== numeroInterroge || form.pathologie !== pathologieInterrogee) {
        return;
      }
      setDoublon(res.data.existe ? res.data : null);
    } catch {
    } finally {
      setCheckingDoublon(false);
    }
  }

  
  useEffect(() => {
    if (isAjoutDocument) return;
    if (form.numero_dossier.trim() && form.pathologie) checkDoublon();
  }, [form.pathologie]);

  function canGoNext() {
    
    if (step === 0) {
      return !doublon && form.numero_dossier.trim() && form.pathologie && form.date_diagnostic && form.date_inclusion;
    }
    if (step === 1) return !!form.type_document;
    if (step === 2) return !!form.type_entree && !!file;
    return true;
  }

  function goNext() {
    if (step === 0 && doublon) {
      setError('Ce numéro de dossier correspond à un patient déjà existant.');
      return;
    }
    if (!canGoNext()) {
      setError('Veuillez compléter les champs requis avant de continuer.');
      return;
    }
    setError('');
    setStep((s) => Math.min(s + 1, STEPS.length - 1));
  }

  function goBack() {
    setError('');
    setStep((s) => Math.max(s - 1, FIRST_STEP));
  }

  function handleFileSelect(f) {
    if (!f) return;
    setFile(f);
    setError('');
  }

  function handleDrop(e) {
    e.preventDefault();
    setDragOver(false);
    handleFileSelect(e.dataTransfer.files?.[0]);
  }

  async function handleSubmit() {
    setSubmitting(true);
    setError('');
    try {
      const body = new FormData();
      Object.entries(form).forEach(([k, v]) => body.append(k, v));
      body.append('fichier', file);
      
      if (isAjoutDocument) {
        body.append('pseudonyme_existant', existingPatient.pseudonyme);
      }

      const res = await client.post('/api/dossiers/creer', body, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      setResult(res.data);
      setTexteCorrige(res.data.texte_transcrit || '');
      onCreated?.(res.data);
    } catch (err) {
      setError(err.response?.data?.error || "Échec de la création du dossier.");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleValider() {
    if (!result?.document_id) {
      onClose();
      return;
    }
    setValidating(true);
    setError('');
    try {
      const res = await client.patch(`/api/dossiers/documents/${result.document_id}/texte`, {
        texte_transcrit: texteCorrige,
      });
      
      setShowToast(true);
      setTimeout(() => setShowToast(false), 1600);
      setTexteValide(true);
      onCreated?.({ ...result, ...res.data });
    } catch (err) {
      setError(err.response?.data?.error || 'Échec de la validation.');
    } finally {
      setValidating(false);
    }
  }

  const acceptAttr = form.type_entree === 'audio'
    ? '.wav,.mp3,.m4a,.flac,audio/*'
    : form.type_entree === 'scan'
      ? '.pdf,.png,.jpg,.jpeg,.tiff,image/*,application/pdf'
      : undefined;

  return (
    <div style={{
      
      position: 'fixed', top: 0, right: 0, bottom: 0, left: 248,
      background: 'var(--paper)', zIndex: 20,
      display: 'flex', flexDirection: 'column',
    }}>
      {/* Keyframes du spinner (bouton "Créer le dossier" pendant la
          transcription) — injectées une fois ici plutôt que dans un fichier
          CSS séparé, pour que ce fichier reste autonome (copier-coller vers
          un autre poste sans rien oublier). */}
      <style>{`@keyframes wizardSpin { to { transform: rotate(360deg); } }`}</style>

      {/* Toast de confirmation après "Valider" — visible ~1.2s avant que le
          wizard ne se ferme, pour remplacer la fermeture silencieuse
          d'avant (rien n'indiquait que l'enregistrement avait réussi). */}
      {showToast && (
        <div style={{
          position: 'absolute', top: 20, right: 32, zIndex: 30,
          display: 'flex', alignItems: 'center', gap: 8,
          padding: '12px 18px', borderRadius: 12,
          background: 'var(--teal-deep)', color: '#fff',
          fontSize: 13, fontWeight: 600, boxShadow: '0 8px 24px rgba(0,0,0,.18)',
        }}>
          <IconCheckCircle size={16} /> Document ajouté au dossier
        </div>
      )}

      {/* ---- En-tête ---- */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '20px 32px', borderBottom: '1px solid var(--line)', background: 'var(--card)',
      }}>
        <div>
          <h2 style={{ margin: 0, fontSize: 18, fontFamily: 'var(--font-display)' }}>
            {isAjoutDocument ? 'Ajouter un document' : 'Ajouter un patient'}
          </h2>
          <p style={{ margin: '2px 0 0', fontSize: 12.5, color: 'var(--slate-soft)' }}>
            {isAjoutDocument && (
              <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--teal-deep)' }}>
                {existingPatient.pseudonyme} —{' '}
              </span>
            )}
            Étape {STEPS_ACTIVES.indexOf(STEPS[step]) + 1} sur {STEPS_ACTIVES.length} — {STEPS[step]}
          </p>
        </div>
        <button onClick={onClose} aria-label="Fermer" style={{
          width: 38, height: 38, borderRadius: 10, border: '1.5px solid var(--line)',
          background: 'var(--card)', display: 'flex', alignItems: 'center', justifyContent: 'center',
          color: 'var(--slate)',
        }}>
          <IconX size={16} />
        </button>
      </div>

      {/* ---- Barre de progression ---- */}
      <div style={{ display: 'flex', gap: 6, padding: '14px 32px 0' }}>
        {STEPS.map((label, i) => (
          <div key={label} style={{
            flex: 1, height: 4, borderRadius: 4,
            background: i <= step ? 'var(--teal)' : 'var(--line)',
            transition: 'background .2s',
          }} />
        ))}
      </div>

      {/* ---- Corps ---- */}
      <div style={{ flex: 1, overflowY: 'auto', display: 'flex', justifyContent: 'center', padding: '32px 24px' }}>
        <div style={{ width: '100%', maxWidth: 620 }}>

          {step === 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
              <Field label="Numéro de dossier">
                <input
                  autoFocus
                  value={form.numero_dossier}
                  onChange={(e) => update('numero_dossier', e.target.value)}
                  onBlur={checkDoublon}
                  placeholder="ex. 2026-EPR-0142"
                  style={inputStyle}
                />
                {checkingDoublon && (
                  <p style={{ margin: '6px 0 0', fontSize: 11.5, color: 'var(--slate-soft)' }}>
                    Vérification…
                  </p>
                )}
                {doublon && (
                  <div style={{
                    marginTop: 10, padding: '12px 14px', borderRadius: 10,
                    border: '1.5px solid var(--error)', background: 'rgba(220,38,38,.06)',
                    display: 'flex', flexDirection: 'column', gap: 8,
                  }}>
                    <p style={{ margin: 0, fontSize: 12.5, color: 'var(--error)', display: 'flex', alignItems: 'center', gap: 6, fontWeight: 600 }}>
                      <IconAlert size={14} /> Patient déjà existant pour ce numéro de dossier.
                    </p>
                    <p style={{ margin: 0, fontSize: 12, color: 'var(--slate)' }}>
                      Pseudonyme :{' '}
                      <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 600 }}>{doublon.pseudonyme}</span>
                    </p>
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                      <button
                        onClick={() => onVoirDossier?.(doublon.pseudonyme)}
                        style={{
                          display: 'flex', alignItems: 'center', gap: 6,
                          padding: '7px 12px', borderRadius: 8,
                          border: '1.5px solid var(--error)', background: '#fff',
                          color: 'var(--error)', fontWeight: 600, fontSize: 12,
                        }}
                      >
                        Voir le dossier
                      </button>
                      <button
                        onClick={() => onSwitchToAjout?.(doublon)}
                        style={{
                          display: 'flex', alignItems: 'center', gap: 6,
                          padding: '7px 12px', borderRadius: 8, border: 'none',
                          background: 'var(--teal)', color: '#fff', fontWeight: 600, fontSize: 12,
                        }}
                      >
                        Ajouter un document à ce dossier
                      </button>
                    </div>
                  </div>
                )}
              </Field>

              <fieldset disabled={!!doublon} style={{ border: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 18, opacity: doublon ? 0.5 : 1 }}>
                <Field label="Pathologie">
                  <div style={{ display: 'flex', gap: 12 }}>
                    <PathologyCard
                      active={form.pathologie === 'SEP'}
                      icon={<IconActivity size={18} />}
                      label="SEP"
                      sublabel="Sclérose en plaques pédiatrique"
                      onClick={() => update('pathologie', 'SEP')}
                    />
                    <PathologyCard
                      active={form.pathologie === 'EPR'}
                      icon={<IconHeart size={18} />}
                      label="EPR"
                      sublabel="Épilepsie pharmacorésistante"
                      onClick={() => update('pathologie', 'EPR')}
                    />
                  </div>
                </Field>

                <div style={{ display: 'flex', gap: 12 }}>
                  <div style={{ flex: 1 }}>
                    <Field label="Date de diagnostic">
                      <input
                        type="date"
                        value={form.date_diagnostic}
                        onChange={(e) => update('date_diagnostic', e.target.value)}
                        style={inputStyle}
                      />
                    </Field>
                  </div>
                  <div style={{ flex: 1 }}>
                    <Field label="Date d'inclusion">
                      <input
                        type="date"
                        value={form.date_inclusion}
                        onChange={(e) => update('date_inclusion', e.target.value)}
                        style={inputStyle}
                      />
                    </Field>
                  </div>
                </div>
              </fieldset>
            </div>
          )}

          {step === 1 && (
            <Field label="Type de document">
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 10 }}>
                {TYPES_DOCUMENT.map((t) => (
                  <button
                    key={t.value}
                    onClick={() => update('type_document', t.value)}
                    style={{
                      padding: '14px 16px', borderRadius: 12, textAlign: 'left',
                      border: `1.5px solid ${form.type_document === t.value ? 'var(--teal)' : 'var(--line)'}`,
                      background: form.type_document === t.value ? 'var(--teal-tint)' : 'var(--card)',
                      color: form.type_document === t.value ? 'var(--teal-deep)' : 'var(--slate)',
                      fontSize: 13.5, fontWeight: 600,
                    }}
                  >
                    {t.label}
                  </button>
                ))}
              </div>
            </Field>
          )}

          {step === 2 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
              <Field label="Type d'entrée">
                <div style={{ display: 'flex', gap: 12 }}>
                  <PathologyCard
                    active={form.type_entree === 'audio'}
                    icon={<IconUpload size={18} />}
                    label="Audio"
                    sublabel="Dictée à transcrire automatiquement"
                    onClick={() => { update('type_entree', 'audio'); setFile(null); }}
                  />
                  <PathologyCard
                    active={form.type_entree === 'scan'}
                    icon={<IconFolder size={18} />}
                    label="Document scanné"
                    sublabel="PDF ou image"
                    onClick={() => { update('type_entree', 'scan'); setFile(null); }}
                  />
                </div>
              </Field>

              {/* Le bouton d'upload n'apparaît qu'une fois le type d'entrée
                  choisi (audio ou scan) — le type de document (visite,
                  admission, ...) est déjà connu depuis l'étape précédente. */}
              {form.type_entree && (
                <Field label={form.type_entree === 'audio' ? 'Fichier audio' : 'Fichier scanné'}>
                  <label
                    onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
                    onDragLeave={() => setDragOver(false)}
                    onDrop={handleDrop}
                    style={{
                      display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8,
                      padding: '32px 20px', borderRadius: 14, cursor: 'pointer',
                      border: `2px dashed ${dragOver ? 'var(--teal)' : 'var(--line)'}`,
                      background: dragOver ? 'var(--teal-tint)' : 'var(--card)',
                      textAlign: 'center',
                    }}
                  >
                    <IconUpload size={22} color="var(--teal-deep)" />
                    {file ? (
                      <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--teal-deep)' }}>{file.name}</span>
                    ) : (
                      <>
                        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--slate)' }}>
                          Glissez le fichier ici ou cliquez pour parcourir
                        </span>
                        <span style={{ fontSize: 11.5, color: 'var(--slate-soft)' }}>
                          {form.type_entree === 'audio' ? 'WAV, MP3, M4A, FLAC' : 'PDF, PNG, JPG, TIFF'}
                        </span>
                      </>
                    )}
                    <input
                      type="file"
                      accept={acceptAttr}
                      onChange={(e) => handleFileSelect(e.target.files?.[0])}
                      style={{ display: 'none' }}
                    />
                  </label>
                  {/* Réécoute immédiate du fichier choisi, avant même l'envoi
                      au serveur — permet de vérifier qu'on n'a pas
                      sélectionné le mauvais enregistrement. */}
                  {form.type_entree === 'audio' && audioUrl && (
                    <audio controls src={audioUrl} style={{ width: '100%', marginTop: 10 }} />
                  )}
                </Field>
              )}
            </div>
          )}

          {step === 3 && !result && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {isAjoutDocument && !form.date_diagnostic && (
                <p style={{
                  display: 'flex', alignItems: 'center', gap: 6, fontSize: 12.5,
                  color: 'var(--amber, #c8790f)', margin: 0,
                }}>
                  <IconAlert size={14} /> La date de diagnostic n'est pas renseignée pour ce dossier. Merci de la compléter ci-dessous pour pouvoir créer le document.
                </p>
              )}
              {isAjoutDocument && !form.date_inclusion && (
                <p style={{
                  display: 'flex', alignItems: 'center', gap: 6, fontSize: 12.5,
                  color: 'var(--amber, #c8790f)', margin: 0,
                }}>
                  <IconAlert size={14} /> La date d'inclusion n'est pas renseignée pour ce dossier. Merci de la compléter ci-dessous pour pouvoir créer le document.
                </p>
              )}
              <SummaryRow label="Numéro de dossier" value={form.numero_dossier} />
              <SummaryRow label="Pathologie" value={form.pathologie} />
              {isAjoutDocument && !form.date_diagnostic ? (
                <div style={{
                  display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 14px',
                  background: 'var(--card)', border: '1px solid var(--amber, #c8790f)', borderRadius: 10, fontSize: 13, gap: 12,
                }}>
                  <span style={{ color: 'var(--slate-soft)', whiteSpace: 'nowrap' }}>Date de diagnostic</span>
                  <input
                    type="date"
                    value={form.date_diagnostic}
                    onChange={(e) => update('date_diagnostic', e.target.value)}
                    style={{ ...inputStyle, padding: '6px 10px', maxWidth: 170 }}
                  />
                </div>
              ) : (
                <SummaryRow label="Date de diagnostic" value={form.date_diagnostic} />
              )}
              {isAjoutDocument && !form.date_inclusion ? (
                <div style={{
                  display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 14px',
                  background: 'var(--card)', border: '1px solid var(--amber, #c8790f)', borderRadius: 10, fontSize: 13, gap: 12,
                }}>
                  <span style={{ color: 'var(--slate-soft)', whiteSpace: 'nowrap' }}>Date d'inclusion</span>
                  <input
                    type="date"
                    value={form.date_inclusion}
                    onChange={(e) => update('date_inclusion', e.target.value)}
                    style={{ ...inputStyle, padding: '6px 10px', maxWidth: 170 }}
                  />
                </div>
              ) : (
                <SummaryRow label="Date d'inclusion" value={form.date_inclusion} />
              )}
              <SummaryRow label="Type de document" value={TYPES_DOCUMENT.find((t) => t.value === form.type_document)?.label} />
              <SummaryRow label="Type d'entrée" value={form.type_entree === 'audio' ? 'Audio (transcription automatique)' : 'Document scanné'} />
              <SummaryRow label="Fichier" value={file?.name} />
              {form.type_entree === 'audio' && audioUrl && (
                <audio controls src={audioUrl} style={{ width: '100%' }} />
              )}
              <p style={{ fontSize: 12, color: 'var(--slate-soft)', marginTop: 6 }}>
                {form.type_entree === 'audio'
                  ? "L'audio sera transcrit automatiquement (WhisperX) à la création du dossier."
                  : "Le document sera traité automatiquement (OCR) à la création du dossier."}
              </p>
            </div>
          )}

          {step === 3 && result && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14, alignItems: 'center', textAlign: 'center', paddingTop: 20 }}>
              <div style={{
                width: 52, height: 52, borderRadius: 26, background: 'var(--teal-tint)',
                color: 'var(--teal-deep)', display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}>
                <IconCheckCircle size={26} />
              </div>
              <h3 style={{ margin: 0, fontSize: 16, fontFamily: 'var(--font-display)' }}>Dossier créé</h3>
              <p style={{ margin: 0, fontSize: 12.5, color: 'var(--slate)', fontFamily: 'var(--font-mono)' }}>
                {result.numero_dossier}
              </p>
              {result.texte_transcrit != null && (
                <div style={{
                  width: '100%', textAlign: 'left', background: 'var(--card)', border: '1px solid var(--line)',
                  borderRadius: 12, padding: 16, marginTop: 8,
                }}>
                  <p style={{ margin: '0 0 6px', fontSize: 11.5, fontWeight: 700, textTransform: 'uppercase', color: 'var(--slate-soft)' }}>
                    Texte transcrit — vérifiez et corrigez si besoin
                  </p>
                  {audioUrl && (
                    <audio controls src={audioUrl} style={{ width: '100%', marginBottom: 10 }} />
                  )}
                  <textarea
                    value={texteCorrige}
                    onChange={(e) => setTexteCorrige(e.target.value)}
                    rows={8}
                    style={{
                      width: '100%', fontSize: 13, lineHeight: 1.6, color: 'var(--slate)',
                      border: '1.5px solid var(--line)', borderRadius: 8, padding: 10,
                      fontFamily: 'inherit', boxSizing: 'border-box', resize: 'vertical',
                      background: 'var(--paper)',
                    }}
                  />
                </div>
              )}
              {result.statut === 'erreur_transcription' && (
                <p style={{ fontSize: 12.5, color: 'var(--error)', display: 'flex', alignItems: 'center', gap: 5 }}>
                  <IconAlert size={13} /> La transcription automatique a échoué ; le fichier a bien été enregistré. Vous pouvez quitter sans valider — le document restera signalé comme non transcrit plutôt que d'apparaître à tort comme "en attente d'extraction".
                </p>
              )}

              {result.pseudonyme && texteValide && (
                <div style={{ width: '100%', textAlign: 'left', marginTop: 4 }}>
                  <p style={{ margin: '0 0 6px', fontSize: 11.5, fontWeight: 700, textTransform: 'uppercase', color: 'var(--slate-soft)' }}>
                    Identité du patient
                  </p>
                  <ExtractionCoordonneesPanel
                    documentId={result.document_id}
                    texte={texteCorrige}
                    pseudonymeCible={result.pseudonyme}
                    label="Extraire données patient"
                  />
                </div>
              )}
            </div>
          )}

          {error && (
            <p style={{ fontSize: 12.5, color: 'var(--error)', display: 'flex', alignItems: 'center', gap: 6, marginTop: 16 }}>
              <IconAlert size={14} /> {error}
            </p>
          )}
        </div>
      </div>

      {/* ---- Pied de page / navigation ---- */}
      <div style={{
        display: 'flex', justifyContent: 'space-between', padding: '18px 32px',
        borderTop: '1px solid var(--line)', background: 'var(--card)',
      }}>
        {result ? (
          texteValide ? (
            <button onClick={onClose} style={{ ...primaryBtn, marginLeft: 'auto' }}>
              Terminer
            </button>
          ) : result.statut === 'erreur_transcription' ? (
            
            <button onClick={onClose} style={{ ...secondaryBtn, marginLeft: 'auto' }}>
              Quitter sans valider
            </button>
          ) : (
            <button onClick={handleValider} disabled={validating} style={{ ...primaryBtn, marginLeft: 'auto', opacity: validating ? 0.7 : 1 }}>
              {validating ? <><Spinner /> Validation…</> : 'Valider'}
            </button>
          )
        ) : (
          <>
            <button
              onClick={step === FIRST_STEP ? onClose : goBack}
              style={secondaryBtn}
            >
              <IconArrowLeft size={14} /> {step === FIRST_STEP ? 'Annuler' : 'Retour'}
            </button>

            {step < STEPS.length - 1 ? (
              <button
                onClick={goNext}
                disabled={step === 0 && !!doublon}
                style={{ ...primaryBtn, opacity: (step === 0 && doublon) ? 0.5 : 1, cursor: (step === 0 && doublon) ? 'not-allowed' : 'pointer' }}
              >
                Suivant <IconArrowRight size={14} />
              </button>
            ) : (
              <button
                onClick={handleSubmit}
                disabled={submitting || !form.date_diagnostic || !form.date_inclusion}
                style={{ ...primaryBtn, opacity: (submitting || !form.date_diagnostic || !form.date_inclusion) ? 0.5 : 1, cursor: (!form.date_diagnostic || !form.date_inclusion) ? 'not-allowed' : 'pointer' }}
              >
                {submitting ? (
                  <>
                    <Spinner />
                    {form.type_entree === 'audio'
                      ? 'Transcription en cours (Whisper)…'
                      : form.type_entree === 'scan'
                        ? 'Extraction en cours (OCR)…'
                        : 'Création…'}
                  </>
                ) : 'Créer le dossier'}
              </button>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function Spinner() {
  return (
    <span style={{
      width: 13, height: 13, borderRadius: '50%',
      border: '2px solid rgba(255,255,255,.4)', borderTopColor: '#fff',
      display: 'inline-block', animation: 'wizardSpin .7s linear infinite',
    }} />
  );
}

function Field({ label, children }) {
  return (
    <div>
      <label style={{ display: 'block', fontSize: 12.5, fontWeight: 600, color: 'var(--slate)', marginBottom: 8 }}>
        {label}
      </label>
      {children}
    </div>
  );
}

function PathologyCard({ active, icon, label, sublabel, onClick }) {
  return (
    <button
      onClick={onClick}
      style={{
        flex: 1, display: 'flex', flexDirection: 'column', gap: 6, alignItems: 'flex-start',
        padding: '16px 18px', borderRadius: 14, textAlign: 'left',
        border: `1.5px solid ${active ? 'var(--teal)' : 'var(--line)'}`,
        background: active ? 'var(--teal-tint)' : 'var(--card)',
      }}
    >
      <span style={{ color: 'var(--teal-deep)' }}>{icon}</span>
      <span style={{ fontSize: 14, fontWeight: 700, color: active ? 'var(--teal-deep)' : 'var(--ink)' }}>{label}</span>
      <span style={{ fontSize: 11.5, color: 'var(--slate-soft)' }}>{sublabel}</span>
    </button>
  );
}

function SummaryRow({ label, value }) {
  return (
    <div style={{
      display: 'flex', justifyContent: 'space-between', padding: '10px 14px',
      background: 'var(--card)', border: '1px solid var(--line)', borderRadius: 10, fontSize: 13,
    }}>
      <span style={{ color: 'var(--slate-soft)' }}>{label}</span>
      <span style={{ fontWeight: 600, color: 'var(--ink)' }}>{value || '—'}</span>
    </div>
  );
}

const inputStyle = {
  width: '100%', padding: '11px 12px', borderRadius: 10, border: '1.5px solid var(--line)',
  fontSize: 13.5, background: 'var(--paper)', boxSizing: 'border-box',
};

const primaryBtn = {
  display: 'flex', alignItems: 'center', gap: 8, padding: '11px 20px', borderRadius: 10,
  border: 'none', background: 'var(--teal)', color: '#fff', fontWeight: 600, fontSize: 13.5,
};

const secondaryBtn = {
  display: 'flex', alignItems: 'center', gap: 8, padding: '11px 20px', borderRadius: 10,
  border: '1.5px solid var(--line)', background: 'var(--card)', color: 'var(--teal-deep)', fontWeight: 600, fontSize: 13.5,
};