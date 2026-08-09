
import contextlib
import io
import os
import ast
import tempfile
import builtins
import matplotlib
matplotlib.use("Agg")


class ReponsesEpuisees(Exception):
    pass


def _input_depuis_file(reponses: list[str]):
    it = iter(reponses)

    def fake_input(prompt=""):
        try:
            return next(it)
        except StopIteration:
            raise ReponsesEpuisees(
                f"Le script attend une réponse supplémentaire (prompt: {prompt!r}) "
                "mais aucune n'a été fournie par le formulaire."
            )
    return fake_input


def _substituer_constantes(source: str, overrides: dict) -> str:
    
    for nom, valeur in overrides.items():
        arbre = ast.parse(source)
        cible = next(
            (n for n in arbre.body
             if isinstance(n, ast.Assign) and len(n.targets) == 1
             and isinstance(n.targets[0], ast.Name) and n.targets[0].id == nom),
            None,
        )
        if cible is None:
            raise ValueError(f"Constante '{nom}' introuvable dans le script — vérifier registry.py")
        lignes = source.splitlines(keepends=True)
        debut, fin = cible.lineno, cible.end_lineno  # 1-indexé, inclusif
        lignes[debut - 1:fin] = [f"{nom} = {valeur!r}\n"]
        source = "".join(lignes)
    return source


def run_original_script(
    chemin_script: str,
    overrides: dict | None = None,
    reponses_stdin: list[str] | None = None,
    env_overrides: dict | None = None,
) -> dict:
    """
    chemin_script   : chemin vers le .py ORIGINAL, jamais copié ni modifié.
    overrides       : {"NOM_CONSTANTE": valeur} -> injecté en mémoire avant exécution.
    reponses_stdin  : réponses aux éventuels input(), dans l'ordre où ils apparaissent.
    env_overrides   : variables d'environnement (ex: PGHOST, OUTPUT_DIR...).
    """
    overrides = overrides or {}
    reponses_stdin = reponses_stdin or []
    env_overrides = env_overrides or {}

    with open(chemin_script, encoding="utf-8") as f:
        source = f.read()
    source = _substituer_constantes(source, overrides)

    dossier_sortie = tempfile.mkdtemp(prefix="analyse_")
    env_sauvegarde = dict(os.environ)
    os.environ.update(env_overrides)
    os.environ.setdefault("OUTPUT_DIR", dossier_sortie)
    os.environ.setdefault("SEP_OUTPUT_DIR", dossier_sortie)

    stdout_capture = io.StringIO()
    input_original = builtins.input
    builtins.input = _input_depuis_file(reponses_stdin)

    try:
        namespace = {"__name__": "__main__", "__file__": chemin_script}
        with contextlib.redirect_stdout(stdout_capture):
            code = compile(source, chemin_script, "exec")
            exec(code, namespace)
    finally:
        builtins.input = input_original
        os.environ.clear()
        os.environ.update(env_sauvegarde)

    figures = []
    for nom_fichier in sorted(os.listdir(dossier_sortie)):
        if nom_fichier.lower().endswith(".png"):
            import base64
            with open(os.path.join(dossier_sortie, nom_fichier), "rb") as img:
                figures.append(f"data:image/png;base64,{base64.b64encode(img.read()).decode()}")

    return {
        "notes": stdout_capture.getvalue().splitlines(),
        "figures": figures,
        "tableau": None,
        "resume_stats": None,
    }