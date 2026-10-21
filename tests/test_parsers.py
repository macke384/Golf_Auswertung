from pathlib import Path

import pytest

from app.imports.parsers import ParseError, parse_csv, parse_pdf, parse_photo, parse_text


def write(tmp_path: Path, name: str, content: bytes) -> Path:
    p = tmp_path / name
    p.write_bytes(content)
    return p


def test_csv_semicolon_18_holes_with_struck(tmp_path):
    header = "Name;DGV;" + ";".join(str(i) for i in range(1, 19))
    row = "Max Müller;123456;" + ";".join(["5"] * 17 + ["-"])
    p = write(tmp_path, "a.csv", (header + "\n" + row + "\n").encode("utf-8-sig"))
    res = parse_csv(p)
    card = res["cards"][0]
    assert card["player"] == "Max Müller" and card["dgv_id"] == "123456"
    assert card["strokes"][:17] == [5] * 17 and card["strokes"][17] is None
    assert card["confidence"] == "high"


def test_csv_windows_1252_comma_nine_holes_loch_headers(tmp_path):
    header = "Spieler," + ",".join(f"Loch {i}" for i in range(1, 10))
    row = "Jörg Weiß," + ",".join(["4"] * 9)
    p = write(tmp_path, "b.csv", (header + "\r\n" + row + "\r\n").encode("cp1252"))
    card = parse_csv(p)["cards"][0]
    assert card["player"] == "Jörg Weiß" and card["strokes"] == [4] * 9


def test_csv_back_nine_headers(tmp_path):
    header = "Name;" + ";".join(str(i) for i in range(10, 19))
    p = write(tmp_path, "c.csv", (header + "\nA B;" + ";".join(["3"] * 9)).encode())
    assert parse_csv(p)["cards"][0]["strokes"] == [3] * 9


def test_csv_bad_values_marked_low_confidence(tmp_path):
    header = "Name;" + ";".join(str(i) for i in range(1, 10))
    p = write(tmp_path, "d.csv", (header + "\nA B;4;4;abc;4;99;4;4;4;4").encode())
    card = parse_csv(p)["cards"][0]
    assert card["confidence"] == "low" and len(card["notes"]) == 2
    assert card["strokes"][2] is None and card["strokes"][4] is None


def test_csv_without_hole_header_fails(tmp_path):
    p = write(tmp_path, "e.csv", b"foo;bar\n1;2\n")
    with pytest.raises(ParseError):
        parse_csv(p)


def test_csv_skips_par_rows(tmp_path):
    header = "Name;" + ";".join(str(i) for i in range(1, 10))
    body = "Par;" + ";".join(["4"] * 9) + "\nA B;" + ";".join(["5"] * 9)
    p = write(tmp_path, "f.csv", (header + "\n" + body).encode())
    cards = parse_csv(p)["cards"]
    assert [c["player"] for c in cards] == ["A B"]


def test_text_with_out_in_totals():
    front, back = [5, 4, 3, 6, 5, 4, 3, 5, 6], [4, 4, 3, 5, 5, 4, 4, 6, 5]
    line = "Erika Mustermann " + " ".join(map(str, front)) + f" {sum(front)} " + " ".join(
        map(str, back)) + f" {sum(back)} {sum(front) + sum(back)}"
    text = "Par 4 4 3 5 4 4 3 4 5 36 4 4 3 5 4 4 3 5 4 36 72\n" + line
    cards = parse_text(text, "high")["cards"]
    assert len(cards) == 1
    assert cards[0]["strokes"] == front + back and cards[0]["confidence"] == "high"


def test_text_merged_digits_from_ocr_resolved_by_sum():
    cards = parse_text("Peter Lustig54365435641", "low")["cards"]
    assert cards[0]["strokes"] == [5, 4, 3, 6, 5, 4, 3, 5, 6]


def test_text_merged_digits_with_double_digit_hole():
    # 5 4 10 6 5 4 3 5 6 = 48
    cards = parse_text("Peter Lustig 5410654356 48", "low")["cards"]
    assert cards[0]["strokes"] == [5, 4, 10, 6, 5, 4, 3, 5, 6]


def test_text_merged_digits_without_matching_sum_ignored():
    with pytest.raises(ParseError):
        parse_text("Peter Lustig 543654356 99", "low")


def test_text_without_totals_is_low_confidence():
    cards = parse_text("Hans Meier 4 5 4 3 5 4 4 5 6", "high")["cards"]
    assert cards[0]["confidence"] == "low"


def test_pdf_table(tmp_path):
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle

    data = [["Name"] + [str(i) for i in range(1, 10)], ["Anna Schmidt"] + ["4"] * 8 + ["-"]]
    p = tmp_path / "card.pdf"
    doc = SimpleDocTemplate(str(p), pagesize=landscape(A4))
    t = Table(data)
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, "black")]))
    doc.build([t])
    card = parse_pdf(p)["cards"][0]
    assert card["player"] == "Anna Schmidt"
    assert card["strokes"] == [4] * 8 + [None]


def test_pdf_corrupt(tmp_path):
    p = write(tmp_path, "x.pdf", b"%PDF-1.7\n garbage \x00\x01")
    with pytest.raises(ParseError):
        parse_pdf(p)


def test_photo_corrupt(tmp_path):
    p = write(tmp_path, "x.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    with pytest.raises(ParseError):
        parse_photo(p)


@pytest.mark.ocr
def test_photo_ocr_roundtrip(tmp_path):
    """Rendert eine einfache Scorekarte als Bild und liest sie per OCR zurück.

    Echte Handyfotos (schräg, Handschrift, Schatten) sind deutlich schwerer —
    dieser Test beweist nur, dass die Pipeline funktioniert.
    """
    import shutil

    if not shutil.which("tesseract"):
        pytest.skip("tesseract nicht installiert")
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (1800, 300), "white")
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 40)
    d.text((20, 40), "Par 4 4 3 5 4 4 3 4 5 36", fill="black", font=font)
    d.text((20, 140), "Peter Lustig 5 4 3 6 5 4 3 5 6 41", fill="black", font=font)
    p = tmp_path / "card.png"
    img.save(p)
    card = parse_photo(p)["cards"][0]
    assert card["strokes"] == [5, 4, 3, 6, 5, 4, 3, 5, 6]
    assert card["confidence"] == "low"
