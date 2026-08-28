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

## Agent plugins

This repository packages the same `skills/anki-notes/SKILL.md` for Claude Code
and Codex, with product-specific manifests in `.claude-plugin/` and
`.codex-plugin/`. You can declare it as a project dependency so a fresh
checkout knows where to find the plugin. The examples below track the `master`
branch for active development; replace `master` with a release tag or commit
SHA when you need reproducible deployments.

The installed plugin still needs the normal Python environment and AnkiWeb
credentials described in [Setup](#setup). Its skill provides the agent-facing
workflow; `main.py` remains the command-line implementation.

### Claude Code

Commit this as `.claude/settings.json` in the consuming repository. If that
file already has settings, merge these keys into it rather than replacing the
file.

```json
{
  "extraKnownMarketplaces": {
    "project-tools": {
      "source": {
        "source": "settings",
        "name": "project-tools",
        "plugins": [
          {
            "name": "anki-upload",
            "source": {
              "source": "github",
              "repo": "frrad/anki-upload",
              "ref": "master"
            }
          }
        ]
      },
      "autoUpdate": true
    }
  },
  "enabledPlugins": {
    "anki-upload@project-tools": true
  }
}
```

When Claude Code first trusts the project, it prompts before adding the
marketplace and installing the enabled plugin. This consent step is expected:
the plugin includes executable helper code and can read and modify AnkiWeb
notes. With `autoUpdate` enabled, Claude Code checks the `master` branch at
startup. After an update, run `/reload-plugins` or start a new session to load
the new copy. See the [Claude Code plugin marketplace documentation](https://code.claude.com/docs/en/plugin-marketplaces)
and [plugin settings reference](https://code.claude.com/docs/en/settings).

### Codex

Commit this as `.agents/plugins/marketplace.json` in the consuming repository:

```json
{
  "name": "project-tools",
  "interface": {
    "displayName": "Project tools"
  },
  "plugins": [
    {
      "name": "anki-upload",
      "source": {
        "source": "url",
        "url": "https://github.com/frrad/anki-upload.git",
        "ref": "master"
      },
      "policy": {
        "installation": "INSTALLED_BY_DEFAULT",
        "authentication": "ON_INSTALL"
      },
      "category": "Productivity"
    }
  ]
}
```

Repository marketplaces are not discovered implicitly. After committing the
marketplace file, register the consuming repository's root once, then install
the plugin using the marketplace's top-level `name` (`project-tools` here):

```sh
codex plugin marketplace add /absolute/path/to/consuming-repository
codex plugin add anki-upload@project-tools
```

Restarting Codex alone does not perform those steps. Verify the result with:

```sh
codex plugin marketplace list
codex plugin list
```

The first command should list `project-tools`; the second should report
`anki-upload@project-tools` as `installed, enabled`. Start a new Codex task
after installation because an existing task does not dynamically reload its
skill inventory.

If the marketplace is already registered and you need to refresh its
Git-backed snapshot, run `codex plugin marketplace upgrade project-tools`,
reinstall with `codex plugin add anki-upload@project-tools`, and then start a
new task. See the [Codex plugin documentation](https://developers.openai.com/plugins/build/plugins).

The skill is intentionally generic. Put consuming-project-specific behavior
in that project's `AGENTS.md` or `CLAUDE.md`, not in this plugin.

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
venv/bin/python main.py get 1758491540484 --field Back > back.txt  # edit, then:
venv/bin/python main.py update 1758491540484 --set-file Back=back.txt
venv/bin/python main.py get 1758491540484 --json > note.json       # whole note
venv/bin/python main.py update 1758491540484 --set Back='new text'
venv/bin/python main.py update 1758491540484 --tags "numpy indexing"

# find notes
venv/bin/python main.py search 'deck:"ml 2025" tag:numpy'
```

`--deck-id` and `--notetype-id` override the values from `.env`.

Editing is field-name addressed and surgical: `--set Front=...` leaves every
other field and the tags untouched. This matters because the underlying
endpoint replaces the *whole* note, so `update` reads the note first and
merges your changes over it.

Prefer `--set-file NAME=PATH` over `--set NAME=VALUE` for anything
non-trivial. Field values routinely contain apostrophes, backslashes and
newlines, and passing those through a shell argument is how they get
mangled; a file has no quoting layer. `--field-file` does the same for
`add`. A single trailing newline is dropped, since editors add one that was
never part of the field.

`get --field NAME` is the other half of that loop: it prints one field's raw
value and nothing else, so it redirects to a file you can edit and hand back
to `--set-file`. The trailing newline it adds is the one `--set-file` strips,
so the round trip is byte-exact:

```sh
venv/bin/python main.py get 1758491540484 --field Back > back.txt
$EDITOR back.txt
venv/bin/python main.py update 1758491540484 --set-file Back=back.txt
```

Both `update` and `add` validate before writing and refuse a field that
would not render: a tag or HTML entity inside a `\( ... \)` or `\[ ... \]`
span, a stray `&nbsp;`, or markup other than `<br>`, `<pre>`, `<b>` and the
canonical inline image generated by `--image-file`. Anki renders fields as
HTML while MathJax needs each math span to be one unbroken run of text, and
those rules are what satisfy both at once.

`<pre>` is the one concession to code. It gives monospace and preserves
whitespace, which is the only way to align columns now that `&nbsp;` is
rejected outright:

```
<pre>nn.LayerNorm(512)  ✓ normalizes the last dim
nn.LayerNorm(10)   ✗ error</pre>
```

Its newlines are already real line breaks, so `import` leaves them alone
rather than promoting them to `<br>` — the same treatment a math span gets,
for the same reason. An unclosed or nested `<pre>` is rejected, since one
swallows the rest of the card and the other means nothing.

`<b>` is the other concession, for the one word in an answer you want the eye
to land on — the discriminating detail, not the whole line. Being inline it
protects nothing, so a newline beside it is promoted like any other, and it
is balance-checked exactly as `<pre>` is: an unclosed `<b>` bolds the rest of
the card, and a nested one means nothing.

```
dim is required, shape preserved, <b>inclusive</b>
```

### Inline images via Base64 data URLs

AnkiWeb's `add-or-update` endpoint accepts an image embedded directly in a
field as a Base64 `data:` URL:

```html
<img src="data:image/png;base64,..." alt="Description" style="max-width:100%;height:auto">
```

This was verified in August 2026 with a PNG added to a Basic note: AnkiWeb
stored the complete field verbatim, reading the note back and decoding the
payload reproduced the source image byte-for-byte, and the image rendered in
the reviewer.

Use `--image-file FIELD=PATH` with `add` or `update`. The target field must
also be supplied in the same command; the image is prepended with a blank line
before the field's existing content:

```sh
venv/bin/python main.py add \
  --field-file Front=front.html \
  --field-file Back=back.html \
  --image-file Back=diagram.png
```

The option accepts PNG, JPEG, GIF and WebP. It verifies the file signature,
Base64-encodes the bytes, emits a responsive tag with the filename as alt text,
and sends the result through the normal `fields.check()` validation. Arbitrary
`<img>` markup, remote URLs, extra attributes, unsupported formats and MIME
mismatches remain rejected.

After adding the note, read it back and confirm that the Base64 payload decodes
to the original bytes.

Base64 adds roughly one third to the binary size and embeds a separate copy in
every note. It is a practical option for an occasional self-contained image,
not a replacement for Anki's media collection when an asset is large or reused.

## Card file format

Cards are separated by a line of `===`, and the front and back of a card by a
line of `---`. Everything between delimiters is taken as written — LaTeX,
backslashes and quotes all pass through without escaping, which is the point of
the format.

The one transformation is line breaks. Anki renders a field as HTML, so a bare
newline would collapse and your lines would run together; `import` promotes
each one to `<br>` so the card looks like the file. Newlines *inside* a
`\( ... \)`, `\[ ... \]` or `<pre>` span are left alone — MathJax reads them as
whitespace and a `<br>` there would stop the formula rendering, while `<pre>`
renders them as breaks already and a `<br>` would double-space the code. That
is why the display equation below can span three lines and still work.

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

This tool speaks AnkiWeb's `/svc/` API, which is note-addressed: it is an editor,
a search box and a reviewer. Card *state* — flags, due dates, queue, suspension,
deletion — never crosses it. That data moves only over the separate `sync/`
protocol that Anki desktop and AnkiDroid use, so anything card-level is out of
reach here and still requires a desktop or mobile client. Note deletion and card
flags are the two you are most likely to go looking for.

The exception is search: card-level *predicates* are evaluated server-side, so
`search 'flag:1'`, `search 'is:suspended'` and `search 'prop:due<7'` all work and
return matching note ids — you can filter on card state without being able to read
or change it.

See [docs/ankiweb-protocols.md](docs/ankiweb-protocols.md) for the full protocol
map and the evidence behind it.
