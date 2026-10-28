"""Todoの登録画面（Ctrl+Alt+T で開くポップアップ）。

タイトル + 重要度（Alt+1〜3）+ 期限（Alt+Q〜Y）。Enter で登録、Esc でやめる。
"""

import tkinter as tk
from datetime import date, timedelta
from typing import Callable

import config
import due
import hotkey

COLORS = {
    "bg": "#ffffff", "border": "#d0d7de", "text": "#1f2328", "sub": "#59636e",
    "chip": "#f6f8fa", "info": "#0969da", "error": "#cf222e",
}
IMPORTANCE_COLORS = {"高": "#cf222e", "中": "#bf8700", "低": "#1a7f37"}
FONT = "Yu Gothic UI"
TITLE_MAX = 200  # 選択したテキストが長いときは、ここで切る


def clean_selection(text: str) -> str:
    """改行や連続した空白を1つの空白にまとめる。"""
    return " ".join(text.split())[:TITLE_MAX]


class AddPopup:
    def __init__(self, root: tk.Tk, on_submit: Callable[[dict], None]):
        self.on_submit = on_submit
        self.importance = config.DEFAULT_IMPORTANCE
        self.due_choice = due.DEFAULT_CHOICE
        self.source = ""
        self.draft = None  # クリックで他のウィンドウに移ったときの入力途中の内容
        self.visible = False

        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=COLORS["border"])
        self.build()
        self.hwnd = hotkey.root_hwnd(self.win)

        self.win.bind("<Return>", lambda e: self.submit())
        self.win.bind("<Escape>", lambda e: self.cancel())
        for i, name in enumerate(config.IMPORTANCE_MARKS, start=1):
            self.win.bind(f"<Alt-Key-{i}>", lambda e, n=name: self.set_importance(n))
        for choice, _, key in due.CHOICES:
            for k in (key, key.upper()):  # CapsLock がオンでも効くように
                self.win.bind(f"<Alt-{k}>", lambda e, c=choice: self.set_due(c))
        self.win.bind("<FocusOut>", lambda e: self.win.after(150, self.close_if_unfocused))

    # --- 画面の組み立て ---

    def build(self) -> None:
        c = COLORS
        frame = tk.Frame(self.win, bg=c["bg"], padx=18, pady=14)
        frame.pack(padx=1, pady=1)

        head = tk.Frame(frame, bg=c["bg"])
        head.pack(fill="x")
        tk.Label(head, text="Todoを追加", bg=c["bg"], fg=c["sub"], font=(FONT, 9)).pack(side="left")
        tk.Label(head, text="Enter で登録 ・ Esc でやめる", bg=c["bg"], fg=c["sub"], font=(FONT, 9)).pack(side="right")

        self.entry = self.text_entry(frame, size=14, width=40)
        self.entry.pack(fill="x", pady=(8, 10), ipady=4)

        row = self.row(frame, "重要度")
        self.importance_chips = {}
        for i, name in enumerate(config.IMPORTANCE_MARKS, start=1):
            self.importance_chips[name] = self.chip(row, f"{name} ({i})", lambda n=name: self.set_importance(n))

        row = self.row(frame, "期限")
        self.due_chips = {}
        for choice, label, key in due.CHOICES:
            self.due_chips[choice] = self.chip(row, f"{label} ({key.upper()})", lambda ch=choice: self.set_due(ch))

        self.custom_row = tk.Frame(frame, bg=c["bg"])
        tk.Label(self.custom_row, text="", bg=c["bg"], width=6).pack(side="left")
        self.custom = self.text_entry(self.custom_row, size=11, width=12)
        self.custom.pack(side="left", ipady=2)
        tk.Label(self.custom_row, text="例: 11/5、1105、+3（3日後）  ↑↓で1日ずつ",
                 bg=c["bg"], fg=c["sub"], font=(FONT, 9)).pack(side="left", padx=(8, 0))
        self.custom.bind("<KeyRelease>", lambda e: self.refresh())
        self.custom.bind("<Up>", lambda e: self.shift_custom(1))
        self.custom.bind("<Down>", lambda e: self.shift_custom(-1))

        self.preview = tk.Label(frame, bg=c["bg"], font=(FONT, 10), anchor="w")
        self.preview.pack(fill="x", pady=(8, 0))
        tk.Label(frame, text="Alt を押しながら（ ）のキーでも選べます", bg=c["bg"], fg=c["sub"],
                 font=(FONT, 8), anchor="w").pack(fill="x")

    def text_entry(self, parent, size: int, width: int) -> tk.Entry:
        c = COLORS
        return tk.Entry(
            parent, font=(FONT, size), width=width, relief="flat", bg=c["chip"], fg=c["text"],
            highlightthickness=1, highlightbackground=c["border"], highlightcolor=c["info"],
            insertbackground=c["text"],
        )

    def row(self, parent, label: str) -> tk.Frame:
        frame = tk.Frame(parent, bg=COLORS["bg"])
        frame.pack(fill="x", pady=3)
        tk.Label(frame, text=label, bg=COLORS["bg"], fg=COLORS["sub"], font=(FONT, 10),
                 width=6, anchor="w").pack(side="left")
        return frame

    def chip(self, parent, text: str, command) -> tk.Label:
        chip = tk.Label(parent, text=text, font=(FONT, 10), padx=8, pady=2, cursor="hand2",
                        highlightthickness=1, highlightbackground=COLORS["border"])
        chip.pack(side="left", padx=(0, 6))
        chip.bind("<Button-1>", lambda e: command())
        return chip

    # --- 表示の更新 ---

    def refresh(self) -> None:
        c = COLORS
        for name, chip in self.importance_chips.items():
            selected = name == self.importance
            chip.config(bg=IMPORTANCE_COLORS[name] if selected else c["chip"], fg="white" if selected else c["text"])
        for choice, chip in self.due_chips.items():
            selected = choice == self.due_choice
            chip.config(bg=c["info"] if selected else c["chip"], fg="white" if selected else c["text"])

        if self.due_choice == "custom":
            self.custom_row.pack(fill="x", pady=(2, 0), before=self.preview)
        else:
            self.custom_row.pack_forget()

        try:
            d = self.current_due()
            text = f"期限: {due.format_due(d)}" if d else "期限: なし（Googleカレンダーには表示されません）"
            self.preview.config(text=text, fg=c["sub"])
        except ValueError:
            text = "日付を入力してください" if not self.custom.get().strip() else "日付が読めません（例: 11/5、1105、+3）"
            self.preview.config(text=text, fg=c["error"])
        self.relayout()

    def relayout(self) -> None:
        """内容に合わせて大きさを変え、画面からはみ出さない位置に置き直す。"""
        if not self.visible:
            return
        self.win.update_idletasks()
        w, h = self.win.winfo_reqwidth(), self.win.winfo_reqheight()
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        x = min(max(self.anchor[0], 8), sw - w - 8)
        y = self.anchor[1]
        if y + h > sh - 48:  # 下にはみ出すならカーソルの上に出す
            y = max(self.anchor[1] - h - 40, 8)
        self.win.geometry(f"{w}x{h}+{x}+{y}")

    # --- 操作 ---

    def current_due(self) -> date | None:
        return due.resolve(self.due_choice, date.today(), self.custom.get())

    def set_importance(self, name: str) -> None:
        self.importance = name
        self.refresh()

    def set_due(self, choice: str) -> None:
        self.due_choice = choice
        self.refresh()
        (self.custom if choice == "custom" else self.entry).focus_set()

    def shift_custom(self, days: int) -> str:
        try:
            d = due.parse_date(self.custom.get(), date.today())
        except ValueError:
            d = date.today()
        d += timedelta(days=days)
        self.custom.delete(0, "end")
        self.custom.insert(0, f"{d.month}/{d.day}" if d.year == date.today().year else f"{d.year}/{d.month}/{d.day}")
        self.refresh()
        return "break"

    def open(self, title: str = "", source: str = "") -> None:
        px, py = self.win.winfo_pointerxy()
        self.anchor = (px - 40, py + 24)
        if not title and self.draft:
            title, self.importance, self.due_choice, custom = self.draft  # 入力途中の内容を戻す
        else:
            self.importance, self.due_choice, custom = config.DEFAULT_IMPORTANCE, due.DEFAULT_CHOICE, ""
        self.draft = None
        self.source = source
        self.entry.delete(0, "end")
        self.entry.insert(0, title)
        self.custom.delete(0, "end")
        self.custom.insert(0, custom)

        self.visible = True
        self.win.deiconify()
        self.win.lift()
        self.win.focus_force()
        hotkey.bring_to_front(self.hwnd)
        self.entry.focus_set()
        self.entry.icursor("end")
        self.refresh()

    def submit(self) -> None:
        title = self.entry.get().strip()
        if not title:
            self.preview.config(text="タイトルを入力してください", fg=COLORS["error"])
            self.entry.focus_set()
            return
        try:
            d = self.current_due()
        except ValueError:
            self.custom.focus_set()
            return
        self.hide()
        self.on_submit({
            "title": title,
            "importance": self.importance,
            "due": d.isoformat() if d else None,
            "source": self.source,
        })

    def cancel(self) -> None:
        self.draft = None
        self.hide()

    def close_if_unfocused(self) -> None:
        """他のウィンドウをクリックしたら閉じる。入力途中の内容は、次に開いたときに戻す。"""
        if self.visible and self.win.focus_displayof() is None:
            title = self.entry.get().strip()
            self.draft = (title, self.importance, self.due_choice, self.custom.get()) if title else None
            self.hide()

    def hide(self) -> None:
        self.visible = False
        self.win.withdraw()
