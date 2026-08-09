import sys
import time
import uuid

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

try:
    import extraction_patient as ep
except Exception as exc:  # ModuleNotFoundError, ImportError CUDA/torch, etc.
    
    print(f"[extraction_service] ERREUR au chargement de extraction_patient : {exc}", file=sys.stderr)
    ep = None
    _import_error = str(exc)
else:
    _import_error = None

app = FastAPI(title="Service d'extraction — étape 1 (données non médicales)")


class ExtractionRequest(BaseModel):
    texte: str = Field(..., description="Texte transcrit (OCR ou ASR) à analyser.")
    chunking: bool = Field(True, description="Active le chunking + résolution de coréférence pour les textes longs.")
    verbose: bool = Field(False, description="Journalise le raisonnement du LLM (debug uniquement).")


class ExtractionResponse(BaseModel):
    nom_prenom: str = ""
    date_naissance: str = ""
    adresse: str = ""
    origine: str = ""
    telephone: str = ""
    cin: str = ""
    num_cnam: str = ""
    nom_prenom_pere: str = ""
    nom_prenom_mere: str = ""
    frere: str = ""
    soeur: str = ""
    autre_antecedent: str = ""


@app.get("/health")
def health():
    if ep is None:
        return {"status": "error", "model_loaded": False, "load_error": _import_error}
    return {
        "status": "ok",
        "model_loaded": ep._extractor_model is not None,
        "load_error": ep._model_load_error,
    }


# ---------------------------------------------------------------------------
# Endpoint OpenAI-compatible /v1/chat/completions — REUTILISE le meme modele
# deja charge en memoire par extraction_patient.py (aucun second chargement
# du gguf, aucun second process). Permet a entities_extraction_service.py
# (pipeline SEP/EPR) de taper sur ce meme serveur/port au lieu d'en demarrer
# un autre.
# ---------------------------------------------------------------------------

class ChatMessageIn(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str | None = None
    messages: list[ChatMessageIn]
    max_tokens: int = 768
    temperature: float = 0.0
    guided_json: dict | None = None          # compat vLLM (ignore ici, voir response_format)
    response_format: dict | None = None       # {"type": "json_object", "schema": {...}}
    chat_template_kwargs: dict | None = None  # {"enable_thinking": false}


@app.post("/v1/chat/completions")
def chat_completions(req: ChatCompletionRequest):
    if ep is None:
        raise HTTPException(
            status_code=503,
            detail=f"Module extraction_patient indisponible : {_import_error}",
        )

    json_schema = None
    if req.response_format and req.response_format.get("schema"):
        json_schema = req.response_format["schema"]
    elif req.guided_json:
        json_schema = req.guided_json

    enable_thinking = True
    if req.chat_template_kwargs and "enable_thinking" in req.chat_template_kwargs:
        enable_thinking = bool(req.chat_template_kwargs["enable_thinking"])

    try:
        sortie = ep.chat_completion_openai_like(
            messages=[m.model_dump() for m in req.messages],
            max_tokens=req.max_tokens,
            temperature=req.temperature,
            json_schema=json_schema,
            enable_thinking=enable_thinking,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erreur pendant la génération : {exc}")

    # llama-cpp-python renvoie deja une reponse au format OpenAI
    # (choices[0].message.content) -> on la relaie telle quelle, avec un
    # id/objet coherents pour les clients qui les inspectent.
    sortie.setdefault("id", f"chatcmpl-{uuid.uuid4().hex}")
    sortie.setdefault("object", "chat.completion")
    sortie.setdefault("created", int(time.time()))
    return sortie


@app.post("/extraire/patient", response_model=ExtractionResponse)
def extraire_patient(req: ExtractionRequest):
    if ep is None:
        
        raise HTTPException(
            status_code=503,
            detail=f"Module extraction_patient indisponible : {_import_error}",
        )

    if not req.texte or not req.texte.strip():
        raise HTTPException(status_code=400, detail="Le champ 'texte' est requis et ne peut pas être vide.")

    try:
        resultat = ep.extraire_donnees_patient(req.texte, chunking=req.chunking, verbose=req.verbose)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erreur pendant l'extraction : {exc}")
    return resultat