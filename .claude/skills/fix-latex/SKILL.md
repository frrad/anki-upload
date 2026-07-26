---
name: fix-latex
description: "Convert an AnkiWeb note's math to LaTeX that actually renders, and normalize its field markup. Use when a card's formulas show as Unicode pseudo-math (μ, σ², √, x_i), when LaTeX is present but renders as literal source text, or when a card's lines run together on one line after editing. Covers the two competing constraints -- MathJax needs clean text context, Anki needs HTML for line breaks -- and the <br>-only format that satisfies both."
---

# Fixing LaTeX rendering in AnkiWeb notes

## The core problem

Two constraints pull in opposite directions:

1. **Anki renders a field as HTML.** A literal `\n` in a field is just
   whitespace and collapses. Without markup, every line runs together.
2. **MathJax needs each math span to be one unbroken run of text.** A tag or
   an HTML entity landing *inside* `\( ... \)` stops it rendering.

Neither an all-HTML field nor an all-plain-text field satisfies both. This
is the trap: stripping markup to make the math render destroys the line
breaks, and adding markup back to fix the line breaks risks the math.

## The target format

Use `<br>` as the **only** markup, with math spans left as clean text:

```
Batch Normalization (BatchNorm)<br>- Uses batch statistics.<br>- Formula: \( \frac{x - \mu}{\sqrt{\sigma^2 + \epsilon}} \)<br><br>Layer Normalization
```

`<br>` is a real line break, and it sits *between* math spans rather than
inside them, so both constraints hold.

Rules:

- **`<br>` between lines.** Consecutive `<br>` give blank lines.
- **No `&nbsp;`.** Use a normal space. `&nbsp;` is U+00A0, and near or inside
  a math span it is a prime suspect for breaking MathJax.
- **No `<div>` wrappers.** They render fine, but the web editor nests them
  further on every edit, and they are what a stray `&nbsp;` rides in on.
- **Inline math is `\( ... \)`; display math is `\[ ... \]`.** This matches
  `examples/cards.txt` in this repo.
- **Nothing but plain text inside the delimiters.** No tags, no entities.

## Converting pseudo-math to LaTeX

Cards written by hand or pasted from an LLM often contain Unicode
pseudo-math, which never renders because there is no LaTeX to render:

| pseudo-math | LaTeX |
|---|---|
| `(x - μ_batch) / √(σ²_batch + ε)` | `\( \frac{x - \mu_{\text{batch}}}{\sqrt{\sigma_{\text{batch}}^2 + \epsilon}} \)` |
| `x / √(mean(x²) + ε)` | `\( \frac{x}{\sqrt{\mathrm{mean}(x^2) + \epsilon}} \)` |

This step is a judgement call and must not be automated blindly. Points to
get right:

- A slash division becomes `\frac`, so precedence is explicit.
- `√(...)` becomes `\sqrt{...}`, so the radical visibly covers the whole
  expression. This is usually the genuinely ambiguous part of the original.
- Word-like subscripts are `\text{batch}`, not `batch`. Function names are
  `\mathrm{mean}` or `\operatorname{mean}`.
- The Unicode `ε` is `\varepsilon`; `\epsilon` is the lunate `ϵ`. Either is
  defensible in ML notation -- pick one and say which.

## Procedure

1. **Read the note.** `python main.py get <url-or-id>`, and save the output
   before touching anything.
2. **Convert the math by hand,** using exact substring replacements asserted
   to fire exactly once, so surrounding prose cannot drift.
3. **Normalize the markup** with `normalize.py` in this skill directory
   (dry run first):

   ```sh
   python .claude/skills/fix-latex/normalize.py <url-or-id>
   python .claude/skills/fix-latex/normalize.py <url-or-id> --apply
   ```

4. **Verify** by reading the note back and diffing against the saved copy.
   Assert the visible text is unchanged once whitespace is normalized.
5. **Ask the user to confirm it renders.** Rendering happens in the Anki
   client and cannot be observed from here. A clean readback proves the
   bytes are right, not that MathJax is happy.

## Editing via the API, not the web editor

The AnkiWeb editor is a contenteditable. Placing a cursor in a plain-text
field and typing makes the browser re-wrap content in block elements: it
will insert `<div>` around everything after the caret and drop `&nbsp;` at
line ends. This recurs on every manual edit.

So make these edits through `main.py update`, which writes the field value
directly with no contenteditable in the path. If a card has already been
edited by hand, re-run `normalize.py` to strip the artifacts.

## What is established, and what is not

Confirmed by observation on note 1756269681533:

- A literal `\n` does **not** produce a line break; text runs together.
- `\( ... \)` **does** render when the field is plain text.
- A `<div>` boundary **does** produce a visible line break.

Not established -- do not assert these as fact:

- Whether per-line `<div>` wrappers or a trailing `&nbsp;` actually break
  MathJax, or whether the original card simply never had LaTeX in it to
  begin with. The `<br>`-only format avoids the question rather than
  answering it.
- Whether `<br>` renders correctly in every notetype's template. If line
  breaks still collapse, the fix is `white-space: pre-wrap` in the card
  styling, not returning to `<div>`.
