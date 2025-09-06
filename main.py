from __future__ import annotations

import os
from dotenv import load_dotenv
from dataclasses import dataclass
from typing import Dict, Tuple
import betterproto
import requests

load_dotenv()


@dataclass(eq=False, repr=False)
class MessageAdd(betterproto.Message):
    deck_id: int = betterproto.int64_field(1)
    notetype_id: int = betterproto.int64_field(2)


@dataclass(eq=False, repr=False)
class Message(betterproto.Message):

    fields: list[str] = betterproto.string_field(1)

    tags: str = betterproto.string_field(2)

    add: MessageAdd = betterproto.message_field(3)


def build_payload(
    front: str, back: str, deck_id: int, notetype_id: int, tags: str | None = None
) -> bytes:
    """General builder for arbitrary inputs."""
    msg = Message()
    msg.fields = [front, back]
    if tags:
        msg.tags = tags
    msg.add = MessageAdd()
    msg.add.deck_id = int(deck_id)
    msg.add.notetype_id = int(notetype_id)
    return bytes(msg)


def post_anki(payload: bytes) -> Tuple[int, str]:
    url = "https://ankiuser.net/svc/editor/add-or-update"
    headers: Dict[str, str] = {
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9",
        "cache-control": "no-cache",
        "content-type": "application/octet-stream",
        "origin": "https://ankiuser.net",
        "pragma": "no-cache",
        "priority": "u=1, i",
        "referer": "https://ankiuser.net/add",
        "sec-ch-ua": '"Not;A=Brand";v="99", "Google Chrome";v="139", "Chromium";v="139"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Chrome OS"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": (
            "Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
        ),
    }
    cookies: Dict[str, str] = {
        "has_auth": "1",
        "ankiweb": os.getenv("ANKIWEB_AUTH"),
    }
    r = requests.post(url, headers=headers, cookies=cookies, data=payload)
    return r.status_code, r.text


if __name__ == "__main__":
    payload = build_payload(
        front="234234front",
        back="backeroni08",
        deck_id=int(os.getenv("ANKIWEB_DECK_ID")),
        notetype_id=int(os.getenv("ANKIWEB_NOTETYPE_ID")),
        tags=None,
    )

    status, body = post_anki(payload)
    print("Status:", status)
    print("Body:", body)
