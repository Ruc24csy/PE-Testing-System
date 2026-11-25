"""查表换算引擎：原始成绩解析、单项得分、加分、折算与舍入。

口径见需求文档二（四）。所有函数都是纯函数，便于单测。
"""
from __future__ import annotations

import json
import re
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from pathlib import Path

STANDARDS_DIR = Path(__file__).parent / "standards"
DEFAULT_VERSION = "GB2014"

# 体测项目：项目代码 -> 适用性别（None 表示男女都有）
ITEM_GENDER = {
    "run1000": "M", "run800": "F", "run50": None, "long_jump": None,
    "pull_up": "M", "sit_up": "F", "vital_capacity": None, "sit_reach": None,
}
TIME_ITEMS = {"run1000", "run800", "run50"}
BONUS_ITEMS = {"run1000", "run800", "pull_up", "sit_up"}  # 加分只适用于耐力跑与引体向上/仰卧起坐

STATUSES = ["正常", "免测", "缓测", "课上未测", "违纪"]
ZERO_STATUSES = {"课上未测", "违纪"}
# 停用的旧写法：老模板、老数据里还会出现，读入时换成现在的状态（数据库在启动时迁移，见 models.init_db）
LEGACY_STATUSES = {"缺考": "课上未测", "作弊": "违纪"}


def normalize_status(text: str) -> str:
    return LEGACY_STATUSES.get(text, text)
# 课程成绩特殊情况：非“正常”时总成绩不算，显示状态文字，运动技能和平时成绩可以不填
COURSE_STATUSES = ["正常", "缓考", "缺考", "退课"]


def level_name(score: float | None) -> str | None:
    if score is None:
        return None
    if score >= 90:
        return "优秀"
    if score >= 80:
        return "良好"
    if score >= 60:
        return "及格"
    return "不及格"


@lru_cache
def load_standard(version: str = DEFAULT_VERSION) -> dict:
    return json.loads((STANDARDS_DIR / f"{version.lower()}.json").read_text("utf-8"))


def d2(x: float) -> float:
    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def d1(x: float) -> float:
    return float(Decimal(str(x)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


# ---------------------------------------------------------------- 输入解析

_FW = str.maketrans("０１２３４５６７８９．：＇＂，", "0123456789.:'\",")


def clean(text) -> str:
    s = str(text).translate(_FW).strip()
    s = s.replace("’", "'").replace("‘", "'").replace("′", "'").replace("“", '"').replace("”", '"').replace("″", '"')
    return re.sub(r"\s+", "", s)


class ParseError(ValueError):
    pass


def parse_time(text, item: str = "run1000") -> float:
    """把各种写法的时间统一成秒（保留一位小数）。

    支持 3'40"、3:40、3分40秒、340（分秒连写）、220秒、3'40.5"；50米跑直接写 7.3。
    """
    s = clean(text)
    if not s:
        raise ParseError("空值")
    examples = "示例：3'40\"、3:40、3分40秒、340、220秒" if item != "run50" else "示例：7.3、7.3秒"
    m = re.fullmatch(r"(\d+)(?:'|:|分)(\d+(?:\.\d+)?)(?:\"|秒)?", s)
    if m:
        mi, se = int(m.group(1)), float(m.group(2))
        if se >= 60:
            raise ParseError(f"秒数不能大于等于60。{examples}")
        return round(mi * 60 + se, 1)
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(秒|s|S|\")", s)
    if m:
        return round(float(m.group(1)), 1)
    m = re.fullmatch(r"\d+(?:\.\d+)?", s)
    if m:
        v = float(s)
        if item == "run50":
            return round(v, 1)
        # 耐力跑：三位或四位整数按“分秒连写”理解（340 = 3分40秒）
        if re.fullmatch(r"\d{3,4}", s) and int(s[-2:]) < 60:
            return float(int(s[:-2]) * 60 + int(s[-2:]))
        raise ParseError(f"无法判断这个数字是分秒还是秒数，请写成带单位的形式。{examples}")
    raise ParseError(f"无法识别的时间格式。{examples}")


def parse_number(text, unit_suffixes=("cm", "厘米", "次", "个")) -> float:
    s = clean(text).lower()
    for suf in unit_suffixes:
        if s.endswith(suf):
            s = s[: -len(suf)]
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", s):
        raise ParseError("请输入数字，例如 235 或 235.5cm")
    return float(s)


def parse_raw(item: str, text) -> float:
    if item in TIME_ITEMS:
        return parse_time(text, item)
    return parse_number(text)


def fmt_time(sec: float | None) -> str:
    if sec is None:
        return ""
    sec = round(sec, 1)
    m = int(sec // 60)
    s = round(sec - m * 60, 1)
    if s == int(s):
        return f"{m}'{int(s):02d}\""
    return f"{m}'{s:04.1f}\""


def fmt_raw(item: str, v: float | None) -> str:
    if v is None:
        return ""
    if item == "run50":
        return f"{v:.1f}"
    if item in TIME_ITEMS:
        return fmt_time(v)
    return f"{v:g}"


def score_parse_simple(text, lo: float, hi: float, name: str) -> float:
    s = clean(text)
    s = s.rstrip("分")
    if not re.fullmatch(r"\d+(?:\.\d+)?", s):
        raise ParseError(f"{name}请输入 {lo:g}—{hi:g} 的数字")
    v = float(s)
    if v < lo or v > hi:
        raise ParseError(f"{name}应在 {lo:g}—{hi:g} 之间")
    if (v * 2) != int(v * 2):
        raise ParseError(f"{name}只支持整数或 0.5 分步进")
    return v


# ---------------------------------------------------------------- 换算


def item_score(item: str, gender: str, band: str, raw: float, version: str = DEFAULT_VERSION) -> dict:
    """单项查表。返回 score（不含加分）、bonus、total、档位说明与距上一档差距。"""
    std = load_standard(version)["items"][item]
    table = std["tables"][gender]
    high = std["direction"] == "high"
    unit = std["unit"]
    val = round(raw, 1) if item in TIME_ITEMS else raw

    def reached(th):
        return val >= th if high else val <= th

    hit = None
    for i, lv in enumerate(table):  # 表按分值从高到低
        if reached(lv[band]):
            hit = i
            break
    res = {"item": item, "raw": val, "band": band, "gender": gender, "version": version,
           "score": 0.0, "bonus": 0.0, "total": 0.0, "threshold": None, "level_score": None,
           "next_gap": None, "next_score": None, "below_min": False, "unit": unit}
    if hit is None:
        res["below_min"] = True
        lowest = table[-1]
        res["next_gap"] = round(abs(val - lowest[band]), 1)
        res["next_score"] = lowest["score"]
        return res
    lv = table[hit]
    res.update(score=float(lv["score"]), threshold=lv[band], level_score=lv["score"])
    if hit > 0:
        up = table[hit - 1]
        res["next_gap"] = round(abs(up[band] - val), 1)
        res["next_score"] = up["score"]
    if hit == 0 and item in BONUS_ITEMS and "bonus" in std:
        diff = round((val - lv[band]) if high else (lv[band] - val), 1)
        bonus = 0
        for b in std["bonus"][gender]:  # 升序
            if diff >= b[band]:
                bonus = b["bonus"]
        bonus = min(bonus, 10)
        res["bonus"] = float(bonus)
        res["bonus_diff"] = diff
        nxt = [b for b in std["bonus"][gender] if b[band] > diff]
        if nxt:
            res["next_gap"] = round(nxt[0][band] - diff, 1)
            res["next_score"] = f"加分+{nxt[0]['bonus']}"
        else:
            res["next_gap"] = None
            res["next_score"] = None
    res["total"] = res["score"] + res["bonus"]
    return res


def describe(res: dict) -> str:
    """单元格下方的浅色提示：命中档位与距上一档差距。"""
    if res is None:
        return ""
    unit = "秒" if res["item"] in TIME_ITEMS else res["unit"]
    if res["below_min"]:
        return f"低于最低档，距10分档差{res['next_gap']:g}{unit}"
    txt = f"{res['level_score']}分档"
    if res["bonus"]:
        txt += f"，含加分+{res['bonus']:g}分"
    if res["next_gap"] is not None:
        nxt = res["next_score"]
        label = nxt if isinstance(nxt, str) else f"{nxt}分档"
        txt += f"，距{label}差{res['next_gap']:g}{unit}"
    return txt


def compute_row(*, gender: str | None, band: str | None, config: dict, values: dict,
                version: str = DEFAULT_VERSION) -> dict:
    """计算一名学生的全部自动列。

    config: {"slots": [{"key": "a", "items": {"M": "run1000", "F": "run800"}, "weight": 0.2}, ...],
             "skill_weight": 0.6, "regular_weight": 0.1}
    values: {"a_raw": float|None, "a_status": str, "b_raw":..., "b_status":..., "skill":..., "regular":...}
    """
    out = {"slots": {}, "subtotal": None, "total": None, "issues": [], "complete": True}
    sub = Decimal("0")
    # 体测小计只在参与它的每一项都“定了”时才出数：填了成绩，或标了免测／课上未测／违纪等特殊情况。
    # 有一项还空着、或标了缓测（按缺项处理），整个小计就不算，避免只加已填的那几项得出偏小的数。
    sub_ready = True
    for slot in config["slots"]:
        k = slot["key"]
        item = slot["items"].get(gender) if gender else None
        status = values.get(f"{k}_status") or "正常"
        raw = values.get(f"{k}_raw")
        info = {"item": item, "status": status, "result": None, "score": None, "folded": None,
                "hint": "", "display": None}
        if status == "免测":
            info["display"] = "免测"
        elif status == "缓测":
            info["display"] = "缓测（缺项）"
            out["complete"] = False
            sub_ready = False
        elif status in ZERO_STATUSES:
            info["score"], info["folded"], info["display"] = 0.0, 0.0, status
        elif raw is None:
            out["complete"] = False
            sub_ready = False
        elif not gender or not band:
            out["complete"] = False
            sub_ready = False
            info["hint"] = "性别或标准档待确认，暂不换算"
        else:
            r = item_score(item, gender, band, raw, version)
            info["result"] = r
            info["score"] = r["total"]
            info["folded"] = d2(r["total"] * slot["weight"])
            info["hint"] = describe(r)
            sub += Decimal(str(info["folded"]))
            if r["below_min"]:
                out["issues"].append(f"{slot.get('label', k)}低于最低档")
        out["slots"][k] = info
    if sub_ready and config["slots"]:
        out["subtotal"] = d2(float(sub))
    skill, regular = values.get("skill"), values.get("regular")
    course = values.get("course_status") or "正常"
    out["course_status"] = course
    if course != "正常":
        # 总成绩不算；“录完”只看两项体测
        return out
    if skill is None or regular is None:
        out["complete"] = False
    # 总成绩同样要四项都齐（体测小计＋运动技能＋平时成绩）才出数
    if sub_ready and config["slots"] and skill is not None and regular is not None:
        out["total"] = d1((out["subtotal"] or 0) + skill + regular)
    return out


def default_config(season: str) -> dict:
    """新建学期时带入的默认体测项目配置。"""
    if season == "spring":
        slots = [
            {"key": "a", "label": "50米跑", "items": {"M": "run50", "F": "run50"}, "weight": 0.2},
            {"key": "b", "label": "引体向上/仰卧起坐", "items": {"M": "pull_up", "F": "sit_up"}, "weight": 0.1},
        ]
    else:
        slots = [
            {"key": "a", "label": "耐力跑", "items": {"M": "run1000", "F": "run800"}, "weight": 0.2},
            {"key": "b", "label": "立定跳远", "items": {"M": "long_jump", "F": "long_jump"}, "weight": 0.1},
        ]
    return {"slots": slots, "skill_weight": 0.6, "regular_weight": 0.1}


def item_label(item: str | None, version: str = DEFAULT_VERSION) -> str:
    if not item:
        return ""
    return load_standard(version)["items"][item]["name"]
