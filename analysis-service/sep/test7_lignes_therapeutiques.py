"""
Test SEP #7 — Lignes thérapeutiques : comparaison des groupes d'efficacité
(interférons/glatiramère vs thérapies de haute efficacité) sur le TAP,
l'activité IRM et le délai avant échec thérapeutique.

Portage non interactif de test_analyse_statistique/SEP/test7_sep.py :
- Le script original utilisait `input()` pour faire classer les molécules
  par le clinicien en ligne de commande. Ce mode est incompatible avec un
  appel API (`run(engine, config)`), donc il a été retiré ici.
- Les molécules déjà classées dans `reference_groupe_efficacite` (table
  alimentée par le clinicien en amont, via le seed ou l'UI de gestion des
  molécules) sont utilisées telles quelles.
- Les molécules encore non classées sont listées dans les notes et exclues
  de l'analyse, exactement comme le faisait le script original pour les
  résidus après classement.
- IMPORTANT : l'INSERT dans reference_groupe_efficacite ne doit utiliser
  que (molecule, groupe) — jamais classe_par depuis ce wrapper, qui ne
  fait aucune écriture (lecture seule sur cette table).
"""

import numpy as np
import pandas as pd
from sqlalchemy import text

import scipy.stats as stats
import statsmodels.api as sm
import statsmodels.formula.api as smf
from statsmodels.stats.anova import anova_lm
from statsmodels.stats.multicomp import pairwise_tukeyhsd

from lifelines import KaplanMeierFitter, CoxPHFitter
from lifelines.statistics import logrank_test, multivariate_logrank_test

from sklearn.linear_model import LogisticRegression
from sklearn.neighbors import NearestNeighbors

import matplotlib.pyplot as plt

from common import Notes, figure_to_base64

GROUPES_AUTORISES = ["Faible_Moderee", "Haute_efficacite"]
EFFECTIF_MINIMUM = 10

PARAMETRES_SCHEMA = {}


REQUETE_TRAITEMENTS = text("""
WITH traitement AS (
    SELECT
        tf.id                     AS traitement_id,
        tf.pseudonyme,
        tf.molecule,
        tf.ligne_therapeutique,
        tf.date_debut,
        tf.date_fin,
        tf.motif_switch,
        tf.observance,
        p.age,
        p.date_inclusion,
        ic.age_diagnostic_mois,
        ic.age_premier_symptome_mois,
        ic.delai_diagnostic_mois,
        ic.date_diagnostic,
        ic.sexe,
        COALESCE(tf.date_fin, s.date_dernier_suivi, CURRENT_DATE) AS date_fin_effective
    FROM sep_traitement_fond tf
    JOIN patients p                          ON p.pseudonyme = tf.pseudonyme
    LEFT JOIN sep_suivi s                    ON s.pseudonyme = tf.pseudonyme
    LEFT JOIN sep_identification_clinique ic ON ic.pseudonyme = tf.pseudonyme
    WHERE p.registre = 'SEP'
      AND tf.date_debut IS NOT NULL
),
poussees_periode AS (
    SELECT
        t.traitement_id,
        COUNT(pou.id) AS nb_poussees
    FROM traitement t
    LEFT JOIN sep_poussees pou
        ON pou.pseudonyme = t.pseudonyme
       AND pou.date_poussee BETWEEN t.date_debut AND t.date_fin_effective
    GROUP BY t.traitement_id
),
irm_periode AS (
    SELECT
        t.traitement_id,
        COUNT(i.id) FILTER (WHERE i.nouvelles_lesions_vs_irm_anterieure = TRUE) AS nb_irm_avec_nouvelles_lesions,
        COUNT(i.id)                                                              AS nb_irm_realisees,
        AVG(i.nb_lesions_t2)                                                     AS moy_lesions_t2
    FROM traitement t
    LEFT JOIN sep_irm i
        ON i.pseudonyme = t.pseudonyme
       AND i.date_examen BETWEEN t.date_debut AND t.date_fin_effective
    GROUP BY t.traitement_id
),
edss_baseline AS (
    SELECT DISTINCT ON (t.traitement_id)
        t.traitement_id,
        e.score_edss AS edss_baseline
    FROM traitement t
    LEFT JOIN sep_edss_visites e
        ON e.pseudonyme = t.pseudonyme
       AND e.date_visite <= t.date_debut
    ORDER BY t.traitement_id, e.date_visite DESC
),
lesions_t2_baseline AS (
    SELECT DISTINCT ON (t.traitement_id)
        t.traitement_id,
        i.nb_lesions_t2 AS lesions_t2_baseline
    FROM traitement t
    LEFT JOIN sep_irm i
        ON i.pseudonyme = t.pseudonyme
       AND i.date_examen <= t.date_debut
    ORDER BY t.traitement_id, i.date_examen DESC
)
SELECT
    t.traitement_id,
    t.pseudonyme,
    t.molecule,
    t.ligne_therapeutique,
    rge.groupe AS groupe_efficacite,
    t.age,
    t.sexe,
    GREATEST(
        (t.date_debut - t.date_diagnostic) / 365.25,
        0
    ) AS duree_maladie_avant_traitement_annees,
    t.date_debut,
    t.date_fin_effective,
    t.motif_switch,
    t.observance,
    GREATEST(
        (t.date_fin_effective - t.date_debut) / 365.25,
        1.0/365.25
    ) AS duree_suivi_annees,
    COALESCE(pp.nb_poussees, 0)                       AS nb_poussees,
    COALESCE(ip.nb_irm_avec_nouvelles_lesions, 0)     AS nb_irm_nouvelles_lesions,
    COALESCE(ip.nb_irm_realisees, 0)                  AS nb_irm_realisees,
    ip.moy_lesions_t2,
    eb.edss_baseline,
    lb.lesions_t2_baseline
FROM traitement t
LEFT JOIN poussees_periode     pp  ON pp.traitement_id = t.traitement_id
LEFT JOIN irm_periode          ip  ON ip.traitement_id = t.traitement_id
LEFT JOIN edss_baseline        eb  ON eb.traitement_id = t.traitement_id
LEFT JOIN lesions_t2_baseline  lb  ON lb.traitement_id = t.traitement_id
LEFT JOIN reference_groupe_efficacite rge ON rge.molecule = t.molecule
ORDER BY t.pseudonyme, t.date_debut;
""")


def _molecules_non_classees(engine) -> list[str]:
    requete = text("""
        SELECT DISTINCT tf.molecule
        FROM sep_traitement_fond tf
        WHERE tf.molecule IS NOT NULL
          AND tf.molecule NOT IN (SELECT molecule FROM reference_groupe_efficacite)
        ORDER BY tf.molecule;
    """)
    with engine.connect() as conn:
        return pd.read_sql(requete, conn)["molecule"].tolist()


def run(engine, config: dict) -> dict:
    """Point d'entrée appelé par l'API. `config` non utilisé (aucun paramètre pour ce test)."""
    notes = Notes()
    figures = []
    tableau = []

    notes("=" * 70)
    notes("REGISTRE SEP PÉDIATRIQUE — LIGNES THÉRAPEUTIQUES ET EFFICACITÉ")
    notes("=" * 70)

    molecules_non_classees = _molecules_non_classees(engine)
    if molecules_non_classees:
        notes(
            f"ATTENTION : {len(molecules_non_classees)} molécule(s) non classée(s) dans "
            f"reference_groupe_efficacite (à classer via la gestion des entités médicales) : "
            f"{', '.join(molecules_non_classees)}. Ces molécules sont exclues de l'analyse."
        )
        notes("")

    with engine.connect() as conn:
        df = pd.read_sql(REQUETE_TRAITEMENTS, conn)

    notes(f"Nombre de séquences de traitement extraites : n = {len(df)}")

    df = df[df["ligne_therapeutique"].notna()]
    df = df[df["ligne_therapeutique"].astype(str).str.upper() != "NA"]

    df_analyse = df[df["groupe_efficacite"].isin(GROUPES_AUTORISES)].copy()

    if len(df_analyse) < EFFECTIF_MINIMUM:
        raise ValueError(
            f"Effectif insuffisant pour ce test (n={len(df_analyse)} séquences de traitement "
            f"avec molécule classée dans un groupe d'efficacité ; minimum {EFFECTIF_MINIMUM} "
            f"recommandé). Classez davantage de molécules dans reference_groupe_efficacite, "
            f"ou complétez la ligne thérapeutique des traitements existants."
        )

    df_analyse["TAP"] = df_analyse["nb_poussees"] / df_analyse["duree_suivi_annees"]
    df_analyse["taux_nouvelles_lesions_irm_an"] = (
        df_analyse["nb_irm_nouvelles_lesions"] / df_analyse["duree_suivi_annees"]
    )
    # Convention du dictionnaire de données : valeurs en snake_case sans accent
    # ('echec', 'effet_indesirable', 'choix', 'NA') — pas 'échec'.
    df_analyse["evenement_echec"] = (df_analyse["motif_switch"] == "echec").astype(int)
    df_analyse["temps_echec_annees"] = df_analyse["duree_suivi_annees"]

    for col in ["edss_baseline", "lesions_t2_baseline", "age", "duree_maladie_avant_traitement_annees"]:
        df_analyse[col] = df_analyse[col].fillna(df_analyse[col].median())

    df_analyse["sexe_F"] = (df_analyse["sexe"] == "F").astype(int)

    notes(f"Effectifs finaux par groupe d'efficacité : "
          f"{df_analyse['groupe_efficacite'].value_counts().to_dict()}")

    # --- Comparabilité des groupes ---
    notes("\n--- ÉTAPE 1 : Comparabilité des groupes (Mann-Whitney) ---")
    for var in ["age", "edss_baseline", "lesions_t2_baseline"]:
        g1 = df_analyse.loc[df_analyse.groupe_efficacite == "Faible_Moderee", var]
        g2 = df_analyse.loc[df_analyse.groupe_efficacite == "Haute_efficacite", var]
        if len(g1) >= 3 and len(g2) >= 3:
            _, p_u = stats.mannwhitneyu(g1, g2, alternative="two-sided")
            notes(f"{var} : Mann-Whitney p = {p_u:.4f} "
                  f"(médiane Faible/Modérée = {g1.median():.2f} | Haute eff. = {g2.median():.2f})")
            tableau.append({"analyse": f"Comparabilité — {var}", "test": "Mann-Whitney U",
                             "p_value": round(float(p_u), 4)})

    # --- ANOVA / Kruskal-Wallis : TAP ~ molécule ---
    notes("\n--- ÉTAPE 2 : TAP selon la molécule (ANOVA / Kruskal-Wallis) ---")
    groupes_tap = [g["TAP"].values for _, g in df_analyse.groupby("molecule") if len(g) >= 2]
    if len(groupes_tap) >= 2:
        modele_anova = smf.ols("TAP ~ C(molecule)", data=df_analyse).fit()
        table_anova = anova_lm(modele_anova, typ=2)
        notes(table_anova.round(4).to_string())
        stat_kw, p_kw = stats.kruskal(*groupes_tap)
        notes(f"Kruskal-Wallis (TAP ~ molécule) : H = {stat_kw:.3f}, p = {p_kw:.4f}")
        tableau.append({"analyse": "TAP vs molécule", "test": "Kruskal-Wallis",
                         "p_value": round(float(p_kw), 4)})

    # --- GEE binomial négatif : nb_poussees ---
    notes("\n--- ÉTAPE 3 : GEE binomial négatif — nb_poussees ~ groupe + covariables ---")
    modele_gee_tap = None
    try:
        df_analyse["log_duree"] = np.log(df_analyse["duree_suivi_annees"])
        modele_gee_tap = smf.gee(
            "nb_poussees ~ groupe_efficacite + age + sexe_F + edss_baseline + "
            "lesions_t2_baseline + duree_maladie_avant_traitement_annees",
            groups="pseudonyme", data=df_analyse, offset=df_analyse["log_duree"],
            family=sm.families.NegativeBinomial(alpha=1.0),
        ).fit()
        irr_tap = np.exp(modele_gee_tap.params)
        ic_tap = np.exp(modele_gee_tap.conf_int())
        ic_tap.columns = ["IC95%_bas", "IC95%_haut"]
        resume_irr = pd.concat([irr_tap.rename("IRR"), ic_tap], axis=1)
        notes(resume_irr.round(4).to_string())
        p_groupe = modele_gee_tap.pvalues.get(
            [c for c in modele_gee_tap.pvalues.index if "Haute_efficacite" in c][0], None
        ) if any("Haute_efficacite" in c for c in modele_gee_tap.pvalues.index) else None
        if p_groupe is not None:
            tableau.append({"analyse": "TAP ajusté (GEE, groupe efficacité)", "test": "GEE - NegBinomial",
                             "p_value": round(float(p_groupe), 4)})
    except Exception as e:
        notes(f"Modèle GEE (TAP) non calculable avec les données fournies : {e}")

    # --- Kaplan-Meier : délai avant échec ---
    notes("\n--- ÉTAPE 4 : Kaplan-Meier — délai avant échec thérapeutique ---")
    kmf = KaplanMeierFitter()
    fig, ax = plt.subplots(figsize=(9, 6))
    for groupe, sous_df in df_analyse.groupby("groupe_efficacite"):
        kmf.fit(durations=sous_df["temps_echec_annees"], event_observed=sous_df["evenement_echec"], label=groupe)
        kmf.plot_survival_function(ax=ax, ci_show=True)
        notes(f"Médiane de survie sans échec ({groupe}) : {kmf.median_survival_time_:.2f} années")
    ax.set_title("Kaplan-Meier — Délai avant échec thérapeutique")
    ax.set_xlabel("Délai depuis l'initiation du traitement (années)")
    ax.set_ylabel("Probabilité de rester sans échec thérapeutique")
    plt.tight_layout()
    figures.append(figure_to_base64(fig))

    g1 = df_analyse[df_analyse.groupe_efficacite == "Faible_Moderee"]
    g2 = df_analyse[df_analyse.groupe_efficacite == "Haute_efficacite"]
    if len(g1) >= 2 and len(g2) >= 2:
        resultat_logrank = logrank_test(
            g1["temps_echec_annees"], g2["temps_echec_annees"],
            event_observed_A=g1["evenement_echec"], event_observed_B=g2["evenement_echec"],
        )
        notes(f"Test du Log-Rank (groupe d'efficacité) : p = {resultat_logrank.p_value:.4f}")
        tableau.append({"analyse": "Survie sans échec — groupe efficacité", "test": "Log-Rank",
                         "p_value": round(float(resultat_logrank.p_value), 4)})

    # --- Cox univarié / multivarié ---
    notes("\n--- ÉTAPE 5 : Modèles de Cox ---")
    cph_multivarie = None
    try:
        df_cox = df_analyse[[
            "temps_echec_annees", "evenement_echec", "groupe_efficacite",
            "age", "sexe_F", "edss_baseline", "lesions_t2_baseline",
            "duree_maladie_avant_traitement_annees", "pseudonyme"
        ]].copy()
        df_cox = pd.get_dummies(df_cox, columns=["groupe_efficacite"], drop_first=True)

        cph_univarie = CoxPHFitter()
        cph_univarie.fit(
            df_cox[["temps_echec_annees", "evenement_echec", "groupe_efficacite_Haute_efficacite"]],
            duration_col="temps_echec_annees", event_col="evenement_echec",
        )
        notes("Cox univarié (groupe d'efficacité seul) :")
        notes(cph_univarie.summary.round(4).to_string())

        cph_multivarie = CoxPHFitter()
        cph_multivarie.fit(
            df_cox.drop(columns=["pseudonyme"]),
            duration_col="temps_echec_annees", event_col="evenement_echec", robust=True,
        )
        notes("\nCox multivarié (ajusté) :")
        notes(cph_multivarie.summary.round(4).to_string())
        notes(f"\nComparaison AIC — univarié : {cph_univarie.AIC_partial_:.1f} | "
              f"multivarié : {cph_multivarie.AIC_partial_:.1f}")

        if "groupe_efficacite_Haute_efficacite" in cph_multivarie.summary.index:
            p_cox = cph_multivarie.summary.loc["groupe_efficacite_Haute_efficacite", "p"]
            tableau.append({"analyse": "Échec thérapeutique — groupe efficacité (ajusté)",
                             "test": "Cox multivarié", "p_value": round(float(p_cox), 4)})
    except Exception as e:
        notes(f"Modèle de Cox non calculable avec les données fournies : {e}")

    # --- Appariement par score de propension ---
    notes("\n--- ÉTAPE 6 : Appariement par score de propension (1:1) ---")
    try:
        covariables_ps = ["age", "edss_baseline", "lesions_t2_baseline", "duree_maladie_avant_traitement_annees"]
        df_ps = df_analyse.dropna(subset=covariables_ps + ["groupe_efficacite"]).copy()
        df_ps["traitement_binaire"] = (df_ps["groupe_efficacite"] == "Haute_efficacite").astype(int)

        if df_ps["traitement_binaire"].nunique() == 2 and len(df_ps) >= EFFECTIF_MINIMUM:
            modele_logit_ps = LogisticRegression(max_iter=1000)
            modele_logit_ps.fit(df_ps[covariables_ps], df_ps["traitement_binaire"])
            df_ps["score_propension"] = modele_logit_ps.predict_proba(df_ps[covariables_ps])[:, 1]

            traites = df_ps[df_ps.traitement_binaire == 1]
            controles = df_ps[df_ps.traitement_binaire == 0]
            caliper = 0.2 * df_ps["score_propension"].std()

            if len(controles) > 0 and len(traites) > 0:
                nn = NearestNeighbors(n_neighbors=1)
                nn.fit(controles[["score_propension"]])
                distances, indices = nn.kneighbors(traites[["score_propension"]])
                paires_valides = distances.flatten() <= caliper
                n_apparies = int(paires_valides.sum())
                notes(f"Paires appariées : {n_apparies} / {len(traites)} patients haute efficacité")
        else:
            notes("Appariement non réalisé : effectif ou variance insuffisante après filtrage.")
    except Exception as e:
        notes(f"Appariement par score de propension non calculable : {e}")

    notes("\n" + "=" * 70)
    notes("Interprétation : p < 0.05 = différence significative entre groupes ")
    notes("d'efficacité thérapeutique sur le critère considéré.")
    notes("=" * 70)

    resume_stats = {
        "n_sequences_extraites": int(len(df)),
        "n_analysees": int(len(df_analyse)),
        "n_molecules_non_classees": len(molecules_non_classees),
        "molecules_non_classees": molecules_non_classees,
    }

    return {"notes": notes.lines, "figures": figures, "tableau": tableau, "resume_stats": resume_stats}
