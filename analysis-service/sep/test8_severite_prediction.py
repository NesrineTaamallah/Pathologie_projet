
import base64
import io
import importlib.util
import logging
import os
import tempfile

from common import Notes

_CHEMIN_SCRIPT_ORIGINAL = os.path.join(
    os.path.dirname(__file__), "..", "..", "test_analyse_statistique", "SEP", "test8_sep.py"
)

EFFECTIF_MINIMUM = 10

PARAMETRES_SCHEMA = {
    "tap_window_months": {
        "type": "number", "default": 12,
        "label": "Fenêtre TAP précoce (mois après le diagnostic)",
    },
}
# Les seuils clinicien/objectif (seuil_bas_clinicien, seuil_haut_clinicien,
# seuil_bas_objectif, seuil_haut_objectif) restent gérés par `run()` via
# `_seuil()`, qui retourne None quand la clé est absente de `config` --
# le calcul automatique par terciles s'applique donc toujours. On les a
# simplement retirés du formulaire affiché pour ne garder que la variable
# demandée (fenêtre TAP précoce).


def _charger_module_original(dossier_sortie: str):
    # OUTPUT_DIR est lu par le script au niveau module (création du dossier +
    # handler de log fichier) : on le fixe AVANT l'exécution du module pour
    # que tous les fichiers (figures, csv, log) atterrissent dans notre
    # dossier temporaire au lieu de /mnt/user-data/outputs.
    env_sauvegarde = os.environ.get("OUTPUT_DIR")
    os.environ["OUTPUT_DIR"] = dossier_sortie
    try:
        spec = importlib.util.spec_from_file_location("test8_sep_original", _CHEMIN_SCRIPT_ORIGINAL)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        if env_sauvegarde is None:
            os.environ.pop("OUTPUT_DIR", None)
        else:
            os.environ["OUTPUT_DIR"] = env_sauvegarde
    return module


def _seuil(config: dict, cle: str):
    """Convertit un champ de seuil du formulaire (nombre ou vide) vers float|None.
    Un champ laissé vide (None, '', ou absent) rend au script son comportement
    par défaut (seuils auto-calculés par terciles)."""
    valeur = config.get(cle)
    if valeur is None or valeur == "":
        return None
    return float(valeur)


def run(engine, config: dict) -> dict:
    """Point d'entrée appelé par l'API. `config` = corps JSON envoyé par React."""
    dossier_sortie = tempfile.mkdtemp(prefix="sep8_")
    m = _charger_module_original(dossier_sortie)

    notes = Notes()
    journal = io.StringIO()
    handler = logging.StreamHandler(journal)
    handler.setFormatter(logging.Formatter("%(message)s"))
    m.logger.addHandler(handler)
    m.logger.setLevel(logging.INFO)

    try:
        tap_window_months = int(float(config.get("tap_window_months", m.TAP_WINDOW_MONTHS_DEFAULT)))

        seuil_bas_clin = _seuil(config, "seuil_bas_clinicien")
        seuil_haut_clin = _seuil(config, "seuil_haut_clinicien")
        seuil_bas_obj = _seuil(config, "seuil_bas_objectif")
        seuil_haut_obj = _seuil(config, "seuil_haut_objectif")

        for nom_bas, nom_haut, bas, haut in [
            ("seuil_bas_clinicien", "seuil_haut_clinicien", seuil_bas_clin, seuil_haut_clin),
            ("seuil_bas_objectif", "seuil_haut_objectif", seuil_bas_obj, seuil_haut_obj),
        ]:
            if bas is not None and haut is not None and bas >= haut:
                raise ValueError(
                    f"Seuils invalides ({nom_bas}={bas} >= {nom_haut}={haut}) : le seuil bas doit "
                    "être strictement inférieur au seuil haut. Laissez les deux champs vides pour "
                    "revenir au calcul automatique (terciles)."
                )
            if (bas is None) != (haut is None):
                raise ValueError(
                    f"Renseignez {nom_bas} ET {nom_haut} ensemble, ou laissez les deux vides pour "
                    "un calcul automatique — un seul seuil forcé n'est pas exploitable."
                )

        origine_donnee_disponible = m.check_origine_donnee_disponible(engine)
        df_raw = m.extract_data(
            engine, tap_window_months=tap_window_months,
            origine_donnee_disponible=origine_donnee_disponible,
        )
        if df_raw.empty:
            raise ValueError(
                "Aucun patient SEP exploitable : la requête d'extraction n'a retourné aucune "
                "ligne (vérifier sep_identification_clinique.date_diagnostic)."
            )

        n_patients_simules = (
            int(df_raw["origine_donnee"].eq("simule").sum())
            if origine_donnee_disponible and "origine_donnee" in df_raw.columns else 0
        )

        df_model, predictors = m.prepare_data(df_raw, tap_window_months=tap_window_months)

        if len(df_model) < EFFECTIF_MINIMUM:
            raise ValueError(
                f"Effectif insuffisant pour ce modèle (n={len(df_model)} patients avec données "
                f"complètes sur les {len(predictors)} prédicteurs requis "
                f"[{', '.join(predictors)}] et la sévérité ; minimum {EFFECTIF_MINIMUM} recommandé). "
                "Complétez le TAP précoce, l'IRM initiale (nombre de lésions T2, atteinte "
                "médullaire) ou la sévérité déclarée pour plus de patients, ou élargissez la "
                "fenêtre TAP précoce."
            )

        concordance = m.analyze_concordance(df_model)

        res_clin = m.run_full_analysis(
            df_model, predictors, "y_clinicien",
            "Y_CLINICIEN (PRINCIPAL — sévérité déclarée)",
            tap_window_months, seuil_bas_clin, seuil_haut_clin, "y_clinicien",
        )
        res_obj = m.run_full_analysis(
            df_model, predictors, "y_objectif",
            "Y_OBJECTIF (SECONDAIRE — définition objective post-TAP)",
            tap_window_months, seuil_bas_obj, seuil_haut_obj, "y_objectif",
        )

        rapport = m.generate_rapport_clinicien(
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
            output_path=os.path.join(dossier_sortie, "rapport_clinicien.txt"),
            fit_method_clin=res_clin["fit_method"], fit_method_obj=res_obj["fit_method"],
            bt_echecs_clin=res_clin["bt_echecs"], bt_echecs_obj=res_obj["bt_echecs"],
            n_patients_simules=n_patients_simules,
            origine_donnee_disponible=origine_donnee_disponible,
        )

        for ligne in rapport.splitlines():
            notes(ligne)

        lignes_journal = journal.getvalue().splitlines()
        if lignes_journal:
            notes("")
            notes("=" * 78)
            notes("JOURNAL D'EXÉCUTION DÉTAILLÉ (VIF, Box-Tidwell, validation croisée, "
                  "bootstrap, calibration)")
            notes("=" * 78)
            for ligne in lignes_journal:
                notes(ligne)

        figures = []
        for nom_fichier in sorted(os.listdir(dossier_sortie)):
            if nom_fichier.lower().endswith(".png"):
                with open(os.path.join(dossier_sortie, nom_fichier), "rb") as img:
                    figures.append(f"data:image/png;base64,{base64.b64encode(img.read()).decode()}")

        risk_clin = res_clin["risk_table"].reset_index()
        risk_clin.insert(0, "population", "Y_clinicien (principal)")
        risk_obj = res_obj["risk_table"].reset_index()
        risk_obj.insert(0, "population", "Y_objectif (secondaire)")
        import pandas as pd
        tableau = pd.concat([risk_clin, risk_obj], ignore_index=True).to_dict(orient="records")

        resume_stats = {
            "n_patients": int(len(df_model)),
            "n_evenements_clinicien": int(df_model["y_clinicien"].sum()),
            "n_evenements_objectif": int(df_model["y_objectif"].sum()),
            "kappa_concordance": round(float(concordance["kappa"]), 3),
            "methode_ajustement_clinicien": res_clin["fit_method"],
            "methode_ajustement_objectif": res_obj["fit_method"],
            "auc_oof_clinicien": round(float(res_clin["bootstrap"]["auc"]), 3),
            "auc_oof_objectif": round(float(res_obj["bootstrap"]["auc"]), 3),
            "fenetre_tap_mois": tap_window_months,
            "n_patients_simules": n_patients_simules,
            "seuils_risque_clinicien": f"{res_clin['seuils'][0]} / {res_clin['seuils'][1]}"
                                       f"{' (forcés)' if seuil_bas_clin is not None else ' (auto)'}",
            "seuils_risque_objectif": f"{res_obj['seuils'][0]} / {res_obj['seuils'][1]}"
                                      f"{' (forcés)' if seuil_bas_obj is not None else ' (auto)'}",
        }

        return {"notes": notes.lines, "figures": figures, "tableau": tableau, "resume_stats": resume_stats}
    finally:
        m.logger.removeHandler(handler)
