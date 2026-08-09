

import sys
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.stats import chi2_contingency, fisher_exact
import statsmodels.api as sm
import statsmodels.formula.api as smf

ALPHA = 0.05

SEUIL_AGE_PRECOCE = 10  


REQUETE_SQL_EXTRACTION = """
SELECT
    p.pseudonyme,
    a.consanguinite_parentale,
    ic.age_premier_symptome_mois,
    ic.sexe,
    ev.forme_evolutive
FROM patients p
LEFT JOIN sep_antecedents a ON a.pseudonyme = p.pseudonyme
LEFT JOIN sep_identification_clinique ic ON ic.pseudonyme = p.pseudonyme
LEFT JOIN sep_evolution ev ON ev.pseudonyme = p.pseudonyme
WHERE p.registre = 'SEP';
"""

def charger_donnees_db(connection) -> tuple[pd.DataFrame, dict]:
    
    df_brut = pd.read_sql(REQUETE_SQL_EXTRACTION, connection)
    return _preparer(df_brut)


def charger_donnees(chemin_csv: str) -> tuple[pd.DataFrame, dict]:
    
    df_brut = pd.read_csv(
        chemin_csv, keep_default_na=False, na_values=[""],
    )

    colonnes_requises = {
        "pseudonyme", "consanguinite_parentale",
        "age_premier_symptome_mois", "sexe", "forme_evolutive",
    }
    manquantes = colonnes_requises - set(df_brut.columns)
    if manquantes:
        raise ValueError(f"Colonnes manquantes dans le CSV : {manquantes}")

    return _preparer(df_brut)


def _preparer(df_brut: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    
    df = df_brut.copy()
    n_total = len(df)

    def _est_na_texte(serie: pd.Series) -> pd.Series:
        
        return serie.notna() & (serie.astype(str).str.strip().str.upper() == "NA")

    masque_null_cons = df["consanguinite_parentale"].isna()
    masque_na_cons = _est_na_texte(df["consanguinite_parentale"])

    masque_null_age = df["age_premier_symptome_mois"].isna()

    masque_null_sexe = df["sexe"].isna()

    masque_null_forme = df["forme_evolutive"].isna()
    masque_na_forme = _est_na_texte(df["forme_evolutive"])

    stats_exclusion = {
        "n_total_avant_filtrage": n_total,
        "consanguinite_parentale_NULL_non_renseigne": int(masque_null_cons.sum()),
        "consanguinite_parentale_NA_non_applicable": int(masque_na_cons.sum()),
        "age_premier_symptome_mois_NULL_non_renseigne": int(masque_null_age.sum()),
        "sexe_NULL_non_renseigne": int(masque_null_sexe.sum()),
        "forme_evolutive_NULL_non_renseigne": int(masque_null_forme.sum()),
        "forme_evolutive_NA_non_applicable": int(masque_na_forme.sum()),
    }

    
    df = df[
        ~masque_null_cons & ~masque_na_cons
        & ~masque_null_age
        & ~masque_null_sexe
        & ~masque_null_forme & ~masque_na_forme
    ].copy()

    stats_exclusion["n_analysable_apres_filtrage"] = len(df)
    stats_exclusion["n_exclu_total"] = n_total - len(df)

    # consanguinite_parentale est de type BOOLEAN en base (voir schema_registre.sql) :
    # pandas/psycopg2 le renvoie comme bool Python, pas comme texte, donc l'ancien
    # .str.strip().str.capitalize() levait "Can only use .str accessor with string
    # values!". On mappe explicitement le booléen vers "Oui"/"Non" (valeurs attendues
    # plus bas, ex. consanguinite_bin = (consanguinite == "Oui")).
    df["consanguinite"] = df["consanguinite_parentale"].map({True: "Oui", False: "Non"})
    df["sexe"] = df["sexe"].str.strip().str.upper()
    df["forme_evolutive"] = df["forme_evolutive"].str.strip().str.upper()
    df["age_debut"] = df["age_premier_symptome_mois"] / 12.0
    df["age_categorie"] = np.where(
        df["age_debut"] < SEUIL_AGE_PRECOCE,
        f"Précoce (<{SEUIL_AGE_PRECOCE} ans)",
        f"Tardif (>={SEUIL_AGE_PRECOCE} ans)",
    )

    return df, stats_exclusion


def chi2_consanguinite_forme(df: pd.DataFrame) -> dict:
    table = pd.crosstab(df["consanguinite"], df["forme_evolutive"])
    return _executer_chi2(table, "Consanguinité x Forme évolutive")


def chi2_consanguinite_age(df: pd.DataFrame) -> dict:
    table = pd.crosstab(df["consanguinite"], df["age_categorie"])
    return _executer_chi2(table, "Consanguinité x Âge de début (catégorisé)")


def _executer_chi2(table: pd.DataFrame, titre: str) -> dict:
    
    chi2, p, dof, attendu = chi2_contingency(table)
    effectifs_faibles = (attendu < 5).any()
    part_cellules_faibles = float((attendu < 5).mean())

    resultat = {
        "titre": titre,
        "tableau_observe": table,
        "methode": "Chi²",
        "statistique": chi2,
        "ddl": dof,
        "p_value": p,
        "avertissement": None,
    }

    if effectifs_faibles and table.shape == (2, 2):
        odds_ratio, p_fisher = fisher_exact(table.values)
        resultat["methode"] = "Test exact de Fisher (effectifs attendus < 5)"
        resultat["p_value"] = p_fisher
        resultat["odds_ratio_fisher"] = odds_ratio
    elif effectifs_faibles:
        resultat["avertissement"] = (
            f"ATTENTION : {part_cellules_faibles:.0%} des cellules ont un "
            f"effectif attendu < 5 dans ce tableau {table.shape[0]}x{table.shape[1]}. "
            "L'approximation du Chi² n'est plus fiable et scipy ne propose pas de "
            "test exact équivalent (Fisher-Freeman-Halton) pour un tableau > 2x2. "
            "À interpréter avec prudence ; envisager de regrouper les catégories "
            "rares (ex. SP + progressive d'emblée vs RR) avec l'encadrante."
        )

    resultat["significatif"] = resultat["p_value"] < ALPHA
    return resultat



def regression_logistique_age(df: pd.DataFrame) -> pd.DataFrame:
   
    data = df.copy()
    data["y_precoce"] = (data["age_categorie"] == f"Précoce (<{SEUIL_AGE_PRECOCE} ans)").astype(int)
    data["consanguinite_bin"] = (data["consanguinite"] == "Oui").astype(int)
    data["sexe_bin"] = (data["sexe"] == "F").astype(int)

    modele = smf.logit("y_precoce ~ consanguinite_bin + sexe_bin", data=data).fit(disp=0)
    return _synthese_logit(
        modele, f"Âge de début précoce (<{SEUIL_AGE_PRECOCE} ans) ~ Consanguinité + Sexe"
    )

def regression_logistique_multinomiale(df: pd.DataFrame) -> pd.DataFrame:
    
    data = df.copy()
    data["consanguinite_bin"] = (data["consanguinite"] == "Oui").astype(int)
    data["sexe_bin"] = (data["sexe"] == "F").astype(int)

    if "RR" not in data["forme_evolutive"].unique():
        raise ValueError("La catégorie de référence 'RR' doit être présente dans les données.")

    data["forme_evolutive"] = pd.Categorical(
        data["forme_evolutive"],
        categories=["RR"] + [c for c in data["forme_evolutive"].unique() if c != "RR"],
    )

    endog = data["forme_evolutive"].cat.codes
    exog = sm.add_constant(data[["consanguinite_bin", "age_debut", "sexe_bin"]])

    modele = sm.MNLogit(endog, exog).fit(disp=0)

    categories = list(data["forme_evolutive"].cat.categories)[1:] 
    conf_int_full = modele.conf_int()
    lignes = []
    for i, cat in enumerate(categories):
        params = modele.params[i]
        pvals = modele.pvalues[i]
        conf_cat = conf_int_full.loc[str(i + 1)]
        for var in exog.columns:
            lignes.append({
                "comparaison": f"{cat} vs RR",
                "variable": var,
                "OR": np.exp(params[var]),
                "IC95_bas": np.exp(conf_cat.loc[var, "lower"]),
                "IC95_haut": np.exp(conf_cat.loc[var, "upper"]),
                "p_value": pvals[var],
            })
    return pd.DataFrame(lignes)


def _synthese_logit(modele, titre: str) -> pd.DataFrame:
    params = modele.params
    conf = modele.conf_int()
    pvals = modele.pvalues
    df_res = pd.DataFrame({
        "variable": params.index,
        "OR": np.exp(params.values),
        "IC95_bas": np.exp(conf[0].values),
        "IC95_haut": np.exp(conf[1].values),
        "p_value": pvals.values,
    })
    df_res.attrs["titre"] = titre
    return df_res



LIBELLES_VARIABLES = {
    "consanguinite_bin": "Consanguinité (Oui vs Non)",
    "sexe_bin": "Sexe (F vs M)",
    "age_debut": "Âge de début (par année)",
}


def graphique_forest_modele1(res_bin: pd.DataFrame, chemin_sortie: str) -> str:
    
    data = res_bin[res_bin["variable"] != "Intercept"].copy()
    data["label"] = data["variable"].map(LIBELLES_VARIABLES).fillna(data["variable"])
    data = data.iloc[::-1]

    fig, ax = plt.subplots(figsize=(7, 2 + 0.6 * len(data)))
    y_pos = np.arange(len(data))

    err_bas = data["OR"] - data["IC95_bas"]
    err_haut = data["IC95_haut"] - data["OR"]
    couleurs = ["#c0392b" if p < ALPHA else "#2c3e50" for p in data["p_value"]]

    ax.errorbar(
        data["OR"], y_pos, xerr=[err_bas, err_haut],
        fmt="o", color="black", ecolor="gray", elinewidth=1.5,
        capsize=4, markersize=7, zorder=2,
    )
    for i, (or_val, couleur) in enumerate(zip(data["OR"], couleurs)):
        ax.plot(or_val, i, "o", color=couleur, markersize=7, zorder=3)

    ax.axvline(1, color="black", linestyle="--", linewidth=1)
    ax.set_xscale("log")
    ax.set_yticks(y_pos)
    ax.set_yticklabels(data["label"])
    ax.set_xlabel("Odds Ratio (échelle log) — IC95%")
    ax.set_title(f"Modèle 1 — {res_bin.attrs.get('titre', '')}", fontsize=11)

    for i, (or_val, p_val, bas, haut) in enumerate(
        zip(data["OR"], data["p_value"], data["IC95_bas"], data["IC95_haut"])
    ):
        ax.text(
            haut * 1.15, i,
            f"OR={or_val:.2f} [{bas:.2f}-{haut:.2f}]  p={p_val:.3f}",
            va="center", fontsize=9,
        )

    ax.set_xlim(left=min(0.1, data["IC95_bas"].min() * 0.8))
    fig.savefig(chemin_sortie, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return chemin_sortie


def graphique_forest_modele2(res_multi: pd.DataFrame, chemin_sortie: str) -> str:
    
    data = res_multi[res_multi["variable"] != "const"].copy()
    data["label"] = data["variable"].map(LIBELLES_VARIABLES).fillna(data["variable"])
    data["ligne"] = data["comparaison"] + " — " + data["label"]
    data = data.iloc[::-1]

    comparaisons = list(dict.fromkeys(res_multi["comparaison"]))  # ordre stable
    palette = ["#2980b9", "#c0392b", "#27ae60", "#8e44ad"]
    couleur_par_comparaison = {c: palette[i % len(palette)] for i, c in enumerate(comparaisons)}

    fig, ax = plt.subplots(figsize=(8, 2 + 0.6 * len(data)))
    y_pos = np.arange(len(data))

    err_bas = data["OR"] - data["IC95_bas"]
    err_haut = data["IC95_haut"] - data["OR"]

    for i, row in enumerate(data.itertuples()):
        couleur = couleur_par_comparaison[row.comparaison]
        ax.errorbar(
            row.OR, i, xerr=[[row.OR - row.IC95_bas], [row.IC95_haut - row.OR]],
            fmt="o", color=couleur, ecolor=couleur, elinewidth=1.5,
            capsize=4, markersize=7,
        )
        ax.text(
            row.IC95_haut * 1.15, i,
            f"OR={row.OR:.2f} [{row.IC95_bas:.2f}-{row.IC95_haut:.2f}]  p={row.p_value:.3f}",
            va="center", fontsize=9,
        )

    ax.axvline(1, color="black", linestyle="--", linewidth=1)
    ax.set_xscale("log")
    ax.set_yticks(y_pos)
    ax.set_yticklabels(data["ligne"])
    ax.set_xlabel("Odds Ratio (échelle log) — IC95%")
    ax.set_title("Modèle 2 — Forme évolutive (réf. RR) ~ Consanguinité + Âge + Sexe", fontsize=11)

    
    handles = [
        plt.Line2D([0], [0], marker="o", color=couleur_par_comparaison[c], linestyle="", label=c)
        for c in comparaisons
    ]
    ax.legend(
        handles=handles, loc="lower center", bbox_to_anchor=(0.5, 1.02),
        ncol=len(comparaisons), fontsize=9, frameon=False,
    )

    ax.set_xlim(left=min(0.1, data["IC95_bas"].min() * 0.8))
    fig.savefig(chemin_sortie, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return chemin_sortie


def executer_test_sep6(
    chemin_csv: str,
    chemin_sortie: str = "resultats_test_sep_consanguinite.txt",
    chemin_graphe_modele1: str = "forest_plot_modele1_age_precoce.png",
    chemin_graphe_modele2: str = "forest_plot_modele2_forme_evolutive.png",
) -> list:
    
    df, stats_exclusion = charger_donnees(chemin_csv)
    fichiers_produits = [chemin_sortie]

    with open(chemin_sortie, "w", encoding="utf-8") as f:
        def ecrire(texte=""):
            print(texte)
            f.write(str(texte) + "\n")

        ecrire("=" * 70)
        ecrire("REGISTRE SEP PEDIATRIQUE — TEST : CONSANGUINITE ET PRESENTATION CLINIQUE")
        ecrire("=" * 70)
        ecrire(f"Effectif brut extrait : n = {stats_exclusion['n_total_avant_filtrage']}")
        ecrire("Exclusions (convention dictionnaire : NULL = non renseigné, "
               "'NA' = non applicable — jamais assimilés à 'Non') :")
        ecrire(f"  - consanguinite_parentale : "
               f"{stats_exclusion['consanguinite_parentale_NULL_non_renseigne']} NULL, "
               f"{stats_exclusion['consanguinite_parentale_NA_non_applicable']} 'NA'")
        ecrire(f"  - age_premier_symptome_mois : "
               f"{stats_exclusion['age_premier_symptome_mois_NULL_non_renseigne']} NULL")
        ecrire(f"  - sexe : "
               f"{stats_exclusion['sexe_NULL_non_renseigne']} NULL")
        ecrire(f"  - forme_evolutive : "
               f"{stats_exclusion['forme_evolutive_NULL_non_renseigne']} NULL, "
               f"{stats_exclusion['forme_evolutive_NA_non_applicable']} 'NA'")
        ecrire(f"Effectif total exclu (union, sans double comptage) : "
               f"{stats_exclusion['n_exclu_total']}")
        ecrire(f"Effectif analysé (après filtrage NULL/NA) : n = "
               f"{stats_exclusion['n_analysable_apres_filtrage']}")
        ecrire()

        ecrire("--- ETAPE 1 : Chi2 (association brute) ---")
        for res in [chi2_consanguinite_forme(df), chi2_consanguinite_age(df)]:
            ecrire(f"\n{res['titre']}")
            ecrire(res["tableau_observe"])
            ecrire(f"Méthode appliquée : {res['methode']}")
            ecrire(f"p-value = {res['p_value']:.4f}  "
                   f"({'significatif' if res['significatif'] else 'non significatif'} à alpha={ALPHA})")
            if res["avertissement"]:
                ecrire(res["avertissement"])

        ecrire("\n--- ETAPE 2 : Régression logistique (association ajustée) ---")

        res_bin = regression_logistique_age(df)
        ecrire(f"\nModèle 1 — {res_bin.attrs['titre']}")
        ecrire(res_bin.round(4).to_string(index=False))

        chemin_g1 = graphique_forest_modele1(res_bin, chemin_graphe_modele1)
        fichiers_produits.append(chemin_g1)
        ecrire(f"\n[Graphique] Forest plot Modèle 1 enregistré : {chemin_g1}")

        try:
            res_multi = regression_logistique_multinomiale(df)
            ecrire("\nModèle 2 — Forme évolutive (réf. RR) ~ Consanguinité + Âge de début + Sexe")
            ecrire(res_multi.round(4).to_string(index=False))

            chemin_g2 = graphique_forest_modele2(res_multi, chemin_graphe_modele2)
            fichiers_produits.append(chemin_g2)
            ecrire(f"\n[Graphique] Forest plot Modèle 2 enregistré : {chemin_g2}")
        except Exception as e:
            ecrire(f"\nModèle 2 non calculable avec les données fournies : {e}")

        ecrire("\n" + "=" * 70)
        ecrire("Interprétation : OR > 1 = association positive avec la consanguinité ;")
        ecrire("OR < 1 = association négative ; significatif si p < 0.05 et IC95%")
        ecrire("n'incluant pas 1.")
        ecrire("=" * 70)

    return fichiers_produits


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage : python test6_sep.py <chemin_vers_export.csv>")
        sys.exit(1)
    fichiers = executer_test_sep6(sys.argv[1])
    print("\nFichiers produits :")
    for chemin in fichiers:
        print(f"  - {chemin}")