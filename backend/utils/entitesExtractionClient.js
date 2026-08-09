const axios = require('axios');

// Port du microservice entities_extraction_service.py (FastAPI).
// À côté de tes services existants : Whisper:8001, PaddleOCR:8002, Qwen (LLM):8003.
const ENTITES_SERVICE_URL = process.env.ENTITES_SERVICE_URL || 'http://localhost:8004';

async function extraireEntitesMedicales(registre, chunks) {
  const { data } = await axios.post(
    `${ENTITES_SERVICE_URL}/extract-entites`,
    { registre, chunks },
    { timeout: 10 * 60 * 1000 } // extraction multi-tables + vote + vérification : peut être long
  );
  return data;
}

// --- Streaming (chunk par chunk, avec memoire des donnees deja en base) ---
// etatInitial (optionnel) : { nom_table: {...} ou [...] }, construit a partir
// des lignes deja enregistrees pour ce patient (voir chargerEtatInitialDepuisDB
// plus bas). patientId (optionnel) : juste renvoye tel quel dans la reponse.
async function extraireEntitesMedicalesStreaming(registre, chunks, { patientId, etatInitial } = {}) {
  const { data } = await axios.post(
    `${ENTITES_SERVICE_URL}/extract-entites-streaming`,
    { registre, chunks, patient_id: patientId, etat_initial: etatInitial },
    { timeout: 10 * 60 * 1000 }
  );
  return data;
}

module.exports = { extraireEntitesMedicales, extraireEntitesMedicalesStreaming };