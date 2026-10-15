import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from jsonschema import Draft4Validator, Draft202012Validator
from openapi_schema_validator import OAS30ReadValidator, OAS31Validator

from horus.adapters.process import MAX_OUTPUT, run_engine
from horus.core.models import Assertion, Request
from horus.core.store import HorusError

MISSING = object()


def pointer(value: Any, path: str) -> Any:
    if path == "":
        return value
    if not path.startswith("/"):
        raise HorusError("invalid_json_pointer", path)
    try:
        for part in path[1:].split("/"):
            key = part.replace("~1", "/").replace("~0", "~")
            if isinstance(value, list) and not re.fullmatch(r"0|[1-9][0-9]*", key):
                return MISSING
            value = value[int(key)] if isinstance(value, list) else value[key]
        return value
    except (ValueError, IndexError, KeyError, TypeError):
        return MISSING


def build_url(operation: dict[str, Any], base_url: str, request: Request) -> str:
    path = operation["path"]
    if not path.startswith("/") or any(p in {".", ".."} for p in path.split("/")):
        raise HorusError("invalid_operation_path")
    for name in re.findall(r"\{([^}]+)\}", path):
        if name not in request.pathParams:
            raise HorusError("missing_path_parameter", name)
        path = path.replace("{" + name + "}", quote(str(request.pathParams[name]), safe=""))
    path = quote(path, safe="/%:@!$&'()*+,;=-._~")
    query = urlencode(request.query, doseq=True)
    return base_url.rstrip("/") + path + ("?" + query if query else "")


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: Any
    elapsed_ms: float

    def check(self, assertions: list[Assertion]) -> list[dict[str, Any]]:
        results = []
        for assertion in assertions:
            actual: Any = None
            expected: Any = assertion.expected
            if assertion.status is not None:
                actual = self.status
                expected = assertion.status
                passed = actual in expected if isinstance(expected, list) else actual == expected
            elif assertion.jsonSchema is not None:
                validators = {
                    "2020-12": Draft202012Validator,
                    "draft4": Draft4Validator,
                    "oas30": OAS30ReadValidator,
                    "oas31": OAS31Validator,
                }
                validator = validators[assertion.dialect or "2020-12"]
                actual = self.body
                passed = validator(assertion.jsonSchema).is_valid(actual)
                expected = assertion.jsonSchema
            else:
                if assertion.jsonPointer is not None:
                    actual = pointer(self.body, assertion.jsonPointer)
                elif assertion.header is not None:
                    actual = self.headers.get(assertion.header.lower(), MISSING)
                elif assertion.elapsedMs:
                    actual = self.elapsed_ms
                passed = compare(actual, assertion.operator, expected)
            results.append(
                {
                    "id": assertion.id,
                    "status": "inconclusive"
                    if assertion.tentative
                    else ("passed" if passed else "failed"),
                    "actual": None if actual is MISSING else actual,
                    "missing": actual is MISSING,
                    "expected": expected,
                    "evidenceRefs": assertion.evidenceRefs,
                }
            )
        return results


def equal(actual: Any, expected: Any) -> bool:
    if type(actual) in (int, float) and type(expected) in (int, float):
        return bool(actual == expected)
    if type(actual) is not type(expected):
        return False
    if isinstance(actual, dict):
        return actual.keys() == expected.keys() and all(
            equal(value, expected[key]) for key, value in actual.items()
        )
    if isinstance(actual, list):
        return len(actual) == len(expected) and all(
            equal(a, b) for a, b in zip(actual, expected, strict=True)
        )
    return bool(actual == expected)


def compare(actual: Any, operator: str, expected: Any) -> bool:
    if operator == "exists":
        return (actual is not MISSING) == (expected if isinstance(expected, bool) else True)
    if actual is MISSING:
        return False
    if operator == "eq":
        return equal(actual, expected)
    if operator == "ne":
        return not equal(actual, expected)
    if operator == "type":
        types = {
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
            "array": list,
            "object": dict,
            "null": type(None),
        }
        if expected == "number":
            return type(actual) in (int, float)
        if expected == "integer":
            return type(actual) in (int, float) and actual % 1 == 0
        return type(actual) is types.get(expected)
    if operator == "length":
        return isinstance(actual, (str, list, dict)) and len(actual) == expected
    if operator in {"gte", "lte"}:
        if type(actual) not in (int, float) or type(expected) not in (int, float):
            return False
        return bool(actual >= expected if operator == "gte" else actual <= expected)
    raise HorusError("unsupported_assertion", operator)


def validate_headers(headers: dict[str, str]) -> None:
    for name, value in headers.items():
        if (
            not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name)
            or name.lower() in {"host", "content-length", "proxy-authorization"}
            or any(c in value for c in ["\r", "\n", "{{", "}}", "\x00"])
        ):
            raise HorusError("invalid_header", name)


class HurlAdapter:
    async def request(
        self, operation: dict[str, Any], base_url: str, request: Request, *, timeout: float = 15
    ) -> Response:
        url = build_url(operation, base_url, request)
        headers = dict(request.headers)
        if request.body is not None:
            headers.setdefault("Content-Type", "application/json")
        validate_headers(headers)
        with tempfile.TemporaryDirectory(prefix="horus-private-") as temporary:
            root = Path(temporary).resolve()
            path = root / "request.hurl"
            content = f"{operation['method']} {url}\n"
            content += "".join(f"{k}: {v}\n" for k, v in headers.items())
            if request.body is not None:
                body_path = root / "body.json"
                body_path.write_text(json.dumps(request.body, ensure_ascii=False))
                body_path.chmod(0o600)
                content += "\nfile,body.json;\n"
            path.write_text(content)
            path.chmod(0o600)
            code, _, _ = await run_engine(
                "hurl",
                [
                    "--json",
                    "--report-json",
                    str(root / "report"),
                    "--connect-timeout",
                    "5",
                    "--max-time",
                    str(timeout),
                    "--max-filesize",
                    str(MAX_OUTPUT),
                    "--retry",
                    "0",
                    str(path),
                ],
                timeout=timeout + 2,
            )
            if code:
                raise HorusError("hurl_request_error", f"exit {code}; write outcome may be unknown")
            try:
                report = json.loads((root / "report" / "report.json").read_bytes())
                call = report[0]["entries"][0]["calls"][-1]
                response = call["response"]
                body_file = (root / "report" / response["body"]).resolve()
                if not body_file.is_relative_to(root / "report"):
                    raise HorusError("invalid_engine_response_path")
                if body_file.stat().st_size > MAX_OUTPUT:
                    raise HorusError("response_truncated")
                raw = body_file.read_bytes()
                try:
                    body = json.loads(raw) if raw else None
                except ValueError:
                    body = raw.decode(errors="replace")
                return Response(
                    status=response["status"],
                    body=body,
                    headers={h["name"].lower(): h["value"] for h in response["headers"]},
                    elapsed_ms=call["timings"]["total"] / 1000,
                )
            except (OSError, ValueError, IndexError, KeyError, TypeError) as exc:
                raise HorusError("unsupported_hurl_result") from exc
