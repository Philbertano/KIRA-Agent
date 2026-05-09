"""Region-pin guard for the legal_sources CDK stack.

Synthesises the stack in-process and walks the resulting template to assert
that no resource declares a region other than eu-central-1. This is the only
place we type-check infrastructure independently of a deployed account.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

INFRA_DIR = Path(__file__).resolve().parents[2] / "infra" / "legal_sources"


@pytest.fixture(scope="module")
def synthesized_template() -> dict:
    """Synthesise the CDK app once and return the CloudFormation template."""
    cdk = pytest.importorskip("aws_cdk")
    sys.path.insert(0, str(INFRA_DIR))
    try:
        from stack import LegalSourcesStack  # type: ignore[import-not-found]
    finally:
        # Keep sys.path tidy; the import has happened.
        pass

    app = cdk.App()
    LegalSourcesStack(
        app,
        "TestRegionPin",
        env=cdk.Environment(account="000000000000", region="eu-central-1"),
    )
    cloud_assembly = app.synth()
    stack = cloud_assembly.get_stack_by_name("TestRegionPin")
    return stack.template


def test_no_resource_declares_non_eu_region(synthesized_template):
    """Every Region/region property in the template must be eu-central-1 or absent."""
    offenders: list[tuple[str, object]] = []

    def _walk(obj, path):
        if isinstance(obj, dict):
            for key, value in obj.items():
                if (
                    key.lower() == "region"
                    and isinstance(value, str)
                    and value not in ("eu-central-1", "")
                ):
                    offenders.append((path, value))
                _walk(value, f"{path}.{key}")
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                _walk(item, f"{path}[{i}]")

    _walk(synthesized_template, "$")
    assert not offenders, f"Non-eu-central-1 regions found: {offenders}"


def test_stack_constructor_rejects_non_eu_region():
    """The Python guard in the stack constructor refuses non-eu deploys."""
    cdk = pytest.importorskip("aws_cdk")
    sys.path.insert(0, str(INFRA_DIR))
    from stack import LegalSourcesStack  # type: ignore[import-not-found]

    app = cdk.App()
    with pytest.raises(RuntimeError) as excinfo:
        LegalSourcesStack(
            app,
            "TestNonEU",
            env=cdk.Environment(account="000000000000", region="us-east-1"),
        )
    assert "eu-central-1" in str(excinfo.value)
