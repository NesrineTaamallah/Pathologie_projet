import os
import sys
import warnings
from pathlib import Path

import pandas as pd
import numpy as np
from scipy import stats
import statsmodels.api as sm
import statsmodels.formula.api as smf
import matplotlib.pyplot as plt

warnings.filterwarnings("ignore")

OUT_DIR = Path("/mnt/user-data/outputs")
OUT_DIR.mkdir(parents=True, exist_ok=True)

REF_CATEGORIE = "Inconnue"         
ALPHA = 0.05


QUERY_ETIOLOGIE_PHARMACORESISTANCE = """
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
  -- Les deux colonnes sont BOOLEAN en base (schema_registre.sql) : pas de
  -- convention 'NA' textuelle possible pour ce type, IS NOT NULL suffit
  -- deja a exclure les valeurs manquantes. Comparer un BOOLEAN a la chaine
  -- 'NA' fait echouer toute la requete (psycopg2.errors.
  -- InvalidTextRepresentation), meme bug que age_debut_crises_mois (NUMERIC)
  -- dans test1_epr.py.
;
"""

QUERY_CONTROLE_DOUBLONS_ETIOLOGIE = """
SELECT pseudonyme, COUNT(*) AS nb_etiologies_principales
FROM epr_etiologie
WHERE etiologie_principale = TRUE
GROUP BY pseudonyme
HAVING COUNT(*) > 1;
"""


def get_connection():
    
    import psycopg2

    return psycopg2.connect(
        host=os.environ.get("PGHOST", "localhost"),
        port=os.environ.get("PGPORT", "5432"),
        dbname=os.environ.get("PGDATABASE", "registre_neuroexo"),
        user=os.environ.get("PGUSER"),
        password=os.environ.get("PGPASSWORD"),
    )


def verifier_integrite_etiologie(conn) -> None:
    doublons = pd.read_sql_query(QUERY_CONTROLE_DOUBLONS_ETIOLOGIE, conn)
    if len(doublons) > 0:
        print(f"[ALERTE] {len(doublons)} patient(s) avec plusieurs étiologies "
              f"principales détectées côté base — vérifier la contrainte "
              f"uq_etiologie_principale :")
        print(doublons)
    else:
        print("[OK] Contrainte d'intégrité vérifiée : 1 étiologie principale / patient.")


def load_data() -> pd.DataFrame:
    
    print("[INFO] Connexion à PostgreSQL...")
    try:
        conn = get_connection()
    except Exception as e:
        print(f"[ERREUR] Connexion PostgreSQL impossible : {e}")
        print("-> Vérifiez les variables d'environnement PGHOST, PGPORT, "
              "PGDATABASE, PGUSER, PGPASSWORD.")
        sys.exit(1)

    try:
        verifier_integrite_etiologie(conn)
        print("[INFO] Exécution de la requête d'extraction "
              "(epr_etiologie / epr_pharmacoresistance / analytics.v_epr_cohorte_etiologie)...")
        df = pd.read_sql_query(QUERY_ETIOLOGIE_PHARMACORESISTANCE, conn)
        print(f"[OK] {len(df)} lignes extraites depuis PostgreSQL.")
        return df
    finally:
        conn.close()


COLONNES_NA_LITTERAL = [
    "categorie_etiologique",
    "statut_pharmacoresistance",
    "statut_dernier_suivi",
    "developpement_psychomoteur_avant_crises",
    "atcd_familiaux_epilepsie",
]


def nettoyer_convention_na(df: pd.DataFrame) -> pd.DataFrame:
    
    df = df.copy()
    for col in COLONNES_NA_LITTERAL:
        if col in df.columns:
            df[col] = df[col].replace({"NA": np.nan, "N/A": np.nan, "": np.nan})
    return df


def controle_qualite(df: pd.DataFrame) -> pd.DataFrame:
    print("\n" + "=" * 78)
    print("CONTRÔLE QUALITÉ DES DONNÉES")
    print("=" * 78)

    n_total = len(df)
    print(f"Nombre de patients extraits : {n_total}")

    doublons = df["pseudonyme"].duplicated().sum()
    if doublons > 0:
        print(f"[ALERTE] {doublons} pseudonyme(s) en double détecté(s) — "
              f"vérifier la contrainte uq_etiologie_principale côté base.")
    else:
        print("[OK] Aucun doublon de pseudonyme (1 étiologie principale / patient).")

    
    df = nettoyer_convention_na(df)

    print("\nDonnées manquantes par colonne (NULL réel + 'NA' littéral convertis) :")
    manquants = df.isna().sum()
    manquants = manquants[manquants > 0].sort_values(ascending=False)
    if len(manquants) > 0:
        for col, n in manquants.items():
            pct = 100 * n / n_total
            print(f"  - {col:45s} {n:4d} manquants ({pct:.1f}%)")
    else:
        print("  Aucune donnée manquante détectée.")

    
    avant = len(df)
    df = df[
        df["categorie_etiologique"].notna()
        & df["statut_pharmacoresistance"].notna()
    ].copy()
    exclus = avant - len(df)
    print(f"\nPatients exclus (étiologie ou statut manquant — NULL ou 'NA') : {exclus}")
    print(f"Effectif final analysable (Chi²) : {len(df)}")

    print("\nRépartition par catégorie étiologique :")
    print(df["categorie_etiologique"].value_counts(dropna=False))

    print("\nRépartition par statut de pharmacorésistance :")
    print(df["statut_pharmacoresistance"].value_counts(dropna=False))

    return df




def test_chi2(df: pd.DataFrame):
    print("\n" + "=" * 78)
    print("ÉTAPE 1 — TEST DU CHI² D'INDÉPENDANCE")
    print("=" * 78)

    table = pd.crosstab(df["categorie_etiologique"], df["statut_pharmacoresistance"])
    print("\nTableau de contingence (effectifs observés) :")
    print(table)

    chi2, p, ddl, attendu = stats.chi2_contingency(table)
    attendu_df = pd.DataFrame(attendu, index=table.index, columns=table.columns)

    print(f"\nChi² = {chi2:.3f}, ddl = {ddl}, p-value = {p:.4g}")
    if (attendu_df < 5).any().any():
        n_cellules_faibles = (attendu_df < 5).sum().sum()
        print(f"[ATTENTION] {n_cellules_faibles} cellule(s) avec effectif théorique < 5 "
              f"-> envisager le test exact de Fisher ou un regroupement de catégories.")

    n = table.sum().sum()
    min_dim = min(table.shape) - 1
    cramers_v = np.sqrt(chi2 / (n * min_dim)) if min_dim > 0 else np.nan
    print(f"V de Cramér (taille d'effet) = {cramers_v:.3f}")

    interpretation = "significative" if p < ALPHA else "non significative"
    print(f"\n=> Association {interpretation} au seuil de {ALPHA} entre étiologie ILAE "
          f"et statut de pharmacorésistance.")

    return {
        "table_observee": table,
        "table_attendue": attendu_df,
        "chi2": chi2,
        "ddl": ddl,
        "p_value": p,
        "cramers_v": cramers_v,
    }




def regression_logistique(df: pd.DataFrame, ajuster: bool = True):
    print("\n" + "=" * 78)
    print("ÉTAPE 2 — RÉGRESSION LOGISTIQUE (catégorie étiologique, réf. = "
          f"{REF_CATEGORIE})")
    print("=" * 78)

    d = df.copy()

    d["y"] = (d["statut_pharmacoresistance"].astype(str).str.strip().str.lower()
              .map({"oui": 1, "yes": 1, "1": 1, "true": 1,
                    "non": 0, "no": 0, "0": 0, "false": 0}))
    d = d.dropna(subset=["y"])

    d["categorie_etiologique"] = pd.Categorical(
        d["categorie_etiologique"],
        categories=[REF_CATEGORIE] + sorted(
            c for c in d["categorie_etiologique"].unique() if c != REF_CATEGORIE
        ),
    )

    formula = "y ~ C(categorie_etiologique, Treatment(reference='%s'))" % REF_CATEGORIE

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

    print(f"Formule du modèle : {formula}")
    print(f"Covariables d'ajustement retenues : {covariables_dispo or 'aucune (modèle brut)'}")

    d_model = d.dropna(subset=["y", "categorie_etiologique"] + covariables_dispo)
    print(f"Effectif utilisé dans le modèle : {len(d_model)} / {len(d)}")

    model = smf.logit(formula, data=d_model).fit(disp=0)
    print(model.summary())

    conf = model.conf_int()
    conf.columns = ["IC95%_bas", "IC95%_haut"]
    resultats = pd.DataFrame({
        "coefficient": model.params,
        "OR": np.exp(model.params),
        "IC95%_bas": np.exp(conf["IC95%_bas"]),
        "IC95%_haut": np.exp(conf["IC95%_haut"]),
        "p_value": model.pvalues,
    })
    resultats = resultats.drop(index="Intercept", errors="ignore")
    resultats["significatif"] = resultats["p_value"] < ALPHA

    print("\nTableau des Odds Ratios (référence = {}) :".format(REF_CATEGORIE))
    print(resultats.round(3))

    pseudo_r2 = model.prsquared
    print(f"\nPseudo-R² de McFadden = {pseudo_r2:.3f}")

    try:
        from statsmodels.stats.outliers_influence import variance_inflation_factor
    except ImportError:
        pass

    return model, resultats, d_model



def regression_multinomiale_si_besoin(df: pd.DataFrame, colonne_y_multiclasse: str):
    
    d = df.dropna(subset=[colonne_y_multiclasse, "categorie_etiologique"]).copy()
    y = pd.Categorical(d[colonne_y_multiclasse])
    X = pd.get_dummies(
        d["categorie_etiologique"], drop_first=False
    ).drop(columns=[REF_CATEGORIE], errors="ignore")
    X = sm.add_constant(X.astype(float))

    model = sm.MNLogit(y.codes, X).fit(disp=0)
    print(model.summary())
    return model




def graphiques(df: pd.DataFrame, chi2_res: dict, or_table: pd.DataFrame):
    prop = pd.crosstab(df["categorie_etiologique"], df["statut_pharmacoresistance"],
                        normalize="index") * 100
    fig, ax = plt.subplots(figsize=(8, 5))
    prop.plot(kind="bar", stacked=True, ax=ax, colormap="RdYlGn_r")
    ax.set_ylabel("% de patients")
    ax.set_xlabel("Catégorie étiologique ILAE")
    ax.set_title("Statut de pharmacorésistance par catégorie étiologique")
    ax.legend(title="Statut", bbox_to_anchor=(1.02, 1), loc="upper left")
    plt.xticks(rotation=30, ha="right")
    plt.tight_layout()
    fig.savefig(OUT_DIR / "fig1_repartition_pharmacoresistance_par_etiologie.png", dpi=150)
    plt.close(fig)

    or_plot = or_table[or_table.index.str.contains("categorie_etiologique")].copy()
    or_plot.index = (or_plot.index
                      .str.extract(r"\[T\.(.*)\]")[0].values)
    fig, ax = plt.subplots(figsize=(7, 4 + 0.4 * len(or_plot)))
    y_pos = np.arange(len(or_plot))
    ax.errorbar(
        or_plot["OR"], y_pos,
        xerr=[or_plot["OR"] - or_plot["IC95%_bas"], or_plot["IC95%_haut"] - or_plot["OR"]],
        fmt="o", color="black", ecolor="gray", capsize=4,
    )
    ax.axvline(1, linestyle="--", color="red", linewidth=1)
    ax.set_yticks(y_pos)
    ax.set_yticklabels(or_plot.index)
    ax.set_xlabel(f"Odds Ratio (réf. = {REF_CATEGORIE})")
    ax.set_title("Odds Ratios ajustés — étiologie vs pharmacorésistance")
    plt.tight_layout()
    fig.savefig(OUT_DIR / "fig2_forest_plot_odds_ratios.png", dpi=150)
    plt.close(fig)

    print(f"\n[OK] Graphiques enregistrés dans {OUT_DIR}")



def export_resultats(chi2_res: dict, or_table: pd.DataFrame, df: pd.DataFrame):
    path = OUT_DIR / "resultats_etiologie_pharmacoresistance.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        chi2_res["table_observee"].to_excel(writer, sheet_name="Contingence_observee")
        chi2_res["table_attendue"].round(2).to_excel(writer, sheet_name="Contingence_attendue")
        pd.DataFrame({
            "Chi2": [chi2_res["chi2"]],
            "ddl": [chi2_res["ddl"]],
            "p_value": [chi2_res["p_value"]],
            "Cramers_V": [chi2_res["cramers_v"]],
        }).to_excel(writer, sheet_name="Resume_Chi2", index=False)
        or_table.round(4).to_excel(writer, sheet_name="Odds_Ratios")
        df["categorie_etiologique"].value_counts().to_excel(writer, sheet_name="Effectifs_etiologie")
    print(f"[OK] Tableau de résultats exporté : {path}")
    return path




if __name__ == "__main__":
    df_raw = load_data()
    df = controle_qualite(df_raw)

    chi2_res = test_chi2(df)
    model, or_table, d_model = regression_logistique(df, ajuster=True)

    graphiques(df, chi2_res, or_table)
    export_resultats(chi2_res, or_table, df)

    print("\n" + "=" * 78)
    print("ANALYSE TERMINÉE — fichiers disponibles dans /mnt/user-data/outputs/")
    print("=" * 78)