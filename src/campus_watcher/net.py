"""网络请求：开着系统代理就走代理，代理没开或连不上时自动直连。"""
from __future__ import annotations

import re
import threading
import urllib.error
import urllib.request

from campus_watcher import state

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) CampusOpportunityWatcher/0.1"
MAX_PAGE_BYTES = 8 * 1024 * 1024
_DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_proxy_unusable = threading.Event()
_proxy_lock = threading.Lock()
_BLOCK_PAGE = re.compile(r"安全验证|人机验证|请输入验证码|访问过于频繁|403 Forbidden|Access Denied", re.I)


def reset_proxy_state() -> None:
    # 每次检查开始时重新试代理：界面一直开着时，用户可能中途打开了代理软件。
    _proxy_unusable.clear()


def _proxy_refused(exc: BaseException) -> bool:
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    return isinstance(reason, ConnectionRefusedError)


def open_url(request: urllib.request.Request, timeout: int = 20):
    if not urllib.request.getproxies() or _proxy_unusable.is_set():
        return _DIRECT_OPENER.open(request, timeout=timeout)
    # 走代理时 urllib 会把 Request 的主机改成代理地址，回退直连必须用一份新请求。
    direct_request = urllib.request.Request(
        request.full_url,
        data=request.data,
        headers=dict(request.header_items()),
        method=request.get_method(),
    )
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError:
        raise
    except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
        if _proxy_refused(exc):
            # 系统代理开着但代理软件没运行（WinError 10061）时，本次检查后续请求都直连。
            # 多个线程会同时失败，只记一次日志。
            with _proxy_lock:
                first = not _proxy_unusable.is_set()
                _proxy_unusable.set()
            if first:
                state.log(f"系统代理不可用，本次检查改为直连：{exc}")
        else:
            # 超时等错误可能只是这个网站慢，代理本身没坏，只给这一条请求改直连。
            state.log(f"走代理请求失败，这一条改为直连重试：{request.full_url}：{exc}")
        return _DIRECT_OPENER.open(direct_request, timeout=timeout)


def read_limited(response) -> bytes:
    raw = response.read(MAX_PAGE_BYTES + 1)
    if len(raw) > MAX_PAGE_BYTES:
        raise ValueError(f"响应超过 {MAX_PAGE_BYTES // (1024 * 1024)} MB，已停止读取")
    return raw


def fetch(url: str, headers: dict | None = None, data: bytes | None = None, method: str | None = None) -> str:
    request = urllib.request.Request(
        url,
        data=data,
        headers={"User-Agent": USER_AGENT, **(headers or {})},
        method=method,
    )
    with open_url(request) as response:
        raw = read_limited(response)
        encoding = response.headers.get_content_charset() or "utf-8"
    try:
        text = raw.decode(encoding)
    except (UnicodeDecodeError, LookupError):
        text = raw.decode("gb18030", errors="replace")
    if _BLOCK_PAGE.search(text):
        raise ValueError("网站返回了访问验证或拦截页面，暂时无法采集")
    if not text.strip():
        raise ValueError("网站返回空页面")
    return text
