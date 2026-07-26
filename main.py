"""CLI for reading and editing AnkiWeb notes.

    ./venv/bin/python main.py login
    ./venv/bin/python main.py get https://ankiuser.net/edit/1758491540484
    ./venv/bin/python main.py update https://ankiuser.net/edit/1758491540484 \
        --set Back='<div>np.argwhere(cond)</div>' --tags numpy
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
import os
import sys
from typing import Dict, List

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


def _parse_assignments(pairs: List[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for pair in pairs:
        name, sep, value = pair.partition("=")
        if not sep:
            raise SystemExit(f"expected NAME=VALUE, got {pair!r}")
        out[name.strip()] = value
    return out


def cmd_get(args: argparse.Namespace) -> int:
    note = get_note(parse_note_id(args.note))
    print(f"note {note.note_id}  {note.url}")
    print(f"tags: {note.tags or '(none)'}")
    for name, value in zip(note.field_names, note.field_values):
        print(f"\n--- {name} ---")
        print(value)
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    note_id = parse_note_id(args.note)
    changes = _parse_assignments(args.set or [])
    if not changes and args.tags is None:
        raise SystemExit("nothing to do: pass --set NAME=VALUE and/or --tags")

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
    info = get_decks_and_notetypes()
    if int(notetype_id) == info.current_notetype_id:
        order = [f.name for f in sorted(info.fields, key=lambda f: f.ord.val)]
    else:
        # get-info-for-adding only returns fields for the current notetype,
        # so fall back to the order the values were given on the command line.
        order = list(named)

    unknown = set(named) - set(order)
    if unknown:
        raise SystemExit(f"unknown field(s) {sorted(unknown)}; available: {order}")

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

    g = sub.add_parser("get", help="print a note")
    g.add_argument("note", help="note id or https://ankiuser.net/edit/<id> URL")
    g.set_defaults(func=cmd_get)

    u = sub.add_parser("update", help="edit fields of an existing note")
    u.add_argument("note", help="note id or https://ankiuser.net/edit/<id> URL")
    u.add_argument(
        "--set",
        action="append",
        metavar="NAME=VALUE",
        help="set a field by name; repeatable. Unset fields keep their value.",
    )
    u.add_argument("--tags", help="replace the note's tags (space separated)")
    u.set_defaults(func=cmd_update)

    a = sub.add_parser("add", help="create a note")
    a.add_argument(
        "--field",
        action="append",
        metavar="NAME=VALUE",
        help="set a field by name; repeatable",
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
    try:
        return args.func(args)
    except (AnkiError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
