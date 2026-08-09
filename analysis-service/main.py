import os
import math
from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine

from dotenv import load_dotenv
load_dotenv()

from registry import ANALYSES  # registre de toutes les analyses SEP/EPR

app = FastAPI(title="Service d'analyse statistique - NeuroExo-Predict")

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg2://user:password@localhost:5432/registre_neuroexo",
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)


def _assainir(valeur):
    
    if isinstance(valeur, float):
        return None if not math.isfinite(valeur) else valeur
    if isinstance(valeur, dict):
        return {k: _assainir(v) for k, v in valeur.items()}
    if isinstance(valeur, list):
        return [_assainir(v) for v in valeur]
    return valeur


@app.get("/analyses")
def lister_analyses():
    
    return [
        {
            "id": key,
            "registre": meta["registre"],
            "titre": meta["titre"],
            "description": meta["description"],
            "parametres": meta["parametres_schema"],  # décrit le formulaire React
        }
        for key, meta in ANALYSES.items()
    ]


@app.post("/analyses/{analyse_id}/run")
def lancer_analyse(analyse_id: str, config: dict):
    if analyse_id not in ANALYSES:
        raise HTTPException(status_code=404, detail=f"Analyse '{analyse_id}' inconnue")

    fonction = ANALYSES[analyse_id]["run"]
    try:
        resultat = fonction(engine, config)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        
        raise HTTPException(
            status_code=422,
            detail=f"Impossible d'ajuster le modèle avec cette configuration "
                   f"({type(e).__name__}: {e}). Réduisez le nombre de "
                   f"covariables ou changez la fenêtre de tolérance.",
        )
    return _assainir(resultat)