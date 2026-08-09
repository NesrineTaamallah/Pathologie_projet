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


def _possede_constante(source: str, nom: str) -> bool:
    """Vrai si `nom` est assigne au niveau module dans `source` (ex: OUT_DIR = "...").
    Sert a auto-injecter le dossier de sortie temporaire pour les scripts qui
    exposent une constante OUT_DIR/OUTPUT_DIR mais ne lisent pas os.environ
    (cas de test7_epr.py, qui ecrit en dur OUT_DIR = "/mnt/user-data/outputs")."""
    arbre = ast.parse(source)
    return any(
        isinstance(n, ast.Assign) and len(n.targets) == 1
        and isinstance(n.targets[0], ast.Name) and n.targets[0].id == nom
        for n in arbre.body
    )


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
    overrides = dict(overrides or {})
    reponses_stdin = reponses_stdin or []
    env_overrides = env_overrides or {}

    with open(chemin_script, encoding="utf-8") as f:
        source = f.read()

    dossier_sortie = tempfile.mkdtemp(prefix="analyse_")

    # Auto-injection : certains scripts (ex. test7_epr.py) exposent une
    # constante OUT_DIR/OUTPUT_DIR en dur au lieu de lire os.environ. On la
    # redirige vers le dossier temporaire si l'appelant ne l'a pas deja fixee
    # explicitement, pour que les figures/CSV generes soient bien recuperes
    # ci-dessous au lieu d'atterrir dans /mnt/user-data/outputs partage par
    # toutes les requetes.
    for nom_const in ("OUT_DIR", "OUTPUT_DIR"):
        if nom_const not in overrides and _possede_constante(source, nom_const):
            overrides[nom_const] = dossier_sortie

    source = _substituer_constantes(source, overrides)

    env_sauvegarde = dict(os.environ)
    os.environ.update(env_overrides)
    os.environ.setdefault("OUTPUT_DIR", dossier_sortie)
    os.environ.setdefault("SEP_OUTPUT_DIR", dossier_sortie)

    # Certains scripts (ex. test6_epr.py) ecrivent leurs fichiers de sortie
    # avec des chemins RELATIFS (ex. "rapport_analyse_chi2.txt", sans
    # constante OUT_DIR a substituer). On se place dans le dossier temporaire
    # le temps de l'execution pour que ces fichiers y atterrissent aussi et
    # soient recuperes -- sans avoir a toucher au script original.
    # NB : os.chdir() est global au processus ; si le serveur traite des
    # requetes EPR en parallele (plusieurs workers/threads), ce chdir partage
    # peut interferer entre requetes concurrentes. Sans incidence pour un
    # usage sequentiel (dev, un seul utilisateur a la fois).
    cwd_sauvegarde = os.getcwd()
    os.chdir(dossier_sortie)

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
        os.chdir(cwd_sauvegarde)
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