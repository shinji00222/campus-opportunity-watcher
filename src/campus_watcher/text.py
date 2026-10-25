"""HTML 文本提取、详情条件提取与报名截止日期识别。与具体学校无关。"""
from __future__ import annotations

import html as html_lib
import re
from datetime import date
from html.parser import HTMLParser


def squash(value) -> str:
    return " ".join(html_lib.unescape(str(value or "")).split())


class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self.href: str | None = None
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            self.href = dict(attrs).get("href")
            self.parts = []

    def handle_data(self, data):
        if self.href is not None:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self.href is not None:
            text = re.sub(r"\s+", " ", "".join(self.parts)).strip()
            if text:
                self.links.append((self.href, text))
            self.href = None
            self.parts = []


class ParagraphParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_p = 0
        self.skip = 0
        self.parts: list[str] = []
        self.paragraphs: list[str] = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in ("script", "style", "nav", "footer"):
            self.skip += 1
        if tag == "p" and not self.skip:
            self.in_p += 1
            self.parts = []

    def handle_data(self, data):
        if self.in_p and not self.skip:
            self.parts.append(data)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "p" and self.in_p:
            text = re.sub(r"\s+", " ", "".join(self.parts)).strip()
            if text:
                self.paragraphs.append(text)
            self.in_p -= 1
            self.parts = []
        if tag in ("script", "style", "nav", "footer") and self.skip:
            self.skip -= 1


class VisibleTextParser(HTMLParser):
    """读取非段落布局页面的可见文字。"""

    def __init__(self):
        super().__init__()
        self.skip = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() in ("script", "style", "noscript"):
            self.skip += 1

    def handle_endtag(self, tag):
        if tag.lower() in ("script", "style", "noscript") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            clean = re.sub(r"\s+", " ", data).strip()
            if clean:
                self.parts.append(clean)


def links(html: str) -> list[tuple[str, str]]:
    parser = LinkParser()
    parser.feed(html)
    return parser.links


def visible_text(html: str) -> str:
    parser = VisibleTextParser()
    parser.feed(html)
    return " ".join(parser.parts)


CONDITION_MARKERS = (
    "参赛对象", "报名条件", "申请条件", "申请资格", "资格要求", "招募对象", "选拔条件",
    "报名要求", "申报条件", "基本要求", "任职要求", "岗位要求", "专业要求", "语言要求",
    "成绩要求", "报名资格", "申请人须", "申请者须", "应聘要求", "限本科", "限研究生",
    "qualifications", "requirements", "eligibility", "education", "experience",
    "languages", "competencies", "skills", "enrolled in", "currently pursuing",
    "applicants must", "must be enrolled", "minimum education", "degree in",
)
# 博达站群等高校 CMS 的正文由脚本加载，静态 HTML 里只剩空的 section 占位。
_DYNAMIC_CONTENT_PLACEHOLDER = re.compile(
    r'<div\b[^>]*class=["\'][^"\']*\baContent\b[^"\']*["\'][^>]*>'
    r'\s*<section\b[^>]*id=["\']content\d+["\'][^>]*>\s*</section>',
    re.I,
)


def extract_detail_data(html: str, title: str) -> tuple[str, str, list[str]]:
    if _DYNAMIC_CONTENT_PLACEHOLDER.search(html):
        return "原通知详情由动态区域加载，当前未能读取正文。", "", []
    parser = ParagraphParser()
    parser.feed(html)
    candidates = []
    for paragraph in parser.paragraphs:
        clean = paragraph.replace(title, "").strip(" ：:，,。")
        if len(clean) < 25:
            continue
        if any(x in clean for x in ("版权所有", "当前位置", "关闭窗口", "浏览次数")):
            continue
        candidates.append(clean)
    if not candidates:
        return "网站暂未提供可提取的简介，请打开原通知查看详情。", "", []
    detail_text = "\n".join(candidates)[:12000]
    summary = " ".join(candidates[:2])
    summary = summary[:220] + ("……" if len(summary) > 220 else "")

    def has_condition_marker(value: str) -> bool:
        normalized = value.casefold()
        return any(marker.casefold() in normalized for marker in CONDITION_MARKERS)

    hard_conditions: list[str] = []
    capture_following = 0
    for paragraph in candidates:
        # 将过长段落按句号切开，保留包含条件标志的句子及其相邻短句。
        paragraph_has_marker = has_condition_marker(paragraph)
        for piece in re.split(r"(?<=[。；;])", paragraph):
            piece = piece.strip()
            should_capture = has_condition_marker(piece) or capture_following > 0
            if 8 <= len(piece) <= 500 and should_capture and piece not in hard_conditions:
                hard_conditions.append(piece)
        if paragraph_has_marker:
            capture_following = 3
        elif capture_following > 0:
            capture_following -= 1
    if re.search(r"附件|详见.*(?:PDF|报名表|通知)", detail_text, re.I):
        hard_conditions.append("部分要求可能位于附件或图片中，需打开原通知核对。")
    if not hard_conditions:
        hard_conditions.append("网页正文未明确列出硬性条件，请打开原通知确认是否有附件、图片或后续补充要求。")
    return summary, detail_text, hard_conditions[:20]


def extract_ncss_job_detail(html: str, title: str) -> tuple[str, str, list[str]]:
    match = re.search(
        r'<pre\b[^>]*class=["\'][^"\']*\bmainContent\b[^"\']*["\'][^>]*>(.*?)</pre>',
        html,
        re.I | re.S,
    )
    if not match:
        raise ValueError("国家大学生就业服务平台职位详情中未找到岗位正文")
    body = re.sub(r"<br\s*/?>", "\n", match.group(1), flags=re.I)
    body = html_lib.unescape(re.sub(r"<[^>]+>", " ", body))
    lines = [re.sub(r"\s+", " ", line).strip() for line in body.splitlines()]
    lines = [line for line in lines if line]
    detail_text = "\n".join(lines)[:12000]
    if len(detail_text) < 20:
        raise ValueError("国家大学生就业服务平台职位正文为空")
    summary = " ".join(lines[:2])[:220]
    markers = ("要求", "须", "必须", "本科", "硕士", "博士", "专业", "出勤", "实习", "经验", "语言")
    conditions = [line for line in lines if any(marker in line for marker in markers)]
    if not conditions:
        conditions = ["岗位正文未明确提取到硬性条件，请打开原职位查看详情。"]
    return summary, detail_text, conditions[:20]


DETAIL_EXTRACTORS = {
    "page": extract_detail_data,
    "ncss_job": extract_ncss_job_detail,
}


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _deadline_context_is_relevant(text: str, start: int, end: int) -> bool:
    prefix = text[max(0, start - 36):start]
    suffix = text[end:min(len(text), end + 18)]
    explicit_deadline_before = re.search(
        r"(?:报名|申报|申请|提交|材料)?(?:截止|截至)(?:时间|日期)?\s*"
        r"(?:(?:已)?(?:调整|变更|延期|延长)(?:为|至|到)?|为|是|：|:|到)?\s*$",
        prefix,
    )
    explicit_deadline_after = re.match(
        r"\s*(?:前|之前|截止(?:前)?|为止)(?:报名|申报|申请|提交|缴费)?",
        suffix,
    )
    return bool(explicit_deadline_before or explicit_deadline_after)


def _registration_range_context(text: str, start: int) -> bool:
    prefix = text[max(0, start - 30):start]
    return bool(re.search(r"(?:报名|申报|申请)(?:时间|日期|期限)\s*(?:为|是|：|:)?\s*$", prefix))


_FULL_RANGE = re.compile(
    r"(20\d{2})\s*(?:年|[-/.])\s*(\d{1,2})\s*(?:月|[-/.])\s*(\d{1,2})\s*(?:日|号)?\s*(?:\d{1,2}:\d{2})?\s*"
    r"(?:至|到|[-~～—])\s*(20\d{2})\s*(?:年|[-/.])\s*(\d{1,2})\s*(?:月|[-/.])\s*(\d{1,2})\s*(?:日|号)?"
)
_CROSS_MONTH_RANGE = re.compile(
    r"(?:(20\d{2})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*(?:日|号)?\s*"
    r"(?:至|到|[-~～—])\s*(?:(20\d{2})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*(?:日|号)?"
)
_SAME_MONTH_RANGE = re.compile(
    r"(?:(20\d{2})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*(?:日|号)?\s*"
    r"(?:至|到|[-~～—])\s*(?!20\d{2})(\d{1,2})\s*(?:日|号)?"
)
_FULL_DATE = re.compile(r"(20\d{2})\s*[年\-/\.]\s*(\d{1,2})\s*[月\-/\.]\s*(\d{1,2})\s*(?:日|号)?")
_MONTH_DAY = re.compile(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*(?:日|号)?")


def extract_registration_deadline(notice: dict, today: date | None = None) -> date | None:
    """从通知文字中取最晚的一个明确报名/申请截止日期；没有明确截止语义时返回 None。"""
    today = today or date.today()
    text = " ".join(str(notice.get(key, "") or "") for key in ("title", "summary", "detail_text"))
    if not text:
        return None

    deadlines: list[date] = []
    reference_year = today.year
    source_date = re.search(r"(20\d{2})", str(notice.get("date", "")))
    if source_date:
        reference_year = int(source_date.group(1))
    covered: list[tuple[int, int]] = []

    def is_covered(position: int, extra: list[tuple[int, int]] = ()) -> bool:
        return any(start <= position < end for start, end in list(covered) + list(extra))

    for match in _FULL_RANGE.finditer(text):
        if not _registration_range_context(text, match.start()):
            continue
        parsed = _safe_date(int(match.group(4)), int(match.group(5)), int(match.group(6)))
        if parsed:
            deadlines.append(parsed)
            covered.append(match.span())
    for match in _CROSS_MONTH_RANGE.finditer(text):
        if not _registration_range_context(text, match.start()) or is_covered(match.start()):
            continue
        start_year = int(match.group(1) or reference_year)
        start_month = int(match.group(2))
        end_year = int(match.group(4) or start_year)
        end_month = int(match.group(5))
        if start_month == end_month:
            continue  # 同月区间交给下面的解析器。
        if not match.group(4) and end_month < start_month:
            end_year += 1
        parsed = _safe_date(end_year, end_month, int(match.group(6)))
        if parsed:
            deadlines.append(parsed)
            covered.append(match.span())
    for match in _SAME_MONTH_RANGE.finditer(text):
        if not _registration_range_context(text, match.start()):
            continue
        parsed = _safe_date(int(match.group(1) or reference_year), int(match.group(2)), int(match.group(4)))
        if parsed:
            deadlines.append(parsed)
            covered.append(match.span())
    for match in _FULL_DATE.finditer(text):
        if is_covered(match.start()) or not _deadline_context_is_relevant(text, match.start(), match.end()):
            continue
        parsed = _safe_date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        if parsed:
            deadlines.append(parsed)
    occupied = [match.span() for match in _FULL_DATE.finditer(text)]
    for match in _MONTH_DAY.finditer(text):
        if is_covered(match.start(), occupied) or not _deadline_context_is_relevant(text, match.start(), match.end()):
            continue
        parsed = _safe_date(reference_year, int(match.group(1)), int(match.group(2)))
        if parsed:
            deadlines.append(parsed)
    return max(deadlines) if deadlines else None
