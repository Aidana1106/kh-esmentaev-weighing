"""Управление пользователями из командной строки.

    python -m app.cli create-user --login vesovaya --name "Весовая" --role operator
    python -m app.cli set-password --login vesovaya
    python -m app.cli list-users

Пароль спрашивается интерактивно (или берётся из переменной PASSWORD).
"""
import argparse
import getpass
import os
import sys

from .config import load_settings
from .db import create_pool, migrate
from .security import hash_password


def _password() -> str:
    pw = os.environ.get("PASSWORD") or getpass.getpass("Пароль: ")
    if len(pw) < 6:
        sys.exit("Пароль должен быть не короче 6 символов")
    return pw


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-user")
    c.add_argument("--login", required=True)
    c.add_argument("--name", required=True)
    c.add_argument("--role", choices=["operator", "admin"], default="operator")
    s = sub.add_parser("set-password")
    s.add_argument("--login", required=True)
    d = sub.add_parser("disable-user")
    d.add_argument("--login", required=True)
    sub.add_parser("list-users")
    args = parser.parse_args()

    pool = create_pool(load_settings().database_url)
    migrate(pool)
    with pool.connection() as conn, conn.transaction():
        if args.cmd == "create-user":
            conn.execute(
                "INSERT INTO users (login, name, role, password_hash) VALUES (%s, %s, %s, %s)",
                (args.login.lower(), args.name, args.role, hash_password(_password())),
            )
            print(f"Создан пользователь {args.login} ({args.role})")
        elif args.cmd == "set-password":
            n = conn.execute(
                "UPDATE users SET password_hash = %s WHERE login = %s",
                (hash_password(_password()), args.login.lower()),
            ).rowcount
            if n:
                conn.execute(
                    "DELETE FROM sessions WHERE user_id = (SELECT id FROM users WHERE login = %s)",
                    (args.login.lower(),),
                )
            print("Пароль изменён" if n else "Пользователь не найден")
        elif args.cmd == "disable-user":
            n = conn.execute("UPDATE users SET active = false WHERE login = %s", (args.login.lower(),)).rowcount
            print("Отключён" if n else "Пользователь не найден")
        else:
            for r in conn.execute("SELECT login, name, role, active FROM users ORDER BY id"):
                print(f"{r['login']:<20} {r['role']:<9} {'активен' if r['active'] else 'отключён':<9} {r['name']}")
    pool.close()


if __name__ == "__main__":
    main()
