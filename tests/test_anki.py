"""Tests for the pure helpers in anki.py.

Nothing here touches the network.
"""

from __future__ import annotations

import pytest

from anki import Session, parse_note_id

# ---------------------------------------------------------------------------
# parse_note_id
# ---------------------------------------------------------------------------


def test_parse_note_id_accepts_a_bare_id() -> None:
    assert parse_note_id("1758491540484") == 1758491540484


def test_parse_note_id_accepts_a_full_edit_url() -> None:
    assert parse_note_id("https://ankiuser.net/edit/1758491540484") == 1758491540484


def test_parse_note_id_accepts_a_url_with_a_trailing_slash() -> None:
    assert parse_note_id("https://ankiuser.net/edit/1758491540484/") == 1758491540484


def test_parse_note_id_tolerates_surrounding_whitespace() -> None:
    assert parse_note_id("  1758491540484\n") == 1758491540484


def test_parse_note_id_rejects_text_with_no_id() -> None:
    with pytest.raises(ValueError, match="could not parse a note id"):
        parse_note_id("https://ankiuser.net/edit/")


def test_parse_note_id_rejects_a_number_that_is_too_short_to_be_an_id() -> None:
    with pytest.raises(ValueError, match="could not parse a note id"):
        parse_note_id("12345")


# ---------------------------------------------------------------------------
# Session cookie routing -- the two hosts issue different cookies, and sending
# the wrong one yields a 404 rather than a 403, so this is worth pinning down.
# ---------------------------------------------------------------------------


def test_session_returns_the_ankiweb_cookie_for_the_ankiweb_host() -> None:
    session = Session(
        ankiweb_cookie="web-cookie-value",
        ankiuser_cookie="user-cookie-value",
        username="someone@example.com",
    )
    assert session.cookies_for("https://ankiweb.net") == {
        "has_auth": "1",
        "ankiweb": "web-cookie-value",
    }


def test_session_returns_the_ankiuser_cookie_for_the_ankiuser_host() -> None:
    session = Session(
        ankiweb_cookie="web-cookie-value",
        ankiuser_cookie="user-cookie-value",
        username="someone@example.com",
    )
    assert session.cookies_for("https://ankiuser.net") == {
        "has_auth": "1",
        "ankiweb": "user-cookie-value",
    }


def test_session_raises_when_the_needed_cookie_is_missing() -> None:
    legacy_cookie_only = Session(
        ankiweb_cookie="",
        ankiuser_cookie="user-cookie-value",
        username="",
    )
    # The editor endpoints still work...
    assert legacy_cookie_only.cookies_for("https://ankiuser.net")["ankiweb"] == (
        "user-cookie-value"
    )
    # ...but search, which lives on ankiweb.net, cannot be reached.
    with pytest.raises(Exception, match="no ankiweb.net cookie"):
        legacy_cookie_only.cookies_for("https://ankiweb.net")


def test_session_round_trips_through_its_file_format(tmp_path: object) -> None:
    from pathlib import Path

    assert isinstance(tmp_path, Path)
    path = tmp_path / "session.json"
    original = Session(
        ankiweb_cookie="web-cookie-value",
        ankiuser_cookie="user-cookie-value",
        username="someone@example.com",
    )
    original.save(path)
    assert Session.load(path) == original


def test_session_file_is_written_with_owner_only_permissions(tmp_path: object) -> None:
    import stat
    from pathlib import Path

    assert isinstance(tmp_path, Path)
    path = tmp_path / "session.json"
    Session(
        ankiweb_cookie="web-cookie-value",
        ankiuser_cookie="user-cookie-value",
        username="someone@example.com",
    ).save(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_session_load_returns_none_when_there_is_no_file() -> None:
    from pathlib import Path

    assert Session.load(Path("/nonexistent/definitely/not/here.json")) is None
