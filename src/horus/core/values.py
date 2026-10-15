import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from horus.core.models import Capture, IdentityProfile
from horus.core.service import VARIABLE
from horus.core.store import HorusError


@dataclass
class CaseContext:
    run_id: str
    secrets: dict[str, str]
    profiles: dict[str, IdentityProfile] = field(default_factory=dict)
    shared: dict[str, Any] = field(default_factory=dict)
    private: dict[str | None, dict[str, Any]] = field(default_factory=dict)

    def view(self, identity: str | None) -> dict[str, dict[str, Any]]:
        if identity is not None and identity not in self.profiles:
            raise HorusError("unknown_identity", identity)
        try:
            secrets = (
                {
                    alias: self.secrets[binding]
                    for alias, binding in self.profiles[identity].credentialRefs.items()
                }
                if identity is not None
                else self.secrets
            )
        except KeyError as exc:
            raise HorusError("missing_credential", str(exc)) from exc
        return {
            "vars": {**self.shared, **self.private.get(identity, {})},
            "secrets": secrets,
            "run": {"id": self.run_id},
        }

    def capture(self, identity: str | None, name: str, capture: Capture, value: Any) -> None:
        target = self.private.setdefault(identity, {}) if capture.private else self.shared
        target[name] = value


def resolve(value: Any, context: dict[str, dict[str, Any]]) -> Any:
    if isinstance(value, dict):
        return {key: resolve(child, context) for key, child in value.items()}
    if isinstance(value, list):
        return [resolve(child, context) for child in value]
    if not isinstance(value, str):
        return value

    def lookup(match: re.Match[str]) -> Any:
        namespace, name = match.groups()
        if name not in context.get(namespace, {}):
            raise HorusError("missing_variable", f"{namespace}.{name}")
        return context[namespace][name]

    full = VARIABLE.fullmatch(value)
    if full:
        return lookup(full)
    return VARIABLE.sub(lambda match: str(lookup(match)), value).replace("$${", "${")


class Redactor:
    def __init__(self, values: list[str]) -> None:
        self.values = set(filter(None, values))
        self.numeric_values: set[str] = set()
        self.protected: list[Any] = []

    def register(self, value: Any) -> None:
        """Keep captured credentials in memory; redact their containers and scalar values."""
        if isinstance(value, str):
            if value:
                self.values.add(value)
            return
        self.protected.append(value)
        if isinstance(value, dict):
            for child in value.values():
                self.register(child)
        elif isinstance(value, list):
            for child in value:
                self.register(child)
        elif value is not None:
            self.numeric_values.add(json.dumps(value))

    def clean(self, value: Any) -> Any:
        if any(type(value) is type(secret) and value == secret for secret in self.protected):
            return "[REDACTED]"
        if isinstance(value, dict):
            return {
                key: (
                    "[REDACTED]"
                    if re.search(r"authorization|password|token$|secret|cookie", key, re.I)
                    else self.clean(child)
                )
                for key, child in value.items()
            }
        if isinstance(value, list):
            return [self.clean(child) for child in value]
        if isinstance(value, str):
            for secret in self.numeric_values:
                pattern = r"(?<![\w.])" + re.escape(secret) + r"(?![\w.])"
                value = re.sub(pattern, "[REDACTED]", value)
            for secret in sorted(self.values, key=len, reverse=True):
                for variant in {secret, quote(secret, safe=""), json.dumps(secret)[1:-1]}:
                    value = value.replace(variant, "[REDACTED]")
        return value
