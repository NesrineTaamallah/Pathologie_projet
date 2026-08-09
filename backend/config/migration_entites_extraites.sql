-- À exécuter avec ton script existant : node scripts/run-migration.js config/migration_entites_extraites.sql
ALTER TABLE documents_bruts
  ADD COLUMN IF NOT EXISTS entites_extraites boolean NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS idx_documents_bruts_entites_en_attente
  ON documents_bruts (pathologie, pseudonyme)
  WHERE texte_transcrit IS NOT NULL AND entites_extraites = false;
