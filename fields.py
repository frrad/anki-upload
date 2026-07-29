"""Turning plain text into an Anki field, and refusing to write a broken one.

Two constraints pull in opposite directions. Anki renders a field as HTML, so
a literal newline collapses and every line runs together. MathJax needs each
`\\( ... \\)` span to be one unbroken run of text, so a tag landing *inside*
the delimiters stops it rendering. `<br>` as the only markup, placed only
between math spans, satisfies both.

`to_html` performs that promotion and `check` refuses anything that would
violate it, so the rules are enforced on the way in rather than repaired
afterwards.
"""

from __future__ import annotations

import re

BR_RE = re.compile(r"<br\s*/?>", re.I)
TAG_RE = re.compile(r"<[^>]+>")
INLINE_MATH_RE = re.compile(r"\\\((.*?)\\\)", re.S)
DISPLAY_MATH_RE = re.compile(r"\\\[(.*?)\\\]", re.S)
# A real HTML entity, not a bare "&" -- inside an `aligned` environment "&" is
# the LaTeX alignment marker and is perfectly legitimate.
ENTITY_RE = re.compile(r"&(?:[a-zA-Z][a-zA-Z0-9]*|#\d+|#[xX][0-9a-fA-F]+);")
# Either delimiter pair, so a span can be found without knowing which it is.
ANY_MATH_RE = re.compile(r"\\\[.*?\\\]|\\\((.*?)\\\)", re.S)


def to_html(text: str) -> str:
    """Plain text -> field HTML: newlines become <br>, except inside math.

    A newline inside `\\[ ... \\]` is whitespace to MathJax and must stay a
    newline; promoting it to `<br>` would put a tag inside the span and stop
    the formula rendering. Newlines outside any span become `<br>`, which is
    what actually produces a visible line break.
    """
    out: list[str] = []
    end = 0
    for span in ANY_MATH_RE.finditer(text):
        out.append(text[end : span.start()].replace("\n", "<br>"))
        out.append(span.group(0))
        end = span.end()
    out.append(text[end:].replace("\n", "<br>"))
    return "".join(out)


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
