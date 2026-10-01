"""Deterministic PII masking. No AI involved.

The LLM only ever sees the output of `mask_text`. Masking runs in a fixed order:

1. image content (data URIs, <img> tags, base64 blobs) -> [IMAGE]
2. emails -> [EMAIL]
3. URLs, personal domains, and social links/handles -> [URL]
4. phone numbers -> [PHONE]
5. street addresses, "City, ST 12345", ZIP codes -> [ADDRESS]
6. the name: found at the top of the resume, then every later occurrence -> [NAME]
7. graduation years, only when MASK_GRAD_YEARS is on -> [YEAR]

Masking errs on the side of hiding too much, but each rule is anchored so job-relevant text survives:
"300 Google Drive accounts" is not an address, "IT 25000 tickets" is not a ZIP code, "ASP.NET" and
"socket.io" are skills, and "@Override" is Java, not a social handle.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

NAME = "[NAME]"
EMAIL = "[EMAIL]"
URL = "[URL]"
PHONE = "[PHONE]"
ADDRESS = "[ADDRESS]"
YEAR = "[YEAR]"
IMAGE = "[IMAGE]"


class MaskingError(RuntimeError):
    """Raised if image content would reach the model after masking."""


@dataclass
class MaskResult:
    masked_text: str
    counts: dict[str, int] = field(default_factory=dict)


# --- 1. Image content ------------------------------------------------------------------------------
_IMAGE_PATTERNS = [
    # A data URI, plus any wrapped continuation lines made only of base64 characters.
    re.compile(r"data:image/[\w.+-]+;base64,[A-Za-z0-9+/=]+(?:\n[A-Za-z0-9+/=]{20,}(?=\n|\Z))*"),
    re.compile(r"<img\b[^>]*>", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{200,}={0,2}"),  # long base64 blob
]

# The guard below checks with its own list, independent of the removal step, as defense in depth.
_IMAGE_REMOVAL_PATTERNS = list(_IMAGE_PATTERNS)


def contains_image_content(text: str) -> bool:
    """True if the text still contains anything that looks like embedded image data."""
    return any(pattern.search(text) for pattern in _IMAGE_PATTERNS)


# --- 2. Emails -------------------------------------------------------------------------------------
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# --- 3. URLs, personal domains, social links and handles ---------------------------------------------
_URL_BODY = r"[^\s<>()\"'|,;]+"
_URL_PATTERNS = [
    re.compile(rf"\bhttps?://{_URL_BODY}", re.IGNORECASE),
    re.compile(rf"\bwww\.{_URL_BODY}", re.IGNORECASE),
    re.compile(
        r"\b(?:linkedin|github|gitlab|twitter|x|facebook|instagram|medium|behance|dribbble|"
        rf"stackoverflow|kaggle|tiktok|youtube)\.com/{_URL_BODY}",
        re.IGNORECASE,
    ),
]
# Bare domains such as "janedoe.dev" or "JaneDoe.com". The ending must be lowercase, so "ASP.NET" is safe.
_BARE_DOMAIN = re.compile(
    r"\b[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.(?:com|org|net|io|dev|me|co|ai|app|info|site|xyz|tech|page|blog|"
    r"design|studio|online|website|works|cloud|link|art|space|digital|codes|us|ca|uk)"
    rf"(?:\.[a-z]{{2}})?(?:/{_URL_BODY})?(?!\w)"
)
# Technology and company names that look like domains but are job-relevant. Extend as needed.
KNOWN_NON_PERSONAL_DOMAINS = {
    "asp.net", "ado.net", "vb.net", "socket.io", "fly.io", "amazon.com", "salesforce.com", "monday.com",
    "booking.com", "hotels.com",
}  # fmt: skip
# Handles count only with a social label ("Twitter: @jane") or in the contact lines at the top.
_LABELED_HANDLE = re.compile(
    r"(?P<label>\b(?:twitter|x|instagram|ig|github|gitlab|tiktok|threads|mastodon|bluesky|telegram|handle)"
    r"\b\s*[:\-]?\s*)@[A-Za-z0-9_.]{2,30}\b",
    re.IGNORECASE,
)
_BARE_HANDLE = re.compile(r"(?<![\w@])@[A-Za-z0-9_]{2,30}\b")
_CONTACT_LINES = 4  # bare handles are only masked on contact-looking lines near the top
_CONTACT_MARKERS = re.compile(r"\[EMAIL\]|\[URL\]|[|•·]|^\s*@\w+\s*$")

# --- 4. Phone numbers ------------------------------------------------------------------------------
_PHONE_PATTERNS = [
    # North American numbers: (312) 555-0142, 312-555-0142, 312.555.0142, +1 312 555 0142, ext. 12
    re.compile(
        r"(?<![\w+])(?:\+?1[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s./-]?\d{3}[\s.-]?\d{4}"
        r"(?:\s*(?:x|ext\.?)\s*\d{1,5})?(?!\d)"
    ),
    # International numbers with a leading +: +44 20 7946 0958
    re.compile(r"(?<!\w)\+\d{1,3}(?:[\s.-]?\(?\d{1,4}\)?){2,5}(?!\d)"),
    # National numbers with a trunk 0: 020 7946 0958, 0161-496-0000
    re.compile(r"(?<![\w+])0\d{2,4}[\s-]\d{3,4}[\s-]\d{3,4}(?!\d)"),
]

# --- 5. Addresses ----------------------------------------------------------------------------------
_STREET_SUFFIX = (
    r"(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Court|Ct|Way|Place|Pl|Terrace|"
    r"Ter|Circle|Cir|Parkway|Pkwy|Highway|Hwy|Square|Sq|Trail|Trl)"
)
_US_STATES = (
    r"(?:AL|AK|AZ|AR|CA|CO|CT|DE|DC|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|"
    r"NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|PR|GU|VI|AS|MP)"
)
_ADDRESS_PATTERNS = [
    # "123 Example Street, Apt 4B": a street address ends the line or is followed by a comma or separator,
    # so "Migrated 300 Google Drive accounts" is left alone.
    re.compile(
        rf"\b\d{{1,6}}\s+(?:[A-Z0-9][\w'.-]*\s+){{0,4}}{_STREET_SUFFIX}\b\.?"
        r"(?:,?\s*(?:Apt|Apartment|Suite|Ste|Unit|#)\.?\s*[\w-]+)?"
        r"(?=[ \t]*(?:,|\||•|·|$))",
        re.MULTILINE,
    ),
    re.compile(r"\bP\.?\s?O\.?\s+Box\s+\d+\b", re.IGNORECASE),
    # "Anytown, OH 43004" (city, state, ZIP) and "OH 43004". Real state codes only: "IT 25000" is not a ZIP.
    re.compile(rf"\b[A-Z][A-Za-z.'-]*(?:\s+[A-Z][A-Za-z.'-]*){{0,3}},\s*{_US_STATES}\s+\d{{5}}(?:-\d{{4}})?\b"),
    re.compile(rf"\b{_US_STATES}\s+\d{{5}}(?:-\d{{4}})?\b"),
    re.compile(r"\b\d{5}-\d{4}\b"),  # ZIP+4 on its own
]
_ADJACENT_ADDRESSES = re.compile(r"\[ADDRESS\](?:[ \t,]*\[ADDRESS\])+")

# --- 6. Names --------------------------------------------------------------------------------------
_TITLE_LINES = {"resume", "résumé", "curriculum vitae", "cv"}
_NAME_PREFIX = re.compile(
    r"^(?:name|resume|résumé|cv|curriculum vitae)\b\s*(?:of\b|for\b|[:\-–—|•·])?\s*", re.IGNORECASE
)
_SECTION_WORDS = {
    "summary", "profile", "objective", "experience", "education", "skills", "contact", "about",
    "work experience", "professional summary", "professional experience",
}  # fmt: skip
_HONORIFICS = {"mr", "mrs", "ms", "mx", "dr", "prof"}
# Words that end a name: credentials, suffixes, and common title or header words ("Jane Doe Data Analyst").
_NAME_STOP_WORDS = {
    "jr", "sr", "ii", "iii", "iv", "mba", "phd", "cpa", "pmp", "md", "jd", "rn", "msc", "bsc", "ba", "bs", "ma",
    "data", "analyst", "analytics", "engineer", "engineering", "manager", "management", "developer",
    "designer", "senior", "junior", "lead", "principal", "consultant", "specialist", "coordinator", "assistant",
    "associate", "director", "intern", "scientist", "architect", "administrator", "officer", "accountant",
    "nurse", "teacher", "technician", "representative", "executive", "supervisor", "operations", "marketing",
    "sales", "software", "business", "product", "project", "program", "finance", "financial", "resume",
    "curriculum", "vitae", "cv", "profile", "summary", "contact", "phone", "email", "mobile", "cell", "address",
    "linkedin", "github", "portfolio", "website", "objective", "experience", "education", "skills", "page",
    "results", "driven", "professional", "experienced", "dedicated", "motivated", "detail", "oriented",
    "highly", "dynamic", "certified", "licensed", "seasoned", "accomplished", "creative", "passionate", "skilled",
}  # fmt: skip
# Lowercase words allowed inside a name ("Lucas van Dijk", "Ana de la Cruz").
_PARTICLES = {"van", "von", "de", "del", "della", "da", "di", "du", "la", "le", "bin", "binti", "al", "dos", "das", "ten", "ter"}
_NAME_WORD = re.compile(r"^[^\W\d_](?:[^\W\d_]|['’.-])*$")
_SEGMENT_SPLIT = re.compile(r"\s*[|•·,;\t]\s*|\s{2,}|\s[-–—]\s")
_NAME_SEARCH_LINES = 5  # contact-only lines above the name are skipped, up to this many lines

# --- 7. Graduation years ---------------------------------------------------------------------------
_EDUCATION_HEADER = re.compile(
    r"^\s*(education|academic background|academics|education (and|&) (training|certifications?))\s*:?\s*$", re.I
)
_OTHER_HEADER = re.compile(
    r"^\s*(experience|work experience|professional experience|relevant experience|work history|career history|"
    r"professional history|employment|employment history|skills|technical skills|core competencies|projects|"
    r"certifications|licenses|summary|professional summary|profile|objective|awards|honors|volunteer|"
    r"volunteering|activities|interests|languages|references|publications|leadership|training|affiliations)"
    r"\s*:?\s*$",
    re.I,
)
_ALL_CAPS_HEADER = re.compile(r"^[A-Z][A-Z &/]{2,40}$")  # e.g. "WORK HISTORY"
_SCHOOL_WORDS = re.compile(r"\b(UNIVERSITY|COLLEGE|SCHOOL|INSTITUTE|ACADEMY)\b")
# Degree wording outside the education section. Bare "Associate" or "Master" would hit job titles
# ("Sales Associate", "Scrum Master"), so only degree forms count.
_DEGREE_HINT = re.compile(
    r"\b(B\.?S\.?|B\.?A\.?|M\.?S\.?|M\.?A\.?|MBA|Ph\.?D\.?|Bachelor(?:'s)?|Master's|Master of|Associate's|"
    r"Associate (?:of|in|degree)|Diploma|Doctorate|High School|Class of|Graduated|Graduation|GPA)\b",
    re.I,
)
_YEAR = re.compile(r"\b(?:19[5-9]\d|20[0-4]\d)\b")


# --- Helpers -----------------------------------------------------------------------------------------


def _sub_keep_trailing_punctuation(pattern: re.Pattern, replacement: str, text: str) -> tuple[str, int]:
    """Replace matches but keep sentence punctuation that the match swallowed ("site.com." -> "[URL].")."""

    def _replace(match: re.Match) -> str:
        value = match.group(0)
        stripped = value.rstrip(".,;:!?")
        return replacement + value[len(stripped):]

    return pattern.subn(_replace, text)


def _mask_bare_domains(text: str) -> tuple[str, int]:
    count = 0

    def _replace(match: re.Match) -> str:
        nonlocal count
        value = match.group(0)
        stripped = value.rstrip(".,;:!?")
        if stripped.split("/")[0].lower() in KNOWN_NON_PERSONAL_DOMAINS:
            return value
        count += 1
        return URL + value[len(stripped):]

    return _BARE_DOMAIN.sub(_replace, text), count


def _mask_handles(text: str) -> tuple[str, int]:
    """Labeled handles anywhere; bare handles only on contact lines near the top ("Jane | @janedoe").

    Elsewhere "@Override" or "@RestController" are code, not people.
    """
    text, labeled = _LABELED_HANDLE.subn(lambda m: m.group("label") + URL, text)
    lines = text.split("\n")
    seen, bare = 0, 0
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        seen += 1
        if seen > _CONTACT_LINES:
            break
        if _CONTACT_MARKERS.search(line) or any(p.search(line) for p in _PHONE_PATTERNS):
            lines[index], count = _BARE_HANDLE.subn(URL, line)
            bare += count
    return "\n".join(lines), labeled + bare


def _strip_contact_details(line: str) -> str:
    """Remove emails, links, and phone numbers so only the words around them remain."""
    for pattern in (_EMAIL, *_URL_PATTERNS, _BARE_DOMAIN, *_PHONE_PATTERNS, _BARE_HANDLE):
        line = pattern.sub(" | ", line)
    return line


def find_name_words(text: str) -> list[str]:
    """Return the words of the applicant's name from the top of the resume.

    The name is the first meaningful line, possibly after a "Resume" title or a contact bar, and possibly
    followed on the same line by contact details or a job title. The leading run of 2-4 name-like words is
    taken. Returns [] if the first meaningful line does not start with a name.
    """
    meaningful = [line.strip() for line in text.splitlines() if line.strip()]
    for line in meaningful[:_NAME_SEARCH_LINES]:
        if line.lower().strip(" :") in _TITLE_LINES:
            continue  # a "Resume" title line
        line = _NAME_PREFIX.sub("", _strip_contact_details(line)).strip()
        segment = _SEGMENT_SPLIT.split(line)[0].strip(" |") if line else ""
        if not segment:
            continue  # a contact-only line above the name: keep looking
        if segment.lower() in _SECTION_WORDS:
            return []
        if len(segment.split()) == 1:
            continue  # a one-word title line such as "Confidential" or "Portfolio"
        words: list[str] = []
        for word in segment.split():
            bare = word.strip(".,").lower()
            if not words and bare in _HONORIFICS:
                continue  # "Dr. Jane Doe"
            if not _NAME_WORD.match(word) or bare in _NAME_STOP_WORDS:
                break
            # Names are capitalized; lowercase words only count as particles inside a name.
            if not word[:1].isupper() and not (words and bare in _PARTICLES):
                break
            words.append(word.rstrip(","))
            if len(words) == 4:
                break
        while words and not words[-1][:1].isupper():
            words.pop()  # a name ends with a capitalized word
        return words if len(words) >= 2 else []
    return []


def _mask_names(text: str, name_words: list[str]) -> tuple[str, int]:
    if not name_words:
        return text, 0
    total = 0
    # Full name first, so "Jane Q. Doe" becomes one [NAME] instead of three.
    full_name = r"\s+".join(re.escape(word) for word in name_words)
    text, count = re.subn(rf"(?<!\w){full_name}(?!\w)", NAME, text, flags=re.IGNORECASE)
    total += count

    # Capitalized name words, skipping initials and lowercase particles ("van", "de"): "drove a van" stays.
    tokens = [w.strip(".") for w in name_words if len(w.strip(".")) >= 2 and w[:1].isupper()]
    tokens = [t for t in tokens if t.lower() not in _NAME_STOP_WORDS and t.lower() not in _HONORIFICS]

    # Joined forms in handles and paths: janedoe, jane.doe, jane-doe, jane_doe, doejane...
    if len(tokens) >= 2:
        first, last = tokens[0], tokens[-1]
        joined = {f"{a}{sep}{b}" for a, b in ((first, last), (last, first)) for sep in ("", ".", "-", "_")}
        alternation = "|".join(re.escape(j) for j in sorted(joined, key=len, reverse=True))
        text, count = re.subn(rf"(?<![A-Za-z0-9])(?:{alternation})(?![A-Za-z0-9])", NAME, text, flags=re.IGNORECASE)
        total += count

    # Then every later occurrence of each name word in capitalized or upper case. Lowercase forms are left
    # alone: "grant writing" is not the applicant "Grant".
    for token in sorted(set(tokens), key=len, reverse=True):
        forms = {token, token.upper(), token.capitalize(), token.title()}
        alternation = "|".join(re.escape(form) for form in sorted(forms, key=len, reverse=True))
        text, count = re.subn(rf"(?<!\w)(?:{alternation})(?!\w)", NAME, text)
        total += count
    return text, total


def _is_other_header(line: str) -> bool:
    stripped = line.strip()
    if _OTHER_HEADER.match(stripped):
        return True
    return bool(_ALL_CAPS_HEADER.match(stripped)) and not _SCHOOL_WORDS.search(stripped)


def _mask_grad_years(text: str) -> tuple[str, int]:
    lines = text.split("\n")
    in_education = False
    total = 0
    for index, line in enumerate(lines):
        if _EDUCATION_HEADER.match(line):
            in_education = True
            continue
        if _is_other_header(line):
            in_education = False
            continue
        if in_education or _DEGREE_HINT.search(line):
            lines[index], count = _YEAR.subn(YEAR, line)
            total += count
    return "\n".join(lines), total


def mask_text(text: str, *, mask_grad_years: bool = False) -> MaskResult:
    """Mask PII in resume text. Same input and flag always give the same output."""
    counts: dict[str, int] = {}
    name_words = find_name_words(text)  # read the name before contact details are replaced

    masked = text
    total = 0
    for pattern in _IMAGE_REMOVAL_PATTERNS:
        masked, count = pattern.subn(IMAGE, masked)
        total += count
    counts["images"] = total

    masked, counts["emails"] = _EMAIL.subn(EMAIL, masked)

    total = 0
    for pattern in _URL_PATTERNS:
        masked, count = _sub_keep_trailing_punctuation(pattern, URL, masked)
        total += count
    masked, count = _mask_bare_domains(masked)
    total += count
    masked, count = _mask_handles(masked)
    counts["urls"] = total + count

    total = 0
    for pattern in _PHONE_PATTERNS:
        masked, count = pattern.subn(PHONE, masked)
        total += count
    counts["phones"] = total

    total = 0
    for pattern in _ADDRESS_PATTERNS:
        masked, count = pattern.subn(ADDRESS, masked)
        total += count
    masked = _ADJACENT_ADDRESSES.sub(ADDRESS, masked)
    counts["addresses"] = total

    masked, counts["names"] = _mask_names(masked, name_words)

    counts["grad_years"] = 0
    if mask_grad_years:
        masked, counts["grad_years"] = _mask_grad_years(masked)

    if contains_image_content(masked):  # guard: image data must never reach the model
        raise MaskingError("Image content remained after masking; refusing to pass it to the model.")
    return MaskResult(masked_text=masked, counts=counts)
