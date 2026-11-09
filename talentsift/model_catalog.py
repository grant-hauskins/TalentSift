"""The curated list of AI models an employer can choose for scoring (SOW: "choice of AI model").

Slugs are OpenRouter model ids. Claude is the default (SOW section 6). The list is short on purpose:
every entry supports structured JSON output, which the screening prompt relies on. "Other" on the
Settings page still accepts any OpenRouter slug, and every screening run records the model it used.

Model ids change over time. Check https://openrouter.ai/models before a release and update this file;
nothing else in the code names a model.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelOption:
    slug: str
    label: str
    note: str


MODEL_CATALOG: tuple[ModelOption, ...] = (
    ModelOption("anthropic/claude-sonnet-4.5", "Claude Sonnet 4.5 (default)", "Best balance of accuracy and cost for screening."),
    ModelOption("anthropic/claude-haiku-4.5", "Claude Haiku 4.5", "Fastest and cheapest Claude; good for large batches."),
    ModelOption("openai/gpt-4o-mini", "GPT-4o mini", "Low-cost alternative if Anthropic models are unavailable."),
    ModelOption("google/gemini-2.5-flash", "Gemini 2.5 Flash", "Low-cost alternative with a large context window."),
)
DEFAULT_MODEL = MODEL_CATALOG[0].slug
OTHER = "other"


def catalog_slugs() -> list[str]:
    return [option.slug for option in MODEL_CATALOG]


def find(slug: str) -> ModelOption | None:
    return next((option for option in MODEL_CATALOG if option.slug == slug), None)


def label_for(slug: str) -> str:
    """Display name for a slug: the catalog label, or the slug itself for a custom model."""
    option = find(slug)
    return option.label if option else slug
