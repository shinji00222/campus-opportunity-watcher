from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

CATEGORIES = ("竞赛", "实习", "出国")


@dataclass
class Notice:
    id: str
    title: str
    url: str
    source: str
    date: str = ""
    first_seen: str = ""
    summary: str = ""
    category: str = "竞赛"
    detail_text: str = ""
    hard_conditions: list[str] = field(default_factory=list)
    lifecycle_stage: str = ""
    detail_checked_at: str = ""
    detail_attempted_at: str = ""
    # 详情抓取方式："page" 通用正文、"ncss_job" 国家就业平台职位页、"none" 列表已含全部信息。
    detail: str = "page"


class PartialSourceError(ValueError):
    """来源部分成功：保留已解析的通知，同时报告失败的分页。"""

    def __init__(self, notices: list[Notice], message: str):
        self.notices = notices
        super().__init__(message)


def make_id(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:20]
