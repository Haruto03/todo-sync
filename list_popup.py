"""Todoの一覧画面（Ctrl+Alt+L で開くポップアップ）。

重要度ごとにまとめて表示し、その場で完了・重要度・期限を変えられる。
変更はNotionに書き込み、すぐにGoogle ToDoリストへ同期する。
"""

import dataclasses
import tkinter as tk
import tkinter.font as tkfont
import webbrowser
from datetime import date, datetime
from typing import Callable

import config
import due
import hotkey
import sync
from add_popup import COLORS, FONT, IMPORTANCE_COLORS
from notion_api import NotionTask

TABS = [("open", "未完了"), ("done", "完了"), ("all", "すべて")]
DONE_LIMIT = 100  # 完了タブに出す件数（新しい順）
ROWS_MAX = 20  # これより多ければスクロール
SELECTED_BG = "#ddf4ff"
# 列の位置（ポイント）。タイトル → 期限（右揃え）→ 残り日数（右揃え）
DATE_RIGHT, REL_RIGHT, TITLE_WIDTH = 330, 400, 225


def due_columns(t: NotionTask, today: date) -> tuple[str, str, str]:
    """(期限の日付, 残り日数, 色のタグ)"""
    if t.due is None:
        return "期限なし", "", "sub"
    d = date.fromisoformat(t.due)
    text = due.format_due(d, today)
    if t.done:
        return text, "", "sub"
    days = (d - today).days
    if t.original_due and days <= 0:  # 期限を過ぎて持ち越している
        return text, f"{(today - date.fromisoformat(t.original_due)).days}日持ち越し", "overdue"
    if days < 0:
        return text, f"{-days}日超過", "overdue"
    if days == 0:
        return text, "今日", "today"
    if days == 1:
        return text, "明日", "normal"
    return text, f"あと{days}日" if days <= 7 else "", "normal"


def sort_key(t: NotionTask):
    # 期限順。同じ日なら、持ち越しが長いものを先に
    return (t.due is None, t.due or "", t.original_due or "9999-12-31", t.title)


class ListPopup:
    def __init__(self, root: tk.Tk, on_edit: Callable[[NotionTask, dict], None], on_refresh: Callable[[], None]):
        self.on_edit = on_edit  # on_edit(変更前のタスク, 変更する項目)
        self.on_refresh = on_refresh
        self.tasks: list[NotionTask] | None = None  # None = まだ読み込んでいない
        self.edit_seq = 0  # 変更するたびに増やす。変更前に取得した古い一覧で上書きしないため
        self.tab = "open"
        self.selected_id = None
        self.rows: list[NotionTask] = []
        self.line_of: dict[str, int] = {}
        self.visible = False

        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=COLORS["border"])
        self.font = tkfont.Font(family=FONT, size=11)
        self.build()
        self.hwnd = hotkey.root_hwnd(self.win)
        self.bind_keys()
        self.win.bind("<FocusOut>", lambda e: self.win.after(150, self.close_if_unfocused))

    # --- 画面の組み立て ---

    def build(self) -> None:
        c = COLORS
        frame = tk.Frame(self.win, bg=c["bg"], padx=18, pady=14)
        frame.pack(padx=1, pady=1)

        head = tk.Frame(frame, bg=c["bg"])
        head.pack(fill="x")
        tk.Label(head, text="Todo一覧", bg=c["bg"], fg=c["sub"], font=(FONT, 9)).pack(side="left")
        tk.Label(head, text="← → でタブ切り替え ・ Esc で閉じる", bg=c["bg"], fg=c["sub"],
                 font=(FONT, 9)).pack(side="right")

        tabs = tk.Frame(frame, bg=c["bg"])
        tabs.pack(fill="x", pady=(8, 6))
        self.tab_chips = {}
        for tab, label in TABS:
            chip = tk.Label(tabs, text=label, font=(FONT, 10), padx=10, pady=2, cursor="hand2",
                            highlightthickness=1, highlightbackground=c["border"])
            chip.pack(side="left", padx=(0, 6))
            chip.bind("<Button-1>", lambda e, t=tab: self.set_tab(t))
            self.tab_chips[tab] = (chip, label)

        px = self.win.winfo_fpixels("1p")
        self.title_px = int(TITLE_WIDTH * px)
        self.text = tk.Text(
            frame, font=self.font, relief="flat", bg=c["bg"], fg=c["text"], cursor="arrow", wrap="none",
            width=int(REL_RIGHT * px / self.font.measure("0")) + 2, height=10, highlightthickness=0,
            borderwidth=0, padx=4, pady=2, spacing1=3, spacing3=3, takefocus=0,
            tabs=(f"{DATE_RIGHT}p", "right", f"{REL_RIGHT}p", "right"),
        )
        self.text.pack(fill="both")
        t = self.text
        t.tag_configure("group", font=(FONT, 10, "bold"), spacing1=8)
        for name, color in IMPORTANCE_COLORS.items():
            t.tag_configure(f"imp_{name}", foreground=color)
        t.tag_configure("title_done", foreground=c["sub"], font=tkfont.Font(family=FONT, size=11, overstrike=True))
        t.tag_configure("sub", foreground=c["sub"])
        t.tag_configure("normal", foreground=c["text"])
        t.tag_configure("overdue", foreground=c["error"])
        t.tag_configure("today", foreground=c["info"], font=(FONT, 11, "bold"))
        t.tag_configure("empty", foreground=c["sub"], justify="center", spacing1=20, spacing3=20)
        t.tag_configure("selected", background=SELECTED_BG)
        t.tag_raise("selected")
        t.bind("<Button-1>", self.on_click)
        t.bind("<Double-Button-1>", lambda e: (self.toggle(), "break")[1])

        # 期限の日付指定（T を押したときだけ出す）
        self.due_row = tk.Frame(frame, bg=c["bg"])
        tk.Label(self.due_row, text="期限を入力", bg=c["bg"], fg=c["sub"], font=(FONT, 10)).pack(side="left")
        self.due_entry = tk.Entry(
            self.due_row, font=(FONT, 11), width=12, relief="flat", bg=c["chip"], fg=c["text"],
            highlightthickness=1, highlightbackground=c["border"], highlightcolor=c["info"],
            insertbackground=c["text"],
        )
        self.due_entry.pack(side="left", padx=(8, 0), ipady=2)
        tk.Label(self.due_row, text="例: 11/5、1105、+3   Enter で決定 ・ Esc でやめる", bg=c["bg"],
                 fg=c["sub"], font=(FONT, 9)).pack(side="left", padx=(8, 0))
        self.due_entry.bind("<Return>", lambda e: self.apply_custom_due())
        self.due_entry.bind("<Escape>", lambda e: self.hide_due_entry())

        self.status = tk.Label(frame, bg=c["bg"], fg=c["sub"], font=(FONT, 9), anchor="w")
        self.status.pack(fill="x", pady=(8, 0))
        tk.Label(frame, text="↑↓ 選択 ・ Space 完了 ・ 1/2/3 重要度 ・ Q〜Y 期限（今日/明日/今週末/来週/日付指定/なし）"
                             " ・ Enter Notionで開く",
                 bg=c["bg"], fg=c["sub"], font=(FONT, 8), anchor="w").pack(fill="x")

    def bind_keys(self) -> None:
        def key(seq, fn):
            # 日付を入力中は、数字や文字を一覧の操作として扱わない
            self.win.bind(seq, lambda e: None if self.win.focus_get() is self.due_entry else fn())

        key("<Up>", lambda: self.move(-1))
        key("<Down>", lambda: self.move(1))
        key("<Prior>", lambda: self.move(-10))
        key("<Next>", lambda: self.move(10))
        key("<Left>", lambda: self.shift_tab(-1))
        key("<Right>", lambda: self.shift_tab(1))
        key("<Tab>", lambda: self.shift_tab(1))
        key("<space>", self.toggle)
        key("<Return>", self.open_in_notion)
        key("<Escape>", self.hide)
        key("<F5>", self.on_refresh)
        for i, name in enumerate(config.IMPORTANCE_MARKS, start=1):
            key(f"<Key-{i}>", lambda n=name: self.set_importance(n))
        for choice, _, k in due.CHOICES:
            for seq in (k, k.upper(), f"Alt-{k}", f"Alt-{k.upper()}"):  # 登録画面と同じ Alt+キーでも効く
                key(f"<{seq}>", lambda ch=choice: self.set_due(ch))

    # --- 表示 ---

    def visible_tasks(self) -> list[NotionTask]:
        tasks = self.tasks or []
        open_ = sorted((t for t in tasks if not t.done), key=sort_key)
        done = sorted((t for t in tasks if t.done), key=lambda t: t.done_at or "", reverse=True)[:DONE_LIMIT]
        return {"open": open_, "done": done, "all": open_ + done}[self.tab]

    def fit(self, text: str) -> str:
        """列からはみ出すタイトルを「…」で切る。"""
        if self.font.measure(text) <= self.title_px:
            return text
        while text and self.font.measure(text + "…") > self.title_px:
            text = text[:-1]
        return text + "…"

    def render(self) -> None:
        c = COLORS
        tasks = self.tasks or []
        counts = {"open": sum(not t.done for t in tasks), "done": sum(t.done for t in tasks), "all": len(tasks)}
        for tab, (chip, label) in self.tab_chips.items():
            selected = tab == self.tab
            chip.config(text=f"{label} {counts[tab]}", bg=c["info"] if selected else c["chip"],
                        fg="white" if selected else c["text"])

        t = self.text
        t.config(state="normal")
        t.delete("1.0", "end")
        old_rows = self.rows
        self.rows, self.line_of = [], {}
        today = date.today()
        items = self.visible_tasks()
        if self.tasks is None:
            t.insert("end", "読み込んでいます…\n", ("empty",))
        elif not items:
            t.insert("end", {"open": "残っているタスクはありません", "done": "完了したタスクはまだありません"}
                     .get(self.tab, "タスクはありません") + "\n", ("empty",))
        for name in config.IMPORTANCE_MARKS:
            group = [x for x in items if (x.importance or config.DEFAULT_IMPORTANCE) == name]
            if not group:
                continue
            t.insert("end", "● ", (f"imp_{name}", "group"))
            t.insert("end", f"{name}  {len(group)}件\n", ("group",))
            for task in group:
                self.line_of[task.page_id] = int(t.index("end-1c").split(".")[0])
                self.rows.append(task)
                date_text, rel, tag = due_columns(task, today)
                t.insert("end", "  ☑  " if task.done else "  ☐  ", ("sub",))
                t.insert("end", self.fit(task.title), ("title_done",) if task.done else ("normal",))
                t.insert("end", f"\t{date_text}\t{rel}\n", (tag,))
        t.config(state="disabled", height=min(max(int(t.index("end-1c").split(".")[0]), 4), ROWS_MAX))

        # 選んでいたタスクが消えたら（完了にして未完了タブから外れたなど）、同じ位置のタスクを選ぶ
        if self.selected_id not in self.line_of:
            old_index = next((i for i, x in enumerate(old_rows) if x.page_id == self.selected_id), 0)
            self.selected_id = self.rows[min(old_index, len(self.rows) - 1)].page_id if self.rows else None
        self.highlight()
        self.relayout()

    def highlight(self) -> None:
        self.text.tag_remove("selected", "1.0", "end")
        line = self.line_of.get(self.selected_id)
        if line:
            self.text.tag_add("selected", f"{line}.0", f"{line + 1}.0")
            self.text.see(f"{line}.0")

    def relayout(self) -> None:
        if not self.visible:
            return
        self.win.update_idletasks()
        w, h = self.win.winfo_reqwidth(), self.win.winfo_reqheight()
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        self.win.geometry(f"{w}x{h}+{(sw - w) // 2}+{max((sh - h) // 3, 8)}")

    def set_status(self, text: str, color: str = "sub") -> None:
        self.status.config(text=text, fg=COLORS[color])

    # --- データの受け取り ---

    def set_tasks(self, tasks: list[NotionTask], seq: int) -> None:
        """一覧を最新にする。取得を始めた後に変更していたら、古い内容なので使わない。"""
        if seq < self.edit_seq:
            return
        self.tasks = tasks
        self.set_status(f"最新の状態です（{datetime.now():%H:%M} 更新）")
        if self.visible:
            self.render()

    # --- 操作 ---

    def selected(self) -> NotionTask | None:
        return next((x for x in self.rows if x.page_id == self.selected_id), None)

    def move(self, delta: int) -> None:
        if not self.rows:
            return
        index = next((i for i, x in enumerate(self.rows) if x.page_id == self.selected_id), 0)
        self.selected_id = self.rows[max(0, min(index + delta, len(self.rows) - 1))].page_id
        self.highlight()

    def on_click(self, event) -> str:
        line, col = map(int, self.text.index(f"@{event.x},{event.y}").split("."))
        page_id = next((pid for pid, ln in self.line_of.items() if ln == line), None)
        if page_id:
            self.selected_id = page_id
            self.highlight()
            if col <= 3:  # チェックボックスの部分をクリック → 完了を切り替える
                self.toggle()
        return "break"  # 文字の選択やカーソルを出さない

    def set_tab(self, tab: str) -> None:
        self.tab = tab
        self.selected_id = None
        self.render()

    def shift_tab(self, delta: int) -> str:
        ids = [t for t, _ in TABS]
        self.set_tab(ids[(ids.index(self.tab) + delta) % len(ids)])
        return "break"

    def apply(self, task: NotionTask, fields: dict, message: str) -> None:
        """画面にはすぐ反映し、Notionへの書き込みは裏で行う。"""
        self.edit_seq += 1
        changed = dataclasses.replace(task, **fields)
        self.tasks = [changed if x.page_id == task.page_id else x for x in self.tasks or []]
        self.render()
        self.set_status(f"「{task.title}」{message}（同期しています…）", "info")
        self.on_edit(task, fields)

    def toggle(self) -> None:
        task = self.selected()
        if task:
            done = not task.done
            self.apply(task, {"done": done, "done_at": sync.now_iso() if done else None},
                       "を完了にしました" if done else "を未完了に戻しました")

    def set_importance(self, name: str) -> None:
        task = self.selected()
        if task and task.importance != name:
            self.apply(task, {"importance": name}, f"の重要度を{name}にしました")

    def set_due(self, choice: str) -> None:
        if not self.selected():
            return
        if choice == "custom":
            self.due_entry.delete(0, "end")
            self.due_row.pack(fill="x", pady=(6, 0), before=self.status)
            self.due_entry.focus_set()
            self.relayout()
            return
        self.change_due(due.resolve(choice, date.today()))

    def apply_custom_due(self) -> str:
        try:
            d = due.parse_date(self.due_entry.get(), date.today())
        except ValueError:
            self.set_status("日付が読めません（例: 11/5、1105、+3）", "error")
            return "break"
        self.hide_due_entry()
        self.change_due(d)
        return "break"

    def hide_due_entry(self) -> str:
        self.due_row.pack_forget()
        self.win.focus_set()
        self.relayout()
        return "break"

    def change_due(self, d: date | None) -> None:
        task = self.selected()
        value = d.isoformat() if d else None
        if task and task.due != value:
            self.apply(task, {"due": value}, f"の期限を{due.format_due(d)}にしました")

    def open_in_notion(self) -> None:
        task = self.selected()
        if task and task.url:
            webbrowser.open(task.url)
            self.hide()

    def open(self) -> None:
        self.visible = True
        self.due_row.pack_forget()
        self.win.deiconify()
        self.render()
        self.win.lift()
        self.win.focus_force()
        hotkey.bring_to_front(self.hwnd)
        self.win.focus_set()
        self.on_refresh()  # 表示は手元の内容ですぐ出し、裏で最新にする

    def close_if_unfocused(self) -> None:
        if self.visible and self.win.focus_displayof() is None:
            self.hide()

    def hide(self) -> None:
        self.visible = False
        self.win.withdraw()
