import pytest

from kira.legal_sources._common.region import REQUIRED_REGION, ensure_eu_region


def test_required_region_is_eu_central_1():
    assert REQUIRED_REGION == "eu-central-1"


def test_ensure_eu_region_passes_for_correct_region():
    ensure_eu_region("eu-central-1")  # no exception


def test_ensure_eu_region_rejects_non_eu():
    with pytest.raises(RuntimeError) as excinfo:
        ensure_eu_region("us-east-1")
    assert "eu-central-1" in str(excinfo.value)


def test_ensure_eu_region_rejects_other_eu_region():
    # Even another EU region is rejected; we want strict eu-central-1 pinning.
    with pytest.raises(RuntimeError):
        ensure_eu_region("eu-west-1")
