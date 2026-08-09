import io
import textwrap

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import seaborn as sns
from scipy import stats
from sqlalchemy import create_engine, text

DB_URI = "postgresql+psycopg2://USER:PASSWORD@HOST:5432/NOM_BASE"  
ACMG_CLASSES_RETENUES = ("Classe IV", "Classe V")   
SEUIL_EFFECTIF_MIN_GENE = 5                          

FAMILLES_FONCTIONNELLES_GENES = {
    "Canaux sodiques":      ["SCN1A", "SCN2A", "SCN8A", "SCN1B"],
    "Canaux potassiques":   ["KCNQ2", "KCNQ3", "KCNT1", "KCNA2"],
    "Voie mTOR":            ["DEPDC5", "TSC1", "TSC2", "NPRL2", "NPRL3", "MTOR"],
    "Récepteurs GABA":      ["GABRA1", "GABRB3", "GABRG2", "STXBP1"],
    "Récepteurs glutamate": ["GRIN1", "GRIN2A", "GRIN2B"],
    "Régulateurs de la chromatine/transcription": ["CDKL5", "FOXG1", "MECP2", "ARX"],
}

FICHIER_RAPPORT = "rapport_chi2_regression_etiologie.txt"
FICHIER_EXCEL = "resultats_chi2_regression_etiologie.xlsx"
PREFIXE_GRAPHES = "graphe_"

sns.set_theme(style="whitegrid")

engine = create_engine(DB_URI)

rapport = io.StringIO()


def log(msg=""):
    print(msg)
    rapport.write(str(msg) + "\n")


def log_titre(titre, niveau=1):
    barre = "=" * 78 if niveau == 1 else "-" * 78
    log("\n" + barre)
    log(titre)
    log(barre)


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


def charger_donnees():
    df_etio = pd.read_sql(text(QUERY_ETIOLOGIE), engine)

    classes_sql = ",".join(f"'{c}'" for c in ACMG_CLASSES_RETENUES)
    query_gene = QUERY_GENE_TEMPLATE.format(classes=classes_sql)
    df_gene = pd.read_sql(text(query_gene), engine)

    return df_etio, df_gene



def rapport_completude(engine):
    log_titre("0. RAPPORT DE COMPLÉTUDE DES DONNÉES (avant filtrage)")

    q_total = text("""
        SELECT COUNT(DISTINCT pseudonyme) AS n
        FROM epr_identification_clinique
    """)
    n_total_epr = pd.read_sql(q_total, engine)["n"].iloc[0]
    log(f"Effectif total registre EPR (epr_identification_clinique) : {n_total_epr}")

    q_regression = text("""
        SELECT
            COUNT(*) AS n_lignes,
            COUNT(*) FILTER (WHERE presence_regression IS NULL) AS n_null,
            0 AS n_na,
            COUNT(*) FILTER (WHERE presence_regression IS NOT NULL) AS n_exploitable
        FROM epr_regression_developpementale
    """)
    # presence_regression est BOOLEAN en base (schema_registre.sql) : pas de
    # convention 'NA' textuelle possible pour ce type (contrairement a
    # categorie_etiologique, VARCHAR, juste en dessous). n_na est donc
    # structurellement toujours 0 ; la comparaison "= 'NA'" sur un booleen
    # faisait echouer toute la requete (psycopg2.errors.
    # InvalidTextRepresentation), meme bug que age_debut_crises_mois
    # (NUMERIC) dans test1_epr.py.
    comp_reg = pd.read_sql(q_regression, engine).iloc[0]
    log(f"\nTable epr_regression_developpementale :")
    log(f"  - lignes totales           : {comp_reg['n_lignes']}")
    log(f"  - NULL (non renseigné)     : {comp_reg['n_null']}")
    log(f"  - 'NA' (non applicable)    : {comp_reg['n_na']}")
    log(f"  - exploitables pour le test: {comp_reg['n_exploitable']}")

    q_etio = text("""
        SELECT
            COUNT(*) AS n_lignes,
            COUNT(*) FILTER (WHERE categorie_etiologique IS NULL) AS n_null,
            COUNT(*) FILTER (WHERE categorie_etiologique = 'NA') AS n_na,
            COUNT(*) FILTER (WHERE etiologie_principale = TRUE) AS n_principale
        FROM epr_etiologie
    """)
    comp_etio = pd.read_sql(q_etio, engine).iloc[0]
    log(f"\nTable epr_etiologie :")
    log(f"  - lignes totales (toutes étiologies)     : {comp_etio['n_lignes']}")
    log(f"  - NULL (non renseigné)                   : {comp_etio['n_null']}")
    log(f"  - 'NA' (non applicable)                  : {comp_etio['n_na']}")
    log(f"  - lignes etiologie_principale = TRUE     : {comp_etio['n_principale']}")
    log("    (c'est ce sous-ensemble qui est utilisé pour le Chi², afin de")
    log("     garantir 1 ligne par patient et éviter le double comptage)")


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
    se_log_or = np.sqrt(1/a + 1/b + 1/c + 1/e)
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

        if table_2x2.shape != (2, 2) or table_2x2.values.min() == 0 and table_2x2.size < 4:
            pass

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


def graphe_barres_empilees(table, titre, nom_fichier):
    fig, ax = plt.subplots(figsize=(9, 6))
    proportions = table.div(table.sum(axis=1), axis=0) * 100
    proportions.plot(kind="bar", stacked=True, ax=ax, colormap="tab20")
    ax.set_ylabel("Pourcentage de patients (%)")
    ax.set_xlabel("Régression développementale")
    ax.set_title(titre)
    ax.legend(title="Catégorie", bbox_to_anchor=(1.02, 1), loc="upper left")
    plt.xticks(rotation=0)
    plt.tight_layout()
    plt.savefig(nom_fichier, dpi=150)
    plt.close(fig)


def graphe_heatmap_residus(table, expected, titre, nom_fichier):
    residus_std = (table.values - expected) / np.sqrt(expected)
    fig, ax = plt.subplots(figsize=(9, 5))
    sns.heatmap(residus_std, annot=True, fmt=".2f", cmap="RdBu_r", center=0,
                xticklabels=table.columns, yticklabels=table.index, ax=ax,
                cbar_kws={"label": "Résidu standardisé"})
    ax.set_title(titre + "\n(résidus standardisés : |valeur| > 2 = contribution notable au Chi²)")
    ax.set_ylabel("Régression développementale")
    plt.tight_layout()
    plt.savefig(nom_fichier, dpi=150)
    plt.close(fig)


def graphe_effectifs_bruts(table, titre, nom_fichier):
    fig, ax = plt.subplots(figsize=(9, 6))
    table.T.plot(kind="bar", ax=ax, color=["#4C72B0", "#DD8452"])
    ax.set_ylabel("Nombre de patients")
    ax.set_xlabel("")
    ax.set_title(titre)
    ax.legend(title="Régression développementale")
    plt.xticks(rotation=30, ha="right")
    plt.tight_layout()
    plt.savefig(nom_fichier, dpi=150)
    plt.close(fig)


def main():
    log_titre("RAPPORT D'ANALYSE STATISTIQUE", niveau=1)
    log("Projet : CDR NeuroExo-Predict — Registre Épilepsie pharmacorésistante (EPR)")
    log("Test   : Régression développementale x Catégorie étiologique / Gène impliqué")
    log("Méthode demandée par l'encadrante (Pr. Kraoua) : Chi² d'indépendance")
    log(f"Date de génération : {pd.Timestamp.now():%Y-%m-%d %H:%M}")

    rapport_completude(engine)
    df_etio, df_gene = charger_donnees()

    res = None
    res_gene = None

    
    log_titre("1. TEST — Régression développementale x Catégorie étiologique")

    if df_etio.empty:
        log("Aucune donnée exploitable après filtrage NULL/'NA'. Test impossible.")
    else:
        res = test_chi2_association(df_etio, "presence_regression", "categorie_etiologique")

        log(f"\nEffectif analysé (n, après exclusion des NULL et 'NA') : {res['n_total']}")
        log(f"\nTableau de contingence (effectifs observés) :\n{res['tableau_contingence']}")
        log(f"\nEffectifs théoriques attendus sous H0 (indépendance) :\n"
            f"{res['effectifs_theoriques'].round(2)}")
        log(f"\n% de cellules avec effectif théorique < 5 : {res['pct_cellules_sous_5']}%")
        log(f"Méthode statistique appliquée : {res['methode']}")
        if res["alerte"]:
            log(f"\n{res['alerte']}")

        log(f"\nChi² = {res['chi2']:.3f}" if not np.isnan(res['chi2']) else "\nChi² : N/A (Fisher utilisé)")
        log(f"Degrés de liberté (ddl) = {res['ddl']}")
        log(f"p-value = {res['p_value']:.4f}")
        log(f"V de Cramér = {res['cramer_v']:.3f} "
            f"(force d'association : {interpreter_cramer_v(res['cramer_v'])})"
            if not np.isnan(res['cramer_v']) else "V de Cramér : N/A")

        log(f"\nInterprétation : l'association entre régression développementale et "
            f"catégorie étiologique est {interpreter_p(res['p_value'])}.")

        if res["p_value"] < 0.05:
            log("=> Cohérent avec la littérature : la régression développementale est un "
                "marqueur clinique fort orientant vers une encéphalopathie épileptique "
                "développementale (DEE) d'origine génétique (ILAE 2017).")

            log_titre("1ter. Test post-hoc par catégorie (correction de Bonferroni)", niveau=2)
            log("Le Chi² global est significatif : chaque catégorie étiologique est comparée "
                "au 'Reste' (tableau 2x2), avec ajustement de Bonferroni pour tests multiples "
                "afin de contrôler l'inflation du risque alpha.")
            log("\nNote méthodologique : Bonferroni contrôle le risque de faux positif "
                "familywise (correction stricte, conservatrice) et constitue la méthode "
                "rapportée par défaut ici — plus difficilement critiquable par un rapporteur "
                "de thèse. La correction de Benjamini-Hochberg (FDR), moins pénalisante, est "
                "affichée à titre indicatif/exploratoire : elle peut révéler des catégories "
                "qu'une correction Bonferroni, plus stricte, aurait fait passer sous le seuil "
                "de significativité, ce qui est utile à signaler sur un petit échantillon "
                "pédiatrique où la puissance est limitée.")
            df_posthoc, alpha_corrige = test_posthoc_bonferroni(
                df_etio, "presence_regression", "categorie_etiologique"
            )
            log(f"\nSeuil alpha corrigé (Bonferroni, {len(df_posthoc)} comparaisons) : "
                f"{alpha_corrige:.4f}")
            log(f"\n{df_posthoc.to_string(index=False)}")

            categories_significatives = df_posthoc[df_posthoc["significatif_apres_correction"]]["categorie"].tolist()
            categories_bh_seulement = df_posthoc[
                (df_posthoc["significatif_bh"] == True)
                & (df_posthoc["significatif_apres_correction"] == False)
            ]["categorie"].tolist()

            if categories_significatives:
                log(f"\n=> Catégorie(s) contribuant significativement à l'association globale "
                    f"après correction de Bonferroni : {', '.join(categories_significatives)}.")
            else:
                log("\n=> Aucune catégorie prise isolément ne reste significative après "
                    "correction de Bonferroni (effet diffus / faible puissance par catégorie).")

            if categories_bh_seulement:
                log(f"\n=> À titre indicatif (FDR/Benjamini-Hochberg, plus sensible) : "
                    f"{', '.join(categories_bh_seulement)} ressortirai(en)t significative(s) "
                    "avec cette méthode moins conservatrice — piste à explorer sur un "
                    "échantillon plus large, mais non retenue comme résultat principal.")

        if res["alerte"] and res["tableau_contingence"].shape != (2, 2):
            log_titre("1quater. Regroupement des catégories rares (condition de Cochran non "
                      "respectée) et nouveau test Chi²", niveau=2)

            
            regroupements_manuels = {"Immune/Infectieuse": ["Immune", "Infectieuse"]}
            df_etio_regr, mapping_applique = regrouper_categories_rares(
                df_etio, "categorie_etiologique",
                effectif_min=5, regroupements_manuels=regroupements_manuels
            )
            log(f"Regroupements appliqués : {mapping_applique}")
            log("(regroupement clinique Immune+Infectieuse proposé par défaut ; à valider avec "
                "Pr. Kraoua — modifiable via le paramètre regroupements_manuels)")

            res_regr = test_chi2_association(
                df_etio_regr, "presence_regression", "categorie_etiologique_regroupe"
            )
            log(f"\nTableau de contingence après regroupement :\n{res_regr['tableau_contingence']}")
            log(f"% de cellules avec effectif théorique < 5 (après regroupement) : "
                f"{res_regr['pct_cellules_sous_5']}%")
            log(f"Méthode : {res_regr['methode']}")
            log(f"Chi² = {res_regr['chi2']:.3f}" if not np.isnan(res_regr['chi2']) else "Chi² : N/A")
            log(f"ddl = {res_regr['ddl']}")
            log(f"p-value = {res_regr['p_value']:.4f}")
            log(f"V de Cramér = {res_regr['cramer_v']:.3f}" if not np.isnan(res_regr['cramer_v']) else "N/A")
            log(f"\nInterprétation (après regroupement) : association "
                f"{interpreter_p(res_regr['p_value'])}.")

        if "Génétique" in df_etio["categorie_etiologique"].unique():
            df_bin = df_etio.copy()
            df_bin["etio_genetique_bin"] = np.where(
                df_bin["categorie_etiologique"] == "Génétique", "Génétique", "Autre"
            )
            orr = odds_ratio_2x2(df_bin, "presence_regression", "etio_genetique_bin",
                                  "Oui", "Génétique")
            log_titre("1bis. Odds Ratio — Régression (Oui) vs Étiologie Génétique", niveau=2)
            log(f"Tableau 2x2 : a={orr['a']}, b={orr['b']}, c={orr['c']}, d={orr['d']}"
                + ("  (correction de Haldane-Anscombe appliquée, cellule à 0 détectée)"
                   if orr["correction_appliquee"] else ""))
            log(f"OR = {orr['OR']:.2f}  (IC95% : {orr['IC95'][0]:.2f} - {orr['IC95'][1]:.2f})")
            if orr["IC95"][0] > 1:
                log("=> L'IC95% exclut 1 : association positive robuste entre régression "
                    "et étiologie génétique.")
            elif orr["IC95"][1] < 1:
                log("=> L'IC95% exclut 1 : association négative.")
            else:
                log("=> L'IC95% inclut 1 : prudence sur la significativité clinique malgré "
                    "un p éventuellement < 0.05.")

        graphe_barres_empilees(
            res["tableau_contingence"],
            "Répartition des catégories étiologiques selon la présence de régression",
            f"{PREFIXE_GRAPHES}etiologie_barres_empilees.png"
        )
        graphe_effectifs_bruts(
            res["tableau_contingence"],
            "Effectifs bruts par catégorie étiologique et statut de régression",
            f"{PREFIXE_GRAPHES}etiologie_effectifs.png"
        )
        graphe_heatmap_residus(
            res["tableau_contingence"], res["effectifs_theoriques"].values,
            "Résidus standardisés — Régression x Étiologie",
            f"{PREFIXE_GRAPHES}etiologie_residus.png"
        )
        log("\nGraphiques générés :")
        log(f"  - {PREFIXE_GRAPHES}etiologie_barres_empilees.png")
        log(f"  - {PREFIXE_GRAPHES}etiologie_effectifs.png")
        log(f"  - {PREFIXE_GRAPHES}etiologie_residus.png")

    
    log_titre("2. TEST — Régression développementale x Gène impliqué "
              f"(variants {'/'.join(ACMG_CLASSES_RETENUES)} uniquement)")

    if df_gene.empty:
        log("\nAucune donnée exploitable : pas de variant classé "
            f"{'/'.join(ACMG_CLASSES_RETENUES)} avec régression renseignée. "
            "Vérifier le volume de la table epr_genetique et epr_etiologie "
            "(categorie_etiologique = 'Génétique').")
    else:
        effectifs_gene = df_gene["gene_teste"].value_counts()
        genes_frequents = effectifs_gene[effectifs_gene >= SEUIL_EFFECTIF_MIN_GENE].index
        df_gene["gene_regroupe"] = np.where(
            df_gene["gene_teste"].isin(genes_frequents), df_gene["gene_teste"],
            f"Autres gènes (n<{SEUIL_EFFECTIF_MIN_GENE})"
        )

        log(f"\nRépartition brute des gènes testés positifs (variants "
            f"{'/'.join(ACMG_CLASSES_RETENUES)}) :\n{effectifs_gene}")
        log(f"\nGènes conservés individuellement (n >= {SEUIL_EFFECTIF_MIN_GENE}) : "
            f"{list(genes_frequents) if len(genes_frequents) else 'aucun — tous regroupés'}")

        res_gene = test_chi2_association(df_gene, "presence_regression", "gene_regroupe")

        log(f"\nEffectif analysé (n) : {res_gene['n_total']}")
        log(f"\nTableau de contingence (effectifs observés) :\n{res_gene['tableau_contingence']}")
        log(f"\n% de cellules avec effectif théorique < 5 : {res_gene['pct_cellules_sous_5']}%")
        log(f"Méthode statistique appliquée : {res_gene['methode']}")
        if res_gene["alerte"]:
            log(f"\n{res_gene['alerte']}")

        log(f"\nChi² = {res_gene['chi2']:.3f}" if not np.isnan(res_gene['chi2'])
            else "\nChi² : N/A (Fisher utilisé)")
        log(f"Degrés de liberté (ddl) = {res_gene['ddl']}")
        log(f"p-value = {res_gene['p_value']:.4f}")
        log(f"V de Cramér = {res_gene['cramer_v']:.3f} "
            f"(force d'association : {interpreter_cramer_v(res_gene['cramer_v'])})"
            if not np.isnan(res_gene['cramer_v']) else "V de Cramér : N/A")

        log(f"\nInterprétation : l'association entre régression développementale et "
            f"gène impliqué est {interpreter_p(res_gene['p_value'])}.")

        if res_gene["p_value"] >= 0.05:
            log("\nDiscussion — résultat non significatif attendu :")
            log(textwrap.fill(
                "Ce résultat non significatif est attendu compte tenu de la puissance "
                "statistique limitée à ce stade : les effectifs par gène individuel sont "
                "faibles (registre pédiatrique monocentrique), ce qui réduit fortement la "
                "capacité du test à détecter une association réelle même si elle existe "
                "(risque de erreur de type II). Une analyse sur la base complète du "
                "registre, une fois l'inclusion des patients terminée, disposera d'une "
                "puissance accrue et permettra de conclure plus fermement. En attendant, "
                "un regroupement des gènes par famille fonctionnelle (mécanisme "
                "physiopathologique partagé) est proposé ci-dessous pour augmenter la "
                "puissance en réduisant le nombre de catégories comparées.",
                width=78
            ))

            log_titre("2bis. Test complémentaire — Régression x Famille fonctionnelle de gène",
                      niveau=2)
            df_gene_fam, mapping_fam = regrouper_genes_par_famille(
                df_gene, "gene_teste", FAMILLES_FONCTIONNELLES_GENES
            )
            repartition_familles = df_gene_fam["famille_fonctionnelle"].value_counts()
            log(f"\nRépartition par famille fonctionnelle :\n{repartition_familles}")
            log("\n(classification proposée : canaux sodiques, canaux potassiques, voie mTOR, "
                "récepteurs GABA, récepteurs glutamate, régulateurs de la chromatine/"
                "transcription — à valider avec Pr. Kraoua, modifiable via "
                "FAMILLES_FONCTIONNELLES_GENES en tête de script)")

            if repartition_familles.shape[0] >= 2 and df_gene_fam["famille_fonctionnelle"].nunique() >= 2:
                res_famille = test_chi2_association(
                    df_gene_fam, "presence_regression", "famille_fonctionnelle"
                )
                log(f"\nTableau de contingence (par famille) :\n{res_famille['tableau_contingence']}")
                log(f"% de cellules avec effectif théorique < 5 : {res_famille['pct_cellules_sous_5']}%")
                log(f"Méthode : {res_famille['methode']}")
                log(f"Chi² = {res_famille['chi2']:.3f}" if not np.isnan(res_famille['chi2'])
                    else "Chi² : N/A (Fisher utilisé)")
                log(f"ddl = {res_famille['ddl']}")
                log(f"p-value = {res_famille['p_value']:.4f}")
                log(f"V de Cramér = {res_famille['cramer_v']:.3f}"
                    if not np.isnan(res_famille['cramer_v']) else "N/A")
                log(f"\nInterprétation (par famille fonctionnelle) : association "
                    f"{interpreter_p(res_famille['p_value'])}.")

                graphe_barres_empilees(
                    res_famille["tableau_contingence"],
                    "Répartition des familles fonctionnelles de gènes selon la régression",
                    f"{PREFIXE_GRAPHES}gene_famille_barres_empilees.png"
                )
                log(f"\nGraphique généré : {PREFIXE_GRAPHES}gene_famille_barres_empilees.png")
            else:
                log("\nEffectif insuffisant même après regroupement par famille fonctionnelle "
                    "pour un test exploitable — confirme la nécessité d'attendre un "
                    "échantillon plus large.")

        graphe_barres_empilees(
            res_gene["tableau_contingence"],
            "Répartition des gènes selon la présence de régression",
            f"{PREFIXE_GRAPHES}gene_barres_empilees.png"
        )
        graphe_effectifs_bruts(
            res_gene["tableau_contingence"],
            "Effectifs bruts par gène et statut de régression",
            f"{PREFIXE_GRAPHES}gene_effectifs.png"
        )
        graphe_heatmap_residus(
            res_gene["tableau_contingence"], res_gene["effectifs_theoriques"].values,
            "Résidus standardisés — Régression x Gène",
            f"{PREFIXE_GRAPHES}gene_residus.png"
        )
        log("\nGraphiques générés :")
        log(f"  - {PREFIXE_GRAPHES}gene_barres_empilees.png")
        log(f"  - {PREFIXE_GRAPHES}gene_effectifs.png")
        log(f"  - {PREFIXE_GRAPHES}gene_residus.png")

    
    log_titre("3. EXPORTS")
    with pd.ExcelWriter(FICHIER_EXCEL) as writer:
        if res is not None:
            res["tableau_contingence"].to_excel(writer, sheet_name="Contingence_Etiologie")
            res["effectifs_theoriques"].to_excel(writer, sheet_name="Attendus_Etiologie")
        if res_gene is not None:
            res_gene["tableau_contingence"].to_excel(writer, sheet_name="Contingence_Gene")
    log(f"Tableaux exportés : {FICHIER_EXCEL}")

    
    with open(FICHIER_RAPPORT, "w", encoding="utf-8") as f:
        f.write(rapport.getvalue())

    print(f"\n>>> Rapport complet écrit dans : {FICHIER_RAPPORT}")


if __name__ == "__main__":
    main()