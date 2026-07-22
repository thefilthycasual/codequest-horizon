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


class BrandRuleChannel(str, Enum):
    """Generation surface controlled by a brand rule."""

    ARTICLE = "article"
    LINKEDIN = "linkedin"
    X = "x"
    FACEBOOK = "facebook"


class Organization(BaseModel):
    """Top-level tenant boundary prepared for the future SaaS product."""

    organization_id: str = Field(default_factory=lambda: f"org_{uuid4().hex}")
    name: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class BrandProfile(BaseModel):
    """Approved brand context supplied to future content generation."""

    brand_id: str = Field(default_factory=lambda: f"brand_{uuid4().hex}")
    organization_id: str
    name: str = Field(min_length=1)
    website_url: str = ""
    description: str = ""
    audience: str = ""
    positioning: str = ""
    voice_summary: str = ""
    default_cta: str = ""
    prohibited_terms: list[str] = Field(default_factory=list)
    default_language: str = "en"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def prompt_context(self) -> str:
        fields = [
            ("Brand", self.name),
            ("Website", self.website_url),
            ("Brand description", self.description),
            ("Audience", self.audience),
            ("Positioning", self.positioning),
            ("Voice", self.voice_summary),
            ("Default call to action", self.default_cta),
            ("Prohibited terms", ", ".join(self.prohibited_terms)),
            ("Default language", self.default_language),
        ]
        populated = [f"- {label}: {value}" for label, value in fields if value]
        return "\n".join(populated) if populated else "No brand context has been configured yet."


class BrandRule(BaseModel):
    """One approved, versionable instruction owned by a brand."""

    rule_id: str = Field(default_factory=lambda: f"brand_rule_{uuid4().hex}")
    brand_id: str
    channel: BrandRuleChannel = BrandRuleChannel.ARTICLE
    signal: PreferenceSignal
    dimension: str = Field(min_length=1)
    instruction: str = Field(min_length=1)
    article_type: ArticleType | None = None
    priority: int = Field(default=50, ge=0, le=100)
    enabled: bool = True
    source: str = "manual"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


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
    brand_profile: BrandProfile | None = None
    rules: list[PreferenceRule] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def writer_instructions(self) -> str:
        if not self.rules and self.brand_profile is None:
            return "No explicit editorial preferences have been recorded yet."
        lines = []
        if self.brand_profile is not None:
            lines.extend(["Approved brand context:", self.brand_profile.prompt_context()])
        if self.rules:
            lines.extend(
                [
                    "Editorial preferences from explicit human feedback and approved brand rules:",
                    *(
                        f"- {rule.signal.value.title()} [{rule.dimension.replace('_', ' ')}]: "
                        f"{rule.instruction}"
                        for rule in self.rules
                    ),
                ]
            )
        lines.append(
            "Use these as writing constraints, but never let them override factual evidence."
        )
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
    brand_profile_snapshot: BrandProfile | None = None
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


class SocialPlatform(str, Enum):
    """Social destinations supported by the editorial workspace."""

    LINKEDIN = "linkedin"
    X = "x"
    FACEBOOK = "facebook"


class SocialPostStatus(str, Enum):
    """Review state for one platform-specific social draft."""

    DRAFT = "draft"
    APPROVED = "approved"
    NEEDS_REVISION = "needs_revision"


class SocialCampaign(BaseModel):
    """A social campaign generated from one exact approved article version."""

    campaign_id: str = Field(default_factory=lambda: f"campaign_{uuid4().hex}")
    content_item_id: str
    article_draft_id: str
    generator_model: str
    preference_snapshot: dict[str, list[str]] = Field(default_factory=dict)
    public_article_url: HttpUrl | None = None
    prompt_version: str = "codequest-social-v1"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SocialPostDraft(BaseModel):
    """Versioned copy for one platform, grounded in the approved article."""

    post_id: str = Field(default_factory=lambda: f"social_{uuid4().hex}")
    campaign_id: str
    content_item_id: str
    article_draft_id: str
    platform: SocialPlatform
    body: str = Field(min_length=1)
    source_ids: list[str] = Field(min_length=1)
    status: SocialPostStatus = SocialPostStatus.DRAFT
    version: int = Field(default=1, ge=1)
    parent_post_id: str | None = None
    edit_note: str = ""
    generator_model: str
    prompt_version: str = "codequest-social-v1"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class BufferDeliveryMode(str, Enum):
    """Explicit Buffer scheduling choices exposed to an editor."""

    SHARE_NOW = "shareNow"
    CUSTOM_SCHEDULED = "customScheduled"


class BufferDeliveryStatus(str, Enum):
    """Durable delivery state, including ambiguous network outcomes."""

    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"
    UNCERTAIN = "uncertain"


class BufferDelivery(BaseModel):
    """One guarded delivery attempt for one exact approved social version."""

    delivery_id: str = Field(default_factory=lambda: f"buffer_{uuid4().hex}")
    content_item_id: str
    campaign_id: str
    post_id: str
    platform: SocialPlatform
    channel_id: str
    mode: BufferDeliveryMode
    final_text: str
    scheduled_for: datetime | None = None
    status: BufferDeliveryStatus = BufferDeliveryStatus.PENDING
    attempts: int = 1
    buffer_post_id: str | None = None
    buffer_status: str | None = None
    buffer_due_at: datetime | None = None
    error_message: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AutomationRunStatus(str, Enum):
    """Durable state for one bounded discovery-to-draft automation run."""

    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class AutomationRun(BaseModel):
    """Operational record for an automation run without storing credentials."""

    run_id: str = Field(default_factory=lambda: f"automation_{uuid4().hex}")
    status: AutomationRunStatus = AutomationRunStatus.RUNNING
    trigger: str = "manual"
    stage: str = "starting"
    discovered_count: int = 0
    imported_count: int = 0
    selected_count: int = 0
    drafted_count: int = 0
    skipped_count: int = 0
    imported_item_ids: list[str] = Field(default_factory=list)
    selected_item_ids: list[str] = Field(default_factory=list)
    drafted_item_ids: list[str] = Field(default_factory=list)
    error_message: str = ""
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
