"""Tkinter 桌面界面。"""
from __future__ import annotations

import os
import threading
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

from campus_watcher import state, updates
from campus_watcher.checker import CHECK_ALREADY_RUNNING, run_check
from campus_watcher.config import School
from campus_watcher.matching import PROFILE_FIELDS, evaluate_notice
from campus_watcher.models import CATEGORIES

FONT = "Microsoft YaHei UI"


class ProfileDialog(tk.Toplevel):
    def __init__(self, parent, profile: dict, on_save):
        super().__init__(parent)
        self.title("个人档案")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.on_save = on_save
        self.vars: dict[str, tk.StringVar] = {}
        body = ttk.Frame(self, padding=16)
        body.pack(fill="both", expand=True)
        for row, (key, label, default) in enumerate(PROFILE_FIELDS):
            ttk.Label(body, text=label + "：").grid(row=row, column=0, sticky="e", padx=(0, 8), pady=4)
            var = tk.StringVar(value=str(profile.get(key, default)))
            self.vars[key] = var
            if key == "education":
                widget = ttk.Combobox(body, textvariable=var, state="readonly", width=31,
                                      values=("本科生", "硕士研究生", "博士研究生"))
            else:
                widget = ttk.Entry(body, textvariable=var, width=34)
            widget.grid(row=row, column=1, sticky="ew", pady=4)
        ttk.Label(body, text="届别默认按本科四年制推算；意向关键词可用逗号分隔。档案仅保存在本机。",
                  foreground="#666").grid(row=len(PROFILE_FIELDS), column=0, columnspan=2, pady=(8, 12))
        buttons = ttk.Frame(body)
        buttons.grid(row=len(PROFILE_FIELDS) + 1, column=0, columnspan=2, sticky="e")
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="left", padx=6)
        ttk.Button(buttons, text="保存并重新排序", command=self.save).pack(side="left")

    def save(self):
        self.on_save({key: var.get().strip() for key, var in self.vars.items()})
        self.destroy()


class MainWindow:
    def __init__(self, school: School):
        self.school = school
        self.root = tk.Tk()
        self.root.title(f"{school.app_name} · {school.short_name}")
        self.root.geometry("1120x720")
        self.root.minsize(820, 560)
        self.root.configure(background="#f3f6fb")
        self.status = tk.StringVar()
        self.search_text = tk.StringVar()
        self.summary_link_url = ""
        self._configure_style()
        self._build()
        self.refresh()

    def _configure_style(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("App.TFrame", background="#f3f6fb")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("Header.TFrame", background="#173b67")
        style.configure("HeaderTitle.TLabel", background="#173b67", foreground="#ffffff", font=(FONT, 19, "bold"))
        style.configure("HeaderHint.TLabel", background="#173b67", foreground="#c9d8eb", font=(FONT, 9))
        style.configure("Section.TLabel", background="#ffffff", foreground="#213547", font=(FONT, 10, "bold"))
        style.configure("Muted.TLabel", background="#f3f6fb", foreground="#63758b", font=(FONT, 9))
        style.configure("Primary.TButton", font=(FONT, 9, "bold"), padding=(14, 8),
                        background="#2474c6", foreground="#ffffff")
        style.map("Primary.TButton", background=[("active", "#1d63a9"), ("disabled", "#a8bfd8")])
        style.configure("TButton", font=(FONT, 9), padding=(10, 7))
        style.configure("TRadiobutton", background="#ffffff", foreground="#42566d", font=(FONT, 9), padding=(5, 5))
        style.map("TRadiobutton", foreground=[("selected", "#1769aa")])
        style.configure("Treeview", background="#ffffff", fieldbackground="#ffffff", foreground="#25364a",
                        rowheight=34, font=(FONT, 9), borderwidth=0)
        style.configure("Treeview.Heading", background="#eaf0f7", foreground="#43566c",
                        font=(FONT, 9, "bold"), padding=(8, 9), relief="flat")
        style.map("Treeview", background=[("selected", "#dcecff")], foreground=[("selected", "#173b67")])
        style.map("Treeview.Heading", background=[("active", "#dce7f3")])
        style.configure("Vertical.TScrollbar", background="#dce5ef", troughcolor="#f3f6fb", borderwidth=0)

    def _build(self):
        shell = ttk.Frame(self.root, style="App.TFrame", padding=18)
        shell.pack(fill="both", expand=True)

        top = ttk.Frame(shell, style="Header.TFrame", padding=(22, 18))
        top.pack(fill="x", pady=(0, 14))
        title_box = ttk.Frame(top, style="Header.TFrame")
        title_box.pack(side="left", fill="x", expand=True)
        ttk.Label(title_box, text=self.school.app_name, style="HeaderTitle.TLabel").pack(anchor="w")
        ttk.Label(title_box, text=f"{self.school.name}｜竞赛 · 实习 · 出国交流｜公开信息汇总与个人条件匹配",
                  style="HeaderHint.TLabel").pack(anchor="w", pady=(4, 0))
        actions = ttk.Frame(top, style="Header.TFrame")
        actions.pack(side="right")
        self.update_btn = ttk.Button(actions, text="", command=self.open_update_page)
        self.update_url = ""
        self.profile_btn = ttk.Button(actions, text="个人档案", command=self.open_profile)
        self.profile_btn.pack(side="left", padx=(0, 8))
        self.check_btn = ttk.Button(actions, text="立即检测", style="Primary.TButton", command=self.start_check)
        self.check_btn.pack(side="left")

        toolbar = ttk.Frame(shell, style="Card.TFrame", padding=(14, 8))
        toolbar.pack(fill="x", pady=(0, 10))
        ttk.Label(toolbar, text="分类", style="Section.TLabel").pack(side="left", padx=(0, 8))
        self.category_filter = tk.StringVar(value="全部")
        for category in ("全部",) + CATEGORIES:
            ttk.Radiobutton(toolbar, text=category, value=category, variable=self.category_filter,
                            command=self.refresh).pack(side="left", padx=(0, 5))
        ttk.Label(toolbar, text="搜索", style="Section.TLabel").pack(side="left", padx=(18, 8))
        ttk.Entry(toolbar, textvariable=self.search_text, width=28).pack(side="left", ipady=4)
        self.search_text.trace_add("write", lambda *_args: self.refresh())
        ttk.Button(toolbar, text="清除", command=lambda: self.search_text.set("")).pack(side="left", padx=(6, 0))

        list_card = ttk.Frame(shell, style="Card.TFrame", padding=(10, 10, 4, 8))
        list_card.pack(fill="both", expand=True, pady=(0, 10))
        list_head = ttk.Frame(list_card, style="Card.TFrame")
        list_head.pack(fill="x", padx=(4, 8), pady=(0, 8))
        ttk.Label(list_head, text="机会列表", style="Section.TLabel").pack(side="left")
        self.count_label = ttk.Label(list_head, text="", style="Muted.TLabel")
        self.count_label.pack(side="right")
        table_frame = ttk.Frame(list_card, style="Card.TFrame")
        table_frame.pack(fill="both", expand=True)
        columns = (
            ("favorite", "收藏", 58, False), ("match", "匹配度", 76, False), ("category", "分类", 80, False),
            ("title", "消息标题", 390, True), ("source", "来源链接", 150, False),
            ("date", "发布日期", 100, False), ("seen", "首次发现", 165, False),
        )
        self.tree = ttk.Treeview(table_frame, columns=[c[0] for c in columns], show="headings", selectmode="browse")
        for key, label, width, stretch in columns:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=min(width, 110), stretch=stretch,
                             anchor="w" if key == "title" else "center")
        y_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=y_scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        y_scroll.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self.open_selected)
        self.tree.bind("<ButtonRelease-1>", self.handle_click)
        self.tree.bind("<Button-3>", self.show_conditions_popup)
        self.tree.bind("<<TreeviewSelect>>", self.show_summary)
        self.tree.tag_configure("favorite", background="#fff6d8")
        self.tree.tag_configure("hard_fail", foreground="#b33a3a")

        summary_box = ttk.Frame(shell, style="Card.TFrame", padding=14)
        summary_box.pack(fill="x", pady=(0, 10))
        ttk.Label(summary_box, text="详情与匹配分析", style="Section.TLabel").pack(anchor="w", pady=(0, 8))
        self.summary = tk.Text(summary_box, height=5, wrap="word", relief="flat", padx=10, pady=8,
                               background="#f7f9fc", foreground="#34465b", font=(FONT, 9), highlightthickness=0)
        self.summary.pack(fill="x")
        self.summary.insert("1.0", "选择一条消息，这里会显示简略介绍。")
        self.summary.tag_configure("link", foreground="#0563c1", underline=True)
        self.summary.tag_configure("unmet", foreground="#d00000", font=(FONT, 9, "bold"))
        self.summary.tag_configure("met", foreground="#287a30")
        self.summary.tag_bind("link", "<Button-1>", lambda _e: self.summary_link_url and webbrowser.open(self.summary_link_url))
        self.summary.tag_bind("link", "<Enter>", lambda _e: self.summary.config(cursor="hand2"))
        self.summary.tag_bind("link", "<Leave>", lambda _e: self.summary.config(cursor=""))
        self.summary.config(state="disabled")

        bottom = ttk.Frame(shell, style="App.TFrame")
        bottom.pack(fill="x")
        ttk.Label(bottom, textvariable=self.status, style="Muted.TLabel").pack(side="left", fill="x", expand=True)
        ttk.Label(bottom, text="双击打开原通知 · 右键查看条件", style="Muted.TLabel").pack(side="left", padx=12)
        ttk.Button(bottom, text="打开日志",
                   command=lambda: os.startfile(state.LOG_FILE) if state.LOG_FILE.exists() else None).pack(side="right")

    def _find(self, notice_id: str, saved: dict | None = None) -> dict | None:
        saved = saved or state.load_state()
        return next((n for n in saved.get("notices", []) if n["id"] == notice_id), None)

    def refresh(self):
        saved = state.load_state()
        self.tree.delete(*self.tree.get_children())
        notices = sorted(saved.get("notices", []), key=lambda x: x.get("first_seen", ""), reverse=True)
        favorites = set(saved.get("favorites", []))
        profile = saved.get("profile", {})
        selected = self.category_filter.get()
        visible = notices if selected == "全部" else [n for n in notices if n.get("category", "竞赛") == selected]
        query = self.search_text.get().strip().casefold()
        if query:
            visible = [n for n in visible
                       if query in " ".join((n.get("title", ""), n.get("summary", ""), n.get("source", ""))).casefold()]
        ranked = []
        for n in visible:
            score, _conditions, hard_fail = evaluate_notice(n, profile)
            ranked.append((hard_fail, -score, n))
        ranked.sort(key=lambda item: (item[0], item[1], item[2].get("first_seen", "")))
        for hard_fail, neg_score, n in ranked:
            is_favorite = n["id"] in favorites
            tags = tuple(tag for tag, on in (("favorite", is_favorite), ("hard_fail", hard_fail)) if on)
            self.tree.insert("", "end", iid=n["id"], tags=tags, values=(
                "●" if is_favorite else "○", f"{-neg_score}%", n.get("category", "竞赛"), n["title"],
                f"{n['source']} ↗", n.get("date", ""), n.get("first_seen", ""),
            ))
        self.count_label.config(text=f"显示 {len(visible)} / 共 {len(notices)} 条")
        detail = f"上次检测：{saved.get('last_check', '从未')}"
        if saved.get("last_error"):
            detail += "　部分来源暂时无法访问"
        self.status.set(detail)
        release = updates.pending_update(saved)
        if release:
            self.update_url = release.get("url") or updates.RELEASES_PAGE
            self.update_btn.config(text=f"新版本 v{release['version']}")
            if not self.update_btn.winfo_ismapped():
                self.update_btn.pack(side="left", padx=(0, 8), before=self.profile_btn)
        elif self.update_btn.winfo_ismapped():
            self.update_btn.pack_forget()

    def open_update_page(self):
        webbrowser.open(self.update_url or updates.RELEASES_PAGE)

    def open_selected(self, _event=None):
        selected = self.tree.selection()
        notice = self._find(selected[0]) if selected else None
        if notice:
            webbrowser.open(notice["url"])

    def handle_click(self, event):
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        row = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        if row and column == "#1":
            self.toggle_favorite(row)
        elif row and column == "#5":
            self.tree.selection_set(row)
            self.open_selected()

    def toggle_favorite(self, notice_id: str):
        def toggle(saved):
            favorites = set(saved.get("favorites", []))
            favorites.symmetric_difference_update({notice_id})
            saved["favorites"] = sorted(favorites)

        try:
            state.update_state(toggle)
        except Exception as exc:
            messagebox.showerror(self.school.app_name, f"保存收藏失败，请重试。\n{exc}")
            return
        self.refresh()
        if self.tree.exists(notice_id):
            self.tree.selection_set(notice_id)
            self.tree.focus(notice_id)

    def show_conditions_popup(self, event):
        row = self.tree.identify_row(event.y)
        if not row:
            return
        self.tree.selection_set(row)
        self.tree.focus(row)
        saved = state.load_state()
        notice = self._find(row, saved)
        if not notice:
            return
        score, conditions, hard_fail = evaluate_notice(notice, saved.get("profile", {}))

        popup = tk.Toplevel(self.root)
        popup.title("条件匹配详情")
        popup.geometry(f"620x430+{event.x_root + 8}+{event.y_root + 8}")
        popup.transient(self.root)
        frame = ttk.Frame(popup, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=notice["title"], font=(FONT, 12, "bold"), wraplength=570).pack(anchor="w")
        ttk.Label(frame, text=f"匹配度：{score}%" + ("　存在硬性条件不符" if hard_fail else ""),
                  foreground="#c62828" if hard_fail else "#333").pack(anchor="w", pady=(6, 12))

        detail = tk.Text(frame, wrap="word", relief="solid", borderwidth=1, padx=10, pady=10)
        detail.pack(fill="both", expand=True)
        detail.tag_configure("heading_ok", foreground="#237a2c", font=(FONT, 10, "bold"))
        detail.tag_configure("heading_bad", foreground="#c62828", font=(FONT, 10, "bold"))
        detail.tag_configure("ok", foreground="#237a2c")
        detail.tag_configure("bad", foreground="#d00000", font=(FONT, 9, "bold"))
        detail.tag_configure("source_condition", foreground="#7a4b00")
        detail.tag_configure("note", foreground="#666")
        detail.insert("end", "原通知识别出的硬性条件\n", "heading_bad")
        for condition in notice.get("hard_conditions") or ["未从网页正文识别到明确条件；请打开原通知及附件核对。"]:
            detail.insert("end", f"• {condition}\n", "source_condition")
        met = [c for c in conditions if not c[1]]
        unmet = [c for c in conditions if c[1]]
        detail.insert("end", "\n已达成条件\n", "heading_ok")
        for condition, _unmet, _hard in met or [("暂无可确认的已达成条件", False, False)]:
            detail.insert("end", f"✓ {condition}\n" if met else f"{condition}\n", "ok")
        detail.insert("end", "\n还需达成条件\n", "heading_bad")
        if unmet:
            for condition, _unmet, is_hard in unmet:
                detail.insert("end", f"✕ {condition}{'【硬性条件】' if is_hard else ''}\n", "bad")
        else:
            detail.insert("end", "当前没有识别到未达成条件\n", "ok")
        detail.insert("end", "\n判断依据来自网页公开文字；请以项目原通知为准。", "note")
        detail.config(state="disabled")
        ttk.Button(frame, text="打开原通知", command=lambda: webbrowser.open(notice["url"])).pack(side="left", pady=(10, 0))
        ttk.Button(frame, text="关闭", command=popup.destroy).pack(side="right", pady=(10, 0))

    def show_summary(self, _event=None):
        selected = self.tree.selection()
        if not selected:
            return
        saved = state.load_state()
        notice = self._find(selected[0], saved)
        self.summary.config(state="normal")
        self.summary.delete("1.0", "end")
        self.summary_link_url = ""
        if notice:
            self.summary.insert("end", (notice.get("summary") or f"{notice['source']}发布的相关通知，双击可打开原文。") + "\n\n")
            if notice.get("url"):
                self.summary.insert("end", "原通知链接：")
                self.summary.insert("end", notice["url"] + "\n\n", "link")
                self.summary_link_url = notice["url"]
            score, conditions, hard_fail = evaluate_notice(notice, saved.get("profile", {}))
            self.summary.insert("end", f"匹配度：{score}%" + ("（存在硬性条件不符）" if hard_fail else "") + "\n")
            for condition, unmet, _hard in conditions:
                self.summary.insert("end", ("✕ " if unmet else "✓ ") + condition + "\n", "unmet" if unmet else "met")
        self.summary.config(state="disabled")

    def open_profile(self):
        ProfileDialog(self.root, state.load_state().get("profile", {}), self.save_profile)

    def save_profile(self, profile: dict):
        try:
            state.update_state(lambda saved: saved.update({"profile": profile}))
        except Exception as exc:
            messagebox.showerror(self.school.app_name, f"保存个人档案失败，请重试。\n{exc}")
            return
        self.refresh()

    def start_check(self):
        self.check_btn.config(state="disabled")
        self.status.set("正在检查学校网站，请稍候……")
        threading.Thread(target=self._check_worker, daemon=True).start()

    def _check_worker(self):
        try:
            result = run_check(self.school, notify=True)
        except Exception as exc:
            state.log(f"检测任务异常: {exc!r}")
            self.root.after(0, self._check_failed)
            return
        self.root.after(0, lambda: self._check_done(*result))

    def _check_failed(self):
        self.check_btn.config(state="normal")
        self.status.set("检测意外中断，详情已写入日志。")
        messagebox.showerror(self.school.app_name, "检测过程中发生异常。请打开日志查看详情，然后重试。")

    def _check_done(self, new_items, errors, first):
        self.check_btn.config(state="normal")
        self.refresh()
        name = self.school.app_name
        if errors == [CHECK_ALREADY_RUNNING]:
            self.status.set("另一项检测正在运行，本次已跳过。")
            messagebox.showinfo(name, "另一项检测正在运行，本次已跳过。")
        elif first:
            messagebox.showinfo(name, "首次检测完成，已建立消息基线。以后只提醒新增消息。")
        elif new_items:
            messagebox.showinfo(name, f"检测完成，发现 {len(new_items)} 条新消息。")
        elif errors:
            messagebox.showwarning(name, "检测完成，没有发现新消息；部分网站暂时无法访问，稍后会自动重试。")
        else:
            messagebox.showinfo(name, "检测完成，没有发现新消息。")

    def run(self):
        self.root.mainloop()
