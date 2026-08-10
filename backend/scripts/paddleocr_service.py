

import os
os.environ.setdefault("OMP_NUM_THREADS", "8")  

import sys
import time
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from pathlib import Path
from threading import Lock
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import paddleocr_transcribe as pt

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("paddleocr_service")

app = FastAPI(title="PaddleOCR-VL Service")


OCR_TIMEOUT_SECONDS = 1800  


_ocr_lock = Lock()


_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ocr-worker")


class OcrRequest(BaseModel):
    input_path: str
    out_dir: Optional[str] = None
    md_out: Optional[str] = None


class OcrResponse(BaseModel):
    markdown: str
    md_path: str
    qc_alerts: List[dict]
    n_pages: int


@app.on_event("startup")
def _preload_pipeline():
    
    try:
        logger.info("Préchargement de PaddleOCR-VL au démarrage...")
        pt._load_pipeline()
        logger.info("Pipeline prêt, service opérationnel.")
    except Exception as e:
        
        logger.warning(f"Préchargement du pipeline échoué : {e}")


@app.get("/health")
def health():
    return {"status": "ok", "pipeline_loaded": bool(pt._PIPELINE_CACHE)}


@app.post("/ocr", response_model=OcrResponse)
def ocr(req: OcrRequest):
    if not os.path.isfile(req.input_path):
        raise HTTPException(status_code=404, detail=f"Fichier introuvable : {req.input_path}")

    t_submitted = time.time()
    logger.info(f"[OCR] Requête reçue : {req.input_path} (en attente du verrou...)")

    future = _executor.submit(_run_transcribe, req)

    try:
        result = future.result(timeout=OCR_TIMEOUT_SECONDS)
    except FutureTimeoutError:
        elapsed = time.time() - t_submitted
        logger.error(f"[OCR] Timeout après {elapsed:.1f}s pour {req.input_path}")
        raise HTTPException(
            status_code=504,
            detail=f"Le traitement OCR a dépassé {OCR_TIMEOUT_SECONDS}s "
                   f"({req.input_path}). Le traitement continue en arrière-plan "
                   f"mais la réponse n'a pas pu être retournée à temps.",
        )
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception(f"[OCR] Échec pour {req.input_path}")
        raise HTTPException(status_code=500, detail=f"OCR échoué : {e}")

    return result


def _run_transcribe(req: OcrRequest) -> dict:
    
    t_wait_start = time.time()
    with _ocr_lock:
        wait_duration = time.time() - t_wait_start
        if wait_duration > 1:
            logger.info(f"[OCR] Attente du verrou : {wait_duration:.1f}s pour {req.input_path}")

        t0 = time.time()
        logger.info(f"[OCR] Début transcription : {req.input_path}")
        result = pt.transcribe_scan(req.input_path, out_dir=req.out_dir)
        logger.info(f"[OCR] Transcription terminée en {time.time() - t0:.1f}s ({req.input_path})")

    markdown_text = result["markdown"]
    md_out_path = Path(req.md_out) if req.md_out else Path(req.input_path).with_suffix(".md")
    try:
        md_out_path.write_text(markdown_text, encoding="utf-8")
    except Exception as e:
        logger.warning(f"Impossible d'écrire le fichier .md ({md_out_path}) : {e}")

    return {
        "markdown": markdown_text,
        "md_path": str(md_out_path),
        "qc_alerts": result["qc_alerts"],
        "n_pages": result["n_pages"],
    }