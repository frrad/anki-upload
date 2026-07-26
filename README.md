# anki-upload

Add cards to AnkiWeb from the command line.

There is no public AnkiWeb API, so this posts a hand-rolled protobuf message to
the same private endpoint the web editor uses
(`ankiuser.net/svc/editor/add-or-update`), authenticating with a session cookie
lifted from a logged-in browser. Nothing about that interface is documented or
stable, so expect it to break whenever AnkiWeb changes.

## Setup

```sh
python -m venv venv
venv/bin/pip install -r requirements.txt
```

Create a `.env` (gitignored) with three values:

```sh
ANKIWEB_AUTH=...        # value of the `ankiweb` cookie
ANKIWEB_DECK_ID=...     # target deck
ANKIWEB_NOTETYPE_ID=... # note type, e.g. Basic
```

All three come from a logged-in session on <https://ankiweb.net>: open devtools,
add a card by hand, and read the `ankiweb` cookie plus the two ids off the
`add-or-update` request. The cookie is a live credential — it grants access to
the account, so keep it out of commits.

## Usage

```sh
venv/bin/python main.py examples/cards.txt
venv/bin/python main.py examples/cards.txt --dry-run   # parse and print, upload nothing
cat card.txt | venv/bin/python main.py -               # read stdin
```

`--deck-id` and `--notetype-id` override the values from `.env`.

## Card file format

Cards are separated by a line of `===`, and the front and back of a card by a
line of `---`. Everything between delimiters is used **verbatim**, which is the
point of the format: LaTeX, backslashes, quotes and blank lines all pass through
without escaping.

```
tags: rl policy-gradients
What does the log-derivative trick say about
\( \nabla_\theta P(\tau|\theta) \)?
---
\[
\nabla_\theta P(\tau|\theta) = P(\tau|\theta) \, \nabla_\theta \log P(\tau|\theta)
\]
===
Front of the second card
---
Back of the second card
```

A card may start with a `tags:` line, as above; it is optional. To start a front
with a literal `tags:`, put a blank line before it.

See `examples/cards.txt` for a working file.
