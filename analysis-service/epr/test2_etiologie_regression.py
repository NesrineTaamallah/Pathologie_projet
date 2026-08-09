"""
EPR test 2 — Étiologie et pharmacorésistance : Chi² + régression logistique.

Converti depuis test_analyse_statistique/EPR/test2_epr.py vers le pattern
structuré (voir epr/test1_etiologie_survie.py pour la même démarche sur le
test 1). `run(engine, config)` retourne toujours
{"notes", "figures", "tableau", "resume_stats"}.
"""

import os
import datetime
import numpy as np
import pandas as pd
from scipy import stats
import statsmodels.formula.api as smf
import matplotlib.pyplot as plt

from common import figure_to_base64, Notes

PARAMETRES_SCHEMA = {
    "reference_categorie": {"type": "select",
                             "options": ["Inconnue", "Structurelle", "Genetique",
                                         "Infectieuse", "Metabolique", "Immune"],
                             "default": "Inconnue",
                             "label": "Catégorie étiologique de référence"},
    "ajuster_covariables": {"type": "select", "options": ["oui", "non"],
                             "default": "oui",
                             "label": "Ajuster sur âge/fréquence/développement"},
    "alpha": {"type": "number", "default": 0.05, "label": "Seuil de significativité (alpha)"},
}

RESULTATS_DIR = os.environ.get(
    "EPR_RESULTATS_DIR",
    os.path.join(os.path.dirname(__file__), "resultats"),
)

COLONNES_NA_LITTERAL = [
    "categorie_etiologique", "statut_pharmacoresistance", "statut_dernier_suivi",
    "developpement_psychomoteur_avant_crises", "atcd_familiaux_epilepsie",
]

SQL_EXTRACTION = """
SELECT
    v.pseudonyme,
    v.etiologie_principale                         AS categorie_etiologique,
    pr.statut_pharmacoresistance_confirme           AS statut_pharmacoresistance,
    ic.age_debut_crises_mois,
    ic.age_diagnostic_pharmacoresistance_mois,
    v.duree_suivi_mois,
    v.statut_dernier_suivi,
    an.developpement_psychomoteur_avant_crises,
    an.atcd_familiaux_epilepsie,
    fc.frequence_normalisee_mois
FROM analytics.v_epr_cohorte_etiologie v
JOIN epr_pharmacoresistance pr
    ON pr.pseudonyme = v.pseudonyme
LEFT JOIN epr_identification_clinique ic
    ON ic.pseudonyme = v.pseudonyme
LEFT JOIN epr_antecedents an
    ON an.pseudonyme = v.pseudonyme
LEFT JOIN LATERAL (
    SELECT f.frequence_normalisee_mois
    FROM epr_frequence_crises f
    WHERE f.pseudonyme = v.pseudonyme
    ORDER BY f.date_rapport DESC
    LIMIT 1
) fc ON TRUE
WHERE v.etiologie_principale IS NOT NULL
  AND pr.statut_pharmacoresistance_confirme IS NOT NULL
"""

SQL_CONTROLE_DOUBLONS = """
SELECT pseudonyme, COUNT(*) AS nb_etiologies_principales
FROM epr_etiologie
WHERE etiologie_principale = TRUE
GROUP BY pseudonyme
HAVING COUNT(*) > 1;
"""


def extraire_depuis_postgres(engine) -> pd.DataFrame:
    return pd.read_sql(SQL_EXTRACTION, engine)


def verifier_integrite_etiologie(engine, notes: Notes):
    doublons = pd.read_sql(SQL_CONTROLE_DOUBLONS, engine)
    if len(doublons) > 0:
        notes(f"[ALERTE] {len(doublons)} patient(s) avec plusieurs étiologies principales "
              "détectées côté base — vérifier la contrainte uq_etiologie_principale.")
    else:
        notes("[OK] Contrainte d'intégrité vérifiée : 1 étiologie principale / patient.")


def nettoyer_convention_na(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in COLONNES_NA_LITTERAL:
        if col in df.columns:
            df[col] = df[col].replace({"NA": np.nan, "N/A": np.nan, "": np.nan})
    return df


def controle_qualite(df: pd.DataFrame, notes: Notes) -> pd.DataFrame:
    notes("=" * 70)
    notes("CONTRÔLE QUALITÉ DES DONNÉES")
    notes("=" * 70)

    n_total = len(df)
    notes(f"Nombre de patients extraits : {n_total}")

    doublons = int(df["pseudonyme"].duplicated().sum())
    if doublons > 0:
        notes(f"[ALERTE] {doublons} pseudonyme(s) en double détecté(s).")
    else:
        notes("[OK] Aucun doublon de pseudonyme.")

    df = nettoyer_convention_na(df)

    manquants = df.isna().sum()
    manquants = manquants[manquants > 0].sort_values(ascending=False)
    if len(manquants) > 0:
        notes("\nDonnées manquantes par colonne (NULL réel + 'NA' littéral convertis) :")
        for col, n in manquants.items():
            notes(f"  - {col:45s} {n:4d} manquants ({100*n/n_total:.1f}%)")

    avant = len(df)
    df = df[df["categorie_etiologique"].notna() & df["statut_pharmacoresistance"].notna()].copy()
    exclus = avant - len(df)
    notes(f"\nPatients exclus (étiologie ou statut manquant) : {exclus}")
    notes(f"Effectif final analysable (Chi²) : {len(df)}")

    notes("\nRépartition par catégorie étiologique :")
    notes(df["categorie_etiologique"].value_counts(dropna=False).to_string())
    notes("\nRépartition par statut de pharmacorésistance :")
    notes(df["statut_pharmacoresistance"].value_counts(dropna=False).to_string())

    return df, exclus


def test_chi2(df: pd.DataFrame, alpha: float, notes: Notes) -> dict:
    notes("\n" + "=" * 70)
    notes("ÉTAPE 1 — TEST DU CHI² D'INDÉPENDANCE")
    notes("=" * 70)

    table = pd.crosstab(df["categorie_etiologique"], df["statut_pharmacoresistance"])
    notes("\nTableau de contingence (effectifs observés) :")
    notes(table.to_string())

    chi2, p, ddl, attendu = stats.chi2_contingency(table)
    attendu_df = pd.DataFrame(attendu, index=table.index, columns=table.columns)

    notes(f"\nChi² = {chi2:.3f}, ddl = {ddl}, p-value = {p:.4g}")
    n_cellules_faibles = int((attendu_df < 5).sum().sum())
    if n_cellules_faibles > 0:
        notes(f"[ATTENTION] {n_cellules_faibles} cellule(s) avec effectif théorique < 5 "
              "-> envisager le test exact de Fisher ou un regroupement de catégories.")

    n = table.sum().sum()
    min_dim = min(table.shape) - 1
    cramers_v = float(np.sqrt(chi2 / (n * min_dim))) if min_dim > 0 else float("nan")
    notes(f"V de Cramér (taille d'effet) = {cramers_v:.3f}")

    interpretation = "significative" if p < alpha else "non significative"
    notes(f"\n=> Association {interpretation} au seuil de {alpha} entre étiologie ILAE "
          "et statut de pharmacorésistance.")

    return {
        "table_observee": table, "table_attendue": attendu_df,
        "chi2": float(chi2), "ddl": int(ddl), "p_value": float(p),
        "cramers_v": cramers_v, "n_cellules_theoriques_faibles": n_cellules_faibles,
    }


def regression_logistique(df: pd.DataFrame, ref_categorie: str, alpha: float,
                           ajuster: bool, notes: Notes):
    notes("\n" + "=" * 70)
    notes(f"ÉTAPE 2 — RÉGRESSION LOGISTIQUE (catégorie étiologique, réf. = {ref_categorie})")
    notes("=" * 70)

    d = df.copy()
    d["y"] = (d["statut_pharmacoresistance"].astype(str).str.strip().str.lower()
              .map({"oui": 1, "yes": 1, "1": 1, "true": 1,
                    "non": 0, "no": 0, "0": 0, "false": 0}))
    d = d.dropna(subset=["y"])

    categories_dispo = d["categorie_etiologique"].unique()

    def _normaliser(s):
        s = str(s).strip().lower()
        for a, b in [("é", "e"), ("è", "e"), ("ê", "e"), ("à", "a"), ("î", "i")]:
            s = s.replace(a, b)
        return s

    if ref_categorie not in categories_dispo:
        correspondance = next(
            (c for c in categories_dispo if _normaliser(c) == _normaliser(ref_categorie)), None
        )
        if correspondance is None:
            raise ValueError(
                f"Catégorie de référence '{ref_categorie}' introuvable "
                f"(catégories disponibles : {sorted(categories_dispo)})."
            )
        notes(f"[info] Catégorie de référence '{ref_categorie}' interprétée comme "
              f"'{correspondance}' (casse/accents normalisés).")
        ref_categorie = correspondance

    d["categorie_etiologique"] = pd.Categorical(
        d["categorie_etiologique"],
        categories=[ref_categorie] + sorted(c for c in categories_dispo if c != ref_categorie),
    )

    formula = f"y ~ C(categorie_etiologique, Treatment(reference='{ref_categorie}'))"

    covariables_dispo = []
    if ajuster:
        for col in ["age_debut_crises_mois", "frequence_normalisee_mois"]:
            if col in d.columns and d[col].notna().sum() > 0.5 * len(d):
                covariables_dispo.append(col)
                formula += f" + {col}"
        if "developpement_psychomoteur_avant_crises" in d.columns:
            if d["developpement_psychomoteur_avant_crises"].notna().sum() > 0.5 * len(d):
                covariables_dispo.append("developpement_psychomoteur_avant_crises")
                formula += " + C(developpement_psychomoteur_avant_crises)"

    notes(f"Formule du modèle : {formula}")
    notes(f"Covariables d'ajustement retenues : {covariables_dispo or 'aucune (modèle brut)'}")

    d_model = d.dropna(subset=["y", "categorie_etiologique"] + covariables_dispo)
    notes(f"Effectif utilisé dans le modèle : {len(d_model)} / {len(d)}")
    if len(d_model) < 10:
        raise ValueError(f"Effectif insuffisant pour la régression logistique (n={len(d_model)} < 10).")

    model = smf.logit(formula, data=d_model).fit(disp=0)
    notes(model.summary().as_text())

    conf = model.conf_int()
    conf.columns = ["IC95_bas", "IC95_haut"]
    resultats = pd.DataFrame({
        "coefficient": model.params,
        "OR": np.exp(model.params),
        "IC95_bas": np.exp(conf["IC95_bas"]),
        "IC95_haut": np.exp(conf["IC95_haut"]),
        "p_value": model.pvalues,
    })
    resultats = resultats.drop(index="Intercept", errors="ignore")
    resultats["significatif"] = resultats["p_value"] < alpha

    notes(f"\nTableau des Odds Ratios (référence = {ref_categorie}) :")
    notes(resultats.round(3).to_string())

    pseudo_r2 = float(model.prsquared)
    notes(f"\nPseudo-R² de McFadden = {pseudo_r2:.3f}")

    return model, resultats.reset_index().rename(columns={"index": "variable"}), pseudo_r2, d_model, ref_categorie


def plot_repartition(df: pd.DataFrame):
    prop = pd.crosstab(df["categorie_etiologique"], df["statut_pharmacoresistance"],
                        normalize="index") * 100
    fig, ax = plt.subplots(figsize=(7, 4.5))
    prop.plot(kind="bar", stacked=True, ax=ax, colormap="RdYlGn_r")
    ax.set_ylabel("% de patients")
    ax.set_xlabel("Catégorie étiologique ILAE")
    ax.set_title("Statut de pharmacorésistance par catégorie étiologique")
    ax.legend(title="Statut", bbox_to_anchor=(1.02, 1), loc="upper left")
    plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
    fig.tight_layout()
    return fig


def plot_forest_or(or_table: pd.DataFrame, ref_categorie: str):
    or_plot = or_table[or_table["variable"].str.contains("categorie_etiologique")].copy()
    or_plot["label"] = or_plot["variable"].str.extract(r"\[T\.(.*)\]")[0]
    fig, ax = plt.subplots(figsize=(6.5, 3.5 + 0.4 * len(or_plot)))
    y_pos = np.arange(len(or_plot))
    ax.errorbar(
        or_plot["OR"], y_pos,
        xerr=[or_plot["OR"] - or_plot["IC95_bas"], or_plot["IC95_haut"] - or_plot["OR"]],
        fmt="o", color="black", ecolor="gray", capsize=4,
    )
    ax.axvline(1, linestyle="--", color="red", linewidth=1)
    ax.set_yticks(y_pos)
    ax.set_yticklabels(or_plot["label"])
    ax.set_xlabel(f"Odds Ratio (réf. = {ref_categorie})")
    ax.set_title("Odds Ratios ajustés — étiologie vs pharmacorésistance")
    fig.tight_layout()
    return fig


def _sauvegarder_resultats_sur_disque(dossier: str, notes: Notes, tables: dict, figures_fig: list):
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

    ref_categorie = config.get("reference_categorie", "Inconnue") or "Inconnue"
    ajuster = config.get("ajuster_covariables", "oui") != "non"
    alpha = float(config.get("alpha", 0.05))

    verifier_integrite_etiologie(engine, notes)

    df_brut = extraire_depuis_postgres(engine)
    if df_brut.empty:
        raise ValueError(
            "Aucun patient exploitable (vue analytics.v_epr_cohorte_etiologie "
            "x epr_pharmacoresistance vide)."
        )

    df, n_exclus = controle_qualite(df_brut, notes)
    if len(df) < 10:
        raise ValueError(f"Effectif insuffisant après nettoyage (n={len(df)} < 10).")

    chi2_res = test_chi2(df, alpha, notes)
    model, or_table, pseudo_r2, d_model, ref_categorie = regression_logistique(
        df, ref_categorie, alpha, ajuster, notes
    )

    fig_repartition = plot_repartition(df)
    fig_forest = plot_forest_or(or_table, ref_categorie)
    figures_base64 = [figure_to_base64(fig_repartition), figure_to_base64(fig_forest)]

    tables_a_sauvegarder = {
        "table_contingence_observee": chi2_res["table_observee"].reset_index(),
        "odds_ratios": or_table,
    }
    figures_a_sauvegarder = [
        ("fig1_repartition_pharmacoresistance_par_etiologie", fig_repartition),
        ("fig2_forest_plot_odds_ratios", fig_forest),
    ]

    n_significatifs = int(or_table["significatif"].sum())
    resume_stats = {
        "n_patients_extraits": int(len(df_brut)),
        "n_patients_exclus": int(n_exclus),
        "n_patients_analyses": int(len(df)),
        "n_effectif_modele": int(len(d_model)),
        "chi2": round(chi2_res["chi2"], 3),
        "ddl_chi2": chi2_res["ddl"],
        "p_value_chi2": round(chi2_res["p_value"], 4),
        "cramers_v": round(chi2_res["cramers_v"], 3),
        "association_significative": chi2_res["p_value"] < alpha,
        "pseudo_r2_mcfadden": round(pseudo_r2, 3),
        "n_categories_or_significatives": n_significatifs,
        "reference_categorie": ref_categorie,
    }

    horodatage = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dossier_sortie = os.path.join(RESULTATS_DIR, f"epr_2_{horodatage}")
    chemin_dossier = _sauvegarder_resultats_sur_disque(
        dossier_sortie, notes, tables_a_sauvegarder, figures_a_sauvegarder
    )
    notes(f"\nTous les fichiers de sortie (CSV + PNG + notes.txt) ont été sauvegardés dans : {chemin_dossier}")

    return {
        "notes": notes.lines,
        "figures": figures_base64,
        "tableau": or_table.round(4).to_dict(orient="records"),
        "resume_stats": resume_stats,
    }