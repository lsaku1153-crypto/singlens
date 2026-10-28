"""账号管理 CLI。

用法（在项目根目录）：
  .venv\\Scripts\\python.exe tools\\accounts.py init
  .venv\\Scripts\\python.exe tools\\accounts.py create --username s01 --singer S01
  .venv\\Scripts\\python.exe tools\\accounts.py passwd --username s01
  .venv\\Scripts\\python.exe tools\\accounts.py list
  .venv\\Scripts\\python.exe tools\\accounts.py disable --username s01

`init` 只补齐不存在的账号，不改已有密码；生成的一次性密码写入
`data/initial_accounts.txt`（已 gitignore）。不要把该文件提交。
"""
from __future__ import annotations

import argparse
import getpass
import os
import re
import secrets
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from web import auth  # noqa: E402
from web import practice_store  # noqa: E402

INIT_FILE = os.path.join(ROOT, "data", "initial_accounts.txt")


def _ask_password() -> str:
    while True:
        a = getpass.getpass("密码（至少 10 位）: ")
        b = getpass.getpass("再输一次: ")
        if a != b:
            print("两次不一致，重来。")
            continue
        if len(a) < auth.MIN_PASSWORD:
            print(f"至少 {auth.MIN_PASSWORD} 位。")
            continue
        return a


def _username_for_singer(sid: str, name: str) -> str:
    if sid == "u_1628490f":
        return "self"
    low = sid.lower()
    if re.match(r"^[a-z0-9_.-]{3,32}$", low):
        return low
    # 中文/非法字符的 singer_id：退成稳定短名
    return "s_" + re.sub(r"[^a-z0-9]+", "", low)[:20] or "s_" + secrets.token_hex(3)


def cmd_init(args) -> int:
    auth.init_db()
    singers = practice_store.list_singers()
    created = []
    admin_name = args.admin_user
    if not auth.get_user_by_username(admin_name):
        pwd = args.admin_password or secrets.token_urlsafe(12)
        u = auth.create_user(username=admin_name, password=pwd, role="admin",
                             display_name="管理员")
        created.append((u["username"], pwd, "admin", ""))
    for s in singers:
        sid = s["singer_id"]
        uname = _username_for_singer(sid, s.get("name") or "")
        if auth.get_user_by_username(uname):
            continue
        pwd = secrets.token_urlsafe(12)
        try:
            u = auth.create_user(username=uname, password=pwd, role="singer",
                                 singer_id=sid, display_name=s.get("name") or sid)
            created.append((u["username"], pwd, "singer", sid))
        except ValueError as e:
            print(f"跳过 {sid}: {e}")
    if not created:
        print("没有新建账号（账号已存在）。")
        return 0
    os.makedirs(os.path.dirname(INIT_FILE), exist_ok=True)
    with open(INIT_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n# {__import__('datetime').datetime.now().isoformat(timespec='seconds')}\n")
        for row in created:
            f.write("\t".join(row) + "\n")
    try:
        os.chmod(INIT_FILE, 0o600)
    except OSError:
        pass
    print(f"已新建 {len(created)} 个账号，密码写入：{INIT_FILE}")
    print("请登录后尽快修改密码；该文件不要提交/外发。")
    return 0


def cmd_create(args) -> int:
    pwd = args.password or _ask_password()
    u = auth.create_user(
        username=args.username, password=pwd, role=args.role,
        singer_id=args.singer, display_name=args.display_name or args.username,
    )
    print(f"已创建 {u['username']} role={u['role']} singer={u['singer_id'] or '-'}")
    return 0


def cmd_passwd(args) -> int:
    u = auth.get_user_by_username(args.username)
    if not u:
        print("没有这个账号。")
        return 1
    pwd = args.password or _ask_password()
    auth.set_password(u["user_id"], pwd)
    auth.revoke_user_sessions(u["user_id"])
    print("密码已更新，所有旧会话已失效。")
    return 0


def cmd_disable(args) -> int:
    u = auth.get_user_by_username(args.username)
    if not u:
        print("没有这个账号。")
        return 1
    auth.set_disabled(u["user_id"], not args.enable)
    if not args.enable:
        auth.revoke_user_sessions(u["user_id"])
    print(("已启用 " if args.enable else "已停用 ") + args.username)
    return 0


def cmd_list(_args) -> int:
    for u in auth.list_users():
        print(f"{u['username']:<18} role={u['role']:<6} "
              f"singer={u['singer_id'] or '-':<14} {u['display_name']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    p.add_argument("--admin-user", default="admin")
    p.add_argument("--admin-password", default="")
    p.set_defaults(func=cmd_init)
    p = sub.add_parser("create")
    p.add_argument("--username", required=True)
    p.add_argument("--password", default="")
    p.add_argument("--role", choices=["singer", "admin"], default="singer")
    p.add_argument("--singer", default=None)
    p.add_argument("--display-name", default="")
    p.set_defaults(func=cmd_create)
    p = sub.add_parser("passwd")
    p.add_argument("--username", required=True)
    p.add_argument("--password", default="")
    p.set_defaults(func=cmd_passwd)
    p = sub.add_parser("disable")
    p.add_argument("--username", required=True)
    p.add_argument("--enable", action="store_true")
    p.set_defaults(func=cmd_disable)
    p = sub.add_parser("list")
    p.set_defaults(func=cmd_list)
    args = ap.parse_args()
    auth.init_db()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
