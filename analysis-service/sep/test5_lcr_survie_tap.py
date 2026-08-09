
import numpy as np
import pandas as pd
from scipy import stats
import statsmodels.api as sm
import statsmodels.formula.api as smf
from lifelines import CoxPHFitter, KaplanMeierFitter
import matplotlib.pyplot as plt

from common import figure_to_base64, Notes

REQUIRED_COLUMNS = [
    "patient_id", "boc_positive", "igg_index",
    "nb_poussees", "duree_suivi_annees",
    "temps_evenement_annees", "evenement_survenu",
]

PARAMETRES_SCHEMA = {
    "definition_evenement": {"type": "select",
                              "options": ["conversion_sp", "poussee_suivante"],
                              "default": "conversion_sp",
                              "label": "Définition de l'évènement (survie)"},
    "count_model": {"type": "select", "options": ["auto", "poisson", "negbin"],
                     "default": "auto", "label": "Modèle de comptage du TAP"},
    "horizons_years": {"type": "text", "default": "1,2,5",
                        "label": "Horizons Cox (années, séparés par virgules)"},
    "igg_threshold": {"type": "number", "default": 0.7, "label": "Seuil index IgG"},
    "multiple_testing_correction": {"type": "select", "options": ["bonferroni", "aucune"],
                                     "default": "bonferroni",
                                     "label": "Correction tests multiples (p-values Cox/horizon)"},
}

SQL_EXTRACTION = """
WITH lcr_diagnostic AS (
    SELECT DISTINCT ON (pseudonyme)
        pseudonyme,
        bandes_oligoclonales,
        index_igg,
        date_prelevement AS date_index
    FROM sep_biologie_lcr
    WHERE bandes_oligoclonales IS NOT NULL
      AND bandes_oligoclonales != 'NA'
      AND index_igg IS NOT NULL
    ORDER BY pseudonyme, date_prelevement ASC
),
poussees_agg AS (
    SELECT
        pseudonyme,
        ARRAY_AGG(date_poussee ORDER BY date_poussee) AS dates_poussees
    FROM sep_poussees
    GROUP BY pseudonyme
)
SELECT
    l.pseudonyme                      AS patient_id,
    l.bandes_oligoclonales,
    l.index_igg,
    l.date_index,
    p.dates_poussees,
    (p.pseudonyme IS NOT NULL)        AS a_des_poussees_documentees,
    e.forme_evolutive,
    e.date_conversion_sp,
    s.date_dernier_suivi,
    s.statut_dernier_suivi
FROM lcr_diagnostic l
LEFT JOIN poussees_agg p ON p.pseudonyme = l.pseudonyme
LEFT JOIN sep_evolution e ON e.pseudonyme = l.pseudonyme
LEFT JOIN sep_suivi s ON s.pseudonyme = l.pseudonyme
WHERE s.date_dernier_suivi IS NOT NULL
"""


def extraire_depuis_postgres(engine):
    df_brut = pd.read_sql(SQL_EXTRACTION, engine)

    def _to_dates_or_none(x):
        if isinstance(x, (list, tuple)):
            return pd.to_datetime(list(x))
        return None

    df_brut["dates_poussees"] = df_brut["dates_poussees"].apply(_to_dates_or_none)
    for col in ["date_index", "date_conversion_sp", "date_dernier_suivi"]:
        df_brut[col] = pd.to_datetime(df_brut[col])
    return df_brut


def recoder_variables_brutes(df_brut):
    df = df_brut.copy()

    def _normaliser_boc(val):
        if pd.isna(val):
            return val
        v = str(val).strip().lower().replace("é", "e").replace("è", "e").replace("ê", "e")
        if v == "positif":
            return "Positif"
        if v == "negatif":
            return "Négatif"
        return val

    df["bandes_oligoclonales_norm"] = df["bandes_oligoclonales"].apply(_normaliser_boc)
    mapping_boc = {"Positif": 1, "Négatif": 0}
    df["boc_positive"] = df["bandes_oligoclonales_norm"].map(mapping_boc)

    masque_non_mappe = df["boc_positive"].isna() & df["bandes_oligoclonales_norm"].notna()
    if masque_non_mappe.any():
        valeurs_inattendues = df.loc[masque_non_mappe, "bandes_oligoclonales"].unique()
        raise ValueError(
            f"Valeurs inattendues dans bandes_oligoclonales : {valeurs_inattendues} "
            "(attendu : 'Positif' / 'Négatif' [variantes casse/accents/espaces tolérées], "
            "ou NULL si non renseigné)"
        )
    df["igg_index"] = df["index_igg"]
    return df


def filtrer_valeurs_manquantes(df, colonnes, notes: Notes, contexte=""):
    n_avant = len(df)
    masque_complet = df[colonnes].notna().all(axis=1)
    df_filtre = df.loc[masque_complet].copy()
    n_exclus = n_avant - len(df_filtre)
    if n_exclus > 0:
        notes(f"[valeurs manquantes]{' ' + contexte if contexte else ''} : "
              f"{n_exclus}/{n_avant} patient(s) exclu(s) pour NaN dans {colonnes}")
    return df_filtre


def valider_schema(df, notes: Notes, colonnes=REQUIRED_COLUMNS, contexte=""):
    manquantes = [c for c in colonnes if c not in df.columns]
    if manquantes:
        raise ValueError(
            f"Schéma invalide{' (' + contexte + ')' if contexte else ''} : "
            f"colonnes manquantes {manquantes}"
        )
    return df


def deriver_variables_survie(df_brut, notes: Notes, definition_evenement="conversion_sp"):
    df = df_brut.copy()

    if definition_evenement == "conversion_sp":
        est_sp = df["forme_evolutive"].eq("SP")
        forme_non_renseignee = df["forme_evolutive"].isna()
        date_manquante = df["date_conversion_sp"].isna()

        donnee_manquante = date_manquante & (est_sp | forme_non_renseignee)
        if donnee_manquante.any():
            n_sp = int((date_manquante & est_sp).sum())
            n_forme_nulle = int((date_manquante & forme_non_renseignee).sum())
            notes(f"[valeurs manquantes] conversion_sp : {donnee_manquante.sum()} "
                  f"patient(s) exclu(s) — {n_sp} avec forme_evolutive='SP' sans date, "
                  f"{n_forme_nulle} avec forme_evolutive non renseignée")
        df = df.loc[~donnee_manquante].copy()

        masque_prog_emblee = df["forme_evolutive"].eq("progressive d'emblée")
        if masque_prog_emblee.any():
            notes(f"[hors périmètre] conversion_sp : {masque_prog_emblee.sum()} "
                  "patient(s) 'progressive d'emblée' exclu(s) — jamais à risque "
                  "de conversion RR->SP par définition")
        df = df.loc[~masque_prog_emblee].copy()

        evenement = df["date_conversion_sp"].notna().astype(int)
        date_fin = df["date_conversion_sp"].fillna(df["date_dernier_suivi"])

    elif definition_evenement == "poussee_suivante":
        if "a_des_poussees_documentees" in df.columns:
            sans_saisie = ~df["a_des_poussees_documentees"].astype(bool)
        else:
            sans_saisie = df["dates_poussees"].isna()
        if sans_saisie.any():
            notes(f"[valeurs manquantes] poussee_suivante : {int(sans_saisie.sum())} "
                  "patient(s) exclu(s) — absence de saisie dans sep_poussees "
                  "(PAS 'pas de poussée future')")
        df = df.loc[~sans_saisie].copy()

        def premiere_poussee_apres_index(row):
            if row["dates_poussees"] is None:
                return pd.NaT
            dates_futures = [d for d in row["dates_poussees"] if d > row["date_index"]]
            return min(dates_futures) if dates_futures else pd.NaT

        date_evenement = df.apply(premiere_poussee_apres_index, axis=1)
        evenement = date_evenement.notna().astype(int)
        date_fin = date_evenement.fillna(df["date_dernier_suivi"])

    else:
        raise ValueError("definition_evenement doit être 'conversion_sp' ou 'poussee_suivante'")

    df["temps_evenement_annees"] = (date_fin - df["date_index"]).dt.days / 365.25
    df["evenement_survenu"] = evenement

    masque_negatif = df["temps_evenement_annees"] < 0
    if masque_negatif.any():
        notes(f"[incohérence dates] {masque_negatif.sum()} patient(s) avec "
              "temps_evenement_annees négatif (date d'évènement antérieure à "
              "date_index) — exclus, à vérifier en base")
        df = df.loc[~masque_negatif].copy()

    if "statut_dernier_suivi" in df.columns:
        censures = df["evenement_survenu"] == 0
        deces_censures = censures & df["statut_dernier_suivi"].eq("Décédé")
        if deces_censures.any():
            notes(f"[censure informative ?] {int(deces_censures.sum())} patient(s) "
                  "censuré(s) suite à un DÉCÈS plutôt qu'une fin de suivi stable/"
                  "perdu de vue — hypothèse de censure non-informative du Cox à "
                  "vérifier ; flag 'censure_par_deces' ajouté pour analyse de "
                  "sensibilité (ex. exclusion ou compétition de risques)")
        df["censure_par_deces"] = deces_censures

    return df


def deriver_variables_tap(df_brut, notes: Notes):
    df = df_brut.copy()

    if "a_des_poussees_documentees" in df.columns:
        sans_saisie = ~df["a_des_poussees_documentees"].astype(bool)
        if sans_saisie.any():
            notes(f"[valeurs manquantes] nb_poussees : {int(sans_saisie.sum())} "
                  "patient(s) sans aucune ligne dans sep_poussees (absence de "
                  "saisie, PAS '0 poussée confirmée') — nb_poussees mis à NaN "
                  "pour exclusion explicite des analyses TAP")

    def _compter_poussees(row):
        if row.get("a_des_poussees_documentees", True) is False or row["dates_poussees"] is None:
            return np.nan
        return sum(1 for d in row["dates_poussees"] if d > row["date_index"])

    df["nb_poussees"] = df.apply(_compter_poussees, axis=1)
    df["duree_suivi_annees"] = (df["date_dernier_suivi"] - df["date_index"]).dt.days / 365.25

    masque_duree_nulle = df["duree_suivi_annees"] <= 0
    if masque_duree_nulle.any():
        notes(f"[durée nulle] {int(masque_duree_nulle.sum())} patient(s) avec "
              "duree_suivi_annees <= 0 (date_dernier_suivi == date_index ou "
              "antérieure) — exclus des analyses TAP (division par zéro / "
              "log(0) sinon)")
        df.loc[masque_duree_nulle, "duree_suivi_annees"] = np.nan
        df.loc[masque_duree_nulle, "nb_poussees"] = np.nan

    return df


def prepare_variables(df, igg_threshold):
    df = df.copy()
    df["tap"] = df["nb_poussees"] / df["duree_suivi_annees"]
    df["tap"] = df["tap"].replace([np.inf, -np.inf], np.nan)
    df["igg_positive"] = (df["igg_index"] > igg_threshold).astype(int)
    return df


def mann_whitney_tap(df, group_col, notes: Notes):
    df = filtrer_valeurs_manquantes(df, [group_col, "tap"], notes, contexte=f"Mann-Whitney ({group_col})")
    g1 = df.loc[df[group_col] == 1, "tap"]
    g0 = df.loc[df[group_col] == 0, "tap"]
    if len(g1) < 2 or len(g0) < 2:
        raise ValueError(f"Effectif insuffisant pour Mann-Whitney sur '{group_col}' "
                          f"(n_positif={len(g1)}, n_negatif={len(g0)}, minimum 2 par groupe).")
    stat, p = stats.mannwhitneyu(g1, g0, alternative="two-sided")
    return {
        "comparaison": group_col,
        "n_positif": len(g1), "n_negatif": len(g0),
        "mediane_TAP_positif": round(float(g1.median()), 3),
        "mediane_TAP_negatif": round(float(g0.median()), 3),
        "U": float(stat), "p_value": float(p),
    }


def test_surdispersion(df):
    model = smf.glm(
        "nb_poussees ~ boc_positive + igg_index",
        data=df, family=sm.families.Poisson(),
        offset=np.log(df["duree_suivi_annees"]),
    ).fit()
    dispersion_ratio = model.pearson_chi2 / model.df_resid
    return dispersion_ratio, model


def count_model_tap(df, notes: Notes, method="auto"):
    df = filtrer_valeurs_manquantes(
        df, ["nb_poussees", "boc_positive", "igg_index", "duree_suivi_annees"],
        notes, contexte="modèle de comptage TAP",
    )
    df = df.loc[df["duree_suivi_annees"] > 0].copy()
    if len(df) < 10:
        raise ValueError(f"Effectif insuffisant pour le modèle de comptage TAP (n={len(df)} < 10).")
    dispersion_ratio, poisson_model = test_surdispersion(df)

    chosen = method
    if method == "auto":
        chosen = "negbin" if dispersion_ratio > 1.5 else "poisson"

    if chosen == "poisson":
        final_model = poisson_model
        params, conf_int, pvalues = final_model.params, final_model.conf_int(), final_model.pvalues
    else:
        import statsmodels.discrete.discrete_model as dm
        X = sm.add_constant(df[["boc_positive", "igg_index"]])
        nb_model = dm.NegativeBinomial(
            df["nb_poussees"], X, exposure=df["duree_suivi_annees"], loglike_method="nb2"
        ).fit(disp=0)
        final_model = nb_model
        params = nb_model.params.drop("alpha", errors="ignore")
        conf_int_full = nb_model.conf_int()
        conf_int = conf_int_full.drop("alpha", errors="ignore")
        pvalues = nb_model.pvalues.drop("alpha", errors="ignore")

    summary = pd.DataFrame({
        "coef": params, "rate_ratio": np.exp(params),
        "IC95_bas": np.exp(conf_int[0]), "IC95_haut": np.exp(conf_int[1]),
        "p_value": pvalues,
    })
    return {"modele_choisi": chosen, "ratio_dispersion_pearson": float(dispersion_ratio),
            "resume": summary, "model_obj": final_model}


def cox_par_horizon(df, horizons_years, notes: Notes):
    df = filtrer_valeurs_manquantes(
        df, ["temps_evenement_annees", "evenement_survenu", "boc_positive", "igg_index"],
        notes, contexte="Cox par horizon",
    )
    masque_negatif = df["temps_evenement_annees"] < 0
    if masque_negatif.any():
        notes(f"[incohérence dates] Cox : {int(masque_negatif.sum())} patient(s) "
              "avec temps_evenement_annees négatif — exclus")
        df = df.loc[~masque_negatif].copy()

    if len(df) < 10:
        raise ValueError(f"Effectif insuffisant pour la régression de Cox par horizon (n={len(df)} < 10).")

    resultats = []
    for h in horizons_years:
        sub = df.copy()
        sub["temps_horizon"] = np.minimum(sub["temps_evenement_annees"], h)
        sub["evenement_horizon"] = np.where(sub["temps_evenement_annees"] <= h, sub["evenement_survenu"], 0)

        nb_evenements = int(sub["evenement_horizon"].sum())
        if nb_evenements < 5:
            notes(f"  ATTENTION horizon {h} an(s) : seulement {nb_evenements} évènement(s) "
                  "observé(s) avant cet horizon — HR potentiellement instable, interpréter avec prudence.")

        cph = CoxPHFitter()
        cph.fit(sub[["temps_horizon", "evenement_horizon", "boc_positive", "igg_index"]],
                duration_col="temps_horizon", event_col="evenement_horizon")
        s = cph.summary
        resultats.append({
            "horizon_annees": h,
            "HR_boc_positive": round(float(np.exp(s.loc["boc_positive", "coef"])), 3),
            "p_boc_positive": round(float(s.loc["boc_positive", "p"]), 4),
            "HR_igg_index": round(float(np.exp(s.loc["igg_index", "coef"])), 3),
            "p_igg_index": round(float(s.loc["igg_index", "p"]), 4),
            "n_evenements": nb_evenements,
        })
    return pd.DataFrame(resultats)


def appliquer_bonferroni(p_values):
    n = len(p_values)
    return [min(p * n, 1.0) for p in p_values]


def plot_mann_whitney(df, group_col):
    df_valide = df.dropna(subset=[group_col, "tap"])
    data = [df_valide.loc[df_valide[group_col] == 0, "tap"].dropna(),
            df_valide.loc[df_valide[group_col] == 1, "tap"].dropna()]
    fig, ax = plt.subplots(figsize=(5, 4))
    ax.boxplot(data, tick_labels=["Négatif", "Positif"], showmeans=True)
    ax.set_ylabel("TAP (poussées/an)")
    ax.set_title(f"TAP selon {group_col}")
    fig.tight_layout()
    return fig


def plot_count_model(count_res):
    resume = count_res["resume"].drop("const", errors="ignore")
    fig, ax = plt.subplots(figsize=(5, 4))
    y_pos = np.arange(len(resume))
    ax.errorbar(resume["rate_ratio"], y_pos,
                xerr=[resume["rate_ratio"] - resume["IC95_bas"], resume["IC95_haut"] - resume["rate_ratio"]],
                fmt="o", capsize=4)
    ax.axvline(1, color="grey", linestyle="--")
    ax.set_yticks(y_pos)
    ax.set_yticklabels(resume.index)
    ax.set_xlabel("Rate ratio (IC95%)")
    ax.set_title(f"Modèle de comptage ({count_res['modele_choisi'].upper()})")
    fig.tight_layout()
    return fig


def plot_cox_horizon(cox_res):
    fig, ax = plt.subplots(figsize=(5, 4))
    ax.plot(cox_res["horizon_annees"], cox_res["HR_boc_positive"], marker="o", label="HR BOC positif")
    ax.plot(cox_res["horizon_annees"], cox_res["HR_igg_index"], marker="s", label="HR IgG index")
    ax.axhline(1, color="grey", linestyle="--")
    ax.set_xlabel("Horizon (années)")
    ax.set_ylabel("Hazard ratio")
    ax.set_title("Cox par horizon temporel")
    ax.legend()
    fig.tight_layout()
    return fig


def plot_km(df, group_col):
    df_valide = df.dropna(subset=["temps_evenement_annees", "evenement_survenu", group_col])
    fig, ax = plt.subplots(figsize=(5, 4))
    kmf = KaplanMeierFitter()
    for val, label in [(0, "Négatif"), (1, "Positif")]:
        sub = df_valide.loc[df_valide[group_col] == val]
        kmf.fit(sub["temps_evenement_annees"], sub["evenement_survenu"], label=label)
        kmf.plot_survival_function(ax=ax)
    ax.set_xlabel("Temps (années)")
    ax.set_ylabel("Survie sans évènement")
    ax.set_title(f"Kaplan-Meier selon {group_col}")
    fig.tight_layout()
    return fig


def _parse_horizons(valeur):
    if isinstance(valeur, (list, tuple)):
        return [float(v) for v in valeur]
    return [float(v.strip()) for v in str(valeur).split(",") if v.strip()]


def run(engine, config: dict) -> dict:
    """Point d'entrée appelé par l'API. `config` = corps JSON envoyé par React."""
    notes = Notes()

    definition_evenement = config.get("definition_evenement", "conversion_sp")
    count_model_choix = config.get("count_model", "auto")
    horizons_years = _parse_horizons(config.get("horizons_years", "1,2,5"))
    igg_threshold = float(config.get("igg_threshold", 0.7))
    correction = config.get("multiple_testing_correction", "bonferroni")

    df_brut = extraire_depuis_postgres(engine)
    if df_brut.empty:
        raise ValueError("Aucun patient exploitable : jointure LCR initial x suivi vide "
                          "(vérifier sep_biologie_lcr / sep_suivi).")

    df_brut = recoder_variables_brutes(df_brut)
    df_brut = deriver_variables_survie(df_brut, notes, definition_evenement=definition_evenement)
    df_brut = deriver_variables_tap(df_brut, notes)
    valider_schema(df_brut, notes, contexte="pipeline API (Postgres)")
    df = prepare_variables(df_brut, igg_threshold)

    notes("=" * 70)
    notes("1) MANN-WHITNEY — TAP selon statut BOC et statut IgG")
    notes("=" * 70)
    mw_boc = mann_whitney_tap(df, "boc_positive", notes)
    mw_igg = mann_whitney_tap(df, "igg_positive", notes)
    mw_results = pd.DataFrame([mw_boc, mw_igg])
    notes(mw_results.to_string(index=False))

    notes("\n" + "=" * 70)
    notes(f"2) MODÈLE DE COMPTAGE POUR LE TAP (choix : {count_model_choix})")
    notes("=" * 70)
    count_res = count_model_tap(df, notes, method=count_model_choix)
    notes(f"Ratio de dispersion Pearson (>1.5 => surdispersion) : "
          f"{count_res['ratio_dispersion_pearson']:.2f}")
    notes(f"Modèle retenu : {count_res['modele_choisi'].upper()}")
    notes(count_res["resume"].round(4).to_string())

    notes("\n" + "=" * 70)
    notes(f"3) RÉGRESSION DE COX PAR HORIZON TEMPOREL : {horizons_years} ans")
    notes("=" * 70)
    cox_res = cox_par_horizon(df, horizons_years, notes)
    notes(cox_res.round(4).to_string(index=False))

    if correction == "bonferroni":
        notes("\n" + "=" * 70)
        notes(f"4) CORRECTION BONFERRONI (sur les p-values BOC des {len(horizons_years)} horizons)")
        notes("=" * 70)
        cox_res["p_boc_positive_bonferroni"] = appliquer_bonferroni(cox_res["p_boc_positive"].tolist())
        notes(cox_res[["horizon_annees", "p_boc_positive", "p_boc_positive_bonferroni"]]
              .round(4).to_string(index=False))

    figures = [
        figure_to_base64(plot_mann_whitney(df, "boc_positive")),
        figure_to_base64(plot_mann_whitney(df, "igg_positive")),
        figure_to_base64(plot_count_model(count_res)),
        figure_to_base64(plot_cox_horizon(cox_res)),
        figure_to_base64(plot_km(df, "boc_positive")),
    ]

    tableau = cox_res.to_dict(orient="records")
    resume_stats = {
        "n_patients": int(len(df)),
        "modele_comptage": count_res["modele_choisi"],
        "dispersion_pearson": round(count_res["ratio_dispersion_pearson"], 3),
        "mann_whitney_boc_p": round(mw_boc["p_value"], 4),
        "mann_whitney_igg_p": round(mw_igg["p_value"], 4),
        "cox_par_horizon": cox_res.to_dict(orient="records"),
    }

    return {"notes": notes.lines, "figures": figures, "tableau": tableau, "resume_stats": resume_stats}
