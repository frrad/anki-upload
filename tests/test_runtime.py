"""Tests for the persistent runtime/configuration contract."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import anki
import main
from anki import Session


def test_data_dir_override_is_used(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    override = tmp_path / "override"
    monkeypatch.setenv("ANKI_UPLOAD_DATA_DIR", str(override))
    assert anki._data_dir() == override


def test_profile_paths_are_under_the_data_root(tmp_path: Path) -> None:
    environment = os.environ.copy()
    for name in (
        "ANKIWEB_AUTH",
        "ANKIWEB_PASSWORD",
        "ANKIWEB_SESSION_FILE",
        "ANKIWEB_USERNAME",
    ):
        environment.pop(name, None)
    data_dir = tmp_path / "persistent data"
    environment.update(
        {
            "ANKI_UPLOAD_DATA_DIR": str(data_dir),
            "ANKI_UPLOAD_PROFILE": "work",
            "PYTHONPATH": str(Path(__file__).parents[1]),
        }
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import anki; print(anki.DATA_DIR); print(anki.PROFILE); "
            "print(anki.PROFILE_DIR); print(anki.CONFIG_PATH); "
            "print(anki.SESSION_PATH)",
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.splitlines() == [
        str(data_dir),
        "work",
        str(data_dir / "profiles" / "work"),
        str(data_dir / "profiles" / "work" / ".env"),
        str(data_dir / "profiles" / "work" / ".anki-session.json"),
    ]


def test_profiles_isolate_sessions_but_reuse_state_within_a_profile(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    environment = os.environ.copy()
    for name in (
        "ANKIWEB_AUTH",
        "ANKIWEB_PASSWORD",
        "ANKIWEB_SESSION_FILE",
        "ANKIWEB_USERNAME",
    ):
        environment.pop(name, None)
    environment.update(
        {
            "ANKI_UPLOAD_DATA_DIR": str(data_dir),
            "PYTHONPATH": str(Path(__file__).parents[1]),
        }
    )
    script = (
        "import anki; from anki import Session; "
        "Session(ankiweb_cookie='web', ankiuser_cookie='user', "
        "username=anki.PROFILE).save(); print(anki.SESSION_PATH)"
    )
    for profile in ("personal", "work"):
        environment["ANKI_UPLOAD_PROFILE"] = profile
        result = subprocess.run(
            [sys.executable, "-c", script],
            env=environment,
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == str(
            data_dir / "profiles" / profile / ".anki-session.json"
        )

    personal = Session.load(data_dir / "profiles" / "personal" / ".anki-session.json")
    work = Session.load(data_dir / "profiles" / "work" / ".anki-session.json")
    assert personal is not None
    assert work is not None
    assert personal.username == "personal"
    assert work.username == "work"


@pytest.mark.parametrize("profile", ["../escape", "a/b", "", ".", "a.b"])
def test_invalid_profile_is_rejected_before_path_resolution(
    tmp_path: Path, profile: str
) -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "ANKI_UPLOAD_DATA_DIR": str(tmp_path / "data"),
            "ANKI_UPLOAD_PROFILE": profile,
            "PYTHONPATH": str(Path(__file__).parents[1]),
        }
    )
    result = subprocess.run(
        [sys.executable, "-c", "import anki"],
        env=environment,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "ANKI_UPLOAD_PROFILE" in result.stderr


def test_session_save_creates_private_parent_and_file(tmp_path: Path) -> None:
    session_path = tmp_path / "nested" / "data" / ".anki-session.json"

    Session(ankiweb_cookie="web", ankiuser_cookie="user").save(session_path)

    assert session_path.exists()
    assert (session_path.parent.stat().st_mode & 0o777) == 0o700
    assert (session_path.stat().st_mode & 0o777) == 0o600
    assert Session.load(session_path) == Session(
        ankiweb_cookie="web", ankiuser_cookie="user"
    )


def test_config_is_loaded_only_from_the_explicit_data_directory(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "persistent data"
    checkout_dir = tmp_path / "plugin checkout"
    data_dir.mkdir()
    checkout_dir.mkdir()
    profile_dir = data_dir / "profiles" / "default"
    profile_dir.mkdir(parents=True)
    (profile_dir / ".env").write_text(
        "ANKIWEB_USERNAME=from-data\nANKIWEB_PASSWORD=data-secret\n",
        encoding="utf-8",
    )
    (checkout_dir / ".env").write_text(
        "ANKIWEB_USERNAME=from-checkout\nANKIWEB_PASSWORD=wrong-secret\n",
        encoding="utf-8",
    )

    environment = os.environ.copy()
    environment.pop("ANKIWEB_USERNAME", None)
    environment.pop("ANKIWEB_PASSWORD", None)
    environment["ANKI_UPLOAD_DATA_DIR"] = str(data_dir)
    environment["PYTHONPATH"] = str(Path(__file__).parents[1])
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os, anki; print(anki.DATA_DIR); "
            "print(os.environ['ANKIWEB_USERNAME'])",
        ],
        cwd=checkout_dir,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )

    assert str(data_dir) in result.stdout
    assert "from-data" in result.stdout
    assert "from-checkout" not in result.stdout


def test_persistent_config_can_override_the_session_path(tmp_path: Path) -> None:
    data_dir = tmp_path / "persistent data"
    custom_session = tmp_path / "custom session.json"
    profile_dir = data_dir / "profiles" / "default"
    profile_dir.mkdir(parents=True)
    (profile_dir / ".env").write_text(
        f"ANKIWEB_SESSION_FILE={custom_session}\n",
        encoding="utf-8",
    )

    environment = os.environ.copy()
    environment.pop("ANKIWEB_SESSION_FILE", None)
    environment["ANKI_UPLOAD_DATA_DIR"] = str(data_dir)
    environment["PYTHONPATH"] = str(Path(__file__).parents[1])
    result = subprocess.run(
        [sys.executable, "-c", "import anki; print(anki.SESSION_PATH)"],
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == str(custom_session)


def test_doctor_reports_state_without_exposing_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Keep the test isolated from the host's real persistent data directory.
    data_dir = tmp_path / "data"
    profile_dir = data_dir / "profiles" / "default"
    config_path = profile_dir / ".env"
    session_path = profile_dir / ".anki-session.json"
    monkeypatch.setattr(anki, "DATA_DIR", data_dir)
    monkeypatch.setattr(anki, "PROFILE_DIR", profile_dir)
    monkeypatch.setattr(main, "DATA_DIR", data_dir)
    monkeypatch.setattr(main, "PROFILE_DIR", profile_dir)
    monkeypatch.setattr(main, "CONFIG_PATH", config_path)
    monkeypatch.setattr(main, "SESSION_PATH", session_path)
    monkeypatch.setattr(anki, "SESSION_PATH", session_path)
    monkeypatch.setenv("ANKIWEB_USERNAME", "alice@example.com")
    monkeypatch.setenv("ANKIWEB_PASSWORD", "super-secret")

    assert main.main(["doctor"]) == 0
    output = capsys.readouterr().out

    assert "Python:" in output
    assert f"Data directory: {data_dir}" in output
    assert "Profile: default" in output
    assert f"Profile directory: {profile_dir}" in output
    assert "Credentials configured: yes" in output
    assert "super-secret" not in output
    assert "alice@example.com" not in output
    assert "Action: run `bin/anki-upload login`" in output

    Session(ankiweb_cookie="web", ankiuser_cookie="user").save(session_path)
    assert main.main(["doctor"]) == 0
    output = capsys.readouterr().out
    assert "Session present: yes; valid: yes" in output
    assert "Session cookie coverage: ankiweb.net=yes, ankiuser.net=yes" in output
    assert "local setup looks ready" in output
