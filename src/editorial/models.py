"""Typed boundary between Horizon discovery and CodeQuest editorial work."""

from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field, HttpUrl

from ..models import SourceType


class ArticleType(str, Enum):
    """Editorial formats with intentionally different writing expectations."""

    NEWS_REPORT = "news_report"
    EXPLAINER = "explainer"
    TOOL_INTRODUCTION = "tool_introduction"
    TUTORIAL = "tutorial"
    ANALYSIS = "analysis"


class PreferenceSignal(str, Enum):
    """Whether an editor wants more or less of a writing behavior."""

    PREFER = "prefer"
    AVOID = "avoid"


class PreferenceScope(str, Enum):
    """How broadly one feedback item should influence future writing."""

    STORY = "story"
    ARTICLE_TYPE = "article_type"
    GLOBAL = "global"


class EvidenceSource(BaseModel):
    """One source and the material it contributes to an editorial brief."""

    url: HttpUrl
    title: str
    publisher: str | None = None
    source_type: SourceType | None = None
    excerpt: str = ""
    is_primary: bool = False


class EvidencePack(BaseModel):
    """Source material available before any long-form article is generated."""

    content_item_id: str
    sources: list[EvidenceSource] = Field(min_length=1)
    unresolved_questions: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def primary_source(self) -> EvidenceSource:
        return self.sources[0]

    @property
    def supporting_sources(self) -> list[EvidenceSource]:
        return self.sources[1:]


class EditorialBrief(BaseModel):
    """Editable assignment that must exist before article generation."""

    content_item_id: str
    working_title: str
    article_type: ArticleType
    central_angle: str
    audience_value: str
    required_facts: list[str] = Field(default_factory=list)
    uncertainty_notes: list[str] = Field(default_factory=list)
    status: str = "draft"


class EditorialPacket(BaseModel):
    """Complete, serializable handoff from the news radar to an editor."""

    brief: EditorialBrief
    evidence: EvidencePack


class PreferenceRule(BaseModel):
    """One explicit, traceable instruction derived from editor feedback."""

    signal: PreferenceSignal
    dimension: str
    instruction: str
    scope: PreferenceScope
    evidence_count: int = 1
    latest_feedback_at: datetime


class EditorialPreferenceProfile(BaseModel):
    """Current writing constraints for an article type or individual story."""

    article_type: ArticleType | None = None
    content_item_id: str | None = None
    rules: list[PreferenceRule] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def writer_instructions(self) -> str:
        if not self.rules:
            return "No explicit editorial preferences have been recorded yet."
        lines = [
            "Editorial preferences from explicit human feedback:",
            *(
                f"- {rule.signal.value.title()} [{rule.dimension.replace('_', ' ')}]: "
                f"{rule.instruction}"
                for rule in self.rules
            ),
            "Use these as writing constraints, but never let them override factual evidence.",
        ]
        return "\n".join(lines)


class DraftParagraph(BaseModel):
    """One draft paragraph and the evidence sources supporting it."""

    text: str = Field(min_length=1)
    source_ids: list[str] = Field(min_length=1)


class DraftSection(BaseModel):
    """A reviewable article section made of source-linked paragraphs."""

    heading: str = Field(min_length=1)
    paragraphs: list[DraftParagraph] = Field(min_length=1)


class ArticleDraft(BaseModel):
    """Versioned, unpublished article output produced for human review."""

    draft_id: str = Field(default_factory=lambda: f"draft_{uuid4().hex}")
    content_item_id: str
    title: str = Field(min_length=1)
    dek: str = Field(min_length=1)
    sections: list[DraftSection] = Field(min_length=1)
    source_map: dict[str, HttpUrl]
    preference_rules: list[PreferenceRule] = Field(default_factory=list)
    generator_model: str
    prompt_version: str = "codequest-draft-v1"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
