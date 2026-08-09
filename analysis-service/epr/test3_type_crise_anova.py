"""
EPR test 3 — Type de crise (ILAE 2017) et nombre d'AE essayés.

Converti depuis test_analyse_statistique/EPR/test3_epr.py vers le pattern
structuré (voir epr/test1_etiologie_survie.py). `run(engine, config)` retourne
toujours {"notes", "figures", "tableau", "resume_stats"}.
"""

import os
import datetime
import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf
from scipy import stats
from statsmodels.stats.multicomp import pairwise_tukeyhsd
import matplotlib.pyplot as plt

from common import figure_to_base64, Notes

PARAMETRES_SCHEMA = {
    "effectif_min_groupe": {"type": "number", "default": 5,
                             "label": "Effectif minimum par groupe (exclusion sinon)"},
    "alpha": {"type": "number", "default": 0.05, "label": "Seuil de significativité (alpha)"},
}

RESULTATS_DIR = os.environ.get(
    "EPR_RESULTATS_DIR",
    os.path.join(os.path.dirname(__file__), "resultats"),
)

SQL_EXTRACTION = """
WITH type_crise_retenu AS (
    SELECT DISTINCT ON (pseudonyme)
        pseudonyme,
        type_crise_ilae2017,
        sous_type,
        date_observation
    FROM epr_type_crise
    ORDER BY pseudonyme, date_observation DESC
)
SELECT
    p.pseudonyme,
    p.age,
    tc.type_crise_ilae2017,
    tc.sous_type,
    nae.nb_ae_essayes,
    pr.statut_pharmacoresistance_confirme
FROM patients p
JOIN type_crise_retenu tc   ON tc.pseudonyme = p.pseudonyme
JOIN v_epr_nb_ae nae        ON nae.pseudonyme = p.pseudonyme
LEFT JOIN epr_pharmacoresistance pr ON pr.pseudonyme = p.pseudonyme
WHERE p.registre = 'EPR';
"""


def extraire_depuis_postgres(engine) -> pd.DataFrame:
    return pd.read_sql(SQL_EXTRACTION, engine)


def nettoyer_valeurs_manquantes(df: pd.DataFrame, notes: Notes) -> pd.DataFrame:
    df = df.copy()
    n_total = len(df)
    rapport = {}

    masque_nae_manquant = df["nb_ae_essayes"].isna()
    rapport["nb_ae_essayes manquant (NULL)"] = int(masque_nae_manquant.sum())
    df = df[~masque_nae_manquant]

    masque_type_manquant = df["type_crise_ilae2017"].isna() | (
        df["type_crise_ilae2017"].astype(str).str.strip().str.upper() == "NA"
    )
    rapport["type_crise_ilae2017 manquant (NULL/'NA')"] = int(masque_type_manquant.sum())
    df = df[~masque_type_manquant]

    masque_sous_type_na = df["sous_type"].astype(str).str.strip().str.upper() == "NA"
    df.loc[masque_sous_type_na | df["sous_type"].isna(), "sous_type"] = np.nan

    notes("\n--- Rapport de gestion des valeurs manquantes ---")
    notes(f"Patients extraits initialement : {n_total}")
    for motif, n in rapport.items():
        notes(f"  Exclus pour '{motif}' : {n}")
    notes(f"Patients retenus pour l'analyse : {len(df)} "
          f"({(len(df) / n_total if n_total else 0):.1%})")

    if n_total and len(df) < n_total * 0.8:
        notes("  /!\\ Plus de 20% des patients exclus pour données manquantes -> "
              "à signaler avant d'interpréter le test.")

    return df, rapport


def construire_groupe_crise(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["_sous_type_norm"] = df["sous_type"].apply(lambda x: x.lower() if isinstance(x, str) else "")
    df["_sous_type_manquant"] = df["sous_type"].isna()

    def classer(row):
        type_ilae = row["type_crise_ilae2017"]
        st = row["_sous_type_norm"]
        if row["_sous_type_manquant"] and type_ilae == "Focale":
            return "Focale (sous-type non renseigné)"
        if type_ilae == "Focale" and (
            "généralisation secondaire" in st or "bilaterale" in st
            or "bilatérale" in st or "focal to bilateral" in st
        ):
            return "Focale avec généralisation secondaire"
        if "spasme" in st:
            return "Spasmes"
        if type_ilae == "Focale":
            return "Focale (sans généralisation secondaire)"
        if type_ilae == "Généralisée":
            return "Généralisée (autre)"
        return "Inconnue / non classée"

    df["groupe_crise"] = df.apply(classer, axis=1)
    return df.drop(columns=["_sous_type_norm", "_sous_type_manquant"])


def filtrer_effectifs_faibles(df: pd.DataFrame, notes: Notes, groupe_col="groupe_crise", n_min=5):
    effectifs = df[groupe_col].value_counts()
    groupes_faibles = effectifs[effectifs < n_min].index.tolist()
    if groupes_faibles:
        notes(f"\n/!\\ Groupe(s) avec effectif < {n_min}, exclus de l'ANOVA "
              "(non interprétables statistiquement) :")
        for g in groupes_faibles:
            notes(f"    '{g}' : n={effectifs[g]}")
        df = df[~df[groupe_col].isin(groupes_faibles)]
    return df, groupes_faibles


def descriptives(df: pd.DataFrame) -> pd.DataFrame:
    return (
        df.groupby("groupe_crise")["nb_ae_essayes"]
          .agg(n="count", moyenne="mean", ecart_type="std", mediane="median", min="min", max="max")
          .round(2)
          .sort_values("moyenne", ascending=False)
          .reset_index()
    )


def verifier_hypotheses(df: pd.DataFrame, notes: Notes, groupe_col="groupe_crise", valeur_col="nb_ae_essayes"):
    notes("\n--- Vérification des hypothèses ANOVA ---")
    normalite_ok = True
    notes("\nNormalité (Shapiro-Wilk) par groupe :")
    for g, sous_df in df.groupby(groupe_col):
        valeurs = sous_df[valeur_col].dropna()
        if len(valeurs) >= 3:
            stat, p = stats.shapiro(valeurs)
            verdict = "normalité rejetée (p<0.05)" if p < 0.05 else "normalité non rejetée"
            if p < 0.05:
                normalite_ok = False
            notes(f"  {g:45s} n={len(valeurs):3d}  W={stat:.3f}  p={p:.4f}  -> {verdict}")
        else:
            notes(f"  {g:45s} n={len(valeurs):3d}  -> effectif insuffisant pour Shapiro")

    groupes_valeurs = [sous_df[valeur_col].dropna().values
                        for _, sous_df in df.groupby(groupe_col)
                        if len(sous_df[valeur_col].dropna()) >= 2]
    stat_lev, p_lev = stats.levene(*groupes_valeurs)
    variances_ok = bool(p_lev >= 0.05)
    notes(f"\nHomogénéité des variances (Levene) : W={stat_lev:.3f}  p={p_lev:.4f}")

    return normalite_ok, variances_ok, {"levene_stat": float(stat_lev), "levene_p": float(p_lev)}


def anova_un_facteur(df: pd.DataFrame, notes: Notes, groupe_col="groupe_crise", valeur_col="nb_ae_essayes"):
    notes("\n--- ANOVA à un facteur : nb_ae_essayes ~ groupe_crise ---")
    modele = smf.ols(f"{valeur_col} ~ C({groupe_col})", data=df).fit()
    table_anova = sm.stats.anova_lm(modele, typ=2)
    notes(table_anova.round(4).to_string())

    p_value = float(table_anova["PR(>F)"].iloc[0])
    if p_value < 0.05:
        notes(f"\n=> p = {p_value:.4f} < 0.05 : différence significative selon le type de crise.")
    else:
        notes(f"\n=> p = {p_value:.4f} >= 0.05 : pas de différence significative détectée.")

    return modele, table_anova, p_value


def posthoc_tukey(df: pd.DataFrame, notes: Notes, groupe_col="groupe_crise", valeur_col="nb_ae_essayes"):
    notes("\n--- Post-hoc Tukey HSD ---")
    tukey = pairwise_tukeyhsd(endog=df[valeur_col], groups=df[groupe_col], alpha=0.05)
    notes(str(tukey))
    return tukey


def welch_anova(df: pd.DataFrame, notes: Notes, groupe_col="groupe_crise", valeur_col="nb_ae_essayes"):
    notes("\n--- Welch-ANOVA (ne suppose pas l'homogénéité des variances) ---")
    groupes = df.groupby(groupe_col)[valeur_col]
    k = groupes.ngroups
    n_i, moy_i, var_i = groupes.count(), groupes.mean(), groupes.var(ddof=1)
    w_i = n_i / var_i
    w_total = w_i.sum()
    moy_ponderee = (w_i * moy_i).sum() / w_total
    numerateur = (w_i * (moy_i - moy_ponderee) ** 2).sum() / (k - 1)
    terme = (1 - w_i / w_total) ** 2 / (n_i - 1)
    denom_ajust = 1 + (2 * (k - 2) / (k ** 2 - 1)) * terme.sum()
    F_welch = numerateur / denom_ajust
    ddl1 = k - 1
    ddl2 = (k ** 2 - 1) / (3 * terme.sum())
    p_value = float(stats.f.sf(F_welch, ddl1, ddl2))
    notes(f"F(Welch) = {F_welch:.3f}   ddl1 = {ddl1}   ddl2 = {ddl2:.1f}   p = {p_value:.4f}")
    return float(F_welch), ddl1, float(ddl2), p_value


def kruskal_wallis(df: pd.DataFrame, notes: Notes, groupe_col="groupe_crise", valeur_col="nb_ae_essayes"):
    notes("\n--- Kruskal-Wallis (alternative non paramétrique) ---")
    groupes_valeurs = [sous_df[valeur_col].dropna().values for _, sous_df in df.groupby(groupe_col)]
    stat, p = stats.kruskal(*groupes_valeurs)
    notes(f"H = {stat:.3f}  p = {p:.4f}")
    return float(stat), float(p)


def regression_poisson(df: pd.DataFrame, notes: Notes, groupe_col="groupe_crise", valeur_col="nb_ae_essayes"):
    notes("\n--- Régression de Poisson : nb_ae_essayes ~ groupe_crise (sensibilité) ---")
    modele = smf.glm(f"{valeur_col} ~ C({groupe_col})", data=df, family=sm.families.Poisson()).fit()
    ratio = float(modele.pearson_chi2 / modele.df_resid)
    notes(f"Ratio de dispersion (Pearson chi2 / ddl) = {ratio:.2f}")
    if ratio > 1.5:
        notes("  -> surdispersion suspectée : envisager un modèle binomial négatif.")
    return modele, ratio


def plot_boxplot(df, groupe_col="groupe_crise", valeur_col="nb_ae_essayes"):
    ordre = df.groupby(groupe_col)[valeur_col].median().sort_values(ascending=False).index
    data = [df.loc[df[groupe_col] == g, valeur_col].dropna().values for g in ordre]
    fig, ax = plt.subplots(figsize=(8, 5))
    bp = ax.boxplot(data, tick_labels=ordre, patch_artist=True, showmeans=True)
    for patch in bp["boxes"]:
        patch.set_facecolor("#a8d0e6")
    rng = np.random.default_rng(0)
    for i, valeurs in enumerate(data, start=1):
        ax.scatter(rng.normal(i, 0.05, size=len(valeurs)), valeurs, alpha=0.4, color="#2c3e50", s=15, zorder=3)
    ax.set_ylabel("Nombre d'AE essayés avant contrôle")
    ax.set_xlabel("Type de crise")
    ax.set_title("Nombre d'AE essayés par type de crise (ILAE 2017)")
    plt.setp(ax.get_xticklabels(), rotation=25, ha="right")
    fig.tight_layout()
    return fig


def plot_forest_posthoc(tukey_result):
    data = tukey_result.summary().data
    entetes, lignes = data[0], data[1:]
    df_tukey = pd.DataFrame(lignes, columns=entetes)
    df_tukey["meandiff"] = df_tukey["meandiff"].astype(float)
    df_tukey["lower"] = df_tukey["lower"].astype(float)
    df_tukey["upper"] = df_tukey["upper"].astype(float)
    df_tukey["reject"] = df_tukey["reject"].astype(str) == "True"
    df_tukey["comparaison"] = df_tukey["group1"] + " vs " + df_tukey["group2"]
    df_tukey = df_tukey.sort_values("meandiff")

    fig, ax = plt.subplots(figsize=(8, 0.5 * len(df_tukey) + 2))
    couleurs = df_tukey["reject"].map({True: "#c0392b", False: "#7f8c8d"}).tolist()
    y_pos = np.arange(len(df_tukey))
    for y, meandiff, lo, hi, coul in zip(y_pos, df_tukey["meandiff"], df_tukey["lower"], df_tukey["upper"], couleurs):
        ax.errorbar(meandiff, y, xerr=[[meandiff - lo], [hi - meandiff]], fmt="o",
                    color="black", ecolor=coul, elinewidth=2, capsize=3)
    ax.axvline(0, color="black", linestyle="--", linewidth=1)
    ax.set_yticks(y_pos)
    ax.set_yticklabels(df_tukey["comparaison"], fontsize=9)
    ax.set_xlabel("Différence de moyennes (nb d'AE essayés), IC 95% Tukey")
    ax.set_title("Post-hoc Tukey HSD (rouge = significatif)")
    fig.tight_layout()
    return fig, df_tukey


def plot_poisson_irr(modele):
    params = modele.params.drop("Intercept", errors="ignore")
    conf_int = modele.conf_int().drop("Intercept", errors="ignore")
    irr, irr_low, irr_high = np.exp(params), np.exp(conf_int[0]), np.exp(conf_int[1])
    labels = [p.split("T.")[-1].rstrip("]") if "T." in p else p for p in params.index]
    ordre = np.argsort(irr.values)
    labels = [labels[i] for i in ordre]
    irr_v, lo_v, hi_v = irr.values[ordre], irr_low.values[ordre], irr_high.values[ordre]

    fig, ax = plt.subplots(figsize=(8, 0.6 * len(labels) + 2))
    y_pos = np.arange(len(labels))
    couleurs = ["#c0392b" if (lo > 1 or hi < 1) else "#7f8c8d" for lo, hi in zip(lo_v, hi_v)]
    for y, val, lo, hi, coul in zip(y_pos, irr_v, lo_v, hi_v, couleurs):
        ax.errorbar(val, y, xerr=[[val - lo], [hi - val]], fmt="o", color="black", ecolor=coul, elinewidth=2, capsize=3)
    ax.axvline(1, color="black", linestyle="--", linewidth=1)
    ax.set_yticks(y_pos)
    ax.set_yticklabels(labels, fontsize=9)
    ax.set_xlabel("Incidence Rate Ratio (IRR), IC 95%")
    ax.set_title("Régression de Poisson — effet du type de crise (rouge = IC95% excluant 1)")
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
    n_min = int(config.get("effectif_min_groupe", 5))
    alpha = float(config.get("alpha", 0.05))

    df_brut = extraire_depuis_postgres(engine)
    if df_brut.empty:
        raise ValueError("Aucun patient exploitable (jointure patients / type de crise / nb AE vide).")

    df, rapport_manquants = nettoyer_valeurs_manquantes(df_brut, notes)
    df = construire_groupe_crise(df)

    notes("=" * 70)
    notes("EFFECTIFS PAR GROUPE DE TYPE DE CRISE")
    notes("=" * 70)
    notes(df["groupe_crise"].value_counts().to_string())

    n_incertain = int((df["groupe_crise"] == "Focale (sous-type non renseigné)").sum())
    if n_incertain > 0:
        notes(f"\n{n_incertain} patient(s) exclus du test ANOVA : type focal avec "
              "sous_type non renseigné.")
        df = df[df["groupe_crise"] != "Focale (sous-type non renseigné)"]

    df, groupes_exclus = filtrer_effectifs_faibles(df, notes, n_min=n_min)
    if len(df) < 10 or df["groupe_crise"].nunique() < 2:
        raise ValueError(
            f"Effectif insuffisant après filtrage (n={len(df)}, "
            f"{df['groupe_crise'].nunique()} groupe(s) restant(s) < 2)."
        )

    desc = descriptives(df)
    notes("\n" + "=" * 70)
    notes("STATISTIQUES DESCRIPTIVES — nb_ae_essayes par groupe")
    notes("=" * 70)
    notes(desc.to_string(index=False))

    normalite_ok, variances_ok, levene_res = verifier_hypotheses(df, notes)

    notes("\n" + "=" * 70)
    notes("ANALYSE PRINCIPALE : ANOVA")
    notes("=" * 70)
    modele_anova, table_anova, p_anova = anova_un_facteur(df, notes)

    tables_a_sauvegarder = {"statistiques_descriptives": desc}
    figures_a_sauvegarder = []
    figures_base64 = []

    fig_box = plot_boxplot(df)
    figures_a_sauvegarder.append(("epr_boxplot_type_crise", fig_box))
    figures_base64.append(figure_to_base64(fig_box))

    tukey_significatif = None
    if p_anova < alpha:
        tukey_result = posthoc_tukey(df, notes)
        fig_forest, df_tukey = plot_forest_posthoc(tukey_result)
        figures_a_sauvegarder.append(("epr_forestplot_posthoc", fig_forest))
        figures_base64.append(figure_to_base64(fig_forest))
        tables_a_sauvegarder["tukey_posthoc"] = df_tukey
        tukey_significatif = int(df_tukey["reject"].sum())

    p_welch = None
    if not normalite_ok or not variances_ok:
        notes("\n" + "=" * 70)
        notes("ANOVA CLASSIQUE PEU FIABLE ICI (hypothèses violées) -> WELCH-ANOVA")
        notes("=" * 70)
        F_welch, ddl1, ddl2, p_welch = welch_anova(df, notes)

    notes("\n" + "=" * 70)
    notes("ANALYSES DE SENSIBILITÉ")
    notes("=" * 70)
    stat_kw, p_kw = kruskal_wallis(df, notes)
    modele_poisson, ratio_dispersion = regression_poisson(df, notes)
    fig_poisson = plot_poisson_irr(modele_poisson)
    figures_a_sauvegarder.append(("epr_poisson_irr", fig_poisson))
    figures_base64.append(figure_to_base64(fig_poisson))

    resume_stats = {
        "n_patients_extraits": int(len(df_brut)),
        "n_patients_analyses": int(len(df)),
        "n_groupes": int(df["groupe_crise"].nunique()),
        "n_groupes_exclus_effectif_faible": len(groupes_exclus),
        "F_anova": round(float(table_anova["F"].iloc[0]), 3),
        "p_value_anova": round(p_anova, 4),
        "anova_significative": p_anova < alpha,
        "normalite_respectee": normalite_ok,
        "homogeneite_variances_respectee": variances_ok,
        "p_value_levene": round(levene_res["levene_p"], 4),
        "p_value_kruskal_wallis": round(p_kw, 4),
        "ratio_dispersion_poisson": round(ratio_dispersion, 2),
        "surdispersion_suspectee": ratio_dispersion > 1.5,
    }
    if p_welch is not None:
        resume_stats["p_value_welch_anova"] = round(p_welch, 4)
    if tukey_significatif is not None:
        resume_stats["n_paires_significatives_tukey"] = tukey_significatif

    horodatage = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dossier_sortie = os.path.join(RESULTATS_DIR, f"epr_3_{horodatage}")
    chemin_dossier = _sauvegarder_resultats_sur_disque(
        dossier_sortie, notes, tables_a_sauvegarder, figures_a_sauvegarder
    )
    notes(f"\nTous les fichiers de sortie (CSV + PNG + notes.txt) ont été sauvegardés dans : {chemin_dossier}")

    return {
        "notes": notes.lines,
        "figures": figures_base64,
        "tableau": desc.to_dict(orient="records"),
        "resume_stats": resume_stats,
    }