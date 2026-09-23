"""Generate a Pyrogram String Session locally.

Use this script on the PC where Telegram login is convenient. Do not run it
on Render and do not commit the resulting STRING_SESSION to git.
"""

from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv
from pyrogram import Client


async def main() -> None:
    load_dotenv()
    try:
        api_id = int(os.environ["API_ID"])
        api_hash = os.environ["API_HASH"].strip()
    except (KeyError, ValueError) as exc:
        raise SystemExit("Set API_ID and API_HASH in .env first.") from exc

    app = Client(
        "string_session_generator",
        api_id=api_id,
        api_hash=api_hash,
        in_memory=True,
    )

    await app.start()
    try:
        session = await app.export_session_string()
        print("\nSTRING_SESSION=\n")
        print(session)
        print("\nCopy it only to a secret store or directly to the private Control Bot chat.")
    finally:
        await app.stop()


if __name__ == "__main__":
    asyncio.run(main())
