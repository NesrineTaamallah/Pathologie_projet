import { useState } from 'react';
import client from '../api/client';
import { IconRefresh, IconAlert, IconX, IconCheckCircle } from './Icons';

/**
 * Modal d'extraction des ENTITÉS MÉDICALES (11 tables SEP / 18 tables EPR),
 * distincte de ExtractionModal.jsx (qui gère les coordonnées patient).
 *
 * Différences volontaires par rapport à ExtractionModal :
 *  - un seul appel "extraireTout" qui lance TOUTES les tables du registre
 *    (le microservice orchestre déjà chunking/vote/vérification en interne) ;
 *  - le clinicien corrige les valeurs affichées, mais "Valider" ne fait
 *    AUCUNE écriture dans une table structurée en base : contrairement aux
 *    coordonnées, on ne persiste pas le contenu clinique extrait ici.
 *    "Valider" marque seulement le dossier comme "revu" (fait disparaître
 *    l'alerte dashboard) et permet d'exporter/copier le JSON corrigé.
 */
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

export default function ExtractionEntitesModal({ pseudonyme, onClose, onReviewed }) {
  const [statut, setStatut] = useState('idle'); // idle | loading | done | error
  const [erreur, setErreur] = useState('');
  const [resultat, setResultat] = useState(null); // { <table>: obj|[...] , ... } -- UNIQUEMENT les tables cliniques
  const [aVerifier, setAVerifier] = useState([]); // [{ table, issues: [...] }, ...]
  const [registre, setRegistre] = useState(null);

  async function lancerExtraction() {
    setStatut('loading');
    setErreur('');
    try {
      const res = await client.post('/api/extraction/entites', { pseudonyme });
      // CORRECTIF : res.data a la forme { pseudonyme, registre, tables: {...}, a_verifier: [...] }.
      // On ne spread PAS tout le reste dans "resultat" -- on extrait
      // explicitement "tables" (les vraies tables cliniques SEP/EPR) et
      // "a_verifier" (les avertissements de validation) séparément, sinon
      // "tables" et "a_verifier" eux-mêmes finissaient affichés comme si
      // c'étaient des tables cliniques.
      setRegistre(res.data.registre);
      setResultat(res.data.tables || {});
      setAVerifier(res.data.a_verifier || []);
      setStatut('done');
    } catch (err) {
      setErreur(err.response?.data?.error || "Échec de l'extraction des entités médicales.");
      setStatut('error');
    }
  }

  function updateChampObjet(tableKey, champ, valeur) {
    setResultat((r) => ({ ...r, [tableKey]: { ...r[tableKey], [champ]: valeur } }));
  }

  function updateChampListe(tableKey, index, champ, valeur) {
    setResultat((r) => {
      const liste = [...r[tableKey]];
      liste[index] = { ...liste[index], [champ]: valeur };
      return { ...r, [tableKey]: liste };
    });
  }

  function supprimerOccurrence(tableKey, index) {
    setResultat((r) => {
      const liste = [...r[tableKey]];
      liste.splice(index, 1);
      return { ...r, [tableKey]: liste };
    });
  }

  async function marquerRevu() {
    // Aucune donnée clinique envoyée ici — seulement la confirmation que le
    // clinicien a relu/corrigé le résultat à l'écran. Le flag côté serveur
    // (documents_bruts.entites_extraites) a déjà été posé lors de l'appel
    // POST /api/extraction/entites ; on notifie juste le parent pour rafraîchir l'UI.
    onReviewed?.(resultat);
    onClose();
  }

  function copierJSON() {
    navigator.clipboard?.writeText(JSON.stringify({ registre, ...resultat }, null, 2));
  }

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, background: 'rgba(18,42,48,.55)', backdropFilter: 'blur(2px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 50, padding: 20,
        // NB : ce conteneur ne doit PAS avoir son propre overflow scrollable :
        // avec deux ancêtres en overflowY:auto imbriqués, la molette/le drag
        // sont parfois captés par celui-ci au lieu de la carte interne, et le
        // bas de la carte (derniers champs / boutons) reste inaccessible.
      }}
    >
      <div onClick={(e) => e.stopPropagation()} className="extraction-modal-card" style={{
        background: 'var(--card)', borderRadius: 16, width: 820, maxWidth: '100%', maxHeight: 'calc(100vh - 40px)',
        overflowY: 'auto', overscrollBehavior: 'contain', padding: '22px 24px 24px', boxShadow: '0 20px 50px -10px rgba(18,42,48,.35)',
        display: 'flex', flexDirection: 'column', gap: 14,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
          <div>
            <h3 style={{ margin: 0, fontSize: 16, fontFamily: 'var(--font-display)' }}>
              Extraire les entités médicales
            </h3>
            <p style={{ margin: '4px 0 0', fontSize: 12, color: 'var(--slate)', fontFamily: 'var(--font-mono)' }}>
              {pseudonyme}{registre ? ` · Registre ${registre}` : ''}
            </p>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <button
              type="button"
              onClick={lancerExtraction}
              disabled={statut === 'loading'}
              style={{
                width: 'auto', margin: 0, padding: '8px 14px', display: 'inline-flex', alignItems: 'center', gap: 7,
                borderRadius: 9, border: '1.5px solid var(--teal)',
                background: statut === 'done' ? 'var(--paper)' : 'var(--teal)',
                color: statut === 'done' ? 'var(--slate)' : '#fff',
                fontWeight: 600, fontSize: 12.5, opacity: statut === 'loading' ? 0.7 : 1,
              }}
            >
              <IconRefresh size={13} />
              {statut === 'loading' ? 'Extraction en cours (toutes tables)…' : statut === 'done' ? 'Ré-extraire' : 'Extraire'}
            </button>
            <button type="button" onClick={onClose} title="Fermer"
              style={{ width: 'auto', margin: 0, padding: 6, background: 'transparent', border: 'none', boxShadow: 'none', color: 'var(--slate-soft)' }}>
              <IconX size={16} />
            </button>
          </div>
        </div>

        {statut === 'idle' && (
          <p className="hint" style={{ margin: 0, fontSize: 12.5 }}>
            Lance l'extraction pour analyser l'ensemble du texte transcrit de ce dossier
            (toutes les tables cliniques du registre {registre || ''}).
          </p>
        )}

        {erreur && (
          <p className="error" style={{ margin: 0, fontSize: 12.5, display: 'flex', alignItems: 'center', gap: 5 }}>
            <IconAlert size={13} /> {erreur}
          </p>
        )}

        {aVerifier.length > 0 && (
          <div style={{
            borderRadius: 12, border: '1.5px solid #f0c36d', background: '#fdf6e6',
            padding: '10px 14px', display: 'flex', flexDirection: 'column', gap: 6,
          }}>
            <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--ink)', display: 'flex', alignItems: 'center', gap: 6 }}>
              <IconAlert size={13} /> À vérifier ({aVerifier.reduce((n, t) => n + t.issues.length, 0)} point{aVerifier.reduce((n, t) => n + t.issues.length, 0) > 1 ? 's' : ''})
            </span>
            {aVerifier.map((t) => (
              <div key={t.table} style={{ fontSize: 11.5 }}>
                <strong style={{ color: 'var(--slate)' }}>{t.table}</strong>
                <ul style={{ margin: '2px 0 0', paddingLeft: 18, color: 'var(--slate-soft)' }}>
                  {t.issues.map((issue, i) => <li key={i}>{issue}</li>)}
                </ul>
              </div>
            ))}
          </div>
        )}

        {resultat && Object.entries(resultat).map(([tableKey, contenu]) => (
          <TableBloc
            key={tableKey}
            tableKey={tableKey}
            contenu={contenu}
            onChampObjet={(champ, val) => updateChampObjet(tableKey, champ, val)}
            onChampListe={(idx, champ, val) => updateChampListe(tableKey, idx, champ, val)}
            onSupprimer={(idx) => supprimerOccurrence(tableKey, idx)}
          />
        ))}

        {resultat && (
          <div style={{ display: 'flex', gap: 8, marginTop: 4, position: 'sticky', bottom: 0, background: 'var(--card)', paddingTop: 8 }}>
            <button type="button" onClick={marquerRevu}
              style={{ width: 'auto', margin: 0, padding: '8px 14px', borderRadius: 9, border: 'none', background: 'var(--teal)', color: '#fff', fontWeight: 600, fontSize: 12.5, display: 'inline-flex', alignItems: 'center', gap: 6 }}>
              <IconCheckCircle size={13} /> Marquer comme relu
            </button>
            <button type="button" onClick={copierJSON}
              style={{ width: 'auto', margin: 0, padding: '8px 14px', borderRadius: 9, border: '1.5px solid var(--line)', background: 'var(--paper)', color: 'var(--slate)', fontWeight: 600, fontSize: 12.5 }}>
              Copier le JSON corrigé
            </button>
          </div>
        )}
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
  const totalementVide = estListe
    ? contenu.length === 0
    : objetEntierementVide(contenu);
  return (
    <div style={{ borderRadius: 12, border: '1.5px solid var(--line)', background: 'var(--paper)', overflow: 'hidden' }}>
      <div style={{ padding: '10px 14px', background: 'var(--card)', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ fontSize: 12.5, fontWeight: 700, color: 'var(--ink)' }}>{tableKey}</span>
        {estListe && (
          <span style={{ fontSize: 11, color: 'var(--slate-soft)' }}>
            {contenu.length} occurrence{contenu.length > 1 ? 's' : ''}
          </span>
        )}
        {totalementVide && (
          <span style={{
            fontSize: 10.5, fontWeight: 600, color: '#8a6d1a', background: '#fdf6e6',
            border: '1px solid #f0c36d', borderRadius: 999, padding: '2px 8px', marginLeft: 'auto',
          }}>
            Rien détecté
          </span>
        )}
      </div>
      <div style={{ padding: '10px 14px', display: 'flex', flexDirection: 'column', gap: 10 }}>
        {estListe
          ? (contenu.length === 0
              ? <p className="hint" style={{ margin: 0, fontSize: 12 }}>Aucune occurrence détectée — le clinicien peut en ajouter une manuellement si nécessaire.</p>
              : contenu.map((occurrence, idx) => (
                  <OccurrenceForm
                    key={idx}
                    occurrence={occurrence}
                    onChange={(champ, val) => onChampListe(idx, champ, val)}
                    onSupprimer={() => onSupprimer(idx)}
                  />
                )))
          : <OccurrenceForm occurrence={contenu} onChange={onChampObjet} />}
      </div>
    </div>
  );
}

function OccurrenceForm({ occurrence, onChange, onSupprimer }) {
  const champs = Object.keys(occurrence || {}).filter((k) => !k.startsWith('_') && !k.startsWith('evidence_span_'));
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, paddingBottom: 8, borderBottom: onSupprimer ? '1px dashed var(--line)' : 'none' }}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
        {champs.map((champ) => {
          const valeurBrute = occurrence[champ];
          const estVide = valeurBrute === 'null' || valeurBrute == null;
          const evidence = occurrence[`evidence_span_${champ}`];
          const aEvidence = typeof evidence === 'string' && evidence.trim().length > 0;
          return (
            <div key={champ} style={{
              display: 'flex', flexDirection: 'column', gap: 3, padding: 6, borderRadius: 8,
              background: estVide ? '#fbfcfe' : '#fff', border: '1px solid var(--line)',
            }}>
              <label style={{ fontSize: 11, fontWeight: 700, color: 'var(--ink)' }}>{champ}</label>
              <input
                value={estVide ? '' : valeurBrute}
                placeholder={valeurBrute === 'null' ? 'non mentionné dans le texte' : valeurBrute === 'NA' ? 'NA' : '—'}
                onChange={(e) => onChange(champ, e.target.value)}
                style={{ padding: '6px 8px', borderRadius: 7, border: '1.5px solid var(--line)', fontSize: 11.5, background: '#fff', color: 'var(--ink)' }}
              />
              {aEvidence ? (
                <p style={{ margin: 0, fontSize: 10.5, color: 'var(--slate)', fontStyle: 'italic', lineHeight: 1.35 }}>
                  « {evidence} »
                </p>
              ) : (
                <p style={{ margin: 0, fontSize: 10.5, color: 'var(--slate-soft)' }}>
                  Aucun passage du texte identifié pour ce champ — à vérifier manuellement.
                </p>
              )}
            </div>
          );
        })}
      </div>
      {onSupprimer && (
        <button type="button" onClick={onSupprimer}
          style={{ width: 'auto', alignSelf: 'flex-end', margin: 0, padding: '4px 9px', borderRadius: 7, border: '1px solid var(--line)', background: 'transparent', color: 'var(--slate-soft)', fontSize: 10.5 }}>
          Retirer cette occurrence
        </button>
      )}
    </div>
  );
}