"""Parser für Scorekarten aus CSV, PDF und Fotos.

Alle Parser liefern dasselbe Format:
  {"cards": [{"player": str, "dgv_id": str|None, "strokes": [int|None, ...],
              "confidence": "high"|"low", "notes": [str]}],
   "warnings": [str]}

Das Ergebnis wird NIE automatisch übernommen. Ein Editor ordnet die Karten
den Turnierteilnehmern zu und bestätigt sie. Besonders OCR-Ergebnisse sind
immer "low" und müssen geprüft werden.

Unterstütztes CSV-Format (Trennzeichen ; , oder Tab, UTF-8 oder Windows-1252):
  Name;DGV;1;2;3;...;18        (Kopfzeile, Loch-Spalten "1".."18" oder "L1"/"Loch 1")
  Max Muster;123456;5;4;-;...  ("-", "x" oder leer = gestrichen)
"""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import Any

MAX_ROWS = 500
MAX_PDF_PAGES = 20
MAX_IMAGE_PIXELS = 40_000_000
OCR_TIMEOUT_S = 60

STRUCK = {"", "-", "–", "—", "x", "X", "/", "0"}
HOLE_HEADER = re.compile(r"^\s*(?:l(?:och)?\.?\s*)?(\d{1,2})\s*$", re.IGNORECASE)
NAME_HEADERS = {"name", "spieler", "spielername", "spieler/in", "player"}
DGV_HEADERS = {"dgv", "dgv-nr", "dgv-nr.", "dgv nr", "ausweis", "ausweisnr", "dgv-id", "mitgliedsnr"}
# Zeilen auf Scorekarten, die keine Spielerscores sind.
SKIP_LABELS = re.compile(
    r"^(par|hcp|loch|hole|vorgabe|stbf|stableford|punkte|netto|brutto|meter|länge|laenge|si|score|"
    r"out|in|total|summe|gesamt|cr|sr|slope)\b",
    re.IGNORECASE,
)


class ParseError(Exception):
    pass


def _cell_to_strokes(raw: str) -> tuple[int | None, str | None]:
    v = raw.strip()
    if v in STRUCK:
        return None, None
    if not v.isdigit():
        return None, f"'{v}' ist keine Schlagzahl"
    n = int(v)
    if not 1 <= n <= 20:
        return None, f"{n} ist keine gültige Schlagzahl"
    return n, None


def _hole_columns(header: list[str]) -> list[int] | None:
    cols: list[tuple[int, int]] = []
    for idx, h in enumerate(header):
        m = HOLE_HEADER.match(h)
        if m and 1 <= int(m.group(1)) <= 18:
            cols.append((int(m.group(1)), idx))
    nums = [n for n, _ in cols]
    for count in (18, 9):
        for start in (1, 10):
            want = list(range(start, start + count))
            if want[-1] <= 18 and all(n in nums for n in want):
                return [dict(cols)[n] for n in want]
    return None


def parse_rows(rows: list[list[str]], confidence: str = "high") -> dict[str, Any]:
    """Tabellarische Daten (CSV oder PDF-Tabelle) mit Kopfzeile parsen."""
    warnings: list[str] = []
    header_idx = None
    hole_cols = None
    for i, row in enumerate(rows[:10]):
        hole_cols = _hole_columns([c or "" for c in row])
        if hole_cols:
            header_idx = i
            break
    if header_idx is None or hole_cols is None:
        raise ParseError("Keine Kopfzeile mit Lochnummern (1–9 oder 1–18) gefunden.")
    header = [(c or "").strip().lower() for c in rows[header_idx]]
    name_col = next((i for i, h in enumerate(header) if h in NAME_HEADERS), None)
    dgv_col = next((i for i, h in enumerate(header) if h in DGV_HEADERS), None)
    if name_col is None:
        # Fallback: erste Spalte, die keine Lochspalte ist
        name_col = next((i for i in range(len(header)) if i not in hole_cols and i != dgv_col), None)
    if name_col is None:
        raise ParseError("Keine Spalte mit Spielernamen gefunden.")

    cards = []
    for line_no, row in enumerate(rows[header_idx + 1 :], start=header_idx + 2):
        row = [(c or "") for c in row]
        if not any(c.strip() for c in row):
            continue
        name = row[name_col].strip() if name_col < len(row) else ""
        if not name or SKIP_LABELS.match(name):
            continue
        if len(cards) >= MAX_ROWS:
            raise ParseError(f"Mehr als {MAX_ROWS} Zeilen.")
        strokes, notes = [], []
        for hole_no, col in enumerate(hole_cols, start=1):
            val, err = _cell_to_strokes(row[col] if col < len(row) else "")
            if err:
                notes.append(f"Loch {hole_no}: {err}")
            strokes.append(val)
        dgv = row[dgv_col].strip() if dgv_col is not None and dgv_col < len(row) else None
        cards.append(
            {
                "player": name[:200],
                "dgv_id": (dgv or None) and dgv[:32],
                "strokes": strokes,
                "confidence": "low" if notes else confidence,
                "notes": notes,
                "line": line_no,
            }
        )
    if not cards:
        raise ParseError("Keine Scorekarten-Zeilen gefunden.")
    return {"cards": cards, "warnings": warnings}


class _Semicolon(csv.excel):
    delimiter = ";"


def parse_csv(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
    except csv.Error:
        dialect = _Semicolon
    reader = csv.reader(io.StringIO(text), dialect)
    rows = []
    for i, row in enumerate(reader):
        if i > MAX_ROWS + 10:
            raise ParseError(f"Mehr als {MAX_ROWS} Zeilen.")
        rows.append(row)
    return parse_rows(rows, "high")


def _extract_holes(nums: list[int]) -> tuple[list[int], bool] | None:
    """Findet 9 oder 18 Lochwerte in einer Zahlenreihe, ggf. mit Out/In/Total.

    Rückgabe: (Werte, durch Summen bestätigt)
    """
    # 18 Loch mit Zwischensummen: 9 Werte, OUT, 9 Werte, IN[, TOTAL]
    if len(nums) >= 20:
        front, out_, back, in_ = nums[:9], nums[9], nums[10:19], nums[19]
        if sum(front) == out_ and sum(back) == in_:
            return front + back, True
    if len(nums) >= 18 and all(0 <= n <= 20 for n in nums[:18]):
        confirmed = len(nums) > 18 and nums[18] == sum(nums[:18])
        return nums[:18], confirmed
    if len(nums) >= 9 and all(0 <= n <= 20 for n in nums[:9]):
        confirmed = len(nums) > 9 and nums[9] == sum(nums[:9])
        return nums[:9], confirmed
    return None


TOKEN = re.compile(r"\d+|[-–—xX]")


def _segment_merged_digits(digits: str) -> list[int] | None:
    """OCR verschluckt oft die Leerzeichen zwischen einstelligen Zahlen
    ("54365435641"). Wir zerlegen die Ziffernfolge in 9 oder 18 Lochwerte
    (1–9 oder 10–20) plus Summe — und akzeptieren nur Zerlegungen, deren
    Summe aufgeht und die eindeutig sind. Sonst lieber nichts erkennen.
    """
    if not 9 <= len(digits) <= 60:
        return None
    solutions: list[list[int]] = []

    def walk(pos: int, holes: list[int], n: int) -> None:
        if len(solutions) > 1:
            return
        if len(holes) == n:
            rest = digits[pos:]
            if rest and rest[0] != "0" and int(rest) == sum(holes):
                solutions.append(list(holes))
            return
        for width in (1, 2):
            part = digits[pos : pos + width]
            if len(part) < width or part[0] == "0":
                continue
            v = int(part)
            if (width == 1 and 1 <= v <= 9) or (width == 2 and 10 <= v <= 20):
                holes.append(v)
                walk(pos + width, holes, n)
                holes.pop()

    for n in (18, 9):
        solutions.clear()
        walk(0, [], n)
        if len(solutions) == 1:
            return solutions[0]
    return None


def parse_text(text: str, confidence: str) -> dict[str, Any]:
    """Heuristik für Freitext (PDF ohne Tabellen, OCR): Zeile = Name + Zahlen."""
    cards = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.search(r"\d", line)
        if not m:
            continue
        label = line[: m.start()].strip(" :;|\t")
        if not label or SKIP_LABELS.match(label) or len(label) < 3:
            continue
        tokens = TOKEN.findall(line[m.start() :])
        # Gestrichene Löcher als 0 → später None
        nums = [int(t) if t.isdigit() else 0 for t in tokens]
        found = _extract_holes(nums) if nums else None
        if not found and any(len(t) > 2 for t in tokens if t.isdigit()):
            merged = _segment_merged_digits("".join(t for t in tokens if t.isdigit()))
            found = (merged, True) if merged else None
        if not found:
            continue
        values, confirmed = found
        strokes = [v if v > 0 else None for v in values]
        notes = [] if confirmed else ["Keine Summenzeile zur Kontrolle gefunden"]
        cards.append(
            {
                "player": label[:200],
                "dgv_id": None,
                "strokes": strokes,
                "confidence": confidence if confirmed else "low",
                "notes": notes,
            }
        )
        if len(cards) >= MAX_ROWS:
            break
    if not cards:
        raise ParseError("Keine Scorekarte im Text erkannt.")
    return {"cards": cards, "warnings": []}


def parse_pdf(path: Path) -> dict[str, Any]:
    import pdfplumber

    try:
        with pdfplumber.open(path) as pdf:
            if len(pdf.pages) > MAX_PDF_PAGES:
                raise ParseError(f"PDF hat mehr als {MAX_PDF_PAGES} Seiten.")
            tables, texts = [], []
            for page in pdf.pages:
                tables.extend(page.extract_tables() or [])
                texts.append(page.extract_text() or "")
    except ParseError:
        raise
    except Exception as e:  # beschädigte/manipulierte PDFs
        raise ParseError("PDF konnte nicht gelesen werden (beschädigt oder verschlüsselt).") from e
    for t in tables:
        try:
            return parse_rows([[c or "" for c in row] for row in t], "high")
        except ParseError:
            continue
    text = "\n".join(texts)
    if not text.strip():
        raise ParseError("PDF enthält keinen Text (gescannt?) — bitte als Foto importieren.")
    return parse_text(text, "high")


def parse_photo(path: Path) -> dict[str, Any]:
    from PIL import Image, ImageOps

    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    try:
        with Image.open(path) as probe:
            probe.verify()
        with Image.open(path) as img:
            img = ImageOps.exif_transpose(img).convert("L")
            if img.width < 1600:
                f = 1600 / img.width
                img = img.resize((1600, int(img.height * f)))
            prepared = img.copy()
    except Image.DecompressionBombError as e:
        raise ParseError("Bild ist zu groß.") from e
    except Exception as e:
        raise ParseError("Bild konnte nicht gelesen werden (beschädigt?).") from e

    import pytesseract

    try:
        langs = set(pytesseract.get_languages(config=""))
        lang = "deu" if "deu" in langs else "eng"
        text = pytesseract.image_to_string(
            prepared, lang=lang, config="--psm 6", timeout=OCR_TIMEOUT_S
        )
    except RuntimeError as e:  # Timeout
        raise ParseError("Texterkennung hat zu lange gedauert.") from e
    except pytesseract.TesseractNotFoundError as e:
        raise ParseError("Texterkennung (tesseract) ist auf dem Server nicht installiert.") from e
    result = parse_text(text, "low")
    for c in result["cards"]:
        c["confidence"] = "low"
    result["warnings"].append("Foto-Erkennung ist fehleranfällig — jede Zeile bitte prüfen.")
    return result


PARSERS = {"csv": parse_csv, "pdf": parse_pdf, "photo": parse_photo}
