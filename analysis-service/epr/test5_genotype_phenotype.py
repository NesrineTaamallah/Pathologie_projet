"""
EPR test 5 — Corrélation génotype-phénotype (analyse de sous-groupes par gène).

Converti depuis test_analyse_statistique/EPR/test5_epr.py vers le pattern
structuré (voir epr/test1_etiologie_survie.py pour la même démarche sur le
test 1). `run(engine, config)` retourne toujours
{"notes", "figures", "tableau", "resume_stats"}.

Le script d'origine supportait un mode CSV (MODE_DONNEES=csv) en plus du mode
SQL ; seul le mode SQL est conservé ici car `run()` reçoit toujours un
`engine` SQLAlchemy déjà connecté à la base (voir registry.py).
"""

import os
import datetime
import numpy as np
import pandas as pd
from scipy import stats
import statsmodels.api as sm
from statsmodels.stats.multitest import multipletests
from sqlalchemy import text
import matplotlib.pyplot as plt
import seaborn as sns

from common import figure_to_base64, Notes

PARAMETRES_SCHEMA = {
    "classes_acmg_causales": {
        "type": "select",
        "options": ["Classe IV/V", "Classe V uniquement", "Classe III/IV/V"],
        "default": "Classe IV/V",
        "label": "Classes ACMG retenues comme variant causal",
    },
    "n_min_sous_groupe": {"type": "number", "default": 5,
                           "label": "Effectif minimum par gène (sous-groupe exclu sinon)"},
    "alpha": {"type": "number", "default": 0.05, "label": "Seuil de significativité (alpha)"},
    "analyse_sensibilite": {"type": "select", "options": ["oui", "non"], "default": "oui",
                             "label": "Inclure les analyses de sensibilité (N_MIN, seuil ACMG)"},
}

RESULTATS_DIR = os.environ.get(
    "EPR_RESULTATS_DIR",
    os.path.join(os.path.dirname(__file__), "resultats"),
)

_OPTIONS_ACMG = {
    "Classe IV/V": ["Classe IV", "Classe V"],
    "Classe V uniquement": ["Classe V"],
    "Classe III/IV/V": ["Classe III", "Classe IV", "Classe V"],
}

ORDRE_SEVERITE_ACMG = {"Classe V": 1, "Classe IV": 2, "Classe III": 3}

GRILLE_N_MIN_SENSIBILITE = [3, 5, 8, 10]


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------

def extraire_gene_par_patient(engine, classes_acmg: list) -> pd.DataFrame:
    query = text("""
        SELECT DISTINCT ON (pseudonyme)
            pseudonyme,
            gene_teste,
            variant_identifie,
            classification_acmg,
            mode_transmission
        FROM epr_genetique
        WHERE gene_teste IS NOT NULL
          AND gene_teste != 'NA'
          AND classification_acmg = ANY(:classes)
        ORDER BY pseudonyme,
            CASE classification_acmg
                WHEN 'Classe V' THEN 1
                WHEN 'Classe IV' THEN 2
                WHEN 'Classe III' THEN 3
            END,
            id ASC
    """)
    return pd.read_sql(query, engine, params={"classes": classes_acmg})


def extraire_type_crise_dominant(engine) -> pd.DataFrame:
    query = text("""
        SELECT DISTINCT ON (pseudonyme)
            pseudonyme,
            type_crise_ilae2017,
            sous_type,
            date_observation
        FROM epr_type_crise
        WHERE type_crise_ilae2017 IS NOT NULL
          AND type_crise_ilae2017 != 'NA'
        ORDER BY pseudonyme, date_observation DESC
    """)
    return pd.read_sql(query, engine)


def extraire_frequence_crises(engine) -> pd.DataFrame:
    query = text("""
        SELECT
            pseudonyme,
            AVG(frequence_normalisee_mois) AS frequence_moyenne_mois,
            COUNT(*) AS nb_rapports_frequence
        FROM epr_frequence_crises
        WHERE frequence_normalisee_mois IS NOT NULL
        GROUP BY pseudonyme
    """)
    return pd.read_sql(query, engine)


def extraire_age_debut(engine) -> pd.DataFrame:
    query = text("""
        SELECT pseudonyme, age_debut_crises_mois
        FROM epr_identification_clinique
        WHERE age_debut_crises_mois IS NOT NULL
    """)
    return pd.read_sql(query, engine)


def extraire_pharmacoresistance(engine) -> pd.DataFrame:
    query = text("""
        SELECT
            pseudonyme,
            statut_declare,
            nb_echecs_inefficacite,
            nb_ae_total,
            statut_calcule_ilae
        FROM analytics.v_epr_pharmacoresistance_detail
    """)
    return pd.read_sql(query, engine)


def _fusionner(df_gene, df_crise, df_freq, df_age, df_pharm) -> pd.DataFrame:
    return (
        df_gene
        .merge(df_crise, on="pseudonyme", how="left")
        .merge(df_freq, on="pseudonyme", how="left")
        .merge(df_age, on="pseudonyme", how="left")
        .merge(df_pharm, on="pseudonyme", how="left")
    )


def construire_dataset(engine, classes_acmg: list) -> pd.DataFrame:
    df_gene = extraire_gene_par_patient(engine, classes_acmg)
    df_crise = extraire_type_crise_dominant(engine)
    df_freq = extraire_frequence_crises(engine)
    df_age = extraire_age_debut(engine)
    df_pharm = extraire_pharmacoresistance(engine)
    return _fusionner(df_gene, df_crise, df_freq, df_age, df_pharm)


# --------------------------------------------------------------------------
# Filtrage / description
# --------------------------------------------------------------------------

def filtrer_sous_groupes_valides(df: pd.DataFrame, notes: Notes, col_gene: str = "gene_teste",
                                  n_min: int = 5, verbeux: bool = True) -> pd.DataFrame:
    effectifs = df[col_gene].value_counts()
    genes_valides = effectifs[effectifs >= n_min].index
    n_exclus = (~df[col_gene].isin(genes_valides)).sum()
    if n_exclus > 0 and verbeux:
        notes(f"[INFO] {n_exclus} patients exclus des comparaisons par gène "
              f"(sous-groupe < {n_min} patients). "
              f"Gènes exclus : {sorted(set(effectifs[effectifs < n_min].index))}")
    return df[df[col_gene].isin(genes_valides)].copy()


def mediane_iqr(serie: pd.Series) -> str:
    serie = serie.dropna()
    if len(serie) == 0:
        return "NA"
    q1, med, q3 = np.nanpercentile(serie, [25, 50, 75])
    return f"{med:.1f} [{q1:.1f}-{q3:.1f}]"


def construire_table1_descriptive(df: pd.DataFrame, col_gene: str,
                                   vars_continues: list, vars_categorielles: list) -> pd.DataFrame:
    lignes = []
    genes = sorted(df[col_gene].dropna().unique())

    lignes.append({"Variable": "N patients", **{g: (df[col_gene] == g).sum() for g in genes},
                    "Ensemble": len(df)})

    for var in vars_continues:
        ligne = {"Variable": f"{var}, médiane [Q1-Q3]"}
        for g in genes:
            ligne[g] = mediane_iqr(df.loc[df[col_gene] == g, var])
        ligne["Ensemble"] = mediane_iqr(df[var])
        lignes.append(ligne)

    for var in vars_categorielles:
        modalites = sorted(df[var].dropna().unique())
        lignes.append({"Variable": f"--- {var} ---"})
        for mod in modalites:
            ligne = {"Variable": f"  {mod}, n (%)"}
            for g in genes:
                sous = df[df[col_gene] == g]
                n_g = len(sous)
                n_mod = (sous[var] == mod).sum()
                pct = 100 * n_mod / n_g if n_g > 0 else np.nan
                ligne[g] = f"{n_mod} ({pct:.1f}%)" if n_g > 0 else "NA"
            n_tot = len(df)
            n_mod_tot = (df[var] == mod).sum()
            ligne["Ensemble"] = f"{n_mod_tot} ({100 * n_mod_tot / n_tot:.1f}%)"
            lignes.append(ligne)

    return pd.DataFrame(lignes)


# --------------------------------------------------------------------------
# Statistiques
# --------------------------------------------------------------------------

def cramers_v(tableau: pd.DataFrame, chi2_stat: float) -> float:
    n = tableau.values.sum()
    r, k = tableau.shape
    phi2 = chi2_stat / n
    phi2_corr = max(0, phi2 - ((k - 1) * (r - 1)) / (n - 1))
    r_corr = r - ((r - 1) ** 2) / (n - 1)
    k_corr = k - ((k - 1) ** 2) / (n - 1)
    denom = min(k_corr - 1, r_corr - 1)
    if denom <= 0:
        return np.nan
    return float(np.sqrt(phi2_corr / denom))


def residus_standardises_ajustes(tableau: pd.DataFrame) -> pd.DataFrame:
    observe = tableau.values.astype(float)
    n = observe.sum()
    total_lignes = observe.sum(axis=1, keepdims=True)
    total_colonnes = observe.sum(axis=0, keepdims=True)
    attendu = total_lignes @ total_colonnes / n
    residu_brut = observe - attendu
    denom = np.sqrt(attendu * (1 - total_lignes / n) * (1 - total_colonnes / n))
    residus = residu_brut / denom
    return pd.DataFrame(residus, index=tableau.index, columns=tableau.columns)


def epsilon_carre_kruskal(h_stat: float, n_total: int, k_groupes: int) -> float:
    if n_total - k_groupes <= 0:
        return np.nan
    return float((h_stat - k_groupes + 1) / (n_total - k_groupes))


def dunn_posthoc(df: pd.DataFrame, col_gene: str, col_outcome: str, alpha: float) -> pd.DataFrame:
    sous = df[[col_gene, col_outcome]].dropna().copy()
    sous["rang"] = stats.rankdata(sous[col_outcome])
    n_total = len(sous)

    groupes = sous.groupby(col_gene)
    stats_par_groupe = groupes["rang"].agg(["mean", "count"])

    valeurs_uniques, effectifs_ties = np.unique(sous[col_outcome], return_counts=True)
    correction_ties = 1 - np.sum(effectifs_ties ** 3 - effectifs_ties) / (n_total ** 3 - n_total)
    if correction_ties <= 0:
        correction_ties = 1.0

    noms = stats_par_groupe.index.tolist()
    resultats = []
    for i in range(len(noms)):
        for j in range(i + 1, len(noms)):
            g1, g2 = noms[i], noms[j]
            r1, n1 = stats_par_groupe.loc[g1, ["mean", "count"]]
            r2, n2 = stats_par_groupe.loc[g2, ["mean", "count"]]
            se = np.sqrt(correction_ties * (n_total * (n_total + 1) / 12) * (1 / n1 + 1 / n2))
            z = (r1 - r2) / se if se > 0 else np.nan
            p_brut = 2 * (1 - stats.norm.cdf(abs(z))) if not np.isnan(z) else np.nan
            resultats.append({"groupe_1": g1, "groupe_2": g2, "n1": int(n1), "n2": int(n2),
                               "z": z, "p_value": p_brut})

    df_res = pd.DataFrame(resultats)
    if len(df_res) > 0 and df_res["p_value"].notna().any():
        valides = df_res["p_value"].notna()
        _, p_corr, _, _ = multipletests(df_res.loc[valides, "p_value"], alpha=alpha, method="fdr_bh")
        df_res.loc[valides, "p_value_fdr"] = p_corr
        df_res["significatif_fdr"] = df_res["p_value_fdr"] < alpha
    return df_res


def test_gene_vs_categorielle(df: pd.DataFrame, col_gene: str, col_outcome: str, alpha: float) -> dict:
    tableau = pd.crosstab(df[col_gene], df[col_outcome])
    residus = None
    taille_effet = None
    nom_effet = None

    if tableau.shape == (2, 2):
        odds_ratio, p_value = stats.fisher_exact(tableau)
        methode = "Fisher exact"
        stat_val = odds_ratio
    else:
        stat_val, p_value, dof, _ = stats.chi2_contingency(tableau)
        methode = "Chi² d'indépendance"
        taille_effet = cramers_v(tableau, stat_val)
        nom_effet = "V de Cramér"
        if p_value < alpha:
            residus = residus_standardises_ajustes(tableau)

    return {
        "variable": col_outcome,
        "methode": methode,
        "statistique": stat_val,
        "p_value": p_value,
        "n": int(tableau.values.sum()),
        "taille_effet": taille_effet,
        "nom_taille_effet": nom_effet,
        "tableau_contingence": tableau,
        "residus_standardises": residus,
    }


def test_gene_vs_continue(df: pd.DataFrame, col_gene: str, col_outcome: str,
                           alpha: float, n_min: int = 5) -> dict:
    sous = df[[col_gene, col_outcome]].dropna()
    effectifs = sous[col_gene].value_counts()
    genes_valides = effectifs[effectifs >= n_min].index
    sous = sous[sous[col_gene].isin(genes_valides)]
    groupes = [g[col_outcome].values for _, g in sous.groupby(col_gene)]

    if len(groupes) < 2:
        return {"variable": col_outcome, "methode": "Kruskal-Wallis",
                "statistique": np.nan, "p_value": np.nan, "n": len(sous),
                "taille_effet": np.nan, "nom_taille_effet": "epsilon²",
                "posthoc_dunn": None, "commentaire": "Pas assez de sous-groupes valides"}

    stat_val, p_value = stats.kruskal(*groupes)
    epsilon2 = epsilon_carre_kruskal(stat_val, len(sous), len(groupes))

    posthoc = None
    if p_value < alpha and len(groupes) > 2:
        posthoc = dunn_posthoc(sous, col_gene, col_outcome, alpha)

    return {
        "variable": col_outcome,
        "methode": "Kruskal-Wallis",
        "statistique": stat_val,
        "p_value": p_value,
        "n": int(sous.shape[0]),
        "taille_effet": epsilon2,
        "nom_taille_effet": "epsilon²",
        "posthoc_dunn": posthoc,
    }


def regression_logistique_pharmacoresistance(df: pd.DataFrame, col_gene: str) -> pd.DataFrame:
    sous = df[[col_gene, "statut_calcule_ilae", "age_debut_crises_mois"]].dropna()
    sous["statut_calcule_ilae"] = sous["statut_calcule_ilae"].astype(int)

    gene_reference = sous[col_gene].value_counts().idxmax()
    sous[col_gene] = pd.Categorical(sous[col_gene])
    sous[col_gene] = sous[col_gene].cat.reorder_categories(
        [gene_reference] + [g for g in sous[col_gene].cat.categories if g != gene_reference]
    )

    X = pd.get_dummies(sous[[col_gene, "age_debut_crises_mois"]],
                        columns=[col_gene], drop_first=True)
    X = sm.add_constant(X.astype(float))
    y = sous["statut_calcule_ilae"]

    modele = sm.Logit(y, X).fit(disp=0)

    resultats = pd.DataFrame({
        "coefficient": modele.params,
        "OR": np.exp(modele.params),
        "IC95_inf": np.exp(modele.conf_int()[0]),
        "IC95_sup": np.exp(modele.conf_int()[1]),
        "p_value": modele.pvalues,
    })
    resultats.attrs["gene_reference"] = gene_reference
    resultats.attrs["n"] = int(sous.shape[0])
    return resultats


def appliquer_correction_fdr(liste_resultats: list, alpha: float) -> pd.DataFrame:
    df_res = pd.DataFrame(liste_resultats)
    p_valides = df_res["p_value"].notna()
    rejet, p_corrige, _, _ = multipletests(
        df_res.loc[p_valides, "p_value"], alpha=alpha, method="fdr_bh"
    )
    df_res.loc[p_valides, "p_value_fdr"] = p_corrige
    df_res.loc[p_valides, "significatif_apres_fdr"] = rejet
    return df_res


# --------------------------------------------------------------------------
# Figures (retournent une figure matplotlib, sauvegarde déléguée à run())
# --------------------------------------------------------------------------

def figure_boxplot_par_gene(df, col_gene, col_outcome, titre):
    fig, ax = plt.subplots(figsize=(9, 5.5))
    ordre = sorted(df[col_gene].dropna().unique())
    sns.boxplot(data=df, x=col_gene, y=col_outcome, order=ordre, ax=ax, showfliers=False)
    sns.stripplot(data=df, x=col_gene, y=col_outcome, order=ordre, ax=ax,
                   color="black", alpha=0.4, size=3, jitter=0.2)
    ax.set_title(titre, fontsize=13, fontweight="bold")
    ax.set_xlabel("Gène")
    ax.set_ylabel(col_outcome)
    plt.xticks(rotation=40, ha="right")
    plt.tight_layout()
    return fig


def figure_heatmap_contingence(tableau, titre):
    fig, ax = plt.subplots(figsize=(max(6, 0.9 * tableau.shape[1] + 3),
                                     max(4, 0.6 * tableau.shape[0] + 2)))
    sns.heatmap(tableau, annot=True, fmt="d", cmap="YlOrRd", ax=ax, cbar_kws={"label": "n patients"})
    ax.set_title(titre, fontsize=13, fontweight="bold")
    ax.set_ylabel("Gène")
    plt.xticks(rotation=40, ha="right")
    plt.tight_layout()
    return fig


def figure_heatmap_residus(residus, titre):
    fig, ax = plt.subplots(figsize=(max(6, 0.9 * residus.shape[1] + 3),
                                     max(4, 0.6 * residus.shape[0] + 2)))
    sns.heatmap(residus, annot=True, fmt=".1f", cmap="coolwarm", center=0, ax=ax,
                cbar_kws={"label": "Résidu standardisé ajusté"}, vmin=-4, vmax=4)
    ax.set_title(titre + "\n(|résidu| > 1.96 ≈ significatif à p<.05)", fontsize=12, fontweight="bold")
    ax.set_ylabel("Gène")
    plt.xticks(rotation=40, ha="right")
    plt.tight_layout()
    return fig


def figure_effectifs_par_gene(df, col_gene, n_min):
    fig, ax = plt.subplots(figsize=(8, 5))
    effectifs = df[col_gene].value_counts().sort_values(ascending=True)
    effectifs.plot(kind="barh", ax=ax, color=sns.color_palette("Set2")[0])
    ax.axvline(n_min, color="red", linestyle="--", linewidth=1, label=f"N_MIN = {n_min}")
    ax.set_title("Effectifs par gène (sous-groupes retenus)", fontsize=13, fontweight="bold")
    ax.set_xlabel("n patients")
    ax.legend()
    plt.tight_layout()
    return fig


def figure_sensibilite_n_min(df_sensibilite, alpha):
    fig, ax = plt.subplots(figsize=(8, 5))
    for variable in df_sensibilite["variable"].unique():
        sous = df_sensibilite[df_sensibilite["variable"] == variable]
        ax.plot(sous["n_min"], sous["p_value"], marker="o", label=variable)
    ax.axhline(alpha, color="red", linestyle="--", linewidth=1, label=f"alpha = {alpha}")
    ax.set_xlabel("N_MIN (taille minimale de sous-groupe)")
    ax.set_ylabel("p-value (non corrigée)")
    ax.set_title("Analyse de sensibilité — stabilité des p-values selon N_MIN",
                 fontsize=12, fontweight="bold")
    ax.legend(fontsize=8)
    plt.tight_layout()
    return fig


# --------------------------------------------------------------------------
# Analyses de sensibilité
# --------------------------------------------------------------------------

def analyse_sensibilite_n_min(engine, df: pd.DataFrame, notes: Notes, alpha: float,
                               col_gene: str = "gene_teste") -> pd.DataFrame:
    lignes = []
    for n_min in GRILLE_N_MIN_SENSIBILITE:
        df_filtre = filtrer_sous_groupes_valides(df, notes, col_gene, n_min=n_min, verbeux=False)
        n_genes = df_filtre[col_gene].nunique()

        res_freq = test_gene_vs_continue(df_filtre, col_gene, "frequence_moyenne_mois", alpha, n_min=n_min)
        res_age = test_gene_vs_continue(df_filtre, col_gene, "age_debut_crises_mois", alpha, n_min=n_min)

        for res, nom in [(res_freq, "fréquence des crises"), (res_age, "âge de début des crises")]:
            lignes.append({
                "n_min": n_min,
                "n_genes_inclus": n_genes,
                "n_patients": len(df_filtre),
                "variable": nom,
                "statistique_H": res["statistique"],
                "p_value": res["p_value"],
                "epsilon2": res.get("taille_effet"),
            })

        if df_filtre[col_gene].nunique() >= 2:
            res_crise = test_gene_vs_categorielle(df_filtre, col_gene, "type_crise_ilae2017", alpha)
            lignes.append({
                "n_min": n_min,
                "n_genes_inclus": n_genes,
                "n_patients": len(df_filtre),
                "variable": "type de crise (ILAE 2017)",
                "statistique_H": res_crise["statistique"],
                "p_value": res_crise["p_value"],
                "epsilon2": res_crise.get("taille_effet"),
            })

    return pd.DataFrame(lignes)


def analyse_sensibilite_classes_acmg(engine, notes: Notes) -> pd.DataFrame:
    scenarios = {
        "Classe V uniquement (strict)": ["Classe V"],
        "Classe IV + V (retenu)": ["Classe IV", "Classe V"],
        "Classe III + IV + V (élargi, exploratoire)": ["Classe III", "Classe IV", "Classe V"],
    }
    lignes = []
    for nom_scenario, classes in scenarios.items():
        try:
            df_scenario = construire_dataset(engine, classes)
            n_patients = df_scenario["pseudonyme"].nunique()
            n_genes = df_scenario["gene_teste"].nunique()
        except Exception as exc:
            n_patients, n_genes = np.nan, np.nan
            notes(f"[ATTENTION] scénario '{nom_scenario}' non évaluable : {exc}")
        lignes.append({"scenario_ACMG": nom_scenario, "classes": classes,
                        "n_patients": n_patients, "n_genes_distincts": n_genes})
    return pd.DataFrame(lignes)


# --------------------------------------------------------------------------
# Sauvegarde disque (même convention que epr/test1-4)
# --------------------------------------------------------------------------

def _sauvegarder_resultats_sur_disque(dossier: str, notes: Notes, tables: dict, figures_fig: list):
    os.makedirs(dossier, exist_ok=True)
    with open(os.path.join(dossier, "notes.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(notes.lines))
    for nom, df in tables.items():
        if df is not None and not df.empty:
            df.to_csv(os.path.join(dossier, f"{nom}.csv"))
    for nom, fig in figures_fig:
        fig.savefig(os.path.join(dossier, f"{nom}.png"), dpi=150, bbox_inches="tight")
    return os.path.abspath(dossier)


# --------------------------------------------------------------------------
# Point d'entrée
# --------------------------------------------------------------------------

def run(engine, config: dict) -> dict:
    """Point d'entrée appelé par l'API. `config` = corps JSON envoyé par React."""
    notes = Notes()
    sns.set_theme(style="whitegrid", palette="Set2")

    classes_acmg = _OPTIONS_ACMG.get(
        config.get("classes_acmg_causales", "Classe IV/V"), _OPTIONS_ACMG["Classe IV/V"]
    )
    n_min = int(config.get("n_min_sous_groupe", 5))
    alpha = float(config.get("alpha", 0.05))
    avec_sensibilite = config.get("analyse_sensibilite", "oui") == "oui"

    notes("=" * 78)
    notes("Analyse de sous-groupes par gène — corrélation génotype-phénotype (EPR)")
    notes(f"Classes ACMG retenues comme variant causal : {'/'.join(classes_acmg)}")
    notes(f"Date de génération : {pd.Timestamp.now():%Y-%m-%d %H:%M}")
    notes("=" * 78)

    df = construire_dataset(engine, classes_acmg)
    if df.empty:
        raise ValueError(
            f"Aucun patient avec variant causal (classes ACMG {'/'.join(classes_acmg)}) exploitable."
        )
    notes(f"\n[INFO] {df['pseudonyme'].nunique()} patients avec variant causal "
          f"(classes ACMG retenues : {classes_acmg})")

    df_valide = filtrer_sous_groupes_valides(df, notes, col_gene="gene_teste", n_min=n_min)
    if df_valide.empty or df_valide["gene_teste"].nunique() < 2:
        raise ValueError(
            f"Effectif insuffisant après filtre n >= {n_min} par gène "
            f"({df_valide['gene_teste'].nunique()} gène(s) restant(s))."
        )
    notes(f"[INFO] {df_valide.shape[0]} patients retenus après filtre n >= {n_min} par gène")
    notes(f"[INFO] Sous-groupes analysés : {sorted(df_valide['gene_teste'].unique())}")

    tables_a_sauvegarder = {}
    figures_a_sauvegarder = []
    figures_base64 = []

    fig_eff = figure_effectifs_par_gene(df, "gene_teste", n_min)
    figures_a_sauvegarder.append(("epr5_00_effectifs_par_gene", fig_eff))
    figures_base64.append(figure_to_base64(fig_eff))

    # Table 1 descriptive
    notes("\n" + "=" * 78)
    notes("TABLE 1 — Statistiques descriptives par sous-groupe (gène)")
    notes("=" * 78)
    table1 = construire_table1_descriptive(
        df_valide, "gene_teste",
        vars_continues=["frequence_moyenne_mois", "age_debut_crises_mois"],
        vars_categorielles=["type_crise_ilae2017", "statut_calcule_ilae"],
    )
    notes(table1.to_string(index=False))
    tables_a_sauvegarder["table1_descriptive"] = table1

    resultats_bruts = []

    # Gène vs type de crise
    notes("\n" + "=" * 78)
    notes("Gène vs type de crise (ILAE 2017)")
    notes("=" * 78)
    res_crise = test_gene_vs_categorielle(df_valide, "gene_teste", "type_crise_ilae2017", alpha)
    notes(res_crise["tableau_contingence"].to_string())
    notes(f"\n{res_crise['methode']} : statistique={res_crise['statistique']:.3f}, "
          f"p={res_crise['p_value']:.4f}, n={res_crise['n']}")
    if res_crise["taille_effet"] is not None:
        v = res_crise["taille_effet"]
        force = "négligeable" if v < 0.1 else "faible" if v < 0.3 else "modérée" if v < 0.5 else "forte"
        notes(f"{res_crise['nom_taille_effet']} (taille d'effet) = {v:.3f} ({force})")

    fig_heat1 = figure_heatmap_contingence(res_crise["tableau_contingence"], "Type de crise (ILAE 2017) par gène")
    figures_a_sauvegarder.append(("epr5_01_heatmap_type_crise_par_gene", fig_heat1))
    figures_base64.append(figure_to_base64(fig_heat1))

    if res_crise["residus_standardises"] is not None:
        notes("\nRésidus standardisés ajustés (localisation de l'écart à l'indépendance) :")
        notes(res_crise["residus_standardises"].round(2).to_string())
        fig_res1 = figure_heatmap_residus(
            res_crise["residus_standardises"], "Résidus standardisés ajustés — Gène x Type de crise"
        )
        figures_a_sauvegarder.append(("epr5_02_heatmap_residus_type_crise", fig_res1))
        figures_base64.append(figure_to_base64(fig_res1))
    resultats_bruts.append({k: v for k, v in res_crise.items()
                             if k not in ("tableau_contingence", "residus_standardises")})

    # Gène vs fréquence des crises
    notes("\n" + "=" * 78)
    notes("Gène vs fréquence des crises (crises/mois)")
    notes("=" * 78)
    res_freq = test_gene_vs_continue(df_valide, "gene_teste", "frequence_moyenne_mois", alpha, n_min=n_min)
    if not np.isnan(res_freq["statistique"]):
        notes(f"Kruskal-Wallis : H={res_freq['statistique']:.3f}, p={res_freq['p_value']:.4f}, "
              f"n={res_freq['n']}, epsilon²={res_freq['taille_effet']:.3f}")
    else:
        notes("Test non réalisable (sous-groupes insuffisants)")
    if res_freq.get("posthoc_dunn") is not None:
        notes("\nPost-hoc de Dunn (comparaisons 2 à 2, correction FDR) :")
        notes(res_freq["posthoc_dunn"].round(4).to_string(index=False))
        tables_a_sauvegarder["posthoc_dunn_frequence"] = res_freq["posthoc_dunn"]
    fig_box_freq = figure_boxplot_par_gene(df_valide, "gene_teste", "frequence_moyenne_mois",
                                            "Fréquence des crises par gène")
    figures_a_sauvegarder.append(("epr5_03_boxplot_frequence_par_gene", fig_box_freq))
    figures_base64.append(figure_to_base64(fig_box_freq))
    resultats_bruts.append({k: v for k, v in res_freq.items() if k != "posthoc_dunn"})

    # Gène vs âge de début
    notes("\n" + "=" * 78)
    notes("Gène vs âge de début des crises (mois)")
    notes("=" * 78)
    res_age = test_gene_vs_continue(df_valide, "gene_teste", "age_debut_crises_mois", alpha, n_min=n_min)
    if not np.isnan(res_age["statistique"]):
        notes(f"Kruskal-Wallis : H={res_age['statistique']:.3f}, p={res_age['p_value']:.4f}, "
              f"n={res_age['n']}, epsilon²={res_age['taille_effet']:.3f}")
    else:
        notes("Test non réalisable (sous-groupes insuffisants)")
    if res_age.get("posthoc_dunn") is not None:
        notes("\nPost-hoc de Dunn (comparaisons 2 à 2, correction FDR) :")
        notes(res_age["posthoc_dunn"].round(4).to_string(index=False))
        tables_a_sauvegarder["posthoc_dunn_age"] = res_age["posthoc_dunn"]
    fig_box_age = figure_boxplot_par_gene(df_valide, "gene_teste", "age_debut_crises_mois",
                                           "Âge de début des crises par gène")
    figures_a_sauvegarder.append(("epr5_04_boxplot_age_debut_par_gene", fig_box_age))
    figures_base64.append(figure_to_base64(fig_box_age))
    resultats_bruts.append({k: v for k, v in res_age.items() if k != "posthoc_dunn"})

    # Gène vs pharmacorésistance
    resultat_logit_disponible = False
    notes("\n" + "=" * 78)
    notes("Gène vs pharmacorésistance (régression logistique, ajustée âge début)")
    notes("=" * 78)
    try:
        res_logit = regression_logistique_pharmacoresistance(df_valide, "gene_teste")
        resultat_logit_disponible = True
        notes(f"Référence : {res_logit.attrs['gene_reference']} | n = {res_logit.attrs['n']}")
        notes(res_logit.round(4).to_string())
        tables_a_sauvegarder["regression_logistique_pharmacoresistance"] = res_logit.reset_index().rename(
            columns={"index": "terme"}
        )
        for gene_dummy, ligne in res_logit.iterrows():
            if str(gene_dummy).startswith("gene_teste_"):
                resultats_bruts.append({
                    "variable": f"pharmacoresistance ({gene_dummy})",
                    "methode": "Régression logistique (OR)",
                    "statistique": ligne["OR"],
                    "p_value": ligne["p_value"],
                    "n": res_logit.attrs["n"],
                    "taille_effet": np.nan,
                    "nom_taille_effet": None,
                })
    except Exception as exc:
        notes(f"[ATTENTION] Régression logistique non réalisable : {exc}")

    # Synthèse FDR
    notes("\n" + "=" * 78)
    notes("Synthèse avec correction pour comparaisons multiples (Benjamini-Hochberg)")
    notes("=" * 78)
    synthese = appliquer_correction_fdr(resultats_bruts, alpha)
    colonnes_affichees = ["variable", "methode", "p_value", "p_value_fdr",
                           "significatif_apres_fdr", "n", "taille_effet", "nom_taille_effet"]
    notes(synthese[colonnes_affichees].to_string(index=False))
    tables_a_sauvegarder["resultats_genotype_phenotype_epr"] = synthese

    # Analyses de sensibilité (optionnelles)
    sensibilite_n_min = None
    sensibilite_acmg = None
    if avec_sensibilite:
        notes("\n" + "=" * 78)
        notes(f"Analyse de sensibilité — N_MIN ∈ {GRILLE_N_MIN_SENSIBILITE}")
        notes("=" * 78)
        sensibilite_n_min = analyse_sensibilite_n_min(engine, df, notes, alpha, "gene_teste")
        notes(sensibilite_n_min.round(4).to_string(index=False))
        notes(
            "\nInterprétation : si le statut de significativité (p<0.05) et le sens "
            "de l'effet (epsilon²/statistique) restent stables à travers la grille "
            "de N_MIN, la conclusion n'est pas un artefact du seuil choisi."
        )
        tables_a_sauvegarder["sensibilite_n_min"] = sensibilite_n_min
        fig_sens_nmin = figure_sensibilite_n_min(sensibilite_n_min, alpha)
        figures_a_sauvegarder.append(("epr5_05_sensibilite_n_min", fig_sens_nmin))
        figures_base64.append(figure_to_base64(fig_sens_nmin))

        notes("\n" + "=" * 78)
        notes("Analyse de sensibilité — seuil ACMG retenu comme variant causal")
        notes("=" * 78)
        try:
            sensibilite_acmg = analyse_sensibilite_classes_acmg(engine, notes)
            notes(sensibilite_acmg.to_string(index=False))
            notes(
                "\n[NOTE] Le scénario 'Classe III+IV+V' est exploratoire (VUS = variant de "
                "signification incertaine) : à ne pas utiliser pour des conclusions "
                "cliniques, seulement pour juger de la sensibilité de la taille de cohorte."
            )
            tables_a_sauvegarder["sensibilite_classes_acmg"] = sensibilite_acmg
        except Exception as exc:
            notes(f"[ATTENTION] Analyse de sensibilité ACMG non réalisée : {exc}")

    resume_stats = {
        "n_patients_variant_causal": int(df["pseudonyme"].nunique()),
        "n_patients_analyses": int(df_valide.shape[0]),
        "n_genes_analyses": int(df_valide["gene_teste"].nunique()),
        "classes_acmg_retenues": classes_acmg,
        "p_value_gene_vs_type_crise": round(float(res_crise["p_value"]), 4),
        "p_value_gene_vs_frequence": (
            round(float(res_freq["p_value"]), 4) if not np.isnan(res_freq["p_value"]) else None
        ),
        "p_value_gene_vs_age_debut": (
            round(float(res_age["p_value"]), 4) if not np.isnan(res_age["p_value"]) else None
        ),
        "resultat_logistique_disponible": resultat_logit_disponible,
        "n_associations_significatives_apres_fdr": int(
            synthese["significatif_apres_fdr"].infer_objects(copy=False).fillna(False).sum()
        ),
    }

    horodatage = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dossier_sortie = os.path.join(RESULTATS_DIR, f"epr_5_{horodatage}")
    chemin_dossier = _sauvegarder_resultats_sur_disque(
        dossier_sortie, notes, tables_a_sauvegarder, figures_a_sauvegarder
    )
    notes(f"\nTous les fichiers de sortie (CSV + PNG + notes.txt) ont été sauvegardés dans : {chemin_dossier}")

    return {
        "notes": notes.lines,
        "figures": figures_base64,
        "tableau": synthese[colonnes_affichees].to_dict(orient="records"),
        "resume_stats": resume_stats,
    }
