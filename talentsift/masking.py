"""Deterministic PII masking. No AI involved.

The LLM only ever sees the output of `mask_text`. Masking runs in a fixed order:

1. image content (data URIs, <img> tags, base64 blobs) -> [IMAGE]
2. emails -> [EMAIL]
3. URLs and social links/handles -> [URL]
4. phone numbers -> [PHONE]
5. street addresses, "City, ST 12345", ZIP codes -> [ADDRESS]
6. the name: found in the header block, then every later occurrence of its words -> [NAME]
7. graduation years, only when MASK_GRAD_YEARS is on -> [YEAR]

Masking errs on the side of hiding too much: a common word that is also the applicant's name is
masked wherever it appears capitalized.

The "header block" is the top of the resume, up to the first section heading (at most
`HEADER_LINES` lines). Contact details live there, so the rules are stricter inside it: any five-digit
number is treated as a ZIP code and street addresses are matched in any letter case.

If no name can be found, `name_was_masked` is False for the result. The screening engine then keeps the
resume away from the model and sends it to a person instead (see `scoring.FLAG_NAME_NOT_MASKED`).
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

    @property
    def name_was_masked(self) -> bool:
        return name_was_masked(self.masked_text)


def name_was_masked(masked_text: str) -> bool:
    """True if masking found and replaced the applicant's name. False means the name may still be there."""
    return NAME in masked_text


# --- 1. Image content ------------------------------------------------------------------------------
_IMAGE_PATTERNS = [
    re.compile(r"data:image/[\w.+-]+;base64,[A-Za-z0-9+/=\s]+"),
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

# --- 3. URLs, social links, handles ----------------------------------------------------------------
_URL_BODY = r"[^\s<>()\"'|,;]+"
_URL_PATTERNS = [
    re.compile(rf"\bhttps?://{_URL_BODY}", re.IGNORECASE),
    re.compile(rf"\bwww\.{_URL_BODY}", re.IGNORECASE),
    re.compile(
        r"\b(?:linkedin|github|gitlab|twitter|x|facebook|instagram|medium|behance|dribbble|"
        rf"stackoverflow|kaggle|tiktok|youtube)\.com/{_URL_BODY}",
        re.IGNORECASE,
    ),
    # Lowercase bare domains such as "janedoe.dev". Case-sensitive on purpose: "ASP.NET" is a skill.
    re.compile(
        r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|org|net|io|dev|me|co|ai|app|info|site|xyz|tech|page|blog)"
        rf"(?:\.[a-z]{{2}})?(?:/{_URL_BODY})?(?!\w)"
    ),
]
_HANDLE = re.compile(r"(?<![\w@])@[A-Za-z0-9_]{2,30}\b")  # social handles like @jane_doe
_SOCIAL_LINE = re.compile(r"\b(twitter|instagram|github|gitlab|linkedin|tiktok|handle|social|telegram|x\.com)\b", re.I)
# Technology names that look like web domains. Never masked as URLs.
_TECH_DOMAINS = {
    "asp.net", "ado.net", "vb.net", "ml.net", "socket.io", "fast.ai", "draw.io", "java.net", "three.js",
    "node.js", "vue.js", "next.js", "nuxt.js", "d3.js", "express.js", "chart.js", "p5.js", "monday.com",
    "salesforce.com", "booking.com", "amazon.com",
}

# --- 4. Phone numbers ------------------------------------------------------------------------------
_PHONE_PATTERNS = [
    # North American numbers: (312) 555-0142, 312-555-0142, 312.555.0142, +1 312 555 0142, ext. 12
    re.compile(
        r"(?<![\w+])(?:\+?1[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]?\d{4}"
        r"(?:\s*(?:x|ext\.?)\s*\d{1,5})?(?!\d)"
    ),
    # International numbers with a leading +: +44 20 7946 0958
    re.compile(r"(?<![\w+])\+\d{1,3}(?:[\s.-]?\(?\d{1,4}\)?){2,5}(?!\d)"),
]

# --- 5. Addresses ----------------------------------------------------------------------------------
_STREET_SUFFIX = (
    r"(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Court|Ct|Way|Place|Pl|Terrace|"
    r"Ter|Circle|Cir|Parkway|Pkwy|Highway|Hwy|Square|Sq|Trail|Trl|Plaza|Plz|Loop|Alley|Row|Crescent|"
    r"Cres|Pike|Turnpike|Tpke|Walk|Path|Run|Commons|Crossing|Xing)"
)
_HOUSE_NUMBER = r"\d{1,6}(?:-\d{1,4})?[A-Za-z]?"
_UNIT = r"(?:,?[ \t]*(?:Apt|Apartment|Suite|Ste|Unit|Floor|Fl|Rm|Room|#)\.?[ \t]*#?[ \t]*[\w-]+)?"
_STATE_CODES = (
    "AL|AK|AZ|AR|CA|CO|CT|DE|DC|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|"
    "NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|PR"
)
_STATE_NAMES = (
    "Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|District of Columbia|Florida|"
    "Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|"
    "Michigan|Minnesota|Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|"
    "New York|North Carolina|North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|"
    "South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|West Virginia|Wisconsin|Wyoming|Puerto Rico"
)
_ZIP = r"\d{5}(?:-\d{4})?"
# City words stay on one line ([ \t], never \s), so the employer on the line above is not swallowed.
_CITY = r"[A-Z][A-Za-z.'-]*(?:[ \t]+[A-Z][A-Za-z.'-]*){0,3}"

# Matched anywhere in the resume. The street rules need a street name between the number and the
# suffix, so "15 Court filings", "3 Way merge", and "2016 St. Xavier's College" are left alone.
_ADDRESS_PATTERNS = [
    # 123 Example Street, Apt 4B  (Title Case)
    re.compile(rf"\b{_HOUSE_NUMBER}[ \t]+(?:[A-Z0-9][\w'.-]*[ \t]+){{1,4}}{_STREET_SUFFIX}\b\.?{_UNIT}"),
    # 123 EXAMPLE STREET  (ALL CAPS)
    re.compile(rf"\b{_HOUSE_NUMBER}[ \t]+(?:[A-Z0-9][A-Z0-9'.-]*[ \t]+){{1,4}}(?-i:{_STREET_SUFFIX.upper()})\b\.?{_UNIT}"),
    re.compile(rf"\b{_HOUSE_NUMBER}[ \t]+(?:[NSEW]\.?[ \t]+)?Broadway\b{_UNIT}"),
    re.compile(r"\b(?:P\.?\s?O\.?|Post\s+Office)\s+Box\s+\d+\b", re.IGNORECASE),
    # Anytown, OH 43004 / Anytown, Ohio 43004 / Anytown, OH, 43004 (the state in any letter case)
    re.compile(rf"\b(?:{_CITY}|[A-Z][A-Z.'-]*(?:[ \t]+[A-Z][A-Z.'-]*){{0,3}}),[ \t]*(?i:{_STATE_CODES}|{_STATE_NAMES})[ \t]*,?[ \t]+{_ZIP}\b"),
    # Anytown OH 43004 / Anytown OH, 43004
    re.compile(rf"\b{_CITY}[ \t]+(?:{_STATE_CODES})[ \t]*,?[ \t]+{_ZIP}\b"),
    # OH 43004 / Ohio 43004: only real state codes, so "EN 50128" and "BS 25999" are standards, not ZIPs
    re.compile(rf"\b(?:{_STATE_CODES})[ \t]*,?[ \t]+{_ZIP}\b"),
    re.compile(rf"\b(?i:{_STATE_NAMES})[ \t]*,?[ \t]+{_ZIP}\b"),
    re.compile(r"\b\d{5}-\d{4}\b"),  # ZIP+4 on its own
    re.compile(r"\bzip(?:[ \t]*code)?[ \t]*[:#]?[ \t]*\d{5}\b", re.IGNORECASE),
]
# Matched only in the header block, where contact details live.
_HEADER_ADDRESS_PATTERNS = [
    # any letter case: "123 main street", "221B Baker Street"
    re.compile(rf"\b{_HOUSE_NUMBER}[ \t]+(?:[\w'.-]+[ \t]+){{1,4}}{_STREET_SUFFIX}\b\.?{_UNIT}", re.IGNORECASE),
    re.compile(r"\b(?:Apt|Apartment|Suite|Ste|Unit)\.?[ \t]*#?[ \t]*\d[\w-]*\b", re.IGNORECASE),
    re.compile(r"\b[A-Z]\d[A-Z][ \t]?\d[A-Z]\d\b"),  # Canadian postal code
    re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?[ \t]\d[A-Z]{2}\b"),  # UK postcode
    re.compile(r"(?<![\d$#.,-])\b\d{5}\b(?![\d.,-]*\d)"),  # a bare five-digit number in the header is a ZIP code
]
_ADJACENT_ADDRESSES = re.compile(r"\[ADDRESS\](?:[ \t,]*\n?[ \t,]*\[ADDRESS\])+")
HEADER_LINES = 8

# --- 6. Names --------------------------------------------------------------------------------------
_HEADER_LINES = {"resume", "résumé", "curriculum vitae", "cv", "contact", "contact information", "contact details"}
_SECTION_WORDS = {
    "summary", "profile", "objective", "experience", "education", "skills", "contact", "about",
    "work experience", "professional summary", "professional experience",
}  # fmt: skip
_NOT_NAME_TOKENS = {
    "mr", "mrs", "ms", "mx", "dr", "prof", "jr", "sr", "ii", "iii", "iv",
    "mba", "phd", "cpa", "pmp", "md", "jd", "rn", "msc", "bsc", "ba", "bs", "ma",
}  # fmt: skip
# Words that show a line is a job title or a heading, not a person's name.
_TITLE_WORDS = {
    "resume", "résumé", "cv", "curriculum", "vitae", "of", "for", "and", "the", "at", "in", "with", "page",
    "engineer", "developer", "manager", "analyst", "designer", "consultant", "specialist", "coordinator",
    "director", "officer", "assistant", "associate", "administrator", "architect", "scientist", "technician",
    "representative", "supervisor", "lead", "senior", "junior", "principal", "intern", "student", "teacher",
    "nurse", "accountant", "recruiter", "writer", "editor", "executive", "president", "founder", "owner",
    "software", "data", "business", "product", "project", "program", "marketing", "sales", "operations",
    "finance", "financial", "customer", "service", "human", "resources", "professional", "experienced",
    "summary", "profile", "objective", "experience", "education", "skills", "contact", "references",
}  # fmt: skip
_MONTHS = {
    "jan", "january", "feb", "february", "mar", "march", "apr", "april", "may", "jun", "june", "jul", "july",
    "aug", "august", "sep", "sept", "september", "oct", "october", "nov", "november", "dec", "december",
}  # fmt: skip
_NAME_WORD = re.compile(r"^[^\W\d_](?:[^\W\d_]|['’.-])*$")
_FIRST_LINE_SPLIT = re.compile(r"\s*[|•·;:\t]\s*|\s{2,}|\s[-–—]\s")
_TITLE_PREFIX = re.compile(r"^\s*(?:resume|résumé|cv|curriculum\s+vitae)\s*(?:of|for)?\s*[-–—:]?\s*", re.IGNORECASE)
_NAME_LABEL = re.compile(r"^name\s*[:\-]\s*", re.IGNORECASE)
_PAGE_LINE = re.compile(r"^\s*page\s+\d+(\s+of\s+\d+)?\s*$", re.IGNORECASE)
_PARENTHETICAL = re.compile(r"\([^)]*\)|\[[^\]]*\]|\"[^\"]*\"|“[^”]*”")
_CONTACT_TOKENS = re.compile(
    r"\[(?:EMAIL|PHONE|URL|ADDRESS|IMAGE)\]|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|"
    r"(?:\+?\d[\d\s().-]{7,}\d)|\bhttps?://\S+|\bwww\.\S+"
)

# --- 7. Graduation years ---------------------------------------------------------------------------
_EDUCATION_HEADER = re.compile(r"^\s*(education|academic background|academics|education and training)\s*:?\s*$", re.I)
_OTHER_HEADER = re.compile(
    r"^\s*(experience|work experience|professional experience|employment|employment history|skills|"
    r"technical skills|projects|certifications|summary|professional summary|profile|objective|awards|"
    r"volunteer|volunteering|activities|interests|languages|references|publications|leadership)\s*:?\s*$",
    re.I,
)
_DEGREE_HINT = re.compile(
    r"\b(B\.?S\.?|B\.?A\.?|M\.?S\.?|M\.?A\.?|MBA|Ph\.?D\.?|Bachelor|Master|Associate|Diploma|Doctorate|"
    r"University|College|High School|Class of|Graduated|Graduation|GPA)\b",
    re.I,
)
_YEAR = re.compile(r"\b(?:19[5-9]\d|20[0-4]\d)\b")


def _sub_keep_trailing_punctuation(pattern: re.Pattern, replacement: str, text: str) -> tuple[str, int]:
    """Replace matches but keep sentence punctuation that the match swallowed ("site.com." -> "[URL].").

    Technology names that look like domains ("socket.io", "fast.ai") are left alone.
    """
    count = 0

    def _replace(match: re.Match) -> str:
        nonlocal count
        value = match.group(0)
        stripped = value.rstrip(".,;:!?")
        if stripped.lower() in _TECH_DOMAINS:
            return value
        count += 1
        return replacement + value[len(stripped):]

    return pattern.sub(_replace, text), count


def _mask_handles(text: str) -> tuple[str, int]:
    """Mask @handles in the header block and on lines about social accounts.

    Elsewhere "@" usually means "at": "Software Engineer @Google" and "@Autowired" stay readable.
    """
    _, header_end = header_block(text)
    total = 0
    position = 0
    lines = []
    for line in text.splitlines(keepends=True):
        if position < header_end or _SOCIAL_LINE.search(line):
            line, count = _HANDLE.subn(URL, line)
            total += count
        position += len(line)
        lines.append(line)
    return "".join(lines), total


def _is_capitalized(word: str) -> bool:
    return word[:1].isupper()


def _name_from_line(line: str) -> list[str]:
    """The name words on one header line, or [] if the line does not look like a name."""
    line = _TITLE_PREFIX.sub("", line.strip())
    line = _NAME_LABEL.sub("", line)
    line = re.sub(r" {2,}", " ", _PARENTHETICAL.sub("", line)) if _PARENTHETICAL.search(line) else line  # (she/her), "JD"
    line = _CONTACT_TOKENS.sub(" | ", line)  # a phone or email on the same line ends the name
    segments = [segment.strip(" ,") for segment in _FIRST_LINE_SPLIT.split(line)]
    segment = next((segment for segment in segments if segment), "")
    if not segment or segment.lower() in _SECTION_WORDS:
        return []

    # "Doe, Jane" -> Jane Doe. Any other comma ends the name ("Jane Doe, PMP").
    parts = [part.strip() for part in segment.split(",") if part.strip()]
    if len(parts) == 2 and all(1 <= len(part.split()) <= 2 for part in parts) and len(parts[0].split()) == 1:
        candidate = parts[1].split() + parts[0].split()
        if all(_NAME_WORD.match(w) and _is_capitalized(w) and w.lower() not in _TITLE_WORDS | _NOT_NAME_TOKENS for w in candidate):
            return candidate
    words = (parts[0] if parts else segment).split()

    # Keep the leading run of name-like words: "Jane Doe Senior Software Engineer" -> Jane Doe.
    name: list[str] = []
    for word in words:
        if not _NAME_WORD.match(word) or not _is_capitalized(word) or word.lower().strip(".") in _TITLE_WORDS:
            break
        name.append(word)
    return name if 2 <= len(name) <= 5 else []


def header_block(text: str) -> tuple[int, int]:
    """Character span of the header block: the top of the resume up to the first section heading."""
    position = 0
    seen = 0
    for raw_line in text.splitlines(keepends=True):
        line = raw_line.strip()
        if line:
            if seen and (line.lower().strip(" :") in _SECTION_WORDS or _OTHER_HEADER.match(line) or _EDUCATION_HEADER.match(line)):
                break
            seen += 1
        position += len(raw_line)
        if seen >= HEADER_LINES:
            break
    return 0, position


def find_name_words(text: str) -> list[str]:
    """Return the words of the applicant's name, searching the header block line by line.

    The name is usually the first line, but a title line ("Resume"), a contact line, or a job title can
    come first, so each header line is tried in turn. Returns [] when no line looks like a name.
    """
    _, end = header_block(text)
    for raw_line in text[:end].splitlines():
        line = raw_line.strip()
        if not line or line.lower().strip(" :") in _HEADER_LINES or _PAGE_LINE.match(line):
            continue
        words = _name_from_line(line)
        if words:
            return words
    return []


def _token_pattern(token: str) -> str:
    """Regex for a name word that tolerates straight and curly apostrophes."""
    return re.escape(token).replace("'", "['’]").replace("’", "['’]")


def _mask_names(text: str, name_words: list[str]) -> tuple[str, int]:
    if not name_words:
        return text, 0
    total = 0
    # Full name first, so "Jane Q. Doe" becomes one [NAME] instead of three.
    full_name = r"\s+".join(_token_pattern(word) for word in name_words)
    text, count = re.subn(rf"(?<!\w){full_name}(?!\w)", NAME, text, flags=re.IGNORECASE)
    total += count

    # Then every later occurrence of each name word, in any capitalized form ("MCDONALD" in the header
    # also hides "McDonald" later). Initials and titles are excluded, hyphenated names are split
    # ("Watson-Parker" also hides "Parker"), and lowercase forms are left alone: "grant writing" is
    # not the applicant "Grant".
    tokens: dict[str, bool] = {}  # token -> was it written in ALL CAPS in the header
    for word in name_words:
        whole = word.strip(".")
        for token in {whole, *re.split(r"-", whole)}:
            if len(token) >= 2 and token.lower() not in _NOT_NAME_TOKENS:
                tokens[token] = tokens.get(token, False) or (token.isupper() and len(token) > 1)

    def replace(match: re.Match, token: str, header_caps: bool) -> str:
        found = match.group(0)
        if not found[0].isupper():
            return found
        # A short name that is also an acronym ("Ai" / "AI", "Lan" / "LAN"): keep the acronym.
        if found.isupper() and len(found) <= 3 and not header_caps:
            return found
        # A name that is also a month ("May 2021 - Present"): keep it when a date follows.
        if found.lower() in _MONTHS and re.match(r"\s*,?\s*\d", match.string[match.end():]):
            return found
        nonlocal total
        total += 1
        return NAME

    for token in sorted(tokens, key=len, reverse=True):
        pattern = re.compile(rf"(?<![\w\[]){_token_pattern(token)}(?![\w\]])", re.IGNORECASE)
        text = pattern.sub(lambda m, t=token: replace(m, t, tokens[t]), text)

    # The name run together in a handle or web address: "JaneDoe.com", "in/jane-doe-1234", "janedoe".
    plain = [re.sub(r"[^A-Za-z]", "", word) for word in name_words]
    plain = [word for word in plain if len(word) >= 2 and word.lower() not in _NOT_NAME_TOKENS]
    if len(plain) >= 2 and len(plain[0]) + len(plain[-1]) >= 6:
        joined = rf"{re.escape(plain[0])}[-_.]?{re.escape(plain[-1])}"
        text, count = re.subn(rf"(?<![A-Za-z]){joined}(?![A-Za-z])", NAME, text, flags=re.IGNORECASE)
        total += count
    return text, total


def _mask_grad_years(text: str) -> tuple[str, int]:
    lines = text.split("\n")
    in_education = False
    total = 0
    for index, line in enumerate(lines):
        if _EDUCATION_HEADER.match(line):
            in_education = True
            continue
        if _OTHER_HEADER.match(line):
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
    _, header_end = header_block(masked)
    header, body = masked[:header_end], masked[header_end:]
    for pattern in _HEADER_ADDRESS_PATTERNS:
        header, count = pattern.subn(ADDRESS, header)
        total += count
    masked = _ADJACENT_ADDRESSES.sub(ADDRESS, header + body)
    counts["addresses"] = total

    masked, counts["names"] = _mask_names(masked, name_words)

    counts["grad_years"] = 0
    if mask_grad_years:
        masked, counts["grad_years"] = _mask_grad_years(masked)

    if contains_image_content(masked):  # guard: image data must never reach the model
        raise MaskingError("Image content remained after masking; refusing to pass it to the model.")
    return MaskResult(masked_text=masked, counts=counts)
