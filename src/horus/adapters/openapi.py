import re
import time
from html.parser import HTMLParser
from urllib.error import URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

import yaml

from horus.core.store import HorusError

MAX_DOCUMENT = 2 * 1024 * 1024


def http_url(url: str) -> str:
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or any(c.isspace() for c in url)
    ):
        raise HorusError("invalid_document_url", "Use an HTTP/HTTPS document URL")
    return url


class Redirects(HTTPRedirectHandler):
    max_redirections = 3

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        http_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Scripts(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.initializers: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            source = dict(attrs).get("src", "") or ""
            if "swagger-initializer" in source:
                self.initializers.append(source)


def load_api(url: str) -> tuple[str, str]:
    """Resolve a direct spec or common Swagger UI/config URL without evaluating JavaScript."""
    opener = build_opener(ProxyHandler({}), Redirects())
    visited: set[str] = set()
    deadline = time.monotonic() + 20
    for _ in range(6):
        http_url(url)
        if url in visited or time.monotonic() >= deadline:
            raise HorusError("document_resolution_limit")
        visited.add(url)
        try:
            with opener.open(
                Request(url, headers={"User-Agent": "horus"}),
                timeout=min(5, deadline - time.monotonic()),
            ) as response:
                content = response.read(MAX_DOCUMENT + 1)
                url = response.url
            if len(content) > MAX_DOCUMENT:
                raise HorusError("document_too_large", "Maximum document size is 2 MiB")
            text = content.decode("utf-8-sig")
        except (URLError, UnicodeError, TimeoutError) as exc:
            raise HorusError("document_fetch_failed", type(exc).__name__) from exc
        try:
            parsed = yaml.safe_load(text) if not text.lstrip().startswith("<") else None
        except yaml.YAMLError:
            parsed = None
        if isinstance(parsed, dict) and ("openapi" in parsed or "swagger" in parsed):
            return text, url
        candidates: list[str] = []
        if isinstance(parsed, dict):
            candidates.extend(
                parsed[key] for key in ("url", "configUrl") if isinstance(parsed.get(key), str)
            )
            candidates.extend(
                item["url"]
                for item in parsed.get("urls", [])
                if isinstance(item, dict) and isinstance(item.get("url"), str)
            )
        else:
            # Handles SwaggerUIBundle, Spring configUrl and .NET's embedded JSON config.
            normalized = text.replace('\\"', '"').replace("\\'", "'")
            candidates.extend(
                re.findall(r"""["']?(?:url|configUrl)["']?\s*:\s*["']([^"'\s]+)["']""", normalized)
            )
        candidates = list(dict.fromkeys(urljoin(url, candidate) for candidate in candidates))
        if not candidates:
            scripts = Scripts()
            scripts.feed(text)
            candidates = [urljoin(url, source) for source in scripts.initializers]
        if len(candidates) > 1:
            raise HorusError(
                "select_api_document", "Multiple definitions: " + ", ".join(candidates)
            )
        if not candidates:
            raise HorusError(
                "openapi_url_required", "Provide the raw OpenAPI/Swagger definition URL"
            )
        url = candidates[0]
    raise HorusError("document_resolution_limit")
