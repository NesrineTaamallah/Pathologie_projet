

CREATE TABLE IF NOT EXISTS coordonnee_patient (
    pseudonyme          VARCHAR(255) PRIMARY KEY REFERENCES patients(pseudonyme),

    -- Champs identifiants, tous chiffrés (TEXT car payload chiffré variable)
    numero_dossier         TEXT NOT NULL,
    nom_prenom              TEXT NOT NULL,
    date_naissance            TEXT,
    adresse                     TEXT,
    origine                      TEXT,
    telephone                     TEXT,
    cin                             TEXT,
    num_cnam                         TEXT,
    nom_prenom_pere                    TEXT,
    nom_prenom_mere                      TEXT,
    frere                                   TEXT,
    soeur                                     TEXT,
    autre_antecedent                            TEXT,

    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by            UUID REFERENCES users(id),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_coordonnee_patient_created_by
  ON coordonnee_patient(created_by);

-- Garde updated_at à jour automatiquement
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_coordonnee_patient_updated_at ON coordonnee_patient;
CREATE TRIGGER trg_coordonnee_patient_updated_at
  BEFORE UPDATE ON coordonnee_patient
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

  -- Retire les contraintes NOT NULL sur numero_dossier et nom_prenom :
-- ces champs sont parfois absents du texte source (OCR/ASR) et le
-- dossier doit pouvoir être enregistré quand même, à compléter plus tard.

ALTER TABLE coordonnee_patient ALTER COLUMN numero_dossier DROP NOT NULL;
ALTER TABLE coordonnee_patient ALTER COLUMN nom_prenom DROP NOT NULL;