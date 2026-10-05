"""Job posting import: extraction across page formats, URL safety checks, and fetching (all offline)."""

import json

import httpx
import pytest

from talentsift.job_import import (
    JobImportError,
    check_public_host,
    extract_posting,
    html_to_text,
    import_posting,
    normalize_url,
)
from bs4 import BeautifulSoup

BODY = (
    "<p>We are hiring an Operations Coordinator to keep our warehouse running smoothly.</p>"
    "<h3>What you will do</h3><ul><li>Schedule inbound and outbound shipments with carriers</li>"
    "<li>Maintain inventory records in our ERP system</li><li>Coordinate with vendors on delivery issues</li></ul>"
    "<h3>What you bring</h3><ul><li>2+ years of operations or logistics experience</li>"
    "<li>Advanced Excel skills, including pivot tables</li><li>Clear written communication</li></ul>"
)
NAV = "<nav><a href='/'>Home</a><a href='/jobs'>All jobs</a><a href='/about'>About us</a></nav>"
FOOTER = "<footer><p>© 2026 Example Corp. All rights reserved. Privacy policy. Terms of use. Cookie settings.</p></footer>"
COOKIES = "<div class='cookie-banner'><p>We use cookies to improve your experience on our website. Accept all?</p></div>"


def page(body: str, head: str = "") -> str:
    return f"<html><head><title>Careers | Example Corp</title>{head}</head><body>{body}</body></html>"


def json_ld(data) -> str:
    return f'<script type="application/ld+json">{json.dumps(data)}</script>'


JOB_POSTING = {
    "@context": "https://schema.org",
    "@type": "JobPosting",
    "title": "Operations Coordinator",
    "description": BODY,
    "hiringOrganization": {"@type": "Organization", "name": "Example Corp"},
    "jobLocation": {"@type": "Place", "address": {"addressLocality": "Columbus", "addressRegion": "OH", "addressCountry": "US"}},
}


def test_json_ld_job_posting_is_preferred():
    html = page(NAV + "<div id='app'>Loading...</div>" + FOOTER, head=json_ld(JOB_POSTING))
    posting = extract_posting(html, url="https://example.com/jobs/1")
    assert posting.method == "structured data"
    assert posting.title == "Operations Coordinator"
    assert posting.company == "Example Corp" and posting.location == "Columbus, OH, US"
    assert posting.text.startswith("Operations Coordinator\nCompany: Example Corp\nLocation: Columbus, OH, US")
    assert "- Schedule inbound and outbound shipments with carriers" in posting.text
    assert "What you bring" in posting.text
    assert "Home" not in posting.text


def test_json_ld_inside_graph_with_escaped_html():
    data = {"@context": "https://schema.org", "@graph": [
        {"@type": "WebPage", "name": "Jobs"},
        {**JOB_POSTING, "description": BODY.replace("<", "&lt;").replace(">", "&gt;"), "@type": ["JobPosting"]},
    ]}
    posting = extract_posting(page("<main>hi</main>", head=json_ld(data)))
    assert posting.method == "structured data"
    assert "<li>" not in posting.text and "- Advanced Excel skills, including pivot tables" in posting.text


def test_named_container_like_indeed_or_greenhouse():
    html = page(
        NAV + COOKIES
        + "<h1 class='app-title'>Operations Coordinator</h1>"
        + "<div class='sidebar'><p>Similar jobs: Warehouse Lead, Shipping Clerk, Buyer, Planner, Analyst</p></div>"
        + f"<div id='jobDescriptionText'>{BODY}</div>"
        + FOOTER
    )
    posting = extract_posting(html)
    assert posting.method == "page section"
    assert posting.title == "Operations Coordinator"
    assert "Maintain inventory records" in posting.text
    assert "cookies" not in posting.text and "Similar jobs" not in posting.text and "Privacy" not in posting.text


def test_main_element_fallback():
    posting = extract_posting(page(NAV + f"<main><h1>Ops Coordinator</h1>{BODY}</main>" + FOOTER))
    assert posting.method == "page section"
    assert "Ops Coordinator" in posting.text and "Clear written communication" in posting.text


def test_densest_block_fallback_for_unlabeled_pages():
    links = "".join(f"<p><a href='/{i}'>Another open role number {i} at the company</a></p>" for i in range(8))
    html = page(NAV + f"<div class='x1'>{links}</div><div class='x2'><div class='x3'>{BODY}</div></div>" + FOOTER)
    posting = extract_posting(html)
    assert posting.method == "best text block"
    assert "Coordinate with vendors" in posting.text
    assert "Another open role" not in posting.text


def test_og_title_wins_over_page_title():
    html = page(f"<article>{BODY}</article>", head='<meta property="og:title" content="Operations Coordinator - Example Corp">')
    assert extract_posting(html).title == "Operations Coordinator - Example Corp"


def test_javascript_only_page_gives_a_clear_error():
    with pytest.raises(JobImportError, match="JavaScript"):
        extract_posting(page("<div id='root'></div><script>window.app = {}</script>"))


def test_html_to_text_keeps_structure():
    text = html_to_text(BeautifulSoup(BODY, "html.parser"))
    assert "What you will do\n\n- Schedule inbound" in text
    assert "\n\n\n" not in text


def test_very_long_posting_is_trimmed():
    huge = "<article>" + "<p>" + ("Responsible for many important duties. " * 40) + "</p>" * 1 + "</article>"
    huge = huge.replace("</article>", "".join("<p>" + "Another duty line here. " * 40 + "</p>" for _ in range(60)) + "</article>")
    posting = extract_posting(page(huge))
    assert len(posting.text) <= 30_000 + 200 and posting.notes


@pytest.mark.parametrize("url", ["", "ftp://example.com/job", "https://"])
def test_bad_urls_are_rejected(url):
    with pytest.raises(JobImportError):
        normalize_url(url)


def test_scheme_is_added_when_missing():
    assert normalize_url("example.com/jobs/1") == "https://example.com/jobs/1"


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.5", "192.168.1.10", "169.254.169.254", "::1"])
def test_private_hosts_are_refused(address):
    with pytest.raises(JobImportError, match="private or local"):
        check_public_host("http://internal.example/", resolve=lambda host: [address])


PUBLIC = lambda host: ["93.184.216.34"]  # noqa: E731


def client_for(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def test_import_posting_fetches_and_follows_redirects():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "/jobs/42"})
        assert "TalentSift" in request.headers["user-agent"]
        return httpx.Response(200, html=page("", head=json_ld(JOB_POSTING)))

    posting = import_posting("https://jobs.example.com/old", client=client_for(handler), resolve=PUBLIC)
    assert posting.url == "https://jobs.example.com/jobs/42"
    assert posting.title == "Operations Coordinator"


def test_redirect_to_a_private_address_is_refused():
    def handler(request):
        return httpx.Response(302, headers={"location": "http://metadata.internal/latest"})

    resolve = lambda host: ["169.254.169.254"] if host == "metadata.internal" else ["93.184.216.34"]  # noqa: E731
    with pytest.raises(JobImportError, match="private or local"):
        import_posting("https://jobs.example.com/x", client=client_for(handler), resolve=resolve)


@pytest.mark.parametrize(
    "response, message",
    [
        (httpx.Response(403), "refused"),
        (httpx.Response(404), "HTTP 404"),
        (httpx.Response(200, content=b"%PDF-1.7", headers={"content-type": "application/pdf"}), "not a web page"),
    ],
)
def test_fetch_errors_are_user_friendly(response, message):
    with pytest.raises(JobImportError, match=message):
        import_posting("https://jobs.example.com/x", client=client_for(lambda r: response), resolve=PUBLIC)


def test_network_failure_is_user_friendly():
    def handler(request):
        raise httpx.ConnectTimeout("slow")

    with pytest.raises(JobImportError, match="too long"):
        import_posting("https://jobs.example.com/x", client=client_for(handler), resolve=PUBLIC)
