import sys

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