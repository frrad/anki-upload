"""Tests for field validation and the plain-text -> field-HTML promotion.

The guard in check() is the interesting part: it decides whether a field is
safe to write, so a false positive blocks a legitimate repair and a false
negative corrupts a card. Both directions are tested.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from fields import check, inline_image, to_html

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
# check(): <pre> for code, the one exception to the <br>-only rule
# ---------------------------------------------------------------------------


def test_check_allows_a_pre_block() -> None:
    check("Back", "prose<br><br><pre>x = torch.randn(32, 10, 512)\nnn.LayerNorm(512)</pre>")


def test_check_rejects_pre_inside_math() -> None:
    # <pre> is allowed markup, but "no tags inside a math span" still wins:
    # anything between the delimiters must be one unbroken run of text.
    with pytest.raises(SystemExit, match="HTML tag inside inline math"):
        check("Back", r"\( a <pre>b</pre> \)")


def test_check_rejects_unclosed_pre() -> None:
    # An unclosed block swallows the rest of the card, which is how the OLS
    # note ended up with a stray </ul> and a mangled tail.
    with pytest.raises(SystemExit, match="unbalanced <pre>"):
        check("Back", "<pre>code goes here")


def test_check_rejects_stray_closing_pre() -> None:
    with pytest.raises(SystemExit, match="unbalanced <pre>"):
        check("Back", "code goes here</pre>")


def test_check_rejects_nested_pre() -> None:
    with pytest.raises(SystemExit, match="unbalanced <pre>"):
        check("Back", "<pre>outer<pre>inner</pre></pre>")


# ---------------------------------------------------------------------------
# check(): <b> for emphasis, the other exception to the <br>-only rule
# ---------------------------------------------------------------------------


def test_check_allows_a_bold_span() -> None:
    check("Back", "dim is required, shape preserved, <b>inclusive</b>.")


def test_check_allows_bold_next_to_a_br() -> None:
    check("Back", "<b>Front</b><br>the back")


def test_check_rejects_bold_inside_math() -> None:
    # Same rule as <pre>: allowed markup still may not sit inside a math span,
    # which must be one unbroken run of text for MathJax.
    with pytest.raises(SystemExit, match="HTML tag inside inline math"):
        check("Back", r"\( a <b>b</b> \)")


def test_check_rejects_unclosed_bold() -> None:
    # An unclosed <b> bolds the rest of the card.
    with pytest.raises(SystemExit, match="unbalanced <b>"):
        check("Back", "the <b>important part")


def test_check_rejects_stray_closing_bold() -> None:
    with pytest.raises(SystemExit, match="unbalanced <b>"):
        check("Back", "the important part</b>")


def test_check_rejects_nested_bold() -> None:
    with pytest.raises(SystemExit, match="unbalanced <b>"):
        check("Back", "<b>outer<b>inner</b></b>")


def test_check_does_not_confuse_br_with_an_opening_bold() -> None:
    # Regression guard for the allowlist pattern: "<br>" must not be read as
    # a <b> tag, or every line break would look like an unbalanced bold.
    check("Back", "line one<br>line two")


def test_check_still_rejects_code_and_span() -> None:
    # The ordinary allowlist is deliberately narrow. Widening it is a deliberate act,
    # not something a paste from a docs site gets to do implicitly.
    with pytest.raises(SystemExit, match="unexpected markup"):
        check("Back", "<code>x</code>")
    with pytest.raises(SystemExit, match="unexpected markup"):
        check("Back", '<span class="pre">x</span>')


# ---------------------------------------------------------------------------
# check(): canonical inline images are allowed, arbitrary <img> markup is not
# ---------------------------------------------------------------------------


def test_inline_image_builds_a_valid_responsive_png_tag(tmp_path: Path) -> None:
    source = tmp_path / "diagram.png"
    payload = b"\x89PNG\r\n\x1a\nimage payload"
    source.write_bytes(payload)

    tag = inline_image(source)

    assert tag.startswith('<img src="data:image/png;base64,')
    assert f' alt="{source.name}"' in tag
    assert tag.endswith('style="max-width:100%;height:auto">')
    encoded = tag.split("base64,", 1)[1].split('"', 1)[0]
    assert base64.b64decode(encoded, validate=True) == payload
    check("Back", tag + "<br><br>answer")


def test_inline_image_escapes_alt_text(tmp_path: Path) -> None:
    source = tmp_path / "diagram.png"
    source.write_bytes(b"\x89PNG\r\n\x1a\nimage payload")

    tag = inline_image(source, 'tensor < split & "reduce"')

    assert 'alt="tensor &lt; split &amp; &quot;reduce&quot;"' in tag
    check("Back", tag)


def test_inline_image_rejects_an_unsupported_extension(tmp_path: Path) -> None:
    source = tmp_path / "diagram.svg"
    source.write_text("<svg></svg>")
    with pytest.raises(SystemExit, match="unsupported image type"):
        inline_image(source)


def test_inline_image_rejects_bytes_that_do_not_match_the_extension(
    tmp_path: Path,
) -> None:
    source = tmp_path / "diagram.png"
    source.write_bytes(b"not actually a PNG")
    with pytest.raises(SystemExit, match="does not match its .png extension"):
        inline_image(source)


def test_check_rejects_a_remote_image() -> None:
    with pytest.raises(SystemExit, match="invalid inline image tag"):
        check("Back", '<img src="https://example.com/diagram.png">')


def test_check_rejects_extra_image_attributes() -> None:
    encoded = base64.b64encode(b"\x89PNG\r\n\x1a\nimage payload").decode("ascii")
    tag = (
        f'<img src="data:image/png;base64,{encoded}" alt="Diagram" '
        'style="max-width:100%;height:auto" onerror="alert(1)">'
    )
    with pytest.raises(SystemExit, match="invalid inline image tag"):
        check("Back", tag)


def test_check_rejects_image_data_that_disagrees_with_its_mime() -> None:
    encoded = base64.b64encode(b"GIF89aimage payload").decode("ascii")
    tag = (
        f'<img src="data:image/png;base64,{encoded}" alt="Diagram" '
        'style="max-width:100%;height:auto">'
    )
    with pytest.raises(SystemExit, match="does not match its MIME type"):
        check("Back", tag)


# ---------------------------------------------------------------------------
# check(): "<" is only markup when a tag can actually start there
# ---------------------------------------------------------------------------


def test_check_allows_less_than_or_equal_in_a_pre_block() -> None:
    # A tag name must begin with a letter, so a browser renders "<=" as text.
    # Reading it as markup made the guard swallow everything up to the next
    # ">" -- here the closing </pre> -- and reject a card that renders fine.
    check("Back", "<pre>return grad_out * (x.abs() <= 1).float()</pre>")


def test_check_allows_a_less_than_followed_by_a_space() -> None:
    check("Back", "keep the learning rate < 1e-3 or it diverges")


def test_check_allows_less_than_or_equal_inside_math() -> None:
    check("Back", r"the mask is \( i <= j \), upper triangular")


def test_check_rejects_a_real_tag_that_follows_a_less_than_or_equal() -> None:
    # The old pattern matched "<= 1 <div>" as a single bogus tag, so this was
    # rejected for the wrong reason. It must still be rejected for the right
    # one: the <div> is real markup.
    with pytest.raises(SystemExit, match="unexpected markup"):
        check("Back", "x <= 1 <div>a</div>")


def test_check_rejects_a_tag_with_attributes_after_a_less_than_or_equal() -> None:
    with pytest.raises(SystemExit, match="unexpected markup"):
        check("Back", 'x <= 1 <span class="pre">a</span>')


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
# to_html(): newlines become <br>, except inside math
# ---------------------------------------------------------------------------


def test_to_html_promotes_newlines_to_br() -> None:
    assert to_html("first\nsecond") == "first<br>second"


def test_to_html_promotes_a_blank_line_to_two_brs() -> None:
    assert to_html("above\n\nbelow") == "above<br><br>below"


def test_to_html_leaves_a_field_without_newlines_alone() -> None:
    assert to_html("single line") == "single line"


def test_to_html_keeps_newlines_inside_display_math() -> None:
    # This is the examples/cards.txt shape: the author writes the equation
    # across three lines. Promoting those newlines would put a <br> inside the
    # delimiters and stop the formula rendering; to MathJax they are just
    # whitespace, so they must survive as newlines.
    text = "The trick says:\n\n\\[\n\\nabla_\\theta P = P \\nabla_\\theta \\log P\n\\]"
    expected = (
        "The trick says:<br><br>\\[\n\\nabla_\\theta P = P \\nabla_\\theta \\log P\n\\]"
    )
    assert to_html(text) == expected


def test_to_html_keeps_newlines_inside_inline_math() -> None:
    assert to_html("see \\( a\nb \\) here") == "see \\( a\nb \\) here"


def test_to_html_promotes_around_but_not_within_a_span() -> None:
    text = "before\n\\[ x = 1 \\]\nafter"
    assert to_html(text) == "before<br>\\[ x = 1 \\]<br>after"


def test_to_html_handles_several_spans_in_one_field() -> None:
    text = "one\n\\( a \\)\ntwo\n\\[ b \\]\nthree"
    assert to_html(text) == "one<br>\\( a \\)<br>two<br>\\[ b \\]<br>three"


def test_to_html_keeps_newlines_inside_pre() -> None:
    # <pre> preserves whitespace itself, so its newlines are already real line
    # breaks. Promoting them would render every code line double-spaced.
    text = "Example:\n\n<pre>a = 1\nb = 2</pre>"
    assert to_html(text) == "Example:<br><br><pre>a = 1\nb = 2</pre>"


def test_to_html_preserves_leading_spaces_inside_pre() -> None:
    # The whole reason for <pre>: column alignment without &nbsp;, which
    # check() bans outright.
    text = "<pre>nn.LayerNorm(512)  ok\nnn.LayerNorm(10)   error</pre>"
    assert to_html(text) == text


def test_to_html_handles_pre_and_math_in_one_field() -> None:
    text = "see\n\\( a \\)\nthen\n<pre>x = 1\ny = 2</pre>\ndone"
    expected = "see<br>\\( a \\)<br>then<br><pre>x = 1\ny = 2</pre><br>done"
    assert to_html(text) == expected


def test_to_html_pre_output_passes_check() -> None:
    text = "Signature:\n\n<pre>nn.LayerNorm(normalized_shape)\nnn.LayerNorm(512)</pre>"
    check("Back", to_html(text))


def test_to_html_promotes_newlines_around_bold() -> None:
    # <b> is inline, not protected: a newline beside it is an ordinary line
    # break and must still be promoted.
    assert to_html("<b>heading</b>\nbody") == "<b>heading</b><br>body"


def test_to_html_bold_output_passes_check() -> None:
    check("Back", to_html("dim is <b>required</b>\nand shape is preserved"))


def test_to_html_output_passes_check_for_the_cards_file_example() -> None:
    # The two halves have to agree: anything to_html produces from the
    # documented cards-file format must survive the write guard.
    text = "Given\n\n\\[\nP(\\tau|\\theta) = \\rho_0(s_0)\n\\]\n\nwhat follows?"
    check("Front", to_html(text))
