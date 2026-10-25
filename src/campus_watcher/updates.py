"""检查 GitHub 上有没有新发行版，有就提醒一次。只读公开接口，失败时静默跳过。"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta

from campus_watcher import __version__, net, state

REPO = "shinji00222/campus-opportunity-watcher"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
CHECK_INTERVAL = timedelta(hours=20)
DISABLE_ENV = "CAMPUS_WATCHER_NO_UPDATE_CHECK"
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def parse_version(value: str) -> tuple[int, ...] | None:
    match = re.fullmatch(r"v?(\d+)\.(\d+)(?:\.(\d+))?", str(value or "").strip())
    if not match:
        return None
    return tuple(int(part or 0) for part in match.groups())


def is_newer(candidate: str, current: str = __version__) -> bool:
    new, old = parse_version(candidate), parse_version(current)
    return bool(new and old and new > old)


def fetch_latest_release() -> dict | None:
    """返回 {"version", "url"}；仓库还没有正式发行版时返回 None。"""
    try:
        payload = json.loads(net.fetch(RELEASES_API, headers={"Accept": "application/vnd.github+json"}))
    except json.JSONDecodeError as exc:
        raise ValueError("GitHub 返回了无效 JSON") from exc
    except Exception as exc:
        if getattr(exc, "code", None) == 404:
            return None
        raise
    tag = str(payload.get("tag_name", "")) if isinstance(payload, dict) else ""
    if not parse_version(tag):
        return None
    url = str(payload.get("html_url", ""))
    # 只打开本仓库的 GitHub 页面，接口返回别的地址时退回发行版列表页。
    if not url.startswith(f"https://github.com/{REPO}/"):
        url = RELEASES_PAGE
    return {"version": tag.lstrip("v"), "url": url}


def pending_update(saved: dict) -> dict | None:
    """状态里记录的新版本（比当前程序新时）。界面用它显示提示。"""
    latest = saved.get("latest_release")
    if isinstance(latest, dict) and is_newer(str(latest.get("version", ""))):
        return latest
    return None


def check_due(saved: dict, now: datetime | None = None) -> bool:
    if os.environ.get(DISABLE_ENV):
        return False
    now = now or datetime.now()
    try:
        last = datetime.strptime(str(saved.get("update_checked_at", "")), TIME_FORMAT)
    except ValueError:
        return True
    return now - last >= CHECK_INTERVAL


def maybe_check_for_update(notify) -> dict | None:
    """到时间就查一次；发现新版本且没提醒过时调用 notify(版本信息)。返回待更新的版本。"""
    if not check_due(state.load_state()):
        return pending_update(state.load_state())
    try:
        latest = fetch_latest_release()
    except Exception as exc:
        # 查询失败不记录检查时间，下次检查时再试。
        state.log(f"检查更新失败：{exc}")
        return pending_update(state.load_state())
    announce = None

    def record(saved: dict) -> None:
        nonlocal announce
        saved["update_checked_at"] = datetime.now().strftime(TIME_FORMAT)
        saved["latest_release"] = latest or {}
        if latest and is_newer(latest["version"]) and saved.get("update_notified") != latest["version"]:
            saved["update_notified"] = latest["version"]
            announce = latest

    saved = state.update_state(record)
    if announce:
        notify(announce)
    return pending_update(saved)
