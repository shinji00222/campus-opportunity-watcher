"""本地状态、日志与跨进程互斥锁。数据只保存在用户电脑上。"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

DATA_ROOT = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "CampusOpportunityWatcher"
APP_DIR = DATA_ROOT
STATE_FILE = APP_DIR / "state.json"
LOG_FILE = APP_DIR / "watcher.log"
MAX_LOG_BYTES = 1024 * 1024
_THREAD_CHECK_LOCK = threading.Lock()
_THREAD_STATE_LOCK = threading.Lock()


def use_school(school_id: str) -> None:
    """每所学校使用独立的数据目录，互不覆盖。"""
    use_app_dir(DATA_ROOT / school_id)


def use_app_dir(app_dir: Path) -> None:
    global APP_DIR, STATE_FILE, LOG_FILE
    APP_DIR = Path(app_dir)
    STATE_FILE = APP_DIR / "state.json"
    LOG_FILE = APP_DIR / "watcher.log"


def log(text: str) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    if LOG_FILE.exists() and LOG_FILE.stat().st_size >= MAX_LOG_BYTES:
        os.replace(LOG_FILE, LOG_FILE.with_name(f"{LOG_FILE.name}.1"))
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {text}\n")


def empty_state() -> dict:
    return {"initialized": False, "seen": [], "notices": [], "favorites": [], "last_check": "从未", "last_error": ""}


@contextmanager
def named_mutex(scope: str, blocking: bool):
    """协调状态短事务和长时间检测，覆盖同一台电脑上的多个进程。"""
    thread_lock = _THREAD_CHECK_LOCK if scope == "Check" else _THREAD_STATE_LOCK
    local_check_guard = False
    if scope == "Check":
        local_check_guard = thread_lock.acquire(blocking=False)
        if not local_check_guard:
            yield False
            return

    if os.name != "nt":
        if scope == "Check":
            try:
                yield True
            finally:
                thread_lock.release()
            return
        acquired = thread_lock.acquire(blocking=blocking)
        try:
            yield acquired
        finally:
            if acquired:
                thread_lock.release()
        return

    mutex_hash = hashlib.sha256(str(APP_DIR).casefold().encode("utf-8")).hexdigest()[:16]
    mutex_name = f"Local\\CampusOpportunityWatcher.{scope}.{mutex_hash}"
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        kernel32.ReleaseMutex.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        ctypes.set_last_error(0)
        handle = kernel32.CreateMutexW(None, False, mutex_name)
        if not handle:
            raise OSError(ctypes.get_last_error(), "无法创建应用互斥锁")
        timeout = 10_000 if blocking else 0
        result = kernel32.WaitForSingleObject(handle, timeout)
        acquired = result in (0, 0x80)  # WAIT_OBJECT_0 or WAIT_ABANDONED
        if result == 0xFFFFFFFF:  # WAIT_FAILED
            error = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            raise OSError(error, "无法等待应用互斥锁")
        if not acquired:
            kernel32.CloseHandle(handle)
            yield False
            return
        try:
            yield True
        finally:
            kernel32.ReleaseMutex(handle)
            kernel32.CloseHandle(handle)
    finally:
        if local_check_guard:
            thread_lock.release()


def _preserve_corrupt_state() -> None:
    if not STATE_FILE.exists():
        return
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = STATE_FILE.with_name(f"state.corrupt-{stamp}.json")
    suffix = 1
    while backup.exists():
        backup = STATE_FILE.with_name(f"state.corrupt-{stamp}-{suffix}.json")
        suffix += 1
    os.replace(STATE_FILE, backup)
    log(f"状态文件格式无效，已保留副本：{backup.name}")


def _state_shape_is_valid(state) -> bool:
    if not isinstance(state, dict):
        return False
    if not all(isinstance(state.get(key, []), list) for key in ("notices", "seen", "favorites")):
        return False
    if not isinstance(state.get("profile", {}), dict):
        return False
    version = state.get("schema_version", 0)
    if not isinstance(version, int) or isinstance(version, bool):
        return False
    if "initialized" in state and not isinstance(state["initialized"], bool):
        return False
    if not all(isinstance(item, str) for item in state.get("seen", []) + state.get("favorites", [])):
        return False
    if not all(
        isinstance(notice, dict) and all(isinstance(notice.get(key), str) for key in ("id", "title", "url", "source"))
        for notice in state.get("notices", [])
    ):
        return False
    return all(value is None or isinstance(value, (str, int, float)) for value in state.get("profile", {}).values())


def load_state() -> dict:
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return empty_state()
    except (json.JSONDecodeError, UnicodeDecodeError):
        _preserve_corrupt_state()
        return empty_state()
    if not _state_shape_is_valid(state):
        _preserve_corrupt_state()
        return empty_state()
    profile = state.setdefault("profile", {})
    for key, value in profile.items():
        profile[key] = "" if value is None else str(value)
    for notice in state.get("notices", []):
        for key in (
            "date", "first_seen", "summary", "detail_text", "category",
            "lifecycle_stage", "detail_checked_at", "detail_attempted_at", "detail",
        ):
            if key in notice and not isinstance(notice[key], str):
                notice[key] = str(notice[key] or "")
        hard_conditions = notice.get("hard_conditions", [])
        if not isinstance(hard_conditions, list):
            hard_conditions = []
        notice["hard_conditions"] = [item for item in hard_conditions if isinstance(item, str)]
        if not notice["hard_conditions"]:
            notice["hard_conditions"] = ["该项目尚未从原通知中识别出明确硬性条件，请打开来源链接及附件人工核对。"]
    return state


def write_state_atomic(state: dict) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    fd, temporary_path = tempfile.mkstemp(prefix="state-", suffix=".tmp", dir=APP_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(state, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, STATE_FILE)
    except Exception:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
        raise


def update_state(mutator) -> dict:
    """短事务读改写，避免覆盖另一个窗口刚保存的修改。"""
    with named_mutex("State", blocking=True) as acquired:
        if not acquired:
            raise TimeoutError("等待状态文件写入锁超时，请稍后重试")
        state = load_state()
        mutator(state)
        write_state_atomic(state)
        return state
