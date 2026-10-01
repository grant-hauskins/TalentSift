"""Resume parsers. Use `registry.parse_file(path)`; never call a format parser directly."""

from talentsift.parsers.base import ParsedResume, ResumeParser
from talentsift.parsers.registry import get_parser, parse_file, register_parser, supported_extensions

__all__ = [
    "ParsedResume",
    "ResumeParser",
    "get_parser",
    "parse_file",
    "register_parser",
    "supported_extensions",
]
