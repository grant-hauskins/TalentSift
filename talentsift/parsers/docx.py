"""DOCX parser stub. Registered so DOCX files get a clear message instead of a silent skip."""

from __future__ import annotations

from pathlib import Path

from talentsift.parsers.base import ParsedResume


class DocxParser:
    name = "docx"
    extensions = (".docx",)

    def supports(self, path: Path) -> bool:
        return Path(path).suffix.lower() in self.extensions

    def parse(self, path: Path) -> ParsedResume:
        raise NotImplementedError("planned for final project")
