import argparse
import os
import secrets

import uvicorn
from cryptography.fernet import Fernet
from dotenv import load_dotenv

from .config import Settings
from .server import create_app


def main():
    parser = argparse.ArgumentParser(description="QQ Mail personal MCP server")
    parser.add_argument(
        "command", choices=["serve", "generate-secrets"], nargs="?", default="serve"
    )
    args = parser.parse_args()
    if args.command == "generate-secrets":
        # Intentionally printed to the user's own terminal; never generated during tests or logs.
        print("QQ_MCP_ADMIN_PASSWORD=" + secrets.token_urlsafe(32))
        print("QQ_MCP_ENCRYPTION_KEY=" + Fernet.generate_key().decode())
        return
    os.umask(0o077)
    load_dotenv(override=False)
    settings = Settings.from_env()
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        access_log=False,
        proxy_headers=False,
    )


if __name__ == "__main__":
    main()
