import pytest
from pydantic import ValidationError

from horus.core.models import Limits, Plan
from horus.core.store import AssetStore, HorusError


def test_immutable_assets_have_content_identity_and_detect_tampering(tmp_path):
    store = AssetStore(tmp_path)
    ref = store.put("plans", {"b": 2, "a": 1})
    assert store.put("plans", {"a": 1, "b": 2}) == ref
    assert store.get("plans", ref) == {"a": 1, "b": 2}
    store.path("plans", ref).write_text('{"a": 3}')
    with pytest.raises(HorusError, match="integrity"):
        store.get("plans", ref)


def test_references_cannot_escape_project(tmp_path):
    store = AssetStore(tmp_path)
    with pytest.raises(HorusError):
        store.get("plans", "../../other-project/secret")


def test_limits_are_strict_and_bounded():
    with pytest.raises(ValidationError):
        Limits(maxVus="5")
    with pytest.raises(ValidationError):
        Limits(maxVus=100_000)


def test_plan_cannot_self_approve():
    with pytest.raises(ValidationError):
        Plan.model_validate({"schemaVersion": "1", "approved": True})
