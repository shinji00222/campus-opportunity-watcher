"""分类与过滤规则。内置关键词适用于大多数高校，学校配置可在此基础上追加。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from campus_watcher.models import CATEGORIES

INTERNSHIP_MAX_AGE = timedelta(days=60)

DEFAULT_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "竞赛": (
        "竞赛", "大赛", "比赛", "挑战杯", "大挑", "小挑", "创业计划竞赛",
        "课外学术科技作品", "蓝桥杯", "创新创业", "中国国际大学生创新大赛", "互联网+",
        "数学建模", "大学生数学竞赛", "电子设计", "大学生工程训练", "服务外包创新创业",
        "网络安全创新大赛", "计算机设计大赛", "外研社·国才杯",
        "词达人杯", "程序设计", "建模竞赛", "学科竞赛", "校赛", "选拔赛", "作品征集",
        "ACM-ICPC", "ICPC", "CCPC", "RoboMaster", "RoboCup", "RAICOM", "MathorCup", "CTF",
    ),
    "出国": (
        "交换生", "交流项目", "海外交流", "境外交流", "出国交流", "联合培养",
        "访学", "留学项目", "国际暑期学校", "海外暑期学校", "国际冬季学校",
        "海外冬季学校", "国际项目", "海外项目",
        "交流生", "研修项目", "名校探访", "国际寒假项目", "海外寒假项目",
        "国际暑期项目", "海外暑期项目", "短期海外游学",
    ),
    "实习": ("实习", "实习生", "暑期实习", "寒假实习", "实践项目"),
}
DEFAULT_IGNORE_WORDS = (
    "教师教学创新大赛", "教职工", "体育比赛成绩", "创新创业教育",
    "竞赛场地", "就餐地点", "住宿信息", "学位留学项目", "选拔名单公示",
)
NON_ACTIONABLE_COMPETITION_MARKERS = (
    "成绩公示", "成绩公布", "获奖名单", "获奖结果", "获奖公示", "拟获奖",
    "名单公示", "拟入驻名单", "喜获", "荣获", "获佳绩", "再捧", "夺冠",
    "圆满落幕", "圆满结束", "赛事回顾",
)
_YEAR = re.compile(r"(?<!\d)(20\d{2})(?!\d)")


@dataclass
class Rules:
    category_keywords: dict[str, tuple[str, ...]] = field(
        default_factory=lambda: dict(DEFAULT_CATEGORY_KEYWORDS)
    )
    ignore_words: tuple[str, ...] = DEFAULT_IGNORE_WORDS

    @classmethod
    def with_extras(cls, extra_keywords: dict[str, list[str]] | None = None,
                    extra_ignore: list[str] | None = None) -> "Rules":
        keywords = {
            category: tuple(dict.fromkeys(DEFAULT_CATEGORY_KEYWORDS[category] + tuple((extra_keywords or {}).get(category, ()))))
            for category in CATEGORIES
        }
        ignore = tuple(dict.fromkeys(DEFAULT_IGNORE_WORDS + tuple(extra_ignore or ())))
        return cls(keywords, ignore)

    @property
    def all_keywords(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(k for values in self.category_keywords.values() for k in values))

    def classify(self, title: str, source_default: str | None = None) -> str | None:
        # 国际组织实习等交叉项目优先归到“实习”。
        normalized = title.casefold()
        if re.search(r"\bintern(?:ship)?\b", normalized):
            return "实习"
        if "联合培养基地" in normalized:
            return None
        for category in ("实习", "出国", "竞赛"):
            if any(k.casefold() in normalized for k in self.category_keywords[category]):
                return category
        if source_default and any(k.casefold() in normalized for k in self.all_keywords):
            return source_default
        return None

    def is_ignored(self, title: str) -> bool:
        return any(word in title for word in self.ignore_words)

    def is_irrelevant(self, notice: dict, today: date | None = None) -> bool:
        today = today or date.today()
        return (
            self.is_ignored(str(notice.get("title", "")))
            or is_stale_competition(notice, today)
            or is_stale_abroad(notice, today)
            or is_stale_internship(notice, today)
        )


def is_stale_competition(notice: dict, today: date | None = None) -> bool:
    today = today or date.today()
    if notice.get("category") != "竞赛":
        return False
    title = str(notice.get("title", ""))
    if any(marker in title for marker in NON_ACTIONABLE_COMPETITION_MARKERS):
        return True
    years = [int(value) for value in _YEAR.findall(title)]
    return bool(years and max(years) < today.year)


def is_stale_abroad(notice: dict, today: date | None = None) -> bool:
    today = today or date.today()
    if notice.get("category") != "出国":
        return False
    title = str(notice.get("title", ""))
    years = [int(value) for value in _YEAR.findall(title)]
    if not years:
        return False
    latest_year = max(years)
    if latest_year < today.year:
        return True
    return latest_year == today.year and today.month >= 9 and "暑期" in title


def is_stale_internship(notice: dict, today: date | None = None) -> bool:
    today = today or date.today()
    if notice.get("category") != "实习":
        return False
    title = str(notice.get("title", ""))
    if "训练营" in title and not any(word in title for word in ("实习", "实习生", "实践项目")):
        return True
    published = str(notice.get("date", ""))[:10]
    if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", published):
        published = str(notice.get("first_seen", ""))[:10]
    try:
        published_at = date.fromisoformat(published)
    except ValueError:
        return False
    return today - published_at > INTERNSHIP_MAX_AGE
