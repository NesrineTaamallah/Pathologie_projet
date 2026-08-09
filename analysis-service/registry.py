import os
from script_runner import run_original_script

SCRIPTS_DIR = os.environ.get(
    "SCRIPTS_DIR",
    os.path.join(os.path.dirname(__file__), "..", "test_analyse_statistique"),
)

PG_ENV = {
    "PGHOST": os.environ.get("PGHOST", "localhost"),
    "PGPORT": os.environ.get("PGPORT", "5432"),
    "PGDATABASE": os.environ.get("PGDATABASE", "registre_neuroexo"),
    "PGUSER": os.environ.get("PGUSER", "postgres"),
    "PGPASSWORD": os.environ.get("PGPASSWORD", ""),
}


def _sep(nom_fichier):
    return os.path.join(SCRIPTS_DIR, "SEP", nom_fichier)


def _epr(nom_fichier):
    return os.path.join(SCRIPTS_DIR, "EPR", nom_fichier)




def run_sep1(engine, config):
    from sep.test1_delai_diagnostic_edss import run as _run
    return _run(engine, config)


from sep.test1_delai_diagnostic_edss import PARAMETRES_SCHEMA as SEP1_PARAMETRES_SCHEMA


def run_sep2(engine, config):
    
    from sep.test2_recuperation_edss import run as _run
    return _run(engine, config)


from sep.test2_recuperation_edss import PARAMETRES_SCHEMA as SEP2_PARAMETRES_SCHEMA


def run_sep3(engine, config):
    
    from sep.test3_tap_precoce import run as _run
    return _run(engine, config)


from sep.test3_tap_precoce import PARAMETRES_SCHEMA as SEP3_PARAMETRES_SCHEMA


def run_sep4(engine, config):
    
    from sep.test4_charge_t2_severite import run as _run
    return _run(engine, config)


from sep.test4_charge_t2_severite import PARAMETRES_SCHEMA as SEP4_PARAMETRES_SCHEMA


def run_sep5(engine, config):
    
    from sep.test5_lcr_survie_tap import run as _run
    return _run(engine, config)


from sep.test5_lcr_survie_tap import PARAMETRES_SCHEMA as SEP5_PARAMETRES_SCHEMA


def run_sep6(engine, config):
    from sep.test6_consanguinite import run as _run
    return _run(engine, config)


from sep.test6_consanguinite import PARAMETRES_SCHEMA as SEP6_PARAMETRES_SCHEMA


def run_sep7(engine, config):
    from sep.test7_lignes_therapeutiques import run as _run
    return _run(engine, config)


from sep.test7_lignes_therapeutiques import PARAMETRES_SCHEMA as SEP7_PARAMETRES_SCHEMA


def run_sep8(engine, config):
    from sep.test8_severite_prediction import run as _run
    return _run(engine, config)


from sep.test8_severite_prediction import PARAMETRES_SCHEMA as SEP8_PARAMETRES_SCHEMA




def _db_uri():
    return (f"postgresql+psycopg2://{PG_ENV['PGUSER']}:{PG_ENV['PGPASSWORD']}"
            f"@{PG_ENV['PGHOST']}:{PG_ENV['PGPORT']}/{PG_ENV['PGDATABASE']}")


def run_epr1(engine, config):
    overrides = {
        "DB_URI": _db_uri(),
        "ANALYSIS_MODE": config.get("mode_analyse", "univariate"),
        "AGE_VARIABLE_MODE": config.get("mode_age", "categorical"),
        "SELECTED_COVARIATES": config.get("covariables", []),
    }
    return run_original_script(_epr("test1_epr.py"), overrides=overrides)


def run_epr2(engine, config):
    return run_original_script(_epr("test2_epr.py"), env_overrides=PG_ENV)


def run_epr3(engine, config):
    return run_original_script(_epr("test3_epr.py"), env_overrides=PG_ENV)


def run_epr4(engine, config):
    return run_original_script(_epr("test4_epr.py"), overrides={"DB_URI": _db_uri()})


def run_epr5(engine, config):
    return run_original_script(_epr("test5_epr.py"), overrides={"DB_URI": _db_uri()})


def run_epr6(engine, config):
    # test6_epr.py ne lit pas les variables d'env PG_* : il expose une
    # constante DB_CONFIG (dict) en dur, qu'on substitue avec les vraies
    # valeurs de connexion.
    overrides = {
        "DB_CONFIG": {
            "host": PG_ENV["PGHOST"],
            "port": int(PG_ENV["PGPORT"]),
            "dbname": PG_ENV["PGDATABASE"],
            "user": PG_ENV["PGUSER"],
            "password": PG_ENV["PGPASSWORD"],
        },
    }
    return run_original_script(_epr("test6_epr.py"), overrides=overrides)


def run_epr7(engine, config):
    # OUT_DIR est auto-redirige vers le dossier temporaire par
    # run_original_script (voir script_runner._possede_constante) ; seul
    # DB_URI doit etre substitue explicitement ici.
    return run_original_script(_epr("test7_epr.py"), overrides={"DB_URI": _db_uri()})




ANALYSES = {
    "sep_1": {"registre": "SEP", "titre": "Délai diagnostique et pronostic (EDSS)",
              "description": "Régression linéaire/logistique délai → EDSS.",
              "parametres_schema": SEP1_PARAMETRES_SCHEMA, "run": run_sep1},
    "sep_2": {"registre": "SEP", "titre": "Récupération incomplète (1er épisode) et trajectoire EDSS",
              "description": "Modèle mixte longitudinal EDSS(t), transformation du temps choisie par AIC.",
              "parametres_schema": SEP2_PARAMETRES_SCHEMA, "run": run_sep2},
    "sep_3": {"registre": "SEP", "titre": "Taux annualisé de poussées (TAP) précoce",
              "description": "TAP précoce, modèle Poisson/Binomiale Négative.",
              "parametres_schema": SEP3_PARAMETRES_SCHEMA, "run": run_sep3},
    "sep_4": {"registre": "SEP", "titre": "Charge lésionnelle T2 et sévérité future",
              "description": "Cox / régression linéaire simple ou multiple, EDSS à 2 ou 5 ans.",
              "parametres_schema": SEP4_PARAMETRES_SCHEMA, "run": run_sep4},
    "sep_5": {"registre": "SEP", "titre": "LCR (bandes oligoclonales/IgG) et évolution",
              "description": "Modèle de comptage + Cox, horizons paramétrables.",
              "parametres_schema": SEP5_PARAMETRES_SCHEMA, "run": run_sep5},
    "sep_6": {"registre": "SEP", "titre": "Consanguinité, sexe et forme évolutive",
              "description": "Tests chi²/Fisher sur antécédents et présentation clinique.",
              "parametres_schema": SEP6_PARAMETRES_SCHEMA, "run": run_sep6},
    "sep_7": {"registre": "SEP", "titre": "Lignes thérapeutiques et efficacité",
              "description": "TAP, activité IRM et délai avant échec par groupe d'efficacité (GEE, Cox, appariement PS).",
              "parametres_schema": SEP7_PARAMETRES_SCHEMA, "run": run_sep7},
    "sep_8": {"registre": "SEP", "titre": "Prédiction de sévérité (modèle validé, VIF, bootstrap)",
              "description": "Modèle de sévérité SEP avec validation croisée et calibration.",
              "parametres_schema": SEP8_PARAMETRES_SCHEMA, "run": run_sep8},

    "epr_1": {"registre": "EPR", "titre": "Étiologie/pharmacorésistance — survie",
              "description": "Kaplan-Meier / Cox, univarié ou multivarié.",
              "parametres_schema": {
                  "mode_analyse": {"type": "select", "options": ["univariate", "multivariate"], "label": "Mode"},
                  "covariables": {"type": "multiselect", "options": [
                      "etiologie_structurelle", "crises_types_multiples", "freq_crises_baseline_mois",
                      "irm_anormale", "eeg_anormal", "atcd_perinataux",
                      "developpement_psychomoteur_avant_crises", "presence_regression",
                  ], "label": "Covariables"},
              }, "run": run_epr1},
    "epr_2": {"registre": "EPR", "titre": "Étiologie et pharmacorésistance (régression)",
              "description": "Régression logistique étiologie -> pharmacorésistance.",
              "parametres_schema": {}, "run": run_epr2},
    "epr_3": {"registre": "EPR", "titre": "Type de crise ILAE 2017 et nombre d'AE essayés",
              "description": "ANOVA / comparaisons post-hoc (Tukey HSD).",
              "parametres_schema": {}, "run": run_epr3},
    "epr_4": {"registre": "EPR", "titre": "Analyse EPR #4",
              "description": "Voir docstring du script original pour le détail clinique.",
              "parametres_schema": {}, "run": run_epr4},
    "epr_5": {"registre": "EPR", "titre": "Analyse EPR #5",
              "description": "Voir docstring du script original pour le détail clinique.",
              "parametres_schema": {}, "run": run_epr5},
    "epr_6": {"registre": "EPR", "titre": "Consanguinité parentale et étiologie génétique (AR)",
              "description": "Chi² r×c consanguinité/étiologie (+ tendance Cochran-Armitage, "
                              "sensibilité GEE intra-famille).",
              "parametres_schema": {}, "run": run_epr6},
    "epr_7": {"registre": "EPR", "titre": "QI et fréquence de crises (régression)",
              "description": "Régression QI ~ fréquence de crises (+ durée d'épilepsie, VIF, "
                              "diagnostics des résidus).",
              "parametres_schema": {}, "run": run_epr7},

}