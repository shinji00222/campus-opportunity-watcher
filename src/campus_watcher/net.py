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
_BLOCK_PAGE = re.compile(r"安全验证|人机验证|请输入验证码|访问过于频繁|403 Forbidden|Access Denied", re.I)


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
        # 系统代理开着但代理软件没运行（WinError 10061）时，本次检查后续请求都直连。
        _proxy_unusable.set()
        state.log(f"系统代理不可用，改为直连：{exc}")
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
