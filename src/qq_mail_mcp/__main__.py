import argparse
import json
import os
import secrets
import sqlite3
import webbrowser

from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv

from .config import Settings
from .connect import CLIENTS, recipes
from .maintenance import backup, initialize, restore


def main():
    parser = argparse.ArgumentParser(description="QQ Mail personal MCP server")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve")
    sub.add_parser("generate-secrets")
    init = sub.add_parser("init", help="Create a private .env without overwriting existing keys")
    init.add_argument("--url", required=True)
    init.add_argument("--file", default=".env")
    init.add_argument("--direct-send", action="store_true")
    connect = sub.add_parser("connect", help="Show client-specific connection instructions")
    connect.add_argument("client", choices=CLIENTS)
    connect.add_argument("--url", required=True)
    connect.add_argument(
        "--open", action="store_true", help="Open the client's official install link"
    )
    back = sub.add_parser("backup", help="Create a consistent encrypted database snapshot")
    back.add_argument("target")
    recover = sub.add_parser("restore", help="Restore a snapshot with the original encryption key")
    recover.add_argument("source")
    recover.add_argument("--confirm-offline", action="store_true", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.command == "generate-secrets":
            print("QQ_MCP_ADMIN_PASSWORD=" + secrets.token_urlsafe(32))
            print("QQ_MCP_ENCRYPTION_KEY=" + Fernet.generate_key().decode())
            return
        if args.command == "init":
            initialize(args.file, args.url, args.direct_send)
            print("Private configuration created. Find the owner password in your local file.")
            return
        if args.command == "connect":
            entry = recipes(args.url)[args.client]
            if args.open:
                if not entry.get("install_url"):
                    parser.error(
                        "This client uses the command/configuration below, not an install URL"
                    )
                webbrowser.open(entry["install_url"])
            print(
                entry.get("command")
                or entry.get("install_url")
                or entry.get("url")
                or json.dumps(entry["config"], indent=2)
            )
            print(entry["next"])
            return
        load_dotenv(override=False)
        settings = Settings.from_env()
        if args.command == "backup":
            backup(settings.database, args.target)
            print("Encrypted database snapshot created.")
            return
        if args.command == "restore":
            restore(settings, args.source)
            print("Database restored. A snapshot of the previous state was retained.")
            return
    except (OSError, ValueError, InvalidToken, sqlite3.DatabaseError):
        parser.error(
            "Operation failed. Check paths, configuration and encryption key. "
            "Existing files were preserved."
        )
    import uvicorn

    from .server import create_app

    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        access_log=False,
        proxy_headers=False,
    )


if __name__ == "__main__":
    main()
