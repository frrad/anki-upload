from __future__ import annotations

import argparse
import os
import sys
from dotenv import load_dotenv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple
import betterproto
import requests

load_dotenv()

CARD_SEPARATOR = "==="
SIDE_SEPARATOR = "---"


@dataclass(eq=False, repr=False)
class MessageAdd(betterproto.Message):
    deck_id: int = betterproto.int64_field(1)
    notetype_id: int = betterproto.int64_field(2)


@dataclass(eq=False, repr=False)
class Message(betterproto.Message):
    fields: list[str] = betterproto.string_field(1)
    tags: str = betterproto.string_field(2)
    add: MessageAdd = betterproto.message_field(3)


@dataclass
class Card:
    front: str
    back: str
    tags: str | None = None


def _split_on(text: str, separator: str) -> List[str]:
    """Split text into chunks on lines consisting solely of `separator`."""
    chunks: List[List[str]] = [[]]
    for line in text.splitlines():
        if line.strip() == separator:
            chunks.append([])
        else:
            chunks[-1].append(line)
    return ["\n".join(chunk) for chunk in chunks]


def parse_card(block: str, index: int) -> Card:
    lines = block.splitlines()

    tags = None
    if lines and lines[0].strip().lower().startswith("tags:"):
        tags = lines[0].split(":", 1)[1].strip() or None
        lines = lines[1:]

    sides = _split_on("\n".join(lines), SIDE_SEPARATOR)
    if len(sides) != 2:
        raise ValueError(
            f"card {index}: expected exactly one {SIDE_SEPARATOR!r} separator, "
            f"found {len(sides) - 1}"
        )

    front, back = (side.strip() for side in sides)
    if not front or not back:
        raise ValueError(f"card {index}: front and back must both be non-empty")

    return Card(front=front, back=back, tags=tags)


def parse_cards(text: str) -> List[Card]:
    """Parse a cards file.

    Cards are separated by a line of `===`, the front and back of each card by a
    line of `---`. Content is taken verbatim, so no escaping is needed. A card
    may start with a `tags: foo bar` line; to begin a front with a literal
    "tags:", precede it with a blank line.
    """
    cards = []
    for index, block in enumerate(_split_on(text, CARD_SEPARATOR), start=1):
        if not block.strip():
            continue
        cards.append(parse_card(block, index))
    return cards


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


def post_anki(payload: bytes, auth: str) -> Tuple[int, str]:
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
        "ankiweb": auth,
    }
    r = requests.post(url, headers=headers, cookies=cookies, data=payload)
    return r.status_code, r.text


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise SystemExit(f"{name} is not set (put it in .env)")
    return value


def read_cards_file(path: Path) -> str:
    if str(path) == "-":
        return sys.stdin.read()
    try:
        return path.read_text(encoding="utf-8")
    except OSError as e:
        raise SystemExit(f"could not read {path}: {e}")


def parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Add cards to AnkiWeb from a text file.",
        epilog=(
            f"Cards are separated by a line of {CARD_SEPARATOR!r}, front and back "
            f"by a line of {SIDE_SEPARATOR!r}. Content is used verbatim."
        ),
    )
    parser.add_argument(
        "cards_file",
        type=Path,
        help="file containing the cards to add, or - to read stdin",
    )
    parser.add_argument(
        "--deck-id", type=int, help="override ANKIWEB_DECK_ID from the environment"
    )
    parser.add_argument(
        "--notetype-id",
        type=int,
        help="override ANKIWEB_NOTETYPE_ID from the environment",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="parse and print the cards without uploading them",
    )
    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        cards = parse_cards(read_cards_file(args.cards_file))
    except ValueError as e:
        raise SystemExit(str(e))

    if not cards:
        raise SystemExit(f"no cards found in {args.cards_file}")

    if args.dry_run:
        for index, card in enumerate(cards, start=1):
            print(f"--- card {index} (tags: {card.tags or 'none'}) ---")
            print(f"front: {card.front}")
            print(f"back: {card.back}")
        print(f"{len(cards)} card(s) parsed, nothing uploaded")
        return 0

    deck_id = args.deck_id or int(require_env("ANKIWEB_DECK_ID"))
    notetype_id = args.notetype_id or int(require_env("ANKIWEB_NOTETYPE_ID"))
    auth = require_env("ANKIWEB_AUTH")

    failures = 0
    for index, card in enumerate(cards, start=1):
        payload = build_payload(
            front=card.front,
            back=card.back,
            deck_id=deck_id,
            notetype_id=notetype_id,
            tags=card.tags,
        )
        status, body = post_anki(payload, auth)
        print(f"card {index}/{len(cards)}: {status} {body}")
        if status != 200:
            failures += 1

    if failures:
        print(f"{failures} of {len(cards)} card(s) failed", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
