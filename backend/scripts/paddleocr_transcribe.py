import argparse
import gc
import io
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import List

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from dotenv import load_dotenv

_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env')
load_dotenv(dotenv_path=_ENV_PATH)


SUPPORTED_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"}
LANG = "fr"

MIN_WIDTH = 1500
MAX_WIDTH = 2000
MAX_SCALE = 4.0

PDF_DPI = 300


def validate_input_file(input_path: Path) -> Path:
    if not input_path.is_file():
        raise FileNotFoundError(f"Fichier introuvable : {input_path}")
    if input_path.suffix.lower() not in SUPPORTED_EXT:
        raise ValueError(
            f"Extension non supportée : {input_path.suffix} "
            f"(supportées : {', '.join(sorted(SUPPORTED_EXT))})"
        )
    return input_path



def pdf_to_images(pdf_path: Path, out_dir: Path, dpi: int = PDF_DPI) -> List[Path]:
    try:
        import fitz  
    except ImportError:
        raise RuntimeError(
            "PyMuPDF (fitz) n'est pas installé. Installez-le avec : pip install pymupdf"
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(str(pdf_path))
    zoom = dpi / 72
    matrix = fitz.Matrix(zoom, zoom)
    out_paths = []
    for i, page in enumerate(doc, start=1):
        pix = page.get_pixmap(matrix=matrix)
        out_path = out_dir / f"{pdf_path.stem}_p{i}.png"
        pix.save(str(out_path))
        out_paths.append(out_path)
    doc.close()
    print(f"[INFO] {pdf_path.name} : {len(out_paths)} page(s) rasterisée(s) -> {out_dir}", file=sys.stderr)
    return out_paths


def expand_input(filepath: Path, out_dir: Path) -> List[Path]:
    if filepath.suffix.lower() == ".pdf":
        return pdf_to_images(filepath, out_dir)
    return [filepath]




def deskew_image(img):
    import cv2
    import numpy as np

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    gray = cv2.bitwise_not(gray)
    thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]
    coords = np.column_stack(np.where(thresh > 0))
    if coords.shape[0] < 20:
        return img
    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle
    if abs(angle) < 0.1 or abs(angle) > 15:
        return img
    (h, w) = img.shape[:2]
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(
        img, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )
    return rotated


def denoise_image(img):
    import cv2
    return cv2.fastNlMeansDenoisingColored(
        img, None, h=5, hColor=5, templateWindowSize=7, searchWindowSize=21
    )


def apply_clahe(img, clip_limit: float = 2.0, tile_grid_size=(8, 8)):
    import cv2
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=tile_grid_size)
    l_eq = clahe.apply(l)
    lab_eq = cv2.merge((l_eq, a, b))
    return cv2.cvtColor(lab_eq, cv2.COLOR_LAB2BGR)


def preprocess_image(filepath: Path, out_dir: Path, apply_denoise: bool = False) -> Path:
    import cv2

    img = cv2.imread(str(filepath))
    if img is None:
        raise ValueError(f"Impossible de lire l'image : {filepath}")

    img = deskew_image(img)
    if apply_denoise:
        img = denoise_image(img)
    img = apply_clahe(img)

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{filepath.stem}_pre{filepath.suffix}"
    cv2.imwrite(str(out_path), img)
    print(f"[INFO] {filepath.name} : prétraité (deskew+CLAHE) -> {out_path}", file=sys.stderr)
    return out_path




def decider_et_appliquer_upscaling(
    filepath: Path,
    min_width: int = MIN_WIDTH,
    max_width: int = MAX_WIDTH,
    max_scale: float = MAX_SCALE,
    out_dir: Path = None,
) -> dict:
    
    import cv2

    img = cv2.imread(str(filepath))
    if img is None:
        raise ValueError(f"Impossible de lire l'image : {filepath}")

    h, w = img.shape[:2]
    out_dir = out_dir or filepath.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = Path(out_dir) / f"{filepath.stem}_upscaled{filepath.suffix}"

    decision = {
        "file": filepath.name, "width_orig": w, "height_orig": h,
        "upscale_applique": False, "facteur": 1.0,
        "width_final": w, "height_final": h, "path": out_path,
    }

    if w >= min_width:
        if w > max_width:
            scale = max_width / w
            resized = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
            cv2.imwrite(str(out_path), resized)
            decision.update(upscale_applique=False, facteur=round(scale, 2),
                             width_final=resized.shape[1], height_final=resized.shape[0])
            print(f"[INFO] {filepath.name} : {w}x{h} -> {resized.shape[1]}x{resized.shape[0]} "
                  f"(downscale, dépassait {max_width}px)", file=sys.stderr)
        else:
            cv2.imwrite(str(out_path), img)
            print(f"[INFO] {filepath.name} : {w}x{h} déjà suffisant, aucun upscaling appliqué", file=sys.stderr)
        return decision

    scale = min(min_width / w, max_scale)
    target_w = int(w * scale)
    if target_w > max_width:
        scale = max_width / w

    resized = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)
    cv2.imwrite(str(out_path), resized)
    decision.update(upscale_applique=True, facteur=round(scale, 2),
                     width_final=resized.shape[1], height_final=resized.shape[0])

    alerte = ""
    if w < 400:
        alerte = " [ATTENTION] résolution source critique, l'upscaling ne compensera pas tout"
    print(f"[INFO] {filepath.name} : {w}x{h} -> {resized.shape[1]}x{resized.shape[0]} "
          f"(scale={scale:.2f}x){alerte}", file=sys.stderr)

    return decision




_PIPELINE_CACHE = {}


def _load_pipeline():
    if _PIPELINE_CACHE:
        return _PIPELINE_CACHE["pipeline"]

    os.environ["FLAGS_use_cuda_managed_memory"] = "true"
    from paddleocr import PaddleOCRVL

    print("[INFO] Chargement de PaddleOCR-VL-1.6...", file=sys.stderr)
    pipeline = PaddleOCRVL(
        pipeline_version="v1.6",
        markdown_ignore_labels=["figure"],
    )
    print("[INFO] PaddleOCR-VL-1.6 chargé (figures ignorées dans le markdown).", file=sys.stderr)

    _PIPELINE_CACHE["pipeline"] = pipeline
    return pipeline


def run_paddleocr_vl(filepath: Path, out_dir: Path, preprocess_dir: Path) -> dict:
    
    import paddle

    pipeline = _load_pipeline()

    print(f"[INFO] PaddleOCR-VL : {filepath.name}", file=sys.stderr)
    info = {
        "file": filepath.name, "n_pages": 0, "n_tables": 0, "n_table_rows": 0,
        "mean_confidence": None, "truncated": False, "error": None,
    }

    def _predict_and_collect(path_to_predict: Path):
        output = pipeline.predict(str(path_to_predict))
        all_confs = []
        for res in output:
            info["n_pages"] += 1
            res.save_to_json(save_path=str(out_dir / f"{filepath.stem}_vl"))
            res.save_to_markdown(save_path=str(out_dir / f"{filepath.stem}_vl"))
            try:
                rj = res.json if hasattr(res, "json") else {}
                for page in rj.get("parsing_res_list", []):
                    label = page.get("block_label", "")
                    if label == "table":
                        info["n_tables"] += 1
                        html = page.get("res", "")
                        info["n_table_rows"] += html.count("<tr>")
                    conf = page.get("score") or page.get("confidence")
                    if conf is not None:
                        all_confs.append(float(conf))
            except Exception as e:
                print(f"[WARN] parsing JSON metrics échoué : {e}", file=sys.stderr)
        return all_confs

    try:
        all_confs = _predict_and_collect(filepath)
        if all_confs:
            info["mean_confidence"] = round(sum(all_confs) / len(all_confs), 4)
    except RuntimeError as e:
        msg = str(e)
        info["error"] = msg
        print(f"[WARN] Erreur sur {filepath.name} : {msg[:200]}", file=sys.stderr)
        if "out of memory" in msg.lower() or "oom" in msg.lower():
            print("[INFO] OOM détecté, retry sans upscaling...", file=sys.stderr)
            paddle.device.cuda.empty_cache()
            gc.collect()

            orig = preprocess_dir / (filepath.stem.replace("_upscaled", "") + filepath.suffix)
            if orig.exists() and orig != filepath:
                try:
                    info["n_pages"] = info["n_tables"] = info["n_table_rows"] = 0
                    all_confs = _predict_and_collect(orig)
                    if all_confs:
                        info["mean_confidence"] = round(sum(all_confs) / len(all_confs), 4)
                    info["truncated"] = True
                    info["error"] = None
                    print(f"[INFO] Retry réussi sur {orig.name}", file=sys.stderr)
                except Exception as e2:
                    info["truncated"] = True
                    info["error"] = f"{msg} | fallback failed: {e2}"
            else:
                info["truncated"] = True
                info["error"] = f"{msg} | fallback introuvable: {orig}"
    finally:
        paddle.device.cuda.empty_cache()
        gc.collect()
    return info




def strip_inline_images(texte: str) -> str:
    return re.sub(
        r"<img[^>]*src=['\"]imgs/[^'\"]*['\"][^>]*/?>",
        "",
        texte,
    )


def nettoyer_latex(texte: str) -> str:
    if texte is None:
        return texte
    t = texte
    t = t.replace("$", "")
    t = t.replace("\\times", "x")
    t = t.replace("\\cdot", "·")
    t = t.replace("\\pm", "±")
    t = t.replace("\\geq", "≥")
    t = t.replace("\\leq", "≤")
    t = t.replace("\\approx", "≈")
    t = t.replace("\\uparrow", "↑")
    t = t.replace("\\downarrow", "↓")
    t = t.replace("\\rightarrow", "→")
    t = t.replace("\\to", "→")
    t = t.replace("\\n", "<br>")
    t = t.replace("\\mu", "µ")
    t = t.replace("\\circ", "°")
    t = t.replace("\\alpha", "α")
    t = t.replace("\\beta", "β")
    t = re.sub(r"\\underline\{\\text\{(.*?)\}\}", r"\1", t)
    t = re.sub(r"\\text\{\{(.*?)\}\}", r"\1", t)
    t = re.sub(r"\\text\{(.*?)\}", r"\1", t)
    frac_pattern = re.compile(r"\\frac\{([^{}]*)\}\{([^{}]*)\}")
    for _ in range(5):
        nouveau_t = frac_pattern.sub(r" \1/\2", t)
        if nouveau_t == t:
            break
        t = nouveau_t
    t = re.sub(r"\\[a-zA-Z]+\{([^{}]*)\}", r"\1", t)
    exposants_unicode = {"0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴",
                          "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹"}

    def remplacer_exposant(match):
        chiffres = match.group(1)
        return "".join(exposants_unicode.get(c, c) for c in chiffres)

    t = re.sub(r"\^\{\{(\d+)\}\}", remplacer_exposant, t)
    t = re.sub(r"\^\{(\d+)\}", remplacer_exposant, t)
    t = re.sub(r"\^(\d+)", remplacer_exposant, t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    t = "\n".join(line.strip() for line in t.split("\n"))
    return t.strip()


def _largeur_ligne(row) -> int:
    total = 0
    for c in row.find_all(["td", "th"]):
        try:
            total += int(c.get("colspan", 1))
        except (TypeError, ValueError):
            total += 1
    return total


def detecter_table_tronquee(texte: str, seuil_lignes: int = 5) -> dict:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(texte, "html.parser")
    alertes = []

    for i, table in enumerate(soup.find_all("table")):
        rows = table.find_all("tr")
        n_tr = len(rows)

        if n_tr == 0:
            alertes.append(f"table_{i}_vide")
            continue

        largeurs = [_largeur_ligne(r) for r in rows]

        if max(largeurs, default=0) <= 1:
            alertes.append(f"table_{i}_probablement_non_tabulaire ({n_tr} tr, 1 colonne)")
            continue

        derniere_ligne = rows[-1]
        cellules = derniere_ligne.find_all(["td", "th"])
        cellules_vides = all(not c.get_text(strip=True) for c in cellules)
        if n_tr < seuil_lignes and cellules_vides and cellules:
            alertes.append(f"table_{i}_courte_cellules_vides_fin ({n_tr} tr)")

        if len(set(largeurs)) > 1:
            alertes.append(f"table_{i}_colonnes_incoherentes {largeurs}")

    return {"tronque": bool(alertes), "alertes": alertes}


def html_tables_vers_markdown(texte: str) -> str:
    """Convertit les tableaux HTML (<table>...</table>) générés par PaddleOCR-VL
    en tableaux Markdown natifs (syntaxe | col1 | col2 |), pour un rendu lisible
    dans un viewer Markdown standard.
    """
    from bs4 import BeautifulSoup

    def _convertir_une_table(match: "re.Match") -> str:
        html_table = match.group(0)
        soup = BeautifulSoup(html_table, "html.parser")
        table = soup.find("table")
        if table is None:
            return html_table

        lignes = []
        for tr in table.find_all("tr"):
            cellules = [
                c.get_text(separator=" ", strip=True) or "-"
                for c in tr.find_all(["td", "th"])
            ]
            if cellules:
                lignes.append(cellules)

        if not lignes:
            return html_table

        n_cols = max(len(l) for l in lignes)
        lignes = [l + ["-"] * (n_cols - len(l)) for l in lignes]

        def _echapper(cellule: str) -> str:
            return cellule.replace("|", "\\|").replace("\n", " ")

        md_lignes = []
        md_lignes.append("| " + " | ".join(_echapper(c) for c in lignes[0]) + " |")
        md_lignes.append("|" + "---|" * n_cols)
        for ligne in lignes[1:]:
            md_lignes.append("| " + " | ".join(_echapper(c) for c in ligne) + " |")

        return "\n".join(md_lignes)

    return re.sub(r"<table[^>]*>.*?</table>", _convertir_une_table, texte, flags=re.DOTALL)


def nettoyer_markdown(contenu: str) -> tuple:
    
    contenu = strip_inline_images(contenu)
    contenu = nettoyer_latex(contenu)

    tbl_diag = detecter_table_tronquee(contenu)
    qc_msgs = []
    if tbl_diag["tronque"]:
        qc_msgs.append(f"table_probablement_tronquee={tbl_diag['alertes']}")

    
    contenu = html_tables_vers_markdown(contenu)

    if qc_msgs:
        commentaire = f"<!-- OCR_QC: {'; '.join(qc_msgs)} -->\n"
        contenu = commentaire + contenu

    return contenu, qc_msgs



def transcribe_scan(input_path: str, out_dir: str = None) -> dict:
    
    src = validate_input_file(Path(input_path))

    work_dir = Path(out_dir) if out_dir else Path(tempfile.mkdtemp(prefix="paddleocr_"))
    pdf_img_dir = work_dir / "pdf_pages"
    preprocess_dir = work_dir / "preprocessed"
    upscale_dir = work_dir / "upscaled"
    ocr_out_dir = work_dir / "paddleocr_output"
    for d in (pdf_img_dir, preprocess_dir, upscale_dir, ocr_out_dir):
        d.mkdir(parents=True, exist_ok=True)

    try:
        pages = expand_input(src, pdf_img_dir)

        pages_preprocessed = [preprocess_image(p, out_dir=preprocess_dir) for p in pages]

        pages_upscaled = []
        for p in pages_preprocessed:
            decision = decider_et_appliquer_upscaling(p, out_dir=upscale_dir)
            pages_upscaled.append(decision["path"])

        page_markdowns = []
        all_qc_alerts = []
        page_infos = []

        for page_path in pages_upscaled:
            info = run_paddleocr_vl(page_path, out_dir=ocr_out_dir, preprocess_dir=preprocess_dir)
            page_infos.append(info)

            md_dir = ocr_out_dir / f"{page_path.stem}_vl"
            md_files = sorted(md_dir.glob("*.md")) if md_dir.exists() else []
            for md_path in md_files:
                brut = md_path.read_text(encoding="utf-8")
                nettoye, qc_msgs = nettoyer_markdown(brut)
                page_markdowns.append(nettoye)
                if qc_msgs:
                    all_qc_alerts.append({"page": page_path.name, "alertes": qc_msgs})

        markdown_final = "\n\n---\n\n".join(page_markdowns).strip()

        return {
            "markdown": markdown_final,
            "qc_alerts": all_qc_alerts,
            "n_pages": len(pages_upscaled),
            "pages_info": page_infos,
        }
    finally:
        if not out_dir:
            shutil.rmtree(work_dir, ignore_errors=True)
        gc.collect()




def main():
    parser = argparse.ArgumentParser(
        description="Transcrit un document scanné (image/PDF) en Markdown via PaddleOCR-VL."
    )
    parser.add_argument("--input", required=True, help="Chemin vers le fichier scanné à transcrire.")
    parser.add_argument("--out-dir", default=None, help="Dossier de sortie pour les fichiers intermédiaires (optionnel).")
    parser.add_argument("--md-out", default=None, help="Chemin du fichier .md final à écrire (optionnel).")
    parser.add_argument("--json", action="store_true", help='Sortie au format JSON ({"markdown": ...}) au lieu de Markdown brut.')
    args = parser.parse_args()

    try:
        result = transcribe_scan(args.input, out_dir=args.out_dir)
    except (FileNotFoundError, ValueError) as e:
        print(json.dumps({"error": str(e)}) if args.json else f"[ERREUR] {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"error": str(e)}) if args.json else f"[ERREUR] Transcription OCR échouée : {e}", file=sys.stderr)
        sys.exit(1)

    markdown_text = result["markdown"]

    
    md_out_path = Path(args.md_out) if args.md_out else Path(args.input).with_suffix(".md")
    try:
        md_out_path.write_text(markdown_text, encoding="utf-8")
    except Exception as e:
        print(f"[WARN] Impossible d'écrire le fichier .md ({md_out_path}) : {e}", file=sys.stderr)

    if args.json:
        print(json.dumps({
            "markdown": markdown_text,
            "md_path": str(md_out_path),
            "qc_alerts": result["qc_alerts"],
            "n_pages": result["n_pages"],
        }, ensure_ascii=False))
    else:
        print(markdown_text)


if __name__ == "__main__":
    main()