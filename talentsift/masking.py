"""Deterministic PII masking. No AI involved.

The LLM only ever sees the output of `mask_text`. Masking runs in a fixed order:

1. image content (data URIs, <img> tags, base64 blobs) -> [IMAGE]
2. emails -> [EMAIL]
3. URLs and social links/handles -> [URL]
4. phone numbers -> [PHONE]
5. street addresses, "City, ST 12345", ZIP codes -> [ADDRESS]
6. the name: taken from the first line, then every later occurrence of those words -> [NAME]
7. graduation years, only when MASK_GRAD_YEARS is on -> [YEAR]

Masking errs on the side of hiding too much: a common word that is also the applicant's name is
masked wherever it appears capitalized.
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
    re.compile(r"(?<![\w@])@[A-Za-z0-9_]{2,30}\b"),  # social handles like @jane_doe
]

# --- 4. Phone numbers ------------------------------------------------------------------------------
_PHONE_PATTERNS = [
    # North American numbers: (312) 555-0142, 312-555-0142, 312.555.0142, +1 312 555 0142, ext. 12
    re.compile(
        r"(?<![\w+])(?:\+?1[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]?\d{4}"
        r"(?:\s*(?:x|ext\.?)\s*\d{1,5})?(?!\d)"
    ),
    # International numbers with a leading +: +44 20 7946 0958
    re.compile(r"(?<!\w)\+\d{1,3}(?:[\s.-]?\(?\d{1,4}\)?){2,5}(?!\d)"),
]

# --- 5. Addresses ----------------------------------------------------------------------------------
_STREET_SUFFIX = (
    r"(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Court|Ct|Way|Place|Pl|Terrace|"
    r"Ter|Circle|Cir|Parkway|Pkwy|Highway|Hwy|Square|Sq|Trail|Trl)"
)
_ADDRESS_PATTERNS = [
    # 123 Example Street, Apt 4B
    re.compile(
        rf"\b\d{{1,6}}\s+(?:[A-Z0-9][\w'.-]*\s+){{0,4}}{_STREET_SUFFIX}\b\.?"
        r"(?:,?\s*(?:Apt|Apartment|Suite|Ste|Unit|#)\.?\s*[\w-]+)?"
    ),
    re.compile(r"\bP\.?\s?O\.?\s+Box\s+\d+\b", re.IGNORECASE),
    # Anytown, OH 43004 (city, state, ZIP)
    re.compile(r"\b[A-Z][A-Za-z.'-]*(?:\s+[A-Z][A-Za-z.'-]*){0,3},\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?\b"),
    re.compile(r"\b[A-Z]{2}\s+\d{5}(?:-\d{4})?\b"),  # OH 43004
    re.compile(r"\b\d{5}-\d{4}\b"),  # ZIP+4 on its own
]
_ADJACENT_ADDRESSES = re.compile(r"\[ADDRESS\](?:[ \t,]*\[ADDRESS\])+")

# --- 6. Names --------------------------------------------------------------------------------------
_HEADER_LINES = {"resume", "résumé", "curriculum vitae", "cv"}
_SECTION_WORDS = {
    "summary", "profile", "objective", "experience", "education", "skills", "contact", "about",
    "work experience", "professional summary", "professional experience",
}  # fmt: skip
_NOT_NAME_TOKENS = {
    "mr", "mrs", "ms", "mx", "dr", "prof", "jr", "sr", "ii", "iii", "iv",
    "mba", "phd", "cpa", "pmp", "md", "jd", "rn", "msc", "bsc", "ba", "bs", "ma",
}  # fmt: skip
_NAME_WORD = re.compile(r"^[^\W\d_](?:[^\W\d_]|['’.-])*$")
_FIRST_LINE_SPLIT = re.compile(r"\s*[|•·,;\t]\s*|\s{2,}|\s[-–—]\s")

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
    """Replace matches but keep sentence punctuation that the match swallowed ("site.com." -> "[URL].")."""

    def _replace(match: re.Match) -> str:
        value = match.group(0)
        stripped = value.rstrip(".,;:!?")
        return replacement + value[len(stripped):]

    return pattern.subn(_replace, text)


def find_name_words(text: str) -> list[str]:
    """Return the words of the applicant's name using the first-line heuristic.

    The first meaningful line of a resume is almost always the name, sometimes followed by contact
    details separated by "|", "," or similar. Returns [] when the first line does not look like a name.
    """
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.lower().strip(" :") in _HEADER_LINES:
            continue  # skip a "Resume" title line
        line = re.sub(r"^name\s*[:\-]\s*", "", line, flags=re.IGNORECASE)
        segment = _FIRST_LINE_SPLIT.split(line)[0].strip()
        if segment.lower() in _SECTION_WORDS:
            return []
        words = segment.split()
        if 2 <= len(words) <= 5 and all(_NAME_WORD.match(word) for word in words):
            return words
        return []
    return []


def _mask_names(text: str, name_words: list[str]) -> tuple[str, int]:
    if not name_words:
        return text, 0
    total = 0
    # Full name first, so "Jane Q. Doe" becomes one [NAME] instead of three.
    full_name = r"\s+".join(re.escape(word) for word in name_words)
    text, count = re.subn(rf"(?<!\w){full_name}(?!\w)", NAME, text, flags=re.IGNORECASE)
    total += count
    # Then every later occurrence of each name word (initials and titles excluded). Lowercase
    # forms are left alone: "grant writing" is not the applicant "Grant".
    tokens = []
    for word in name_words:
        token = word.strip(".")
        if len(token) >= 2 and token.lower() not in _NOT_NAME_TOKENS:
            tokens.append(token)
    for token in sorted(set(tokens), key=len, reverse=True):
        forms = {token, token.upper(), token.capitalize(), token.title()}
        alternation = "|".join(re.escape(form) for form in sorted(forms, key=len, reverse=True))
        text, count = re.subn(rf"(?<!\w)(?:{alternation})(?!\w)", NAME, text)
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
    counts["urls"] = total

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
