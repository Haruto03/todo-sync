"""期限の選択肢と、日付指定の入力の読み取り。"""

import re
from datetime import date, timedelta

WEEKDAYS = "月火水木金土日"

# (選択肢のID, 表示名, Altと一緒に押すキー)
CHOICES = [
    ("today", "今日", "q"),
    ("tomorrow", "明日", "w"),
    ("weekend", "今週末", "e"),
    ("next_week", "来週", "r"),
    ("custom", "日付指定", "t"),
    ("none", "期限なし", "y"),
]
DEFAULT_CHOICE = "today"

# 日本語入力がオンのまま打っても読めるように、全角の数字・記号を半角にする
ZENKAKU = str.maketrans("０１２３４５６７８９／－．＋", "0123456789/-.+")


def this_sunday(today: date) -> date:
    """今週末 = 次の日曜日（今日が日曜ならその日）"""
    return today + timedelta(days=(6 - today.weekday()) % 7)


def next_monday(today: date) -> date:
    """来週 = 次の月曜日（今日が月曜なら7日後）"""
    return today + timedelta(days=(7 - today.weekday()) % 7 or 7)


def parse_date(text: str, today: date) -> date:
    """"11/5" "2026/11/5" "1105" "+3"（3日後）を日付にする。読めなければ ValueError。
    年を省略して今日より前の日付になったら、来年として扱う。"""
    text = text.strip().translate(ZENKAKU)
    if m := re.fullmatch(r"\+(\d{1,3})", text):
        return today + timedelta(days=int(m.group(1)))
    if m := re.fullmatch(r"(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})", text):
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.fullmatch(r"(\d{1,2})[/\-.](\d{1,2})", text) or re.fullmatch(r"(\d{2})(\d{2})", text)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        try:
            d = date(today.year, month, day)
        except ValueError:
            d = date(today.year + 1, month, day)  # 2/29 など今年にない日付
        return d if d >= today else date(today.year + 1, month, day)
    raise ValueError(text)


def resolve(choice: str, today: date, custom_text: str = "") -> date | None:
    """選択肢から期限の日付を求める。期限なしなら None、日付指定が読めなければ ValueError。"""
    if choice == "today":
        return today
    if choice == "tomorrow":
        return today + timedelta(days=1)
    if choice == "weekend":
        return this_sunday(today)
    if choice == "next_week":
        return next_monday(today)
    if choice == "custom":
        return parse_date(custom_text, today)
    return None


def format_due(d: date | None, today: date | None = None) -> str:
    """2026-10-28 → "10/28(水)"。今年以外なら年も付ける。"""
    if d is None:
        return "期限なし"
    today = today or date.today()
    head = f"{d.month}/{d.day}" if d.year == today.year else f"{d.year}/{d.month}/{d.day}"
    return f"{head}({WEEKDAYS[d.weekday()]})"
