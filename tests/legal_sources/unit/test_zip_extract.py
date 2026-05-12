"""Tests for the gii-zip helper."""

from __future__ import annotations

import io
import zipfile

import pytest

from kira.legal_sources._common.zip_extract import extract_xml_from_zip


def _make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_extract_xml_returns_xml_bytes() -> None:
    zip_bytes = _make_zip({"bgb.xml": b"<dokumente/>"})
    assert extract_xml_from_zip(zip_bytes) == b"<dokumente/>"


def test_extract_xml_picks_first_xml_when_multiple() -> None:
    zip_bytes = _make_zip({"a.txt": b"hi", "b.xml": b"<a/>", "c.xml": b"<b/>"})
    assert extract_xml_from_zip(zip_bytes) == b"<a/>"


def test_extract_xml_raises_when_no_xml() -> None:
    zip_bytes = _make_zip({"readme.txt": b"hi"})
    with pytest.raises(RuntimeError, match="Keine XML-Datei"):
        extract_xml_from_zip(zip_bytes)
