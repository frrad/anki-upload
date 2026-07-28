"""Normalize an AnkiWeb note's field markup to <br>-only.

Anki renders fields as HTML, so a literal newline collapses; MathJax needs
each \\( ... \\) span to be one unbroken run of text. <br> as the only markup
satisfies both. See SKILL.md in this directory.

    python .claude/skills/anki-notes/normalize.py <url-or-id>
    python .claude/skills/anki-notes/normalize.py <url-or-id> --apply
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from anki import get_note, parse_note_id, update_note  # noqa: E402

OPEN_DIV_RE = re.compile(r"<div[^>]*>", re.I)
BR_RE = re.compile(r"<br\s*/?>", re.I)
# A <br> that only fills out its enclosing div, rather than opening a line.
TRAILING_BR_RE = re.compile(r"<br\s*/?>\s*(?=</div>)", re.I)
TAG_RE = re.compile(r"<[^>]+>")
INLINE_MATH_RE = re.compile(r"\\\((.*?)\\\)", re.S)
DISPLAY_MATH_RE = re.compile(r"\\\[(.*?)\\\]", re.S)
# A real HTML entity, not a bare "&" -- inside an `aligned` environment "&" is
# the LaTeX alignment marker and is perfectly legitimate.
ENTITY_RE = re.compile(r"&(?:[a-zA-Z][a-zA-Z0-9]*|#\d+|#[xX][0-9a-fA-F]+);")


def to_lines(field: str) -> List[str]:
    """Field HTML -> plain text lines, tolerating the web editor's stray divs.

    A <br> immediately before </div> fills out the div rather than opening
    another line -- a browser renders <div><br></div> as one blank line, not
    two -- so it is absorbed before <br> is treated as a line separator.
    Only the opening <div> is a line boundary; the closing tag is not, or
    every div would be followed by a spurious blank line.
    """
    s = TRAILING_BR_RE.sub("", field)
    s = BR_RE.sub("\n", s)
    s = OPEN_DIV_RE.sub("\n", s)
    s = TAG_RE.sub("", s)
    s = s.replace("&nbsp;", " ")
    s = html.unescape(s)
    lines = [line.rstrip() for line in s.split("\n")]
    while lines and lines[0] == "":
        lines.pop(0)
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def normalize(field: str) -> str:
    return "<br>".join(to_lines(field))


def visible_words(s: str) -> str:
    """Everything a reader would see, with all whitespace flattened."""
    s = BR_RE.sub(" ", s)
    s = TAG_RE.sub(" ", s).replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def check(name: str, value: str) -> None:
    """Fail loudly rather than write a field that will not render."""
    for pattern, kind in ((INLINE_MATH_RE, "inline"), (DISPLAY_MATH_RE, "display")):
        for span in pattern.findall(value):
            if TAG_RE.search(span):
                raise SystemExit(f"{name}: HTML tag inside {kind} math: {span!r}")
            if ENTITY_RE.search(span) or "\xa0" in span:
                raise SystemExit(f"{name}: entity inside {kind} math: {span!r}")
    if "\xa0" in value or "&nbsp;" in value:
        raise SystemExit(f"{name}: non-breaking space survived")
    stray = {t for t in TAG_RE.findall(value) if not BR_RE.fullmatch(t)}
    if stray:
        raise SystemExit(f"{name}: unexpected markup {sorted(stray)}")


def main(argv: List[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("note", help="note id or https://ankiuser.net/edit/<id> URL")
    p.add_argument("--apply", action="store_true", help="write the change")
    p.add_argument("--backup", type=Path, help="write the current note here first")
    args = p.parse_args(argv)

    note_id = parse_note_id(args.note)
    note = get_note(note_id)

    if args.backup:
        args.backup.write_text(
            json.dumps(
                {"note_id": note.note_id, "fields": note.fields, "tags": note.tags},
                indent=2,
            )
        )
        print(f"backed up to {args.backup}\n")

    new_fields: Dict[str, str] = {}
    for name, value in note.fields.items():
        new_value = normalize(value)
        check(name, new_value)
        if visible_words(value) != visible_words(new_value):
            raise SystemExit(f"{name}: visible text changed, refusing to write")
        new_fields[name] = new_value

    changed = [n for n in note.field_names if new_fields[n] != note.fields[n]]
    for name in note.field_names:
        lines = new_fields[name].split("<br>")
        mark = "CHANGED" if name in changed else "unchanged"
        print("=" * 72)
        print(
            f"{name}  [{mark}]  {len(note.fields[name])} -> {len(new_fields[name])} chars"
        )
        print("=" * 72)
        for i, line in enumerate(lines, 1):
            print(f"{i:3} | {line}")
        print()

    if not changed:
        print("already normalized; nothing to do")
        return 0

    if not args.apply:
        print("(dry run; pass --apply to write)")
        return 0

    update_note(note_id, new_fields)
    if get_note(note_id).fields != new_fields:
        raise SystemExit("readback mismatch")
    print(f"wrote {', '.join(changed)}; read back OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
