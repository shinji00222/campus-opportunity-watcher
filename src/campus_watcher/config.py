"""学校配置：schools/<学校 id>/school.toml，可选 parsers.py 放该校专用解析函数。"""
from __future__ import annotations

import importlib.util
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from campus_watcher import state
from campus_watcher.models import CATEGORIES
from campus_watcher.rules import Rules

SOURCE_TYPES = ("html_links", "ncss_internships", "ncss_international", "lanqiao", "custom")
CONFIG_FILE = "school.toml"
PARSERS_FILE = "parsers.py"


class ConfigError(ValueError):
    def __init__(self, path: Path, problems: list[str]):
        self.path = path
        self.problems = problems
        super().__init__(f"{path} 配置有误：\n" + "\n".join(f"- {item}" for item in problems))


@dataclass
class School:
    id: str
    name: str
    short_name: str
    sources: list[dict]
    rules: Rules
    directory: Path
    parsers: dict[str, Callable] = field(default_factory=dict)

    @property
    def app_name(self) -> str:
        return f"{self.short_name}机会提醒"


def school_search_dirs() -> list[Path]:
    """查找顺序：用户数据目录 > 程序旁边 > 程序内置（或源码仓库）。先找到的同名学校优先。"""
    candidates = [state.DATA_ROOT / "schools"]
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).parent / "schools")
        candidates.append(Path(getattr(sys, "_MEIPASS", "")) / "schools")
    else:
        candidates.append(Path(__file__).resolve().parents[2] / "schools")
    seen, result = set(), []
    for item in candidates:
        key = str(item).casefold()
        if key not in seen and item.is_dir():
            seen.add(key)
            result.append(item)
    return result


def available_schools() -> dict[str, Path]:
    found: dict[str, Path] = {}
    for base in school_search_dirs():
        for config in sorted(base.glob(f"*/{CONFIG_FILE}")):
            school_id = config.parent.name
            if not school_id.startswith("_") and school_id not in found:
                found[school_id] = config
    return found


def _is_str_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value)


def _int_in(value, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def validate_raw(raw: dict, parser_names: set[str]) -> list[str]:
    problems: list[str] = []
    school = raw.get("school")
    if not isinstance(school, dict):
        return ["缺少 [school] 段"]
    if not isinstance(school.get("id"), str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", school["id"]):
        problems.append("school.id 必须是小写英文、数字或短横线，例如 gdut、sysu")
    for key in ("name", "short_name"):
        if not isinstance(school.get(key), str) or not school[key].strip():
            problems.append(f"school.{key} 不能为空")

    keywords = raw.get("keywords", {})
    if not isinstance(keywords, dict):
        problems.append("[keywords] 必须是表")
        keywords = {}
    for key, value in keywords.items():
        if key not in CATEGORIES + ("ignore",):
            problems.append(f"keywords.{key} 无效，只能是 {'、'.join(CATEGORIES)} 或 ignore")
        elif not _is_str_list(value):
            problems.append(f"keywords.{key} 必须是非空字符串列表")

    sources = raw.get("sources")
    if not isinstance(sources, list) or not sources:
        problems.append("至少需要一个 [[sources]]")
        return problems
    names: set[str] = set()
    for index, source in enumerate(sources, 1):
        label = f"第 {index} 个来源"
        if not isinstance(source, dict):
            problems.append(f"{label} 格式无效")
            continue
        name = source.get("name")
        if not isinstance(name, str) or not name.strip():
            problems.append(f"{label} 缺少 name")
        elif name in names:
            problems.append(f"来源名称重复：{name}")
        else:
            names.add(name)
            label = f"来源「{name}」"
        kind = source.get("type")
        if kind not in SOURCE_TYPES:
            problems.append(f"{label} 的 type 必须是 {', '.join(SOURCE_TYPES)} 之一")
            continue
        if "category" in source and source["category"] not in CATEGORIES:
            problems.append(f"{label} 的 category 只能是 {'、'.join(CATEGORIES)}")
        url = source.get("url")
        if kind in ("html_links", "custom") and not (isinstance(url, str) and url.startswith(("http://", "https://"))):
            problems.append(f"{label} 需要 http(s) 开头的 url")
        if kind == "html_links":
            pattern = source.get("pages_pattern")
            if pattern is not None:
                try:
                    re.compile(pattern)
                except (re.error, TypeError) as exc:
                    problems.append(f"{label} 的 pages_pattern 不是有效正则：{exc}")
            if "extra_pages" in source and not _int_in(source["extra_pages"], 1, 10):
                problems.append(f"{label} 的 extra_pages 必须是 1–10 的整数")
        if kind in ("ncss_internships", "ncss_international") and "max_pages" in source:
            if not _int_in(source["max_pages"], 1, 5):
                problems.append(f"{label} 的 max_pages 必须是 1–5 的整数")
        if kind == "ncss_internships":
            code = source.get("area_code", "")
            if not isinstance(code, str) or not re.fullmatch(r"(\d{2})?", code):
                problems.append(f"{label} 的 area_code 必须是两位省级代码（如广东 \"44\"），留空表示全国")
        if kind == "custom":
            parser = source.get("parser")
            if not isinstance(parser, str) or not parser:
                problems.append(f"{label} 需要 parser（{PARSERS_FILE} 里的函数名）")
            elif parser not in parser_names:
                problems.append(f"{label} 的 parser「{parser}」在 {PARSERS_FILE} 中不存在")
    return problems


def _load_parsers(directory: Path, school_id: str) -> dict[str, Callable]:
    path = directory / PARSERS_FILE
    if not path.exists():
        return {}
    spec = importlib.util.spec_from_file_location(f"campus_school_{school_id.replace('-', '_')}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return {
        name: value for name, value in vars(module).items()
        if callable(value) and not name.startswith("_") and getattr(value, "__module__", "") == module.__name__
    }


def load_school_file(config_path: Path) -> School:
    config_path = Path(config_path)
    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(config_path, ["找不到配置文件"]) from None
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(config_path, [f"TOML 格式错误：{exc}"]) from None
    school_section = raw.get("school") if isinstance(raw.get("school"), dict) else {}
    school_id = str(school_section.get("id") or config_path.parent.name)
    try:
        parsers = _load_parsers(config_path.parent, school_id)
    except Exception as exc:
        raise ConfigError(config_path, [f"{PARSERS_FILE} 加载失败：{exc!r}"]) from None
    problems = validate_raw(raw, set(parsers))
    if problems:
        raise ConfigError(config_path, problems)
    keywords = raw.get("keywords", {})
    return School(
        id=raw["school"]["id"],
        name=raw["school"]["name"],
        short_name=raw["school"]["short_name"],
        sources=raw["sources"],
        rules=Rules.with_extras({k: v for k, v in keywords.items() if k in CATEGORIES}, keywords.get("ignore")),
        directory=config_path.parent,
        parsers=parsers,
    )


def load_school(school_id: str | None = None, config_path: str | Path | None = None) -> School:
    if config_path:
        return load_school_file(Path(config_path))
    schools = available_schools()
    if school_id:
        if school_id not in schools:
            known = "、".join(schools) or "无"
            raise ConfigError(Path(school_id), [f"找不到学校「{school_id}」，已有：{known}"])
        return load_school_file(schools[school_id])
    if len(schools) == 1:
        return load_school_file(next(iter(schools.values())))
    known = "、".join(schools) or "无"
    raise ConfigError(Path("schools"), [f"请用 --school 指定学校，已有：{known}"])
