import pytest

from horus.adapters.hurl import MISSING, compare, pointer
from horus.core.store import HorusError
from horus.core.values import Redactor, resolve


def test_runtime_values_preserve_json_types_and_escape_literal_placeholders():
    context = {"vars": {"count": 42}, "run": {"id": "abc"}, "secrets": {}}
    assert resolve({"value": "${vars.count}", "name": "run-${run.id}"}, context) == {
        "value": 42,
        "name": "run-abc",
    }
    assert resolve("$${vars.count}", context) == "${vars.count}"
    with pytest.raises(HorusError, match="missing_variable"):
        resolve("${vars.missing}", context)


def test_redaction_covers_nested_fields_and_known_values_in_diagnostics():
    redactor = Redactor(["private-value"])
    assert redactor.clean(
        {"password": "unseen", "nested": ["private-value"], "Authorization": "Bearer anything"}
    ) == {"password": "[REDACTED]", "nested": ["[REDACTED]"], "Authorization": "[REDACTED]"}


def test_json_comparisons_preserve_types_with_consistent_number_semantics():
    assert compare({"nested": [1]}, "eq", {"nested": [1.0]})
    assert not compare({"nested": [True]}, "eq", {"nested": [1]})
    assert compare(1.0, "type", "integer")
    assert not compare(True, "type", "integer")
    assert pointer(["first", "last"], "/-1") is MISSING
    assert pointer(["first", "last"], "/01") is MISSING
    assert pointer({"01": "valid-object-key"}, "/01") == "valid-object-key"


def test_numeric_secrets_redact_values_without_corrupting_hash_references():
    redactor = Redactor([])
    redactor.register(1)
    assert redactor.clean({"runId": "run_a1b", "message": "PIN=1", "body": {"pin": 1}}) == {
        "runId": "run_a1b", "message": "PIN=[REDACTED]", "body": {"pin": "[REDACTED]"},
    }
