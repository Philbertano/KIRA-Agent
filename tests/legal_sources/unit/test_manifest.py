import json

import pytest
from pydantic import ValidationError

from kira.legal_sources._common.manifest import (
    GesetzManifestEntry,
    Manifest,
    ManifestVersionError,
    parse_manifest,
)


def test_parses_minimal_v2_manifest():
    payload = {
        "version": 2,
        "stand": "2026-05-10",
        "gesetze": {
            "bgb": {
                "abkuerzung": "BGB",
                "titel": "Bürgerliches Gesetzbuch",
                "type": "Gesetz",
                "meta_key": "gesetze/bgb/_meta.json",
                "upstream_etag": "\"abc\"",
                "upstream_last_modified": "Wed, 06 May 2026 15:45:05 GMT",
            }
        },
    }
    m = parse_manifest(payload)
    assert isinstance(m, Manifest)
    assert m.version == 2
    assert "bgb" in m.gesetze
    assert m.gesetze["bgb"].abkuerzung == "BGB"


def test_v1_manifest_raises_clear_error():
    payload = {"version": 1, "files": ["gesetze/bgb.json"]}
    with pytest.raises(ManifestVersionError) as excinfo:
        parse_manifest(payload)
    assert "version 2" in str(excinfo.value)


def test_unknown_version_raises():
    payload = {"version": 99, "stand": "2026-05-10", "gesetze": {}}
    with pytest.raises(ManifestVersionError):
        parse_manifest(payload)


def test_round_trip_serialization():
    m = Manifest(
        version=2,
        stand="2026-05-10",
        gesetze={
            "bgb": GesetzManifestEntry(
                abkuerzung="BGB",
                titel="Bürgerliches Gesetzbuch",
                type="Gesetz",
                meta_key="gesetze/bgb/_meta.json",
                upstream_etag="\"abc\"",
                upstream_last_modified="Wed, 06 May 2026 15:45:05 GMT",
            )
        },
    )
    dumped = m.model_dump_json()
    parsed = parse_manifest(json.loads(dumped))
    assert parsed.gesetze["bgb"].abkuerzung == "BGB"


def test_extra_fields_rejected_on_entry():
    payload = {
        "version": 2,
        "stand": "2026-05-10",
        "gesetze": {
            "bgb": {
                "abkuerzung": "BGB",
                "titel": "x",
                "type": "Gesetz",
                "meta_key": "gesetze/bgb/_meta.json",
                "upstream_etag": "\"abc\"",
                "upstream_last_modified": "...",
                "extra_field": "boom",
            }
        },
    }
    with pytest.raises(ValidationError):
        parse_manifest(payload)
