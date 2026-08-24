---
name: anki-notes
description: "Write AnkiWeb notes and edit existing ones: add a new card to the deck, bulk-import a batch, convert math to LaTeX that actually renders, and repair markup the web editor has mangled. Use when asked to make, add, or create a flashcard or a set of them, and when a card's formulas show as Unicode pseudo-math (μ, σ², √, x_i), when LaTeX is present but renders as literal source text, or when a card's lines run together on one line after editing. Covers the two competing constraints -- MathJax needs clean text context, Anki needs HTML for line breaks -- and the <br>-plus-<pre>-plus-<b> format that satisfies both."
---

# Writing and editing AnkiWeb notes

Everything here runs from the `anki-upload` checkout, so `cd
~/Projects/anki-upload` first; `venv/bin/python main.py` is relative to it.
Credentials and the default deck come from its `.env`, so no ids need
passing on the command line.

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

Use `<br>` as the **only** markup outside code, with math spans left as clean
text (`<pre>` and `<b>` are the two exceptions, covered below):

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
  A newline *is* allowed there and is the one exception to the rule below:
  MathJax treats it as whitespace, so an equation may span several lines.
- **`<pre>` for code, and nothing else.** It is one of the two exceptions to
  `<br>`-only: monospace plus preserved whitespace, which is how a code block
  keeps its column alignment without `&nbsp;`. Newlines inside it stay
  newlines — a `<br>` there would double-space. It must be closed and never
  nested.
- **`<b>` for the one word that carries the answer.** The other exception:
  the discriminating detail — `<b>inclusive</b>`, `<b>flattens</b>` — so the
  eye finds it on a phone screen. Being inline it protects nothing, and it
  must be closed and never nested, same as `<pre>`.
- **A newline outside a span is not a line break.** Anki collapses it. On
  the import path `to_html` promotes those newlines to `<br>` for you,
  leaving the ones inside math spans alone; on the `update` path what you
  put in the file is what gets written, so write `<br>` yourself.

Bold sparingly: one or two spans in a field. Bolding a whole line is the
same as bolding nothing, and it is what a paste from a docs site produces.

Use `<pre>` only for genuine code. A signature pasted from a docs site
arrives wrapped in `<dl>/<dt>/<span>` with inline styles and hardcoded
colors, which is what note 1758416878706 looked like; the repair is to keep
the text and re-wrap it in one `<pre>`, not to preserve any of that.

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
- The expectation operator is `\mathbb{E}`, not a bare `E` (which renders as
  an italic variable). Confirmed to render -- see below.

## Card writing style

Fixing a card's markup is not licence to rewrite it. Keep these separate:

- **Do not change field content while fixing formatting.** Converting
  pseudo-math to LaTeX preserves meaning and is in scope. Rewording a
  prompt, adding facts, or restructuring an answer is a content change --
  propose it and get agreement first, as a separate step.
- **Prefer a short front.** A few words are enough when a few words
  suffice; do not pad a prompt into a full sentence or a wall of text for
  its own sake. A bare topic name like `Jensen's inequality` is a
  legitimate front, not a defect to be fixed.

When writing a *new* card, the same brevity applies, plus:

- **Agree the card in chat before adding it.** Draft front and back as
  text, iterate, and only then upload. The deck is not a scratchpad.
- **The front must not give away the back.** No hints, no restating the
  answer, no phrasing that reads as multiple choice. If two answers are
  equally right, pluralize the front and ask for both rather than picking
  one arbitrarily.
- **A card must name its own context.** A bare signature with no mention of
  the class or module it belongs to is unanswerable in a shuffled deck, and
  a sibling card establishing that context does not help.
- **Cards that cannot be failed are as useless as cards that cannot be
  passed.** Purely definitional material and "the full list of X" reference
  sheets are both reference, not recall; they belong in notes, not the deck.
- **It is reviewed on a phone.** Prose with `<br>` reflows; a `<pre>` block
  wider than a phone screen does not. Never hard-wrap a field at ~72
  columns -- those breaks land mid-sentence on a narrow screen.

## Procedure: adding a note

One card goes in through `add`, a batch through `import`. Both validate
exactly as `update` does, so a rejection means the file is wrong.

1. **Write each field to its own file.** Same reason as editing: a shell
   argument mangles backslashes, apostrophes and newlines, and a card is
   mostly those. `--field NAME=VALUE` exists for trivial values only.

   For `add`, write the field exactly as it should be stored -- **`<br>`
   between lines, written by hand**. `add` does no newline promotion, so a
   bare newline in the file collapses on the card.

   ```sh
   venv/bin/python main.py add --field-file Front=front.txt --field-file Back=back.txt --tags "python stdlib"
   ```

2. **For several cards, use a cards file instead.** Cards are separated by a
   line of `===`, front from back by a line of `---`, and an optional
   leading `tags:` line sets that card's tags.

   On this path newlines *are* promoted: `import` turns each one into `<br>`
   so the card looks like the file, leaving alone those inside `\( ... \)`,
   `\[ ... \]` and `<pre>` spans, where a `<br>` would break the formula or
   double-space the code. So write plain newlines here and `<br>` under
   `add` -- the difference is real and easy to get backwards.

   ```sh
   venv/bin/python main.py import cards.txt --dry-run   # parse and print only
   venv/bin/python main.py import cards.txt
   ```

   `--dry-run` is worth running first on anything non-trivial: it shows the
   parse, so a misplaced `---` surfaces before the upload rather than as a
   card split down the middle.

3. **Check the note landed by reading it back**, using the id the add
   printed. There is nothing to diff against, so read it and check it says
   what you drafted.

   ```sh
   venv/bin/python main.py get <id> --json
   ```

4. **Ask the user to confirm it renders**, as with an edit.

Note that **there is no delete.** The AnkiWeb API is note-addressed and
carries no card state, so a card added by mistake can only be tombstoned --
set every field to `deleteme` and remove it in the client. Getting the card
right before it goes in is cheaper than getting it out.

## Procedure: editing an existing note

Edits go through a file, never through a shell argument. Field values
contain apostrophes, backslashes and newlines, all of which the shell will
mangle or the quoting will terminate early.

1. **Pull the field into a file, twice.** `--field` prints the raw value and
   nothing else, so it redirects straight to disk. Keep one copy untouched
   to diff against.

   ```sh
   venv/bin/python main.py get <url-or-id> --field Back > back.txt
   cp back.txt back.orig.txt
   ```

   Use `--json` instead when you need the whole note -- every field plus the
   tags -- in a form that can be read back without ambiguity. The
   human-readable default cannot: a field whose content contains a
   `--- Name ---` line is indistinguishable from a field boundary.

2. **Edit `back.txt` by hand.** Convert the math with exact substring
   replacements asserted to fire exactly once, so surrounding prose cannot
   drift. Content is not yours to reword -- see the section above.

3. **Write it back.**

   ```sh
   venv/bin/python main.py update <url-or-id> --set-file Back=back.txt
   ```

   `update` validates before it writes and refuses anything that would not
   render -- a tag or entity inside a math span, a stray `&nbsp;`, markup
   other than `<br>`, `<pre>` and `<b>`, or an unbalanced one of the latter
   two. A rejection is a real defect in the file, not an obstacle to route
   around.

4. **Verify** by reading the field back and diffing it against the copy you
   kept. The round trip is exact, so this should be silent:

   ```sh
   venv/bin/python main.py get <url-or-id> --field Back | diff back.orig.txt -
   ```

   `update` also prints only the fields whose value actually changed, so an
   unexpected field in that list means you have written something you did
   not intend.

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
edited in the browser, its artifacts have to come out by hand: there is no
script for it. There used to be, and it was deleted for cause -- it stripped
every tag to compare before against after, so it silently discarded anything
a tag carried (images above all) while its own safety check, which compared
the same stripped text, saw nothing wrong. Removing markup you cannot see is
not a job to automate against a live card.

## What is established, and what is not

Confirmed by observation on note 1756269681533:

- A literal `\n` does **not** produce a line break; text runs together.
- `\( ... \)` **does** render when the field is plain text.
- A `<div>` boundary **does** produce a visible line break.

Confirmed by observation on note 1765641609697:

- `\mathbb{E}` **does** render, so the AMS fonts extension is present in the
  MathJax config AnkiWeb ships. No `\operatorname{E}` fallback is needed.

Not established -- do not assert these as fact:

- **That a tag inside `\( ... \)` stops MathJax rendering.** This is the
  claim the whole document rests on and the one `check()` refuses writes
  over, so it is worth being honest about: it is a strong inference, not an
  observation. Note 1756269959560 had `<br>` inside both display spans, was
  tagged `leech` from repeated failure, and rendered correctly once the tags
  came out -- but that edit changed a Unicode `σ` to `\sigma` at the same
  time, so it does not isolate the cause. Treat the rule as a cheap
  precaution that has never yet cost anything, not as a measured fact.
- Whether per-line `<div>` wrappers or a trailing `&nbsp;` actually break
  MathJax, or whether the original card simply never had LaTeX in it to
  begin with. The `<br>`-only format avoids the question rather than
  answering it.
- Whether `<br>` renders correctly in every notetype's template. If line
  breaks still collapse, the fix is `white-space: pre-wrap` in the card
  styling, not returning to `<div>`.
