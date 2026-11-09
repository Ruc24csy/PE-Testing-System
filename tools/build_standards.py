"""从 2014 年《国家学生体质健康标准》两份 docx 生成内置评分表 JSON（只取大学两列）。

用法: python tools/build_standards.py 单项指标评分表.docx 加分指标评分表.docx > app/standards/gb2014.json
"""
import json
import re
import sys
import zipfile


def read_blocks(path):
    x = zipfile.ZipFile(path).read("word/document.xml").decode()
    out = []
    for m in re.finditer(r"<w:tbl>.*?</w:tbl>|<w:p[ >].*?</w:p>", x, re.S):
        s = m.group(0)
        if s.startswith("<w:tbl>"):
            rows = []
            for tr in re.findall(r"<w:tr[ >].*?</w:tr>", s, re.S):
                rows.append(["".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", tc))
                             for tc in re.findall(r"<w:tc>.*?</w:tc>", tr, re.S)])
            out.append(("table", rows))
        else:
            t = "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", s)).strip()
            if t:
                out.append(("p", t))
    return out


def to_seconds(v):
    v = v.strip().replace("″", '"').replace("′", "'")
    m = re.fullmatch(r"(\d+)'(\d+(?:\.\d+)?)\"?", v)
    if m:
        return int(m.group(1)) * 60 + float(m.group(2))
    m = re.fullmatch(r"-?(\d+(?:\.\d+)?)\"?", v)
    if m:
        return float(m.group(1))
    return float(v)


ITEMS = {  # 标题关键字 -> (项目代码, 中文名, 方向, 单位)
    "肺活量": ("vital_capacity", "肺活量", "high", "毫升"),
    "50米跑": ("run50", "50米跑", "low", "秒"),
    "坐位体前屈": ("sit_reach", "坐位体前屈", "high", "厘米"),
    "立定跳远": ("long_jump", "立定跳远", "high", "厘米"),
    "引体向上": ("pull_up", "引体向上", "high", "次"),
    "仰卧起坐": ("sit_up", "一分钟仰卧起坐", "high", "次"),
    "1000米跑": ("run1000", "1000米跑", "low", "秒"),
    "800米跑": ("run800", "800米跑", "low", "秒"),
}


def main(single, bonus):
    data = {"version": "GB2014", "name": "2014年《国家学生体质健康标准》", "items": {}}
    title = None
    for kind, val in read_blocks(single):
        if kind == "p":
            title = val
            continue
        if not title or "体重指数" in title or "跳绳" in title:
            continue
        gender = "M" if "男生" in title else "F"
        if "耐力跑" in title:
            key = "1000米跑" if gender == "M" else "800米跑"
        elif "引体向上" in title:
            key = "引体向上"
        else:
            key = next(k for k in ITEMS if k in title)
        code, name, direction, unit = ITEMS[key]
        head = val[0]
        i12, i34 = head.index("大一大二"), head.index("大三大四")
        levels = []
        for r in val[1:]:
            score = int(r[1])
            if not r[i12].strip():  # 男生引体向上部分档位在原表中为空，该档不可达
                continue
            conv = (lambda s: to_seconds(s)) if unit == "秒" else float
            levels.append({"score": score, "12": conv(r[i12]), "34": conv(r[i34])})
        it = data["items"].setdefault(code, {"name": name, "direction": direction, "unit": unit, "tables": {}})
        it["tables"][gender] = levels
    title = None
    for kind, val in read_blocks(bonus):
        if kind == "p":
            title = val
            continue
        if "跳绳" in title:
            continue
        key = next(k for k in ITEMS if k in title)
        code = ITEMS[key][0]
        gender = "M" if "男生" in title else "F"
        head = val[0]
        i12, i34 = head.index("大一大二"), head.index("大三大四")
        rows = [{"bonus": int(r[0]), "12": to_seconds(r[i12]), "34": to_seconds(r[i34])} for r in val[1:]]
        rows.sort(key=lambda r: r["bonus"])
        data["items"][code].setdefault("bonus", {})[gender] = rows
    json.dump(data, sys.stdout, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
