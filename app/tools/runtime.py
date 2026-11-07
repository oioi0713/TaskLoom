"""Bounded, read-only tool implementations used by Worker Agent loops."""

from __future__ import annotations

import html
import ipaddress
import json
import re
import socket
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urlparse, urlunparse
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, ValidationError


class WebSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        min_length=1,
        max_length=500,
        description="Focused search query used only to discover an unknown page URL.",
    )
    max_results: int = Field(
        default=5,
        ge=1,
        le=5,
        description="Maximum candidate pages to return; request only what is needed.",
    )


class WebPageReaderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: HttpUrl = Field(description="Exact public page URL to read.")
    max_chars: int = Field(
        default=12000,
        ge=500,
        le=20000,
        description="Maximum page characters returned in this observation.",
    )


class KnowledgeSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        min_length=1,
        max_length=300,
        description=(
            "Specific symbol or keyword query for locating unknown files or call sites; "
            "do not use when the exact target path is already known."
        ),
    )
    max_results: int = Field(
        default=5,
        ge=1,
        le=10,
        description="Maximum matching path/excerpt pairs to return.",
    )


class DocumentReaderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(
        min_length=1,
        max_length=1000,
        description="Exact workspace-relative path of the text file to read.",
    )
    max_chars: int = Field(
        default=12000,
        ge=500,
        le=20000,
        description=(
            "Maximum characters returned from the start of the file. Repeating the "
            "same path and value returns the same content; pagination is not supported."
        ),
    )


TOOL_INPUT_SCHEMAS: Mapping[str, type[BaseModel]] = {
    "web_search": WebSearchInput,
    "web_page_reader": WebPageReaderInput,
    "knowledge_search": KnowledgeSearchInput,
    "document_reader": DocumentReaderInput,
}


@dataclass(frozen=True, slots=True)
class ToolExecution:
    """One normalized tool result returned to the authorization wrapper."""

    success: bool
    observation: dict[str, Any]
    error: str | None
    execution_time_ms: int


class _SearchResultParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._href: str | None = None
        self._title_parts: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag != "a":
            return
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        if "result__a" in classes:
            self._href = attributes.get("href")
            self._title_parts = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._title_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self._href is None:
            return
        url = _decode_search_redirect(self._href)
        title = " ".join("".join(self._title_parts).split())
        if url and title:
            self.results.append({"title": html.unescape(title), "url": url})
        self._href = None
        self._title_parts = []


class _BingSearchResultParser(HTMLParser):
    """Extract ordinary result titles and links from Bing's HTML page."""

    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._result_depth = 0
        self._in_heading = False
        self._href: str | None = None
        self._title_parts: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attributes = dict(attrs)
        if tag == "li":
            classes = (attributes.get("class") or "").split()
            if self._result_depth:
                self._result_depth += 1
            elif "b_algo" in classes:
                self._result_depth = 1
        if not self._result_depth:
            return
        if tag == "h2":
            self._in_heading = True
        elif tag == "a" and self._in_heading and self._href is None:
            self._href = attributes.get("href")
            self._title_parts = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._title_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            title = " ".join("".join(self._title_parts).split())
            if self._href.startswith(("http://", "https://")) and title:
                self.results.append(
                    {"title": html.unescape(title), "url": html.unescape(self._href)}
                )
            self._href = None
            self._title_parts = []
        elif tag == "h2":
            self._in_heading = False
        elif tag == "li" and self._result_depth:
            self._result_depth -= 1


class _ReadableHTMLParser(HTMLParser):
    _SKIPPED = {"script", "style", "noscript", "svg"}

    def __init__(self) -> None:
        super().__init__()
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag in self._SKIPPED:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIPPED and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title_parts.append(data)
        self.text_parts.append(data)


def _decode_search_redirect(href: str) -> str:
    parsed = urlparse(html.unescape(href))
    target = parse_qs(parsed.query).get("uddg", [None])[0]
    return unquote(target) if target else href


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _source(
    *,
    source_id: str,
    title: str,
    excerpt: str,
    url: str | None = None,
    document_id: str | None = None,
    source_type: str,
) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "title": title,
        "url": url,
        "document_id": document_id,
        "published_at": None,
        "accessed_at": _utc_now(),
        "excerpt": excerpt,
        "source_type": source_type,
    }


def _assert_public_http_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only public http/https URLs are allowed.")
    addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443)
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise ValueError("Private, loopback, link-local, and reserved URLs are blocked.")


def _fetch(url: str, *, timeout_seconds: float, max_bytes: int) -> tuple[str, str]:
    _assert_public_http_url(url)
    request = Request(
        url,
        headers={
            "User-Agent": "TaskLoom/0.8 (+read-only research tool)",
            "Accept": "text/html, text/plain, application/json;q=0.8",
            "Accept-Language": "en-US,en;q=0.8",
        },
    )
    with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
        content_type = response.headers.get_content_type()
        charset = response.headers.get_content_charset() or "utf-8"
        payload = response.read(max_bytes + 1)
    if len(payload) > max_bytes:
        payload = payload[:max_bytes]
    return payload.decode(charset, errors="replace"), content_type


class ToolRuntime:
    """Validate and execute an allow-listed set of read-only tools."""

    def __init__(
        self,
        *,
        workspace_root: Path,
        handlers: Mapping[str, Callable[[BaseModel], dict[str, Any]]] | None = None,
        request_timeout_seconds: float = 15.0,
        max_calls_per_tool: int = 3,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.request_timeout_seconds = request_timeout_seconds
        self.max_calls_per_tool = max_calls_per_tool
        self._handlers: dict[str, Callable[[BaseModel], dict[str, Any]]] = {
            "web_search": self._web_search,
            "web_page_reader": self._web_page_reader,
            "knowledge_search": self._knowledge_search,
            "document_reader": self._document_reader,
        }
        if handlers:
            self._handlers.update(handlers)

    @property
    def executable_tools(self) -> frozenset[str]:
        return frozenset(self._handlers)

    def tool_schema(self, name: str, description: str) -> dict[str, Any]:
        try:
            schema = TOOL_INPUT_SCHEMAS[name].model_json_schema()
        except KeyError as exc:
            raise ValueError(f"Tool has no executable input schema: {name}") from exc
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": schema,
            },
        }

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolExecution:
        started = time.perf_counter()
        try:
            schema = TOOL_INPUT_SCHEMAS[name]
            handler = self._handlers[name]
            validated = schema.model_validate(arguments)
            observation = handler(validated)
            return ToolExecution(
                success=True,
                observation=observation,
                error=None,
                execution_time_ms=max(0, int((time.perf_counter() - started) * 1000)),
            )
        except (KeyError, ValidationError, ValueError, OSError) as exc:
            return ToolExecution(
                success=False,
                observation={"error": str(exc), "recoverable": True},
                error=f"{type(exc).__name__}: {exc}",
                execution_time_ms=max(0, int((time.perf_counter() - started) * 1000)),
            )
        except Exception as exc:  # Tool failures are observations, not graph crashes.
            return ToolExecution(
                success=False,
                observation={"error": str(exc), "recoverable": False},
                error=f"{type(exc).__name__}: {exc}",
                execution_time_ms=max(0, int((time.perf_counter() - started) * 1000)),
            )

    def _web_search(self, value: BaseModel) -> dict[str, Any]:
        request = WebSearchInput.model_validate(value)
        url = "https://html.duckduckgo.com/html/?q=" + quote_plus(request.query)
        body, _ = _fetch(
            url,
            timeout_seconds=self.request_timeout_seconds,
            max_bytes=500_000,
        )
        parser = _SearchResultParser()
        parser.feed(body)
        results = parser.results
        provider = "duckduckgo"
        if not results:
            fallback_url = (
                "https://www.bing.com/search?q="
                + quote_plus(request.query)
                + "&setlang=en-US&cc=US"
            )
            fallback_body, _ = _fetch(
                fallback_url,
                timeout_seconds=self.request_timeout_seconds,
                max_bytes=500_000,
            )
            fallback_parser = _BingSearchResultParser()
            fallback_parser.feed(fallback_body)
            results = fallback_parser.results
            provider = "bing"
        results = results[: request.max_results]
        if not results:
            raise ValueError("All search providers returned no parseable results.")
        return {
            "query": request.query,
            "provider": provider,
            "results": [
                {
                    **item,
                    "source": _source(
                        source_id=f"search-{index}",
                        title=item["title"],
                        url=item["url"],
                        excerpt="Search result; open the page before relying on it.",
                        source_type="web_page",
                    ),
                }
                for index, item in enumerate(results, start=1)
            ],
            "untrusted": True,
        }

    def _web_page_reader(self, value: BaseModel) -> dict[str, Any]:
        request = WebPageReaderInput.model_validate(value)
        url = str(request.url)
        body, content_type = _fetch(
            url,
            timeout_seconds=self.request_timeout_seconds,
            max_bytes=max(request.max_chars * 4, 50_000),
        )
        if content_type == "text/html":
            parser = _ReadableHTMLParser()
            parser.feed(body)
            title = " ".join("".join(parser.title_parts).split()) or url
            text = "\n".join(
                part for part in (" ".join(item.split()) for item in parser.text_parts) if part
            )
            parsed_url = urlparse(url)
            if (
                len(text) < 500
                and parsed_url.hostname == "docs.langchain.com"
                and not Path(parsed_url.path).suffix
            ):
                markdown_url = urlunparse(
                    parsed_url._replace(path=parsed_url.path.rstrip("/") + ".md")
                )
                try:
                    markdown_body, _ = _fetch(
                        markdown_url,
                        timeout_seconds=self.request_timeout_seconds,
                        max_bytes=max(request.max_chars * 4, 50_000),
                    )
                    if len(markdown_body) > len(text):
                        text = markdown_body
                except OSError:
                    pass
        else:
            title = url
            text = body
        text = text[: request.max_chars]
        source_type = "official_doc" if "docs." in (urlparse(url).hostname or "") else "web_page"
        return {
            "title": title,
            "content": text,
            "source": _source(
                source_id="page-1",
                title=title,
                url=url,
                excerpt=text[:500],
                source_type=source_type,
            ),
            "untrusted": True,
        }

    def _knowledge_search(self, value: BaseModel) -> dict[str, Any]:
        request = KnowledgeSearchInput.model_validate(value)
        query_terms = [term.casefold() for term in re.findall(r"\w+", request.query)]
        if not query_terms:
            raise ValueError("Knowledge query contains no searchable terms.")
        matches: list[dict[str, Any]] = []
        allowed_suffixes = {".md", ".txt", ".json", ".jsonl", ".py"}
        for index, path in enumerate(self.workspace_root.rglob("*")):
            if index >= 2000 or len(matches) >= request.max_results:
                break
            if not path.is_file() or path.suffix.lower() not in allowed_suffixes:
                continue
            if any(part in {".git", ".venv", "__pycache__"} for part in path.parts):
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")[:200_000]
            except OSError:
                continue
            folded = content.casefold()
            if not all(term in folded for term in query_terms):
                continue
            first = min(folded.find(term) for term in query_terms)
            excerpt = content[max(0, first - 250) : first + 750]
            relative = path.relative_to(self.workspace_root).as_posix()
            matches.append(
                {
                    "path": relative,
                    "excerpt": excerpt,
                    "source": _source(
                        source_id=f"knowledge-{len(matches) + 1}",
                        title=path.name,
                        document_id=relative,
                        excerpt=excerpt[:500],
                        source_type="local_document",
                    ),
                }
            )
        return {"query": request.query, "matches": matches, "untrusted": True}

    def _document_reader(self, value: BaseModel) -> dict[str, Any]:
        request = DocumentReaderInput.model_validate(value)
        requested = Path(request.path)
        path = (
            requested.resolve()
            if requested.is_absolute()
            else (self.workspace_root / requested).resolve()
        )
        if not path.is_relative_to(self.workspace_root):
            raise ValueError("Document path is outside the approved workspace root.")
        if not path.is_file():
            raise ValueError("Document does not exist or is not a file.")
        if path.suffix.lower() not in {".md", ".txt", ".json", ".jsonl", ".py"}:
            raise ValueError("Only approved UTF-8 text document types are supported.")
        content = path.read_text(encoding="utf-8", errors="replace")[: request.max_chars]
        relative = path.relative_to(self.workspace_root).as_posix()
        return {
            "path": relative,
            "content": content,
            "source": _source(
                source_id="document-1",
                title=path.name,
                document_id=relative,
                excerpt=content[:500],
                source_type="local_document",
            ),
            "untrusted": True,
        }


def create_default_tool_runtime(workspace_root: Path | None = None) -> ToolRuntime:
    return ToolRuntime(workspace_root=workspace_root or Path.cwd())


EXECUTABLE_TOOL_NAMES: tuple[str, ...] = tuple(TOOL_INPUT_SCHEMAS)
