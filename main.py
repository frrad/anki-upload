"""CLI for reading, editing and bulk-adding AnkiWeb notes.

    ./venv/bin/python main.py login
    ./venv/bin/python main.py import examples/cards.txt
    ./venv/bin/python main.py get https://ankiuser.net/edit/1758491540484
    ./venv/bin/python main.py get 1758491540484 --json > note.json
    ./venv/bin/python main.py update https://ankiuser.net/edit/1758491540484 \
        --set-file Back=back.txt --tags numpy
    ./venv/bin/python main.py add --field Front=hello --field Back=world
    ./venv/bin/python main.py search 'deck:"ml 2025" tag:numpy'
    ./venv/bin/python main.py decks

Auth resolves in this order: the cached session file, then
ANKIWEB_USERNAME/ANKIWEB_PASSWORD from .env (logging in automatically and
renewing on expiry), then a bare ANKIWEB_AUTH cookie. The last is the
legacy path and only reaches the ankiuser.net editor endpoints, not search.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List

from fields import check, to_html

from anki import (
    SESSION_PATH,
    AnkiError,
    add_note,
    get_decks_and_notetypes,
    get_note,
    login,
    logout,
    parse_note_id,
    search_notes,
    set_session,
    update_note,
)

CARD_SEPARATOR = "==="
SIDE_SEPARATOR = "---"


def _parse_assignments(pairs: List[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for pair in pairs:
        name, sep, value = pair.partition("=")
        if not sep:
            raise SystemExit(f"expected NAME=VALUE, got {pair!r}")
        out[name.strip()] = value
    return out


# --------------------------------------------------------------------------
# cards file
# --------------------------------------------------------------------------


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

    return Card(front=to_html(front), back=to_html(back), tags=tags)


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


def read_cards_file(path: Path) -> str:
    if str(path) == "-":
        return sys.stdin.read()
    try:
        return path.read_text(encoding="utf-8")
    except OSError as e:
        raise SystemExit(f"could not read {path}: {e}")


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise SystemExit(f"{name} is not set (put it in .env)")
    return value


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------


def _field_order(notetype_id: int) -> List[str] | None:
    """Field names for `notetype_id`, or None if the server won't tell us.

    get-info-for-adding only reports fields for the *current* notetype, so
    anything else has to fall back to positional order.
    """
    info = get_decks_and_notetypes()
    if notetype_id != info.current_notetype_id:
        return None
    return [f.name for f in sorted(info.fields, key=lambda f: f.ord.val)]


def cmd_import(args: argparse.Namespace) -> int:
    try:
        cards = parse_cards(read_cards_file(args.cards_file))
    except ValueError as e:
        raise SystemExit(str(e))

    if not cards:
        raise SystemExit(f"no cards found in {args.cards_file}")

    # Validate every card before uploading any, so a bad card late in the file
    # cannot leave the deck half-imported.
    for index, card in enumerate(cards, start=1):
        check(f"card {index} front", card.front)
        check(f"card {index} back", card.back)

    if args.dry_run:
        for index, card in enumerate(cards, start=1):
            print(f"--- card {index} (tags: {card.tags or 'none'}) ---")
            print(f"front: {card.front}")
            print(f"back: {card.back}")
        print(f"{len(cards)} card(s) parsed, nothing uploaded")
        return 0

    deck_id = args.deck_id or int(require_env("ANKIWEB_DECK_ID"))
    notetype_id = args.notetype_id or int(require_env("ANKIWEB_NOTETYPE_ID"))

    # Pad front/back out to the notetype's real field count where we can, so
    # notetypes with more than two fields do not get their tail blanked.
    order = _field_order(notetype_id)
    width = max(len(order), 2) if order else 2

    failures = 0
    for index, card in enumerate(cards, start=1):
        values = [card.front, card.back] + [""] * (width - 2)
        try:
            add_note(values, deck_id, notetype_id, card.tags or "")
            print(f"card {index}/{len(cards)}: ok")
        except AnkiError as e:
            print(f"card {index}/{len(cards)}: FAILED {e}", file=sys.stderr)
            failures += 1

    if failures:
        print(f"{failures} of {len(cards)} card(s) failed", file=sys.stderr)
    return 1 if failures else 0


def cmd_get(args: argparse.Namespace) -> int:
    note = get_note(parse_note_id(args.note))

    if args.json:
        # The round-trippable form. The human layout below cannot be parsed
        # back: a field whose own content contains a "--- Name ---" line is
        # indistinguishable from a field boundary.
        json.dump(
            {
                "note_id": note.note_id,
                "url": note.url,
                "tags": note.tags,
                "fields": dict(zip(note.field_names, note.field_values)),
            },
            sys.stdout,
            indent=2,
            ensure_ascii=False,
        )
        print()
        return 0

    print(f"note {note.note_id}  {note.url}")
    print(f"tags: {note.tags or '(none)'}")
    for name, value in zip(note.field_names, note.field_values):
        print(f"\n--- {name} ---")
        print(value)
    return 0


def _read_field_files(pairs: List[str]) -> Dict[str, str]:
    """NAME=PATH -> {NAME: file contents}, with the trailing newline dropped.

    An editor will end the file with a newline that was never part of the
    field, and a trailing <br> is a visible blank line on the card.
    """
    out: Dict[str, str] = {}
    for name, path in _parse_assignments(pairs).items():
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError as e:
            raise SystemExit(f"could not read {path}: {e}")
        out[name] = text[:-1] if text.endswith("\n") else text
    return out


def cmd_update(args: argparse.Namespace) -> int:
    note_id = parse_note_id(args.note)
    changes = _parse_assignments(args.set or [])
    from_files = _read_field_files(args.set_file or [])

    both = sorted(set(changes) & set(from_files))
    if both:
        raise SystemExit(f"field(s) given by both --set and --set-file: {both}")
    changes.update(from_files)

    if not changes and args.tags is None:
        raise SystemExit(
            "nothing to do: pass --set NAME=VALUE, --set-file NAME=PATH and/or --tags"
        )

    for name, value in changes.items():
        check(name, value)

    before = get_note(note_id)
    note = update_note(note_id, changes, args.tags)

    print(f"updated note {note.note_id}  {note.url}")
    for name, old, new in zip(note.field_names, before.field_values, note.field_values):
        if old != new:
            print(f"  {name}: {old!r} -> {new!r}")
    if before.tags != note.tags:
        print(f"  tags: {before.tags!r} -> {note.tags!r}")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    deck_id = args.deck_id or os.getenv("ANKIWEB_DECK_ID")
    notetype_id = args.notetype_id or os.getenv("ANKIWEB_NOTETYPE_ID")
    if not deck_id or not notetype_id:
        raise SystemExit(
            "need --deck-id/--notetype-id or ANKIWEB_DECK_ID/ANKIWEB_NOTETYPE_ID"
        )

    named = _parse_assignments(args.field or [])
    from_files = _read_field_files(args.field_file or [])

    both = sorted(set(named) & set(from_files))
    if both:
        raise SystemExit(f"field(s) given by both --field and --field-file: {both}")
    named.update(from_files)

    order = _field_order(int(notetype_id)) or list(named)

    unknown = set(named) - set(order)
    if unknown:
        raise SystemExit(f"unknown field(s) {sorted(unknown)}; available: {order}")

    for name, value in named.items():
        check(name, value)

    values = [named.get(name, "") for name in order]
    add_note(values, int(deck_id), int(notetype_id), args.tags or "")
    print(f"added note to deck {deck_id} with fields {order}")
    return 0


def cmd_login(args: argparse.Namespace) -> int:
    username = (
        args.username or os.getenv("ANKIWEB_USERNAME") or input("AnkiWeb email: ")
    )
    password = os.getenv("ANKIWEB_PASSWORD") or getpass.getpass("Password: ")

    session = login(username, password)
    set_session(session)
    session.save()
    print(f"logged in as {session.username}; session saved to {SESSION_PATH}")
    print("  ankiweb.net cookie:  " + ("yes" if session.ankiweb_cookie else "no"))
    print("  ankiuser.net cookie: " + ("yes" if session.ankiuser_cookie else "no"))
    return 0


def cmd_logout(args: argparse.Namespace) -> int:
    print("session removed" if logout() else "no stored session")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    notes = search_notes(args.query)
    if not notes:
        print("no matches")
        return 0
    for n in notes:
        preview = " ".join(n.joined_fields.split())[: args.width]
        print(f"{n.note_id}  {preview}")
    print(f"\n{len(notes)} note(s)")
    return 0


def cmd_decks(args: argparse.Namespace) -> int:
    info = get_decks_and_notetypes()
    print("NOTETYPES:")
    for n in info.notetypes:
        mark = " *" if n.id == info.current_notetype_id else ""
        print(f"  {n.id}  {n.name}{mark}")
    print("\nDECKS:")
    for d in info.decks:
        mark = " *" if d.id == info.current_deck_id else ""
        print(f"  {d.id}  {d.name}{mark}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="command", required=True)

    i = sub.add_parser(
        "import",
        help="bulk-add cards from a cards file",
        epilog=(
            f"Cards are separated by a line of {CARD_SEPARATOR!r}, front and back "
            f"by a line of {SIDE_SEPARATOR!r}. Content is used verbatim."
        ),
    )
    i.add_argument(
        "cards_file",
        type=Path,
        help="file containing the cards to add, or - to read stdin",
    )
    i.add_argument(
        "--deck-id", type=int, help="override ANKIWEB_DECK_ID from the environment"
    )
    i.add_argument(
        "--notetype-id",
        type=int,
        help="override ANKIWEB_NOTETYPE_ID from the environment",
    )
    i.add_argument(
        "--dry-run",
        action="store_true",
        help="parse and print the cards without uploading them",
    )
    i.set_defaults(func=cmd_import)

    g = sub.add_parser("get", help="print a note")
    g.add_argument("note", help="note id or https://ankiuser.net/edit/<id> URL")
    g.add_argument(
        "--json",
        action="store_true",
        help="emit JSON, the only form that can be read back without ambiguity",
    )
    g.set_defaults(func=cmd_get)

    u = sub.add_parser("update", help="edit fields of an existing note")
    u.add_argument("note", help="note id or https://ankiuser.net/edit/<id> URL")
    u.add_argument(
        "--set",
        action="append",
        metavar="NAME=VALUE",
        help="set a field by name; repeatable. Unset fields keep their value.",
    )
    u.add_argument(
        "--set-file",
        action="append",
        metavar="NAME=PATH",
        help="set a field from a file; repeatable. Preferred for anything with "
        "quotes, backslashes or newlines, which the shell would mangle.",
    )
    u.add_argument("--tags", help="replace the note's tags (space separated)")
    u.set_defaults(func=cmd_update)

    a = sub.add_parser("add", help="create a single note")
    a.add_argument(
        "--field",
        action="append",
        metavar="NAME=VALUE",
        help="set a field by name; repeatable",
    )
    a.add_argument(
        "--field-file",
        action="append",
        metavar="NAME=PATH",
        help="set a field from a file; repeatable. Preferred for anything with "
        "quotes, backslashes or newlines, which the shell would mangle.",
    )
    a.add_argument("--deck-id", help="defaults to $ANKIWEB_DECK_ID")
    a.add_argument("--notetype-id", help="defaults to $ANKIWEB_NOTETYPE_ID")
    a.add_argument("--tags", help="tags (space separated)")
    a.set_defaults(func=cmd_add)

    d = sub.add_parser("decks", help="list decks and notetypes")
    d.set_defaults(func=cmd_decks)

    li = sub.add_parser("login", help="authenticate and cache a session")
    li.add_argument("--username", help="defaults to $ANKIWEB_USERNAME, else prompts")
    li.set_defaults(func=cmd_login)

    lo = sub.add_parser("logout", help="delete the cached session")
    lo.set_defaults(func=cmd_logout)

    s = sub.add_parser("search", help="find notes (Anki search syntax)")
    s.add_argument("query", help="e.g. 'deck:\"ml 2025\" tag:foo'")
    s.add_argument("--width", type=int, default=90, help="preview width")
    s.set_defaults(func=cmd_search)

    return p


def main(argv: List[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # argparse.Namespace attributes are Any; pin the handler's real signature.
    handler: Callable[[argparse.Namespace], int] = args.func
    try:
        return handler(args)
    except (AnkiError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
