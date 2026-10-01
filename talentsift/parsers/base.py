"""Parser interface.

To support a new file format, write one class with `supports()` and `parse()` and register it in
`registry.py`. Nothing in scoring changes: scoring only ever sees the text a parser returns.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass
class ParsedResume:
    """What a parser returns. `status` is one of the `models.PARSE_*` values."""

    text: str
    status: str
    message: str = ""
    page_count: int = 0
    image_count: int = 0
    scanned_pages: list[int] = field(default_factory=list)  # 1-based pages that look scanned


@runtime_checkable
class ResumeParser(Protocol):
    """Every parser has a short name, the file extensions it handles, and these two methods."""

    name: str
    extensions: tuple[str, ...]

    def supports(self, path: Path) -> bool:
        """True if this parser can read the file (normally decided by extension)."""
        ...

    def parse(self, path: Path) -> ParsedResume:
        """Extract text. Raise NotImplementedError if the format is recognized but not built yet."""
        ...


def normalize_extracted_text(text: str) -> str:
    """Clean up extracted text so evidence quotes can be matched reliably.

    NFKC turns ligatures and full-width characters into plain letters (e.g. "ﬁ" -> "fi").
    """
    text = unicodedata.normalize("NFKC", text).replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    text = "\n".join(lines).strip()
    return re.sub(r"\n{3,}", "\n\n", text)
