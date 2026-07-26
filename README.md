# anki-upload

Read, edit and bulk-add AnkiWeb cards from the command line.

There is no public AnkiWeb API, so this speaks the private protobuf endpoints
the web client uses. The wire format is not guessed: AnkiWeb's frontend is a
SvelteKit app whose bundle (`_app/immutable/chunks/frontend.*.mjs`) registers
every protobuf message descriptor in plaintext, and the schemas in `anki.py`
are transcribed from there. Nothing about this interface is documented or
stable, so expect it to break whenever AnkiWeb changes.

## Setup

```sh
python -m venv venv
venv/bin/pip install -r requirements.txt
```

Create a `.env` (gitignored) with your credentials and the default target:

```sh
ANKIWEB_USERNAME=you@example.com
ANKIWEB_PASSWORD=...
ANKIWEB_DECK_ID=...     # target deck
ANKIWEB_NOTETYPE_ID=... # note type, e.g. Basic
```

Then log in once:

```sh
venv/bin/python main.py login
venv/bin/python main.py decks   # lists deck and notetype ids
```

`login` caches both session cookies to `.anki-session.json` (gitignored,
`0600`); your password is never written there. Sessions renew themselves — a
request that gets a 403 re-authenticates and retries once — so this is a
one-time step.

Instead of credentials you may set `ANKIWEB_AUTH` to an `ankiweb` cookie
lifted from a logged-in browser. That still works, but only for the
ankiuser.net editor endpoints; `search` needs a real login.

## Usage

```sh
# bulk-add from a cards file
venv/bin/python main.py import examples/cards.txt
venv/bin/python main.py import examples/cards.txt --dry-run  # parse only
cat card.txt | venv/bin/python main.py import -              # read stdin

# single note
venv/bin/python main.py add --field Front=hello --field Back=world

# read and edit an existing note, by id or by the URL in your browser
venv/bin/python main.py get https://ankiuser.net/edit/1758491540484
venv/bin/python main.py update 1758491540484 --set Back='<div>new</div>'
venv/bin/python main.py update 1758491540484 --tags "numpy indexing"

# find notes
venv/bin/python main.py search 'deck:"ml 2025" tag:numpy'
```

`--deck-id` and `--notetype-id` override the values from `.env`.

Editing is field-name addressed and surgical: `--set Front=...` leaves every
other field and the tags untouched. This matters because the underlying
endpoint replaces the *whole* note, so `update` reads the note first and
merges your changes over it.

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

## Limitations

AnkiWeb exposes no note-deletion endpoint, so deleting cards still requires a
desktop or mobile Anki client.
