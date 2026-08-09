"""
EPR test 7 — Quotient intellectuel (QI) x fréquence/durée des crises,
ajusté sur la durée d'évolution de l'épilepsie et l'étiologie.

Converti depuis test_analyse_statistique/EPR/test7_epr.py vers le pattern
structuré (voir epr/test6_consanguinite_etiologie.py pour la même
démarche sur le test 6). `run(engine, config)` retourne toujours
{"notes", "figures", "tableau", "resume_stats"}, exploitable directement
par le frontend (mêmes composants que pour les tests SEP).

Corrections méthodologiques / de fidélité aux données reprises du script
original :
  1. `p.registre = 'EPRLEPSIE'` n'existe pas dans le schéma réel — la
     valeur stockée est `'EPR'` (cf. seed_200_patients.sql / patients).
     Avec 'EPRLEPSIE', la requête ne retournait jamais aucune ligne.
  2. La base stocke les catégories étiologiques SANS accent ('Genetique',
     'Metabolique', ... — cf. seed_200_patients.sql), contrairement au
     script original qui utilisait des libellés accentués
     ('Génétique', 'Métabolique') dans la catégorisation ordonnée. Avec
     l'accent, toutes les lignes "Genetique"/"Metabolique" de la base
     tombaient hors catégorie (NaN silencieux côté pandas.Categorical).
  3. Mesures répétées (plusieurs bilans QI par patient) : erreurs-types
     clusterisées par pseudonyme (cov_type='cluster'), comme dans le
     script original.
  4. Trois modèles (brut / log-fréquence / + nb_ae_essayes en analyse de
     sensibilité) + VIF + Breusch-Pagan + normalité des résidus,
     identiques à la logique du script original.
"""

import os
import datetime
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from scipy import stats
import statsmodels.api as sm
import statsmodels.formula.api as smf
from statsmodels.stats.outliers_influence import variance_inflation_factor
from statsmodels.stats.diagnostic import het_breuschpagan
from statsmodels.graphics.gofplots import qqplot

from common import figure_to_base64, Notes

PARAMETRES_SCHEMA = {}

RESULTATS_DIR = os.environ.get(
    "EPR_RESULTATS_DIR",
    os.path.join(os.path.dirname(__file__), "resultats"),
)

ALPHA = 0.05

# --- Requête principale d'extraction --------------------------------------
# /!\ p.registre = 'EPR' (pas 'EPRLEPSIE', qui n'existe pas dans le schéma
# réel — voir docstring du module).
SQL_EXTRACTION = """
WITH qi_valide AS (
    SELECT
        bn.id,
        bn.pseudonyme,
        bn.date_bilan,
        bn.qi
    FROM epr_bilan_neuropsy bn
    WHERE bn.qi IS NOT NULL
),
freq_appariee AS (
    -- pour chaque bilan QI, la mesure de fréquence la plus récente
    -- rapportée avant (ou le jour de) la date du bilan
    SELECT DISTINCT ON (q.id)
        q.id AS bilan_id,
        q.pseudonyme,
        q.date_bilan,
        q.qi,
        fc.frequence_normalisee_mois,
        fc.duree_moyenne_min,
        fc.date_rapport
    FROM qi_valide q
    LEFT JOIN epr_frequence_crises fc
        ON fc.pseudonyme = q.pseudonyme
        AND fc.date_rapport <= q.date_bilan
        AND fc.frequence_normalisee_mois IS NOT NULL
    ORDER BY q.id, fc.date_rapport DESC
),
etiologie_principale AS (
    SELECT pseudonyme, categorie_etiologique
    FROM epr_etiologie
    WHERE etiologie_principale = TRUE
),
onset AS (
    SELECT pseudonyme, age_debut_crises_mois
    FROM epr_identification_clinique
    WHERE age_debut_crises_mois IS NOT NULL
),
nb_ae AS (
    SELECT pseudonyme, COUNT(*) AS nb_ae_essayes
    FROM epr_liste_ae
    GROUP BY pseudonyme
)
SELECT
    f.pseudonyme,
    f.date_bilan,
    f.qi,
    f.frequence_normalisee_mois,
    f.duree_moyenne_min,
    o.age_debut_crises_mois,
    p.age                           AS age_patient_ans,
    p.date_inclusion,
    et.categorie_etiologique,
    COALESCE(a.nb_ae_essayes, 0)    AS nb_ae_essayes
FROM freq_appariee f
JOIN patients p               ON p.pseudonyme = f.pseudonyme
LEFT JOIN etiologie_principale et ON et.pseudonyme = f.pseudonyme
LEFT JOIN onset o             ON o.pseudonyme = f.pseudonyme
LEFT JOIN nb_ae a             ON a.pseudonyme = f.pseudonyme
WHERE p.registre = 'EPR'
  AND f.frequence_normalisee_mois IS NOT NULL
  AND (et.categorie_etiologique IS NOT NULL AND et.categorie_etiologique != 'NA');
"""

# Ordre clinique des catégories étiologiques — SANS accent, cf. docstring
# (point 2). Toute catégorie hors de cette liste (ex. valeur inattendue)
# devient NaN via pandas.Categorical, comme dans le script original.
CATEGORIES_ETIOLOGIQUES = [
    "Inconnue", "Structurelle", "Genetique", "Metabolique",
    "Infectieuse", "Immune",
]


def extraire_depuis_postgres(engine):
    df = pd.read_sql(SQL_EXTRACTION, engine)
    df["date_bilan"] = pd.to_datetime(df["date_bilan"])
    df["date_inclusion"] = pd.to_datetime(df["date_inclusion"])
    return df


def _sauvegarder_resultats_sur_disque(dossier: str, notes: Notes, tables: dict, figures_fig: list):
    """Écrit sur disque, dans `dossier`, TOUT ce que produisait le script
    original : notes.txt (log complet), un CSV par tableau, un PNG par
    figure. Retourne le chemin absolu du dossier créé."""
    os.makedirs(dossier, exist_ok=True)

    with open(os.path.join(dossier, "notes.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(notes.lines))

    for nom, df in tables.items():
        if df is not None and not df.empty:
            df.to_csv(os.path.join(dossier, f"{nom}.csv"), index=False)

    for nom, fig in figures_fig:
        fig.savefig(os.path.join(dossier, f"{nom}.png"), dpi=150, bbox_inches="tight")

    return os.path.abspath(dossier)


def _test_normalite(notes: Notes, nom, serie):
    stat, p = stats.shapiro(serie.dropna())
    notes(f"Shapiro-Wilk {nom} : W={stat:.3f}, p={p:.4f} "
          f"{'(non normal -> Spearman justifié)' if p < 0.05 else '(normal)'}")


def run(engine, config: dict) -> dict:
    """Point d'entrée appelé par l'API. `config` non utilisé (aucun paramètre pour ce test)."""
    notes = Notes()

    df = extraire_depuis_postgres(engine)
    if df.empty:
        raise ValueError(
            "Aucun bilan QI exploitable pour cette analyse (QI, fréquence des "
            "crises et étiologie principale doivent être renseignés)."
        )

    df["decalage_inclusion_bilan_mois"] = (
        (df["date_bilan"] - df["date_inclusion"]).dt.days / 30.44
    )
    df["age_au_bilan_mois"] = df["age_patient_ans"] * 12 + df["decalage_inclusion_bilan_mois"]
    df["duree_epilepsie_mois"] = df["age_au_bilan_mois"] - df["age_debut_crises_mois"]

    n_avant = int((df["duree_epilepsie_mois"] < 0).sum())
    if n_avant:
        notes(f"[Attention] {n_avant} lignes avec durée d'épilepsie négative — exclues.")
    df = df[df["duree_epilepsie_mois"] >= 0].copy()

    if len(df) < 10:
        raise ValueError(f"Effectif insuffisant pour ce test (n={len(df)} < 10).")

    n_multi = int((df.groupby("pseudonyme").size() > 1).sum())
    notes(f"Échantillon final : {df.shape[0]} bilans QI sur {df['pseudonyme'].nunique()} patients "
          f"({n_multi} patients avec plusieurs bilans QI)")
    if n_multi:
        notes("-> Mesures répétées détectées : les erreurs-types de la régression seront "
              "clusterisées par pseudonyme (cov_type='cluster') pour ne pas sous-estimer "
              "la variance (indépendance violée sinon).")

    # ------------------------------------------------------------------
    # Tests de normalité
    # ------------------------------------------------------------------
    notes("\n" + "=" * 70)
    notes("TESTS DE NORMALITÉ (Shapiro-Wilk)")
    notes("=" * 70)
    _test_normalite(notes, "QI", df["qi"])
    _test_normalite(notes, "Fréquence crises (mois)", df["frequence_normalisee_mois"])
    _test_normalite(notes, "Durée épilepsie (mois)", df["duree_epilepsie_mois"])

    df["log_frequence_mois"] = np.log1p(df["frequence_normalisee_mois"])

    # ------------------------------------------------------------------
    # Corrélations de Spearman bivariées
    # ------------------------------------------------------------------
    notes("\n" + "=" * 70)
    notes("CORRÉLATIONS DE SPEARMAN (bivariées)")
    notes("=" * 70)
    correlations_bivariees = {}
    for var in ["frequence_normalisee_mois", "duree_moyenne_min", "duree_epilepsie_mois"]:
        sous_df = df.dropna(subset=["qi", var])
        if len(sous_df) < 3:
            notes(f"QI vs {var} : échantillon insuffisant (n={len(sous_df)})")
            continue
        rho, p = stats.spearmanr(sous_df["qi"], sous_df[var])
        correlations_bivariees[var] = (rho, p, len(sous_df))
        notes(f"QI vs {var} : rho={rho:.3f}, p={p:.4f}, n={len(sous_df)}")

    # ------------------------------------------------------------------
    # Régression multiple (modèles A / B / C)
    # ------------------------------------------------------------------
    df_reg = df.dropna(subset=[
        "qi", "frequence_normalisee_mois", "log_frequence_mois", "duree_epilepsie_mois",
        "duree_moyenne_min", "categorie_etiologique", "age_debut_crises_mois",
    ]).copy()

    df_reg["categorie_etiologique"] = pd.Categorical(
        df_reg["categorie_etiologique"], categories=CATEGORIES_ETIOLOGIQUES,
    )
    n_categorie_hors_liste = int(df_reg["categorie_etiologique"].isna().sum())
    if n_categorie_hors_liste:
        notes(f"\n[Attention] {n_categorie_hors_liste} lignes avec une catégorie étiologique "
              f"hors de la liste attendue {CATEGORIES_ETIOLOGIQUES} — exclues de la régression.")
    df_reg = df_reg.dropna(subset=["categorie_etiologique"])

    if len(df_reg) < 10:
        raise ValueError(
            f"Effectif insuffisant pour la régression multiple (n={len(df_reg)} < 10) "
            "après exclusion des lignes incomplètes."
        )

    cluster_kw = dict(cov_type="cluster", cov_kwds={"groups": df_reg["pseudonyme"]})

    formule_base = (
        "qi ~ {freq} + duree_epilepsie_mois + duree_moyenne_min "
        "+ age_debut_crises_mois + C(categorie_etiologique)"
    )

    notes("\n" + "=" * 70)
    notes("MODÈLE A : fréquence brute (sans nb_ae_essayes)")
    notes("=" * 70)
    modele_a = smf.ols(formule_base.format(freq="frequence_normalisee_mois"),
                        data=df_reg).fit(**cluster_kw)
    notes(modele_a.summary().as_text())

    notes("\n" + "=" * 70)
    notes("MODÈLE B : log(fréquence) (sans nb_ae_essayes) — modèle retenu")
    notes("=" * 70)
    modele_b = smf.ols(formule_base.format(freq="log_frequence_mois"),
                        data=df_reg).fit(**cluster_kw)
    notes(modele_b.summary().as_text())
    notes(f"\nComparaison ajustement : R² ajusté brut={modele_a.rsquared_adj:.3f} "
          f"vs log={modele_b.rsquared_adj:.3f}")

    notes("\n" + "=" * 70)
    notes("MODÈLE C : log(fréquence) + nb_ae_essayes (analyse de sensibilité)")
    notes("=" * 70)
    modele_c = smf.ols(
        formule_base.format(freq="log_frequence_mois") + " + nb_ae_essayes",
        data=df_reg,
    ).fit(**cluster_kw)
    notes(modele_c.summary().as_text())

    # ------------------------------------------------------------------
    # Comparaison des coefficients B vs C (masquage éventuel)
    # ------------------------------------------------------------------
    notes("\n" + "=" * 70)
    notes("COMPARAISON DES COEFFICIENTS — modèle B (sans nb_ae_essayes) vs modèle C (avec)")
    notes("=" * 70)
    variables_communes = [v for v in modele_b.params.index if v in modele_c.params.index]
    notes(f"{'Variable':40s} {'Coef. B (sans)':>16s} {'Coef. C (avec)':>16s} {'Variation %':>12s}")
    for v in variables_communes:
        cb, cc = modele_b.params[v], modele_c.params[v]
        var_pct = 100 * abs(cc - cb) / abs(cb) if abs(cb) > 1e-9 else np.nan
        alerte = " <-- masquage probable" if (not np.isnan(var_pct) and var_pct > 20) else ""
        notes(f"{v:40s} {cb:16.3f} {cc:16.3f} {var_pct:11.1f}%{alerte}")

    notes(f"\nCoefficient propre de nb_ae_essayes dans le modèle C : "
          f"{modele_c.params.get('nb_ae_essayes', np.nan):.3f} "
          f"(p={modele_c.pvalues.get('nb_ae_essayes', np.nan):.4f})")
    notes("Interprétation : nb_ae_essayes n'est pas une cause du QI mais un marqueur "
          "indirect de sévérité/pharmacorésistance (plus d'échecs d'AE reflète un cas "
          "plus sévère). Un coefficient élevé et très significatif sur cette variable, "
          "combiné à une variation importante des coefficients fréquence/durée entre "
          "B et C, indique que nb_ae_essayes capture une partie de l'information que "
          "la fréquence/durée est censée mesurer -> risque de sur-ajustement "
          "(over-adjustment bias). Le modèle B (sans nb_ae_essayes) est donc le "
          "modèle principal ; le modèle C reste une analyse de sensibilité.")

    # ------------------------------------------------------------------
    # VIF (multicolinéarité, modèle B)
    # ------------------------------------------------------------------
    notes("\n" + "=" * 70)
    notes("VIF (multicolinéarité, modèle B)")
    notes("=" * 70)
    x_vif = df_reg[["log_frequence_mois", "duree_epilepsie_mois", "duree_moyenne_min",
                     "age_debut_crises_mois"]].dropna()
    x_vif = sm.add_constant(x_vif)
    lignes_vif = []
    for i, col in enumerate(x_vif.columns):
        if col == "const":
            continue
        vif = variance_inflation_factor(x_vif.values, i)
        lignes_vif.append({"variable": col, "VIF": round(float(vif), 3)})
        notes(f"{col} : VIF={vif:.2f} {'(>5, à surveiller)' if vif > 5 else ''}")

    # ------------------------------------------------------------------
    # Breusch-Pagan + normalité des résidus (modèle B, OLS classique)
    # ------------------------------------------------------------------
    notes("\n" + "=" * 70)
    notes("DIAGNOSTICS DES RÉSIDUS (modèle B, OLS classique non clusterisé)")
    notes("=" * 70)
    modele_ols_classique = smf.ols(formule_base.format(freq="log_frequence_mois"),
                                    data=df_reg).fit()
    bp_stat, bp_p, bp_f, bp_fp = het_breuschpagan(
        modele_ols_classique.resid, modele_ols_classique.model.exog
    )
    notes(f"Breusch-Pagan : LM stat={bp_stat:.3f}, p={bp_p:.4f} "
          f"{'(hétéroscédasticité détectée -> envisager des erreurs-types robustes HC3)' if bp_p < 0.05 else '(homoscédasticité non rejetée)'}")

    resid = modele_ols_classique.resid
    w_stat, w_p = stats.shapiro(resid)
    notes(f"Shapiro-Wilk des résidus : W={w_stat:.3f}, p={w_p:.4f} "
          f"{'(résidus non normaux -> IC/p-values à interpréter avec prudence)' if w_p < 0.05 else '(normalité non rejetée)'}")

    # ------------------------------------------------------------------
    # Corrélations de Spearman stratifiées par étiologie
    # ------------------------------------------------------------------
    notes("\n" + "=" * 70)
    notes("SPEARMAN QI vs FRÉQUENCE, stratifié par étiologie principale")
    notes("=" * 70)
    correlations_stratifiees = []
    for etio, sous_groupe in df.dropna(subset=["categorie_etiologique"]).groupby("categorie_etiologique"):
        sous_groupe = sous_groupe.dropna(subset=["qi", "frequence_normalisee_mois"])
        if len(sous_groupe) < 5:
            notes(f"{etio} : n={len(sous_groupe)} (insuffisant, non testé)")
            continue
        rho, p = stats.spearmanr(sous_groupe["qi"], sous_groupe["frequence_normalisee_mois"])
        correlations_stratifiees.append({"etiologie": etio, "rho": round(float(rho), 3),
                                          "p_value": round(float(p), 4), "n": len(sous_groupe)})
        notes(f"{etio} (n={len(sous_groupe)}) : rho={rho:.3f}, p={p:.4f}")

    # ------------------------------------------------------------------
    # Discussion / limites (générée à partir des résultats)
    # ------------------------------------------------------------------
    notes("\n" + "=" * 70)
    notes("DISCUSSION / LIMITES")
    notes("=" * 70)
    notes(
        "1. nb_ae_essayes n'est pas une variable causale : c'est un marqueur indirect "
        "de sévérité/pharmacorésistance, pas un déterminant biologique du QI. Le "
        "modèle B (sans cette variable) est retenu comme modèle principal ; le "
        "modèle C est rapporté uniquement en analyse de sensibilité."
    )

    rho_duree_crise, p_duree_crise = (np.nan, np.nan)
    sous_dm = df.dropna(subset=["qi", "duree_moyenne_min"])
    if len(sous_dm) >= 3:
        rho_duree_crise, p_duree_crise = stats.spearmanr(sous_dm["qi"], sous_dm["duree_moyenne_min"])
    coef_dm = modele_b.params.get("duree_moyenne_min", np.nan)
    p_coef_dm = modele_b.pvalues.get("duree_moyenne_min", np.nan)
    notes(
        f"2. Durée d'une crise (duree_moyenne_min) : corrélation bivariée QI vs durée "
        f"moyenne : rho={rho_duree_crise:.3f}, p={p_duree_crise:.3f} ; coefficient ajusté "
        f"dans le modèle B : {coef_dm:.3f} (p={p_coef_dm:.4f}). Une absence d'effet ici "
        "n'est pas un oubli méthodologique : la durée d'un épisode critique (minutes) et "
        "la durée de la maladie (chronicité, en mois) sont deux dimensions distinctes."
    )

    coef_duree_epi = modele_b.params.get("duree_epilepsie_mois", np.nan)
    p_duree_epi = modele_b.pvalues.get("duree_epilepsie_mois", np.nan)
    notes(
        f"3. Durée de l'épilepsie (duree_epilepsie_mois) : coefficient ajusté "
        f"{coef_duree_epi:.3f} (p={p_duree_epi:.4f}). Un résultat tendanciel ici "
        "n'invalide pas l'hypothèse d'un effet de la chronicité — il peut refléter "
        "une puissance statistique limitée (taille d'échantillon) ou un effet réel "
        "mais modeste, à confirmer sur un échantillon plus large."
    )

    # ------------------------------------------------------------------
    # Graphes
    # ------------------------------------------------------------------
    sns.set_style("whitegrid")
    figures_base64 = []
    figures_a_sauvegarder = []

    vars_continues = {
        "qi": "QI",
        "frequence_normalisee_mois": "Fréquence des crises (crises/mois)",
        "log_frequence_mois": "log(1 + fréquence des crises)",
        "duree_moyenne_min": "Durée moyenne d'une crise (min)",
        "duree_epilepsie_mois": "Durée de l'épilepsie (mois)",
    }

    fig1, axes = plt.subplots(2, 3, figsize=(15, 8))
    for ax, (col, label) in zip(axes.flat, vars_continues.items()):
        sns.histplot(df[col].dropna(), kde=True, ax=ax, color="steelblue")
        ax.set_title(label)
        ax.set_xlabel("")
    axes.flat[-1].axis("off")
    fig1.tight_layout()
    figures_a_sauvegarder.append(("01_distributions", fig1))
    figures_base64.append(figure_to_base64(fig1))

    paires = [
        ("frequence_normalisee_mois", "Fréquence des crises (crises/mois)"),
        ("log_frequence_mois", "log(1 + fréquence des crises)"),
        ("duree_moyenne_min", "Durée moyenne d'une crise (min)"),
        ("duree_epilepsie_mois", "Durée de l'épilepsie (mois)"),
    ]
    fig2, axes2 = plt.subplots(2, 2, figsize=(12, 10))
    for ax, (col, label) in zip(axes2.flat, paires):
        sous_df = df.dropna(subset=["qi", col])
        sns.regplot(data=sous_df, x=col, y="qi", ax=ax,
                    scatter_kws={"alpha": 0.6}, line_kws={"color": "firebrick"})
        if len(sous_df) >= 3:
            rho, p = stats.spearmanr(sous_df["qi"], sous_df[col])
            ax.set_title(f"{label}\nrho={rho:.2f}, p={p:.3f}, n={len(sous_df)}")
        else:
            ax.set_title(f"{label} (n insuffisant)")
        ax.set_xlabel(label)
        ax.set_ylabel("QI")
    fig2.tight_layout()
    figures_a_sauvegarder.append(("02_qi_vs_frequence_duree", fig2))
    figures_base64.append(figure_to_base64(fig2))

    fig3, ax3 = plt.subplots(figsize=(9, 5))
    ordre = (df.dropna(subset=["categorie_etiologique", "qi"])
               .groupby("categorie_etiologique")["qi"].median()
               .sort_values().index)
    sns.boxplot(data=df, x="categorie_etiologique", y="qi", order=ordre,
                ax=ax3, color="lightsteelblue", showfliers=False)
    sns.stripplot(data=df, x="categorie_etiologique", y="qi", order=ordre,
                  ax=ax3, color="black", alpha=0.5, size=4, jitter=True)
    ax3.set_xlabel("Étiologie principale")
    ax3.set_ylabel("QI")
    ax3.set_title("QI par étiologie principale")
    plt.setp(ax3.get_xticklabels(), rotation=30, ha="right")
    fig3.tight_layout()
    figures_a_sauvegarder.append(("03_qi_par_etiologie", fig3))
    figures_base64.append(figure_to_base64(fig3))

    fig4, ax4 = plt.subplots(figsize=(9, 6))
    sns.scatterplot(data=df.dropna(subset=["categorie_etiologique"]),
                     x="frequence_normalisee_mois", y="qi",
                     hue="categorie_etiologique", ax=ax4, alpha=0.75, s=60)
    ax4.set_xlabel("Fréquence des crises (crises/mois)")
    ax4.set_ylabel("QI")
    ax4.set_title("QI vs fréquence des crises, par étiologie")
    ax4.legend(title="Étiologie", bbox_to_anchor=(1.02, 1), loc="upper left")
    fig4.tight_layout()
    figures_a_sauvegarder.append(("04_qi_vs_frequence_par_etiologie", fig4))
    figures_base64.append(figure_to_base64(fig4))

    fig5, ax5 = plt.subplots(figsize=(8, 6))
    mat_corr = df[list(vars_continues.keys()) + ["age_debut_crises_mois", "nb_ae_essayes"]].corr(method="spearman")
    sns.heatmap(mat_corr, annot=True, fmt=".2f", cmap="coolwarm", center=0,
                square=True, ax=ax5, cbar_kws={"label": "rho de Spearman"})
    ax5.set_title("Matrice de corrélation (Spearman)")
    fig5.tight_layout()
    figures_a_sauvegarder.append(("05_heatmap_correlations", fig5))
    figures_base64.append(figure_to_base64(fig5))

    fig6, axes6 = plt.subplots(1, 2, figsize=(10, 4))
    qqplot(resid, line="s", ax=axes6[0])
    axes6[0].set_title("Q-Q plot des résidus")
    axes6[1].scatter(modele_ols_classique.fittedvalues, resid, alpha=0.6)
    axes6[1].axhline(0, color="red", linestyle="--")
    axes6[1].set_xlabel("Valeurs prédites (QI)")
    axes6[1].set_ylabel("Résidus")
    axes6[1].set_title("Résidus vs valeurs prédites")
    fig6.tight_layout()
    figures_a_sauvegarder.append(("diagnostics_residus", fig6))
    figures_base64.append(figure_to_base64(fig6))

    # ------------------------------------------------------------------
    # Tableau de résultats (exploité par le frontend) + resume_stats
    # ------------------------------------------------------------------
    lignes_resultats = [
        {"indicateur": "N_bilans_QI", "valeur": int(len(df))},
        {"indicateur": "N_patients", "valeur": int(df["pseudonyme"].nunique())},
        {"indicateur": "N_regression", "valeur": int(len(df_reg))},
        {"indicateur": "R2_ajuste_modeleA_brut", "valeur": round(float(modele_a.rsquared_adj), 3)},
        {"indicateur": "R2_ajuste_modeleB_log", "valeur": round(float(modele_b.rsquared_adj), 3)},
        {"indicateur": "R2_ajuste_modeleC_sensibilite", "valeur": round(float(modele_c.rsquared_adj), 3)},
        {"indicateur": "Coef_log_frequence_modeleB", "valeur": round(float(modele_b.params.get("log_frequence_mois", np.nan)), 3)},
        {"indicateur": "p_value_log_frequence_modeleB", "valeur": round(float(modele_b.pvalues.get("log_frequence_mois", np.nan)), 4)},
        {"indicateur": "Coef_duree_epilepsie_modeleB", "valeur": round(float(coef_duree_epi), 3)},
        {"indicateur": "p_value_duree_epilepsie_modeleB", "valeur": round(float(p_duree_epi), 4)},
        {"indicateur": "Breusch_Pagan_p_value", "valeur": round(float(bp_p), 4)},
        {"indicateur": "Shapiro_residus_p_value", "valeur": round(float(w_p), 4)},
    ]

    resume_stats = {
        "n_bilans_qi": int(len(df)),
        "n_patients": int(df["pseudonyme"].nunique()),
        "n_regression": int(len(df_reg)),
        "r2_ajuste_modele_retenu": round(float(modele_b.rsquared_adj), 3),
        "coef_log_frequence": round(float(modele_b.params.get("log_frequence_mois", np.nan)), 3),
        "p_value_log_frequence": round(float(modele_b.pvalues.get("log_frequence_mois", np.nan)), 4),
        "significatif_log_frequence": bool(modele_b.pvalues.get("log_frequence_mois", 1.0) < ALPHA),
        "heteroscedasticite_detectee": bool(bp_p < ALPHA),
        "residus_non_normaux": bool(w_p < ALPHA),
    }

    tables_a_sauvegarder = {
        "resultats_regression_qi": pd.DataFrame(lignes_resultats),
        "vif_modele_B": pd.DataFrame(lignes_vif),
        "correlations_spearman_stratifiees": pd.DataFrame(correlations_stratifiees),
        "dataset_frequence_qi_epr": df,
    }

    horodatage = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dossier_sortie = os.path.join(RESULTATS_DIR, f"epr_7_{horodatage}")
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