"""Interactive local account initialization/recovery; never accepts a password argument."""

import argparse
import getpass
from pathlib import Path

from quant_assistant.config import Settings
from quant_assistant.services.auth import AuthError, AuthService


def prompt_password():
    password = getpass.getpass("密码（15至128字符，不回显）: ")
    if password != getpass.getpass("确认密码: "):
        raise ValueError("两次输入的密码不一致。")
    return password


def initialize(root):
    service = AuthService(root)
    if service.status()["initialized"]:
        return
    print("首次启动：创建管理者账号。该账号使用现有账户数据，计入30人注册上限。")
    username = input("管理者用户名（3至32位字母、数字或下划线，以字母开头）: ").strip()
    service.bootstrap(username, prompt_password())
    print("管理者已创建，请在网页输入账号和密码登录。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("init-admin", "reset-password"))
    parser.add_argument("--data-dir", type=Path, default=Settings.from_env().data_dir)
    parser.add_argument("--username")
    args = parser.parse_args()
    try:
        if args.action == "init-admin":
            initialize(args.data_dir)
        else:
            username = args.username or input("需要重置的用户名: ")
            AuthService(args.data_dir).reset_password(username, prompt_password())
            print("密码已重置，该账号原有登录会话全部失效。")
    except (AuthError, ValueError, EOFError, KeyboardInterrupt) as error:
        print(str(error) if isinstance(error, ValueError) else "初始化已取消，未保存密码。")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
