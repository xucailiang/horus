import json

import pytest
from pydantic import ValidationError

from horus.adapters.hurl import Response
from horus.core.models import Assertion, LoadStep
from horus.core.store import HorusError
from tests.support import FIXTURES


@pytest.mark.parametrize(
    "pattern,value,passed",
    json.loads((FIXTURES / "regex-cases.json").read_bytes()),
)
async def test_regex_assertion_uses_ecmascript_unicode_string_matching(pattern, value, passed):
    assertion = Assertion(
        id="format",
        jsonPointer="/value",
        operator="regex",
        expected=pattern,
        evidenceRefs=["contract"],
    )
    checks = await Response(200, {}, {"value": value}, 1).check([assertion])
    assert checks[0]["status"] == ("passed" if passed else "failed")


@pytest.mark.parametrize("pattern", ["(", "(?P<name>a)", 42, None, "x" * 513])
def test_invalid_regex_is_rejected_before_review(pattern):
    with pytest.raises(ValidationError, match="Regex|regular expression"):
        Assertion(
            id="format",
            jsonPointer="/value",
            operator="regex",
            expected=pattern,
            evidenceRefs=["contract"],
        )


async def test_regex_missing_value_fails_and_headers_can_be_matched():
    assertions = [
        Assertion(
            id="missing",
            jsonPointer="/missing",
            operator="regex",
            expected=".*",
            evidenceRefs=["contract"],
        ),
        Assertion(
            id="header",
            header="Content-Type",
            operator="regex",
            expected="^application/",
            evidenceRefs=["contract"],
        ),
    ]
    checks = await Response(200, {"content-type": "application/json"}, {}, 1).check(assertions)
    assert [check["status"] for check in checks] == ["failed", "passed"]
    assert checks[0]["missing"]


async def test_regex_timeout_keeps_the_worker_responsive():
    assertion = Assertion(
        id="bounded",
        jsonPointer="/value",
        operator="regex",
        expected="(a+)+$",
        evidenceRefs=["contract"],
    )
    with pytest.raises(HorusError, match="regex_timeout"):
        await Response(200, {}, {"value": "a" * 10000 + "!"}, 1).check([assertion])


@pytest.mark.parametrize(
    "pattern,value",
    [
        (r"^\p{Script=Han}+$", "中文"),
        (r"\p{Emoji}", "😀"),
        (r"\p{Script_Extensions=Greek}+", "α"),
        (r"\P{ASCII}", "é"),
    ],
)
async def test_functional_unicode_properties_are_rejected_for_load_before_review(pattern, value):
    assertion = Assertion(
        id="property",
        jsonPointer="/value",
        operator="regex",
        expected=pattern,
        evidenceRefs=["contract"],
    )
    checks = await Response(200, {}, {"value": value}, 1).check([assertion])
    assert checks[0]["status"] == "passed"
    with pytest.raises(ValidationError, match="k6 does not support Unicode property escapes"):
        LoadStep(
            kind="load",
            id="read",
            operationId="read",
            request={},
            assertions=[assertion],
            loadProfile={"vus": 1, "iterations": 1, "maxDurationMs": 1000},
        )


def test_load_regex_allows_literal_text_resembling_a_unicode_property():
    step = LoadStep.model_validate(
        {
            "kind": "load",
            "id": "read",
            "operationId": "read",
            "request": {},
            "assertions": [
                {
                    "id": "literal",
                    "jsonPointer": "/value",
                    "operator": "regex",
                    "expected": r"\\p\{Script=Han\}",
                    "evidenceRefs": ["contract"],
                }
            ],
            "loadProfile": {"vus": 1, "iterations": 1, "maxDurationMs": 1000},
        }
    )
    assert step.assertions[0].operator == "regex"
