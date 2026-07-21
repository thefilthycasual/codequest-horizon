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
    revision_notes: list[str] = Field(default_factory=list)
    parent_draft_id: str | None = None
    edit_note: str = ""
    generator_model: str
    prompt_version: str = "codequest-draft-v1"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class QualityCheckStatus(str, Enum):
    PASS = "pass"
    WARNING = "warning"
    BLOCK = "block"


class DraftQualityCheck(BaseModel):
    key: str
    label: str
    status: QualityCheckStatus
    detail: str


class DraftQualityReport(BaseModel):
    draft_id: str
    checks: list[DraftQualityCheck]
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def can_approve(self) -> bool:
        return all(check.status != QualityCheckStatus.BLOCK for check in self.checks)


class DecisionOutcome(str, Enum):
    APPROVED = "approved"
    READY_FOR_APPROVAL = "ready_for_approval"
    NEEDS_REVISION = "needs_revision"


class DraftDecision(BaseModel):
    decision_id: str = Field(default_factory=lambda: f"decision_{uuid4().hex}")
    content_item_id: str
    draft_id: str
    outcome: DecisionOutcome
    notes: str = ""
    quality_report: DraftQualityReport
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DiscordApprovalStatus(str, Enum):
    PENDING = "pending"
    ENDORSED = "endorsed"
    REVISION_SUGGESTED = "revision_suggested"
    DELIVERY_FAILED = "delivery_failed"


class DiscordApprovalRequest(BaseModel):
    """A durable request tying one reviewed draft to one Discord message."""

    request_id: str = Field(default_factory=lambda: f"discord_{uuid4().hex}")
    content_item_id: str
    draft_id: str
    status: DiscordApprovalStatus = DiscordApprovalStatus.PENDING
    channel_id: str | None = None
    message_id: str | None = None
    resolved_by_id: str | None = None
    resolved_by_name: str | None = None
    revision_notes: str = ""
    requested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: datetime | None = None


class WordPressDeliveryStatus(str, Enum):
    PENDING = "pending"
    DRAFT_CREATED = "draft_created"
    FAILED = "failed"


class WordPressDelivery(BaseModel):
    """Durable record for delivering one exact approved draft to WordPress."""

    delivery_id: str = Field(default_factory=lambda: f"wordpress_{uuid4().hex}")
    content_item_id: str
    draft_id: str
    status: WordPressDeliveryStatus = WordPressDeliveryStatus.PENDING
    attempts: int = 1
    post_id: int | None = None
    post_url: str | None = None
    editor_url: str | None = None
    error_message: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
