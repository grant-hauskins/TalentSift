"""Pick a parser by file extension.

Adding a format: write `parsers/<format>.py` with a class like `PdfParser`, then add an instance to
`_PARSERS` below (or call `register_parser` at startup). Scoring code never changes.
"""

from __future__ import annotations

from pathlib import Path

from talentsift.models import PARSE_UNSUPPORTED
from talentsift.parsers.base import ParsedResume, ResumeParser
from talentsift.parsers.docx import DocxParser
from talentsift.parsers.pdf import PdfParser

_PARSERS: list[ResumeParser] = [PdfParser(), DocxParser()]


def register_parser(parser: ResumeParser) -> None:
    """Add a parser. Later registrations win, so a new parser can replace an old one."""
    _PARSERS.insert(0, parser)


def get_parser(path: Path) -> ResumeParser | None:
    for parser in _PARSERS:
        if parser.supports(Path(path)):
            return parser
    return None


def supported_extensions() -> tuple[str, ...]:
    """Extensions with a registered parser (including stubs that report "planned")."""
    return tuple(sorted({ext for parser in _PARSERS for ext in parser.extensions}))


def parse_file(path: Path) -> tuple[ParsedResume, str]:
    """Parse a file with the right parser. Returns the result and the parser name ("none" if none)."""
    parser = get_parser(path)
    if parser is None:
        return (
            ParsedResume(
                text="",
                status=PARSE_UNSUPPORTED,
                message=f"No parser for '{Path(path).suffix or 'this file type'}'. Supported: {', '.join(supported_extensions())}.",
            ),
            "none",
        )
    try:
        return parser.parse(Path(path)), parser.name
    except NotImplementedError as exc:
        return (
            ParsedResume(
                text="",
                status=PARSE_UNSUPPORTED,
                message=f"{parser.name.upper()} files are not supported yet ({exc}). Please upload a PDF.",
            ),
            parser.name,
        )
