"""Load versioned prompt files from `prompts/`.

File format (Markdown):

    ---
    prompt_version: screen_v1
    ---
    <!-- SYSTEM -->
    ...system prompt...
    <!-- USER -->
    ...user prompt with {{placeholders}}...

Never edit a prompt in place once it has been used: copy it to `*_v2.md` and bump `prompt_version`.
The file's SHA-256 is recorded with every run, so silent edits are still detectable.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

SCREEN_PROMPT = "screen_v1"
RUBRIC_PROMPT = "rubric_draft_v1"

_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
_SYSTEM_MARKER = "<!-- SYSTEM -->"
_USER_MARKER = "<!-- USER -->"


@dataclass(frozen=True)
class PromptTemplate:
    name: str
    version: str
    system: str
    user_template: str
    sha256: str

    @property
    def cache_id(self) -> str:
        """Version plus content hash: part of the result cache key."""
        return f"{self.version}@{self.sha256[:12]}"

    def placeholders(self) -> set[str]:
        return set(_PLACEHOLDER.findall(self.user_template))

    def render_user(self, **values: str) -> str:
        """Fill every {{placeholder}} in one pass, so text inside values is never re-interpreted."""
        missing = self.placeholders() - values.keys()
        if missing:
            raise KeyError(f"Prompt {self.name} is missing values for: {sorted(missing)}")
        return _PLACEHOLDER.sub(lambda match: values[match.group(1)], self.user_template)


def load_prompt(name: str, prompts_dir: Path) -> PromptTemplate:
    path = Path(prompts_dir) / f"{name}.md"
    content = path.read_text(encoding="utf-8")
    sha = hashlib.sha256(content.encode("utf-8")).hexdigest()

    front_matter = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
    if not front_matter:
        raise ValueError(f"{path} is missing its front matter block")
    meta = dict(
        line.split(":", 1) for line in front_matter.group(1).splitlines() if ":" in line
    )
    version = meta.get("prompt_version", "").strip()
    if not version:
        raise ValueError(f"{path} front matter has no prompt_version")

    body = content[front_matter.end():]
    if _SYSTEM_MARKER not in body or _USER_MARKER not in body:
        raise ValueError(f"{path} needs both {_SYSTEM_MARKER} and {_USER_MARKER} sections")
    system = body.split(_SYSTEM_MARKER, 1)[1].split(_USER_MARKER, 1)[0].strip()
    user = body.split(_USER_MARKER, 1)[1].strip()
    return PromptTemplate(name=name, version=version, system=system, user_template=user, sha256=sha)
