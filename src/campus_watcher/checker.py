"""一次完整检查：采集、刷新详情、清理过期、合并状态、发通知。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date, datetime, timedelta

from campus_watcher import net, state, text, updates
from campus_watcher.config import School
from campus_watcher.models import Notice
from campus_watcher.sources import check_all

DETAIL_REFRESH_INTERVAL = timedelta(days=7)
DETAIL_RETRY_INTERVAL = timedelta(days=1)
MAX_DETAIL_TARGETS = 30
CHECK_ALREADY_RUNNING = "已有一次检测正在运行，本次跳过"
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def now_text() -> str:
    return datetime.now().strftime(TIME_FORMAT)


def notice_is_registration_expired(notice: dict, today: date | None = None) -> bool:
    today = today or date.today()
    deadline = text.extract_registration_deadline(notice, today)
    return bool(deadline and deadline < today)


def detail_refresh_due(last_checked_at: str, last_attempted_at: str = "", now: datetime | None = None) -> bool:
    now = now or datetime.now()
    if last_attempted_at:
        try:
            attempted_at = datetime.strptime(last_attempted_at, TIME_FORMAT)
        except (TypeError, ValueError):
            attempted_at = None
        if attempted_at and now - attempted_at < DETAIL_RETRY_INTERVAL:
            return False
    if not last_checked_at:
        return True
    try:
        checked_at = datetime.strptime(last_checked_at, TIME_FORMAT)
    except (TypeError, ValueError):
        return True
    return now - checked_at >= DETAIL_REFRESH_INTERVAL


def select_detail_targets(notices: list[Notice]) -> list[Notice]:
    eligible = [
        n for n in notices
        if n.detail != "none"
        and (
            ((not n.detail_text or not n.hard_conditions) and detail_refresh_due("", n.detail_attempted_at))
            or detail_refresh_due(n.detail_checked_at, n.detail_attempted_at)
        )
    ]
    # 先处理未尝试项，再按截止日从近到远处理，避免旧失败项或远期岗位挡住紧急机会。
    eligible.sort(key=lambda n: (
        bool(n.detail_attempted_at),
        text.extract_registration_deadline(asdict(n)) or date.max,
        n.detail_attempted_at,
        n.first_seen,
        n.id,
    ))
    return eligible[:MAX_DETAIL_TARGETS]


def recently_expired_notice_ids(saved: dict, notices: list[Notice], today: date | None = None) -> set[str]:
    today = today or date.today()
    cache = saved.get("expired_checked_at", {})
    if not isinstance(cache, dict):
        return set()
    suppressed = set()
    for notice in notices:
        entry = cache.get(notice.id)
        if not isinstance(entry, dict) or detail_refresh_due(entry.get("checked_at", ""), entry.get("attempted_at", "")):
            continue
        if entry.get("title") and entry["title"] != notice.title:
            continue
        deadline = text.extract_registration_deadline(asdict(notice), today)
        if deadline and deadline >= today:
            continue
        suppressed.add(notice.id)
    return suppressed


def _drop(saved: dict, removed_ids: set) -> None:
    saved["notices"] = [n for n in saved.get("notices", []) if n.get("id") not in removed_ids]
    saved["favorites"] = [item for item in saved.get("favorites", []) if item not in removed_ids]


def prune_irrelevant_notices(saved: dict, school: School, today: date | None = None) -> list[str]:
    today = today or date.today()
    removed_ids = {n.get("id") for n in saved.get("notices", []) if school.rules.is_irrelevant(n, today)}
    if removed_ids:
        _drop(saved, removed_ids)
    return sorted(item for item in removed_ids if item)


def prune_expired_notices(saved: dict, today: date | None = None) -> list[str]:
    today = today or date.today()
    notices = saved.get("notices", [])
    removed = {n.get("id"): n for n in notices if notice_is_registration_expired(n, today)}
    if not removed:
        return []
    _drop(saved, set(removed))
    checked_at = now_text()
    cache = saved.get("expired_checked_at", {})
    cache = {k: v for k, v in cache.items() if isinstance(k, str) and isinstance(v, dict)} if isinstance(cache, dict) else {}
    for notice_id, notice in removed.items():
        cache[notice_id] = {
            "checked_at": notice.get("detail_checked_at") or checked_at,
            "attempted_at": notice.get("detail_attempted_at") or notice.get("detail_checked_at") or checked_at,
            "title": str(notice.get("title", "")),
        }
    if len(cache) > 500:
        cache = dict(sorted(cache.items(), key=lambda item: str(item[1].get("checked_at", "")))[-500:])
    saved["expired_checked_at"] = cache
    saved["seen"] = [item for item in saved.get("seen", []) if item not in removed]
    return sorted(item for item in removed if item)


def load_detail(n: Notice) -> Notice:
    try:
        detail_html = net.fetch(n.url)
        extractor = text.DETAIL_EXTRACTORS.get(n.detail, text.extract_detail_data)
        summary, detail_text, hard_conditions = extractor(detail_html, n.title)
        if not detail_text:
            raise ValueError("通知正文没有可提取的有效内容")
        n.summary, n.detail_text, n.hard_conditions = summary, detail_text, hard_conditions
        n.detail_checked_at = now_text()
    except Exception as exc:
        n.summary = n.summary or "详情暂时无法读取，请打开原通知查看。"
        n.hard_conditions = n.hard_conditions or ["原通知详情暂时无法读取，硬性条件尚未完成采集；请打开来源链接人工核对。"]
        state.log(f"简介提取失败 {n.url}: {exc!r}")
    finally:
        n.detail_attempted_at = now_text()
    return n


def notify_windows(app_name: str, title: str, message: str, launch: str = "") -> None:
    try:
        from winotify import Notification
        Notification(app_id=app_name, title=title, msg=message, duration="long", launch=launch).show()
    except Exception as exc:
        state.log(f"通知发送失败: {exc!r}")


def check_for_update(school: School) -> None:
    """检查完顺带看一下有没有新版本；任何失败都不影响本次检查结果。"""
    def announce(release: dict) -> None:
        notify_windows(school.app_name, f"{school.app_name} 有新版本 v{release['version']}",
                       "点击打开下载页面。更新时关闭程序，下载新版后重新运行安装脚本即可，个人数据会保留。",
                       release["url"])

    try:
        updates.maybe_check_for_update(announce)
    except Exception as exc:
        state.log(f"检查更新异常：{exc!r}")


@contextmanager
def check_mutex():
    """防止计划任务和手动检测同时运行、互相覆盖状态。"""
    with state.named_mutex("Check", blocking=False) as acquired:
        yield acquired


def run_check(school: School, notify: bool = True) -> tuple[list[Notice], list[str], bool]:
    with check_mutex() as acquired:
        if not acquired:
            state.log(CHECK_ALREADY_RUNNING)
            return [], [CHECK_ALREADY_RUNNING], False
        result = _run_check_locked(school, notify)
        if notify:
            check_for_update(school)
        return result


def _run_check_locked(school: School, notify: bool) -> tuple[list[Notice], list[str], bool]:
    saved = state.load_state()
    current, errors = check_all(school)
    now = now_text()
    seen = set(saved.get("seen", []))
    first_run = not saved.get("initialized", False)
    old_by_id = {n["id"]: n for n in saved.get("notices", [])}
    expired_cache = saved.get("expired_checked_at", {})
    if not isinstance(expired_cache, dict):
        expired_cache = {}
    saved["expired_checked_at"] = expired_cache
    expired_recently = recently_expired_notice_ids(saved, current)
    current = [n for n in current if n.id not in expired_recently]
    for n in current:
        old = old_by_id.get(n.id, {})
        n.first_seen = old.get("first_seen", now)
        if n.detail != "none":
            n.summary = n.summary or old.get("summary", "")
            n.detail_text = old.get("detail_text") or n.detail_text
            n.hard_conditions = old.get("hard_conditions") or n.hard_conditions
            n.detail_checked_at = old.get("detail_checked_at") or old.get("first_seen", "")
            n.detail_attempted_at = old.get("detail_attempted_at", "")

    # 先刷新详情，再按截止日清理，避免延期后仍按旧日期删除。
    detail_targets = select_detail_targets(current)
    if detail_targets:
        with ThreadPoolExecutor(max_workers=min(6, len(detail_targets))) as pool:
            list(pool.map(load_detail, detail_targets))
    expired_current = {n.id for n in current if notice_is_registration_expired(asdict(n))}
    for n in current:
        if n.id in expired_current:
            old_by_id[n.id] = asdict(n)
    current = [n for n in current if n.id not in expired_current]

    # 过期后又出现在列表里：标题变了或识别到新的未过期截止日才重新提醒。
    retired_without_reopen, reopened_ids = set(), set()
    for n in current:
        entry = expired_cache.get(n.id)
        if not isinstance(entry, dict):
            continue
        old_title = entry.get("title", "")
        title_changed = bool(old_title and old_title != n.title)
        if title_changed or text.extract_registration_deadline(asdict(n)) is not None:
            reopened_ids.add(n.id)
        else:
            retired_without_reopen.add(n.id)
            expired_cache[n.id] = {
                "checked_at": n.detail_checked_at or now,
                "attempted_at": n.detail_attempted_at or entry.get("attempted_at") or now,
                "title": n.title or old_title,
            }
    current = [n for n in current if n.id not in retired_without_reopen]

    revised_ids = set()
    for n in current:
        old = old_by_id.get(n.id)
        if not old:
            continue
        title_changed = bool(old.get("title") and old["title"] != n.title)
        old_deadline = text.extract_registration_deadline(old)
        new_deadline = text.extract_registration_deadline(asdict(n))
        # 详情每轮只抓一部分，第一次读到正文时识别出截止日不算“修改”，否则会重复提醒。
        if title_changed or (old_deadline is not None and old_deadline != new_deadline):
            revised_ids.add(n.id)

    new_items = [
        n for n in current
        if n.id not in seen
        or n.id in revised_ids
        or n.id in reopened_ids
        or (n.lifecycle_stage == "open" and old_by_id.get(n.id, {}).get("lifecycle_stage", "open") != "open")
    ]
    for n in current:
        old_by_id[n.id] = asdict(n)
    saved.update({
        "initialized": bool(current) or saved.get("initialized", False),
        "seen": list(seen | {n.id for n in current})[-2000:],
        "notices": list(old_by_id.values())[-500:],
        "last_check": now,
        "last_error": "；".join(errors),
        "schema_version": 1,
    })
    with state.named_mutex("State", blocking=True) as acquired:
        if not acquired:
            raise TimeoutError("等待状态文件写入锁超时，请稍后重试")
        # 检测期间用户可能改了档案或收藏，提交前以磁盘上的最新值为准。
        latest = state.load_state()
        saved["profile"] = latest.get("profile", saved.get("profile", {}))
        saved["favorites"] = latest.get("favorites", saved.get("favorites", []))
        for notice_id in reopened_ids:
            expired_cache.pop(notice_id, None)
        removed_irrelevant = prune_irrelevant_notices(saved, school)
        removed_expired = prune_expired_notices(saved)
        state.write_state_atomic(saved)

    if notify and new_items and not first_run:
        preview = "；".join(n.title[:35] for n in new_items[:3])
        if len(new_items) > 3:
            preview += f"；另有 {len(new_items) - 3} 条"
        counts: dict[str, int] = {}
        for item in new_items:
            counts[item.category] = counts.get(item.category, 0) + 1
        category_text = "、".join(f"{k}{v}条" for k, v in counts.items())
        notify_windows(school.app_name, f"发现 {len(new_items)} 条新消息（{category_text}）", preview)
    state.log(
        f"检查完成，发现 {len(current)} 条，新增 {0 if first_run else len(new_items)} 条，"
        f"清理过期 {len(removed_expired)} 条，清理归档/无关 {len(removed_irrelevant)} 条，错误 {len(errors)} 个"
    )
    return ([] if first_run else new_items), errors, first_run
