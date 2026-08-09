import json
import re
import threading
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from llama_cpp import Llama

MODEL_PATH = r"C:\hf-cache\qwen3-8b-gguf\Qwen3-8B-Q4_K_M.gguf"

CHAMPS = [
    "nom_prenom", "date_naissance", "adresse", "origine",
    "telephone", "cin", "num_cnam", "nom_prenom_pere", "nom_prenom_mere",
    "frere", "soeur", "autre_antecedent",
]

_extractor_model = None
_extractor_tokenizer = None
_model_load_error = None
_load_lock = threading.Lock()


# ============================================================
# 1. Structures partagees (identique au notebook)
# ============================================================

class EntityType(str, Enum):
    NOM_PRENOM = "NOM_PRENOM"
    DATE_NAISSANCE = "DATE_NAISSANCE"
    CIN = "CIN"
    NUM_CNAM = "NUM_CNAM"
    TELEPHONE = "TELEPHONE"
    ORIGINE = "ORIGINE"
    ADRESSE_PATIENT = "ADRESSE_PATIENT"


class EntityRole(str, Enum):
    PATIENT = "PATIENT"
    PERE = "PERE"
    MERE = "MERE"
    FRERE = "FRERE"
    SOEUR = "SOEUR"
    ANTECEDENT = "ANTECEDENT"
    INCONNU = "MEDECIN"


@dataclass
class Entity:
    text: str
    start: int
    end: int
    label: EntityType
    method: str
    confidence: float
    role: Optional[EntityRole] = None

    def __repr__(self):
        role_part = f" | role={self.role.value}" if self.role else ""
        return (f"{self.label.value:<18} {self.text!r}{role_part}  "
                f"(method={self.method}, conf={self.confidence:.2f})")


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def _clean_json_block(raw: str) -> str:
    raw = raw.strip()
    raw = re.sub(r"^```json\s*|\s*```$", "", raw)
    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    return match.group(0) if match else raw


def _find_span_extractor(text: str, needle: str):
    needle = (needle or "").strip(" ,.;:!?()\"'")
    if len(needle) < 2:
        return None
    idx = text.find(needle)
    if idx == -1:
        idx = text.lower().find(needle.lower())
        if idx == -1:
            return None
        needle = text[idx: idx + len(needle)]
    return needle, idx, idx + len(needle)


def dedup_entities_by_identity(entities: list) -> list:
    if not entities:
        return entities

    sorted_ents = sorted(entities, key=lambda e: e.start)

    def _norm(text: str) -> str:
        return _strip_accents(text.strip().lower())

    enriched = []
    for ent in sorted_ents:
        n_text = _norm(ent.text)
        n_label = ent.label.value
        n_role = ent.role.value if ent.role else "PATIENT"
        enriched.append((ent, n_text, n_label, n_role))

    keep = []
    for i, (ent_i, ntext_i, nlabel_i, nrole_i) in enumerate(enriched):
        is_dup = False
        for ent_j, ntext_j, nlabel_j, nrole_j in keep:
            if nlabel_i != nlabel_j or nrole_i != nrole_j:
                continue
            if ntext_i in ntext_j or ntext_j in ntext_i:
                is_dup = True
                break
        if not is_dup:
            keep.append((ent_i, ntext_i, nlabel_i, nrole_i))

    return [item[0] for item in keep]


# ============================================================
# 2. Chargement du modele (llama_cpp / GGUF local, remplace transformers)
# ============================================================

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
            _extractor_tokenizer = None
            _extractor_model = model
        except Exception as exc:
            _model_load_error = str(exc)
            raise


# ============================================================
# 3bis. Chat completion generique OpenAI-compatible
#
# Reutilise le MEME objet Llama() deja charge par _charger_modele()
# (pas de second chargement du gguf, pas de second processus).
# Expose depuis extraction_service.py sous /v1/chat/completions pour
# que entities_extraction_service.py (pipeline SEP/EPR) tape sur ce
# meme serveur/modele deja en memoire au lieu d'en charger un autre.
# ============================================================

def chat_completion_openai_like(messages, max_tokens=768, temperature=0.0,
                                 json_schema=None, enable_thinking=False):
    """Appelle le modele deja charge et renvoie une reponse au format
    OpenAI (choices[0].message.content), pret a etre reembale par
    extraction_service.py. json_schema (optionnel) contraint la sortie
    via le mecanisme grammar de llama-cpp-python (equivalent local au
    'guided_json' de vLLM)."""
    _charger_modele()
    if _extractor_model is None:
        raise RuntimeError(f"Modele non charge : {_model_load_error}")

    kwargs = dict(
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    if json_schema is not None:
        # llama-cpp-python : contrainte JSON via response_format (grammar
        # derivee automatiquement du schema) - equivalent local du
        # guided_json de vLLM, sans second serveur ni second modele.
        kwargs["response_format"] = {"type": "json_object", "schema": json_schema}
    if not enable_thinking:
        # Qwen3 : desactive le bloc <think> pour les appels d'extraction
        # structuree (plus rapide, sortie directement exploitable).
        kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}

    return _extractor_model.create_chat_completion(**kwargs)


# ============================================================
# 3. Prompt systeme (identique au notebook, NUM_DOSSIER retire)
# ============================================================

_EXTRACTOR_SYSTEM_PROMPT = """### ROLE
Tu es un extracteur d'entites cliniques specialise pour le contexte medical tunisien (dossiers pediatriques, comptes-rendus, courriers).
Tu appliques des REGLES GENERALES ci-dessous a N'IMPORTE QUEL texte, y compris des formulations, formats de dates, d'identifiants ou de noms que tu n'as jamais vus. Ne te limite pas aux formes illustrees en exemple : elles servent a montrer le PRINCIPE, pas la liste exhaustive des cas.
Tu ne dois JAMAIS inventer d'information. En cas de doute reel, tu n'extrais rien plutot que de risquer une erreur — mais l'absence d'un exemple identique dans ce prompt n'est PAS un doute legitime : applique la regle generale.

### METHODE DE RAISONNEMENT (obligatoire)
Pour chaque entite candidate, verifie dans l'ordre, dans ton raisonnement :
1. TYPE — A quelle categorie appartient l'information factuellement (pas juste au mot le plus proche) ?
2. PREUVE — Le texte justifie-t-il explicitement ce type et ce role, ou est-ce une supposition ?
3. FORME — Le span copie est-il exactement celui du texte source, sans mot ajoute ni tronque ?
Si les 3 checks passent, extrait. Sinon, n'extrait pas.

---

### LES 7 TYPES D'ENTITES

**NOM_PRENOM**
- Nom et/ou prenom d'une personne physique, reelle, nommement designee.
- N'inclut JAMAIS : titre (Dr., Pr., Mme, M.), lien de parente ("le pere", "sa soeur"), fonction.
- N'inclut JAMAIS un identifiant ou numero de dossier patient (ex. "EPR-AZ-004", "SEP_MJ_001", "D-2024-118", tout code alphanumerique avec tirets ou underscores) : ce n'est pas un nom de personne, quelle que soit sa position dans le texte. Un nom_prenom valide est un nom de personne en toutes lettres (ex. "Mehdi Jendoubi"), jamais un code.
- Inclut toute personne nommee avec un titre ou une fonction soignante (medecin traitant, specialiste consulte, personnel medical cite par son nom) — extraire le nom SANS le titre, avec role=MEDECIN. Ceci s'applique de facon SYSTEMATIQUE, a chaque occurrence, pas seulement si le contexte semble important.
- Exemple positif : "le pere Mohamed Gharbi" -> "Mohamed Gharbi"
- Exemple positif : "suivi par le Dr. Salma Ben Youssef" -> "Salma Ben Youssef", role=MEDECIN
- Exemple negatif : "Dr. Amira Kraoua" seule mention sans autre contexte -> quand meme extraire "Amira Kraoua", role=MEDECIN (un professionnel nomme reste une personne identifiable)
- Exemple negatif : "il s'agit de l'enfant EPR-AZ-004" -> ne rien extraire pour EPR-AZ-004 (identifiant de dossier, pas un nom)

**DATE_NAISSANCE**
- Date calendaire COMPLETE (jour + mois + annee) referant EXPLICITEMENT a un evenement de naissance ("ne(e) le", "date de naissance", "DN", "venu(e) au monde le"...).
- Formats possibles : chiffres (04/03/2015, 04-03-2015, 2015-03-04), ou en toutes lettres, ou mixte. Le format n'a pas d'importance : seul le lien explicite a la naissance compte.
- N'EST PAS une date de naissance (donc NE PAS EXTRAIRE, quel que soit le type) :
  - une duree/age ("3 ans", "18 mois", "a l'age de X ans") — ce n'est pas une date calendaire, ne l'etiquette dans AUCUNE categorie ;
  - une date liee a autre chose qu'une naissance (dossier ouvert, consultation, diagnostic, admission, rendez-vous...) — cette date n'est simplement PAS une entite a extraire, quelle que soit sa proximite avec un autre mot-cle comme "dossier" ou "numero".
- Exemple negatif : "dossier ouvert le 12/01/2024" -> ne rien extraire
- Exemple negatif : "premiere crise a l'age de 3 ans, actuellement agee de 7 ans" -> ne rien extraire, aucun des deux ages n'est une date de naissance

**CIN**
- Numero de carte d'identite nationale tunisienne, en general 8 chiffres, avec ou sans separateurs (espaces, tirets).

**NUM_CNAM**
- Identifiant d'assurance maladie / CNAM, numerique ou alphanumerique, avec ou sans prefixe.

**TELEPHONE**
- Numero de telephone, tout format (avec ou sans indicatif, espace ou non).

**ORIGINE**
- Lieu de naissance / origine geographique declaree ("originaire de", "natif de", region d'origine).

**ADRESSE_PATIENT**
- Lieu de residence ACTUELLE. Si plusieurs niveaux administratifs sont donnes pour la meme residence (quartier + gouvernorat, par exemple), extraire en un seul bloc continu tel qu'ecrit dans le texte.
- Un lieu mentionne seulement comme etape passee ("apres avoir demenage depuis X") n'est ni origine ni adresse actuelle : ne pas l'extraire.

### IMPORTANT — identifiants de dossier
Le texte peut mentionner un identifiant ou numero de dossier patient (codes alphanumeriques avec tirets/underscores, ex. "EPR-AZ-004", "SEP_MJ_001"). ll ne s'agit d'AUCUN des types ci-dessus : ne l'extrais dans AUCUNE categorie, quel que soit le contexte (le numero de dossier est deja connu par ailleurs et n'a pas besoin d'etre extrait par toi).

---

### ROLES (uniquement pour NOM_PRENOM — pour les 6 autres types, le role est TOUJOURS "PATIENT")
- PATIENT : la personne suivie/prise en charge.
- PERE / MERE / FRERE / SOEUR : lien familial direct explicitement nomme.
- ANTECEDENT : famille elargie (oncle, tante, grand-parent, cousin) ou lien familial imprecis.
- MEDECIN : toute personne avec titre ou fonction soignante/medicale (Dr., Pr., "medecin traitant", "suivi par", "consultation avec", "adresse par", "revu en RCP avec"...). Applique cette regle a CHAQUE personne correspondant a ce profil dans le texte, sans exception.

Regle generale de robustesse : si un role familial ou professionnel n'est pas explicitement nomme mais que la personne n'est clairement ni PATIENT ni MEDECIN, utilise ANTECEDENT plutot que de ne rien extraire.

---

### REGLES DE QUALITE
1. FIDELITE VERBATIM : span copie exactement tel qu'il apparait (accents, casse, ponctuation interne), sans mot introductif ni ponctuation finale ajoutee.
2. PAS D'INVENTION : n'extrait que ce qui est ecrit, ne complete jamais un nom, une date ou un identifiant partiel.
3. GENERALISATION : les formats, l'orthographe, la presence de bruit OCR (apostrophes cassees, majuscules manquantes, guillemets typographiques) ne changent pas les regles ci-dessus — applique-les malgre le bruit.
4. COHERENCE : applique chaque regle de facon identique a chaque occurrence similaire dans le meme texte (ex. si un medecin est extrait, tous les medecins nommes du texte doivent l'etre).
5. PRUDENCE CIBLEE : le doute legitime porte sur le TYPE ou le ROLE d'une entite, jamais sur le fait d'extraire une entite qui correspond clairement a une definition ci-dessus.

Termine ton raisonnement, puis donne la reponse finale.

### FORMAT DE SORTIE (apres le raisonnement, JSON strict, rien d'autre, pas de balises markdown)
{"entites": [{"type": "TYPE", "texte": "extrait exact", "role": "ROLE"}, ...]}
Si aucune entite : {"entites": []}
"""

_ROLE_MAP = {
    "PATIENT": EntityRole.PATIENT,
    "PERE": EntityRole.PERE,
    "MERE": EntityRole.MERE,
    "FRERE": EntityRole.FRERE,
    "SOEUR": EntityRole.SOEUR,
    "ANTECEDENT": EntityRole.ANTECEDENT,
    "MEDECIN": EntityRole.INCONNU,
}

_TYPE_MAP = {
    "NOM_PRENOM": EntityType.NOM_PRENOM,
    "DATE_NAISSANCE": EntityType.DATE_NAISSANCE,
    "CIN": EntityType.CIN,
    "NUM_CNAM": EntityType.NUM_CNAM,
    "TELEPHONE": EntityType.TELEPHONE,
    "ORIGINE": EntityType.ORIGINE,
    "ADRESSE_PATIENT": EntityType.ADRESSE_PATIENT,
}


# ============================================================
# 4. Extraction LLM (adapte a llama_cpp au lieu de transformers.generate)
# ============================================================

def llm_extract_with_roles(text: str, max_new_tokens: int = 4000, verbose: bool = False) -> list:
    _charger_modele()

    if _extractor_model is None:
        return []

    user_prompt = f'TEXTE :\n"{text}"\nREPONSE ATTENDUE :'

    messages = [
        {"role": "system", "content": _EXTRACTOR_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    sortie = _extractor_model.create_chat_completion(
        messages=messages,
        max_tokens=max_new_tokens,
        temperature=0.2,
        top_p=0.85,
        top_k=20,
        min_p=0.05,
        repeat_penalty=1.0,
        seed=42,
    )

    texte_genere = sortie["choices"][0]["message"]["content"]

    if "</think>" in texte_genere:
        thinking_content, raw = texte_genere.split("</think>", 1)
    else:
        thinking_content, raw = "", texte_genere

    if verbose:
        print("----- THINKING -----")
        print(thinking_content[:2000])
        print("----- REPONSE -----")
        print(raw)

    raw = _clean_json_block(raw)

    try:
        parsed = json.loads(raw)
    except Exception as e:
        print(f"[warn] Reponse extracteur non parsable ({e}) : {raw!r}")
        return []

    entities = []
    for item in parsed.get("entites", []) or []:
        entity_type = _TYPE_MAP.get(item.get("type"))
        if entity_type is None:
            continue
        found = _find_span_extractor(text, item.get("texte", ""))
        if not found:
            continue
        clean_text, start, end = found
        role = (
            _ROLE_MAP.get(item.get("role"), EntityRole.ANTECEDENT)
            if entity_type == EntityType.NOM_PRENOM
            else EntityRole.PATIENT
        )
        entities.append(
            Entity(clean_text, start, end, entity_type, "qwen3-8b-extractor-thinking", 0.8, role=role)
        )
    return dedup_entities_by_identity(sorted(entities, key=lambda e: e.start))


# ============================================================
# 5. Chunking avec overlap (zones core) pour textes longs
# ============================================================

def _split_into_sentences(text: str) -> list:
    spans = []
    start = 0
    for m in re.finditer(r'(?<=[.!?;\n])\s+', text):
        end = m.start() + 1
        spans.append((start, end))
        start = m.end()
    spans.append((start, len(text)))
    return [(s, e) for s, e in spans if e > s]


def chunk_text_with_core_zones(
    text: str,
    max_tokens: int = 100,
    overlap_tokens: int = 30,
    unit: str = "words",
) -> list:
    sentences = _split_into_sentences(text)
    if not sentences:
        return [{"text": text, "chunk_start": 0, "chunk_end": len(text),
                  "core_start": 0, "core_end": len(text)}]

    def n_tokens(s: str) -> int:
        return len(s.split())  # unit="words" uniquement (pas de tokenizer HF disponible avec llama_cpp)

    sent_tokens = [n_tokens(text[s:e]) for s, e in sentences]

    raw_chunks = []
    cur_start = 0
    cur_tokens = 0
    for i, t in enumerate(sent_tokens):
        if cur_tokens + t > max_tokens and i > cur_start:
            raw_chunks.append((cur_start, i))
            cur_start = i
            cur_tokens = 0
        cur_tokens += t
    raw_chunks.append((cur_start, len(sentences)))

    def extend_left(idx_start, budget):
        tok = 0
        j = idx_start
        while j > 0 and tok < budget:
            j -= 1
            tok += sent_tokens[j]
        return j

    def extend_right(idx_end, budget):
        tok = 0
        j = idx_end
        while j < len(sentences) and tok < budget:
            tok += sent_tokens[j]
            j += 1
        return j

    chunks = []
    for k, (core_i_start, core_i_end) in enumerate(raw_chunks):
        ext_i_start = extend_left(core_i_start, overlap_tokens)
        ext_i_end = extend_right(core_i_end, overlap_tokens)

        chunk_start = sentences[ext_i_start][0]
        chunk_end = sentences[ext_i_end - 1][1]

        core_start = sentences[core_i_start][0]
        core_end = sentences[core_i_end - 1][1]

        is_first = (k == 0)
        is_last = (k == len(raw_chunks) - 1)

        chunks.append({
            "text": text[chunk_start:chunk_end],
            "chunk_start": chunk_start,
            "chunk_end": chunk_end,
            "core_start": core_start if not is_first else chunk_start,
            "core_end": core_end if not is_last else chunk_end,
        })

    return chunks


def llm_extract_with_roles_chunked(
    text: str,
    max_tokens: int = 100,
    overlap_tokens: int = 30,
    unit: str = "words",
    max_new_tokens: int = 4000,
    verbose: bool = False,
) -> list:
    chunks = chunk_text_with_core_zones(text, max_tokens=max_tokens, overlap_tokens=overlap_tokens, unit=unit)

    if verbose:
        print(f"[chunking] {len(chunks)} chunk(s) pour {len(text)} caracteres")
        for k, c in enumerate(chunks):
            print(f"  chunk {k}: total=[{c['chunk_start']}:{c['chunk_end']}] "
                  f"core=[{c['core_start']}:{c['core_end']}]")

    all_entities = []
    for c in chunks:
        chunk_entities = llm_extract_with_roles(c["text"], max_new_tokens=max_new_tokens, verbose=verbose)
        for ent in chunk_entities:
            abs_start = ent.start + c["chunk_start"]
            abs_end = ent.end + c["chunk_start"]
            if abs_start >= c["core_start"] and abs_end <= c["core_end"]:
                ent.start = abs_start
                ent.end = abs_end
                all_entities.append(ent)

    return dedup_entities_by_identity(sorted(all_entities, key=lambda e: e.start))


# ============================================================
# 6. Adaptateur : liste d'entites -> dict plat CHAMPS (pour extraction_service.py)
# ============================================================

CHUNKING_THRESHOLD_WORDS = 100


def _entities_to_champs(entities: list) -> dict:
    resultat = {champ: "" for champ in CHAMPS}
    freres, soeurs, antecedents = [], [], []

    for ent in entities:
        role = ent.role.value if ent.role else "PATIENT"

        if ent.label == EntityType.DATE_NAISSANCE:
            resultat["date_naissance"] = resultat["date_naissance"] or ent.text
        elif ent.label == EntityType.ADRESSE_PATIENT:
            resultat["adresse"] = resultat["adresse"] or ent.text
        elif ent.label == EntityType.ORIGINE:
            resultat["origine"] = resultat["origine"] or ent.text
        elif ent.label == EntityType.TELEPHONE:
            resultat["telephone"] = resultat["telephone"] or ent.text
        elif ent.label == EntityType.CIN:
            resultat["cin"] = resultat["cin"] or ent.text
        elif ent.label == EntityType.NUM_CNAM:
            resultat["num_cnam"] = resultat["num_cnam"] or ent.text
        elif ent.label == EntityType.NOM_PRENOM:
            if role == "PATIENT":
                resultat["nom_prenom"] = resultat["nom_prenom"] or ent.text
            elif role == "PERE":
                resultat["nom_prenom_pere"] = resultat["nom_prenom_pere"] or ent.text
            elif role == "MERE":
                resultat["nom_prenom_mere"] = resultat["nom_prenom_mere"] or ent.text
            elif role == "FRERE":
                freres.append(ent.text)
            elif role == "SOEUR":
                soeurs.append(ent.text)
            elif role == "ANTECEDENT":
                antecedents.append(ent.text)
            # role MEDECIN volontairement ignore : pas de champ dedie dans CHAMPS

    resultat["frere"] = ", ".join(freres)
    resultat["soeur"] = ", ".join(soeurs)
    resultat["autre_antecedent"] = ", ".join(antecedents)

    return resultat


def extraire_donnees_patient(texte: str, chunking: bool = True, verbose: bool = False) -> dict:
    if chunking and len(texte.split()) > CHUNKING_THRESHOLD_WORDS:
        entities = llm_extract_with_roles_chunked(texte, verbose=verbose)
    else:
        entities = llm_extract_with_roles(texte, verbose=verbose)

    return _entities_to_champs(entities)