"""The no-new-facts guard: checks a resume draft against the candidate's core facts, in code.

SOW critical success factor: "the assistant adds no skills, credentials, or experience beyond the
candidate's core facts". The guard is deterministic (no AI) and runs on every draft before a candidate
sees it as ready, and again before a version is saved. The full specification, with examples and known
limits, is docs/drafting_guard.md.

What the model may write is limited by the output schema (`schemas.ResumeDraft`): a summary, a skills
list, and bullets under experience facts. Job titles, employers, schools, degrees, certifications, and
dates are never written by the model: code renders them from the facts. The guard then checks the text
the model did write:

1. Structure: every fact id exists and is used in the right section.
2. Skills: each listed skill appears, word for word, in the candidate's facts.
3. Numbers: every number in a bullet appears in that bullet's own fact; in the summary, in any fact.
4. Named terms (tools, products, acronyms, proper nouns): must appear in the facts exactly.
5. Posting terms: a word from the target job posting that the facts do not support is the classic
   way a draft drifts toward what the employer wants to hear. Blocked.
6. Credential words (certified, degree, licensed, award, promoted, ...) the facts do not support. Blocked.

Other unfamiliar wording is not blocked but is listed for the candidate to read before approving.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Sequence

from talentsift.draft_template import FactView
from talentsift.schemas import ResumeDraft
from talentsift.textutil import STOPWORDS, keywords

# Ordinary words match when they share this many leading letters ("analyst" ~ "analysis" ~ "analyzed"
# share "analy"). Named terms and numbers never use this: they must match exactly.
PREFIX_MATCH = 5

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9+#]*(?:[./'’-][A-Za-z0-9+#]+)*")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_SENTENCE_START = re.compile(r"(?:^|[.!?:;]\s+|\n\s*|^[-*•]\s*)$")

_NUMBER_WORDS = {
    "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8",
    "nine": "9", "ten": "10", "eleven": "11", "twelve": "12", "fifteen": "15", "twenty": "20",
    "thirty": "30", "forty": "40", "fifty": "50", "hundred": "100", "thousand": "1000",
    "dozen": "12", "million": "1000000", "billion": "1000000000",
    "double": "2", "doubled": "2", "triple": "3", "tripled": "3", "half": "50", "halved": "50",
}  # fmt: skip

# Words that claim a credential, a rank, or a result. Never acceptable unless the facts say so.
_CREDENTIAL_PREFIXES = (
    "certif", "licens", "accredit", "credential", "degree", "bachelor", "master", "doctor", "phd",
    "diploma", "graduat", "honor", "award", "scholar", "fellow", "promot", "valedict", "summa", "magna",
    "expert", "fluent", "bilingual", "senior", "lead", "principal", "director", "manager", "supervis",
    "patent", "publish", "clearance",
)  # fmt: skip

# Connecting words a resume needs that claim nothing on their own.
_SAFE_WORDS = frozenset(
    """
    experience experienced skills skill include includes including background professional work worked
    working role roles using used use across within while during through throughout where when well
    as at in on for to with and or of the a an by from into plus also such
    strong proven track record focus focused focusing bring brings bringing seeking
    """.split()
)


def normalize_number(text: str) -> str:
    return text.replace(",", "").rstrip(".")


def _fold(text: str) -> str:
    return " ".join(text.replace("’", "'").casefold().split())


def _plain(word: str) -> str:
    """Casefolded word without possessive or trailing punctuation, for exact matching of named terms."""
    word = word.replace("’", "'").casefold().strip(".'-/")
    return word[:-2] if word.endswith("'s") else word


def _is_named(word: str, sentence_start: bool) -> bool:
    """Tools, products, acronyms, and proper nouns: anything written like a name."""
    if any(ch.isdigit() or ch in "+#" for ch in word):
        return True  # C++, C#, S3, Python3
    if len(word) >= 2 and word.isupper():
        return True  # SQL, AWS, ERP
    if any(ch.isupper() for ch in word[1:]):
        return True  # PowerPoint, JavaScript, NetSuite
    if "." in word.strip("."):
        return True  # Node.js, ASP.NET
    return word[0].isupper() and not sentence_start


class _Source:
    """The words and numbers the candidate actually wrote."""

    def __init__(self, text: str):
        self.folded = _fold(text)
        self.words = {_plain(w) for w in _WORD.findall(text)}
        # Parts of compound words count too: "data-driven" supports "data".
        self.words |= {part for w in list(self.words) for part in re.split(r"[./'-]", w) if part}
        self.numbers = {normalize_number(n) for n in _NUMBER.findall(text)}

    def has_exact(self, word: str) -> bool:
        plain = _plain(word)
        singular = plain[:-1] if plain.endswith("s") else plain
        if plain in self.words or singular in self.words or plain + "s" in self.words:
            return True
        return all(part in self.words for part in re.split(r"[./'-]", plain) if part) if re.search(r"[./'-]", plain) else False

    def has_similar(self, word: str) -> bool:
        plain = _plain(word)
        if self.has_exact(word):
            return True
        if len(plain) < PREFIX_MATCH:
            return False
        head = plain[:PREFIX_MATCH]
        return any(len(w) >= PREFIX_MATCH and w.startswith(head) for w in self.words)

    def has_phrase(self, phrase: str) -> bool:
        return re.search(rf"(?<![\w+#]){re.escape(_fold(phrase))}(?![\w+#])", self.folded) is not None


@dataclass(frozen=True)
class Violation:
    kind: str  # unknown_fact | skill | number | named_term | posting_term | credential | empty
    where: str  # "summary", "skills", or "experience (Job title), bullet 2"
    text: str  # the offending word, number, or id
    message: str  # plain-language explanation shown to the candidate

    def as_dict(self) -> dict:
        return {"kind": self.kind, "where": self.where, "text": self.text, "message": self.message}


@dataclass
class GuardReport:
    passed: bool
    violations: list[Violation] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # unfamiliar wording, for the candidate to read
    lines_checked: int = 0
    facts_hash: str = ""

    def as_dict(self) -> dict:
        return {
            "passed": self.passed,
            "violations": [v.as_dict() for v in self.violations],
            "notes": self.notes,
            "lines_checked": self.lines_checked,
            "facts_hash": self.facts_hash,
        }

    def retry_message(self) -> str:
        """Feedback the model can act on when asked to try again."""
        items = "; ".join(f"{v.where}: {v.message}" for v in self.violations[:12])
        return (
            "the draft adds information that is not in the candidate's core facts. Remove or reword it using "
            f"only the facts given. Problems: {items}"
        )


def facts_hash(facts: Sequence[FactView], headline: str = "") -> str:
    """Fingerprint of the facts a draft was checked against (stored with every saved version)."""
    payload = [[f.id, f.category, f.title, f.organization, f.start, f.end, f.details] for f in facts]
    return hashlib.sha256(json.dumps([headline, payload], ensure_ascii=False).encode("utf-8")).hexdigest()


def _check_line(
    text: str,
    where: str,
    *,
    word_source: _Source,
    number_source: _Source,
    number_scope: str,
    posting_words: set[str],
    violations: list[Violation],
    notes: list[str],
) -> None:
    for match in _NUMBER.finditer(text):
        number = normalize_number(match.group(0))
        if number not in number_source.numbers:
            violations.append(Violation("number", where, match.group(0), f"the number {match.group(0)} is not in {number_scope}"))

    seen: set[str] = set()
    for match in _WORD.finditer(text):
        word = match.group(0)
        plain = _plain(word)
        if not plain or plain in seen:
            continue
        seen.add(plain)

        if plain in _NUMBER_WORDS:
            digit = _NUMBER_WORDS[plain]
            if plain not in number_source.words and digit not in number_source.numbers:
                violations.append(Violation("number", where, word, f"the quantity '{word}' is not in {number_scope}"))
            continue

        sentence_start = bool(_SENTENCE_START.search(text[: match.start()]))
        if _is_named(word, sentence_start):
            if not word_source.has_exact(word):
                violations.append(Violation("named_term", where, word, f"'{word}' is not in your core facts"))
            continue
        if plain in STOPWORDS or plain in _SAFE_WORDS:
            continue
        if plain.startswith(_CREDENTIAL_PREFIXES):
            # Credential words must match exactly: "certificate" coursework does not support "certified".
            if not word_source.has_exact(word):
                violations.append(Violation("credential", where, word, f"'{word}' claims a credential or level your core facts do not state"))
            continue
        if word_source.has_similar(word):
            continue
        if posting_words.intersection(keywords(plain)):
            violations.append(Violation("posting_term", where, word, f"'{word}' comes from the job posting, not from your core facts"))
        else:
            notes.append(f"{where}: '{word}' is wording that does not appear in your facts")


def check_draft(draft: ResumeDraft, facts: Sequence[FactView], role_text: str = "", headline: str = "") -> GuardReport:
    """Check a draft against the facts. `passed` is False if anything was added beyond them."""
    by_id = {f.id: f for f in facts}
    everything = _Source("\n".join([headline, *(f.text for f in facts)]))
    posting_words = set(keywords(role_text))
    violations: list[Violation] = []
    notes: list[str] = []
    lines = 0

    # 1. Structure
    def check_ids(ids: Sequence[int], category: str, section: str) -> None:
        if len(set(ids)) != len(ids):
            violations.append(Violation("unknown_fact", section, str(list(ids)), "a fact is listed more than once"))
        for fact_id in ids:
            fact = by_id.get(fact_id)
            if fact is None:
                violations.append(Violation("unknown_fact", section, str(fact_id), f"fact {fact_id} is not one of your core facts"))
            elif fact.category != category:
                violations.append(Violation("unknown_fact", section, str(fact_id), f"fact {fact_id} is a {fact.category} fact, not {category}"))

    check_ids([entry.fact_id for entry in draft.experience], "experience", "experience")
    check_ids(draft.education_ids, "education", "education")
    check_ids(draft.certification_ids, "certifications", "certifications")

    # 2. Skills: word for word from the facts
    for skill in draft.skills:
        lines += 1
        if not skill.strip() or not everything.has_phrase(skill):
            violations.append(Violation("skill", "skills", skill, f"the skill '{skill}' is not in your core facts"))

    # 3-6. Free text
    if draft.summary.strip():
        lines += 1
        _check_line(
            draft.summary, "summary", word_source=everything, number_source=everything,
            number_scope="your core facts", posting_words=posting_words, violations=violations, notes=notes,
        )
    for entry in draft.experience:
        fact = by_id.get(entry.fact_id)
        if fact is None or fact.category != "experience":
            continue  # already reported above
        own = _Source(fact.text)
        for number, bullet in enumerate(entry.bullets, start=1):
            lines += 1
            where = f"experience ({fact.title}), bullet {number}"
            if not bullet.strip():
                violations.append(Violation("empty", where, "", "the bullet is empty"))
                continue
            _check_line(
                bullet, where, word_source=everything, number_source=own,
                number_scope=f"your facts for {fact.title}", posting_words=posting_words,
                violations=violations, notes=notes,
            )

    if not (draft.summary.strip() or draft.skills or draft.experience or draft.education_ids or draft.certification_ids):
        violations.append(Violation("empty", "draft", "", "the draft is empty"))
    return GuardReport(
        passed=not violations,
        violations=violations,
        notes=list(dict.fromkeys(notes)),
        lines_checked=lines,
        facts_hash=facts_hash(facts, headline),
    )
