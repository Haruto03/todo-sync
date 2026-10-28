"""グローバルホットキーと、選択中のテキストの取得（Windows API）。

word-saver のホットキーアプリと同じ仕組みで、複数のホットキーを登録できるようにしたもの。
"""

import ctypes
import ctypes.wintypes as wt
import queue
import time
from typing import Callable

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
user32.GetForegroundWindow.restype = wt.HWND
user32.GetAncestor.restype = wt.HWND
user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.OpenClipboard.argtypes = [wt.HWND]
user32.GetClipboardData.restype = wt.HANDLE
user32.GetClipboardData.argtypes = [wt.UINT]
user32.SetClipboardData.restype = wt.HANDLE
user32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
kernel32.GlobalAlloc.restype = wt.HGLOBAL
kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [wt.HGLOBAL]
kernel32.GlobalUnlock.argtypes = [wt.HGLOBAL]
kernel32.GlobalFree.argtypes = [wt.HGLOBAL]

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x4000
MODIFIERS = {"ctrl": (MOD_CONTROL, 0x11), "alt": (MOD_ALT, 0x12), "shift": (MOD_SHIFT, 0x10)}
WM_HOTKEY = 0x0312
VK_CONTROL, VK_C = 0x11, 0x43
KEYEVENTF_KEYUP = 0x2
GA_ROOT = 2
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x2
# Ctrl+C を送ると実行中の処理が止まってしまうコンソール系のウィンドウ
CONSOLE_CLASSES = {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS"}


def parse_hotkey(text: str) -> tuple[int, int, list[int]]:
    """"ctrl+alt+t" → (修飾キーのフラグ, キーの仮想キーコード, 押されているか確認するキー一覧)"""
    *mods, key = [p.strip().lower() for p in text.split("+")]
    if len(key) != 1 or not key.isalnum() or any(m not in MODIFIERS for m in mods):
        raise ValueError(f"ホットキーの形式が正しくありません: {text}（例: ctrl+alt+t）")
    flags = MOD_NOREPEAT
    keys = [ord(key.upper())]
    for m in mods:
        flags |= MODIFIERS[m][0]
        keys.append(MODIFIERS[m][1])
    return flags, ord(key.upper()), keys


def root_hwnd(widget) -> int:
    """tkinter のウィンドウの、Windows上のハンドル"""
    widget.update_idletasks()
    return user32.GetAncestor(widget.winfo_id(), GA_ROOT)


def bring_to_front(hwnd: int) -> None:
    user32.SetForegroundWindow(hwnd)


# ---------- クリップボード ----------

def open_clipboard() -> bool:
    # コピーした直後は、コピー元のアプリがまだクリップボードを開いていることがあるので少し待つ
    for _ in range(10):
        if user32.OpenClipboard(None):
            return True
        time.sleep(0.02)
    return False


def get_clipboard_text() -> str | None:
    """クリップボードの文字列。文字列でなければ（画像など） None。"""
    if not open_clipboard():
        return None
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return None
        try:
            return ctypes.wstring_at(ptr)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def set_clipboard_text(text: str) -> None:
    data = ctypes.create_unicode_buffer(text)
    size = ctypes.sizeof(data)
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
    if not handle:
        return
    ptr = kernel32.GlobalLock(handle)
    ctypes.memmove(ptr, data, size)
    kernel32.GlobalUnlock(handle)
    if not open_clipboard():
        kernel32.GlobalFree(handle)
        return
    try:
        user32.EmptyClipboard()
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)  # 成功したときはWindowsが管理するので解放しない
    finally:
        user32.CloseClipboard()


# ---------- 選択中のテキスト ----------

def window_text(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def wait_keys_released(keys: list[int], timeout: float = 1.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if not any(user32.GetAsyncKeyState(k) & 0x8000 for k in keys):
            return True
        time.sleep(0.02)
    return False


def capture_selection(hotkey_keys: list[int], own_hwnds: set[int]) -> tuple[str | None, str]:
    """前面のアプリで選択中のテキストと、そのウィンドウのタイトルを返す。
    Ctrl+C を送ってコピーし、もとのクリップボードの文字列は書き戻す（画像などは戻せない）。"""
    fg = user32.GetForegroundWindow()
    if not fg or fg in own_hwnds:
        return None, ""
    source = window_text(fg)
    cls = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(fg, cls, 256)
    if cls.value in CONSOLE_CLASSES:
        return None, source
    # ホットキーの Ctrl/Alt が押されたままだと Ctrl+Alt+C になってしまうので、離されるのを待つ
    if not wait_keys_released(hotkey_keys):
        return None, source

    before = get_clipboard_text()
    seq = user32.GetClipboardSequenceNumber()
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(VK_C, 0, 0, 0)
    user32.keybd_event(VK_C, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)

    end = time.time() + 0.5
    while time.time() < end:
        if user32.GetClipboardSequenceNumber() != seq:
            time.sleep(0.03)
            text = get_clipboard_text()
            if before is not None:
                set_clipboard_text(before)
            return text, source
        time.sleep(0.02)
    return None, source  # 何も選択されていなかった


# ---------- ホットキーの監視（別スレッドで動かす） ----------

def listen(hotkeys: dict[int, str], on_hotkey: Callable[[int, list[int]], None], ready: queue.Queue) -> None:
    """hotkeys = {ID: "ctrl+alt+t", …} を登録し、押されたら on_hotkey(ID, キー一覧) を呼ぶ。
    登録の結果を ready に入れる（成功なら None、失敗ならエラーメッセージ）。"""
    keys_by_id = {}
    for hotkey_id, text in hotkeys.items():
        try:
            flags, vk, keys = parse_hotkey(text)
        except ValueError as e:
            ready.put(str(e))
            return
        if not user32.RegisterHotKey(None, hotkey_id, flags, vk):
            ready.put(f"ホットキー {text.upper()} を登録できませんでした。\n"
                      "todo-sync が既に起動しているか、他のアプリが同じキーを使っています。")
            return
        keys_by_id[hotkey_id] = keys
    ready.put(None)

    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == WM_HOTKEY and msg.wParam in keys_by_id:
            on_hotkey(msg.wParam, keys_by_id[msg.wParam])
