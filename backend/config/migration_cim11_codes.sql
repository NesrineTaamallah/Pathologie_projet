-- Migration : intégration CIM-11 (ICD-11) avec recherche floue (fuzzy search)
-- À exécuter APRES les migrations existantes (via backend/scripts/run-migration.js)

-- 1. Extension pg_trgm pour la similarité de texte (tolère fautes, accents, variantes)
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;

-- 1bis. unaccent() est marquée STABLE par PostgreSQL (dépend du search_path),
-- ce qui est refusé dans une colonne générée (GENERATED ALWAYS ... STORED)
-- qui exige une expression IMMUTABLE. On crée donc un wrapper IMMUTABLE
-- qui fixe explicitement le dictionnaire 'unaccent'.
CREATE OR REPLACE FUNCTION immutable_unaccent(text)
RETURNS text AS $$
  SELECT unaccent('public.unaccent'::regdictionary, $1)
$$ LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT;

-- 2. Table des codes CIM-11
CREATE TABLE IF NOT EXISTS cim11_codes (
  id            SERIAL PRIMARY KEY,
  chapter       VARCHAR(10),
  code          VARCHAR(20),          -- peut être vide pour les "block" (regroupements)
  title         TEXT NOT NULL,
  class_kind    VARCHAR(20),          -- chapter | block | category
  parent_code   VARCHAR(20),
  uri           TEXT,
  definition    TEXT,
  -- colonne dédiée à la recherche : titre + définition, sans accents, en minuscule
  search_text   TEXT GENERATED ALWAYS AS (
                  immutable_unaccent(lower(coalesce(title, '') || ' ' || coalesce(definition, '')))
                ) STORED
);

-- 3. Index trigram pour la recherche floue rapide (ILIKE '%...%' et similarity())
CREATE INDEX IF NOT EXISTS idx_cim11_search_trgm
  ON cim11_codes USING gin (search_text gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_cim11_title_trgm
  ON cim11_codes USING gin (immutable_unaccent(lower(title)) gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_cim11_code ON cim11_codes (code);
CREATE INDEX IF NOT EXISTS idx_cim11_class_kind ON cim11_codes (class_kind);

-- 4. Table optionnelle : synonymes / variantes cliniques (fautes fréquentes,
--    orthographes alternatives, abréviations) pour renforcer la tolérance
--    (ex: "epilepsie" <-> "épilepsie", "sep" <-> "sclérose en plaques")
CREATE TABLE IF NOT EXISTS cim11_synonymes (
  id        SERIAL PRIMARY KEY,
  cim11_id  INTEGER REFERENCES cim11_codes(id) ON DELETE CASCADE,
  synonyme  TEXT NOT NULL,
  search_text TEXT GENERATED ALWAYS AS (immutable_unaccent(lower(synonyme))) STORED
);

CREATE INDEX IF NOT EXISTS idx_cim11_syn_trgm
  ON cim11_synonymes USING gin (search_text gin_trgm_ops);

-- 5. Table de liaison : rattacher un code CIM-11 à une entité extraite
--    (résultat de l'extraction Qwen2.5-3B) pour un patient/document donné
CREATE TABLE IF NOT EXISTS entites_cim11 (
  id                SERIAL PRIMARY KEY,
  entite_extraite_id INTEGER, -- FK logique vers votre table entites_extraites existante
  pseudonyme        VARCHAR(50),
  terme_source      TEXT NOT NULL,      -- texte brut extrait (ex: "epilepsie focale")
  cim11_id          INTEGER REFERENCES cim11_codes(id),
  code_cim11        VARCHAR(20),
  titre_cim11       TEXT,
  score_similarite  NUMERIC(4,3),       -- score de similarité trigram (0 à 1)
  valide_par_clinicien BOOLEAN DEFAULT FALSE,
  date_creation     TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_entites_cim11_pseudonyme ON entites_cim11(pseudonyme);