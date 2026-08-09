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
    from epr.test1_etiologie_survie import run as _run
    return _run(engine, config)


from epr.test1_etiologie_survie import PARAMETRES_SCHEMA as EPR1_PARAMETRES_SCHEMA


def run_epr2(engine, config):
    from epr.test2_etiologie_regression import run as _run
    return _run(engine, config)


from epr.test2_etiologie_regression import PARAMETRES_SCHEMA as EPR2_PARAMETRES_SCHEMA


def run_epr3(engine, config):
    from epr.test3_type_crise_anova import run as _run
    return _run(engine, config)


from epr.test3_type_crise_anova import PARAMETRES_SCHEMA as EPR3_PARAMETRES_SCHEMA


def run_epr4(engine, config):
    from epr.test4_regression_etiologie_gene import run as _run
    return _run(engine, config)


from epr.test4_regression_etiologie_gene import PARAMETRES_SCHEMA as EPR4_PARAMETRES_SCHEMA


def run_epr5(engine, config):
    from epr.test5_genotype_phenotype import run as _run
    return _run(engine, config)


from epr.test5_genotype_phenotype import PARAMETRES_SCHEMA as EPR5_PARAMETRES_SCHEMA


def run_epr6(engine, config):
    from epr.test6_consanguinite_etiologie import run as _run
    return _run(engine, config)


from epr.test6_consanguinite_etiologie import PARAMETRES_SCHEMA as EPR6_PARAMETRES_SCHEMA


def run_epr7(engine, config):
    from epr.test7_qi_frequence_crises import run as _run
    return _run(engine, config)


from epr.test7_qi_frequence_crises import PARAMETRES_SCHEMA as EPR7_PARAMETRES_SCHEMA




ANALYSES = {
    "sep_1": {"registre": "SEP", "titre": "Délai diagnostique et pronostic à l'EDSS",
              "description": "Estime dans quelle mesure un diagnostic plus précoce est associé à un "
                              "meilleur score EDSS à un horizon donné (régression linéaire ou logistique).",
              "parametres_schema": SEP1_PARAMETRES_SCHEMA, "run": run_sep1},
    "sep_2": {"registre": "SEP", "titre": "Récupération après le premier épisode et trajectoire EDSS",
              "description": "Modélise l'évolution du score EDSS dans le temps après le premier épisode "
                              "pour repérer les patients à récupération incomplète (modèle mixte longitudinal).",
              "parametres_schema": SEP2_PARAMETRES_SCHEMA, "run": run_sep2},
    "sep_3": {"registre": "SEP", "titre": "Taux annualisé de poussées (TAP) précoce",
              "description": "Quantifie la fréquence des poussées dans la période précoce suivant le "
                              "diagnostic et identifie les facteurs associés à un TAP élevé (Poisson / binomiale négative).",
              "parametres_schema": SEP3_PARAMETRES_SCHEMA, "run": run_sep3},
    "sep_4": {"registre": "SEP", "titre": "Charge lésionnelle T2 à l'IRM et sévérité future",
              "description": "Évalue le lien entre le volume de lésions T2 précoce et le risque d'aggravation "
                              "clinique (EDSS) à 2 ou 5 ans (régression ou modèle de survie de Cox).",
              "parametres_schema": SEP4_PARAMETRES_SCHEMA, "run": run_sep4},
    "sep_5": {"registre": "SEP", "titre": "Marqueurs du LCR (bandes oligoclonales, index IgG) et évolution",
              "description": "Étudie si la présence de bandes oligoclonales ou un index IgG élevé au "
                              "diagnostic prédit l'activité ultérieure de la maladie (modèle de comptage + survie).",
              "parametres_schema": SEP5_PARAMETRES_SCHEMA, "run": run_sep5},
    "sep_6": {"registre": "SEP", "titre": "Consanguinité, sexe et forme évolutive",
              "description": "Recherche une association entre antécédents de consanguinité, sexe et "
                              "forme clinique de présentation (tests du chi² / exact de Fisher).",
              "parametres_schema": SEP6_PARAMETRES_SCHEMA, "run": run_sep6},
    "sep_7": {"registre": "SEP", "titre": "Lignes thérapeutiques et efficacité du traitement",
              "description": "Compare le TAP, l'activité IRM et le délai avant échec thérapeutique selon "
                              "la ligne de traitement reçue (GEE, modèle de Cox, appariement par score de propension).",
              "parametres_schema": SEP7_PARAMETRES_SCHEMA, "run": run_sep7},
    "sep_8": {"registre": "SEP", "titre": "Modèle prédictif de sévérité (validation et calibration)",
              "description": "Modèle multivarié de prédiction de la sévérité, avec contrôle de la "
                              "colinéarité (VIF), validation croisée et estimation de l'incertitude par bootstrap.",
              "parametres_schema": SEP8_PARAMETRES_SCHEMA, "run": run_sep8},

    "epr_1": {"registre": "EPR", "titre": "Étiologie et délai avant pharmacorésistance",
              "description": "Compare, selon l'étiologie de l'épilepsie, le délai de survenue de la "
                              "pharmacorésistance (courbes de Kaplan-Meier, modèle de Cox univarié ou multivarié).",
              "parametres_schema": EPR1_PARAMETRES_SCHEMA, "run": run_epr1},
    "epr_2": {"registre": "EPR", "titre": "Étiologie comme facteur prédictif de pharmacorésistance",
              "description": "Estime, par régression logistique, la probabilité de pharmacorésistance "
                              "associée à chaque catégorie étiologique.",
              "parametres_schema": EPR2_PARAMETRES_SCHEMA, "run": run_epr2},
    "epr_3": {"registre": "EPR", "titre": "Type de crise (ILAE 2017) et nombre d'antiépileptiques essayés",
              "description": "Compare le nombre de traitements antiépileptiques essayés selon le type de "
                              "crise, classifié selon ILAE 2017 (ANOVA avec comparaisons post-hoc de Tukey).",
              "parametres_schema": EPR3_PARAMETRES_SCHEMA, "run": run_epr3},
    "epr_4": {"registre": "EPR", "titre": "Régression développementale selon l'étiologie et le gène impliqué",
          "description": "Recherche une association entre la survenue d'une régression développementale "
                          "et la catégorie étiologique ou le gène causal identifié (tests du chi², odds ratio).",
          "parametres_schema": {}, "run": run_epr4},
    "epr_5": {"registre": "EPR", "titre": "Corrélation génotype-phénotype",
            "description": "Étudie le lien entre le gène ou la classe de variant identifié (classification "
                            "ACMG) et le phénotype clinique observé, par famille fonctionnelle de gène.",
            "parametres_schema": {}, "run": run_epr5},

    "epr_6": {"registre": "EPR", "titre": "Consanguinité parentale et étiologie génétique",
              "description": "Recherche un gradient entre le degré de consanguinité parentale et la "
                              "probabilité d'une étiologie génétique à transmission autosomique récessive "
                              "(chi² r×c, tendance de Cochran-Armitage, sensibilité par GEE intrafamiliale).",
              "parametres_schema": {}, "run": run_epr6},
    "epr_7": {"registre": "EPR", "titre": "Quotient intellectuel et fréquence des crises",
              "description": "Modélise la relation entre le quotient intellectuel et la fréquence des "
                              "crises, ajustée sur la durée d'évolution de l'épilepsie (régression, VIF, diagnostics des résidus).",
              "parametres_schema": {}, "run": run_epr7},

}