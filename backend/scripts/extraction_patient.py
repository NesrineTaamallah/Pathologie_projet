import json
import re
import threading

from llama_cpp import Llama


print(f"### FICHIER CHARGE DEPUIS : {__file__}", flush=True)


MODEL_PATH = r"C:\hf-cache\qwen3-8b-gguf\Qwen3-8B-Q4_K_M.gguf"

CHAMPS = [
    "numero_dossier", "nom_prenom", "date_naissance", "adresse", "origine",
    "telephone", "cin", "num_cnam", "nom_prenom_pere", "nom_prenom_mere",
    "frere", "soeur", "autre_antecedent",
]

_extractor_model = None
_extractor_tokenizer = None
_model_load_error = None
_load_lock = threading.Lock()

SYSTEM_PROMPT = (
    "Tu es un système d'extraction d'informations d'identification patient "
    "à partir de documents médicaux pédiatriques tunisiens (texte OCR ou "
    "transcription audio, potentiellement bruité). "
    "Réponds UNIQUEMENT avec un objet JSON contenant exactement ces clés : "
    f"{', '.join(CHAMPS)}. "
    "Utilise une chaîne vide \"\" pour tout champ absent du texte. "
    "N'invente jamais de valeur. Pas de texte hors JSON, pas de balises markdown."
)


def _charger_modele():
    global _extractor_model, _extractor_tokenizer, _model_load_error

    if _extractor_model is not None or _model_load_error is not None:
        return

    with _load_lock:
        if _extractor_model is not None or _model_load_error is not None:
            return
        try:
            model = Llama(
                model_path=MODEL_PATH,
                n_ctx=8192,
                n_gpu_layers=-1,
                verbose=False,
            )
            _extractor_tokenizer = None  # non utilisé avec llama.cpp (chat template intégré)
            _extractor_model = model
        except Exception as exc:

            _model_load_error = str(exc)
            raise


def _extraire_json(texte_genere: str) -> dict:
    match = re.search(r"\{.*\}", texte_genere, re.DOTALL)
    if not match:
        raise ValueError("Aucun JSON trouvé dans la sortie du modèle.")
    return json.loads(match.group(0))


def _normaliser(resultat: dict) -> dict:
    return {champ: str(resultat.get(champ, "") or "").strip() for champ in CHAMPS}


def extraire_donnees_patient(texte: str, chunking: bool = True, verbose: bool = False) -> dict:

    _charger_modele()

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": texte},
    ]

    sortie = _extractor_model.create_chat_completion(
        messages=messages,
        max_tokens=512,
        temperature=0,
    )

    texte_genere = sortie["choices"][0]["message"]["content"]
    if verbose:
        print(f"[extraction_patient] sortie brute du modèle :\n{texte_genere}")

    try:
        resultat = _extraire_json(texte_genere)
    except (ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Sortie du modèle non parsable en JSON : {exc}") from exc

    return _normaliser(resultat)