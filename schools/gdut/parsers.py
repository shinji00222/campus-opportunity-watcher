"""广东工业大学的专用解析函数。

school.toml 里 type = "custom" 的来源用 parser = "函数名" 指向这里。
每个函数接收 ctx（campus_watcher.sources.SourceContext），返回 ctx.Notice 列表；
分页部分失败时 raise ctx.PartialSourceError(已解析的通知, "错误说明")。
"""
from __future__ import annotations

import html as html_lib
import json
import re
import urllib.parse
from datetime import datetime
from html.parser import HTMLParser


class _CareerCardParser(HTMLParser):
    """就业指导中心赛事卡片：标题链接是 javascript:void()，详情 ID 在 onclick 里。"""

    def __init__(self):
        super().__init__()
        self.card: dict | None = None
        self.title_parts: list[str] = []
        self.paragraph_parts: list[str] | None = None
        self.in_title = False
        self.cards: list[dict] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        tag = tag.lower()
        if tag == "li":
            self._finish_card()
            self.card = {"id": "", "title": "", "paragraphs": []}
        elif self.card is not None and tag == "div":
            match = re.search(r"gotoDetail\((\d+)\)", attrs.get("onclick", "") or "")
            if match:
                self.card["id"] = match.group(1)
        elif self.card is not None and tag == "a" and "title" in (attrs.get("class") or "").split():
            self.in_title = True
            self.title_parts = []
        elif self.card is not None and tag == "p":
            self.paragraph_parts = []

    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data)
        if self.paragraph_parts is not None:
            self.paragraph_parts.append(data)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "a" and self.in_title:
            self.card["title"] = re.sub(r"\s+", " ", "".join(self.title_parts)).strip()
            self.in_title = False
        elif tag == "p" and self.paragraph_parts is not None:
            paragraph = re.sub(r"\s+", " ", "".join(self.paragraph_parts)).strip()
            if paragraph:
                self.card["paragraphs"].append(paragraph)
            self.paragraph_parts = None
        elif tag == "li":
            self._finish_card()

    def _finish_card(self):
        if self.card and self.card.get("id") and self.card.get("title"):
            self.cards.append(self.card)
        self.card = None
        self.in_title = False
        self.paragraph_parts = None


def _parse_career_cards(ctx, html: str, base_url: str) -> list:
    parser = _CareerCardParser()
    parser.feed(html)
    if not parser.cards:
        raise ValueError("页面中没有可解析的赛事卡片，可能网站结构已变化")
    notices = []
    for card in parser.cards:
        if ctx.is_stale({"category": "竞赛", "title": card["title"]}):
            continue
        detail = "\n".join(card["paragraphs"])
        url = urllib.parse.urljoin(base_url, f"/competition/view/competition_id/{card['id']}")
        notices.append(ctx.Notice(
            id=ctx.make_id(url), title=card["title"], url=url, source=ctx.name,
            summary=detail[:220], category="竞赛", detail_text=detail,
        ))
    return notices


def career_competitions(ctx):
    """学生就业指导中心赛事列表，含最多 3 个分页。"""
    html = ctx.fetch(ctx.url)
    items = _parse_career_cards(ctx, html, ctx.url)
    host = urllib.parse.urlparse(ctx.url).netloc
    page_urls = []
    for href, _title in ctx.links(html):
        if re.search(r"/page/\d+(?:/|$)", href):
            page_url = urllib.parse.urljoin(ctx.url, href)
            if urllib.parse.urlparse(page_url).netloc == host and page_url not in page_urls:
                page_urls.append(page_url)
    errors = []
    for page_url in page_urls[:3]:
        try:
            items.extend(_parse_career_cards(ctx, ctx.fetch(page_url), page_url))
        except Exception as exc:
            errors.append(f"分页读取失败：{exc}")
    if errors:
        raise ctx.PartialSourceError(items, "；".join(errors))
    return items


_JNDS_CARD = re.compile(
    r'<h1\b[^>]*class=["\'][^"\']*\btext_c\b[^"\']*["\'][^>]*>(.*?)</h1>\s*<p\b[^>]*>(.*?)</p>',
    re.I | re.S,
)


def _parse_jnds_featured(ctx, html: str, base_url: str) -> list:
    """工科实验竞赛首页卡片：标题在 <h1>，链接文字只有“查看详情”。"""
    notices = {}
    for match in _JNDS_CARD.finditer(html):
        title = html_lib.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", match.group(1))).strip())
        category = ctx.rules.classify(title)
        if not category or ctx.is_stale({"category": category, "title": title}):
            continue
        detail_href = next((href for href, label in ctx.links(match.group(2)) if "查看详情" in label), None)
        if not detail_href:
            continue
        url = urllib.parse.urljoin(base_url, detail_href).split("#", 1)[0]
        summary = html_lib.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", match.group(2)))).strip()
        summary = re.sub(r"【?查看详情】?", "", summary).strip()
        notice_id = ctx.make_id(url)
        notices[notice_id] = ctx.Notice(notice_id, title, url, ctx.name, summary=summary[:220], category=category)
    return list(notices.values())


def jnds_competitions(ctx):
    """广东省工科大学生实验综合技能竞赛网站：普通链接 + 首页特色卡片。"""
    html = ctx.fetch(ctx.url)
    return ctx.extract_notices(html) + _parse_jnds_featured(ctx, html, ctx.url)


def icxcy_collection(ctx):
    """广工创新创业平台竞赛征集。接口需要请求头 tCode: gdut（来自该站前端自身），无需登录。"""
    endpoint = urllib.parse.urljoin(ctx.url, "/api/admin/project/project_collection/page")
    page_size, max_pages, total_pages = 50, 2, 1
    notices, errors = {}, []
    for page in range(1, max_pages + 1):
        if page > total_pages:
            break
        try:
            payload = ctx.fetch_json(
                endpoint,
                data=json.dumps({"page": page, "size": page_size, "status": 1}).encode("utf-8"),
                headers={"Content-Type": "application/json", "Referer": ctx.url, "tCode": "gdut", "language": "zh-cn"},
                method="POST",
            )
            data = payload.get("data") if isinstance(payload, dict) else None
            rows = data.get("list") if isinstance(data, dict) else None
            if not isinstance(payload, dict) or payload.get("code") != 0 or not isinstance(rows, list):
                raise ValueError("接口格式异常：征集列表缺失")
            if page == 1:
                pagination = data.get("pagination")
                total = pagination.get("total") if isinstance(pagination, dict) else len(rows)
                try:
                    total_pages = min(max_pages, max(1, (int(total) + page_size - 1) // page_size))
                except (TypeError, ValueError):
                    total_pages = 1
            for row in rows:
                if not isinstance(row, dict):
                    continue
                title = ctx.squash(row.get("name"))
                if not title or ctx.rules.classify(title) != "竞赛":
                    continue
                if row.get("isOngoing") not in (1, True, "1", "true"):
                    continue
                raw_deadline = str(row.get("ddlTime", "")).strip()
                deadline = None
                if raw_deadline:
                    try:
                        deadline = datetime.strptime(raw_deadline[:19], "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        errors.append(f"第 {page} 页有征集条目的截止时间格式无法识别")
                        continue
                    if deadline.date() < ctx.today:
                        continue
                collection_id = str(row.get("id", "")).strip()
                if not collection_id:
                    continue
                url = urllib.parse.urljoin(ctx.url, f"/collectionDetail?id={urllib.parse.quote(collection_id, safe='')}")
                create_time = str(row.get("createTime", "")).strip()
                deadline_label = (
                    f"{deadline.year}年{deadline.month}月{deadline.day}日 {deadline:%H:%M}" if deadline else "以原征集页面为准"
                )
                detail_text = ctx.visible_text(str(row.get("content", "") or "")).strip()
                if not detail_text:
                    detail_text = "请打开广工创新创业平台原征集页面核对通知及附件。"
                notice_id = ctx.make_id(url)
                notices[notice_id] = ctx.Notice(
                    id=notice_id, title=title, url=url, source=ctx.name,
                    date=create_time[:10] if re.match(r"20\d{2}-\d{2}-\d{2}", create_time) else "",
                    summary=f"平台显示正在征集，截止时间：{deadline_label}。 {detail_text[:180]}"[:260],
                    category="竞赛", detail_text=detail_text[:12000],
                    hard_conditions=["参赛对象、作品要求及提交方式请以广工创新创业平台原征集页面和附件为准。"],
                    lifecycle_stage="open", detail="none",
                )
        except Exception as exc:
            errors.append(f"第 {page} 页读取失败：{exc}")
    if not notices and errors:
        raise ValueError("广工双创平台竞赛征集读取失败；" + "；".join(errors))
    if errors:
        raise ctx.PartialSourceError(list(notices.values()), "；".join(errors))
    return list(notices.values())
