"""
EPR test 1 — Étiologie / pharmacorésistance : analyse de survie.

Converti depuis le script autonome test_analyse_statistique/EPR/test1_epr.py
vers le pattern structuré utilisé par les modules SEP (analysis-service/sep/*.py) :
la fonction `run(engine, config)` retourne toujours
{"notes", "figures", "tableau", "resume_stats"}, exploitable directement
par le frontend (mêmes composants que pour les tests SEP).

En plus du retour JSON, `run()` sauvegarde sur disque, dans un dossier dédié
et horodaté, TOUT ce que produisait le script original : les CSV (résumé
Cox univarié âge, Cox univarié toutes covariables, Cox multivarié le cas
échéant), les figures PNG, et un fichier notes.txt avec le journal complet.
"""

import os
import datetime
import numpy as np
import pandas as pd
from lifelines import CoxPHFitter, KaplanMeierFitter
from lifelines.statistics import multivariate_logrank_test
import matplotlib.pyplot as plt

from common import figure_to_base64, Notes

PARAMETRES_SCHEMA = {
    "mode_analyse": {"type": "select", "options": ["univariate", "multivariate"],
                      "default": "univariate", "label": "Mode"},
    "mode_age": {"type": "select", "options": ["categorical", "continuous"],
                 "default": "categorical", "label": "Âge de début des crises"},
    "covariables": {"type": "multiselect", "options": [
        "etiologie_structurelle", "crises_types_multiples", "freq_crises_baseline_mois",
        "irm_anormale", "eeg_anormal", "atcd_perinataux",
        "developpement_psychomoteur_avant_crises", "presence_regression",
    ], "default": [], "label": "Covariables (mode multivarié uniquement)"},
}

REQUIRED_COLUMNS = [
    "pseudonyme", "age_debut_crises_mois", "categorie_age_debut",
    "duree_mois", "event_pharmacoresistance",
]

# Dossier racine où l'on conserve, pour chaque exécution, tous les fichiers
# produits (CSV + PNG + notes) — utile pour retrouver une analyse précise
# sans avoir à la relancer.
RESULTATS_DIR = os.environ.get(
    "EPR_RESULTATS_DIR",
    os.path.join(os.path.dirname(__file__), "resultats"),
)

SQL_EXTRACTION = """
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


def extraire_depuis_postgres(engine) -> pd.DataFrame:
    return pd.read_sql(SQL_EXTRACTION, engine)


def recoder_variables_brutes(df_brut: pd.DataFrame, notes: Notes) -> pd.DataFrame:
    df = df_brut.copy()

    bool_cols = ["etiologie_structurelle", "crises_types_multiples", "irm_anormale", "eeg_anormal"]
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

    n_avant = len(df)
    df = df[(df["duree_mois"].notna()) & (df["duree_mois"] > 0)]
    n_exclus = n_avant - len(df)
    if n_exclus > 0:
        notes(f"[valeurs manquantes] duree_mois : {n_exclus}/{n_avant} patient(s) exclu(s) "
              "(duree_mois manquante ou <= 0)")
    return df


def valider_schema(df: pd.DataFrame, notes: Notes):
    manquantes = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if manquantes:
        raise ValueError(f"Schéma invalide : colonnes manquantes {manquantes}")


def descriptive_summary(df: pd.DataFrame, notes: Notes) -> dict:
    n = len(df)
    n_evenements = int(df["event_pharmacoresistance"].sum())
    taux_evenement = round(100 * df["event_pharmacoresistance"].mean(), 1) if n else 0.0

    notes("=" * 70)
    notes("RÉSUMÉ DESCRIPTIF DE LA COHORTE")
    notes("=" * 70)
    notes(f"Nombre de patients inclus : {n}")
    notes(f"Événements (pharmacorésistance confirmée) : {n_evenements} ({taux_evenement}%)")
    notes(f"Censurés : {n - n_evenements}")

    repartition = (
        df.groupby("categorie_age_debut")["event_pharmacoresistance"]
          .agg(n="count", evenements="sum", taux="mean")
          .reset_index()
    )
    repartition["taux"] = (repartition["taux"] * 100).round(1)
    notes("\nRépartition par catégorie d'âge de début :")
    notes(repartition.to_string(index=False))

    # NB : "repartition_age" n'est PAS mise dans resume_stats — le frontend
    # affiche resume_stats comme une grille de cartes clé/valeur scalaires
    # (Object.entries -> string), donc une liste d'objets y ressort comme
    # "[object Object]". Le détail par catégorie d'âge reste consultable
    # dans les notes (texte) ci-dessus, et on expose ici des clés scalaires
    # aplaties, une par catégorie, utilisables telles quelles par les cartes.
    resume = {
        "n_patients": n,
        "n_evenements": n_evenements,
        "taux_evenement_pct": taux_evenement,
        "n_censures": n - n_evenements,
    }
    for row in repartition.to_dict(orient="records"):
        cle = f"taux_evenement_{row['categorie_age_debut']}_pct"
        resume[cle] = row["taux"]
    return resume


def km_analysis(df: pd.DataFrame, notes: Notes, group_col="categorie_age_debut"):
    kmf = KaplanMeierFitter()
    fig, ax = plt.subplots(figsize=(7, 4.5))

    groups = df[group_col].dropna().unique()
    for g in sorted(groups):
        mask = df[group_col] == g
        kmf.fit(df.loc[mask, "duree_mois"], df.loc[mask, "event_pharmacoresistance"], label=str(g))
        kmf.plot_survival_function(ax=ax)

    ax.set_title("Survie sans pharmacorésistance selon l'âge de début des crises")
    ax.set_xlabel("Temps depuis le début des crises (mois)")
    ax.set_ylabel("Probabilité de rester sans pharmacorésistance")
    fig.tight_layout()

    result = multivariate_logrank_test(df["duree_mois"], df[group_col], df["event_pharmacoresistance"])
    notes("\nTest du log-rank (comparaison des groupes d'âge) :")
    notes(f"  Statistique = {result.test_statistic:.3f}, p = {result.p_value:.4f}")

    return fig, {
        "statistique_logrank": round(float(result.test_statistic), 3),
        "p_value_logrank": round(float(result.p_value), 4),
    }


def cox_univariate_age(df: pd.DataFrame, age_mode: str, notes: Notes):
    age_col = "age_debut_crises_mois" if age_mode == "continuous" else "categorie_age_debut"
    cph_df = df[["duree_mois", "event_pharmacoresistance", age_col]].dropna()

    if age_mode == "categorical":
        cph_df = pd.get_dummies(cph_df, columns=[age_col], drop_first=False)
        ref_col = [c for c in cph_df.columns if "Tardif_gt5ans" in c]
        if ref_col:
            cph_df = cph_df.drop(columns=ref_col)

    cph = CoxPHFitter()
    cph.fit(cph_df, duration_col="duree_mois", event_col="event_pharmacoresistance")

    notes("\n" + "=" * 70)
    notes(f"COX UNIVARIÉ — variable d'exposition : {age_col} ({age_mode})")
    notes("=" * 70)
    notes(cph.summary.round(4).to_string())

    summary = cph.summary.copy()
    summary["HR"] = np.exp(summary["coef"])
    return summary.reset_index().rename(columns={"index": "covariable"})


def cox_univariate_all_candidates(df: pd.DataFrame, notes: Notes) -> pd.DataFrame:
    candidats = [
        "etiologie_structurelle", "crises_types_multiples", "freq_crises_baseline_mois",
        "irm_anormale", "eeg_anormal", "atcd_perinataux",
        "developpement_psychomoteur_avant_crises", "presence_regression",
    ]
    rows = []
    for var in candidats:
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
                "HR": round(float(np.exp(row["coef"])), 3),
                "IC95_bas": round(float(np.exp(row["coef lower 95%"])), 3),
                "IC95_haut": round(float(np.exp(row["coef upper 95%"])), 3),
                "p_value": round(float(row["p"]), 4),
                "n": int(len(sub)),
            })
        except Exception as e:
            notes(f"  [!] Échec Cox univarié pour {var} : {e}")

    result_df = pd.DataFrame(rows).sort_values("p_value") if rows else pd.DataFrame(
        columns=["covariable", "HR", "IC95_bas", "IC95_haut", "p_value", "n"]
    )
    notes("\n" + "=" * 70)
    notes("COX UNIVARIÉ — TOUTES LES COVARIABLES CANDIDATES")
    notes("=" * 70)
    notes(result_df.to_string(index=False))
    return result_df


def cox_multivariate(df: pd.DataFrame, age_mode: str, covariates: list, notes: Notes):
    if not covariates:
        raise ValueError(
            "Mode multivarié sélectionné mais aucune covariable choisie "
            "(paramètre 'covariables')."
        )

    age_col = "age_debut_crises_mois" if age_mode == "continuous" else "categorie_age_debut"
    cols = ["duree_mois", "event_pharmacoresistance", age_col] + covariates
    cph_df = df[cols].dropna()

    if len(cph_df) < 10:
        raise ValueError(f"Effectif insuffisant pour le modèle multivarié (n={len(cph_df)} < 10).")

    if age_mode == "categorical":
        cph_df = pd.get_dummies(cph_df, columns=[age_col], drop_first=False)
        ref_col = [c for c in cph_df.columns if "Tardif_gt5ans" in c]
        if ref_col:
            cph_df = cph_df.drop(columns=ref_col)

    cph = CoxPHFitter()
    cph.fit(cph_df, duration_col="duree_mois", event_col="event_pharmacoresistance")

    notes("\n" + "=" * 70)
    notes(f"COX MULTIVARIÉ — âge ({age_mode}) ajusté sur : {covariates}")
    notes("=" * 70)
    notes(cph.summary.round(4).to_string())
    notes(f"\nC-index (concordance) : {cph.concordance_index_:.3f}")

    fig, ax = plt.subplots(figsize=(7, 0.5 * len(cph.summary) + 2))
    cph.plot(ax=ax)
    ax.set_title("Hazard Ratios — modèle de Cox multivarié")
    fig.tight_layout()

    summary = cph.summary.copy()
    summary["HR"] = np.exp(summary["coef"])
    return summary.reset_index().rename(columns={"index": "covariable"}), fig, cph.concordance_index_


def _sauvegarder_resultats_sur_disque(dossier: str, notes: Notes, tables: dict, figures_fig: list):
    """Écrit sur disque, dans `dossier`, TOUT ce que produisait le script
    original : notes.txt (log complet), un CSV par tableau, un PNG par figure.
    Retourne le chemin absolu du dossier créé."""
    os.makedirs(dossier, exist_ok=True)

    with open(os.path.join(dossier, "notes.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(notes.lines))

    for nom, df in tables.items():
        if df is not None and not df.empty:
            df.to_csv(os.path.join(dossier, f"{nom}.csv"), index=False)

    for nom, fig in figures_fig:
        fig.savefig(os.path.join(dossier, f"{nom}.png"), dpi=150, bbox_inches="tight")

    return os.path.abspath(dossier)


def run(engine, config: dict) -> dict:
    """Point d'entrée appelé par l'API. `config` = corps JSON envoyé par React."""
    notes = Notes()

    mode_analyse = config.get("mode_analyse", "univariate")
    age_mode = config.get("mode_age", "categorical")
    covariables = config.get("covariables", []) or []

    if mode_analyse not in ("univariate", "multivariate"):
        raise ValueError("mode_analyse doit être 'univariate' ou 'multivariate'")

    df_brut = extraire_depuis_postgres(engine)
    if df_brut.empty:
        raise ValueError(
            "Aucun patient exploitable pour cette analyse (jointure "
            "identification clinique / pharmacorésistance / suivi vide)."
        )

    df = recoder_variables_brutes(df_brut, notes)
    valider_schema(df, notes)
    if len(df) < 10:
        raise ValueError(f"Effectif insuffisant après nettoyage (n={len(df)} < 10).")

    resume_descriptif = descriptive_summary(df, notes)
    fig_km, resume_logrank = km_analysis(df, notes, group_col="categorie_age_debut")

    tables_a_sauvegarder = {}
    figures_a_sauvegarder = [("km_curves_age_onset", fig_km)]
    figures_base64 = [figure_to_base64(fig_km)]

    cox_age = cox_univariate_age(df, age_mode, notes)
    tables_a_sauvegarder["cox_univariate_age_summary"] = cox_age

    resume_stats = {
        **resume_descriptif,
        "mode_analyse": mode_analyse,
        "mode_age": age_mode,
        **resume_logrank,
    }

    if mode_analyse == "univariate":
        cox_toutes = cox_univariate_all_candidates(df, notes)
        tables_a_sauvegarder["cox_univariate_all_candidates"] = cox_toutes
        tableau = cox_toutes.to_dict(orient="records")
        if not cox_toutes.empty:
            top = cox_toutes.iloc[0]
            resume_stats["covariable_plus_significative"] = top["covariable"]
            resume_stats["p_value_covariable_plus_significative"] = float(top["p_value"])

    else:  # multivariate
        cox_multi, fig_forest, c_index = cox_multivariate(df, age_mode, covariables, notes)
        tables_a_sauvegarder["cox_multivariate_summary"] = cox_multi
        figures_a_sauvegarder.append(("cox_multivariate_forest_plot", fig_forest))
        figures_base64.append(figure_to_base64(fig_forest))
        tableau = cox_multi.to_dict(orient="records")
        resume_stats["c_index"] = round(float(c_index), 3)
        resume_stats["covariables_ajustees"] = covariables

    horodatage = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dossier_sortie = os.path.join(RESULTATS_DIR, f"epr_1_{mode_analyse}_{horodatage}")
    chemin_dossier = _sauvegarder_resultats_sur_disque(
        dossier_sortie, notes, tables_a_sauvegarder, figures_a_sauvegarder
    )
    notes(f"\nTous les fichiers de sortie (CSV + PNG + notes.txt) ont été sauvegardés dans : {chemin_dossier}")
    # NB : le chemin complet n'est volontairement pas mis dans resume_stats
    # (carte "Points clés") — il est long, illisible en carte, et déjà
    # consultable dans les notes ci-dessus.

    return {
        "notes": notes.lines,
        "figures": figures_base64,
        "tableau": tableau,
        "resume_stats": resume_stats,
    }