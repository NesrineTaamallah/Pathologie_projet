"""
EPR test 4 — Régression développementale x Catégorie étiologique / Gène impliqué.

Converti depuis test_analyse_statistique/EPR/test4_epr.py vers le pattern
structuré (voir epr/test1_etiologie_survie.py pour la même démarche sur le
test 1). `run(engine, config)` retourne toujours
{"notes", "figures", "tableau", "resume_stats"}.
"""

import os
import datetime
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from scipy import stats

from common import figure_to_base64, Notes

PARAMETRES_SCHEMA = {
    "acmg_classes_retenues": {
        "type": "select",
        "options": ["Classe IV/V", "Classe V uniquement", "Classe III/IV/V"],
        "default": "Classe IV/V",
        "label": "Classes ACMG retenues pour l'analyse par gène",
    },
    "effectif_min_gene": {"type": "number", "default": 5,
                           "label": "Effectif minimum pour conserver un gène individuellement"},
    "alpha": {"type": "number", "default": 0.05, "label": "Seuil de significativité (alpha)"},
}

RESULTATS_DIR = os.environ.get(
    "EPR_RESULTATS_DIR",
    os.path.join(os.path.dirname(__file__), "resultats"),
)

FAMILLES_FONCTIONNELLES_GENES = {
    "Canaux sodiques":      ["SCN1A", "SCN2A", "SCN8A", "SCN1B"],
    "Canaux potassiques":   ["KCNQ2", "KCNQ3", "KCNT1", "KCNA2"],
    "Voie mTOR":            ["DEPDC5", "TSC1", "TSC2", "NPRL2", "NPRL3", "MTOR"],
    "Récepteurs GABA":      ["GABRA1", "GABRB3", "GABRG2", "STXBP1"],
    "Récepteurs glutamate": ["GRIN1", "GRIN2A", "GRIN2B"],
    "Régulateurs de la chromatine/transcription": ["CDKL5", "FOXG1", "MECP2", "ARX"],
}

QUERY_ETIOLOGIE = """
    SELECT
        r.pseudonyme,
        r.presence_regression,
        e.categorie_etiologique
    FROM epr_regression_developpementale r
    JOIN epr_etiologie e
        ON e.pseudonyme = r.pseudonyme
        AND e.etiologie_principale = TRUE
    WHERE r.presence_regression IS NOT NULL
        AND e.categorie_etiologique IS NOT NULL
        AND e.categorie_etiologique != 'NA'
"""

QUERY_GENE_TEMPLATE = """
    SELECT
        r.pseudonyme,
        r.presence_regression,
        g.gene_teste,
        g.classification_acmg
    FROM epr_regression_developpementale r
    JOIN epr_genetique g
        ON g.pseudonyme = r.pseudonyme
    JOIN epr_etiologie e
        ON e.pseudonyme = r.pseudonyme
        AND e.etiologie_principale = TRUE
        AND e.categorie_etiologique = 'Génétique'
    WHERE r.presence_regression IS NOT NULL
        AND g.gene_teste IS NOT NULL
        AND g.gene_teste != 'NA'
        AND g.classification_acmg IN ({classes})
"""

_OPTIONS_ACMG = {
    "Classe IV/V": ("Classe IV", "Classe V"),
    "Classe V uniquement": ("Classe V",),
    "Classe III/IV/V": ("Classe III", "Classe IV", "Classe V"),
}


def extraire_depuis_postgres(engine, classes_acmg: tuple[str, ...]):
    df_etio = pd.read_sql(QUERY_ETIOLOGIE, engine)

    classes_sql = ",".join(f"'{c}'" for c in classes_acmg)
    df_gene = pd.read_sql(QUERY_GENE_TEMPLATE.format(classes=classes_sql), engine)

    return df_etio, df_gene


def rapport_completude(engine, notes: Notes):
    notes("=" * 70)
    notes("0. RAPPORT DE COMPLÉTUDE DES DONNÉES (avant filtrage)")
    notes("=" * 70)

    n_total_epr = pd.read_sql(
        "SELECT COUNT(DISTINCT pseudonyme) AS n FROM epr_identification_clinique", engine
    )["n"].iloc[0]
    notes(f"Effectif total registre EPR (epr_identification_clinique) : {n_total_epr}")

    comp_reg = pd.read_sql(
        """
        SELECT
            COUNT(*) AS n_lignes,
            COUNT(*) FILTER (WHERE presence_regression IS NULL) AS n_null,
            COUNT(*) FILTER (WHERE presence_regression IS NOT NULL) AS n_exploitable
        FROM epr_regression_developpementale
        """,
        engine,
    ).iloc[0]
    notes(f"\nTable epr_regression_developpementale :")
    notes(f"  - lignes totales           : {comp_reg['n_lignes']}")
    notes(f"  - NULL (non renseigné)     : {comp_reg['n_null']}")
    notes(f"  - exploitables pour le test: {comp_reg['n_exploitable']}")

    comp_etio = pd.read_sql(
        """
        SELECT
            COUNT(*) AS n_lignes,
            COUNT(*) FILTER (WHERE categorie_etiologique IS NULL) AS n_null,
            COUNT(*) FILTER (WHERE categorie_etiologique = 'NA') AS n_na,
            COUNT(*) FILTER (WHERE etiologie_principale = TRUE) AS n_principale
        FROM epr_etiologie
        """,
        engine,
    ).iloc[0]
    notes(f"\nTable epr_etiologie :")
    notes(f"  - lignes totales (toutes étiologies)     : {comp_etio['n_lignes']}")
    notes(f"  - NULL (non renseigné)                   : {comp_etio['n_null']}")
    notes(f"  - 'NA' (non applicable)                  : {comp_etio['n_na']}")
    notes(f"  - lignes etiologie_principale = TRUE     : {comp_etio['n_principale']}")
    notes("    (c'est ce sous-ensemble qui est utilisé pour le Chi², afin de "
          "garantir 1 ligne par patient et éviter le double comptage)")


def test_chi2_association(df, col_facteur, col_reponse, min_effectif_attendu=5):
    table = pd.crosstab(df[col_facteur], df[col_reponse])
    n = table.values.sum()

    chi2, p, ddl, expected = stats.chi2_contingency(table, correction=False)

    pct_sous_5 = (expected < min_effectif_attendu).sum() / expected.size * 100
    condition_ok = (pct_sous_5 <= 20) and (expected.min() >= 1)

    methode = "Chi² de Pearson"
    alerte = None

    if not condition_ok and table.shape == (2, 2):
        odds_ratio_brut, p_fisher = stats.fisher_exact(table)
        methode = "Test exact de Fisher (repli automatique : condition de Cochran non respectée)"
        p = p_fisher
        chi2 = np.nan
        ddl = 1
    elif not condition_ok and table.shape != (2, 2):
        alerte = ("ATTENTION : condition de Cochran violée (effectifs théoriques < 5 dans "
                  f"{pct_sous_5:.1f}% des cellules). Résultat du Chi² à interpréter avec prudence "
                  "; envisager un regroupement de catégories rares ou un test de "
                  "Fisher-Freeman-Halton.")

    r, c = table.shape
    cramer_v = np.sqrt(chi2 / (n * (min(r, c) - 1))) if not np.isnan(chi2) else np.nan

    return {
        "tableau_contingence": table,
        "effectifs_theoriques": pd.DataFrame(expected, index=table.index, columns=table.columns),
        "chi2": chi2,
        "ddl": ddl,
        "p_value": p,
        "cramer_v": cramer_v,
        "n_total": n,
        "pct_cellules_sous_5": round(pct_sous_5, 1),
        "methode": methode,
        "alerte": alerte,
    }


def odds_ratio_2x2(df, col_facteur, col_reponse, val_facteur_pos, val_reponse_pos):
    d = df.copy()
    d["_f"] = (d[col_facteur] == val_facteur_pos)
    d["_r"] = (d[col_reponse] == val_reponse_pos)

    a = ((d["_f"]) & (d["_r"])).sum()
    b = ((d["_f"]) & (~d["_r"])).sum()
    c = ((~d["_f"]) & (d["_r"])).sum()
    e = ((~d["_f"]) & (~d["_r"])).sum()

    correction = 0
    if 0 in (a, b, c, e):
        correction = 0.5
    a, b, c, e = a + correction, b + correction, c + correction, e + correction

    OR = (a * e) / (b * c)
    se_log_or = np.sqrt(1 / a + 1 / b + 1 / c + 1 / e)
    ic_bas = np.exp(np.log(OR) - 1.96 * se_log_or)
    ic_haut = np.exp(np.log(OR) + 1.96 * se_log_or)

    return {"a": a, "b": b, "c": c, "d": e, "OR": OR, "IC95": (ic_bas, ic_haut),
            "correction_appliquee": correction > 0}


def interpreter_p(p, alpha=0.05):
    if p < 0.001:
        return "hautement significative (p < 0.001)"
    elif p < alpha:
        return f"statistiquement significative (p = {p:.4f} < {alpha})"
    else:
        return f"non statistiquement significative (p = {p:.4f} >= {alpha})"


def interpreter_cramer_v(v):
    if np.isnan(v):
        return "N/A"
    if v < 0.10:
        return "négligeable"
    elif v < 0.30:
        return "faible"
    elif v < 0.50:
        return "modérée"
    else:
        return "forte"


def regrouper_categories_rares(df, col_reponse, effectif_min=5, regroupements_manuels=None):
    df = df.copy()
    mapping = {}

    if regroupements_manuels:
        for nouvelle_cat, anciennes_cats in regroupements_manuels.items():
            for cat in anciennes_cats:
                mapping[cat] = nouvelle_cat

    df[f"{col_reponse}_regroupe"] = df[col_reponse].map(mapping).fillna(df[col_reponse])

    effectifs = df[f"{col_reponse}_regroupe"].value_counts()
    categories_rares = effectifs[effectifs < effectif_min].index.tolist()

    if categories_rares:
        for cat in categories_rares:
            mapping[cat] = "Autres catégories rares"
        df[f"{col_reponse}_regroupe"] = df[f"{col_reponse}_regroupe"].replace(
            {cat: "Autres catégories rares" for cat in categories_rares}
        )

    return df, mapping


def test_posthoc_bonferroni(df, col_facteur, col_reponse, alpha_global=0.05):
    categories = sorted(df[col_reponse].dropna().unique())
    n_comparaisons = len(categories)
    alpha_corrige = alpha_global / n_comparaisons if n_comparaisons > 0 else alpha_global

    resultats = []
    for cat in categories:
        d = df.copy()
        d["_cible"] = np.where(d[col_reponse] == cat, cat, "Reste")
        table_2x2 = pd.crosstab(d[col_facteur], d["_cible"])

        try:
            chi2_p, p_p, ddl_p, expected_p = stats.chi2_contingency(table_2x2, correction=False)
            if (expected_p < 5).any():
                _, p_p = stats.fisher_exact(table_2x2) if table_2x2.shape == (2, 2) else (np.nan, np.nan)
                methode_p = "Fisher exact"
            else:
                methode_p = "Chi² Pearson"
        except ValueError:
            p_p = np.nan
            methode_p = "Non calculable (effectifs insuffisants)"

        resultats.append({
            "categorie": cat,
            "methode": methode_p,
            "p_value_brut": p_p,
            "p_value_bonferroni": min(p_p * n_comparaisons, 1.0) if not np.isnan(p_p) else np.nan,
            "significatif_apres_correction": (p_p < alpha_corrige) if not np.isnan(p_p) else False,
        })

    df_posthoc = pd.DataFrame(resultats).sort_values("p_value_bonferroni", na_position="last")

    df_valide = df_posthoc.dropna(subset=["p_value_brut"]).sort_values("p_value_brut").reset_index(drop=True)
    m = len(df_valide)
    if m > 0:
        df_valide["rang"] = np.arange(1, m + 1)
        df_valide["seuil_bh"] = (df_valide["rang"] / m) * alpha_global
        p_bh = df_valide["p_value_brut"] * m / df_valide["rang"]
        p_bh_ajuste = p_bh[::-1].cummin()[::-1]
        df_valide["p_value_bh"] = np.minimum(p_bh_ajuste, 1.0)
        df_valide["significatif_bh"] = df_valide["p_value_brut"] <= df_valide["seuil_bh"]

        df_posthoc = df_posthoc.merge(
            df_valide[["categorie", "p_value_bh", "significatif_bh"]],
            on="categorie", how="left"
        )
    else:
        df_posthoc["p_value_bh"] = np.nan
        df_posthoc["significatif_bh"] = False

    df_posthoc = df_posthoc.sort_values("p_value_bonferroni", na_position="last")
    return df_posthoc, alpha_corrige


def regrouper_genes_par_famille(df, col_gene, familles):
    df = df.copy()
    mapping_gene_famille = {}
    for famille, genes in familles.items():
        for gene in genes:
            mapping_gene_famille[gene] = famille

    df["famille_fonctionnelle"] = df[col_gene].map(mapping_gene_famille).fillna("Non catégorisé")
    return df, mapping_gene_famille


def graphe_barres_empilees(table, titre):
    fig, ax = plt.subplots(figsize=(9, 6))
    proportions = table.div(table.sum(axis=1), axis=0) * 100
    proportions.plot(kind="bar", stacked=True, ax=ax, colormap="tab20")
    ax.set_ylabel("Pourcentage de patients (%)")
    ax.set_xlabel("Régression développementale")
    ax.set_title(titre)
    ax.legend(title="Catégorie", bbox_to_anchor=(1.02, 1), loc="upper left")
    plt.xticks(rotation=0)
    plt.tight_layout()
    return fig


def graphe_heatmap_residus(table, expected, titre):
    residus_std = (table.values - expected) / np.sqrt(expected)
    fig, ax = plt.subplots(figsize=(9, 5))
    sns.heatmap(residus_std, annot=True, fmt=".2f", cmap="RdBu_r", center=0,
                xticklabels=table.columns, yticklabels=table.index, ax=ax,
                cbar_kws={"label": "Résidu standardisé"})
    ax.set_title(titre + "\n(résidus standardisés : |valeur| > 2 = contribution notable au Chi²)")
    ax.set_ylabel("Régression développementale")
    plt.tight_layout()
    return fig


def graphe_effectifs_bruts(table, titre):
    fig, ax = plt.subplots(figsize=(9, 6))
    table.T.plot(kind="bar", ax=ax, color=["#4C72B0", "#DD8452"])
    ax.set_ylabel("Nombre de patients")
    ax.set_xlabel("")
    ax.set_title(titre)
    ax.legend(title="Régression développementale")
    plt.xticks(rotation=30, ha="right")
    plt.tight_layout()
    return fig


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


def run(engine, config: dict) -> dict:
    """Point d'entrée appelé par l'API. `config` = corps JSON envoyé par React."""
    notes = Notes()
    sns.set_theme(style="whitegrid")

    classes_acmg = _OPTIONS_ACMG.get(
        config.get("acmg_classes_retenues", "Classe IV/V"), _OPTIONS_ACMG["Classe IV/V"]
    )
    effectif_min_gene = int(config.get("effectif_min_gene", 5))
    alpha = float(config.get("alpha", 0.05))

    notes("RAPPORT D'ANALYSE STATISTIQUE")
    notes("Projet : CDR NeuroExo-Predict — Registre Épilepsie pharmacorésistante (EPR)")
    notes("Test   : Régression développementale x Catégorie étiologique / Gène impliqué")
    notes("Méthode : Chi² d'indépendance")
    notes(f"Classes ACMG retenues pour l'analyse par gène : {'/'.join(classes_acmg)}")
    notes(f"Date de génération : {pd.Timestamp.now():%Y-%m-%d %H:%M}")

    rapport_completude(engine, notes)
    df_etio, df_gene = extraire_depuis_postgres(engine, classes_acmg)

    if df_etio.empty:
        raise ValueError(
            "Aucune donnée exploitable après filtrage NULL/'NA' pour le test "
            "régression x étiologie."
        )

    tables_a_sauvegarder = {}
    figures_a_sauvegarder = []
    figures_base64 = []
    resume_stats = {}

    # 1. Régression développementale x Catégorie étiologique
    notes("\n" + "=" * 70)
    notes("1. TEST — Régression développementale x Catégorie étiologique")
    notes("=" * 70)

    res = test_chi2_association(df_etio, "presence_regression", "categorie_etiologique")

    notes(f"\nEffectif analysé (n, après exclusion des NULL et 'NA') : {res['n_total']}")
    notes(f"\nTableau de contingence (effectifs observés) :\n{res['tableau_contingence']}")
    notes(f"\nEffectifs théoriques attendus sous H0 (indépendance) :\n"
          f"{res['effectifs_theoriques'].round(2)}")
    notes(f"\n% de cellules avec effectif théorique < 5 : {res['pct_cellules_sous_5']}%")
    notes(f"Méthode statistique appliquée : {res['methode']}")
    if res["alerte"]:
        notes(f"\n{res['alerte']}")

    notes(f"\nChi² = {res['chi2']:.3f}" if not np.isnan(res['chi2']) else "\nChi² : N/A (Fisher utilisé)")
    notes(f"Degrés de liberté (ddl) = {res['ddl']}")
    notes(f"p-value = {res['p_value']:.4f}")
    notes(f"V de Cramér = {res['cramer_v']:.3f} (force d'association : {interpreter_cramer_v(res['cramer_v'])})"
          if not np.isnan(res['cramer_v']) else "V de Cramér : N/A")
    notes(f"\nInterprétation : l'association entre régression développementale et "
          f"catégorie étiologique est {interpreter_p(res['p_value'], alpha)}.")

    tables_a_sauvegarder["contingence_etiologie"] = res["tableau_contingence"]

    categories_significatives_posthoc = []
    if res["p_value"] < alpha:
        notes("=> Cohérent avec la littérature : la régression développementale est un "
              "marqueur clinique fort orientant vers une encéphalopathie épileptique "
              "développementale (DEE) d'origine génétique (ILAE 2017).")

        notes("\n1ter. Test post-hoc par catégorie (correction de Bonferroni)")
        df_posthoc, alpha_corrige = test_posthoc_bonferroni(
            df_etio, "presence_regression", "categorie_etiologique", alpha_global=alpha
        )
        notes(f"\nSeuil alpha corrigé (Bonferroni, {len(df_posthoc)} comparaisons) : {alpha_corrige:.4f}")
        notes(f"\n{df_posthoc.to_string(index=False)}")
        tables_a_sauvegarder["posthoc_bonferroni_etiologie"] = df_posthoc

        categories_significatives_posthoc = df_posthoc[
            df_posthoc["significatif_apres_correction"]
        ]["categorie"].tolist()
        if categories_significatives_posthoc:
            notes(f"\n=> Catégorie(s) contribuant significativement à l'association globale "
                  f"après correction de Bonferroni : {', '.join(categories_significatives_posthoc)}.")
        else:
            notes("\n=> Aucune catégorie prise isolément ne reste significative après "
                  "correction de Bonferroni (effet diffus / faible puissance par catégorie).")

    if res["alerte"] and res["tableau_contingence"].shape != (2, 2):
        notes("\n1quater. Regroupement des catégories rares (condition de Cochran non "
              "respectée) et nouveau test Chi²")
        regroupements_manuels = {"Immune/Infectieuse": ["Immune", "Infectieuse"]}
        df_etio_regr, mapping_applique = regrouper_categories_rares(
            df_etio, "categorie_etiologique", effectif_min=5,
            regroupements_manuels=regroupements_manuels,
        )
        notes(f"Regroupements appliqués : {mapping_applique}")
        res_regr = test_chi2_association(
            df_etio_regr, "presence_regression", "categorie_etiologique_regroupe"
        )
        notes(f"\nTableau de contingence après regroupement :\n{res_regr['tableau_contingence']}")
        notes(f"Méthode : {res_regr['methode']}")
        notes(f"p-value = {res_regr['p_value']:.4f}")
        notes(f"\nInterprétation (après regroupement) : association "
              f"{interpreter_p(res_regr['p_value'], alpha)}.")

    if "Génétique" in df_etio["categorie_etiologique"].unique():
        df_bin = df_etio.copy()
        df_bin["etio_genetique_bin"] = np.where(
            df_bin["categorie_etiologique"] == "Génétique", "Génétique", "Autre"
        )
        orr = odds_ratio_2x2(df_bin, "presence_regression", "etio_genetique_bin", "Oui", "Génétique")
        notes("\n1bis. Odds Ratio — Régression (Oui) vs Étiologie Génétique")
        notes(f"Tableau 2x2 : a={orr['a']}, b={orr['b']}, c={orr['c']}, d={orr['d']}"
              + ("  (correction de Haldane-Anscombe appliquée)" if orr["correction_appliquee"] else ""))
        notes(f"OR = {orr['OR']:.2f}  (IC95% : {orr['IC95'][0]:.2f} - {orr['IC95'][1]:.2f})")
        resume_stats["odds_ratio_regression_vs_genetique"] = round(float(orr["OR"]), 2)
        resume_stats["or_ic95"] = [round(float(orr["IC95"][0]), 2), round(float(orr["IC95"][1]), 2)]

    fig1 = graphe_barres_empilees(
        res["tableau_contingence"],
        "Répartition des catégories étiologiques selon la présence de régression",
    )
    figures_a_sauvegarder.append(("epr4_etiologie_barres_empilees", fig1))
    figures_base64.append(figure_to_base64(fig1))

    fig2 = graphe_effectifs_bruts(
        res["tableau_contingence"],
        "Effectifs bruts par catégorie étiologique et statut de régression",
    )
    figures_a_sauvegarder.append(("epr4_etiologie_effectifs", fig2))
    figures_base64.append(figure_to_base64(fig2))

    fig3 = graphe_heatmap_residus(
        res["tableau_contingence"], res["effectifs_theoriques"].values,
        "Résidus standardisés — Régression x Étiologie",
    )
    figures_a_sauvegarder.append(("epr4_etiologie_residus", fig3))
    figures_base64.append(figure_to_base64(fig3))

    # 2. Régression développementale x Gène impliqué
    res_gene = None
    notes("\n" + "=" * 70)
    notes(f"2. TEST — Régression développementale x Gène impliqué "
          f"(variants {'/'.join(classes_acmg)} uniquement)")
    notes("=" * 70)

    if df_gene.empty:
        notes(f"\nAucune donnée exploitable : pas de variant classé {'/'.join(classes_acmg)} "
              "avec régression renseignée.")
    else:
        effectifs_gene = df_gene["gene_teste"].value_counts()
        genes_frequents = effectifs_gene[effectifs_gene >= effectif_min_gene].index
        df_gene["gene_regroupe"] = np.where(
            df_gene["gene_teste"].isin(genes_frequents), df_gene["gene_teste"],
            f"Autres gènes (n<{effectif_min_gene})",
        )

        notes(f"\nRépartition brute des gènes testés positifs (variants "
              f"{'/'.join(classes_acmg)}) :\n{effectifs_gene}")
        notes(f"\nGènes conservés individuellement (n >= {effectif_min_gene}) : "
              f"{list(genes_frequents) if len(genes_frequents) else 'aucun — tous regroupés'}")

        res_gene = test_chi2_association(df_gene, "presence_regression", "gene_regroupe")
        tables_a_sauvegarder["contingence_gene"] = res_gene["tableau_contingence"]

        notes(f"\nEffectif analysé (n) : {res_gene['n_total']}")
        notes(f"\nTableau de contingence (effectifs observés) :\n{res_gene['tableau_contingence']}")
        notes(f"Méthode statistique appliquée : {res_gene['methode']}")
        if res_gene["alerte"]:
            notes(f"\n{res_gene['alerte']}")
        notes(f"\nChi² = {res_gene['chi2']:.3f}" if not np.isnan(res_gene['chi2'])
              else "\nChi² : N/A (Fisher utilisé)")
        notes(f"p-value = {res_gene['p_value']:.4f}")
        notes(f"\nInterprétation : l'association entre régression développementale et "
              f"gène impliqué est {interpreter_p(res_gene['p_value'], alpha)}.")

        if res_gene["p_value"] >= alpha:
            notes("\nDiscussion — résultat non significatif attendu : effectifs faibles par "
                  "gène individuel (registre pédiatrique monocentrique), puissance limitée. "
                  "Regroupement par famille fonctionnelle proposé ci-dessous.")

            df_gene_fam, mapping_fam = regrouper_genes_par_famille(
                df_gene, "gene_teste", FAMILLES_FONCTIONNELLES_GENES
            )
            repartition_familles = df_gene_fam["famille_fonctionnelle"].value_counts()
            notes("\n2bis. Test complémentaire — Régression x Famille fonctionnelle de gène")
            notes(f"\nRépartition par famille fonctionnelle :\n{repartition_familles}")

            if repartition_familles.shape[0] >= 2 and df_gene_fam["famille_fonctionnelle"].nunique() >= 2:
                res_famille = test_chi2_association(
                    df_gene_fam, "presence_regression", "famille_fonctionnelle"
                )
                notes(f"\nChi² = {res_famille['chi2']:.3f}" if not np.isnan(res_famille['chi2'])
                      else "Chi² : N/A (Fisher utilisé)")
                notes(f"p-value = {res_famille['p_value']:.4f}")
                notes(f"\nInterprétation (par famille fonctionnelle) : association "
                      f"{interpreter_p(res_famille['p_value'], alpha)}.")

                fig4 = graphe_barres_empilees(
                    res_famille["tableau_contingence"],
                    "Répartition des familles fonctionnelles de gènes selon la régression",
                )
                figures_a_sauvegarder.append(("epr4_gene_famille_barres_empilees", fig4))
                figures_base64.append(figure_to_base64(fig4))

                resume_stats["p_value_famille_fonctionnelle"] = round(float(res_famille["p_value"]), 4)
            else:
                notes("\nEffectif insuffisant même après regroupement par famille fonctionnelle.")

        fig5 = graphe_barres_empilees(
            res_gene["tableau_contingence"], "Répartition des gènes selon la présence de régression"
        )
        figures_a_sauvegarder.append(("epr4_gene_barres_empilees", fig5))
        figures_base64.append(figure_to_base64(fig5))

        fig6 = graphe_effectifs_bruts(
            res_gene["tableau_contingence"], "Effectifs bruts par gène et statut de régression"
        )
        figures_a_sauvegarder.append(("epr4_gene_effectifs", fig6))
        figures_base64.append(figure_to_base64(fig6))

        fig7 = graphe_heatmap_residus(
            res_gene["tableau_contingence"], res_gene["effectifs_theoriques"].values,
            "Résidus standardisés — Régression x Gène",
        )
        figures_a_sauvegarder.append(("epr4_gene_residus", fig7))
        figures_base64.append(figure_to_base64(fig7))

    resume_stats.update({
        "n_patients_etiologie": int(res["n_total"]),
        "chi2_etiologie": None if np.isnan(res["chi2"]) else round(float(res["chi2"]), 3),
        "p_value_etiologie": round(float(res["p_value"]), 4),
        "cramer_v_etiologie": None if np.isnan(res["cramer_v"]) else round(float(res["cramer_v"]), 3),
        "association_etiologie_significative": bool(res["p_value"] < alpha),
        "methode_etiologie": res["methode"],
        "categories_significatives_posthoc": categories_significatives_posthoc,
    })
    if res_gene is not None:
        resume_stats.update({
            "n_patients_gene": int(res_gene["n_total"]),
            "chi2_gene": None if np.isnan(res_gene["chi2"]) else round(float(res_gene["chi2"]), 3),
            "p_value_gene": round(float(res_gene["p_value"]), 4),
            "association_gene_significative": bool(res_gene["p_value"] < alpha),
            "methode_gene": res_gene["methode"],
        })

    horodatage = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dossier_sortie = os.path.join(RESULTATS_DIR, f"epr_4_{horodatage}")
    chemin_dossier = _sauvegarder_resultats_sur_disque(
        dossier_sortie, notes, tables_a_sauvegarder, figures_a_sauvegarder
    )
    notes(f"\nTous les fichiers de sortie (CSV + PNG + notes.txt) ont été sauvegardés dans : {chemin_dossier}")

    return {
        "notes": notes.lines,
        "figures": figures_base64,
        "tableau": res["tableau_contingence"].reset_index().to_dict(orient="records"),
        "resume_stats": resume_stats,
    }
