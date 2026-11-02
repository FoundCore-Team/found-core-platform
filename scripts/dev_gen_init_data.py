"""Dev-only: generates a valid (or expired/corrupted) Telegram initData
for manually testing POST /api/v1/auth/telegram, signed with the same BOT_TOKEN
that is set in .env (APP_CONFIG__BOT__TOKEN).

Usage:
    python scripts/dev_gen_init_data.py
    python scripts/dev_gen_init_data.py --expired
    python scripts/dev_gen_init_data.py --bad-hash
    python scripts/dev_gen_init_data.py --telegram-id 555 --bot-token 123
    python scripts/dev_gen_init_data.py --telegram-id 1000000000001 --username seed_anna_backend --first-name Анна
    python scripts/dev_gen_init_data.py --no-username

Logging in as a seed user overwrites its Telegram profile fields with what is signed here, so pass the seed user's
own --username/--first-name (see scripts/data/seed_profiles/) to keep them intact.
"""

import argparse
import hashlib
import hmac
import json
import time
import urllib.parse

WEBAPP_DATA_KEY = b"WebAppData"


def build_init_data(
    bot_token: str,
    telegram_id: int,
    auth_date: int,
    bad_hash: bool,
    first_name: str = "Test",
    username: str | None = "testuser",
) -> str:
    user = {
        "id": telegram_id,
        "first_name": first_name,
        "last_name": "User",
        "language_code": "en",
    }
    if username is not None:
        user["username"] = username
    params = {
        "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
        "user": json.dumps(user, separators=(",", ":")),
        "auth_date": str(auth_date),
    }
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret_key = hmac.new(WEBAPP_DATA_KEY, bot_token.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    params["hash"] = "0" * 64 if bad_hash else computed_hash
    return "&".join(f"{k}={urllib.parse.quote(v, safe='')}" for k, v in params.items())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bot-token", default="123")
    parser.add_argument("--telegram-id", type=int, default=123456789)
    parser.add_argument("--expired", action="store_true", help="set auth_date 400 seconds in the past")
    parser.add_argument("--bad-hash", action="store_true", help="corrupt the hash")
    parser.add_argument("--first-name", default="Test")
    username_group = parser.add_mutually_exclusive_group()
    username_group.add_argument("--username", default="testuser")
    username_group.add_argument("--no-username", action="store_true", help="sign a user without a Telegram username")
    args = parser.parse_args()

    auth_date = int(time.time()) - (400 if args.expired else 0)
    username = None if args.no_username else args.username
    init_data = build_init_data(
        args.bot_token, args.telegram_id, auth_date, args.bad_hash, first_name=args.first_name, username=username
    )

    print(init_data)
    print()
    print("curl -X POST http://localhost:8000/api/v1/auth/telegram \\")
    print("  -H 'Content-Type: application/json' \\")
    print(f"  -d '{json.dumps({'init_data': init_data})}'")


if __name__ == "__main__":
    main()
