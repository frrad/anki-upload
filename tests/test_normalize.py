"""Tests for the fix-latex skill's normalizer.

The guard in check() is the interesting part: it decides whether a field is
safe to write, so a false positive blocks a legitimate repair and a false
negative corrupts a card. Both directions are tested.
"""

from __future__ import annotations

import pytest

from normalize import check, normalize, to_lines, visible_words

# ---------------------------------------------------------------------------
# check(): must reject anything that stops MathJax rendering
# ---------------------------------------------------------------------------


def test_check_rejects_br_inside_display_math() -> None:
    # This is the shape note 1758039655479 arrived in: the author wrote the
    # equation across several lines and the web editor turned each break into
    # a <br>, leaving markup in the middle of the expression.
    field = (
        r"The Kullback-Leibler divergence:<br><br>\[<br>"
        r"D_{\mathrm{KL}}(p\|q) = H(p,q) - H(p)<br>\]"
    )
    with pytest.raises(SystemExit, match="HTML tag inside display math"):
        check("Back", field)


def test_check_rejects_br_inside_inline_math() -> None:
    with pytest.raises(SystemExit, match="HTML tag inside inline math"):
        check("Back", r"the value \( a <br> b \) here")


def test_check_rejects_named_entity_inside_math() -> None:
    with pytest.raises(SystemExit, match="entity inside inline math"):
        check("Back", r"the value \( a &nbsp; b \) here")


def test_check_rejects_amp_entity_inside_display_math() -> None:
    with pytest.raises(SystemExit, match="entity inside display math"):
        check("Back", r"\[ a &amp; b \]")


def test_check_rejects_decimal_numeric_entity_inside_math() -> None:
    with pytest.raises(SystemExit, match="entity inside inline math"):
        check("Back", r"\( a &#160; b \)")


def test_check_rejects_hex_numeric_entity_inside_math() -> None:
    with pytest.raises(SystemExit, match="entity inside inline math"):
        check("Back", r"\( a &#xA0; b \)")


def test_check_rejects_literal_nbsp_character_anywhere() -> None:
    with pytest.raises(SystemExit, match="non-breaking space survived"):
        check("Front", "some\xa0text")


def test_check_rejects_nbsp_entity_outside_math() -> None:
    with pytest.raises(SystemExit, match="non-breaking space survived"):
        check("Front", "some&nbsp;text")


def test_check_rejects_div_markup() -> None:
    with pytest.raises(SystemExit, match="unexpected markup"):
        check("Back", "<div>a</div>")


def test_check_allows_br_as_the_only_markup() -> None:
    check("Back", "line one<br>line two<br><br>line four")


# ---------------------------------------------------------------------------
# check(): must NOT reject legitimate LaTeX
# ---------------------------------------------------------------------------


def test_check_allows_ampersand_alignment_marker_in_aligned_environment() -> None:
    # Regression. The guard originally tested `"&" in span`, which treats the
    # alignment marker of an `aligned` environment as an HTML entity. That
    # rejected every correctly written multi-line equation -- including the
    # repair for note 1758039655479, which is what surfaced the bug.
    field = (
        r"\[ \begin{aligned} D_{\mathrm{KL}}(p\|q) &= H(p,q) - H(p) \\ "
        r"&= \mathbb{E}_p\!\left[\log \frac{p(x)}{q(x)}\right]. \end{aligned} \]"
    )
    check("Back", field)


def test_check_allows_escaped_ampersand_inside_math() -> None:
    check("Back", r"\( A \& B \)")


def test_check_allows_backslash_pipe_and_braces_inside_math() -> None:
    check("Back", r"\( D_{\mathrm{KL}}(p\|q) \)")


def test_check_allows_a_field_with_no_math_at_all() -> None:
    check("Front", "just some plain prose<br>on two lines")


# ---------------------------------------------------------------------------
# normalize(): HTML in, <br>-only out
# ---------------------------------------------------------------------------


def test_normalize_turns_div_per_line_into_br() -> None:
    field = "<div>first</div><div>second</div><div>third</div>"
    assert normalize(field) == "first<br>second<br>third"


def test_normalize_turns_empty_div_into_a_blank_line() -> None:
    field = "<div>above</div><div><br></div><div>below</div>"
    assert normalize(field) == "above<br><br>below"


def test_normalize_replaces_nbsp_entity_with_an_ordinary_space() -> None:
    field = "<div>Batch Normalization&nbsp;(BatchNorm)</div>"
    assert normalize(field) == "Batch Normalization (BatchNorm)"


def test_normalize_strips_trailing_nbsp_padding() -> None:
    field = "<div>a line&nbsp;&nbsp;</div><div>another&nbsp;&nbsp;</div>"
    assert normalize(field) == "a line<br>another"


def test_normalize_handles_the_web_editors_wrapping_div() -> None:
    # After a manual edit the editor leaves the first line bare and wraps
    # everything after the caret in one div.
    field = "first line&nbsp;<div>second line\nthird line</div>"
    assert normalize(field) == "first line<br>second line<br>third line"


def test_normalize_unescapes_html_entities_in_prose() -> None:
    field = "<div>a &lt; b &amp;&amp; c &gt; d</div>"
    assert normalize(field) == "a < b && c > d"


def test_normalize_preserves_latex_backslashes_untouched() -> None:
    field = r"<div>- Formula: \( \frac{x}{\sqrt{\sigma^2 + \epsilon}} \)</div>"
    assert normalize(field) == r"- Formula: \( \frac{x}{\sqrt{\sigma^2 + \epsilon}} \)"


def test_normalize_is_idempotent() -> None:
    field = "<div>one</div><div><br></div><div>two</div>"
    once = normalize(field)
    assert normalize(once) == once


def test_normalize_drops_leading_and_trailing_blank_lines() -> None:
    field = "<div><br></div><div>content</div><div><br></div>"
    assert normalize(field) == "content"


def test_normalize_keeps_interior_blank_line_runs() -> None:
    field = "<div>a</div><div><br></div><div><br></div><div>b</div>"
    assert normalize(field) == "a<br><br><br>b"


# ---------------------------------------------------------------------------
# to_lines() and visible_words()
# ---------------------------------------------------------------------------


def test_to_lines_splits_on_both_div_and_br() -> None:
    assert to_lines("<div>a<br>b</div><div>c</div>") == ["a", "b", "c"]


def test_visible_words_ignores_markup_and_whitespace_differences() -> None:
    html_version = "<div>Batch&nbsp;Normalization</div><div>- uses stats</div>"
    br_version = "Batch Normalization<br>- uses stats"
    assert visible_words(html_version) == visible_words(br_version)


def test_visible_words_still_notices_a_real_text_change() -> None:
    assert visible_words("<div>alpha</div>") != visible_words("<div>beta</div>")
