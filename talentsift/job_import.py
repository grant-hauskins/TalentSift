"""Import a job posting from a web page.

Job pages come in every shape, so extraction tries the most reliable source first and falls back:

1. **Structured data.** Most job boards and applicant tracking systems (Greenhouse, Lever, Workable,
   LinkedIn, Indeed, many career sites) embed a schema.org `JobPosting` in JSON-LD for search engines.
   When present it is the cleanest copy of the posting.
2. **A named container.** An element whose id or class says it holds the posting
   (`job-description`, `posting`, `jobDescriptionText`, ...), or the page's `<main>` / `<article>`.
3. **Text density.** The block with the most paragraph and list text after navigation, headers, footers,
   forms, and scripts are removed (a small "readability" heuristic).

The result is plain text with headings and bullets kept, ready for the Roles page. Pages that build the
posting with JavaScript after loading have nothing to extract; the error says to paste the text instead.

Only public http(s) addresses are fetched: private, loopback, and link-local hosts are refused, including
after redirects, so a hosted copy of the app cannot be used to probe its own network.
"""

from __future__ import annotations

import html as html_lib
import ipaddress
import json
import re
import socket
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, NavigableString, Tag

MAX_BYTES = 3_000_000
TIMEOUT_SECONDS = 15.0
MAX_REDIRECTS = 5
MIN_POSTING_CHARS = 200  # less than this is almost certainly not the posting
MAX_POSTING_CHARS = 30_000
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36 TalentSift/0.2"
)

# Elements that never hold the posting body.
NOISE_TAGS = ("script", "style", "noscript", "template", "svg", "iframe", "form", "button", "nav", "header",
              "footer", "aside", "select", "input", "dialog")
NOISE_HINT = re.compile(
    r"cookie|consent|banner|navbar|nav-|menu|breadcrumb|footer|header|sidebar|social|share|newsletter|"
    r"modal|popup|related|similar|recommend|apply-form|signup|login",
    re.I,
)
POSTING_HINT = re.compile(
    r"job[-_ ]?desc|jobdescription|description[-_ ]?(text|body|content)|posting|job[-_ ]?details|"
    r"job[-_ ]?body|job[-_ ]?content|vacancy|position[-_ ]?desc|careers?[-_ ]?content",
    re.I,
)
BLOCK_TAGS = {"p", "div", "section", "article", "main", "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6",
              "table", "tr", "br", "hr", "blockquote", "pre", "dl", "dt", "dd"}
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
SKIP_IN_TEXT = {"script", "style", "noscript", "template", "svg"}


class JobImportError(Exception):
    """The page could not be fetched, or no posting could be found on it. The message is user-facing."""


@dataclass
class ImportedPosting:
    url: str
    title: str
    text: str
    method: str  # "structured data", "page section", or "best text block"
    company: str = ""
    location: str = ""
    notes: list[str] = field(default_factory=list)


# --- Fetching ----------------------------------------------------------------------------------------


def normalize_url(url: str) -> str:
    url = url.strip()
    if not url:
        raise JobImportError("Enter the link to the job posting.")
    if "://" not in url:
        url = f"https://{url}"
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise JobImportError("Use a web link that starts with http:// or https://.")
    return url


def _default_resolver(host: str) -> list[str]:
    return [info[4][0] for info in socket.getaddrinfo(host, None)]


def check_public_host(url: str, resolve: Callable[[str], list[str]] = _default_resolver) -> None:
    """Refuse hosts that resolve to private, loopback, link-local, or reserved addresses."""
    host = urlparse(url).hostname or ""
    try:
        addresses = resolve(host)
    except OSError as exc:
        raise JobImportError(f"Could not find the website '{host}'. Check the link.") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%")[0])
        if not ip.is_global:
            raise JobImportError(f"'{host}' is a private or local address. Only public web pages can be imported.")


def fetch_html(
    url: str,
    *,
    client: httpx.Client | None = None,
    resolve: Callable[[str], list[str]] = _default_resolver,
) -> tuple[str, str]:
    """Download a page. Returns (final URL, HTML). Redirects are followed by hand so each hop is checked."""
    url = normalize_url(url)
    own_client = client is None
    client = client or httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=False)
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
               "Accept-Language": "en-US,en;q=0.8"}
    try:
        for _ in range(MAX_REDIRECTS + 1):
            check_public_host(url, resolve)
            with client.stream("GET", url, headers=headers) as response:
                if response.is_redirect and response.headers.get("location"):
                    url = normalize_url(urljoin(url, response.headers["location"]))
                    continue
                if response.status_code in (401, 403, 429):
                    raise JobImportError(
                        f"The site refused the request (HTTP {response.status_code}). Some job boards block "
                        "automated access; open the page in your browser and paste the posting instead."
                    )
                if response.status_code >= 400:
                    raise JobImportError(f"The page returned HTTP {response.status_code}. Check the link.")
                content_type = response.headers.get("content-type", "").lower()
                if content_type and "html" not in content_type and "xml" not in content_type:
                    raise JobImportError(
                        f"That link is not a web page ({content_type.split(';')[0]}). Link to the posting itself."
                    )
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        raise JobImportError("The page is too large to import. Paste the posting instead.")
                encoding = response.encoding or "utf-8"
                return url, bytes(body).decode(encoding, errors="replace")
        raise JobImportError("The link redirected too many times.")
    except httpx.TimeoutException as exc:
        raise JobImportError("The page took too long to respond. Try again or paste the posting.") from exc
    except httpx.HTTPError as exc:
        raise JobImportError(f"Could not load the page: {exc.__class__.__name__}.") from exc
    finally:
        if own_client:
            client.close()


def import_posting(
    url: str,
    *,
    client: httpx.Client | None = None,
    resolve: Callable[[str], list[str]] = _default_resolver,
) -> ImportedPosting:
    """Fetch a job page and extract the posting."""
    final_url, page = fetch_html(url, client=client, resolve=resolve)
    return extract_posting(page, url=final_url)


# --- Extraction --------------------------------------------------------------------------------------


def extract_posting(page: str, *, url: str = "") -> ImportedPosting:
    """Pull the title and posting text out of an HTML page."""
    soup = BeautifulSoup(page, "html.parser")
    structured = _from_json_ld(soup)
    if structured is not None:
        title, text, company, location = structured
        if len(text) >= MIN_POSTING_CHARS:
            return _finish(url, title or _page_title(soup), text, "structured data", company, location)

    title = _page_title(soup)
    _strip_noise(soup)
    for method, node in (("page section", _named_container(soup)), ("best text block", _densest_block(soup))):
        if node is None:
            continue
        text = html_to_text(node)
        if len(text) >= MIN_POSTING_CHARS:
            return _finish(url, title, text, method)

    raise JobImportError(
        "Could not find a job description on that page. It may load its content with JavaScript or require a "
        "login. Open it in your browser, copy the posting, and paste it into the box below."
    )


def _finish(url: str, title: str, text: str, method: str, company: str = "", location: str = "") -> ImportedPosting:
    notes = []
    if len(text) > MAX_POSTING_CHARS:
        text = text[:MAX_POSTING_CHARS].rsplit("\n", 1)[0]
        notes.append(f"The posting was very long and was cut to {MAX_POSTING_CHARS:,} characters.")
    header = []
    if title and not text.lstrip().lower().startswith(title.lower()):
        header.append(title)
    header += [f"Company: {company}"] if company else []
    header += [f"Location: {location}"] if location else []
    if header:
        text = "\n".join(header) + "\n\n" + text
    return ImportedPosting(url=url, title=title, text=text.strip(), method=method,
                           company=company, location=location, notes=notes)


def _iter_json_ld(soup: BeautifulSoup) -> Iterable[dict[str, Any]]:
    for script in soup.find_all("script", type=re.compile(r"ld\+json", re.I)):
        raw = script.string or script.get_text() or ""
        try:
            data = json.loads(raw.strip().rstrip(";"))
        except (json.JSONDecodeError, ValueError):
            continue
        stack = [data]
        while stack:
            item = stack.pop()
            if isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, dict):
                yield item
                stack.extend(v for k, v in item.items() if k in ("@graph", "mainEntity", "itemListElement", "item"))


def _is_job_posting(item: dict[str, Any]) -> bool:
    kind = item.get("@type")
    kinds = kind if isinstance(kind, list) else [kind]
    return any(isinstance(k, str) and k.lower() == "jobposting" for k in kinds)


def _from_json_ld(soup: BeautifulSoup) -> tuple[str, str, str, str] | None:
    for item in _iter_json_ld(soup):
        if not _is_job_posting(item):
            continue
        description = item.get("description") or ""
        if not isinstance(description, str):
            continue
        # Descriptions are HTML, sometimes HTML-escaped twice.
        unescaped = html_lib.unescape(description)
        if "<" not in description and "<" in unescaped:
            description = unescaped
        text = html_to_text(BeautifulSoup(description, "html.parser"))
        extras = []
        for label, key in (("Qualifications", "qualifications"), ("Responsibilities", "responsibilities"),
                           ("Skills", "skills"), ("Education", "educationRequirements"),
                           ("Experience", "experienceRequirements")):
            value = _as_text(item.get(key))
            if value and value[:60] not in text:
                extras.append(f"{label}\n{value}")
        if extras:
            text = text + "\n\n" + "\n\n".join(extras)
        title = _as_text(item.get("title")).strip()
        company = _as_text(item.get("hiringOrganization"))
        return title, text, company.strip(), _location(item.get("jobLocation"), item.get("jobLocationType"))
    return None


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        value = html_lib.unescape(value)
        return html_to_text(BeautifulSoup(value, "html.parser")) if "<" in value else value.strip()
    if isinstance(value, list):
        return "\n".join(f"- {_as_text(v)}" for v in value if _as_text(v))
    if isinstance(value, dict):
        return _as_text(value.get("name") or value.get("description") or value.get("credentialCategory"))
    return str(value)


def _location(value: Any, location_type: Any) -> str:
    places = value if isinstance(value, list) else [value]
    parts = []
    for place in places:
        if not isinstance(place, dict):
            continue
        address = place.get("address") or {}
        if isinstance(address, str):
            parts.append(address)
            continue
        city = ", ".join(x for x in (address.get("addressLocality"), address.get("addressRegion")) if isinstance(x, str) and x)
        country = address.get("addressCountry")
        if isinstance(country, dict):
            country = country.get("name")
        line = ", ".join(x for x in (city, country if isinstance(country, str) else "") if x)
        if line:
            parts.append(line)
    if isinstance(location_type, str) and "telecommute" in location_type.lower():
        parts.append("Remote")
    return "; ".join(dict.fromkeys(parts))


def _page_title(soup: BeautifulSoup) -> str:
    for attrs in ({"property": "og:title"}, {"name": "twitter:title"}):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            return tag["content"].strip()
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        return " ".join(h1.get_text(" ", strip=True).split())
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    return ""


def _attr_text(tag: Tag) -> str:
    if tag.attrs is None:
        return ""
    classes = tag.get("class") or []
    return " ".join([tag.get("id") or "", *classes, tag.get("role") or "", tag.get("data-testid") or ""])


def _strip_noise(soup: BeautifulSoup) -> None:
    for tag in soup.find_all(NOISE_TAGS):
        tag.decompose()
    for tag in soup.find_all(True):
        if tag.decomposed or tag.attrs is None:
            continue
        hints = _attr_text(tag)
        if hints and NOISE_HINT.search(hints) and not POSTING_HINT.search(hints) and tag.name not in ("body", "html", "main"):
            # Only drop small containers: a mislabeled wrapper around the whole page must survive.
            if len(tag.get_text(" ", strip=True)) < 2000:
                tag.decompose()


def _named_container(soup: BeautifulSoup) -> Tag | None:
    candidates = [tag for tag in soup.find_all(True) if POSTING_HINT.search(_attr_text(tag))]
    candidates = [tag for tag in candidates if len(tag.get_text(" ", strip=True)) >= MIN_POSTING_CHARS]
    if candidates:
        # The outermost match keeps sibling sections (requirements, benefits) that sit beside the description.
        return max(candidates, key=lambda tag: len(tag.get_text(" ", strip=True)))
    for name in ("main", "article"):
        tags = soup.find_all(name)
        if tags:
            best = max(tags, key=lambda tag: len(tag.get_text(" ", strip=True)))
            if len(best.get_text(" ", strip=True)) >= MIN_POSTING_CHARS:
                return best
    return None


def _densest_block(soup: BeautifulSoup) -> Tag | None:
    """Score each container by the paragraph and list text directly inside it, then pick the best one."""
    scores: dict[int, float] = {}
    nodes: dict[int, Tag] = {}
    for leaf in soup.find_all(["p", "li", "h2", "h3", "h4", "pre", "td"]):
        text = leaf.get_text(" ", strip=True)
        if len(text) < 20:
            continue
        link_text = sum(len(a.get_text(" ", strip=True)) for a in leaf.find_all("a"))
        weight = len(text) * (1 - min(link_text / max(len(text), 1), 1))
        parent = leaf.parent
        for depth, ancestor in enumerate((parent, parent.parent if parent else None)):
            if isinstance(ancestor, Tag) and ancestor.name not in ("html", "[document]"):
                nodes[id(ancestor)] = ancestor
                scores[id(ancestor)] = scores.get(id(ancestor), 0) + weight / (1 + depth)
    if not scores:
        body = soup.body or soup
        return body if isinstance(body, Tag) else None
    return nodes[max(scores, key=scores.get)]


def html_to_text(node: Tag | BeautifulSoup) -> str:
    """Readable text: headings on their own lines, bullets as '- ', paragraphs separated by a blank line."""
    lines: list[str] = []
    current: list[str] = []

    def flush() -> None:
        line = " ".join("".join(current).split())
        current.clear()
        if line:
            lines.append(line)

    def walk(element: Tag) -> None:
        for child in element.children:
            if isinstance(child, NavigableString):
                if child.__class__.__name__ in ("Comment", "Doctype", "Declaration", "ProcessingInstruction"):
                    continue
                current.append(str(child))
                continue
            if not isinstance(child, Tag) or child.name in SKIP_IN_TEXT:
                continue
            name = child.name
            if name == "br":
                flush()
                continue
            if name in BLOCK_TAGS:
                flush()
                if name in HEADINGS or name in ("ul", "ol", "table", "section", "article"):
                    lines.append("")
                if name == "li":
                    current.append("- ")
                walk(child)
                flush()
                if name in ("p", "ul", "ol", "table", "section", "article", "blockquote") or name in HEADINGS:
                    lines.append("")
            else:
                walk(child)

    walk(node)
    flush()
    text = "\n".join(lines)
    text = re.sub(r"(?m)^-\s*$\n?", "", text)  # empty bullets
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
