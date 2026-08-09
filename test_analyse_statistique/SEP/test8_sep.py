
import os
import sys

import numpy as np
import pandas as pd
import statsmodels.api as sm
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from sklearn.metrics import roc_curve, roc_auc_score, cohen_kappa_score, confusion_matrix
from sklearn.model_selection import StratifiedKFold
from sklearn.calibration import calibration_curve
from statsmodels.stats.outliers_influence import variance_inflation_factor
from scipy import stats as scipy_stats


OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "/mnt/user-data/outputs")
os.makedirs(OUTPUT_DIR, exist_ok=True)

import logging

logger = logging.getLogger("sep_severite_analysis")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    _console = logging.StreamHandler()
    _console.setFormatter(_fmt)
    logger.addHandler(_console)
    try:
        _file_handler = logging.FileHandler(
            os.path.join(OUTPUT_DIR, "analyse_sep_severite.log"), encoding="utf-8"
        )
        _file_handler.setFormatter(_fmt)
        logger.addHandler(_file_handler)
    except OSError as e:
        logger.warning(f"Impossible de creer le fichier de log ({e}) -> sortie console uniquement.")

DB_CONFIG = {
    "host": os.environ.get("PGHOST", "localhost"),
    "port": os.environ.get("PGPORT", "5432"),
    "dbname": os.environ.get("PGDATABASE", "registre_neuro"),
    "user": os.environ.get("PGUSER", "postgres"),
    "password": os.environ.get("PGPASSWORD", ""),
}

MODALITES_SEVERITE_POSITIVES = [
    "Hautement active", "hautement active",
    "Agressive", "agressive",
    "Hautement active / agressive", "hautement active / agressive",
    # Convention reelle du dictionnaire de donnees (snake_case) :
    "hautement_active", "hautement_active_agressive",
]

N_FOLDS = 5
N_BOOTSTRAP = 1000
RANDOM_STATE = 42
TAP_WINDOW_MONTHS_DEFAULT = 12
HL_GROUP_IMBALANCE_RATIO_ALERTE = 5.0  
BCA_MIN_REPLICATS_VALIDES = 50        

PREDICTEURS_CAHIER_DES_CHARGES = [
    "tap_annualise", "age_diagnostic_mois", "nb_lesions_t2", "atteinte_medullaire",
]

LIBELLES_PREDICTEURS = {
    "tap_annualise": "TAP precoce (poussees/an)",
    "age_diagnostic_mois": "Age au diagnostic (mois)",
    "nb_lesions_t2": "Nombre de lesions T2 a l'IRM initiale",
    "atteinte_medullaire": "Atteinte medullaire initiale (Oui vs Non)",
}

SCORE_B_REF_PLANCHER = 1e-3


def get_engine():
    
    from sqlalchemy import create_engine
    url = (
        f"postgresql+psycopg2://{DB_CONFIG['user']}:{DB_CONFIG['password']}"
        f"@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['dbname']}"
    )
    return create_engine(url)


MIGRATION_ORIGINE_DONNEE_SQL = """
-- A executer manuellement une fois (psql, DBeaver, etc.) avant d'utiliser
-- l'alerte "cohorte partiellement simulee" de ce script.
ALTER TABLE patients
  ADD COLUMN IF NOT EXISTS origine_donnee VARCHAR(20) DEFAULT 'reel'
  CHECK (origine_donnee IN ('reel', 'simule'));
"""


def check_origine_donnee_disponible(engine) -> bool:
    
    from sqlalchemy import text
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'patients' AND column_name = 'origine_donnee'"
        )).fetchone()
    disponible = result is not None
    if not disponible:
        logger.warning("Colonne patients.origine_donnee absente en base.")
        logger.warning("L'alerte 'cohorte partiellement simulee' est DESACTIVEE.")
        logger.warning("Migration a executer :\n%s", MIGRATION_ORIGINE_DONNEE_SQL)
    return disponible



EXTRACTION_SQL_TEMPLATE = """
WITH
params AS (SELECT :tap_window_months AS tap_w),

cohorte AS (
    SELECT ic.pseudonyme, ic.age_diagnostic_mois, ic.date_diagnostic, ic.sexe
           {origine_donnee_select}
    FROM sep_identification_clinique ic
    {origine_donnee_join}
    WHERE ic.date_diagnostic IS NOT NULL
),

suivi AS (
    SELECT s.pseudonyme, s.date_dernier_suivi
    FROM sep_suivi s
),

tap_precoce AS (
    SELECT ic.pseudonyme,
           -- Denominateur = duree REELLEMENT observee dans la fenetre TAP
           -- (mois ecoules entre diagnostic et date_dernier_suivi, plafonnee
           -- a tap_w), et non systematiquement la fenetre nominale complete.
           -- su.date_dernier_suivi et ic.date_diagnostic sont de type DATE :
           -- en PostgreSQL, (date - date) renvoie directement un entier (jours),
           -- pas un INTERVAL, donc EXTRACT(EPOCH FROM ...) leve "la fonction
           -- pg_catalog.extract(unknown, integer) n'existe pas". On convertit
           -- l'ecart en jours (deja numerique) en mois via /30.44, sans EXTRACT.
           COUNT(pou.id)::NUMERIC / GREATEST(
               LEAST(
                   COALESCE((su.date_dernier_suivi - ic.date_diagnostic)::NUMERIC / 30.44, p.tap_w),
                   p.tap_w
               ) / 12.0,
           0.1) AS tap_annualise,
           (su.date_dernier_suivi - ic.date_diagnostic)::NUMERIC / 30.44 AS suivi_total_mois
    FROM sep_identification_clinique ic
    CROSS JOIN params p
    LEFT JOIN sep_poussees pou ON pou.pseudonyme = ic.pseudonyme
        AND pou.date_poussee BETWEEN ic.date_diagnostic
                                 AND ic.date_diagnostic + (p.tap_w || ' months')::INTERVAL
    LEFT JOIN suivi su ON su.pseudonyme = ic.pseudonyme
    WHERE ic.date_diagnostic IS NOT NULL
    GROUP BY ic.pseudonyme, p.tap_w, su.date_dernier_suivi
),

tap_post_tap AS (
    SELECT ic.pseudonyme, COUNT(pou.id) AS nb_poussees_post_tap
    FROM sep_identification_clinique ic
    CROSS JOIN params p
    LEFT JOIN sep_poussees pou ON pou.pseudonyme = ic.pseudonyme
        AND pou.date_poussee BETWEEN ic.date_diagnostic + (p.tap_w || ' months')::INTERVAL
                                 AND ic.date_diagnostic + (p.tap_w || ' months')::INTERVAL + INTERVAL '24 months'
    WHERE ic.date_diagnostic IS NOT NULL
    GROUP BY ic.pseudonyme
),

irm_initiale AS (
    SELECT DISTINCT ON (i.pseudonyme)
        i.pseudonyme, i.nb_lesions_t2, i.localisation_moelle,
        i.localisation_juxta_corticale, i.nb_lesions_rehaussees
    FROM sep_irm i
    JOIN sep_identification_clinique ic ON ic.pseudonyme = i.pseudonyme
    WHERE i.date_examen BETWEEN ic.date_diagnostic - INTERVAL '3 months'
                            AND ic.date_diagnostic + INTERVAL '6 months'
    ORDER BY i.pseudonyme, ABS(i.date_examen - ic.date_diagnostic) ASC
),

gd_post_tap AS (
    SELECT i.pseudonyme, MAX(i.nb_lesions_rehaussees) AS max_gd_post_tap
    FROM sep_irm i
    JOIN sep_identification_clinique ic ON ic.pseudonyme = i.pseudonyme
    CROSS JOIN params p
    WHERE i.date_examen BETWEEN ic.date_diagnostic + (p.tap_w || ' months')::INTERVAL
                            AND ic.date_diagnostic + (p.tap_w || ' months')::INTERVAL + INTERVAL '24 months'
      AND i.prise_contraste_gd = TRUE
    GROUP BY i.pseudonyme
),

edss_post_tap AS (
    SELECT ic.pseudonyme, MAX(v.score_edss) AS edss_max_post_tap
    FROM sep_identification_clinique ic
    CROSS JOIN params p
    LEFT JOIN sep_edss_visites v ON v.pseudonyme = ic.pseudonyme
        AND v.date_visite BETWEEN ic.date_diagnostic + (p.tap_w || ' months')::INTERVAL
                              AND ic.date_diagnostic + (p.tap_w || ' months')::INTERVAL + INTERVAL '24 months'
    WHERE ic.date_diagnostic IS NOT NULL
    GROUP BY ic.pseudonyme
),

edss_dernier AS (
    SELECT pseudonyme, edss_dernier AS edss_last, date_dernier_edss
    FROM analytics.v_sep_edss_dernier_connu
),


edss6_delai AS (
    SELECT ic.pseudonyme,
           MIN(v.date_visite) AS date_edss6,
           EXTRACT(MONTH FROM AGE(MIN(v.date_visite), ic.date_diagnostic)) AS delai_edss6_mois
    FROM sep_identification_clinique ic
    JOIN sep_edss_visites v ON v.pseudonyme = ic.pseudonyme
    CROSS JOIN params p
    WHERE v.score_edss >= 6
      AND v.date_visite > ic.date_diagnostic + (p.tap_w || ' months')::INTERVAL
      AND v.date_visite < ic.date_diagnostic + INTERVAL '5 years'
    GROUP BY ic.pseudonyme
),

presentation AS (
    SELECT pseudonyme, type_premier_evenement, recuperation_complete
    FROM sep_presentation_initiale
),

lcr AS (
    SELECT DISTINCT ON (pseudonyme) pseudonyme, bandes_oligoclonales
    FROM sep_biologie_lcr
    ORDER BY pseudonyme, date_prelevement ASC
),

evolution AS (SELECT pseudonyme, severite FROM sep_evolution)

SELECT
    c.pseudonyme, c.age_diagnostic_mois, c.date_diagnostic, c.sexe,
    {origine_donnee_out}
    tp.tap_annualise,
    tp.suivi_total_mois,
    t2.nb_poussees_post_tap,
    i.nb_lesions_t2, i.localisation_moelle,
    i.localisation_juxta_corticale, i.nb_lesions_rehaussees,
    g.max_gd_post_tap,
    e2.edss_max_post_tap,
    ed.edss_last, ed.date_dernier_edss,
    e6.delai_edss6_mois,
    p.type_premier_evenement, p.recuperation_complete,
    l.bandes_oligoclonales,
    ev.severite
FROM cohorte c
LEFT JOIN tap_precoce    tp  ON tp.pseudonyme  = c.pseudonyme
LEFT JOIN tap_post_tap   t2  ON t2.pseudonyme  = c.pseudonyme
LEFT JOIN irm_initiale   i   ON i.pseudonyme   = c.pseudonyme
LEFT JOIN gd_post_tap    g   ON g.pseudonyme   = c.pseudonyme
LEFT JOIN edss_post_tap  e2  ON e2.pseudonyme  = c.pseudonyme
LEFT JOIN edss_dernier   ed  ON ed.pseudonyme  = c.pseudonyme
LEFT JOIN edss6_delai    e6  ON e6.pseudonyme  = c.pseudonyme
LEFT JOIN presentation   p   ON p.pseudonyme   = c.pseudonyme
LEFT JOIN lcr            l   ON l.pseudonyme   = c.pseudonyme
LEFT JOIN evolution      ev  ON ev.pseudonyme  = c.pseudonyme
WHERE c.pseudonyme IN (SELECT pseudonyme FROM patients WHERE registre = 'SEP');
"""


def build_extraction_sql(origine_donnee_disponible: bool) -> str:
    if origine_donnee_disponible:
        return EXTRACTION_SQL_TEMPLATE.format(
            origine_donnee_select=", pt.origine_donnee",
            origine_donnee_join="JOIN patients pt ON pt.pseudonyme = ic.pseudonyme",
            origine_donnee_out="c.origine_donnee,",
        )
    return EXTRACTION_SQL_TEMPLATE.format(
        origine_donnee_select="",
        origine_donnee_join="",
        origine_donnee_out="",
    )


def extract_data(engine, tap_window_months: int = TAP_WINDOW_MONTHS_DEFAULT,
                  origine_donnee_disponible: bool = False) -> pd.DataFrame:
    from sqlalchemy import text
    if not isinstance(tap_window_months, int) or not (1 <= tap_window_months <= 60):
        raise ValueError(f"tap_window_months entre 1 et 60 (recu : {tap_window_months})")
    sql = build_extraction_sql(origine_donnee_disponible)
    df = pd.read_sql(text(sql), engine,
                     params={"tap_window_months": tap_window_months})
    logger.info(f"[extraction] {len(df)} patients SEP (TAP modele = {tap_window_months} mois).")
    return df


def compute_y_objectif(df: pd.DataFrame, tap_window_months: int) -> pd.Series:
    
    c1 = df["nb_poussees_post_tap"].fillna(0) >= 2
    c2 = (df["nb_poussees_post_tap"].fillna(0) >= 1) & (df["max_gd_post_tap"].fillna(0) >= 1)
    c3 = df["edss_max_post_tap"].fillna(0) >= 3
    c4 = (df["edss_last"].fillna(0) >= 6) & (df["delai_edss6_mois"].fillna(999) > tap_window_months) & (df["delai_edss6_mois"].fillna(999) <= 60)
    a_un_evenement = c1 | c2 | c3 | c4

    suivi_suffisant = df["suivi_total_mois"].fillna(0) >= (tap_window_months + 24)
    y = pd.Series(np.where(a_un_evenement, 1.0, np.where(suivi_suffisant, 0.0, np.nan)), index=df.index)

    n_censures = int(((~a_un_evenement) & (~suivi_suffisant)).sum())
    if n_censures > 0:
        logger.info(f"[Y_objectif] {n_censures} patient(s) exclu(s) : suivi post-TAP insuffisant "
                    f"(< {tap_window_months + 24} mois) pour conclure a l'absence d'evenement.")
    return y


def prepare_data(
    df: pd.DataFrame,
    tap_window_months: int = TAP_WINDOW_MONTHS_DEFAULT,
) -> tuple:
    
    df = df.copy()
    n_avant = len(df)

    for col in df.columns:
        if col == "severite":
            continue  # traite plus bas : 'NA' texte a une semantique specifique pour ce champ
        if df[col].dtype == object:
            df[col] = df[col].replace({"NA": np.nan, "N/A": np.nan, "": np.nan})

    # Convention documentee (schema_registre.sql, commentaire de la colonne :
    # "severite VARCHAR(50) -- Hautement active / agressive") : ce champ ne
    # sert qu'a flaguer le sous-groupe hautement actif/agressif. La valeur
    # texte 'NA' est saisie explicitement pour signifier "critere non rempli"
    # (evenement NEGATIF determine), et non "non evalue". Seul un NULL reel
    # (non renseigne en base) est traite comme donnee manquante. Traiter 'NA'
    # comme manquant ici exclurait a tort ~73% des patients (cf. seed :
    # 146/200 patients severite='NA', repartis proportionnellement sur
    # toutes les formes evolutives -- signe d'une valeur determinee, pas
    # d'une saisie incomplete concentree sur un sous-groupe).
    df["y_clinicien"] = np.where(
        df["severite"].isna(), np.nan,
        df["severite"].isin(MODALITES_SEVERITE_POSITIVES).astype(float)
    )

    df["y_objectif"] = compute_y_objectif(df, tap_window_months)

    
    # localisation_moelle est une colonne BOOLEAN en base (TRUE/FALSE), pas
    # une chaine "Oui"/"Non". On accepte les deux representations pour rester
    # robuste si la colonne est un jour convertie en VARCHAR, mais le cas
    # reel (booleen psycopg2 -> True/False Python) est desormais gere.
    mapping_atteinte_medullaire = {
        True: 1, False: 0,
        "Oui": 1, "Non": 0,
        "TRUE": 1, "FALSE": 0, "true": 1, "false": 0,
    }
    valeurs_valides = set(mapping_atteinte_medullaire.keys())
    masque_inattendu = ~df["localisation_moelle"].isin(valeurs_valides) & df["localisation_moelle"].notna()
    valeurs_inconnues = df.loc[masque_inattendu, "localisation_moelle"].unique()
    if len(valeurs_inconnues) > 0:
        logger.warning(
            f"[atteinte_medullaire] localisation_moelle contient {masque_inattendu.sum()} valeur(s) "
            f"inattendue(s) (ni booleen ni 'Oui'/'Non') : {list(valeurs_inconnues)} -> "
            f"ces patients seront exclus par le filtrage en cas complets ci-dessous."
        )
    df["atteinte_medullaire"] = df["localisation_moelle"].map(mapping_atteinte_medullaire)
    predictors = list(PREDICTEURS_CAHIER_DES_CHARGES)

    cols_requises = predictors + ["y_clinicien", "y_objectif"]
    df_model = df.dropna(subset=cols_requises).copy()
    n_exclus = n_avant - len(df_model)
    taux_exclus = 100 * n_exclus / n_avant if n_avant > 0 else 0
    logger.info(f"[preparation] {n_exclus}/{n_avant} exclus ({taux_exclus:.1f}%) -> {len(df_model)} retenus.")
    if taux_exclus > 30:
        logger.warning("[ALERTE] >30% de patients exclus par donnees manquantes. "
                        "Envisager une imputation multiple (MICE) ou un test de Little.")

    n_evt_clin = int(df_model["y_clinicien"].sum())
    n_evt_obj = int(df_model["y_objectif"].sum())
    # len(df_model) peut valoir 0 si la fenetre TAP choisie exclut toute la
    # cohorte : on protege ces logs contre la division par zero pour laisser
    # le controle EFFECTIF_MINIMUM (fait par l'appelant juste apres) produire
    # le message clinique clair, au lieu d'un ZeroDivisionError brut.
    if len(df_model) > 0:
        logger.info(f"[preparation] Y_clinicien (PRINCIPAL) : {n_evt_clin}/{len(df_model)} ({100*n_evt_clin/len(df_model):.1f}%) HA/A")
        logger.info(f"[preparation] Y_objectif  (SECONDAIRE) : {n_evt_obj}/{len(df_model)} ({100*n_evt_obj/len(df_model):.1f}%) HA/A")
        min_epv = min(n_evt_clin, n_evt_obj) / len(predictors)
        if min_epv < 10:
            logger.warning(f"[AVERTISSEMENT] EPV = {min_epv:.1f} (< 10) -> resultats EXPLORATOIRES.")
    else:
        logger.warning("[preparation] Aucun patient retenu apres filtrage des cas complets "
                        "(predicteurs/severite manquants) pour cette fenetre TAP.")
    return df_model, predictors


def check_multicollinearity(df_model: pd.DataFrame, predictors: list) -> pd.DataFrame:
    X = sm.add_constant(df_model[predictors])
    vif_data = pd.DataFrame({
        "variable": X.columns,
        "VIF": [variance_inflation_factor(X.values, i) for i in range(X.shape[1])]
    })
    logger.info("\n[VIF] Facteurs d'inflation de la variance (>5 = colinearite problematique) :\n"
                + vif_data.to_string(index=False))
    return vif_data


def check_box_tidwell(df_model: pd.DataFrame, predictors: list, y_col: str, alpha: float = 0.05) -> pd.DataFrame:
    continuous_vars = [p for p in predictors if p in ["tap_annualise", "age_diagnostic_mois", "nb_lesions_t2"]]
    results = []
    X_base = df_model[predictors].copy()
    y = df_model[y_col]
    for var in continuous_vars:
        x = X_base[var].astype(float)
        offset = 1.0 if (x <= 0).any() else 0.0
        if offset:
            logger.info(f"[Box-Tidwell] {var} contient des valeurs <= 0 -> decalage +1.")
        X_bt = X_base.copy()
        X_bt[f"{var}_ln_interaction"] = x * np.log(x + offset)
        X_bt = sm.add_constant(X_bt)
        try:
            model_bt = sm.Logit(y, X_bt).fit(disp=0)
            p_val = model_bt.pvalues[f"{var}_ln_interaction"]
            coef = model_bt.params[f"{var}_ln_interaction"]
            results.append({"variable": var, "coef_interaction": coef, "p_value": p_val, "non_linearite": "OUI" if p_val < alpha else "Non"})
        except Exception as e:
            results.append({"variable": var, "coef_interaction": np.nan, "p_value": np.nan, "non_linearite": f"Echec ({e})"})
    bt_table = pd.DataFrame(results)
    logger.info("\n[Box-Tidwell] Test de linearite du logit (p < 0.05 -> non lineaire) :\n"
                + bt_table.to_string(index=False))
    n_echecs = bt_table["non_linearite"].astype(str).str.startswith("Echec").sum()
    if n_echecs > 0:
        logger.warning(f"[Box-Tidwell] ALERTE : {n_echecs}/{len(bt_table)} test(s) en echec numerique "
                        f"(matrice singuliere) -> probablement quasi-separation. Linearite NON verifiee.")
    return bt_table

_firth_checked = False
_firth_available = False
_FirthLogisticRegression = None


def _check_firth_available() -> bool:
    global _firth_checked, _firth_available, _FirthLogisticRegression
    if not _firth_checked:
        try:
            from firthlogist import FirthLogisticRegression
            _FirthLogisticRegression = FirthLogisticRegression
            _firth_available = True
        except ImportError:
            _firth_available = False
        _firth_checked = True
    return _firth_available


class _FirthResultAdapter:
    
    def __init__(self, firth_model, predictors):
        self._m = firth_model
        self.columns = list(predictors) + ["const"]
        params_vals = np.append(np.asarray(firth_model.coef_), firth_model.intercept_)
        self.params = pd.Series(params_vals, index=self.columns)
        self.pvalues = pd.Series(np.asarray(firth_model.pvals_), index=self.columns)
        ci = np.asarray(firth_model.ci_)
        self._conf_int = pd.DataFrame(ci, index=self.columns, columns=[0, 1])

    def conf_int(self):
        return self._conf_int

    def predict(self, X_const):
        X_const = np.asarray(X_const)
        X_no_const = X_const[:, 1:]
        return self._m.predict_proba(X_no_const)[:, 1]

    def summary(self):
        return (f"FirthLogisticRegression (vraie penalisation de Jeffreys, package firthlogist)\n"
                f"{pd.DataFrame({'coef': self.params, 'p_value': self.pvalues}).to_string()}")


def fit_logistic_robuste(X: pd.DataFrame, y: pd.Series, predictors: list):
    
    if _check_firth_available():
        try:
            firth = _FirthLogisticRegression()
            firth.fit(X[predictors].values, y.values)
            logger.info("[modele] Convergence Firth (firthlogist, penalisation de Jeffreys).")
            return _FirthResultAdapter(firth, predictors), "firth"
        except Exception as e0:
            logger.info(f"[modele] Echec Firth ({e0}) -> repli sur Newton-Raphson standard.")
    else:
        logger.info("[modele] Package firthlogist non installe -> repli sur Newton-Raphson standard "
                     "(pip install firthlogist --break-system-packages).")

    X_const = sm.add_constant(X[predictors])
    try:
        model = sm.Logit(y, X_const)
        result = model.fit(disp=0, maxiter=100)
        if np.all(np.abs(result.params) < 20):
            logger.info("[modele] Convergence standard (Newton-Raphson).")
            return result, "standard"
        else:
            raise RuntimeError("Coefficients aberrants -> quasi-separation")
    except Exception as e1:
        logger.info(f"[modele] Echec standard : {e1}")
    try:
        model = sm.Logit(y, X_const)
        result = model.fit(disp=0, method="bfgs", maxiter=200)
        if np.all(np.abs(result.params) < 20):
            logger.info("[modele] Convergence BFGS.")
            return result, "bfgs"
        else:
            raise RuntimeError("Coefficients aberrants -> quasi-separation")
    except Exception as e2:
        logger.info(f"[modele] Echec BFGS : {e2}")
    logger.warning("[modele] -> Fallback penalisation L1 alpha=1e-8 (approximation numerique). "
                    "CECI N'EST PAS UNE REGRESSION DE FIRTH.")
    model = sm.Logit(y, X_const)
    result = model.fit_regularized(method="l1", alpha=1e-8, disp=0)
    return result, "l1_fallback"


def fit_and_summarize(df_model: pd.DataFrame, predictors: list, y_col: str):
    y = df_model[y_col]
    result, method = fit_logistic_robuste(df_model, y, predictors)
    logger.info("=" * 70)
    logger.info(f"REGRESSION LOGISTIQUE MULTIVARIEE — Outcome : {y_col} | Methode : {method}")
    logger.info("=" * 70)
    logger.info("\n" + str(result.summary()))
    params = result.params
    conf = result.conf_int()
    conf.columns = ["IC95%_bas", "IC95%_haut"]
    or_table = pd.DataFrame({"OR": np.exp(params), "IC95%_bas": np.exp(conf["IC95%_bas"]), "IC95%_haut": np.exp(conf["IC95%_haut"]), "p_value": result.pvalues})
    logger.info("\n[Odds Ratios]\n" + or_table.to_string())
    logger.info(interpret_odds_ratios(or_table))
    return result, or_table, method


def interpret_odds_ratios(or_table: pd.DataFrame) -> str:
    lignes = ["\n[Interpretation clinique des Odds Ratios]", "-" * 70]
    for var in or_table.index:
        if var == "const":
            continue
        or_val = or_table.loc[var, "OR"]
        ci_low = or_table.loc[var, "IC95%_bas"]
        ci_high = or_table.loc[var, "IC95%_haut"]
        p_val = or_table.loc[var, "p_value"]
        libelle = LIBELLES_PREDICTEURS.get(var, var)
        significatif = p_val < 0.05
        sens = "augmente" if or_val > 1 else "diminue"
        facteur = or_val if or_val > 1 else 1 / or_val
        conclusion = "EFFET SIGNIFICATIF" if significatif else "NON SIGNIFICATIF (IC inclut 1)"
        suffixe = "." if significatif else " — possible manque de puissance."
        phrase = (
            f"• {libelle} :\n"
            f"    OR = {or_val:.2f} (IC95% [{ci_low:.2f} – {ci_high:.2f}]), p = {p_val:.3f}\n"
            f"    -> {sens} le risque d'un facteur ~{facteur:.2f}.\n"
            f"    -> {conclusion}{suffixe}"
        )
        lignes.append(phrase)
    lignes.append("-" * 70)
    return "\n".join(lignes)




def cross_validated_predictions(df_model: pd.DataFrame, predictors: list, y_col: str) -> np.ndarray:
    
    y = df_model[y_col].values
    X = df_model[predictors].values

    n_classe_0 = int(np.sum(y == 0))
    n_classe_1 = int(np.sum(y == 1))
    n_min_classe = min(n_classe_0, n_classe_1)

    if n_min_classe < 2:
        logger.error(f"[CV] Classe minoritaire trop petite (n={n_min_classe}) -> "
                      f"validation croisee impossible, probabilites OOF non calculees.")
        return np.full(len(df_model), np.nan)

    n_folds_eff = min(N_FOLDS, n_min_classe)
    if n_folds_eff < N_FOLDS:
        logger.warning(f"[CV] N_FOLDS reduit de {N_FOLDS} a {n_folds_eff} "
                        f"(classe minoritaire = {n_min_classe} patients).")

    oof_proba = np.full(len(df_model), np.nan)
    skf = StratifiedKFold(n_splits=n_folds_eff, shuffle=True, random_state=RANDOM_STATE)
    n_cols_attendu = len(predictors) + 1  
    for fold_i, (train_idx, test_idx) in enumerate(skf.split(X, y), start=1):
        y_train = y[train_idx]
        if len(np.unique(y_train)) < 2:
            logger.warning(f"[CV fold {fold_i}] Une seule classe dans l'entrainement -> fold ignore.")
            continue
        X_test = sm.add_constant(X[test_idx], has_constant="add")
        if X_test.shape[1] != n_cols_attendu:
            X_test = np.column_stack([np.ones(len(test_idx)), X[test_idx]])
        try:
            result, _ = fit_logistic_robuste(
                pd.DataFrame(X[train_idx], columns=predictors),
                pd.Series(y_train), predictors)
            oof_proba[test_idx] = result.predict(X_test)
        except Exception as e:
            logger.warning(f"[CV fold {fold_i}] Echec ({e}) — fold ignore.")
    n_valid = np.sum(~np.isnan(oof_proba))
    logger.info(f"[CV] Probabilites OOF obtenues pour {n_valid}/{len(df_model)} patients "
                f"({n_folds_eff} folds).")
    return oof_proba


def _bca_confidence_interval(point: float, replicats: np.ndarray, jackknife_stats: np.ndarray,
                              alpha: float = 0.05) -> tuple:
    
    replicats = replicats[~np.isnan(replicats)]
    z0 = scipy_stats.norm.ppf(np.mean(replicats < point))
    if not np.isfinite(z0):
        raise ValueError("z0 non fini (replicats bootstrap degeneres)")

    jackknife_stats = jackknife_stats[~np.isnan(jackknife_stats)]
    jack_mean = jackknife_stats.mean()
    num = np.sum((jack_mean - jackknife_stats) ** 3)
    den = 6.0 * (np.sum((jack_mean - jackknife_stats) ** 2) ** 1.5)
    a_hat = num / den if den != 0 else 0.0

    def _bca_percentile(p):
        z_p = scipy_stats.norm.ppf(p)
        denom = 1 - a_hat * (z0 + z_p)
        if denom == 0:
            raise ValueError("Denominateur BCa nul")
        adj = z0 + (z0 + z_p) / denom
        return scipy_stats.norm.cdf(adj)

    p_low = _bca_percentile(alpha / 2)
    p_high = _bca_percentile(1 - alpha / 2)
    ci_low, ci_high = np.percentile(replicats, [100 * p_low, 100 * p_high])
    return ci_low, ci_high


def bootstrap_auc_ci(y_true: np.ndarray, y_proba: np.ndarray, n_bootstrap: int = N_BOOTSTRAP) -> dict:
    
    mask = ~np.isnan(y_proba)
    y_true, y_proba = np.asarray(y_true)[mask], np.asarray(y_proba)[mask]
    n = len(y_true)
    if n == 0:
        raise ValueError(
            "Effectif insuffisant pour ce modele : aucune probabilite hors-echantillon "
            "(validation croisee) n'a pu etre calculee -- la classe minoritaire (patients "
            "avec evenement positif) est trop petite pour former les plis de validation "
            "croisee (voir le journal d'execution, section [CV]). Completez la severite "
            "declaree ou l'evenement objectif pour davantage de patients, ou elargissez "
            "la fenetre TAP precoce."
        )
    if len(np.unique(y_true)) < 2:
        raise ValueError(
            "Effectif insuffisant pour ce modele : les probabilites hors-echantillon "
            "valides ne couvrent qu'une seule classe d'evenement -- l'AUC n'est pas "
            "definissable. Completez les donnees ou elargissez la fenetre TAP precoce "
            "pour obtenir des evenements positifs et negatifs en nombre suffisant."
        )
    point = roc_auc_score(y_true, y_proba)
    rng = np.random.RandomState(RANDOM_STATE)

    aucs = []
    for _ in range(n_bootstrap):
        idx = rng.randint(0, n, n)
        y_b, p_b = y_true[idx], y_proba[idx]
        if len(np.unique(y_b)) < 2:
            continue
        aucs.append(roc_auc_score(y_b, p_b))
    aucs = np.array(aucs)

    if len(aucs) < BCA_MIN_REPLICATS_VALIDES:
        logger.warning(f"[Bootstrap AUC] Seulement {len(aucs)} replicats valides (< {BCA_MIN_REPLICATS_VALIDES}) "
                        f"-> repli sur IC percentile simple (BCa non fiable).")
        ci_low, ci_high = np.percentile(aucs, [2.5, 97.5]) if len(aucs) > 0 else (np.nan, np.nan)
        method = "percentile"
    else:
        jack_aucs = []
        for i in range(n):
            idx_j = np.delete(np.arange(n), i)
            y_j, p_j = y_true[idx_j], y_proba[idx_j]
            if len(np.unique(y_j)) < 2:
                continue
            jack_aucs.append(roc_auc_score(y_j, p_j))
        jack_aucs = np.array(jack_aucs)
        try:
            ci_low, ci_high = _bca_confidence_interval(point, aucs, jack_aucs)
            method = "BCa"
        except ValueError as e:
            logger.warning(f"[Bootstrap AUC] BCa impossible ({e}) -> repli sur IC percentile simple.")
            ci_low, ci_high = np.percentile(aucs, [2.5, 97.5])
            method = "percentile"

    logger.info(f"[Bootstrap AUC] {point:.3f} (IC95% {method} [{ci_low:.3f} – {ci_high:.3f}], "
                f"{len(aucs)}/{n_bootstrap} replicats valides)")
    return {"auc": point, "ci_low": ci_low, "ci_high": ci_high, "n_valid": len(aucs), "method": method}


def hosmer_lemeshow(y_true: np.ndarray, y_proba: np.ndarray, n_groups: int = 5) -> dict:
    
    mask = ~np.isnan(y_proba)
    y_true, y_proba = np.asarray(y_true)[mask], np.asarray(y_proba)[mask]
    df_hl = pd.DataFrame({"y": y_true, "p": y_proba})
    df_hl["groupe"] = pd.qcut(df_hl["p"], q=n_groups, duplicates="drop")
    obs = df_hl.groupby("groupe", observed=True).agg(n=("y", "size"), obs_pos=("y", "sum"), p_mean=("p", "mean"))
    obs["attendu"] = obs["n"] * obs["p_mean"]
    if (obs["n"] < 5).any():
        logger.warning("[HL] AVERTISSEMENT : groupe(s) < 5 patients -> reduire n_groups.")

    n_total_groupes = len(obs)
    obs_valides = obs[(obs["p_mean"] > 0) & (obs["p_mean"] < 1)]
    n_exclus = n_total_groupes - len(obs_valides)
    if n_exclus > 0:
        logger.warning(f"[HL] ALERTE : {n_exclus}/{n_total_groupes} groupe(s) exclu(s) du test "
                        f"(p_mean = 0 ou 1 exactement) -> signe probable de quasi-separation.")

    if len(obs_valides) >= 2:
        ratio_taille = obs_valides["n"].max() / obs_valides["n"].min()
        if ratio_taille > HL_GROUP_IMBALANCE_RATIO_ALERTE:
            logger.warning(f"[HL] ALERTE : groupes tres desequilibres (ratio taille max/min = "
                            f"{ratio_taille:.1f}x, seuil = {HL_GROUP_IMBALANCE_RATIO_ALERTE}x) -> "
                            f"le chi2 est peu fiable ici, interpreter avec prudence.")

    if len(obs_valides) < 3:
        logger.warning("[Hosmer-Lemeshow] Trop peu de groupes exploitables (<3) -> test non calculable.")
        return {"chi2": np.nan, "dof": np.nan, "p_value": np.nan, "table": obs,
                "n_groupes_exclus": n_exclus}

    chi2 = np.sum((obs_valides["obs_pos"] - obs_valides["attendu"]) ** 2
                  / (obs_valides["n"] * obs_valides["p_mean"] * (1 - obs_valides["p_mean"])))
    dof = len(obs_valides) - 2
    p_val = 1 - scipy_stats.chi2.cdf(chi2, dof) if dof > 0 else np.nan
    logger.info(f"[Hosmer-Lemeshow] Chi2 = {chi2:.3f}, ddl = {dof}, p = {p_val:.3f} "
                f"(sur {len(obs_valides)}/{n_total_groupes} groupes valides)")
    return {"chi2": chi2, "dof": dof, "p_value": p_val, "table": obs, "n_groupes_exclus": n_exclus}


def plot_calibration(y_true, y_proba, output_path, n_bins=8):
    mask = ~np.isnan(y_proba)
    y_true, y_proba = np.asarray(y_true)[mask], np.asarray(y_proba)[mask]
    prob_true, prob_pred = calibration_curve(y_true, y_proba, n_bins=n_bins, strategy="quantile")
    plt.figure(figsize=(6, 6))
    plt.plot(prob_pred, prob_true, marker="o", label="Modele")
    plt.plot([0, 1], [0, 1], "k--", label="Calibration parfaite")
    plt.xlabel("Probabilite predite moyenne")
    plt.ylabel("Frequence observee")
    plt.title("Courbe de calibration")
    plt.legend(loc="upper left")
    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()
    logger.info(f"[Calibration] Graphique : {output_path}")



def build_points_score(result, predictors: list, df_model: pd.DataFrame) -> pd.DataFrame:
    
    betas = result.params.drop("const", errors="ignore")
    moyennes = df_model[predictors].mean()
    ecarts_types = df_model[predictors].std(ddof=0).replace(0, 1.0)
    effet_par_ecart_type = betas * ecarts_types
    b_ref_brut = effet_par_ecart_type.abs().min()
    b_ref = max(b_ref_brut, SCORE_B_REF_PLANCHER)
    if b_ref_brut < SCORE_B_REF_PLANCHER:
        logger.warning(f"[Score] Effet minimal quasi nul ({b_ref_brut:.2e}) -> reference plafonnee "
                        f"a {SCORE_B_REF_PLANCHER:.0e} pour eviter des points aberrants.")
    points = (effet_par_ecart_type / b_ref).round().astype(int)
    score_table = pd.DataFrame({
        "variable": predictors,
        "beta": betas.values,
        "OR": np.exp(betas.values),
        "moyenne": moyennes.values,
        "ecart_type": ecarts_types.values,
        "effet_par_ecart_type": effet_par_ecart_type.values,
        "points": points.values,
    })
    logger.info("\n[Score en points] Grille Sullivan standardisee :\n" + score_table.to_string(index=False))
    return score_table


def compute_round_thresholds(scores: np.ndarray, method: str = "terciles") -> tuple:
    if method == "terciles":
        t1, t2 = np.percentile(scores, [33.3, 66.7])
        s1 = round(float(t1), 1)
        s2 = round(float(t2), 1)
        if s2 <= s1:
            s2 = s1 + 0.2
        return s1, s2
    return 0.0, 0.0


def score_to_risk_table(df_model: pd.DataFrame, score_table: pd.DataFrame, predictors: list, y_col: str, seuil_bas: int = None, seuil_haut: int = None):
    
    score_table_idx = score_table.set_index("variable").reindex(predictors)
    manquants = score_table_idx[score_table_idx["points"].isna()].index.tolist()
    if manquants:
        logger.warning(f"[Score] Predicteur(s) absent(s) de score_table : {manquants} -> "
                        f"points fixes a 0 (contribution nulle au score total).")
    score_table_idx["points"] = score_table_idx["points"].fillna(0)
    score_table_idx["moyenne"] = score_table_idx["moyenne"].fillna(0.0)
    score_table_idx["ecart_type"] = score_table_idx["ecart_type"].fillna(1.0)

    df_scored = df_model.copy()
    score_total = pd.Series(0.0, index=df_scored.index)
    for var in predictors:
        pts = score_table_idx.loc[var, "points"]
        mu = score_table_idx.loc[var, "moyenne"]
        sigma = score_table_idx.loc[var, "ecart_type"] or 1.0
        z = (df_scored[var] - mu) / sigma
        score_total = score_total + z * pts
    df_scored["score_total"] = score_total.round(1)
    if seuil_bas is None or seuil_haut is None:
        seuil_bas, seuil_haut = compute_round_thresholds(df_scored["score_total"].values)
        logger.info(f"[Seuils] Auto-calculés (terciles) : {seuil_bas} et {seuil_haut}")
    else:
        logger.info(f"[Seuils] Forces par l'interface : {seuil_bas} et {seuil_haut}")

    def categoriser(score):
        if score < seuil_bas:
            return "Risque faible"
        elif score < seuil_haut:
            return "Risque intermediaire"
        return "Risque eleve"

    df_scored["categorie_risque"] = df_scored["score_total"].apply(categoriser)
    risk_table = df_scored.groupby("categorie_risque").agg(n=(y_col, "size"), n_events=(y_col, "sum"))
    risk_table["taux_observe_%"] = (100 * risk_table["n_events"] / risk_table["n"]).round(1)
    logger.info("\n[Categories de risque] :\n" + risk_table.to_string())
    return df_scored, risk_table, (seuil_bas, seuil_haut)


def evaluate_roc(df_model: pd.DataFrame, result, predictors: list, y_col: str, output_path: str):
    X = sm.add_constant(df_model[predictors])
    y_true = df_model[y_col]
    y_proba = result.predict(X)
    fpr, tpr, thresholds = roc_curve(y_true, y_proba)
    auc_val = roc_auc_score(y_true, y_proba)
    youden = tpr - fpr
    best_idx = np.argmax(youden)
    best_thresh = thresholds[best_idx]
    best_sens, best_spec = tpr[best_idx], 1 - fpr[best_idx]
    logger.info(f"[ROC apparente] AUC = {auc_val:.3f}")
    logger.info(f"[Youden] Seuil = {best_thresh:.3f} -> Se = {best_sens:.3f}, Sp = {best_spec:.3f}")
    plt.figure(figsize=(6, 6))
    plt.plot(fpr, tpr, label=f"AUC = {auc_val:.3f}")
    plt.plot([0, 1], [0, 1], "k--")
    plt.scatter(fpr[best_idx], tpr[best_idx], color="red", zorder=5, label=f"Seuil optimal = {best_thresh:.2f}")
    plt.xlabel("1 - Specificite")
    plt.ylabel("Sensibilite")
    plt.title(f"ROC — {y_col}")
    plt.legend(loc="lower right")
    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()
    logger.info(f"[ROC] Graphique : {output_path}")
    return {"auc": auc_val, "threshold": best_thresh, "sensibilite": best_sens, "specificite": best_spec}


def analyze_concordance(df_model: pd.DataFrame) -> dict:
    yc = df_model["y_clinicien"].astype(int)
    yo = df_model["y_objectif"].astype(int)
    kappa = cohen_kappa_score(yc, yo)
    cm = confusion_matrix(yc, yo)
    acc = (cm[0, 0] + cm[1, 1]) / cm.sum()
    logger.info("=" * 70)
    logger.info("CONCORDANCE Y_clinicien vs Y_objectif")
    logger.info(f"Kappa de Cohen = {kappa:.3f} | Exactitude = {acc:.3f}")
    logger.info("\nMatrice de confusion (lignes=Y_clinicien, colonnes=Y_objectif) :\n"
                + pd.DataFrame(cm, index=["Non-HA/A", "HA/A"], columns=["Non-HA/A", "HA/A"]).to_string())
    if kappa < 0.4:
        logger.warning("[ALERTE] Kappa < 0.4 -> DESACCORD MODERE A FAIBLE.")
    elif kappa < 0.6:
        logger.info("[ATTENTION] Kappa 0.4-0.6 -> Concordance MODEREE. A discuter avec Pr. Kraoua.")
    else:
        logger.info("[OK] Kappa >= 0.6 -> Concordance SUBSTANTIELLE A EXCELLENTE.")
    return {"kappa": kappa, "accuracy": acc, "cm": cm}


def generate_rapport_clinicien(
    n_patients: int, n_evt_clin: int, n_evt_obj: int, tap_window_months: int,
    kappa: float, or_table_clin: pd.DataFrame, or_table_obj: pd.DataFrame,
    roc_clin_app: dict, roc_obj_app: dict,
    boot_clin: dict, boot_obj: dict,
    hl_clin: dict, hl_obj: dict,
    score_table_clin: pd.DataFrame, score_table_obj: pd.DataFrame,
    risk_clin: pd.DataFrame, risk_obj: pd.DataFrame,
    seuils_clin: tuple, seuils_obj: tuple,
    output_path: str,
    fit_method_clin: str = "?", fit_method_obj: str = "?",
    bt_echecs_clin: int = 0, bt_echecs_obj: int = 0,
    n_patients_simules: int = 0,
    origine_donnee_disponible: bool = True,
):
    lignes = []
    lignes.append("=" * 78)
    lignes.append("RAPPORT DE SYNTHESE v10 — Predicteurs precoces de forme HA/A")
    lignes.append("Registre SEP pediatrique — INN Pediatrique")
    lignes.append(f"Predicteurs (cahier des charges, item 8) : {', '.join(PREDICTEURS_CAHIER_DES_CHARGES)}")
    lignes.append("=" * 78)
    lignes.append("")
    if not origine_donnee_disponible:
        lignes.append("[NOTE TECHNIQUE] Colonne patients.origine_donnee absente en base -> ")
        lignes.append("   la verification 'cohorte partiellement simulee' n'a PAS pu etre effectuee.")
        lignes.append("")
    if n_patients_simules > 0:
        taux_sim = 100 * n_patients_simules / n_patients if n_patients > 0 else 0
        lignes.append("!!! ATTENTION - COHORTE PARTIELLEMENT SIMULEE !!!")
        lignes.append(f"    {n_patients_simules}/{n_patients} patients ({taux_sim:.0f}%) sont des donnees SIMULEES.")
        lignes.append("")
    lignes.append("1. POPULATION ANALYSEE")
    lignes.append(f"   {n_patients} patients avec donnees completes.")
    lignes.append(f"   Y_clinicien (PRINCIPAL) : {n_evt_clin}/{n_patients} ({100*n_evt_clin/n_patients:.1f}%) HA/A")
    lignes.append(f"   Y_objectif  (SECONDAIRE) : {n_evt_obj}/{n_patients} ({100*n_evt_obj/n_patients:.1f}%) HA/A")
    lignes.append(f"   Fenetre TAP predicteur : {tap_window_months} mois apres diagnostic.")
    lignes.append(f"   Fenetre Y_objectif : {tap_window_months}–{tap_window_months+24} mois post-TAP ; critere EDSS>=6 borne a 5 ans.")
    lignes.append("")
    lignes.append("2. CONCORDANCE ENTRE LES DEUX DEFINITIONS DE SEVERITE")
    lignes.append(f"   Kappa de Cohen = {kappa:.3f}")
    if kappa < 0.4:
        lignes.append("   -> DESACCORD MODERE A FAIBLE. Privilegier Y_objectif pour toute conclusion statistique.")
    elif kappa < 0.6:
        lignes.append("   -> Concordance MODEREE. A valider avec Pr. Kraoua avant publication.")
    else:
        lignes.append("   -> Concordance SUBSTANTIELLE A EXCELLENTE.")
    lignes.append("")
    lignes.append("3. ANALYSE PRINCIPALE — Y_clinicien (severite declaree)")
    lignes.append(f"   Methode d'ajustement : {fit_method_clin}"
                   + ("  [Firth]" if fit_method_clin == "firth" else
                      "  [ATTENTION : repli L1, pas de vraie Firth]" if fit_method_clin == "l1_fallback" else ""))
    lignes.append(f"   AUC OOF = {boot_clin['auc']:.3f} (IC95% {boot_clin.get('method','?')} "
                   f"[{boot_clin['ci_low']:.3f} – {boot_clin['ci_high']:.3f}])")
    hl_p_clin = hl_clin['p_value']
    lignes.append(f"   Hosmer-Lemeshow p = {hl_p_clin:.3f}" if not np.isnan(hl_p_clin)
                   else "   Hosmer-Lemeshow : NON CALCULABLE (quasi-separation)")
    if bt_echecs_clin > 0:
        lignes.append(f"   [ALERTE] Box-Tidwell en echec pour {bt_echecs_clin} variable(s).")
    lignes.append(f"   Seuils score proposes : < {seuils_clin[0]} / {seuils_clin[0]}-{seuils_clin[1]} / >= {seuils_clin[1]}")
    lignes.append("")
    lignes.append("4. ANALYSE SECONDAIRE DE SENSIBILITE — Y_objectif (definition objective post-TAP)")
    lignes.append(f"   Methode d'ajustement : {fit_method_obj}"
                   + ("  [Firth]" if fit_method_obj == "firth" else
                      "  [ATTENTION : repli L1, pas de vraie Firth]" if fit_method_obj == "l1_fallback" else ""))
    lignes.append(f"   AUC OOF = {boot_obj['auc']:.3f} (IC95% {boot_obj.get('method','?')} "
                   f"[{boot_obj['ci_low']:.3f} – {boot_obj['ci_high']:.3f}])")
    hl_p_obj = hl_obj['p_value']
    lignes.append(f"   Hosmer-Lemeshow p = {hl_p_obj:.3f}" if not np.isnan(hl_p_obj)
                   else "   Hosmer-Lemeshow : NON CALCULABLE (quasi-separation)")
    if bt_echecs_obj > 0:
        lignes.append(f"   [ALERTE] Box-Tidwell en echec pour {bt_echecs_obj} variable(s).")
    lignes.append(f"   Seuils score proposes : < {seuils_obj[0]} / {seuils_obj[0]}-{seuils_obj[1]} / >= {seuils_obj[1]}")
    lignes.append("")
    lignes.append("5. LIMITES ET RECOMMANDATIONS")
    lignes.append("   - Y_clinicien = analyse principale demandee par l'encadrante (Pr. Kraoua).")
    lignes.append("   - Y_objectif = analyse secondaire exploratoire, sans chevauchement temporel avec les predicteurs.")
    lignes.append("   - Predicteurs strictement limites aux 4 du cahier des charges (item 8).")
    lignes.append("   - Les seuils du score sont provisoires (terciles arrondis) -> a valider cliniquement.")
    lignes.append("   - EPV < 10 -> resultats exploratoires, a confirmer sur cohorte plus grande.")
    if "l1_fallback" in (fit_method_clin, fit_method_obj):
        lignes.append("   - Methode de repli L1 utilisee pour au moins une analyse : installer firthlogist.")
    lignes.append("=" * 78)
    rapport = "\n".join(lignes)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(rapport)
    logger.info(f"[Rapport clinicien] Enregistre : {output_path}")
    return rapport


def export_chiffres_complets(
    label: str, output_suffix: str, y_col: str, n_patients: int, n_events: int,
    vif_data: pd.DataFrame, bt_table: pd.DataFrame, result, or_table: pd.DataFrame,
    roc_app: dict, oof_n_valid: int, boot: dict, hl: dict,
    score_table: pd.DataFrame, risk_table: pd.DataFrame, seuils: tuple,
    fit_method: str, output_path: str,
):
    lignes = []
    lignes.append("=" * 78)
    lignes.append(f"CHIFFRES COMPLETS — {label}")
    lignes.append(f"Outcome : {y_col}  |  Methode d'ajustement : {fit_method}")
    lignes.append("=" * 78)
    lignes.append("\n--- 1. POPULATION ---")
    lignes.append(f"N patients (cas complets) : {n_patients}")
    lignes.append(f"N evenements (HA/A)       : {n_events} ({100*n_events/n_patients:.1f}%)")
    lignes.append(f"EPV (events per variable) : {n_events/len(PREDICTEURS_CAHIER_DES_CHARGES):.2f}")
    lignes.append("\n--- 2. MULTICOLLINEARITE (VIF) ---")
    lignes.append(vif_data.to_string(index=False))
    lignes.append("\n--- 3. LINEARITE DU LOGIT (Box-Tidwell) ---")
    lignes.append(bt_table.to_string(index=False))
    lignes.append("\n--- 4. REGRESSION LOGISTIQUE MULTIVARIEE (summary complet) ---")
    try:
        lignes.append(str(result.summary()))
    except Exception as e:
        lignes.append(f"[summary() indisponible : {e}]")
    lignes.append("\n--- 5. ODDS RATIOS (IC95%, p-values) ---")
    lignes.append(or_table.to_string())
    lignes.append("\n--- 6. COURBE ROC APPARENTE (in-sample) ---")
    lignes.append(f"AUC apparente        : {roc_app['auc']:.3f}")
    lignes.append(f"Seuil optimal (Youden): {roc_app['threshold']:.3f}")
    lignes.append(f"Sensibilite           : {roc_app['sensibilite']:.3f}")
    lignes.append(f"Specificite           : {roc_app['specificite']:.3f}")
    lignes.append("\n--- 7. VALIDATION CROISEE (hors echantillon) ---")
    lignes.append(f"Patients avec probabilite OOF valide : {oof_n_valid}/{n_patients}")
    lignes.append(f"AUC OOF (bootstrap)  : {boot['auc']:.3f}")
    lignes.append(f"IC95% AUC OOF ({boot.get('method','?')}) : [{boot['ci_low']:.3f} – {boot['ci_high']:.3f}]")
    lignes.append(f"Replicats bootstrap valides : {boot['n_valid']}/{N_BOOTSTRAP}")
    lignes.append("\n--- 8. CALIBRATION (Hosmer-Lemeshow) ---")
    if np.isnan(hl["p_value"]):
        lignes.append("Test NON CALCULABLE (probabilites trop polarisees / <3 groupes exploitables).")
    else:
        lignes.append(f"Chi2 = {hl['chi2']:.3f}, ddl = {hl['dof']}, p = {hl['p_value']:.3f}")
    lignes.append(f"Groupes exclus (p_mean=0 ou 1) : {hl['n_groupes_exclus']}")
    lignes.append("\nDetail par groupe (quantiles de probabilite predite) :")
    lignes.append(hl["table"].to_string())
    lignes.append("\n--- 9. SCORE PREDICTIF COMBINE (grille standardisee) ---")
    lignes.append(score_table.to_string(index=False))
    lignes.append(f"\nSeuils de risque (score standardise) : bas={seuils[0]} / haut={seuils[1]}")
    lignes.append("\n--- 10. CATEGORIES DE RISQUE (taux observe) ---")
    lignes.append(risk_table.to_string())
    lignes.append("\n" + "=" * 78)
    rapport = "\n".join(lignes)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(rapport)
    logger.info(f"[export] Chiffres complets : {output_path}")
    return rapport


def run_full_analysis(df_model, predictors, y_col, label, tap_window_months,
                     score_seuil_bas, score_seuil_haut, output_suffix):
    logger.info("=" * 70)
    logger.info(f"ANALYSE COMPLETE — {label}")
    logger.info("=" * 70)
    vif_data = check_multicollinearity(df_model, predictors)
    vif_data.to_csv(os.path.join(OUTPUT_DIR, f"vif_{output_suffix}.csv"), index=False)
    bt_table = check_box_tidwell(df_model, predictors, y_col)
    bt_table.to_csv(os.path.join(OUTPUT_DIR, f"box_tidwell_{output_suffix}.csv"), index=False)
    bt_echecs = int(bt_table["non_linearite"].astype(str).str.startswith("Echec").sum())
    result, or_table, fit_method = fit_and_summarize(df_model, predictors, y_col)
    or_table.to_csv(os.path.join(OUTPUT_DIR, f"odds_ratios_{output_suffix}.csv"))
    roc_app = evaluate_roc(df_model, result, predictors, y_col,
                           os.path.join(OUTPUT_DIR, f"roc_apparente_{output_suffix}.png"))
    oof_proba = cross_validated_predictions(df_model, predictors, y_col)
    boot = bootstrap_auc_ci(df_model[y_col].values, oof_proba)
    hl = hosmer_lemeshow(df_model[y_col].values, oof_proba, n_groups=5)
    plot_calibration(df_model[y_col].values, oof_proba,
                     os.path.join(OUTPUT_DIR, f"calibration_{output_suffix}.png"))
    score_table = build_points_score(result, predictors, df_model)
    score_table.to_csv(os.path.join(OUTPUT_DIR, f"score_points_{output_suffix}.csv"), index=False)
    df_scored, risk_table, seuils = score_to_risk_table(df_model, score_table, predictors, y_col,
                                                        score_seuil_bas, score_seuil_haut)
    risk_table.to_csv(os.path.join(OUTPUT_DIR, f"categories_risque_{output_suffix}.csv"))
    df_export = df_model.copy()
    df_export["proba_apparente"] = result.predict(sm.add_constant(df_model[predictors]))
    df_export["proba_cv"] = oof_proba
    df_export["score_total"] = df_scored["score_total"].values
    df_export["categorie_risque"] = df_scored["categorie_risque"].values
    df_export.to_csv(os.path.join(OUTPUT_DIR, f"dataset_predictions_{output_suffix}.csv"), index=False)
    n_oof_valid = int(np.sum(~np.isnan(oof_proba)))
    export_chiffres_complets(
        label=label, output_suffix=output_suffix, y_col=y_col,
        n_patients=len(df_model), n_events=int(df_model[y_col].sum()),
        vif_data=vif_data, bt_table=bt_table, result=result, or_table=or_table,
        roc_app=roc_app, oof_n_valid=n_oof_valid, boot=boot, hl=hl,
        score_table=score_table, risk_table=risk_table, seuils=seuils,
        fit_method=fit_method,
        output_path=os.path.join(OUTPUT_DIR, f"chiffres_complets_{output_suffix}.txt"),
    )
    return {
        "result": result, "or_table": or_table, "roc_app": roc_app,
        "bootstrap": boot, "hl": hl, "score_table": score_table,
        "risk_table": risk_table, "seuils": seuils, "vif": vif_data, "bt": bt_table,
        "fit_method": fit_method, "bt_echecs": bt_echecs,
    }


def main(
    tap_window_months: int = TAP_WINDOW_MONTHS_DEFAULT,
    score_seuil_bas_clin: int = None,
    score_seuil_haut_clin: int = None,
    score_seuil_bas_obj: int = None,
    score_seuil_haut_obj: int = None,
):
    engine = get_engine()
    origine_donnee_disponible = check_origine_donnee_disponible(engine)
    df_raw = extract_data(engine, tap_window_months=tap_window_months,
                          origine_donnee_disponible=origine_donnee_disponible)
    n_patients_simules = (
        int(df_raw["origine_donnee"].eq("simule").sum())
        if origine_donnee_disponible and "origine_donnee" in df_raw.columns
        else 0
    )
    df_model, predictors = prepare_data(df_raw, tap_window_months=tap_window_months)
    if len(df_model) < 10:
        logger.error("[ERREUR] Effectif insuffisant. Arret.")
        sys.exit(1)
    concordance = analyze_concordance(df_model)
    res_clin = run_full_analysis(
        df_model, predictors, "y_clinicien", "Y_CLINICIEN (PRINCIPAL — severite declaree)",
        tap_window_months, score_seuil_bas_clin, score_seuil_haut_clin, "y_clinicien"
    )
    res_obj = run_full_analysis(
        df_model, predictors, "y_objectif", "Y_OBJECTIF (SECONDAIRE — definition objective post-TAP)",
        tap_window_months, score_seuil_bas_obj, score_seuil_haut_obj, "y_objectif"
    )
    generate_rapport_clinicien(
        n_patients=len(df_model),
        n_evt_clin=int(df_model["y_clinicien"].sum()),
        n_evt_obj=int(df_model["y_objectif"].sum()),
        tap_window_months=tap_window_months,
        kappa=concordance["kappa"],
        or_table_clin=res_clin["or_table"], or_table_obj=res_obj["or_table"],
        roc_clin_app=res_clin["roc_app"], roc_obj_app=res_obj["roc_app"],
        boot_clin=res_clin["bootstrap"], boot_obj=res_obj["bootstrap"],
        hl_clin=res_clin["hl"], hl_obj=res_obj["hl"],
        score_table_clin=res_clin["score_table"], score_table_obj=res_obj["score_table"],
        risk_clin=res_clin["risk_table"], risk_obj=res_obj["risk_table"],
        seuils_clin=res_clin["seuils"], seuils_obj=res_obj["seuils"],
        output_path=os.path.join(OUTPUT_DIR, "RAPPORT_CLINICIEN_sep_severite_v10.txt"),
        fit_method_clin=res_clin["fit_method"], fit_method_obj=res_obj["fit_method"],
        bt_echecs_clin=res_clin["bt_echecs"], bt_echecs_obj=res_obj["bt_echecs"],
        n_patients_simules=n_patients_simules,
        origine_donnee_disponible=origine_donnee_disponible,
    )
    logger.info(f"[export] Tous les resultats sont dans {OUTPUT_DIR}")
    logger.info("[export] -> Commencer par RAPPORT_CLINICIEN_sep_severite_v10.txt")


if __name__ == "__main__":
    args = sys.argv[1:]
    tap_w = int(args[0]) if len(args) > 0 else TAP_WINDOW_MONTHS_DEFAULT
    main(tap_window_months=tap_w)