import sys
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

try:
    import extraction_patient as ep
except Exception as exc:  
    
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


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    """Schéma minimal, compatible OpenAI, pour que
    entities_extraction_service.py (port 8004) puisse appeler CE serveur
    (port 8003) sans jamais recharger le modèle : on réutilise l'objet
    Llama() unique déjà chargé en mémoire par extraction_patient.py."""

    model: Optional[str] = None
    messages: List[ChatMessage]
    max_tokens: int = 768
    temperature: float = 0.0
    response_format: Optional[Dict[str, Any]] = None
    # chat_template_kwargs est envoyé à plat par entities_extraction_service.py
    # (voir _appliquer_chat_template_sans_reflexion) plutôt que sous extra_body.
    chat_template_kwargs: Optional[Dict[str, Any]] = None
    extra_body: Optional[Dict[str, Any]] = None


@app.post("/v1/chat/completions")
def chat_completions(req: ChatCompletionRequest):
    """Point d'entrée manquant : entities_extraction_service.py tape ici
    en boucle (localhost:8003/v1/chat/completions) pour éviter de charger
    un second modèle. Sans cette route, chaque appel échouait."""
    if ep is None:
        raise HTTPException(
            status_code=503,
            detail=f"Module extraction_patient indisponible : {_import_error}",
        )

    json_schema = None
    if req.response_format and req.response_format.get("type") == "json_object":
        json_schema = req.response_format.get("schema")

    # enable_thinking peut arriver soit à plat (chat_template_kwargs),
    # soit imbriqué dans extra_body — on gère les deux.
    enable_thinking = True
    ctk = req.chat_template_kwargs
    if ctk is None and req.extra_body:
        ctk = req.extra_body.get("chat_template_kwargs")
    if ctk is not None and "enable_thinking" in ctk:
        enable_thinking = bool(ctk["enable_thinking"])

    try:
        resultat = ep.chat_completion_openai_like(
            messages=[m.model_dump() for m in req.messages],
            max_tokens=req.max_tokens,
            temperature=req.temperature,
            json_schema=json_schema,
            enable_thinking=enable_thinking,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erreur pendant l'appel au modèle : {exc}")
    return resultat


@app.get("/health")
def health():
    if ep is None:
        return {"status": "error", "model_loaded": False, "load_error": _import_error}
    return {
        "status": "ok",
        "model_loaded": ep._extractor_model is not None,
        "load_error": ep._model_load_error,
    }


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