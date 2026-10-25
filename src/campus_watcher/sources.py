"""信息来源类型。通用类型覆盖大多数学校；特殊页面用学校目录里的 parsers.py。"""
from __future__ import annotations

import json
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Callable

from campus_watcher import net, state, text
from campus_watcher.config import School
from campus_watcher.models import Notice, PartialSourceError, make_id
from campus_watcher.rules import Rules, is_stale_abroad, is_stale_competition, is_stale_internship

LANQIAO_URL = "https://dasai.lanqiao.cn/pages/v7/dasai/competition/individual_competition.html"
NCSS_INTERNSHIP_ENDPOINT = "https://job.ncss.cn/student/jobs/jobslist/ajax/"
NCSS_INTERNATIONAL_URL = "https://gj.ncss.cn/job.html"
MAX_WORKERS = 6


@dataclass
class SourceContext:
    """传给每个来源（包括学校自定义解析函数）的工具箱。"""
    source: dict
    rules: Rules
    today: date

    # 让 parsers.py 不用 import 内部模块也能拿到常用工具。
    Notice = Notice
    PartialSourceError = PartialSourceError
    make_id = staticmethod(make_id)
    links = staticmethod(text.links)
    visible_text = staticmethod(text.visible_text)
    squash = staticmethod(text.squash)
    log = staticmethod(lambda message: state.log(message))

    @property
    def name(self) -> str:
        return self.source["name"]

    @property
    def url(self) -> str:
        return self.source.get("url", "")

    @property
    def category(self) -> str | None:
        return self.source.get("category")

    def fetch(self, url: str, **kwargs) -> str:
        return net.fetch(url, **kwargs)

    def fetch_json(self, url: str, **kwargs):
        try:
            return json.loads(net.fetch(url, **kwargs))
        except json.JSONDecodeError as exc:
            raise ValueError("接口返回了无效 JSON") from exc

    def is_stale(self, notice: dict) -> bool:
        return self.rules.is_irrelevant(notice, self.today)

    def extract_notices(self, html: str, base_url: str | None = None, category: str | None = None) -> list[Notice]:
        return extract_link_notices(
            self.name, base_url or self.url, html, self.rules, category or self.category, self.today
        )


def extract_link_notices(
    source: str,
    base_url: str,
    html: str,
    rules: Rules,
    source_default: str | None = None,
    today: date | None = None,
) -> list[Notice]:
    """把页面里标题像竞赛/实习/出国的链接都变成通知。"""
    today = today or date.today()
    page_links = text.links(html)
    if not page_links:
        raise ValueError("页面中没有可解析的链接，可能网站结构已变化或内容由脚本动态加载")
    found: dict[str, Notice] = {}
    for href, title in page_links:
        title = re.sub(r"[​﻿]", "", title).strip(" -|·")
        category = rules.classify(title, source_default)
        if len(title) < 6 or not category or rules.is_ignored(title):
            continue
        notice_data = {"category": category, "title": title}
        if is_stale_competition(notice_data, today) or is_stale_abroad(notice_data, today):
            continue
        url = urllib.parse.urljoin(base_url, href)
        if not url.startswith(("http://", "https://")):
            continue
        clean_url = url.split("#", 1)[0]
        notice_id = make_id(clean_url)
        date_match = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})", title)
        published = "-".join(date_match.groups()) if date_match else ""
        found[notice_id] = Notice(notice_id, title, clean_url, source, published, category=category)
    return list(found.values())


def find_page_urls(html: str, base_url: str, pattern: str, limit: int) -> list[str]:
    host = urllib.parse.urlparse(base_url).netloc
    page_urls: list[str] = []
    for href, _title in text.links(html):
        if not re.search(pattern, href):
            continue
        page_url = urllib.parse.urljoin(base_url, href)
        if urllib.parse.urlparse(page_url).netloc == host and page_url not in page_urls and page_url != base_url:
            page_urls.append(page_url)
    return page_urls[:limit]


def html_links(ctx: SourceContext) -> list[Notice]:
    html = ctx.fetch(ctx.url)
    items = ctx.extract_notices(html)
    pattern = ctx.source.get("pages_pattern")
    if not pattern:
        return items
    errors = []
    for page_url in find_page_urls(html, ctx.url, pattern, ctx.source.get("extra_pages", 3)):
        try:
            items.extend(ctx.extract_notices(ctx.fetch(page_url), page_url))
        except Exception as exc:
            errors.append(f"分页读取失败：{exc}")
    if errors:
        raise PartialSourceError(items, "；".join(errors))
    return items


def _timestamp_date(value) -> str:
    if not isinstance(value, (int, float)) or value <= 0:
        return ""
    try:
        timestamp = value / 1000 if value > 100_000_000_000 else value
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return ""


def _finish_pages(notices_by_id: dict[str, Notice], page_errors: list[str], empty_message: str) -> list[Notice]:
    if not notices_by_id:
        raise ValueError(empty_message + ("；" + "；".join(page_errors) if page_errors else ""))
    if page_errors:
        raise PartialSourceError(list(notices_by_id.values()), "；".join(page_errors))
    return list(notices_by_id.values())


def parse_ncss_international(payload: str, source_name: str) -> list[Notice]:
    try:
        rows = json.loads(payload)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("国际组织实习平台岗位接口返回了无效 JSON") from exc
    if not isinstance(rows, list):
        raise ValueError("国际组织实习平台岗位接口格式异常")
    notices = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        job_id = str(row.get("id", "")).strip()
        title = text.squash(row.get("title"))
        if not job_id or not title:
            continue
        job_url = urllib.parse.urljoin(NCSS_INTERNATIONAL_URL, f"/job/{urllib.parse.quote(job_id, safe='')}.html")
        publish_date = _timestamp_date(row.get("publishDate"))
        deadline = _timestamp_date(row.get("endDate"))
        details = [part for part in (text.squash(row.get("orgName")), text.squash(row.get("address"))) if part]
        if publish_date:
            details.append(f"发布日期：{publish_date}")
        if deadline:
            details.append(f"截止日期：{deadline}")
        notices.append(Notice(
            id=make_id(job_url),
            title=title,
            url=job_url,
            source=source_name,
            date=publish_date,
            summary="；".join(details) or "岗位详情请打开国际组织实习平台原文查看。",
            category="实习",
        ))
    return notices


def ncss_international(ctx: SourceContext) -> list[Notice]:
    endpoint = urllib.parse.urljoin(NCSS_INTERNATIONAL_URL, "/job/job.json")
    notices_by_id: dict[str, Notice] = {}
    page_errors = []
    for page in range(ctx.source.get("max_pages", 2)):
        params = urllib.parse.urlencode({
            "page": page, "size": 20, "key": "", "orgField": "", "jobType": "实习",
            "jobField": "", "jobGrade": "", "eduLevel": "", "area": "",
        })
        try:
            for notice in parse_ncss_international(ctx.fetch(f"{endpoint}?{params}"), ctx.name):
                notices_by_id[notice.id] = notice
        except Exception as exc:
            page_errors.append(f"第 {page + 1} 页读取失败：{exc}")
    return _finish_pages(notices_by_id, page_errors, "国际组织实习平台未返回可解析岗位，可能接口或筛选结构已变化")


def ncss_internships(ctx: SourceContext) -> list[Notice]:
    """国家大学生就业服务平台的实习岗位，按省份筛选，只读最新几页。"""
    notices_by_id: dict[str, Notice] = {}
    page_errors = []
    for page in range(1, ctx.source.get("max_pages", 3) + 1):
        params = urllib.parse.urlencode({
            "jobType": "03", "areaCode": ctx.source.get("area_code", ""), "jobName": "", "monthPay": "",
            "industrySectors": "", "property": "", "categoryCode": "", "memberLevel": "",
            "offset": str(page), "limit": "20", "keyUnits": "", "degreeCode": "",
            "sourcesName": "0", "sourcesType": "",
        })
        try:
            response = ctx.fetch_json(f"{NCSS_INTERNSHIP_ENDPOINT}?{params}")
            data = response.get("data") if isinstance(response, dict) else None
            rows = data.get("list") if isinstance(data, dict) else None
            if not isinstance(response, dict) or response.get("flag") is not True or not isinstance(rows, list):
                raise ValueError("接口格式异常：岗位列表缺失")
            for row in rows:
                if not isinstance(row, dict):
                    continue
                job_id = str(row.get("jobId", "")).strip()
                title = text.squash(row.get("jobName"))
                if not job_id or not title:
                    continue
                job_url = f"https://job.ncss.cn/student/jobs/{urllib.parse.quote(job_id, safe='')}/detail.html"
                low, high = row.get("lowMonthPay"), row.get("highMonthPay")
                salary = ""
                if isinstance(low, (int, float)) and isinstance(high, (int, float)):
                    salary = "面议" if low == high == 0 else f"{low:g}-{high:g}K/月"
                major = text.squash(row.get("major"))
                published = _timestamp_date(row.get("publishDate"))
                details = [part for part in (
                    text.squash(row.get("recName")), text.squash(row.get("areaCodeName")), salary,
                    text.squash(row.get("degreeName")), f"专业：{major}" if major else "",
                ) if part]
                if published:
                    details.append(f"发布日期：{published}")
                notice = Notice(
                    id=make_id(job_url), title=title, url=job_url, source=ctx.name, date=published,
                    summary="；".join(details), category="实习", detail="ncss_job",
                )
                notices_by_id[notice.id] = notice
        except Exception as exc:
            page_errors.append(f"第 {page} 页读取失败：{exc}")
    return _finish_pages(notices_by_id, page_errors, "国家大学生就业服务平台未返回可解析的实习岗位")


def parse_lanqiao(html: str, source_name: str, url: str, now: datetime | None = None) -> Notice | None:
    """把蓝桥杯官网的报名时间变成按届区分的提醒。"""
    match = re.search(
        r"报名时间\s*[：:]\s*(20\d{2})年(\d{1,2})月(\d{1,2})日\s*"
        r"(\d{1,2}:\d{2})\s*[—–-]\s*(20\d{2})年(\d{1,2})月(\d{1,2})日\s*(\d{1,2}:\d{2})",
        text.visible_text(html),
    )
    if not match:
        return None
    sy, sm, sd, start_time, ey, em, ed, end_time = match.groups()
    start_date = f"{sy}-{int(sm):02d}-{int(sd):02d}"
    end_date = f"{ey}-{int(em):02d}-{int(ed):02d}"
    period = f"{sy}年{int(sm)}月{int(sd)}日 {start_time} 至 {ey}年{int(em)}月{int(ed)}日 {end_time}"
    now = now or datetime.now()
    start_at = datetime.strptime(f"{start_date} {start_time}", "%Y-%m-%d %H:%M")
    end_at = datetime.strptime(f"{end_date} {end_time}", "%Y-%m-%d %H:%M")
    if now < start_at:
        stage, title = "upcoming", f"{sy}年蓝桥杯报名将于{int(sm)}月{int(sd)}日开始"
    elif now <= end_at:
        stage, title = "open", f"{sy}年蓝桥杯报名已开放（截止{int(em)}月{int(ed)}日）"
    else:
        stage, title = "closed", f"{sy}年蓝桥杯报名已截止"
    return Notice(
        id=make_id(url, start_date),
        title=title,
        url=url,
        source=source_name,
        date=start_date,
        summary=f"蓝桥杯官网报名时间：{period}。请登录官网查看赛项、组别和报名要求。",
        category="竞赛",
        detail_text=(f"官网公布的报名时间：{period}。报名截止日期：{ey}年{int(em)}月{int(ed)}日 {end_time}。"
                     "报名、缴费及准考证等事项以蓝桥杯官网说明为准。"),
        hard_conditions=["报名组别及参赛资格请以蓝桥杯大赛官网当届通知为准。"],
        lifecycle_stage=stage,
        detail="none",
    )


def lanqiao(ctx: SourceContext) -> list[Notice]:
    url = ctx.url or LANQIAO_URL
    html = ctx.fetch(url)
    items = ctx.extract_notices(html, url, "竞赛")
    notice = parse_lanqiao(html, ctx.name, url)
    if notice:
        items.append(notice)
    return items


BUILTIN_TYPES: dict[str, Callable[[SourceContext], list[Notice]]] = {
    "html_links": html_links,
    "ncss_internships": ncss_internships,
    "ncss_international": ncss_international,
    "lanqiao": lanqiao,
}


def load_source(school: School, source: dict, today: date | None = None) -> tuple[list[Notice], str]:
    """读取一个来源，返回 (通知, 错误信息)。部分成功时两者都有。"""
    ctx = SourceContext(source=source, rules=school.rules, today=today or date.today())
    handler = school.parsers[source["parser"]] if source["type"] == "custom" else BUILTIN_TYPES[source["type"]]
    try:
        return list(handler(ctx) or []), ""
    except PartialSourceError as exc:
        return list(exc.notices), str(exc)
    except Exception as exc:
        return [], str(exc) or repr(exc)


def check_all(school: School, only: str | None = None) -> tuple[list[Notice], list[str]]:
    sources = [s for s in school.sources if not only or s["name"] == only]
    notices: dict[str, Notice] = {}
    errors: list[str] = []
    if not sources:
        return [], [f"没有名为「{only}」的来源"]
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(sources))) as pool:
        for source, (items, error) in zip(sources, pool.map(lambda s: load_source(school, s), sources)):
            notices.update((item.id, item) for item in items)
            if error:
                errors.append(f"{source['name']}: {error}")
                state.log(f"ERROR {source['name']}: {error}")
    return [n for n in notices.values() if not is_stale_internship(asdict(n))], errors
