"""Todoをすばやく登録・確認するアプリ（Windows用）。

- テキストを選択して Ctrl+Alt+T → そのテキストをタイトルにして登録画面を表示
- 何も選択せずに Ctrl+Alt+T → 空の登録画面を表示
- Ctrl+Alt+L → 一覧画面（重要度ごと・未完了/完了）。その場で完了・重要度・期限を変えられる
- 登録・変更はNotionに保存し、すぐにGoogle ToDoリストへ同期する（数分おきにも同期）
- 通知領域（画面右下）のアイコンから、今すぐ同期・Googleに再ログイン・自動起動の設定・終了ができる

起動: .venv\\Scripts\\pythonw.exe app.py   （pythonw だと黒い画面が出ない）
"""

import ctypes
import logging
import queue
import threading
import tkinter as tk
import webbrowser
from datetime import date
from logging.handlers import RotatingFileHandler
from tkinter import messagebox

import pystray
from PIL import Image, ImageDraw, ImageFont

import config
import due
import hotkey
import notion_api
import startup
import sync
from add_popup import AddPopup, clean_selection
from background import Background
from list_popup import ListPopup
from notion_api import NotionTask

HOTKEY_ADD, HOTKEY_LIST = 1, 2
LOG_FILE = config.BASE_DIR / "todo-sync.log"
logger = logging.getLogger("todo-sync")


class App:
    def __init__(self) -> None:
        self.events: queue.Queue = queue.Queue()
        self.icon: pystray.Icon | None = None
        self.background: Background | None = None

        self.root = tk.Tk()
        self.root.withdraw()
        self.add_popup = AddPopup(self.root, self.on_submit)
        self.list_popup = ListPopup(self.root, self.on_list_edit, self.refresh_tasks)

    def start_background(self) -> None:
        self.background = Background(self.notify, on_synced=self.refresh_tasks)

    def notify(self, title: str, message: str) -> None:
        logger.info("通知: %s / %s", title, message)
        if self.icon:
            self.icon.notify(message, title)

    # --- 登録・変更 ---

    def on_submit(self, fields: dict) -> None:
        d = date.fromisoformat(fields["due"]) if fields["due"] else None
        summary = f"{config.IMPORTANCE_MARKS[fields['importance']]} {fields['title']}（期限: {due.format_due(d)}）"
        self.background.add(fields, summary)

    def on_list_edit(self, task: NotionTask, fields: dict) -> None:
        self.background.update(task.page_id, fields, task.title)

    def refresh_tasks(self) -> None:
        """Notionから一覧を取り直す（どのスレッドから呼んでもよい）。"""
        seq = self.list_popup.edit_seq

        def work():
            try:
                self.events.put(("tasks", notion_api.query_all(), seq))
            except Exception as e:
                logger.exception("一覧の取得に失敗しました")
                self.events.put(("tasks_error", str(e)))

        threading.Thread(target=work, daemon=True).start()

    # --- ホットキーとイベント ---

    def on_hotkey(self, hotkey_id: int, keys: list[int]) -> None:
        """ホットキーの監視スレッドから呼ばれる。画面の操作はメインスレッド（pump）で行う。"""
        if hotkey_id == HOTKEY_ADD:
            text, source = hotkey.capture_selection(keys, {self.add_popup.hwnd, self.list_popup.hwnd})
            self.events.put(("add", clean_selection(text) if text else "", source))
        elif hotkey_id == HOTKEY_LIST:
            self.events.put(("list",))

    def pump(self) -> None:
        try:
            while True:
                kind, *args = self.events.get_nowait()
                if kind == "add":
                    self.add_popup.open(*args)
                elif kind == "list":
                    self.list_popup.open()
                elif kind == "tasks":
                    self.list_popup.set_tasks(*args)
                elif kind == "tasks_error":
                    self.list_popup.set_status(f"✗ 一覧を取得できませんでした: {args[0]}"[:120], "error")
                elif kind == "quit":
                    self.root.destroy()
                    return
        except queue.Empty:
            pass
        self.root.after(100, self.pump)

    def run(self) -> None:
        self.root.after(100, self.pump)
        self.root.mainloop()


# ---------- 通知領域のアイコン ----------

def make_icon_image() -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2, 2, 62, 62), radius=14, fill=(26, 127, 55))
    try:
        font = ImageFont.truetype("segoeuib.ttf", 40)
    except OSError:
        font = ImageFont.load_default()
    d.text((32, 33), "T", fill="white", font=font, anchor="mm")
    return img


def start_tray(app: App) -> pystray.Icon:
    def open_notion():
        webbrowser.open(f"https://www.notion.so/{config.NOTION_DATABASE_ID.replace('-', '')}")

    def toggle_startup():
        try:
            startup.remove() if startup.is_installed() else startup.install()
        except Exception as e:
            app.notify("自動起動を設定できませんでした", str(e)[:250])

    def setup(icon):
        icon.visible = True
        icon.notify(f"{config.ADD_HOTKEY.upper()} で追加、{config.LIST_HOTKEY.upper()} で一覧を開きます。",
                    "todo-sync を起動しました")

    icon = pystray.Icon(
        "todo-sync",
        make_icon_image(),
        f"todo-sync（追加 {config.ADD_HOTKEY.upper()} / 一覧 {config.LIST_HOTKEY.upper()}）",
        menu=pystray.Menu(
            pystray.MenuItem("Todoを追加", lambda: app.events.put(("add", "", "")), default=True),
            pystray.MenuItem("Todo一覧", lambda: app.events.put(("list",))),
            pystray.MenuItem("今すぐ同期", lambda: app.background.request_sync()),
            pystray.MenuItem("Googleに再ログイン", lambda: app.background.relogin()),
            pystray.MenuItem("NotionのTodoを開く", open_notion),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("PCの起動時に自動で起動", toggle_startup, checked=lambda item: startup.is_installed()),
            pystray.MenuItem("終了", lambda: app.events.put(("quit",))),
        ),
    )
    threading.Thread(target=icon.run, kwargs={"setup": setup}, daemon=True).start()
    return icon


def setup_logging() -> None:
    handler = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=1, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    sync.log = logger.info  # 同期の内容もログに残す


def main() -> None:
    setup_logging()
    # 高DPI画面で文字がぼやけないようにする
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        ctypes.windll.user32.SetProcessDPIAware()

    missing = config.missing_env()
    if missing:
        messagebox.showerror("todo-sync", f".env に次の値が設定されていません:\n{', '.join(missing)}")
        return

    app = App()
    ready: queue.Queue = queue.Queue()
    hotkeys = {HOTKEY_ADD: config.ADD_HOTKEY, HOTKEY_LIST: config.LIST_HOTKEY}
    threading.Thread(target=hotkey.listen, args=(hotkeys, app.on_hotkey, ready), daemon=True).start()
    error = ready.get()
    if error:
        messagebox.showerror("todo-sync", error)
        return

    app.icon = start_tray(app)
    app.start_background()
    logger.info("起動しました（追加 %s / 一覧 %s）", config.ADD_HOTKEY, config.LIST_HOTKEY)
    try:
        app.run()
    finally:
        app.icon.stop()


if __name__ == "__main__":
    main()
