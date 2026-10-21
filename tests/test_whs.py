from decimal import Decimal

import pytest

from app.scoring.plausibility import check_result, check_strokes, check_tee
from app.scoring.whs import (
    Hole,
    TeeRating,
    course_handicap,
    evaluate_round,
    playing_handicap,
    stableford_points,
    strokes_received,
)

PARS_18 = [4, 4, 3, 5, 4, 4, 3, 4, 5, 4, 3, 4, 5, 4, 4, 3, 5, 4]  # Par 72
SI_18 = [7, 11, 15, 1, 3, 13, 17, 9, 5, 8, 16, 2, 6, 12, 4, 18, 10, 14]
HOLES_18 = [Hole(i + 1, PARS_18[i], SI_18[i]) for i in range(18)]


def test_course_handicap_18():
    tee = TeeRating(Decimal("71.2"), 128, 72, 18)
    # 18.4 × 128/113 − 0.8 = 20.04 → 20
    assert course_handicap(Decimal("18.4"), tee) == 20


def test_course_handicap_9_uses_half_index():
    tee = TeeRating(Decimal("35.4"), 125, 36, 9)
    # 9.2 × 125/113 − 0.6 = 9.58 → 10
    assert course_handicap(Decimal("18.4"), tee) == 10


def test_course_handicap_plus_rounding_half_up_numerically():
    tee = TeeRating(Decimal("72.0"), 113, 72, 18)
    assert course_handicap(Decimal("-2.5"), tee) == -2
    assert course_handicap(Decimal("2.5"), tee) == 3


def test_playing_handicap_allowance():
    assert playing_handicap(20, 95) == 19
    assert playing_handicap(20, 100) == 20
    with pytest.raises(ValueError):
        playing_handicap(20, 0)


def test_strokes_received_distribution_19():
    r = strokes_received(HOLES_18, 19)
    assert sum(r.values()) == 19
    assert r[4] == 2  # SI 1
    assert all(v >= 1 for v in r.values())


def test_strokes_received_over_36():
    r = strokes_received(HOLES_18, 40)
    assert sum(r.values()) == 40
    assert r[4] == 3 and r[12] == 3 and r[5] == 3  # SI 1,2,3 → 3 Schläge
    assert r[16] == 2  # SI 18


def test_strokes_received_plus_handicap_gives_back_on_easiest():
    r = strokes_received(HOLES_18, -2)
    assert r[16] == -1 and r[7] == -1  # SI 18 und 17
    assert sum(r.values()) == -2


def test_strokes_received_nine_holes_of_eighteen_ranks_by_si():
    back9 = HOLES_18[9:]  # SI 8,16,2,6,12,4,18,10,14
    r = strokes_received(back9, 3)
    # Rang 1..3 = SI 2, 4, 6 → Löcher 12, 15, 13
    assert {n for n, v in r.items() if v == 1} == {12, 15, 13}


def test_stableford_points():
    assert stableford_points(4, 4) == 2
    assert stableford_points(4, 3) == 3
    assert stableford_points(4, 7) == 0
    assert stableford_points(4, 5, 1) == 2
    assert stableford_points(4, None, 2) == 0


def test_evaluate_round_with_struck_hole():
    strokes = {h.number: h.par + 1 for h in HOLES_18}  # Bogey überall
    strokes[4] = None  # gestrichen
    res = evaluate_round(HOLES_18, strokes, 18)
    assert not res.complete and res.gross_strokes is None
    assert res.net_stableford == 17 * 2  # Bogey + 1 Schlag = Netto-Par
    assert res.gross_stableford == 17


def test_evaluate_round_mismatch_raises():
    with pytest.raises(ValueError):
        evaluate_round(HOLES_18, {1: 4}, 10)


def test_check_tee_valid_and_invalid():
    assert check_tee(HOLES_18, Decimal("71.2"), 128, 72, 18).ok
    bad = check_tee(HOLES_18[:17], Decimal("71.2"), 200, 72, 18)
    assert not bad.ok and len(bad.errors) >= 2
    dup = [Hole(h.number, h.par, 1) for h in HOLES_18]
    assert any("doppelt" in e for e in check_tee(dup, Decimal("71.2"), 128, 72, 18).errors)


def test_check_strokes_errors_and_warnings():
    s = {h.number: h.par for h in HOLES_18}
    s[1], s[4] = 0, 1  # unmöglich / Hole-in-one auf Par 5
    c = check_strokes(HOLES_18, s)
    assert any("Loch 1" in e for e in c.errors)
    assert any("Hole-in-one" in w for w in c.warnings)
    c2 = check_strokes(HOLES_18, {1: 4})
    assert any("Fehlende" in e for e in c2.errors)
    c3 = check_strokes(HOLES_18, {h.number: True for h in HOLES_18})  # bool ist kein Score
    assert not c3.ok


def test_check_result_flags_very_high_net():
    strokes = {h.number: h.par - 1 for h in HOLES_18}
    res = evaluate_round(HOLES_18, strokes, 20)
    assert check_result(res).warnings
