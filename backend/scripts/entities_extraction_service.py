

import json
import time
import gc
from pathlib import Path

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from jinja2 import Environment

app = FastAPI(title="Entités médicales — service d'extraction")


QWEN_API_URL = "http://localhost:8003/v1/chat/completions"
QWEN_MODEL_NAME = "Qwen/Qwen3-8B"   # doit correspondre exactement au nom déclaré côté serveur vLLM/TGI
QWEN_TIMEOUT_S = 120
QWEN_MAX_RETRIES = 3

MAX_NEW_TOKENS = 768
MAX_CONTEXTE_TABLE_CHARS = 8000
RETRIEVAL_KEEP_HEAD = 2
RETRIEVAL_KEEP_TAIL = 2
RETRIEVAL_MIN_MATCHES = 2
N_VOTES_REPEATED = 3
VOTE_DEDUP_THRESHOLD = 80
VOTE_TABLES_EPR = {"epr_eeg", "epr_imagerie", "epr_genetique", "epr_type_crise", "epr_frequence_crises"}
VOTE_TABLES_SEP = {"sep_irm", "sep_biologie_lcr"}
RECENCE_FRACTION = 1 / 3
RECENCE_MIN_CHUNKS = 2
VERIFICATION_TABLES_EPR = {"epr_eeg", "epr_imagerie", "epr_frequence_crises", "epr_type_crise", "epr_genetique"}
VERIFICATION_TABLES_SEP = set()



def _appliquer_chat_template_sans_reflexion(prompt_texte: str) -> dict:
    
    return {
        "messages": [{"role": "user", "content": prompt_texte}],
        "chat_template_kwargs": {"enable_thinking": False},
    }


def _appel_qwen_api(prompt_texte: str, json_schema: dict, max_tokens: int, temperature: float) -> dict:
    payload = {
        "model": QWEN_MODEL_NAME,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "guided_json": json_schema,       # vLLM (guided decoding)
        **_appliquer_chat_template_sans_reflexion(prompt_texte),
    }
    resp = requests.post(QWEN_API_URL, json=payload, timeout=QWEN_TIMEOUT_S)
    resp.raise_for_status()
    data = resp.json()
    contenu = data["choices"][0]["message"]["content"]
    return json.loads(contenu)


def llm_extract(prompt, json_schema, max_tokens=MAX_NEW_TOKENS, repetee=False, sampler=None):
    
    if sampler == "greedy":
        temperature = 0.0
    elif sampler == "multinomial":
        temperature = 0.3
    else:
        temperature = 0.3 if repetee else 0.0

    derniere_erreur = None
    for tentative in range(QWEN_MAX_RETRIES):
        try:
            return _appel_qwen_api(prompt, json_schema, max_tokens, temperature)
        except (requests.RequestException, json.JSONDecodeError, KeyError, IndexError) as exc:
            derniere_erreur = exc
            if tentative < QWEN_MAX_RETRIES - 1:
                time.sleep(1.5 * (tentative + 1))
    raise RuntimeError(f"Échec de l'appel au serveur Qwen (localhost:8003) après {QWEN_MAX_RETRIES} tentatives : {derniere_erreur}")



class ChunkIn(BaseModel):
    texte: str
    date: str | None = None       


class ExtractionRequest(BaseModel):
    registre: str                 
    chunks: list[ChunkIn]


@app.post("/extract-entites")
def extract_entites(req: ExtractionRequest):
    if req.registre not in ("SEP", "EPR"):
        raise HTTPException(400, "registre doit être 'SEP' ou 'EPR'.")
    if not req.chunks:
        raise HTTPException(422, "Aucun chunk de texte fourni.")

    chunks_dossier = [{"texte": c.texte, "date": c.date} for c in req.chunks]
    tables_config = SCHEMAS_SEP if req.registre == "SEP" else SCHEMAS_EPR  # nécessite la section 2 collée

    try:
        resultat = extraire_un_dossier(chunks_dossier, tables_config, req.registre)  # section 12
    except NameError as exc:
        # Garde-fou explicite si les sections 2/3/5-12 n'ont pas encore été collées.
        raise HTTPException(
            500,
            f"Service mal configuré : une fonction/config du notebook n'a pas été "
            f"collée dans entities_extraction_service.py ({exc}).",
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Échec de l'extraction : {exc}")
    finally:
        gc.collect()

    return resultat


@app.get("/health")
def health():
    try:
        r = requests.get("http://localhost:8003/health", timeout=3)
        qwen_ok = r.status_code == 200
    except requests.RequestException:
        qwen_ok = False
    return {"service": "ok", "qwen_8003": qwen_ok}
