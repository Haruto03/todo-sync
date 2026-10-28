"""登録と同期を裏で行う。画面が固まらないよう、通信はすべて別スレッドで行う。"""

import logging
import threading
from typing import Callable

import config
import google_auth
import notion_api
import pending
import sync

logger = logging.getLogger("todo-sync")


class Background:
    def __init__(self, notify: Callable[[str, str], None], on_synced: Callable[[], None] = lambda: None):
        self.notify = notify  # notify(タイトル, 本文) → 画面右下の通知
        self.on_synced = on_synced  # 同期が終わるたびに呼ぶ（一覧を最新にするため）
        self.wake = threading.Event()
        self.wake.set()  # 起動したらすぐ1回同期する
        self.login_needed = False
        self.last_error = None
        threading.Thread(target=self.loop, daemon=True).start()

    # --- 登録 ---

    def add(self, fields: dict, summary: str) -> None:
        """Notionに登録し、すぐにGoogleへ同期する。失敗したら保留して、次の同期で再送する。"""
        def work():
            try:
                notion_api.create(fields)
            except Exception as e:
                logger.exception("Notionへの登録に失敗しました")
                pending.add(fields)
                self.notify("登録を保留しました", f"{summary}\nNotionに接続できないため、あとで自動で再送します。（{e}）"[:250])
                return
            self.notify("Todoを追加しました", summary)
            self.request_sync()

        threading.Thread(target=work, daemon=True).start()

    def update(self, page_id: str, fields: dict, title: str) -> None:
        """一覧画面での変更（完了・重要度・期限）をNotionに書き込み、すぐにGoogleへ同期する。"""
        def work():
            try:
                notion_api.update(page_id, fields)
            except Exception as e:
                logger.exception("Notionの更新に失敗しました")
                self.notify("変更を保存できませんでした", f"「{title}」: {e}"[:250])
                self.on_synced()  # 画面を実際の内容に戻す
                return
            self.request_sync()

        threading.Thread(target=work, daemon=True).start()

    # --- 同期 ---

    def request_sync(self) -> None:
        self.wake.set()

    def loop(self) -> None:
        while True:
            self.wake.wait(timeout=config.SYNC_INTERVAL_SEC)
            self.wake.clear()
            self.sync_now()

    def sync_now(self) -> None:
        try:
            sent = pending.flush()
            if sent:
                self.notify("保留していたTodoを登録しました", f"{sent}件")
            counts = sync.sync_once(interactive=False)
        except google_auth.LoginRequired:
            if not self.login_needed:  # 同じ通知を何度も出さない
                self.login_needed = True
                self.notify(
                    "Googleに再ログインしてください",
                    "通知領域のアイコンを右クリック →「Googleに再ログイン」。それまでの変更は、ログイン後にまとめて同期します。",
                )
            return
        except Exception as e:
            logger.exception("同期に失敗しました")
            if str(e) != self.last_error:
                self.last_error = str(e)
                self.notify("同期に失敗しました", str(e)[:250])
            return
        self.login_needed = False
        self.last_error = None
        if counts:
            logger.info("同期しました: %s", dict(counts))
        self.on_synced()

    def relogin(self) -> None:
        """ブラウザを開いてGoogleにログインし直す。"""
        def work():
            try:
                google_auth.get_credentials(interactive=True)
            except Exception as e:
                self.notify("Googleにログインできませんでした", str(e)[:250])
                return
            self.login_needed = False
            self.notify("Googleにログインしました", "同期を再開します。")
            self.request_sync()

        threading.Thread(target=work, daemon=True).start()
