# How Anki clients talk to AnkiWeb

AnkiWeb runs **two unrelated remote protocols**, and the different Anki clients use
different ones. This tool speaks the smaller of the two, which puts a hard ceiling on
what it can ever do.

|                | `/svc/` protobuf RPC                     | `sync/` protocol                              |
| -------------- | ---------------------------------------- | --------------------------------------------- |
| Hosts          | `ankiweb.net`, `ankiuser.net`            | `sync.ankiweb.net`                             |
| Used by        | the AnkiWeb web client — and `anki-upload` | Anki desktop, AnkiDroid                      |
| Shape          | one endpoint per operation, protobuf bodies | whole-collection record streams            |
| Addressed by   | note id, deck id, notetype id            | every table: cards, notes, revlog, graves      |
| Card state     | never sent or accepted                   | the entire card row                            |

The short version: **`/svc/` is an editor, a search box and a reviewer. It is
note-addressed, and card rows never cross it. Everything that is card state rather
than note content moves only over `sync/`.**

This document exists because that boundary is invisible from the outside and expensive
to rediscover — see [What this means for anki-upload](#what-this-means-for-anki-upload)
for the practical consequence, and [Provenance](#provenance) for how it was
established and how to tell whether it has gone stale.

## `/svc/` — what the web client uses

The wire format is recovered from the AnkiWeb SvelteKit bundle, as described in the
README. Following `_app/immutable/entry/app.*.mjs` to every route chunk it lists and
grepping the lot yields **67 endpoints**:

| Service   | Count | What it covers                                         |
| --------- | ----: | ------------------------------------------------------ |
| `account` |    12 | login, signup, settings, terms, account deletion       |
| `decks`   |     8 | create/rename/remove/select, deck sharing              |
| `editor`  |     4 | read a note, add-or-update a note, notetype metadata   |
| `radmin`  |    25 | server administration — not reachable by a normal account |
| `search`  |     1 | `search`                                               |
| `shared`  |    13 | the shared-deck and add-on marketplace                 |
| `study`   |     4 | the reviewer and per-deck limits                       |

Setting aside `radmin`, that is 42 user-facing endpoints, and the *shape* of the set is
the point. There is no `cards` service. There is no endpoint that accepts or returns a
card row. The four `editor` endpoints are the whole of the note-editing surface, and
`anki-upload` already uses all but one of them.

Two message descriptors are worth recording verbatim, because they are the closest the
protocol comes to card data and they show exactly where it stops.

**`search` response.** Card-level *predicates* are accepted, but only note ids come
back:

```
SearchResponse {
  repeated Note notes = 1;   // Note { int64 note_id = 1; string joined_fields = 2; }
  string error = 2;
}
```

**The reviewer's card message**, from `study-cards` — the one place a `card_id`
appears at all:

```
{ card_id, question, answer, count_index, button_labels,
  note_id, template_index, next_states }
```

Rendered HTML and scheduling states, to drive a review session. No `flags`, no `queue`,
no `due`, no `ivl` — none of the card row. `anki.py` already parses every field both of
these carry, so nothing is being dropped today; there is simply nothing more there.

## `sync/` — what desktop and AnkiDroid use

Desktop and AnkiDroid do not use `/svc/` at all. They talk to `https://sync.ankiweb.net/`
([`rslib/src/sync/http_client/mod.rs:45`][client]) using a twelve-method protocol
([`rslib/src/sync/collection/protocol.rs:35-48`][proto]):

```
hostKey  meta  start  applyGraves  applyChanges  chunk
applyChunk  sanityCheck2  finish  abort  upload  download
```

This is not an operation-per-endpoint API. It is a whole-collection differ: the client
and server exchange streams of record chunks
([`rslib/src/sync/collection/chunks.rs:34-44`][chunk]):

```rust
pub struct Chunk {
    pub done: bool,
    pub revlog: Vec<RevlogEntry>,
    pub cards: Vec<CardEntry>,
    pub notes: Vec<NoteEntry>,
}
```

and `CardEntry` ([`chunks.rs:64-87`][entry]) is the full card row — `id`, `nid`, `did`,
`ord`, `mtime`, `usn`, `ctype`, `queue`, `due`, `ivl`, `factor`, `reps`, `lapses`,
`left`, `odue`, `odid`, **`flags`**, `data`.

**This is the only channel over which card state crosses the network.**

## No client has a "set flag" remote call

This is the finding that explains everything above, and it is easy to get backwards.

Anki desktop and AnkiDroid do not ask a server to flag a card. They mutate their
**local** collection through the embedded Rust backend, and the changed row reaches
AnkiWeb later as an ordinary `CardEntry` in the next sync chunk. In AnkiDroid those are
two separate, unrelated calls:

```kotlin
// libanki/src/main/java/com/ichi2/anki/libanki/Collection.kt:1247-1250
fun setUserFlagForCards(
    cids: Iterable<Long>,
    flag: Int,
): OpChangesWithCount = backend.setFlag(cardIds = cids, flag = flag)

// …:1313-1316
fun syncCollection(
    auth: SyncAuth,
    syncMedia: Boolean,
): SyncCollectionResponse = backend.syncCollection(auth, syncMedia)
```

`pylib` is the same two calls — [`collection.py:1131`][pyflag] and
[`collection.py:1164`][pysync] — because both clients wrap the same `rslib`.

So there is no flag endpoint to find on the remote. Not because AnkiWeb hides one, but
because **no client ever makes one**. `CardsService.SetFlag`
([`proto/anki/cards.proto:17`][cardsvc]) is a call into a local backend, not a network
request.

The same reasoning applies to every other card-state operation, which is why searching
for a "suspend card" or "delete note" endpoint will fail the same way.

## A third surface that is not remote at all

Worth recording because it is a plausible wrong turn.

Anki desktop serves protobuf RPC over `http://127.0.0.1:<port>/_anki/<method>`. It
looks like a remote API — HTTP, protobuf bodies, one path per method — but it is the
desktop's own webview talking to its in-process backend, and it is not a way to reach
AnkiWeb.

It is also closed for this purpose. The exposed methods are an explicit allowlist
([`qt/aqt/mediasrv.py:1096-1167`][exposed]); of `CardsService` only `get_card` appears,
and `set_flag` does not. Every POST additionally requires
`Authorization: Bearer <key>`, where the key is `secrets.token_urlsafe(32)` generated
fresh on each run ([`mediasrv.py:1296-1303`][apikey]).

## What this means for `anki-upload`

Anything that is **card state rather than note content** — flags, due dates, queue,
suspension, deletion — is reachable only over `sync/`, and so is out of scope for a
`/svc/` client. The note-deletion limitation the README already documents is not a
one-off gap; it is one instance of this rule.

Reaching card state would mean a local collection file plus `pip install anki`
(9.3 MB wheel, ships the Rust backend), driving Anki's own sync implementation via
`col.sync_login()` / `col.sync_collection()` — that is, *calling* Anki's sync rather
than reimplementing the protocol. That route is known to work and was **considered and
declined** in August 2026: it would turn a dependency-light HTTP client into something
that maintains a synced local mirror of the collection.

## The one card-adjacent thing that does work

`/svc/search/search` runs the same `rslib` query parser as every other client
([`rslib/src/search/parser.rs:372`][parser]), so **card-level predicates work
server-side even though card data never comes back**:

```sh
venv/bin/python main.py search 'flag:1'          # notes with a red-flagged card
venv/bin/python main.py search 'is:suspended'
venv/bin/python main.py search 'prop:due<7'
```

Verified working against AnkiWeb on 2026-08-05, with no code change. The server even
supplies its own vocabulary on a bad value — `flag:8` returns:

> Invalid search: `flag:` must be followed by a valid flag number: `1` (red),
> `2` (orange), `3` (green), `4` (blue), `5` (pink), `6` (turquoise), `7` (purple)
> or `0` (no flag).

Two limits follow from the response shape. Results are **note** ids, so a note whose
notetype generates several cards matches if *any* of its cards qualifies, and you
cannot tell which. And it is query-only: you can ask which notes have a red-flagged
card, but you can neither read the flag back nor set it.

That asymmetry is the useful summary of the whole document — **`/svc/` lets you filter
on card state without ever exposing it.**

## Provenance

Checked **2026-08-05**. AnkiWeb ships a new bundle on every deploy, so treat the
specifics below as a staleness check rather than as stable facts.

- **Bundle**: `_app/immutable/entry/app.CqXcrSxO.mjs` and the 54 chunks it references
  (~672 KB), including `chunks/frontend.BA434DQn.mjs` which carries the protobuf
  descriptors. Hashes change on every deploy.
- **Method**: fetch `app.*.mjs`, extract its `chunks/*.mjs` and `nodes/*.mjs` list,
  fetch all 54, then grep the set. A case-insensitive search for `flag` across all of
  them returns **zero** matches — which also rules out a `flags` field, since that
  contains `flag` as a substring.
- **Probing**: six speculative paths (`/svc/editor/set-flag`, `/svc/cards/set-flag`,
  `/svc/study/set-flag`, `/svc/editor/set-card-flag`, `/svc/cards/get-card`,
  `/svc/browser/set-flag`) were POSTed to **both** hosts with an empty body — a no-op
  even had one existed. All twelve returned 404.
- **Upstream sources**, both read at these commits:
  - `ankitects/anki` @ `898902cdaf88695db684cc9b60d97e9ddbc1c49f` (2026-08-05)
  - `ankidroid/Anki-Android` @ `eb22e1e6ad7e245d6fe34eceb75954792f6fe7ab` (2026-08-03)

All line references in this document point at those two commits.

[client]: https://github.com/ankitects/anki/blob/898902cdaf88695db684cc9b60d97e9ddbc1c49f/rslib/src/sync/http_client/mod.rs#L45
[proto]: https://github.com/ankitects/anki/blob/898902cdaf88695db684cc9b60d97e9ddbc1c49f/rslib/src/sync/collection/protocol.rs#L35-L48
[chunk]: https://github.com/ankitects/anki/blob/898902cdaf88695db684cc9b60d97e9ddbc1c49f/rslib/src/sync/collection/chunks.rs#L34-L44
[entry]: https://github.com/ankitects/anki/blob/898902cdaf88695db684cc9b60d97e9ddbc1c49f/rslib/src/sync/collection/chunks.rs#L64-L87
[cardsvc]: https://github.com/ankitects/anki/blob/898902cdaf88695db684cc9b60d97e9ddbc1c49f/proto/anki/cards.proto#L17
[parser]: https://github.com/ankitects/anki/blob/898902cdaf88695db684cc9b60d97e9ddbc1c49f/rslib/src/search/parser.rs#L372
[exposed]: https://github.com/ankitects/anki/blob/898902cdaf88695db684cc9b60d97e9ddbc1c49f/qt/aqt/mediasrv.py#L1096-L1167
[apikey]: https://github.com/ankitects/anki/blob/898902cdaf88695db684cc9b60d97e9ddbc1c49f/qt/aqt/mediasrv.py#L1296-L1303
[pyflag]: https://github.com/ankitects/anki/blob/898902cdaf88695db684cc9b60d97e9ddbc1c49f/pylib/anki/collection.py#L1131
[pysync]: https://github.com/ankitects/anki/blob/898902cdaf88695db684cc9b60d97e9ddbc1c49f/pylib/anki/collection.py#L1164
