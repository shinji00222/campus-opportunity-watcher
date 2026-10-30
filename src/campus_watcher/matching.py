"""按本机个人档案给通知打匹配分。判断只基于网页公开文字，仅作参考。"""
from __future__ import annotations

import re

PROFILE_FIELDS = (
    ("education", "当前学历", "本科生"),
    ("enrollment_year", "本科入学年份", ""),
    ("college", "学院", ""),
    ("major", "专业", ""),
    ("gpa", "GPA（满分4.0）", ""),
    ("cet4", "英语四级分数", ""),
    ("cet6", "英语六级分数", ""),
    ("ielts", "雅思分数", ""),
    ("toefl", "托福分数", ""),
    ("interests", "意向关键词", "人工智能,互联网,海外交流"),
)

_NUMERIC_RULES = (
    (r"(?:四级|CET[- ]?4).{0,12}?(\d{3})", "cet4", "英语四级"),
    (r"(?:六级|CET[- ]?6).{0,12}?(\d{3})", "cet6", "英语六级"),
    (r"(?:雅思|IELTS).{0,12}?(\d(?:\.\d)?)", "ielts", "雅思"),
    (r"(?:托福|TOEFL).{0,12}?(\d{2,3})", "toefl", "托福"),
    (r"GPA.{0,12}?(\d(?:\.\d)?)", "gpa", "GPA"),
)


def interest_keywords(profile: dict) -> list[str]:
    return [x.strip().lower() for x in re.split(r"[,，、;；]", profile.get("interests", "")) if x.strip()]


def count_keyword(word: str, text: str) -> int:
    """数关键词出现次数（都按小写）。英文词只算完整单词，免得 "ai" 命中 unpaid、maintain。"""
    if re.fullmatch(r"[a-z0-9+#.\- ]+", word):
        return len(re.findall(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", text))
    return text.count(word)


def evaluate_notice(notice: dict, profile: dict) -> tuple[int, list[tuple[str, bool, bool]], bool]:
    """返回 (匹配分, [(条件, 是否未达到, 是否硬条件)], 是否存在硬条件不符)。"""
    if not profile:
        return 50, [("尚未填写个人档案，暂时无法精确匹配", True, False)], False
    content = f"{notice.get('title', '')} {notice.get('detail_text') or notice.get('summary', '')}"
    conditions: list[tuple[str, bool, bool]] = []
    hard_fail = False
    score = 70
    education = profile.get("education", "")

    if re.search(r"仅限.{0,4}(硕士|博士|研究生)|参赛对象.{0,12}研究生", content):
        failed = education == "本科生"
        conditions.append(("要求研究生", failed, True))
        hard_fail |= failed
    if re.search(r"仅限.{0,4}本科|参赛对象.{0,12}本科生", content):
        failed = education not in ("本科生", "")
        conditions.append(("要求本科生", failed, True))
        hard_fail |= failed

    years = set(re.findall(r"(20\d{2})届", content))
    if years:
        expected_graduation = ""
        try:
            expected_graduation = str(int(str(profile.get("enrollment_year", "")).strip()) + 4)
        except ValueError:
            pass
        failed = bool(expected_graduation) and expected_graduation not in years
        conditions.append((
            f"面向 {'/'.join(sorted(years))} 届（按本科入学年份推算：{expected_graduation or '未填写'}）", failed, True
        ))
        hard_fail |= failed

    for pattern, key, label in _NUMERIC_RULES:
        match = re.search(pattern, content, re.I)
        if match:
            required = float(match.group(1))
            try:
                actual = float(profile.get(key) or 0)
            except ValueError:
                actual = 0
            conditions.append((f"{label}要求 ≥ {required:g}（你的档案：{actual:g}）", actual < required, False))

    matched = [x for x in interest_keywords(profile) if count_keyword(x, content.lower())]
    if matched:
        score += min(20, len(matched) * 5)
        conditions.append((f"符合意向：{'、'.join(matched)}", False, False))

    score -= 10 * sum(1 for _, unmet, hard in conditions if unmet and not hard)
    if hard_fail:
        score = min(score, 20)
    elif not any(unmet for _, unmet, _ in conditions):
        score += 10
    return max(0, min(100, score)), conditions, hard_fail
