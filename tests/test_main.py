"""Tests for CLI composition helpers that do not touch AnkiWeb."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from fields import check
from main import _prepend_image_files


def test_prepend_image_file_adds_image_before_existing_field(tmp_path: Path) -> None:
    source = tmp_path / "diagram.png"
    payload = b"\x89PNG\r\n\x1a\nimage payload"
    source.write_bytes(payload)
    values = {"Back": "<b>answer</b>"}

    _prepend_image_files(values, [f"Back={source}"])

    assert values["Back"].endswith("<br><br><b>answer</b>")
    encoded = values["Back"].split("base64,", 1)[1].split('"', 1)[0]
    assert base64.b64decode(encoded, validate=True) == payload
    check("Back", values["Back"])


def test_prepend_image_file_requires_the_target_field(tmp_path: Path) -> None:
    source = tmp_path / "diagram.png"
    source.write_bytes(b"\x89PNG\r\n\x1a\nimage payload")

    with pytest.raises(SystemExit, match="must also be supplied"):
        _prepend_image_files({}, [f"Back={source}"])
