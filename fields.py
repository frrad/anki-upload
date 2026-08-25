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

`<b>` is the other, for the load-bearing word in an answer. It is inline and
carries no whitespace meaning, so unlike `<pre>` it protects nothing: a
newline beside it is an ordinary line break and still becomes `<br>`.

Inline images use one canonical Base64 `<img>` shape produced by
`inline_image()`. The narrow shape keeps images self-contained and responsive
without admitting arbitrary pasted attributes or remote URLs.

`to_html` performs that promotion and `check` refuses anything that would
violate it, so the rules are enforced on the way in rather than repaired
afterwards.
"""

from __future__ import annotations

import base64
import binascii
import html
import re
from pathlib import Path

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
# "b" must be followed by the closing ">", or "<br>" would read as an opening
# bold and every line break would look unbalanced.
B_TAG_RE = re.compile(r"</?b\s*>", re.I)
# The ordinary markup allowlist. Inline images are checked separately because
# accepting an arbitrary <img> would also accept remote URLs, event handlers,
# and pasted inline styles.
ALLOWED_TAG_RE = re.compile(r"<br\s*/?>|</?pre\s*>|</?b\s*>", re.I)
# Canonical tag emitted by inline_image(). Attribute order and the responsive
# style are deliberate: a narrow pattern is much safer than attempting to
# sanitize arbitrary pasted <img> markup.
IMAGE_TAG_RE = re.compile(
    r'<img src="data:image/(?P<mime>png|jpeg|gif|webp);base64,'
    r'(?P<data>[A-Za-z0-9+/]+={0,2})" alt="(?P<alt>[^"<>]*)" '
    r'style="max-width:100%;height:auto"\s*/?>',
    re.I,
)
IMAGE_MIME_BY_SUFFIX = {
    ".png": "png",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".gif": "gif",
    ".webp": "webp",
}
# Spans whose newlines are load-bearing and must not become <br>: math, where
# they are whitespace to MathJax, and <pre>, where they are the line breaks.
PROTECTED_RE = re.compile(r"\\\[.*?\\\]|\\\(.*?\\\)|<pre\s*>.*?</pre\s*>", re.S | re.I)


def _has_image_signature(mime: str, data: bytes) -> bool:
    """Return whether data has the expected signature for a supported MIME."""
    mime = mime.lower()
    if mime == "png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if mime == "jpeg":
        return data.startswith(b"\xff\xd8\xff")
    if mime == "gif":
        return data.startswith((b"GIF87a", b"GIF89a"))
    if mime == "webp":
        return len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP"
    return False


def _check_image_tag(name: str, tag: str) -> bool:
    """Validate a canonical inline image, returning False for a non-image tag."""
    if not re.match(r"<img(?:\s|>)", tag, re.I):
        return False

    match = IMAGE_TAG_RE.fullmatch(tag)
    if not match:
        raise SystemExit(f"{name}: invalid inline image tag")

    try:
        data = base64.b64decode(match.group("data"), validate=True)
    except (binascii.Error, ValueError):
        raise SystemExit(f"{name}: invalid Base64 image data") from None
    if not _has_image_signature(match.group("mime"), data):
        raise SystemExit(f"{name}: image data does not match its MIME type")
    return True


def inline_image(path: Path, alt: str | None = None) -> str:
    """Encode a supported local image as the canonical inline <img> tag."""
    mime = IMAGE_MIME_BY_SUFFIX.get(path.suffix.lower())
    if mime is None:
        supported = ", ".join(sorted(IMAGE_MIME_BY_SUFFIX))
        raise SystemExit(f"unsupported image type {path.suffix!r}; expected {supported}")
    try:
        data = path.read_bytes()
    except OSError as e:
        raise SystemExit(f"could not read image {path}: {e}") from e
    if not _has_image_signature(mime, data):
        raise SystemExit(f"image {path} does not match its {path.suffix} extension")

    encoded = base64.b64encode(data).decode("ascii")
    description = html.escape(alt if alt is not None else path.name, quote=True)
    return (
        f'<img src="data:image/{mime};base64,{encoded}" alt="{description}" '
        'style="max-width:100%;height:auto">'
    )


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


def _check_balance(name: str, value: str, tag: str, pattern: re.Pattern[str]) -> None:
    """Refuse an unbalanced or nested container tag.

    An unclosed one swallows the rest of the card -- everything after it is
    monospaced or bold -- and a nested one has no meaning; both are the
    signature of markup that arrived by paste rather than intent.
    """
    depth = 0
    for found in pattern.findall(value):
        depth += -1 if found.startswith("</") else 1
        if depth not in (0, 1):
            raise SystemExit(f"{name}: unbalanced <{tag}>")
    if depth != 0:
        raise SystemExit(f"{name}: unbalanced <{tag}>")


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
    stray = {
        tag
        for tag in TAG_RE.findall(value)
        if not ALLOWED_TAG_RE.fullmatch(tag) and not _check_image_tag(name, tag)
    }
    if stray:
        raise SystemExit(f"{name}: unexpected markup {sorted(stray)}")
    _check_balance(name, value, "pre", PRE_TAG_RE)
    _check_balance(name, value, "b", B_TAG_RE)
