
import base64
import importlib.util
import os
import tempfile

from common import Notes

_CHEMIN_SCRIPT_ORIGINAL = os.path.join(
    os.path.dirname(__file__), "..", "..", "test_analyse_statistique", "SEP", "test6_sep.py"
)

EFFECTIF_MINIMUM = 10


def _charger_module_original():
    spec = importlib.util.spec_from_file_location("test6_sep_original", _CHEMIN_SCRIPT_ORIGINAL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PARAMETRES_SCHEMA = {}


def _figure_base64_depuis_fichier(chemin: str) -> str:
    with open(chemin, "rb") as img:
        return f"data:image/png;base64,{base64.b64encode(img.read()).decode()}"


def run(engine, config: dict) -> dict:
    """Point d'entrée appelé par l'API. `config` non utilisé (aucun paramètre pour ce test)."""
    m = _charger_module_original()
    notes = Notes()

    df, stats_exclusion = m.charger_donnees_db(engine)

    notes("=" * 70)
    notes("REGISTRE SEP PÉDIATRIQUE — CONSANGUINITÉ ET PRÉSENTATION CLINIQUE")
    notes("=" * 70)
    notes(f"Effectif brut extrait : n = {stats_exclusion['n_total_avant_filtrage']}")
    notes("Exclusions (convention dictionnaire : NULL = non renseigné, "
          "'NA' = non applicable — jamais assimilés à 'Non') :")
    notes(f"  - consanguinite_parentale : "
          f"{stats_exclusion['consanguinite_parentale_NULL_non_renseigne']} NULL, "
          f"{stats_exclusion['consanguinite_parentale_NA_non_applicable']} 'NA'")
    notes(f"  - age_premier_symptome_mois : "
          f"{stats_exclusion['age_premier_symptome_mois_NULL_non_renseigne']} NULL")
    notes(f"  - sexe : {stats_exclusion['sexe_NULL_non_renseigne']} NULL")
    notes(f"  - forme_evolutive : "
          f"{stats_exclusion['forme_evolutive_NULL_non_renseigne']} NULL, "
          f"{stats_exclusion['forme_evolutive_NA_non_applicable']} 'NA'")
    notes(f"Effectif analysé (après filtrage NULL/NA) : n = "
          f"{stats_exclusion['n_analysable_apres_filtrage']}")
    notes("")

    if len(df) < EFFECTIF_MINIMUM:
        raise ValueError(
            f"Effectif insuffisant pour ce test (n={len(df)} patients avec données complètes "
            f"sur consanguinité, âge au premier symptôme, sexe et forme évolutive ; minimum "
            f"{EFFECTIF_MINIMUM} recommandé pour un Chi²/une régression logistique interprétable). "
            "Complétez ces champs dans les dossiers patients pour augmenter l'effectif exploitable."
        )

    notes("--- ÉTAPE 1 : Chi² / Fisher (association brute) ---")
    tableau = []
    for res in [m.chi2_consanguinite_forme(df), m.chi2_consanguinite_age(df)]:
        notes(f"\n{res['titre']}")
        notes(f"Méthode appliquée : {res['methode']}")
        notes(f"p-value = {res['p_value']:.4f}  "
              f"({'significatif' if res['significatif'] else 'non significatif'} à alpha={m.ALPHA})")
        if res["avertissement"]:
            notes(res["avertissement"])
        tableau.append({
            "test": res["titre"],
            "methode": res["methode"],
            "p_value": round(float(res["p_value"]), 4),
            "significatif": "Oui" if res["significatif"] else "Non",
        })

    notes("\n--- ÉTAPE 2 : Régression logistique (association ajustée) ---")

    figures = []
    modele1_calculable = False
    modele2_calculable = False

    try:
        res_bin = m.regression_logistique_age(df)
        notes(f"\nModèle 1 — {res_bin.attrs['titre']}")
        notes(res_bin.round(4).to_string(index=False))

        fd, chemin_fig1 = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            m.graphique_forest_modele1(res_bin, chemin_fig1)
            figures.append(_figure_base64_depuis_fichier(chemin_fig1))
        finally:
            os.remove(chemin_fig1)
        modele1_calculable = True
    except Exception as e:
        notes(f"\nModèle 1 non calculable avec les données fournies : {e}")

    try:
        res_multi = m.regression_logistique_multinomiale(df)
        notes("\nModèle 2 — Forme évolutive (réf. RR) ~ Consanguinité + Âge de début + Sexe")
        notes(res_multi.round(4).to_string(index=False))

        fd, chemin_fig2 = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            m.graphique_forest_modele2(res_multi, chemin_fig2)
            figures.append(_figure_base64_depuis_fichier(chemin_fig2))
        finally:
            os.remove(chemin_fig2)
        modele2_calculable = True
    except Exception as e:
        notes(f"\nModèle 2 non calculable avec les données fournies : {e}")

    notes("\n" + "=" * 70)
    notes("Interprétation : OR > 1 = association positive avec la consanguinité ;")
    notes("OR < 1 = association négative ; significatif si p < 0.05 et IC95%")
    notes("n'incluant pas 1.")
    notes("=" * 70)

    resume_stats = {
        "n_total_extrait": int(stats_exclusion["n_total_avant_filtrage"]),
        "n_exclus": int(stats_exclusion["n_exclu_total"]),
        "n_patients_analyses": int(len(df)),
        "modele1_age_precoce_calculable": modele1_calculable,
        "modele2_forme_evolutive_calculable": modele2_calculable,
    }

    return {"notes": notes.lines, "figures": figures, "tableau": tableau, "resume_stats": resume_stats}
