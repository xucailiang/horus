import pytest
from pydantic import ValidationError

from horus.adapters.hurl import Response
from horus.core.models import Assertion, Capture, DataDependency, ResponseOperand

POINTER_FIELDS = [
    (Assertion, {"id": "value", "evidenceRefs": ["contract"]}, "jsonPointer"),
    (Capture, {"secret": False, "purpose": "case-data"}, "jsonPointer"),
    (ResponseOperand, {"kind": "response"}, "jsonPointer"),
    (DataDependency, {
        "id": "resource", "producer": "case.create", "consumers": ["case.read"],
        "purpose": "resource", "evidenceRefs": ["contract"],
    }, "sourceJsonPointer"),
]


@pytest.mark.parametrize("model,fields,name", POINTER_FIELDS)
def test_jsonpath_and_malformed_pointer_escapes_are_rejected_before_review(model, fields, name):
    for path in ["$.token", "id", "#/id", "/bad~2key", "/dangling~", "\n"]:
        with pytest.raises(ValidationError):
            model.model_validate({**fields, name: path})


@pytest.mark.parametrize("path,body,expected", [
    ("", {"id": 42}, {"id": 42}),
    ("/", {"": 42}, 42),
    ("/a~1b/m~0n", {"a/b": {"m~n": 42}}, 42),
    ("/~01", {"~1": 42}, 42),
    ("/items/0", {"items": [42]}, 42),
    ("/中文\n", {"中文\n": 42}, 42),
])
async def test_valid_pointers_keep_root_empty_keys_escapes_arrays_and_unicode(path, body, expected):
    for model, fields, name in POINTER_FIELDS:
        model.model_validate({**fields, name: path})
    checks = await Response(200, {}, body, 1).check([
        Assertion(id="value", jsonPointer=path, expected=expected, evidenceRefs=["contract"]),
    ])
    assert checks[0]["status"] == "passed"
