"""Minimal client for AnkiWeb's private protobuf API.

Wire format recovered from the AnkiWeb SvelteKit frontend bundle
(_app/immutable/chunks/frontend.*.mjs), which registers the protobuf
descriptors in plaintext. Endpoints used:

    ankiweb.net   POST /svc/account/login             authenticate
                  GET  /account/ankiuser-login?t=..   exchange token
                  POST /svc/search/search             find notes
    ankiuser.net  POST /svc/editor/get-note-info      read one note
                  POST /svc/editor/add-or-update      create or edit a note
                  POST /svc/editor/get-info-for-adding  decks + notetypes

The two hosts issue *different* `ankiweb` session cookies. Sending the
ankiweb.net cookie to an ankiuser.net endpoint (or vice versa) yields a
404, not a 403 — so a Session carries both.
"""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass, asdict
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import betterproto
from platformdirs import user_data_dir
import requests
from dotenv import load_dotenv

ANKIWEB = "https://ankiweb.net"
ANKIUSER = "https://ankiuser.net"
TIMEOUT_S = 15


_PROFILE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")


def _data_dir() -> Path:
    """Return the durable per-user directory used by the CLI.

    The launcher sets ``ANKI_UPLOAD_DATA_DIR`` when it needs an isolated
    location (for example, in tests).  Otherwise platformdirs gives us the
    normal application-data location: ``~/Library/Application Support`` on
    macOS, ``$XDG_DATA_HOME``/``anki-upload`` on Linux, and the equivalent
    Windows location.
    """
    override = os.getenv("ANKI_UPLOAD_DATA_DIR")
    if override:
        return Path(override).expanduser()
    return Path(user_data_dir("anki-upload", appauthor=False))


def _profile_name() -> str:
    """Return and validate the profile selected for this invocation.

    Profiles are deliberately a small, portable namespace.  In particular,
    accepting path separators here would let an environment variable escape
    ``DATA_DIR/profiles``.
    """
    profile = os.getenv("ANKI_UPLOAD_PROFILE", "default")
    if not _PROFILE_RE.fullmatch(profile):
        raise ValueError(
            "ANKI_UPLOAD_PROFILE must be 1-64 characters using only letters, "
            "digits, '-' or '_'"
        )
    return profile


DATA_DIR = _data_dir()
PROFILE = _profile_name()
PROFILE_DIR = DATA_DIR / "profiles" / PROFILE
CONFIG_PATH = PROFILE_DIR / ".env"

# Never ask python-dotenv to search from the current working directory.  A
# plugin checkout may be replaced while the user's working directory remains
# unchanged, so configuration must come from this stable location only.
load_dotenv(dotenv_path=CONFIG_PATH, override=False)

_session_override = os.getenv("ANKIWEB_SESSION_FILE")
SESSION_PATH = (
    Path(_session_override).expanduser()
    if _session_override
    else PROFILE_DIR / ".anki-session.json"
)


def ensure_data_dir() -> Path:
    """Create the root and selected profile with owner-only permissions."""
    DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    DATA_DIR.chmod(0o700)
    profiles_dir = DATA_DIR / "profiles"
    profiles_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    profiles_dir.chmod(0o700)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    PROFILE_DIR.chmod(0o700)
    return DATA_DIR


def protect_config_file() -> None:
    """Restrict an existing config file without following symlinks."""
    try:
        mode = CONFIG_PATH.lstat()
    except OSError:
        return
    if stat.S_ISREG(mode.st_mode):
        CONFIG_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)


protect_config_file()
USER_AGENT = (
    "Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
)


class AnkiError(RuntimeError):
    pass


class AnkiAuthError(AnkiError):
    pass


# --------------------------------------------------------------------------
# protobuf messages
# --------------------------------------------------------------------------


class LoginStatus(betterproto.Enum):
    UNKNOWN = 0
    AUTHENTICATED = 1
    INVALID_USER = 2
    INVALID_PASS = 3


@dataclass(eq=False, repr=False)
class LoginRequest(betterproto.Message):
    username: str = betterproto.string_field(1)
    password: str = betterproto.string_field(2)


@dataclass(eq=False, repr=False)
class LoginResponse(betterproto.Message):
    status: LoginStatus = betterproto.enum_field(1)
    token: str = betterproto.string_field(2)


@dataclass(eq=False, repr=False)
class AddMode(betterproto.Message):
    notetype_id: int = betterproto.int64_field(1)
    deck_id: int = betterproto.int64_field(2)


@dataclass(eq=False, repr=False)
class EditMode(betterproto.Message):
    note_id: int = betterproto.int64_field(1)


@dataclass(eq=False, repr=False)
class AddOrUpdateRequest(betterproto.Message):
    fields: List[str] = betterproto.string_field(1)
    tags: str = betterproto.string_field(2)
    # oneof mode
    add: AddMode = betterproto.message_field(3, group="mode")
    edit: EditMode = betterproto.message_field(4, group="mode")


@dataclass(eq=False, repr=False)
class Ord(betterproto.Message):
    val: int = betterproto.uint32_field(1)


@dataclass(eq=False, repr=False)
class NotetypeField(betterproto.Message):
    ord: Ord = betterproto.message_field(1)
    name: str = betterproto.string_field(2)


@dataclass(eq=False, repr=False)
class GetNoteInfoRequest(betterproto.Message):
    note_id: int = betterproto.int64_field(1)


@dataclass(eq=False, repr=False)
class GetNoteInfoResponse(betterproto.Message):
    fields: List[str] = betterproto.string_field(1)
    notetype_fields: List[NotetypeField] = betterproto.message_field(2)
    tags: str = betterproto.string_field(3)


@dataclass(eq=False, repr=False)
class IdName(betterproto.Message):
    id: int = betterproto.int64_field(1)
    name: str = betterproto.string_field(2)


@dataclass(eq=False, repr=False)
class GetInfoForAddingResponse(betterproto.Message):
    notetypes: List[IdName] = betterproto.message_field(1)
    decks: List[IdName] = betterproto.message_field(2)
    current_deck_id: int = betterproto.int64_field(3)
    current_notetype_id: int = betterproto.int64_field(4)
    fields: List[NotetypeField] = betterproto.message_field(5)


@dataclass(eq=False, repr=False)
class SearchRequest(betterproto.Message):
    search: str = betterproto.string_field(1)


@dataclass(eq=False, repr=False)
class SearchNote(betterproto.Message):
    note_id: int = betterproto.int64_field(1)
    joined_fields: str = betterproto.string_field(2)


@dataclass(eq=False, repr=False)
class SearchResponse(betterproto.Message):
    notes: List[SearchNote] = betterproto.message_field(1)
    error: str = betterproto.string_field(2)


# --------------------------------------------------------------------------
# session
# --------------------------------------------------------------------------


@dataclass
class Session:
    """The pair of `ankiweb` session cookies, one per host."""

    ankiweb_cookie: str = ""
    ankiuser_cookie: str = ""
    username: str = ""

    def cookies_for(self, host: str) -> Dict[str, str]:
        value = self.ankiweb_cookie if host == ANKIWEB else self.ankiuser_cookie
        if not value:
            other = "ankiweb.net" if host == ANKIWEB else "ankiuser.net"
            raise AnkiAuthError(
                f"no {other} cookie in this session — run `bin/anki-upload login` "
                "to obtain both"
            )
        return {"has_auth": "1", "ankiweb": value}

    def save(self, path: Path | None = None) -> None:
        """Persist this session in a private file, creating its parent safely."""
        destination = path or SESSION_PATH
        if path is None and destination.parent == PROFILE_DIR:
            ensure_data_dir()
        else:
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Write beside the destination, set the mode before it becomes
        # visible, and replace atomically so a refresh cannot leave a partial
        # cookie file behind.
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(json.dumps(asdict(self), indent=2))
        temporary_path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600 — credentials
        temporary_path.replace(destination)
        destination.chmod(stat.S_IRUSR | stat.S_IWUSR)

    @classmethod
    def load(cls, path: Path | None = None) -> Optional["Session"]:
        destination = path or SESSION_PATH
        try:
            payload = json.loads(destination.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return None
            session = cls(**payload)
            if not all(
                isinstance(value, str)
                for value in (
                    session.ankiweb_cookie,
                    session.ankiuser_cookie,
                    session.username,
                )
            ):
                return None
            return session
        except (OSError, ValueError, TypeError):
            return None


def _cookie_from_jar(jar: CookieJar, host: str) -> str:
    """Pull the `ankiweb` cookie scoped to `host` out of a requests cookie jar."""
    hostname = host.split("//", 1)[-1]
    for c in jar:
        if c.name == "ankiweb" and hostname.endswith(c.domain.lstrip(".")):
            return c.value or ""
    return ""


def login(username: str, password: str) -> Session:
    """Authenticate and return a Session holding both host cookies.

    Two steps: POST credentials to ankiweb.net, which sets that host's
    cookie and returns a one-shot token; then GET the token back on
    ankiuser.net, which sets the second cookie.
    """
    http = requests.Session()
    http.headers.update({"user-agent": USER_AGENT})

    req = LoginRequest()
    req.username = username
    req.password = password
    r = http.post(
        ANKIWEB + "/svc/account/login",
        headers={"content-type": "application/octet-stream", "origin": ANKIWEB},
        data=bytes(req),
        timeout=TIMEOUT_S,
    )
    if not r.ok:
        raise AnkiAuthError(f"login failed: HTTP {r.status_code} {r.text[:200]}")

    resp = LoginResponse().parse(r.content)
    if resp.status == LoginStatus.INVALID_USER:
        raise AnkiAuthError(f"unknown user {username!r}")
    if resp.status == LoginStatus.INVALID_PASS:
        raise AnkiAuthError("incorrect password")
    if resp.status != LoginStatus.AUTHENTICATED:
        raise AnkiAuthError(f"login failed with status {resp.status!r}")

    ankiweb_cookie = _cookie_from_jar(http.cookies, ANKIWEB)

    # Exchange the token for the ankiuser.net cookie.
    r2 = http.get(
        ANKIUSER + "/account/ankiuser-login",
        params={"t": resp.token},
        allow_redirects=True,
        timeout=TIMEOUT_S,
    )
    if not r2.ok:
        raise AnkiAuthError(f"token exchange failed: HTTP {r2.status_code}")

    ankiuser_cookie = _cookie_from_jar(http.cookies, ANKIUSER)
    if not ankiuser_cookie:
        raise AnkiAuthError("token exchange did not set an ankiuser.net cookie")

    return Session(
        ankiweb_cookie=ankiweb_cookie,
        ankiuser_cookie=ankiuser_cookie,
        username=username,
    )


def _credentials() -> Optional[Tuple[str, str]]:
    user = os.getenv("ANKIWEB_USERNAME")
    password = os.getenv("ANKIWEB_PASSWORD")
    return (user, password) if user and password else None


_SESSION: Optional[Session] = None


def get_session() -> Session:
    """Resolve a session: cached, file, env credentials, then auth cookie."""
    global _SESSION
    if _SESSION is not None:
        return _SESSION

    _SESSION = Session.load()
    if _SESSION is not None:
        return _SESSION

    creds = _credentials()
    if creds:
        _SESSION = login(*creds)
        _SESSION.save()
        return _SESSION

    # A bare browser cookie remains useful for the editor endpoints.  It is
    # intentionally not persisted and cannot authenticate search on ankiweb.
    legacy = os.getenv("ANKIWEB_AUTH")
    if legacy:
        _SESSION = Session(ankiuser_cookie=legacy)
        return _SESSION

    raise AnkiAuthError(
        "not authenticated — run `bin/anki-upload login`, or set "
        "ANKIWEB_USERNAME and ANKIWEB_PASSWORD in the persistent config file"
    )


def set_session(session: Session) -> None:
    global _SESSION
    _SESSION = session


def logout(path: Path | None = None) -> bool:
    """Forget the stored session. Returns whether a file was removed."""
    global _SESSION
    _SESSION = None
    destination = path or SESSION_PATH
    try:
        destination.unlink()
        return True
    except OSError:
        return False


# --------------------------------------------------------------------------
# transport
# --------------------------------------------------------------------------


def _post(path: str, payload: bytes, host: str = ANKIUSER, referer: str = "/") -> bytes:
    """POST a protobuf body, re-authenticating once if the session expired."""
    for attempt in (1, 2):
        session = get_session()
        r = requests.post(
            host + path,
            headers={
                "accept": "*/*",
                "content-type": "application/octet-stream",
                "origin": host,
                "referer": host + referer,
                "user-agent": USER_AGENT,
            },
            cookies=session.cookies_for(host),
            data=payload,
            timeout=TIMEOUT_S,
        )

        if r.status_code == 403 and attempt == 1:
            creds = _credentials()
            if creds is not None:
                # Cookie expired; log in again and retry once.
                set_session(login(*creds))
                get_session().save()
                continue

        if r.status_code == 403:
            raise AnkiAuthError(
                "403 — session expired. Run `bin/anki-upload login`, or set "
                "ANKIWEB_USERNAME/ANKIWEB_PASSWORD in the persistent config "
                "file to auto-renew."
            )
        if r.status_code == 404:
            raise AnkiError(
                f"404 on {host}{path} — the note does not exist, or this "
                "endpoint lives on the other host"
            )
        if not r.ok:
            raise AnkiError(f"{r.status_code} on {host}{path}: {r.text[:200]}")
        return r.content

    raise AnkiError("unreachable")


# --------------------------------------------------------------------------
# note ids
# --------------------------------------------------------------------------

_NOTE_ID_RE = re.compile(r"(?:ankiuser\.net/edit/)?(\d{6,})/?\s*$")


def parse_note_id(ref: str) -> int:
    """Accept a bare note id or an https://ankiuser.net/edit/<id> URL."""
    m = _NOTE_ID_RE.search(ref.strip())
    if not m:
        raise ValueError(f"could not parse a note id from {ref!r}")
    return int(m.group(1))


# --------------------------------------------------------------------------
# notes
# --------------------------------------------------------------------------


@dataclass
class Note:
    note_id: int
    field_names: List[str]
    field_values: List[str]
    tags: str

    @property
    def fields(self) -> Dict[str, str]:
        """Field values keyed by notetype field name."""
        return dict(zip(self.field_names, self.field_values))

    @property
    def url(self) -> str:
        return f"{ANKIUSER}/edit/{self.note_id}"


def get_note(note_id: int) -> Note:
    """Read a single note by id."""
    req = GetNoteInfoRequest()
    req.note_id = note_id
    raw = _post("/svc/editor/get-note-info", bytes(req), referer=f"/edit/{note_id}")
    resp = GetNoteInfoResponse().parse(raw)

    names = [f.name for f in sorted(resp.notetype_fields, key=lambda f: f.ord.val)]
    # Fall back to positional names if the notetype metadata is ever absent.
    if len(names) != len(resp.fields):
        names = names + [f"Field {i}" for i in range(len(names), len(resp.fields))]

    return Note(
        note_id=note_id,
        field_names=names[: len(resp.fields)],
        field_values=list(resp.fields),
        tags=resp.tags,
    )


def put_note(note_id: int, field_values: List[str], tags: str = "") -> None:
    """Overwrite a note's fields and tags wholesale.

    `field_values` must be in notetype field order and must cover every
    field — the server replaces the whole note, so a short list silently
    blanks the trailing fields. Prefer update_note() unless you have
    already read the note and know the full ordered list.
    """
    req = AddOrUpdateRequest()
    req.fields = list(field_values)
    req.tags = tags
    req.edit = EditMode()
    req.edit.note_id = note_id
    _post("/svc/editor/add-or-update", bytes(req), referer=f"/edit/{note_id}")


def update_note(
    note_id: int,
    changes: Dict[str, str] | None = None,
    tags: str | None = None,
) -> Note:
    """Read-modify-write a note, changing only the named fields.

    `changes` maps notetype field name (e.g. "Front") to new HTML. Fields
    not mentioned keep their current value; `tags` keeps its current value
    when None. Returns the note as written.
    """
    note = get_note(note_id)
    changes = changes or {}

    unknown = set(changes) - set(note.field_names)
    if unknown:
        raise AnkiError(
            f"note {note_id} has no field(s) {sorted(unknown)}; "
            f"available: {note.field_names}"
        )

    values = [
        changes.get(name, value)
        for name, value in zip(note.field_names, note.field_values)
    ]
    new_tags = note.tags if tags is None else tags

    put_note(note_id, values, new_tags)
    return Note(note_id, note.field_names, values, new_tags)


def add_note(
    fields: List[str],
    deck_id: int,
    notetype_id: int,
    tags: str = "",
) -> None:
    """Create a note. `fields` is in notetype field order."""
    req = AddOrUpdateRequest()
    req.fields = list(fields)
    req.tags = tags
    req.add = AddMode()
    req.add.deck_id = int(deck_id)
    req.add.notetype_id = int(notetype_id)
    _post("/svc/editor/add-or-update", bytes(req), referer="/add")


def get_decks_and_notetypes() -> GetInfoForAddingResponse:
    """List the account's decks and notetypes."""
    raw = _post("/svc/editor/get-info-for-adding", b"", referer="/add")
    return GetInfoForAddingResponse().parse(raw)


def search_notes(query: str) -> List[SearchNote]:
    """Search notes with Anki search syntax, e.g. 'deck:"ml 2025" tag:foo'.

    Lives on ankiweb.net, so this needs a full session (see login()).
    """
    req = SearchRequest()
    req.search = query
    raw = _post("/svc/search/search", bytes(req), host=ANKIWEB, referer="/search")
    resp = SearchResponse().parse(raw)
    if resp.error:
        raise AnkiError(f"search failed: {resp.error}")
    return list(resp.notes)
