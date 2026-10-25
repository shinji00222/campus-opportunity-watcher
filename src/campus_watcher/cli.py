"""命令行入口。不带参数时打开桌面界面；其余命令主要给配置学校的人（或 AI）用。"""
from __future__ import annotations

import argparse
import re
import sys
import urllib.parse
from collections import Counter
from dataclasses import asdict

from campus_watcher import __version__, net, state, text
from campus_watcher.checker import CHECK_ALREADY_RUNNING, load_detail, run_check
from campus_watcher.config import ConfigError, School, available_schools, load_school, school_search_dirs
from campus_watcher.models import CATEGORIES
from campus_watcher.rules import Rules, is_stale_internship
from campus_watcher.sources import load_source


def _print(*args) -> None:
    print(*args, flush=True)


def cmd_list_schools() -> int:
    schools = available_schools()
    _print("查找目录：")
    for directory in school_search_dirs():
        _print(f"  {directory}")
    if not schools:
        _print("还没有任何学校配置。复制 schools/_template 开始配置，详见 AI_SETUP.md。")
        return 1
    for school_id, path in schools.items():
        try:
            school = load_school(config_path=path)
            _print(f"{school_id}\t{school.name}\t{len(school.sources)} 个来源\t{path}")
        except ConfigError as exc:
            _print(f"{school_id}\t（配置有误：{len(exc.problems)} 处）\t{path}")
    return 0


def cmd_validate(school: School) -> int:
    _print(f"配置有效：{school.name}（{school.id}），{len(school.sources)} 个来源，"
           f"自定义解析函数 {len(school.parsers)} 个。")
    for source in school.sources:
        target = source.get("url") or source.get("parser") or source.get("area_code", "")
        _print(f"  - [{source['type']}] {source['name']}  {target}")
    return 0


def cmd_probe(school: School, only: str | None, details: int, show: int) -> int:
    """只读试跑：逐个来源抓取并打印结果，不写状态、不发通知。"""
    sources = [s for s in school.sources if not only or s["name"] == only]
    if not sources:
        _print(f"没有名为「{only}」的来源。")
        return 2
    failed = 0
    total = 0
    for source in sources:
        items, error = load_source(school, source)
        stale = [n for n in items if is_stale_internship(asdict(n))]
        items = [n for n in items if n not in stale]
        total += len(items)
        counts = Counter(n.category for n in items)
        flag = "错误" if error and not items else ("部分失败" if error else "正常")
        failed += 1 if error else 0
        _print(f"\n== {source['name']}  [{source['type']}]  {flag}：{len(items)} 条 "
               + " ".join(f"{k}{v}" for k, v in counts.items()))
        if error:
            _print(f"   错误：{error}")
        if stale:
            _print(f"   另有 {len(stale)} 条实习发布超过 60 天，正式检查时会被过滤")
        for notice in items[:show]:
            _print(f"   · [{notice.category}] {notice.title}")
            _print(f"     {notice.url}" + (f"  发布 {notice.date}" if notice.date else ""))
        for notice in [n for n in items if n.detail != "none"][:details]:
            load_detail(notice)
            deadline = text.extract_registration_deadline(asdict(notice))
            _print(f"   详情 {notice.title[:30]}")
            _print(f"     摘要：{notice.summary[:90]}")
            _print(f"     截止日：{deadline or '未识别'}　条件 {len(notice.hard_conditions)} 条　正文 {len(notice.detail_text)} 字")
    _print(f"\n合计 {total} 条，{failed} 个来源有错误。（试跑不会写入状态，也不会发通知）")
    return 1 if failed else 0


_PAGE_HINT = re.compile(r"下一页|尾页|末页|^\d{1,3}$|next|>>|»", re.I)


def cmd_inspect(url: str, rules: Rules, category: str | None) -> int:
    """看一个网页能不能当来源：列出链接分类结果、可能的分页链接和动态加载迹象。"""
    html = net.fetch(url)
    page_links = text.links(html)
    visible = text.visible_text(html)
    _print(f"网页 {len(html)} 字节，可见文字 {len(visible)} 字，链接 {len(page_links)} 个。")
    if len(page_links) < 10 or len(visible) < 300:
        _print("提示：链接或文字很少，内容可能由脚本动态加载，html_links 可能抓不到，需要找接口或写 custom 解析函数。")
    matched, skipped = [], []
    for href, title in page_links:
        full = urllib.parse.urljoin(url, href)
        result = rules.classify(title, category)
        if result and len(title) >= 6 and not rules.is_ignored(title):
            matched.append((result, title, full))
        elif len(title) >= 12:
            skipped.append((title, full))
    _print(f"\n会被收集的链接（{len(matched)}）：")
    for result, title, full in matched[:40]:
        _print(f"  [{result}] {title}\n         {full}")
    _print(f"\n较长但未命中关键词的链接（前 20 个，用来判断是否要补关键词或设 category）：")
    for title, full in skipped[:20]:
        _print(f"  {title}\n         {full}")
    pages = [(t, urllib.parse.urljoin(url, h)) for h, t in page_links if _PAGE_HINT.search(t.strip())]
    if pages:
        _print("\n疑似分页链接（可据此写 pages_pattern）：")
        for title, full in pages[:10]:
            _print(f"  {title}  {full}")
    return 0


def run_scheduled(school: School) -> int:
    try:
        _new_items, errors, _first_run = run_check(school, notify=True)
    except Exception as exc:
        state.log(f"计划检测异常: {exc!r}")
        return 1
    if errors == [CHECK_ALREADY_RUNNING]:
        return 0
    return 1 if errors else 0


def _show_config_error(exc: ConfigError, gui: bool) -> None:
    message = str(exc) + "\n\n配置方法见 AI_SETUP.md。"
    if gui:
        try:
            from tkinter import Tk, messagebox
            root = Tk()
            root.withdraw()
            messagebox.showerror("校园机会提醒", message)
            root.destroy()
            return
        except Exception:
            pass
    _print(message)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="campus-watcher", description="高校竞赛、实习、出国机会定时提醒")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--school", help="学校 id（schools/ 下的文件夹名）")
    parser.add_argument("--config", help="直接指定 school.toml 路径")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--scheduled", action="store_true", help="静默检查一次并退出（计划任务用）")
    actions.add_argument("--list-schools", action="store_true", help="列出可用的学校配置")
    actions.add_argument("--validate", action="store_true", help="检查配置文件是否有效")
    actions.add_argument("--probe", action="store_true", help="试跑所有来源并打印结果，不写状态")
    actions.add_argument("--inspect", metavar="URL", help="分析一个网页适不适合做来源")
    parser.add_argument("--source", help="配合 --probe：只试跑这个来源")
    parser.add_argument("--details", type=int, default=0, metavar="N", help="配合 --probe：每个来源抓 N 条详情")
    parser.add_argument("--show", type=int, default=5, metavar="N", help="配合 --probe：每个来源显示 N 条标题")
    parser.add_argument("--category", choices=CATEGORIES, help="配合 --inspect：模拟来源的默认分类")
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    if args.list_schools:
        return cmd_list_schools()
    if args.inspect:
        try:
            rules = load_school(args.school, args.config).rules if (args.school or args.config) else Rules()
        except ConfigError as exc:
            _show_config_error(exc, gui=False)
            return 2
        return cmd_inspect(args.inspect, rules, args.category)

    gui = not (args.scheduled or args.validate or args.probe)
    try:
        school = load_school(args.school, args.config)
    except ConfigError as exc:
        _show_config_error(exc, gui=gui)
        return 2
    state.use_school(school.id)
    if args.validate:
        return cmd_validate(school)
    if args.probe:
        return cmd_probe(school, args.source, max(0, args.details), max(0, args.show))
    if args.scheduled:
        return run_scheduled(school)
    from campus_watcher.gui import MainWindow
    MainWindow(school).run()
    return 0
