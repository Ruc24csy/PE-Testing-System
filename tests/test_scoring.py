import pytest

from app import scoring as s


@pytest.mark.parametrize("text,sec", [
    ("3'40\"", 220), ("3:40", 220), ("3分40秒", 220), ("340", 220), ("220秒", 220),
    ("３：４０", 220), (" 3 ' 40.5 \"", 220.5), ("3′40″", 220), ("4'05", 245),
])
def test_parse_time(text, sec):
    assert s.parse_time(text) == sec


@pytest.mark.parametrize("text", ["abc", "3:75", "12.5", ""])
def test_parse_time_rejects(text):
    with pytest.raises(s.ParseError):
        s.parse_time(text)


def test_parse_number():
    assert s.parse_number("235cm") == 235
    assert s.parse_number("２３５.５ 厘米") == 235.5


def test_fmt_time():
    assert s.fmt_time(220) == "3'40\""
    assert s.fmt_time(245.5) == "4'05.5\""


def test_doc_example_long_jump_bands():
    # 需求文档：男生立定跳远273厘米，大一大二100分，大三大四95分
    assert s.item_score("long_jump", "M", "12", 273)["score"] == 100
    assert s.item_score("long_jump", "M", "34", 273)["score"] == 95


def test_low_is_better_and_boundaries():
    r = s.item_score("run1000", "M", "12", 197)  # 3'17" 正好是100分档
    assert r["score"] == 100 and r["bonus"] == 0
    assert s.item_score("run1000", "M", "12", 197.1)["score"] == 95
    assert s.item_score("run1000", "M", "12", 372)["score"] == 10  # 6'12"
    r = s.item_score("run1000", "M", "12", 372.1)
    assert r["score"] == 0 and r["below_min"]


def test_bonus_running():
    # 男生大一大二 100分档 3'17"（197秒），快 27 秒 → ≥26 秒对应加 7 分
    r = s.item_score("run1000", "M", "12", 170)
    assert (r["score"], r["bonus"], r["total"]) == (100, 7, 107)
    assert s.item_score("run1000", "M", "12", 100)["bonus"] == 10  # 上限10分
    assert s.item_score("run800", "F", "12", 198 - 50)["bonus"] == 10


def test_no_bonus_for_long_jump():
    assert s.item_score("long_jump", "M", "12", 320)["bonus"] == 0


def test_pull_up_bonus_and_missing_levels():
    r = s.item_score("pull_up", "M", "12", 22)  # 100分档19个，多3个 → +3
    assert (r["score"], r["bonus"]) == (100, 3)
    assert s.item_score("pull_up", "M", "12", 14)["score"] == 76  # 男生引体向上没有78分档
    assert s.item_score("pull_up", "M", "12", 11)["score"] == 64


def test_compute_row_totals_and_rounding():
    cfg = s.default_config("fall")
    out = s.compute_row(gender="M", band="12", config=cfg,
                        values={"a_raw": 170, "b_raw": 273, "skill": 55.5, "regular": 9})
    assert out["slots"]["a"]["folded"] == 21.4  # 107 × 20%
    assert out["slots"]["b"]["folded"] == 10.0
    assert out["subtotal"] == 31.4
    assert out["total"] == 95.9
    assert out["complete"]
    best = s.compute_row(gender="M", band="12", config=cfg, values={"a_raw": 100, "b_raw": 300, "skill": 60, "regular": 10})
    assert best["subtotal"] == 32.0


def test_statuses():
    cfg = s.default_config("fall")
    out = s.compute_row(gender="F", band="34", config=cfg,
                        values={"a_status": "缺考", "b_status": "免测", "skill": 50, "regular": 8})
    assert out["slots"]["a"]["folded"] == 0
    assert out["slots"]["b"]["display"] == "免测" and out["slots"]["b"]["folded"] is None
    assert out["total"] == 58.0
    out = s.compute_row(gender="F", band="34", config=cfg, values={"a_status": "缓测", "b_raw": 200})
    assert out["slots"]["a"]["display"] == "缓测（缺项）" and not out["complete"]


def test_empty_is_not_zero():
    out = s.compute_row(gender="M", band="12", config=s.default_config("fall"), values={})
    assert out["subtotal"] is None and out["total"] is None and not out["complete"]


def test_no_gender_no_conversion():
    out = s.compute_row(gender=None, band="12", config=s.default_config("fall"), values={"a_raw": 200})
    assert out["slots"]["a"]["score"] is None


def test_spring_config():
    cfg = s.default_config("spring")
    out = s.compute_row(gender="F", band="12", config=cfg, values={"a_raw": 7.5, "b_raw": 56})
    assert out["slots"]["a"]["item"] == "run50" and out["slots"]["b"]["item"] == "sit_up"
    assert out["slots"]["b"]["score"] == 100


def test_score_inputs():
    assert s.score_parse_simple("55.5", 0, 60, "x") == 55.5
    for bad in ("61", "55.3", "-1", "abc"):
        with pytest.raises(s.ParseError):
            s.score_parse_simple(bad, 0, 60, "x")
