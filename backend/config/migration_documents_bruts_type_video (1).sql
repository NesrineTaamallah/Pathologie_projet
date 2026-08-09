-- ============================================================================
-- Migration : ajoute le type d'entrée "video" (ex. enregistrement EEG vidéo,
-- stocké tel quel sans transcription/OCR) et le statut "stocke" associé.
-- À exécuter une fois sur les bases déjà créées avant cet ajout.
-- ============================================================================

ALTER TABLE documents_bruts DROP CONSTRAINT IF EXISTS documents_bruts_type_entree_check;
ALTER TABLE documents_bruts ADD CONSTRAINT documents_bruts_type_entree_check
    CHECK (type_entree IN ('audio', 'scan', 'video'));

ALTER TABLE documents_bruts DROP CONSTRAINT IF EXISTS documents_bruts_statut_check;
ALTER TABLE documents_bruts ADD CONSTRAINT documents_bruts_statut_check
    CHECK (statut IN (
        'en_attente',            -- scan pas encore traité (OCR à venir)
        'transcrit',              -- audio transcrit / scan avec OCR réussi
        'erreur_transcription',
        'stocke',                 -- vidéo brute stockée sans traitement
        'pseudonymise'            -- traité par l'étape suivante (pseudonyme + entités attribués)
    ));
