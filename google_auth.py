"""Google ToDoリスト（Tasks API）へのログイン。

初回だけブラウザが開いてログインを求められ、以降は token.json を使って自動でログインする。
（Google Cloudのアプリが「テスト中」のため、7日ごとにログインし直しが必要）
"""

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

import config


class LoginRequired(RuntimeError):
    """ログインが切れていて、ブラウザでログインし直す必要がある。"""


def get_credentials(interactive: bool = True) -> Credentials:
    """interactive=False のときは、ログインが切れていたらブラウザを開かずに LoginRequired を投げる
    （裏で動く同期処理で、突然ブラウザが開かないようにするため）。"""
    if not config.GOOGLE_CREDENTIALS_FILE.exists():
        raise RuntimeError(
            f"{config.GOOGLE_CREDENTIALS_FILE.name} が見つかりません。\n"
            "  → Google Cloudでダウンロードしたクライアントのファイルを、この名前でフォルダに置いてください。"
        )

    creds = None
    if config.GOOGLE_TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(config.GOOGLE_TOKEN_FILE), config.GOOGLE_SCOPES)

    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception:
            creds = None  # 7日の期限切れ・取り消しなどで更新できない → ログインし直す
    if not creds or not creds.valid:
        if not interactive:
            raise LoginRequired("Googleへのログインが切れました。ログインし直してください。")
        flow = InstalledAppFlow.from_client_secrets_file(str(config.GOOGLE_CREDENTIALS_FILE), config.GOOGLE_SCOPES)
        creds = flow.run_local_server(port=0, prompt="consent")

    config.GOOGLE_TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    return creds


def tasks_service(interactive: bool = True):
    return build("tasks", "v1", credentials=get_credentials(interactive), cache_discovery=False)
