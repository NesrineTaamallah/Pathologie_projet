import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text
from lifelines import CoxPHFitter, KaplanMeierFitter
from lifelines.statistics import multivariate_logrank_test
import matplotlib.pyplot as plt

DB_URI = "postgresql://user:password@localhost:5432/registre_neuroexo"

ANALYSIS_MODE = "univariate"   

AGE_VARIABLE_MODE = "categorical" 

CANDIDATE_COVARIATES = [
    "etiologie_structurelle",
    "crises_types_multiples",
    "freq_crises_baseline_mois",
    "irm_anormale",
    "eeg_anormal",
    "atcd_perinataux",
    "developpement_psychomoteur_avant_crises",
    "presence_regression",
]

SELECTED_COVARIATES = [
    # "etiologie_structurelle",
    # "crises_types_multiples",
    # "freq_crises_baseline_mois",
]

OUTPUT_DIR = "/mnt/user-data/outputs"

DATASET_QUERY = """
WITH base AS (
    SELECT
        ic.pseudonyme,
        ic.age_debut_crises_mois,
        ic.age_diagnostic_pharmacoresistance_mois,
        pr.statut_pharmacoresistance_confirme,
        su.duree_suivi_mois,
        su.statut_dernier_suivi
    FROM epr_identification_clinique ic
    LEFT JOIN epr_pharmacoresistance pr ON pr.pseudonyme = ic.pseudonyme
    LEFT JOIN epr_suivi su ON su.pseudonyme = ic.pseudonyme
    WHERE ic.age_debut_crises_mois IS NOT NULL
      -- age_debut_crises_mois est NUMERIC en base (schema_registre.sql) :
      -- le comparer a la chaine 'NA' fait echouer la requete entiere
      -- (psycopg2.errors.InvalidTextRepresentation, Postgres ne sait pas
      -- caster 'NA' en numeric). IS NOT NULL suffit deja a exclure les
      -- valeurs manquantes pour une colonne numerique -- pas de convention
      -- 'NA' textuelle a filtrer ici (contrairement a des champs VARCHAR
      -- comme type_crise_ilae2017 ci-dessous, ou severite dans le registre
      -- SEP, qui utilisent 'NA' comme valeur determinee et non manquante).
),

etio AS (
    SELECT pseudonyme, categorie_etiologique
    FROM epr_etiologie
    WHERE etiologie_principale = TRUE
),

types_crise AS (
    SELECT
        pseudonyme,
        COUNT(DISTINCT type_crise_ilae2017) AS nb_types_crise_distincts
    FROM epr_type_crise
    WHERE type_crise_ilae2017 IS NOT NULL AND type_crise_ilae2017 != 'NA'
    GROUP BY pseudonyme
),

freq_baseline AS (
    SELECT DISTINCT ON (pseudonyme)
        pseudonyme,
        frequence_normalisee_mois AS freq_crises_baseline_mois
    FROM epr_frequence_crises
    WHERE frequence_normalisee_mois IS NOT NULL
    ORDER BY pseudonyme, periode_debut ASC
),

irm_anormale AS (
    SELECT DISTINCT pseudonyme, TRUE AS irm_anormale
    FROM epr_imagerie
    WHERE irm_cerebrale = 'Anormal'
),

eeg_anormal AS (
    SELECT DISTINCT pseudonyme, TRUE AS eeg_anormal
    FROM epr_eeg
    WHERE eeg_intercritique = 'Anormal'
),

nb_ae AS (
    SELECT pseudonyme, COUNT(*) AS nb_ae_essayes
    FROM epr_liste_ae
    GROUP BY pseudonyme
)

SELECT
    b.pseudonyme,

    b.age_debut_crises_mois,
    CASE
        WHEN b.age_debut_crises_mois < 12  THEN 'Tres_precoce_lt1an'
        WHEN b.age_debut_crises_mois < 60  THEN 'Precoce_1_5ans'
        ELSE 'Tardif_gt5ans'
    END AS categorie_age_debut,

    b.statut_pharmacoresistance_confirme,
    -- statut_pharmacoresistance_confirme est BOOLEAN en base
    -- (schema_registre.sql), pas de valeurs textuelles 'Oui'/'Non' :
    -- comparer un booleen a une chaine echoue avec
    -- psycopg2.errors.InvalidTextRepresentation. On compare a TRUE/FALSE.
    CASE WHEN b.statut_pharmacoresistance_confirme = TRUE THEN 1 ELSE 0 END AS event_pharmacoresistance,
    CASE
        WHEN b.statut_pharmacoresistance_confirme = TRUE
             THEN b.age_diagnostic_pharmacoresistance_mois - b.age_debut_crises_mois
        ELSE b.duree_suivi_mois
    END AS duree_mois,

    et.categorie_etiologique,
    (et.categorie_etiologique = 'Structurelle')                        AS etiologie_structurelle,
    COALESCE(tc.nb_types_crise_distincts > 1, FALSE)                   AS crises_types_multiples,
    fb.freq_crises_baseline_mois,
    COALESCE(im.irm_anormale, FALSE)                                   AS irm_anormale,
    COALESCE(eg.eeg_anormal, FALSE)                                    AS eeg_anormal,
    ant.atcd_perinataux,
    ant.developpement_psychomoteur_avant_crises,
    reg.presence_regression,
    na.nb_ae_essayes

FROM base b
LEFT JOIN etio et            ON et.pseudonyme = b.pseudonyme
LEFT JOIN types_crise tc     ON tc.pseudonyme = b.pseudonyme
LEFT JOIN freq_baseline fb   ON fb.pseudonyme = b.pseudonyme
LEFT JOIN irm_anormale im    ON im.pseudonyme = b.pseudonyme
LEFT JOIN eeg_anormal eg     ON eg.pseudonyme = b.pseudonyme
LEFT JOIN epr_antecedents ant ON ant.pseudonyme = b.pseudonyme
LEFT JOIN epr_regression_developpementale reg ON reg.pseudonyme = b.pseudonyme
LEFT JOIN nb_ae na            ON na.pseudonyme = b.pseudonyme

WHERE
    (
        b.statut_pharmacoresistance_confirme = TRUE
        AND b.age_diagnostic_pharmacoresistance_mois IS NOT NULL
        AND b.age_diagnostic_pharmacoresistance_mois > b.age_debut_crises_mois
    )
    OR
    (
        b.statut_pharmacoresistance_confirme = FALSE
        AND b.duree_suivi_mois IS NOT NULL
        AND b.duree_suivi_mois > 0
    );
"""


def load_data():
    engine = create_engine(DB_URI)
    df = pd.read_sql(text(DATASET_QUERY), engine)

    bool_cols = ["etiologie_structurelle", "crises_types_multiples",
                 "irm_anormale", "eeg_anormal"]
    for c in bool_cols:
        if c in df.columns:
            df[c] = df[c].astype(bool).astype(int)

    for c in ["atcd_perinataux", "presence_regression"]:
        if c in df.columns:
            df[c] = (df[c] == "Oui").astype(int)

    if "developpement_psychomoteur_avant_crises" in df.columns:
        df["developpement_psychomoteur_avant_crises"] = (
            df["developpement_psychomoteur_avant_crises"] == "Retard"
        ).astype(int)

    df = df[(df["duree_mois"].notna()) & (df["duree_mois"] > 0)]
    return df


def descriptive_summary(df):
    print("=" * 70)
    print("RÉSUMÉ DESCRIPTIF DE LA COHORTE")
    print("=" * 70)
    print(f"Nombre de patients inclus : {len(df)}")
    print(f"Événements (pharmacorésistance confirmée) : {df['event_pharmacoresistance'].sum()} "
          f"({100*df['event_pharmacoresistance'].mean():.1f}%)")
    print(f"Censurés : {(df['event_pharmacoresistance']==0).sum()}")
    print("\nRépartition par catégorie d'âge de début :")
    print(df.groupby("categorie_age_debut")["event_pharmacoresistance"]
            .agg(n="count", evenements="sum", taux="mean"))
    print()


def km_analysis(df, group_col="categorie_age_debut"):
    kmf = KaplanMeierFitter()
    fig, ax = plt.subplots(figsize=(8, 5))

    groups = df[group_col].dropna().unique()
    for g in groups:
        mask = df[group_col] == g
        kmf.fit(df.loc[mask, "duree_mois"], df.loc[mask, "event_pharmacoresistance"], label=str(g))
        kmf.plot_survival_function(ax=ax)

    ax.set_title("Survie sans pharmacorésistance selon l'âge de début des crises")
    ax.set_xlabel("Temps depuis le début des crises (mois)")
    ax.set_ylabel("Probabilité de rester sans pharmacorésistance")
    plt.tight_layout()
    plt.savefig(f"{OUTPUT_DIR}/km_curves_age_onset.png", dpi=150)
    plt.close()

    result = multivariate_logrank_test(
        df["duree_mois"], df[group_col], df["event_pharmacoresistance"]
    )
    print("Test du log-rank (comparaison des groupes d'âge) :")
    print(f"  Statistique = {result.test_statistic:.3f}, p = {result.p_value:.4f}")
    return result


def cox_univariate(df, age_mode):
    age_col = "age_debut_crises_mois" if age_mode == "continuous" else "categorie_age_debut"

    cph_df = df[["duree_mois", "event_pharmacoresistance", age_col]].dropna()

    if age_mode == "categorical":
        cph_df = pd.get_dummies(cph_df, columns=[age_col], drop_first=False)
        ref_col = [c for c in cph_df.columns if "Tardif_gt5ans" in c]
        if ref_col:
            cph_df = cph_df.drop(columns=ref_col)

    cph = CoxPHFitter()
    cph.fit(cph_df, duration_col="duree_mois", event_col="event_pharmacoresistance")

    print("\n" + "=" * 70)
    print(f"COX UNIVARIÉ — variable d'exposition : {age_col} ({age_mode})")
    print("=" * 70)
    cph.print_summary()

    cph.summary.to_csv(f"{OUTPUT_DIR}/cox_univariate_age_summary.csv")
    return cph


def cox_univariate_all_candidates(df):
    rows = []
    for var in CANDIDATE_COVARIATES:
        if var not in df.columns:
            continue
        sub = df[["duree_mois", "event_pharmacoresistance", var]].dropna()
        if sub[var].nunique() < 2 or len(sub) < 10:
            continue
        cph = CoxPHFitter()
        try:
            cph.fit(sub, duration_col="duree_mois", event_col="event_pharmacoresistance")
            row = cph.summary.iloc[0]
            rows.append({
                "covariable": var,
                "HR": np.exp(row["coef"]),
                "IC95_bas": np.exp(row["coef lower 95%"]),
                "IC95_haut": np.exp(row["coef upper 95%"]),
                "p_value": row["p"],
                "n": len(sub),
            })
        except Exception as e:
            print(f"  [!] Échec Cox univarié pour {var} : {e}")

    result_df = pd.DataFrame(rows).sort_values("p_value")
    print("\n" + "=" * 70)
    print("COX UNIVARIÉ — TOUTES LES COVARIABLES CANDIDATES")
    print("=" * 70)
    print(result_df.to_string(index=False))
    result_df.to_csv(f"{OUTPUT_DIR}/cox_univariate_all_candidates.csv", index=False)
    return result_df


def cox_multivariate(df, age_mode, covariates):
    if not covariates:
        raise ValueError("SELECTED_COVARIATES est vide.")

    age_col = "age_debut_crises_mois" if age_mode == "continuous" else "categorie_age_debut"
    cols = ["duree_mois", "event_pharmacoresistance", age_col] + covariates
    cph_df = df[cols].dropna()

    if age_mode == "categorical":
        cph_df = pd.get_dummies(cph_df, columns=[age_col], drop_first=False)
        ref_col = [c for c in cph_df.columns if "Tardif_gt5ans" in c]
        if ref_col:
            cph_df = cph_df.drop(columns=ref_col)

    cph = CoxPHFitter()
    cph.fit(cph_df, duration_col="duree_mois", event_col="event_pharmacoresistance")

    print("\n" + "=" * 70)
    print(f"COX MULTIVARIÉ — âge ({age_mode}) ajusté sur : {covariates}")
    print("=" * 70)
    cph.print_summary()
    print(f"\nC-index (concordance) : {cph.concordance_index_:.3f}")

    cph.summary.to_csv(f"{OUTPUT_DIR}/cox_multivariate_summary.csv")

    print("\nTest de l'hypothèse des risques proportionnels (résidus de Schoenfeld) :")
    try:
        cph.check_assumptions(cph_df, p_value_threshold=0.05, show_plots=False)
    except Exception as e:
        print(f"  [!] Impossible de vérifier l'hypothèse PH automatiquement : {e}")

    fig, ax = plt.subplots(figsize=(7, 0.5 * len(cph.summary) + 2))
    cph.plot(ax=ax)
    ax.set_title("Hazard Ratios — modèle de Cox multivarié")
    plt.tight_layout()
    plt.savefig(f"{OUTPUT_DIR}/cox_multivariate_forest_plot.png", dpi=150)
    plt.close()

    return cph


if __name__ == "__main__":
    df = load_data()

    descriptive_summary(df)
    km_analysis(df, group_col="categorie_age_debut")

    if ANALYSIS_MODE == "univariate":
        cox_univariate(df, AGE_VARIABLE_MODE)
        cox_univariate_all_candidates(df)

    elif ANALYSIS_MODE == "multivariate":
        cox_univariate_all_candidates(df)
        cox_multivariate(df, AGE_VARIABLE_MODE, SELECTED_COVARIATES)

    else:
        raise ValueError('ANALYSIS_MODE doit être "univariate" ou "multivariate"')

    print(f"\nTous les fichiers de sortie (CSV + PNG) sont dans : {OUTPUT_DIR}")