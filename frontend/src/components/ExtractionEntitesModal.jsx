import { useEffect, useState } from 'react';
import client from '../api/client';
import ExtractionEntitesPanel from './ExtractionEntitesPanel';
import { IconX } from './Icons';

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

const scrollbarStyleId = 'extraction-modal-scrollbar-style';
if (typeof document !== 'undefined' && !document.getElementById(scrollbarStyleId)) {
  const style = document.createElement('style');
  style.id = scrollbarStyleId;
  style.textContent = `
    .extraction-modal-card { scrollbar-width: thin; scrollbar-color: var(--line) transparent; }
    .extraction-modal-card::-webkit-scrollbar { width: 9px; }
    .extraction-modal-card::-webkit-scrollbar-track { background: transparent; }
    .extraction-modal-card::-webkit-scrollbar-thumb { background: var(--line); border-radius: 6px; }
    .extraction-modal-card::-webkit-scrollbar-thumb:hover { background: var(--slate-soft); }
  `;
  document.head.appendChild(style);
}

/**
 * Modal d'extraction des ENTITÉS MÉDICALES, "texte par texte" : reprend
 * exactement le modèle de ExtractionModal.jsx (coordonnées) — on liste les
 * documents transcrits de ce patient pas encore passés dans le pipeline
 * d'entités, un par un, et pour chacun on affiche le texte source à côté des
 * tables extraites (ExtractionEntitesPanel), que le clinicien corrige puis
 * enregistre réellement en base (POST /api/entites). Le document disparaît
 * de la liste une fois enregistré ; la modal se ferme quand tout est fait.
 */
export default function ExtractionEntitesModal({ pseudonyme, onClose, onAllDone, onReviewed }) {
  const [documents, setDocuments] = useState(null);
  const [loadError, setLoadError] = useState('');

  useEffect(() => {
    client.get(`/api/extraction/entites/${pseudonyme}/documents-non-extraits`)
      .then((res) => setDocuments(res.data.documents || []))
      .catch(() => setLoadError('Impossible de charger les documents de ce patient.'));
  }, [pseudonyme]);

  function retirerDocument(docId) {
    setDocuments((docs) => {
      const reste = docs.filter((d) => d.id !== docId);
      if (reste.length === 0) {
        onAllDone?.();
        onReviewed?.();
      }
      return reste;
    });
  }

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, background: 'rgba(18,42,48,.55)', backdropFilter: 'blur(2px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 50, padding: 20,
      }}
    >
      <div onClick={(e) => e.stopPropagation()} className="extraction-modal-card" style={{
        background: 'var(--card)', borderRadius: 16, width: 900, maxWidth: '100%', maxHeight: 'calc(100vh - 40px)',
        overflowY: 'auto', overscrollBehavior: 'contain', padding: '22px 24px 24px', boxShadow: '0 20px 50px -10px rgba(18,42,48,.35)',
        display: 'flex', flexDirection: 'column', gap: 14,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, flexShrink: 0 }}>
          <div>
            <h3 style={{ margin: 0, fontSize: 16, fontFamily: 'var(--font-display)' }}>
              Extraire les entités médicales
            </h3>
            <p style={{ margin: '4px 0 0', fontSize: 12, color: 'var(--slate)', fontFamily: 'var(--font-mono)' }}>
              {pseudonyme}
            </p>
          </div>
          <button type="button" onClick={onClose} title="Fermer"
            style={{ width: 'auto', margin: 0, padding: 6, background: 'transparent', border: 'none', boxShadow: 'none', color: 'var(--slate-soft)' }}>
            <IconX size={16} />
          </button>
        </div>

        {loadError && <p className="error" style={{ margin: 0, fontSize: 12 }}>{loadError}</p>}
        {documents === null && !loadError && (
          <p className="hint" style={{ margin: 0, fontSize: 12.5 }}>Chargement des documents…</p>
        )}
        {documents && documents.length === 0 && (
          <p className="hint" style={{ margin: 0, fontSize: 12.5 }}>
            Tous les documents de ce patient ont déjà été extraits.
          </p>
        )}

        {documents && documents.map((doc) => (
          <div key={doc.id} style={{
            borderRadius: 12, border: '1.5px solid var(--line)', background: 'var(--paper)', overflow: 'hidden',
            flexShrink: 0,
          }}>
            <div style={{ padding: '12px 14px', display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 10 }}>
              <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--ink)' }}>
                {TYPE_DOCUMENT_LABELS[doc.type_document] || doc.type_document}
                <span style={{ fontWeight: 400, color: 'var(--slate-soft)' }}> · {doc.type_entree === 'audio' ? 'Audio' : 'Scan'}</span>
              </span>
              <span style={{ fontSize: 11, color: 'var(--slate-soft)' }}>Ajouté le {fmtDate(doc.created_at)}</span>
            </div>
            <div style={{ padding: '0 14px 14px' }}>
              <ExtractionEntitesPanel
                documentId={doc.id}
                autoStart
                label="Extraire les entités de ce document"
                onSaved={() => retirerDocument(doc.id)}
              />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}