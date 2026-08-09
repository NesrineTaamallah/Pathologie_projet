

import os
import re
import datetime
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy import stats

from common import figure_to_base64, Notes

PARAMETRES_SCHEMA = {}

RESULTATS_DIR = os.environ.get(
    "EPR_RESULTATS_DIR",
    os.path.join(os.path.dirname(__file__), "resultats"),
)

ALPHA = 0.05

SQL_EXTRACTION = """
WITH patients_epr AS (
    SELECT pseudonyme
    FROM patients
    WHERE registre = 'EPR'
),

consanguinite AS (
    SELECT
        pseudonyme,
        consanguinite_parentale,
        consanguinite_degre
    FROM epr_antecedents
    WHERE consanguinite_parentale IS NOT NULL
      AND consanguinite_parentale::text <> 'NA'
),

etiologie_toute AS (
    SELECT DISTINCT ON (pseudonyme)
        pseudonyme, categorie_etiologique
    FROM epr_etiologie
    WHERE etiologie_principale = TRUE
    ORDER BY pseudonyme, id
),

etiologie_ar AS (
    -- /!\ La base stocke les catégories étiologiques SANS accent
    -- ('Genetique', 'Metabolique', etc. — cf. seed_200_patients.sql),
    -- contrairement au script original qui filtrait sur 'Génétique' (avec
    -- accent). Avec l'accent, ce CTE ne matchait jamais aucun patient :
    -- la colonne AR_confirmee du tableau de contingence était donc
    -- entièrement à zéro, ce qui fait planter chi2_contingency
    -- ("expected frequencies has a zero element").
    SELECT DISTINCT et.pseudonyme
    FROM etiologie_toute et
    INNER JOIN epr_genetique g
            ON g.pseudonyme = et.pseudonyme
    WHERE et.categorie_etiologique = 'Genetique'
      AND g.mode_transmission = 'AR'
)

SELECT
    p.pseudonyme,
    c.consanguinite_parentale,
    c.consanguinite_degre,
    CASE WHEN ar.pseudonyme IS NOT NULL THEN 'AR_confirmee' ELSE 'Autre_etiologie' END
        AS groupe_etiologique
FROM patients_epr p
INNER JOIN consanguinite   c  ON c.pseudonyme  = p.pseudonyme
INNER JOIN etiologie_toute et ON et.pseudonyme = p.pseudonyme
LEFT JOIN  etiologie_ar    ar ON ar.pseudonyme = p.pseudonyme;
"""

SQL_QC = """
SELECT
    (SELECT COUNT(*) FROM patients WHERE registre = 'EPR')
        AS n_total_registre,

    (SELECT COUNT(*) FROM patients p
     INNER JOIN epr_antecedents a ON a.pseudonyme = p.pseudonyme
     WHERE p.registre = 'EPR'
       AND (a.consanguinite_parentale IS NULL OR a.consanguinite_parentale::text = 'NA'))
        AS n_exclus_consanguinite_manquante,

    (SELECT COUNT(*) FROM patients p
     WHERE p.registre = 'EPR'
       AND NOT EXISTS (
           SELECT 1 FROM epr_etiologie e
           WHERE e.pseudonyme = p.pseudonyme AND e.etiologie_principale = TRUE
       ))
        AS n_exclus_sans_etiologie_principale,

    (SELECT COUNT(*) FROM (
        SELECT pseudonyme FROM epr_etiologie
        WHERE etiologie_principale = TRUE
        GROUP BY pseudonyme HAVING COUNT(*) > 1
     ) t)
        AS n_patients_etiologie_principale_multiple
;
"""


def extraire_depuis_postgres(engine):
    df = pd.read_sql(SQL_EXTRACTION, engine)
    qc = pd.read_sql(SQL_QC, engine)

    
    df["consanguinite_parentale"] = df["consanguinite_parentale"].map({True: "Oui", False: "Non"})

    return df, qc.iloc[0].to_dict()


def test_tendance_cochran_armitage(df, col_categorie, ordre_scores, col_binaire):
    
    lignes = []
    for cat, score in ordre_scores.items():
        sous = df[df[col_categorie] == cat]
        n_i = len(sous)
        r_i = int(sous[col_binaire].sum())
        lignes.append((cat, score, n_i, r_i))

    N = sum(n_i for _, _, n_i, _ in lignes)
    R = sum(r_i for _, _, _, r_i in lignes)
    if N == 0 or R == 0 or R == N:
        return None

    p_barre = R / N
    T = sum(score * (r_i - n_i * p_barre) for _, score, n_i, r_i in lignes)
    somme_n_x2 = sum(n_i * score ** 2 for _, score, n_i, _ in lignes)
    somme_n_x = sum(n_i * score for _, score, n_i, _ in lignes)
    S = somme_n_x2 - (somme_n_x ** 2) / N
    var_T = p_barre * (1 - p_barre) * S
    if var_T <= 0:
        return None

    z = T / np.sqrt(var_T)
    chi2_tendance = z ** 2
    p_valeur = 2 * (1 - stats.norm.cdf(abs(z)))

    tableau = pd.DataFrame(lignes, columns=["categorie", "score", "n", "n_AR_confirmee"])
    tableau["pourcentage_AR"] = (tableau["n_AR_confirmee"] / tableau["n"].replace(0, np.nan) * 100).round(1)

    return {"tableau": tableau, "Z": z, "chi2": chi2_tendance, "ddl": 1, "p_value": p_valeur}


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
    notes = Notes()

    df, qc = extraire_depuis_postgres(engine)
    if df.empty:
        raise ValueError(
            "Aucun patient exploitable pour cette analyse (consanguinité et "
            "étiologie principale renseignées simultanément)."
        )

    notes("=" * 70)
    notes("FLUX D'INCLUSION DES PATIENTS (style STROBE)")
    notes("=" * 70)
    notes(f"Patients dans le registre épilepsie                          : {qc['n_total_registre']}")
    notes(f"  (-) exclus : consanguinité non renseignée (NULL/'NA')      : {qc['n_exclus_consanguinite_manquante']}")
    notes(f"  (-) exclus : aucune étiologie principale codée             : {qc['n_exclus_sans_etiologie_principale']}")
    notes(f"Patients inclus dans l'analyse (avant gestion des familles)  : {len(df)}")
    notes(f"\n[Anomalie de saisie signalée, non-excluante] patients avec "
          f">1 ligne 'etiologie_principale = TRUE' : {qc['n_patients_etiologie_principale_multiple']} "
          f"(dédoublonnés automatiquement par DISTINCT ON, voir requête SQL)")

    if len(df) < 10:
        raise ValueError(f"Effectif insuffisant pour ce test (n={len(df)} < 10).")

    
    df["famille_id"] = df["pseudonyme"].apply(
        lambda x: x if re.match(r"^EPR_SIM_\d+$", x) else re.sub(r"_\d+$", "", x)
    )

    tailles_familles = df.groupby("famille_id").size()
    familles_multiples = tailles_familles[tailles_familles > 1]

    notes("\n" + "=" * 70)
    notes("STRUCTURE FAMILIALE DÉTECTÉE (heuristique sur le pseudonyme)")
    notes("=" * 70)
    notes(f"Nombre de familles avec >1 patient inclus : {len(familles_multiples)}")
    notes(f"Nombre de patients concernés (non indépendants) : {int(familles_multiples.sum())}")

    df["est_AR_confirmee"] = (df["groupe_etiologique"] == "AR_confirmee").astype(int)
    df["consanguinite_bin"] = (df["consanguinite_parentale"] == "Oui").astype(int)

    df_dedup = (
        df.sort_values("pseudonyme")
          .drop_duplicates(subset="famille_id", keep="first")
          .reset_index(drop=True)
    )
    notes(f"\nN avant dédoublonnage familial : {len(df)}")
    notes(f"N après dédoublonnage familial (analyse principale) : {len(df_dedup)}")

    
    table_contingence = pd.crosstab(df_dedup["consanguinite_parentale"], df_dedup["groupe_etiologique"])
    table_contingence = table_contingence.reindex(index=["Oui", "Non"], columns=["AR_confirmee", "Autre_etiologie"]).fillna(0)

    notes("\n" + "=" * 70)
    notes("TABLEAU DE CONTINGENCE (analyse principale, 1 observation/famille)")
    notes("=" * 70)
    notes(table_contingence.to_string())

    
    lignes_vides = table_contingence.sum(axis=1) == 0
    colonnes_vides = table_contingence.sum(axis=0) == 0
    if lignes_vides.any() or colonnes_vides.any():
        raise ValueError(
            "Chi² non calculable : au moins une ligne/colonne du tableau de "
            "contingence est entièrement vide sur cet échantillon "
            f"(effectifs par ligne : {table_contingence.sum(axis=1).to_dict()}, "
            f"par colonne : {table_contingence.sum(axis=0).to_dict()}). "
            "Effectif insuffisant dans le sous-groupe AR_confirmee et/ou "
            "consanguinité 'Oui' pour ce test."
        )

    chi2, p_value, ddl, effectifs_attendus = stats.chi2_contingency(table_contingence, correction=True)
    effectifs_attendus_df = pd.DataFrame(
        effectifs_attendus, index=table_contingence.index, columns=table_contingence.columns
    )

    notes("\n" + "=" * 70)
    notes("EFFECTIFS THÉORIQUES ATTENDUS SOUS H0")
    notes("=" * 70)
    notes(effectifs_attendus_df.round(2).to_string())
    condition_validite = bool((effectifs_attendus >= 5).all())
    notes(f"\nCondition Ei >= 5 respectée : {condition_validite}")

    notes("\n" + "=" * 70)
    notes("RÉSULTAT DU TEST CHI² (correction de Yates)")
    notes("=" * 70)
    notes(f"Chi² = {chi2:.3f}   ddl = {ddl}   p-value = {p_value:.4g}")
    if p_value < ALPHA:
        notes(f"\n=> p < {ALPHA} : association statistiquement significative.")
    else:
        notes(f"\n=> p >= {ALPHA} : pas d'association significative détectée.")

    odds_ratio_fisher = p_value_fisher = None
    if not condition_validite:
        notes("\n" + "=" * 70)
        notes("Ei < 5 dans au moins une case -> test exact de Fisher recommandé")
        notes("=" * 70)
        odds_ratio_fisher, p_value_fisher = stats.fisher_exact(table_contingence)
        notes(f"OR (Fisher) = {odds_ratio_fisher:.3f}   p-value (Fisher) = {p_value_fisher:.4g}")

    a = table_contingence.loc["Oui", "AR_confirmee"]
    b = table_contingence.loc["Oui", "Autre_etiologie"]
    c = table_contingence.loc["Non", "AR_confirmee"]
    d = table_contingence.loc["Non", "Autre_etiologie"]
    if min(a, b, c, d) > 0:
        odds_ratio = (a * d) / (b * c)
        se_log_or = np.sqrt(1 / a + 1 / b + 1 / c + 1 / d)
        ic95_bas = np.exp(np.log(odds_ratio) - 1.96 * se_log_or)
        ic95_haut = np.exp(np.log(odds_ratio) + 1.96 * se_log_or)
    else:
        odds_ratio = ic95_bas = ic95_haut = float("nan")
        notes("\n[!] OR non calculable (au moins une case du tableau de contingence est nulle).")

    notes("\n" + "=" * 70)
    notes("ODDS RATIO (analyse principale, données dédoublonnées)")
    notes("=" * 70)
    notes(f"OR = {odds_ratio:.2f}   IC95% = [{ic95_bas:.2f} ; {ic95_haut:.2f}]" if odds_ratio == odds_ratio else "OR non calculable")

    n_total_dedup = table_contingence.to_numpy().sum()
    cramers_v = np.sqrt(chi2 / (n_total_dedup * (min(table_contingence.shape) - 1)))
    notes("\n" + "=" * 70)
    notes("CRAMÉR'S V")
    notes("=" * 70)
    notes(f"V = {cramers_v:.3f}  (repère : ~0.1 faible / ~0.3 modérée / ~0.5+ forte)")

    
    notes("\n" + "=" * 70)
    notes("ANALYSE DE SENSIBILITÉ : GEE (structure familiale, Exchangeable)")
    notes("=" * 70)
    or_gee = or_gee_bas = or_gee_haut = p_gee = None
    try:
        import statsmodels.api as sm
        import statsmodels.formula.api as smf

        modele_gee = smf.gee(
            "est_AR_confirmee ~ consanguinite_bin",
            groups="famille_id",
            data=df,
            family=sm.families.Binomial(),
            cov_struct=sm.cov_struct.Exchangeable(),
        )
        resultat_gee = modele_gee.fit()

        coef = resultat_gee.params["consanguinite_bin"]
        p_gee = float(resultat_gee.pvalues["consanguinite_bin"])
        ic_gee = resultat_gee.conf_int().loc["consanguinite_bin"]
        or_gee = float(np.exp(coef))
        or_gee_bas, or_gee_haut = float(np.exp(ic_gee[0])), float(np.exp(ic_gee[1]))

        notes(f"N (toutes observations, y compris jumeaux/fratrie) = {len(df)}")
        notes(f"OR (GEE, corrigé structure familiale) = {or_gee:.2f}   "
              f"IC95% = [{or_gee_bas:.2f} ; {or_gee_haut:.2f}]")
        notes(f"p-value (GEE) = {p_gee:.4g}")
        notes(f"\n=> Comparaison : OR naïf (dédoublonné) = {odds_ratio:.2f} (p={p_value:.4g})  "
              f"vs  OR GEE (corrigé) = {or_gee:.2f} (p={p_gee:.4g})")
        if (p_value < ALPHA) == (p_gee < ALPHA):
            notes("   Conclusion cohérente entre les deux approches.")
        else:
            notes("   /!\\ Conclusion DIVERGENTE entre les deux approches — "
                  "à investiguer avant toute interprétation clinique.")
    except Exception as err:
        notes(f"GEE non calculé (erreur : {err}). "
              f"Vérifier l'installation de statsmodels et la taille de l'échantillon.")

    
    notes("\n" + "=" * 70)
    notes("TEST DE TENDANCE DE COCHRAN-ARMITAGE (effet dose-réponse)")
    notes("=" * 70)
    notes("H0 : pas de tendance linéaire du % d'étiologie AR selon le degré de parenté")
    notes("Ordre croissant : Non (0) < 3e degré (1) < 2e degré (2) < 1er degré (3)")

    df_dedup["categorie_ordinale"] = np.where(
        df_dedup["consanguinite_parentale"] == "Non", "Non", df_dedup["consanguinite_degre"]
    )
    
    def _normaliser_degre(valeur):
        if pd.isna(valeur):
            return valeur
        v = (str(valeur).lower()
             .replace("é", "e").replace("è", "e").replace("ème", "e")
             .replace("eme", "e").strip())
        if v.startswith("1er"):
            return "1er degre"
        if v.startswith("2e") or v.startswith("2eme"):
            return "2e degre"
        if v.startswith("3e") or v.startswith("3eme"):
            return "3e degre"
        return "Non" if v == "non" else valeur

    df_dedup["categorie_ordinale"] = df_dedup["categorie_ordinale"].apply(_normaliser_degre)
    ordre_scores = {"Non": 0, "3e degre": 1, "2e degre": 2, "1er degre": 3}

    n_categorie_manquante = int(df_dedup["categorie_ordinale"].isna().sum())
    if n_categorie_manquante > 0:
        notes(f"(NB : {n_categorie_manquante} patients consanguins sans degré précisé, "
              "exclus du test de tendance)")

    resultat_ca = test_tendance_cochran_armitage(
        df_dedup.dropna(subset=["categorie_ordinale"]),
        "categorie_ordinale", ordre_scores, "est_AR_confirmee",
    )

    if resultat_ca is not None:
        notes(resultat_ca["tableau"].to_string(index=False))
        notes(f"\nZ = {resultat_ca['Z']:.3f}   Chi² tendance = {resultat_ca['chi2']:.3f}   "
              f"ddl = {resultat_ca['ddl']}   p-value = {resultat_ca['p_value']:.4g}")
        if resultat_ca["p_value"] < ALPHA:
            notes("\n=> Tendance dose-réponse statistiquement significative : le % d'étiologie AR "
                  "augmente avec la proximité du lien de parenté des parents.")
        else:
            notes("\n=> Pas de tendance dose-réponse significative détectée sur cet échantillon.")
    else:
        notes("Test de tendance non calculable (effectifs insuffisants ou dégénérés).")

    
    figures_base64 = []
    figures_a_sauvegarder = []

    fig1, ax1 = plt.subplots(figsize=(7, 5))
    table_contingence.plot(kind="bar", ax=ax1, color=["#2E86AB", "#A23B72"])
    ax1.set_title("Consanguinité parentale selon l'étiologie génétique\n(1 observation/famille)")
    ax1.set_xlabel("Consanguinité parentale")
    ax1.set_ylabel("Nombre de patients")
    ax1.legend(title="Groupe étiologique")
    plt.setp(ax1.get_xticklabels(), rotation=0)
    fig1.tight_layout()
    figures_a_sauvegarder.append(("graphique_contingence", fig1))
    figures_base64.append(figure_to_base64(fig1))

    prop = df_dedup.groupby("consanguinite_parentale")["est_AR_confirmee"].mean().reindex(["Oui", "Non"]) * 100
    effectifs = df_dedup.groupby("consanguinite_parentale").size().reindex(["Oui", "Non"])

    fig2, ax2 = plt.subplots(figsize=(6, 5))
    barres = ax2.bar(prop.index, prop.values, color=["#2E86AB", "#A23B72"], width=0.5)
    for rect, (idx, val) in zip(barres, prop.items()):
        ax2.text(rect.get_x() + rect.get_width() / 2, val + 1.5,
                  f"{val:.1f}%\n(n={effectifs[idx]})", ha="center", fontsize=10)
    ax2.set_ylabel("% d'étiologie AR confirmée")
    ax2.set_xlabel("Consanguinité parentale")
    ax2.set_title("Pourcentage d'étiologie AR confirmée\nselon la consanguinité parentale")
    ax2.set_ylim(0, min(100, prop.max() + 20) if len(prop) else 100)
    fig2.tight_layout()
    figures_a_sauvegarder.append(("graphique_proportions_AR", fig2))
    figures_base64.append(figure_to_base64(fig2))

    if resultat_ca is not None:
        tab = resultat_ca["tableau"]
        fig3, ax3 = plt.subplots(figsize=(7, 5))
        ax3.plot(tab["categorie"], tab["pourcentage_AR"], marker="o", linewidth=2, color="#F18F01")
        for x, y, n in zip(tab["categorie"], tab["pourcentage_AR"], tab["n"]):
            ax3.annotate(f"{y:.1f}%\n(n={n})", (x, y), textcoords="offset points",
                         xytext=(0, 10), ha="center", fontsize=9)
        ax3.set_ylabel("% d'étiologie AR confirmée")
        ax3.set_xlabel("Degré de consanguinité parentale")
        ax3.set_title("Effet dose-réponse : % AR selon le degré de consanguinité\n"
                       f"(Cochran-Armitage, p={resultat_ca['p_value']:.3g})")
        ax3.set_ylim(0, min(100, tab["pourcentage_AR"].max() + 20))
        fig3.tight_layout()
        figures_a_sauvegarder.append(("graphique_tendance_degre", fig3))
        figures_base64.append(figure_to_base64(fig3))

    
    lignes_resultats = [
        {"indicateur": "N_avant_dedoublonnage", "valeur": len(df)},
        {"indicateur": "N_apres_dedoublonnage", "valeur": len(df_dedup)},
        {"indicateur": "Chi2", "valeur": round(float(chi2), 4)},
        {"indicateur": "ddl", "valeur": int(ddl)},
        {"indicateur": "p_value_chi2", "valeur": round(float(p_value), 4)},
        {"indicateur": "OR", "valeur": round(float(odds_ratio), 3) if odds_ratio == odds_ratio else None},
        {"indicateur": "IC95_bas", "valeur": round(float(ic95_bas), 3) if ic95_bas == ic95_bas else None},
        {"indicateur": "IC95_haut", "valeur": round(float(ic95_haut), 3) if ic95_haut == ic95_haut else None},
        {"indicateur": "Cramers_V", "valeur": round(float(cramers_v), 3)},
    ]
    if odds_ratio_fisher is not None:
        lignes_resultats += [
            {"indicateur": "OR_Fisher", "valeur": round(float(odds_ratio_fisher), 3)},
            {"indicateur": "p_value_Fisher", "valeur": round(float(p_value_fisher), 4)},
        ]
    if or_gee is not None:
        lignes_resultats += [
            {"indicateur": "OR_GEE_corrige_famille", "valeur": round(or_gee, 3)},
            {"indicateur": "IC95_bas_GEE", "valeur": round(or_gee_bas, 3)},
            {"indicateur": "IC95_haut_GEE", "valeur": round(or_gee_haut, 3)},
            {"indicateur": "p_value_GEE", "valeur": round(p_gee, 4)},
        ]
    if resultat_ca is not None:
        lignes_resultats += [
            {"indicateur": "Chi2_tendance_CochranArmitage", "valeur": round(float(resultat_ca["chi2"]), 3)},
            {"indicateur": "p_value_tendance", "valeur": round(float(resultat_ca["p_value"]), 4)},
        ]

    resume_stats = {
        "n_avant_dedoublonnage": len(df),
        "n_apres_dedoublonnage": len(df_dedup),
        "n_familles_multiples": int(len(familles_multiples)),
        "chi2": round(float(chi2), 3),
        "p_value_chi2": round(float(p_value), 4),
        "significatif_chi2": bool(p_value < ALPHA),
        "cramers_v": round(float(cramers_v), 3),
    }
    if or_gee is not None:
        resume_stats["or_gee"] = round(or_gee, 3)
        resume_stats["p_value_gee"] = round(p_gee, 4)
    if resultat_ca is not None:
        resume_stats["p_value_tendance"] = round(float(resultat_ca["p_value"]), 4)
        resume_stats["tendance_significative"] = bool(resultat_ca["p_value"] < ALPHA)

    tables_a_sauvegarder = {
        "resultats_chi2_consanguinite_etiologie": pd.DataFrame(lignes_resultats),
    }

    horodatage = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dossier_sortie = os.path.join(RESULTATS_DIR, f"epr_6_{horodatage}")
    chemin_dossier = _sauvegarder_resultats_sur_disque(
        dossier_sortie, notes, tables_a_sauvegarder, figures_a_sauvegarder
    )
    notes(f"\nTous les fichiers de sortie (CSV + PNG + notes.txt) ont été sauvegardés dans : {chemin_dossier}")

    return {
        "notes": notes.lines,
        "figures": figures_base64,
        "tableau": lignes_resultats,
        "resume_stats": resume_stats,
    }