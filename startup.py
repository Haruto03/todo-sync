"""PCの起動時に todo-sync を自動で起動する設定（スタートアップフォルダのショートカット）。

通知領域のアイコンのメニューから切り替えられる。コマンドでも設定できる:
    .venv\\Scripts\\python.exe startup.py           設定する
    .venv\\Scripts\\python.exe startup.py --remove  やめる
"""

import argparse
import os
import subprocess
from pathlib import Path

import config

SHORTCUT = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "todo-sync.lnk"
PYTHONW = config.BASE_DIR / ".venv" / "Scripts" / "pythonw.exe"  # pythonw だと黒い画面が出ない


def is_installed() -> bool:
    return SHORTCUT.exists()


def install() -> None:
    # ショートカットは WScript.Shell で作る（パスは環境変数で渡して、引用符の問題を避ける）
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:TODO_LNK); "
        "$s.TargetPath = $env:TODO_TARGET; $s.Arguments = 'app.py'; "
        "$s.WorkingDirectory = $env:TODO_DIR; $s.Description = 'todo-sync'; $s.Save()"
    )
    env = {**os.environ, "TODO_LNK": str(SHORTCUT), "TODO_TARGET": str(PYTHONW), "TODO_DIR": str(config.BASE_DIR)}
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        env=env, check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
    )


def remove() -> None:
    SHORTCUT.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="PCの起動時に todo-sync を自動で起動する")
    parser.add_argument("--remove", action="store_true", help="自動で起動するのをやめる")
    args = parser.parse_args()
    if args.remove:
        remove()
        print("✓ 自動で起動しないようにしました")
    else:
        install()
        print(f"✓ PCの起動時に自動で起動するようにしました（{SHORTCUT}）")


if __name__ == "__main__":
    main()
