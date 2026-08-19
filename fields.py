"""Turning plain text into an Anki field, and refusing to write a broken one.

Two constraints pull in opposite directions. Anki renders a field as HTML, so
a literal newline collapses and every line runs together. MathJax needs each
`\\( ... \\)` span to be one unbroken run of text, so a tag landing *inside*
the delimiters stops it rendering. `<br>` as the only markup, placed only
between math spans, satisfies both.

`<pre>` is the one addition, for code. It buys monospace and preserved
whitespace, which is the only way to align columns now that `&nbsp;` is
banned outright -- and its newlines are already real line breaks, so they
must survive the promotion exactly as a math span's do.

`to_html` performs that promotion and `check` refuses anything that would
violate it, so the rules are enforced on the way in rather than repaired
afterwards.
"""

from __future__ import annotations

import re

BR_RE = re.compile(r"<br\s*/?>", re.I)
# A tag can only start where the HTML parser would start one: "<" followed by
# a letter, "/", "!" or "?". Anything else -- "<=" in code, "< 1e-3" in prose --
# is text to a browser, and reading it as markup made this run to the next ">"
# and reject the whole span it swallowed.
TAG_RE = re.compile(r"<[a-zA-Z/!?][^>]*>")
INLINE_MATH_RE = re.compile(r"\\\((.*?)\\\)", re.S)
DISPLAY_MATH_RE = re.compile(r"\\\[(.*?)\\\]", re.S)
# A real HTML entity, not a bare "&" -- inside an `aligned` environment "&" is
# the LaTeX alignment marker and is perfectly legitimate.
ENTITY_RE = re.compile(r"&(?:[a-zA-Z][a-zA-Z0-9]*|#\d+|#[xX][0-9a-fA-F]+);")
# Either delimiter pair, so a span can be found without knowing which it is.
ANY_MATH_RE = re.compile(r"\\\[.*?\\\]|\\\((.*?)\\\)", re.S)
PRE_TAG_RE = re.compile(r"</?pre\s*>", re.I)
# The full markup allowlist. Everything else is rejected, so a paste from a
# docs site cannot widen it by accident.
ALLOWED_TAG_RE = re.compile(r"<br\s*/?>|</?pre\s*>", re.I)
# Spans whose newlines are load-bearing and must not become <br>: math, where
# they are whitespace to MathJax, and <pre>, where they are the line breaks.
PROTECTED_RE = re.compile(r"\\\[.*?\\\]|\\\(.*?\\\)|<pre\s*>.*?</pre\s*>", re.S | re.I)


def to_html(text: str) -> str:
    """Plain text -> field HTML: newlines become <br>, except where protected.

    A newline inside `\\[ ... \\]` is whitespace to MathJax and must stay a
    newline; promoting it to `<br>` would put a tag inside the span and stop
    the formula rendering. A newline inside `<pre>` is already a rendered line
    break, so promoting it would double-space the code. Newlines outside any
    protected span become `<br>`, which is what actually produces a visible
    line break.
    """
    out: list[str] = []
    end = 0
    for span in PROTECTED_RE.finditer(text):
        out.append(text[end : span.start()].replace("\n", "<br>"))
        out.append(span.group(0))
        end = span.end()
    out.append(text[end:].replace("\n", "<br>"))
    return "".join(out)


def _check_pre_balance(name: str, value: str) -> None:
    """Refuse an unbalanced or nested <pre>.

    An unclosed block swallows the rest of the card, and a nested one has no
    meaning; both are the signature of markup that arrived by paste rather
    than intent.
    """
    depth = 0
    for tag in PRE_TAG_RE.findall(value):
        depth += -1 if tag.startswith("</") else 1
        if depth not in (0, 1):
            raise SystemExit(f"{name}: unbalanced <pre>")
    if depth != 0:
        raise SystemExit(f"{name}: unbalanced <pre>")


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
    stray = {t for t in TAG_RE.findall(value) if not ALLOWED_TAG_RE.fullmatch(t)}
    if stray:
        raise SystemExit(f"{name}: unexpected markup {sorted(stray)}")
    _check_pre_balance(name, value)
