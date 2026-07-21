"""Typed boundary between Horizon discovery and CodeQuest editorial work."""

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field, HttpUrl

from ..models import SourceType


class ArticleType(str, Enum):
    """Editorial formats with intentionally different writing expectations."""

    NEWS_REPORT = "news_report"
    EXPLAINER = "explainer"
    TOOL_INTRODUCTION = "tool_introduction"
    TUTORIAL = "tutorial"
    ANALYSIS = "analysis"


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
