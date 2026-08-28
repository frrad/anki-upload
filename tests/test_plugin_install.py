"""Exercise the launcher the way an installed agent plugin uses it.

These tests deliberately run from a disposable, cache-like copy rather than
from the repository checkout.  No AnkiWeb request is made: ``doctor`` and an
``import --dry-run`` only exercise bootstrap, path resolution, and parsing.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
_SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    "__pycache__",
    "venv",
    "runtimes",
}
_SKIP_FILES = {".env", ".anki-session.json"}


def _plugin_files() -> list[Path]:
    """Return tracked files plus the new bootstrap files during local work."""
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        check=True,
        stdout=subprocess.PIPE,
    )
    tracked = {Path(name) for name in result.stdout.decode().split("\0") if name}
    # During development these files can be untracked until the final commit;
    # in CI they are already included by git ls-files.
    for name in ("bin/anki-upload", "pyproject.toml", "uv.lock"):
        if (REPO_ROOT / name).exists():
            tracked.add(Path(name))
    return sorted(tracked)


def _copy_plugin(destination: Path) -> None:
    for relative in _plugin_files():
        if relative.name in _SKIP_FILES or any(
            part in _SKIP_DIRS for part in relative.parts
        ):
            continue
        source = REPO_ROOT / relative
        if not source.is_file():
            # A deleted tracked file is still returned by git ls-files while a
            # working tree change is under test.
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _run_launcher(
    plugin: Path,
    data_dir: Path,
    *arguments: str,
    cwd: Path,
    profile: str | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    for name in (
        "ANKIWEB_AUTH",
        "ANKIWEB_DECK_ID",
        "ANKIWEB_NOTETYPE_ID",
        "ANKIWEB_PASSWORD",
        "ANKIWEB_SESSION_FILE",
        "ANKIWEB_USERNAME",
        "ANKI_UPLOAD_PROFILE",
        "UV_PROJECT_ENVIRONMENT",
    ):
        environment.pop(name, None)
    environment["ANKI_UPLOAD_DATA_DIR"] = str(data_dir)
    if profile is not None:
        environment["ANKI_UPLOAD_PROFILE"] = profile
    return subprocess.run(
        [str(plugin / "bin/anki-upload"), *arguments],
        cwd=cwd,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _assert_ok(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 0, (
        f"launcher failed ({result.returncode})\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_clean_installed_plugin_bootstraps_and_keeps_state_after_replacement(
    tmp_path: Path,
) -> None:
    """A cache replacement must not discard the user's runtime or session."""
    plugin = tmp_path / "plugin cache" / "anki-upload"
    data_dir = tmp_path / "user data with spaces"
    caller_dir = tmp_path / "caller working directory"
    plugin.mkdir(parents=True)
    caller_dir.mkdir()
    _copy_plugin(plugin)

    assert not (plugin / ".env").exists()
    assert not (plugin / ".anki-session.json").exists()
    assert not (plugin / "venv").exists()

    doctor = _run_launcher(plugin, data_dir, "doctor", cwd=caller_dir)
    _assert_ok(doctor)
    assert str(data_dir) in doctor.stdout
    runtime_envs = list((data_dir / "runtimes").glob("*/venv"))
    assert len(runtime_envs) == 1
    runtime_before = runtime_envs[0]

    help_result = _run_launcher(plugin, data_dir, "--help", cwd=caller_dir)
    _assert_ok(help_result)
    assert "doctor" in help_result.stdout

    cards = caller_dir / "cards.txt"
    cards.write_text("A clean plugin flow\n---\nworks offline\n", encoding="utf-8")
    dry_run = _run_launcher(
        plugin,
        data_dir,
        "import",
        str(cards),
        "--dry-run",
        cwd=caller_dir,
    )
    _assert_ok(dry_run)
    assert "1 card(s) parsed, nothing uploaded" in dry_run.stdout

    # A valid-looking session is enough to test persistence without contacting
    # AnkiWeb.  The real login command writes this same durable path.
    session = data_dir / "profiles" / "default" / ".anki-session.json"
    session.write_text(
        json.dumps(
            {
                "ankiweb_cookie": "web-cookie",
                "ankiuser_cookie": "user-cookie",
                "username": "test@example.com",
            }
        ),
        encoding="utf-8",
    )
    session.chmod(0o600)
    session_before = session.read_bytes()

    shutil.rmtree(plugin)
    plugin.mkdir(parents=True)
    _copy_plugin(plugin)
    assert not (plugin / ".anki-session.json").exists()

    after_replacement = _run_launcher(plugin, data_dir, "doctor", cwd=caller_dir)
    _assert_ok(after_replacement)
    assert session.read_bytes() == session_before
    assert str(session) in after_replacement.stdout
    assert runtime_before.is_dir()
    assert list((data_dir / "runtimes").glob("*/venv")) == [runtime_before]


def test_different_plugin_locks_get_isolated_runtimes_and_share_default_profile(
    tmp_path: Path,
) -> None:
    """Different installed versions never share a runtime, but share default state."""
    cache = tmp_path / "plugin cache"
    plugin_a = cache / "anki-upload-a"
    plugin_b = cache / "anki-upload-b"
    data_dir = tmp_path / "user data"
    caller_dir = tmp_path / "caller"
    caller_dir.mkdir()
    plugin_a.mkdir(parents=True)
    plugin_b.mkdir(parents=True)
    _copy_plugin(plugin_a)
    _copy_plugin(plugin_b)

    # A TOML comment keeps each lock valid while making the lock contents (and
    # therefore the runtime identity) deliberately different.
    lock_b = plugin_b / "uv.lock"
    lock_b.write_text(
        lock_b.read_text(encoding="utf-8") + "\n# second installed plugin version\n",
        encoding="utf-8",
    )

    first = _run_launcher(plugin_a, data_dir, "doctor", cwd=caller_dir)
    _assert_ok(first)
    second = _run_launcher(plugin_b, data_dir, "doctor", cwd=caller_dir)
    _assert_ok(second)

    runtime_envs = sorted((data_dir / "runtimes").glob("*/venv"))
    assert len(runtime_envs) == 2
    assert runtime_envs[0] != runtime_envs[1]

    session = data_dir / "profiles" / "default" / ".anki-session.json"
    session.parent.mkdir(parents=True, exist_ok=True)
    session.write_text(
        json.dumps(
            {
                "ankiweb_cookie": "web-cookie",
                "ankiuser_cookie": "user-cookie",
                "username": "default@example.com",
            }
        ),
        encoding="utf-8",
    )
    session.chmod(0o600)

    # The second plugin copy sees the session created by the first profile,
    # without relying on login or any AnkiWeb request.
    shared_state = _run_launcher(plugin_b, data_dir, "doctor", cwd=caller_dir)
    _assert_ok(shared_state)
    assert f"Session file: {session}" in shared_state.stdout
    assert "Session present: yes; valid: yes" in shared_state.stdout


def test_named_profiles_do_not_share_config_or_sessions(
    tmp_path: Path,
) -> None:
    """Profile selection isolates accounts while retaining one data directory."""
    plugin = tmp_path / "plugin cache" / "anki-upload"
    data_dir = tmp_path / "user data"
    caller_dir = tmp_path / "caller"
    caller_dir.mkdir()
    plugin.mkdir(parents=True)
    _copy_plugin(plugin)

    work = data_dir / "profiles" / "work"
    personal = data_dir / "profiles" / "personal"
    work.mkdir(parents=True)
    personal.mkdir(parents=True)
    (work / ".env").write_text(
        "ANKIWEB_USERNAME=work@example.com\n"
        "ANKIWEB_PASSWORD=work-secret\n"
        "ANKIWEB_DECK_ID=101\n",
        encoding="utf-8",
    )
    (personal / ".env").write_text(
        "ANKIWEB_USERNAME=personal@example.com\n"
        "ANKIWEB_PASSWORD=personal-secret\n"
        "ANKIWEB_DECK_ID=202\n",
        encoding="utf-8",
    )
    for profile_dir, username in (
        (work, "work@example.com"),
        (personal, "personal@example.com"),
    ):
        session = profile_dir / ".anki-session.json"
        session.write_text(
            json.dumps(
                {
                    "ankiweb_cookie": f"{username}-web",
                    "ankiuser_cookie": f"{username}-user",
                    "username": username,
                }
            ),
            encoding="utf-8",
        )
        session.chmod(0o600)

    work_doctor = _run_launcher(
        plugin, data_dir, "doctor", cwd=caller_dir, profile="work"
    )
    personal_doctor = _run_launcher(
        plugin, data_dir, "doctor", cwd=caller_dir, profile="personal"
    )
    _assert_ok(work_doctor)
    _assert_ok(personal_doctor)

    work_session = work / ".anki-session.json"
    personal_session = personal / ".anki-session.json"
    assert f"Config file: {work / '.env'}" in work_doctor.stdout
    assert f"Session file: {work_session}" in work_doctor.stdout
    assert "Session present: yes; valid: yes" in work_doctor.stdout
    assert f"Config file: {personal / '.env'}" in personal_doctor.stdout
    assert f"Session file: {personal_session}" in personal_doctor.stdout
    assert "Session present: yes; valid: yes" in personal_doctor.stdout
    assert str(work_session) not in personal_doctor.stdout
    assert str(personal_session) not in work_doctor.stdout
