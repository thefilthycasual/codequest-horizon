"""Small SQLite repository for CodeQuest editorial state."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import (
    ArticleDraft,
    AutomationRun,
    AutomationRunStatus,
    BrandProfile,
    BrandRule,
    BrandRuleChannel,
    BufferDelivery,
    BufferDeliveryMode,
    BufferDeliveryStatus,
    DecisionOutcome,
    DraftDecision,
    DiscordApprovalRequest,
    DiscordApprovalStatus,
    EditorialPacket,
    Organization,
    PreferenceScope,
    PreferenceSignal,
    SocialCampaign,
    SocialPlatform,
    SocialPostDraft,
    SocialPostStatus,
    WordPressDelivery,
    WordPressDeliveryStatus,
    WordPressCategory,
    WordPressPublishingSettings,
)


EDITORIAL_STATUSES = (
    "candidate",
    "selected",
    "needs_revision",
    "ready_for_approval",
    "approved",
    "archived",
)

FEEDBACK_DIMENSIONS = (
    "general",
    "angle",
    "headline",
    "tone",
    "depth",
    "structure",
    "evidence",
    "technical_level",
)

FEEDBACK_SIGNALS = tuple(signal.value for signal in PreferenceSignal)
FEEDBACK_SCOPES = tuple(scope.value for scope in PreferenceScope)
SOCIAL_PREFERENCE_LIMIT_PER_PLATFORM = 12
DEFAULT_ORGANIZATION_ID = "org_codequest"
DEFAULT_BRAND_ID = "brand_codequest"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class EditorialItemRecord:
    packet: EditorialPacket
    status: str
    created_at: str
    updated_at: str
    brand_id: str = DEFAULT_BRAND_ID


class EditorialStore:
    """Persist editorial packets without coupling them to Horizon run files."""

    def __init__(self, path: str | Path = "data/codequest-editorial.sqlite3"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def checkpoint(self) -> None:
        """Copy committed WAL frames into the database after a batch boundary."""

        with self._connect() as connection:
            connection.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS organizations (
                    organization_id TEXT PRIMARY KEY,
                    organization_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS brands (
                    brand_id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    profile_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (organization_id)
                        REFERENCES organizations(organization_id)
                        ON DELETE CASCADE
                )
                """
            )
            default_organization = Organization(
                organization_id=DEFAULT_ORGANIZATION_ID,
                name="CodeQuest workspace",
            )
            connection.execute(
                "INSERT INTO organizations "
                "(organization_id, organization_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(organization_id) DO NOTHING",
                (
                    default_organization.organization_id,
                    default_organization.model_dump_json(),
                    default_organization.created_at.isoformat(),
                    default_organization.updated_at.isoformat(),
                ),
            )
            default_brand = BrandProfile(
                brand_id=DEFAULT_BRAND_ID,
                organization_id=DEFAULT_ORGANIZATION_ID,
                name="CodeQuest",
            )
            connection.execute(
                "INSERT INTO brands "
                "(brand_id, organization_id, profile_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(brand_id) DO NOTHING",
                (
                    default_brand.brand_id,
                    default_brand.organization_id,
                    default_brand.model_dump_json(),
                    default_brand.created_at.isoformat(),
                    default_brand.updated_at.isoformat(),
                ),
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS editorial_items (
                    content_item_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    packet_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            item_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(editorial_items)").fetchall()
            }
            if "brand_id" not in item_columns:
                connection.execute(
                    "ALTER TABLE editorial_items "
                    f"ADD COLUMN brand_id TEXT NOT NULL DEFAULT '{DEFAULT_BRAND_ID}'"
                )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS brand_rules (
                    rule_id TEXT PRIMARY KEY,
                    brand_id TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    article_type TEXT,
                    enabled INTEGER NOT NULL,
                    priority INTEGER NOT NULL,
                    rule_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (brand_id)
                        REFERENCES brands(brand_id)
                        ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS discord_approval_requests (
                    request_id TEXT PRIMARY KEY,
                    content_item_id TEXT NOT NULL,
                    draft_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (content_item_id)
                        REFERENCES editorial_items(content_item_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (draft_id)
                        REFERENCES editorial_drafts(draft_id)
                        ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS editorial_feedback (
                    feedback_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content_item_id TEXT NOT NULL,
                    dimension TEXT NOT NULL,
                    signal TEXT NOT NULL DEFAULT 'prefer',
                    scope TEXT NOT NULL DEFAULT 'story',
                    note TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (content_item_id)
                        REFERENCES editorial_items(content_item_id)
                        ON DELETE CASCADE
                )
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(editorial_feedback)").fetchall()
            }
            if "signal" not in columns:
                connection.execute(
                    "ALTER TABLE editorial_feedback "
                    "ADD COLUMN signal TEXT NOT NULL DEFAULT 'prefer'"
                )
            if "scope" not in columns:
                connection.execute(
                    "ALTER TABLE editorial_feedback "
                    "ADD COLUMN scope TEXT NOT NULL DEFAULT 'story'"
                )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS editorial_drafts (
                    draft_id TEXT PRIMARY KEY,
                    content_item_id TEXT NOT NULL,
                    draft_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (content_item_id)
                        REFERENCES editorial_items(content_item_id)
                        ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS draft_fact_confirmations (
                    draft_id TEXT NOT NULL,
                    fact_text TEXT NOT NULL,
                    confirmed_at TEXT NOT NULL,
                    PRIMARY KEY (draft_id, fact_text),
                    FOREIGN KEY (draft_id)
                        REFERENCES editorial_drafts(draft_id)
                        ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS editorial_decisions (
                    decision_id TEXT PRIMARY KEY,
                    content_item_id TEXT NOT NULL,
                    draft_id TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    decision_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (content_item_id)
                        REFERENCES editorial_items(content_item_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (draft_id)
                        REFERENCES editorial_drafts(draft_id)
                        ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS wordpress_categories (
                    category_id INTEGER PRIMARY KEY,
                    category_json TEXT NOT NULL,
                    synced_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS wordpress_publishing_settings (
                    content_item_id TEXT PRIMARY KEY,
                    settings_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (content_item_id)
                        REFERENCES editorial_items(content_item_id)
                        ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS wordpress_deliveries (
                    delivery_id TEXT PRIMARY KEY,
                    content_item_id TEXT NOT NULL,
                    draft_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    delivery_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (content_item_id)
                        REFERENCES editorial_items(content_item_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (draft_id)
                        REFERENCES editorial_drafts(draft_id)
                        ON DELETE CASCADE
                )
                """
            )

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS social_campaigns (
                    campaign_id TEXT PRIMARY KEY,
                    content_item_id TEXT NOT NULL,
                    article_draft_id TEXT NOT NULL UNIQUE,
                    campaign_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (content_item_id)
                        REFERENCES editorial_items(content_item_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (article_draft_id)
                        REFERENCES editorial_drafts(draft_id)
                        ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS social_post_drafts (
                    post_id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL,
                    content_item_id TEXT NOT NULL,
                    article_draft_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    post_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (campaign_id, platform, version),
                    FOREIGN KEY (campaign_id)
                        REFERENCES social_campaigns(campaign_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (content_item_id)
                        REFERENCES editorial_items(content_item_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (article_draft_id)
                        REFERENCES editorial_drafts(draft_id)
                        ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS social_feedback (
                    feedback_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content_item_id TEXT NOT NULL,
                    campaign_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    signal TEXT NOT NULL,
                    note TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (content_item_id)
                        REFERENCES editorial_items(content_item_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (campaign_id)
                        REFERENCES social_campaigns(campaign_id)
                        ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS buffer_deliveries (
                    delivery_id TEXT PRIMARY KEY,
                    content_item_id TEXT NOT NULL,
                    campaign_id TEXT NOT NULL,
                    post_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    delivery_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (content_item_id)
                        REFERENCES editorial_items(content_item_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (campaign_id)
                        REFERENCES social_campaigns(campaign_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY (post_id)
                        REFERENCES social_post_drafts(post_id)
                        ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS automation_runs (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    trigger TEXT NOT NULL,
                    run_json TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT
                )
                """
            )

    def replace_wordpress_categories(
        self, categories: list[WordPressCategory]
    ) -> None:
        """Atomically replace the cached taxonomy after a successful sync."""

        synced_at = _now()
        with self._connect() as connection:
            connection.execute("DELETE FROM wordpress_categories")
            connection.executemany(
                "INSERT INTO wordpress_categories "
                "(category_id, category_json, synced_at) VALUES (?, ?, ?)",
                [
                    (category.category_id, category.model_dump_json(), synced_at)
                    for category in categories
                ],
            )

    def list_wordpress_categories(self) -> list[WordPressCategory]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT category_json FROM wordpress_categories "
                "ORDER BY json_extract(category_json, '$.name') COLLATE NOCASE"
            ).fetchall()
        return [
            WordPressCategory.model_validate_json(row["category_json"])
            for row in rows
        ]

    def wordpress_categories_synced_at(self) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT MAX(synced_at) AS synced_at FROM wordpress_categories"
            ).fetchone()
        return str(row["synced_at"]) if row and row["synced_at"] else None

    def save_wordpress_publishing_settings(
        self, settings: WordPressPublishingSettings
    ) -> WordPressPublishingSettings:
        if self.get_item(settings.content_item_id) is None:
            raise KeyError(settings.content_item_id)
        known_ids = {
            category.category_id for category in self.list_wordpress_categories()
        }
        unknown = set(settings.category_ids) - known_ids
        if unknown:
            raise ValueError("Refresh WordPress categories before saving this selection.")
        settings.category_ids = list(dict.fromkeys(settings.category_ids))
        settings.updated_at = datetime.now(timezone.utc)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO wordpress_publishing_settings "
                "(content_item_id, settings_json, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(content_item_id) DO UPDATE SET "
                "settings_json = excluded.settings_json, updated_at = excluded.updated_at",
                (
                    settings.content_item_id,
                    settings.model_dump_json(),
                    settings.updated_at.isoformat(),
                ),
            )
        return settings

    def get_wordpress_publishing_settings(
        self, content_item_id: str
    ) -> WordPressPublishingSettings:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT settings_json FROM wordpress_publishing_settings "
                "WHERE content_item_id = ?",
                (content_item_id,),
            ).fetchone()
        if row:
            return WordPressPublishingSettings.model_validate_json(
                row["settings_json"]
            )
        return WordPressPublishingSettings(content_item_id=content_item_id)

    def start_automation_run(self, trigger: str) -> AutomationRun:
        """Start one run while preventing overlapping workers."""

        cleaned_trigger = trigger.strip() or "manual"
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
        run = AutomationRun(trigger=cleaned_trigger)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            stale_rows = connection.execute(
                "SELECT run_json FROM automation_runs "
                "WHERE status = ? AND started_at < ?",
                (AutomationRunStatus.RUNNING.value, cutoff),
            ).fetchall()
            for row in stale_rows:
                stale = AutomationRun.model_validate_json(row["run_json"])
                stale.status = AutomationRunStatus.FAILED
                stale.stage = "interrupted"
                stale.error_message = "The worker stopped before this run finished."
                stale.finished_at = datetime.now(timezone.utc)
                connection.execute(
                    "UPDATE automation_runs SET status = ?, run_json = ?, finished_at = ? "
                    "WHERE run_id = ?",
                    (
                        stale.status.value,
                        stale.model_dump_json(),
                        stale.finished_at.isoformat(),
                        stale.run_id,
                    ),
                )
            active = connection.execute(
                "SELECT run_id FROM automation_runs WHERE status = ? LIMIT 1",
                (AutomationRunStatus.RUNNING.value,),
            ).fetchone()
            if active:
                raise ValueError("An automation run is already in progress.")
            connection.execute(
                "INSERT INTO automation_runs "
                "(run_id, status, trigger, run_json, started_at, finished_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    run.run_id,
                    run.status.value,
                    run.trigger,
                    run.model_dump_json(),
                    run.started_at.isoformat(),
                    None,
                ),
            )
        return run

    def save_automation_run(self, run: AutomationRun) -> AutomationRun:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE automation_runs SET status = ?, trigger = ?, run_json = ?, "
                "finished_at = ? WHERE run_id = ?",
                (
                    run.status.value,
                    run.trigger,
                    run.model_dump_json(),
                    run.finished_at.isoformat() if run.finished_at else None,
                    run.run_id,
                ),
            )
        if cursor.rowcount != 1:
            raise KeyError(run.run_id)
        return run

    def get_automation_run(self, run_id: str) -> AutomationRun | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT run_json FROM automation_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return AutomationRun.model_validate_json(row["run_json"]) if row else None

    def list_automation_runs(self, limit: int = 20) -> list[AutomationRun]:
        if limit < 1:
            return []
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT run_json FROM automation_runs "
                "ORDER BY started_at DESC, run_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [AutomationRun.model_validate_json(row["run_json"]) for row in rows]

    def save_packet(self, packet: EditorialPacket, status: str = "candidate") -> None:
        self._validate_status(status)
        now = _now()
        payload = packet.model_dump_json()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO editorial_items
                    (content_item_id, status, packet_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(content_item_id) DO UPDATE SET
                    packet_json = excluded.packet_json,
                    updated_at = excluded.updated_at
                """,
                (packet.brief.content_item_id, status, payload, now, now),
            )

    def list_items(self) -> list[EditorialItemRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM editorial_items ORDER BY updated_at DESC"
            ).fetchall()
        return [self._record(row) for row in rows]

    def dashboard_stats(self) -> dict[str, int]:
        """Return compact counts for the local editorial overview."""

        with self._connect() as connection:
            status_rows = connection.execute(
                "SELECT status, COUNT(*) AS total FROM editorial_items GROUP BY status"
            ).fetchall()
            draft_count = connection.execute(
                "SELECT COUNT(*) AS total FROM editorial_drafts"
            ).fetchone()["total"]
            reusable_feedback = connection.execute(
                "SELECT COUNT(*) AS total FROM editorial_feedback "
                "WHERE scope IN ('global', 'article_type')"
            ).fetchone()["total"]
        stats = {status: 0 for status in EDITORIAL_STATUSES}
        stats.update({row["status"]: row["total"] for row in status_rows})
        stats["items"] = sum(row["total"] for row in status_rows)
        stats["drafts"] = draft_count
        stats["reusable_feedback"] = reusable_feedback
        stats["attention"] = stats["selected"] + stats["needs_revision"]
        return stats

    def get_item(self, content_item_id: str) -> EditorialItemRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM editorial_items WHERE content_item_id = ?",
                (content_item_id,),
            ).fetchone()
        return self._record(row) if row else None

    def set_status(self, content_item_id: str, status: str) -> None:
        self._validate_status(status)
        if status == "approved":
            raise ValueError("Approve the latest draft with a persisted editorial decision.")
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE editorial_items SET status = ?, updated_at = ? "
                "WHERE content_item_id = ?",
                (status, _now(), content_item_id),
            )
        if cursor.rowcount != 1:
            raise KeyError(content_item_id)

    def get_brand_profile(self, brand_id: str = DEFAULT_BRAND_ID) -> BrandProfile:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT profile_json FROM brands WHERE brand_id = ?",
                (brand_id,),
            ).fetchone()
        if row is None:
            raise KeyError(brand_id)
        return BrandProfile.model_validate_json(row["profile_json"])

    def save_brand_profile(self, profile: BrandProfile) -> BrandProfile:
        profile = BrandProfile.model_validate(profile.model_dump())
        existing = self.get_brand_profile(profile.brand_id)
        if existing.organization_id != profile.organization_id:
            raise ValueError("A brand cannot be moved to another organization.")
        profile.updated_at = datetime.now(timezone.utc)
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE brands SET profile_json = ?, updated_at = ? WHERE brand_id = ?",
                (
                    profile.model_dump_json(),
                    profile.updated_at.isoformat(),
                    profile.brand_id,
                ),
            )
        if cursor.rowcount != 1:
            raise KeyError(profile.brand_id)
        return profile

    def add_brand_rule(self, rule: BrandRule) -> BrandRule:
        rule = BrandRule.model_validate(rule.model_dump())
        self.get_brand_profile(rule.brand_id)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO brand_rules "
                "(rule_id, brand_id, channel, article_type, enabled, priority, "
                "rule_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    rule.rule_id,
                    rule.brand_id,
                    rule.channel.value,
                    rule.article_type.value if rule.article_type else None,
                    int(rule.enabled),
                    rule.priority,
                    rule.model_dump_json(),
                    rule.created_at.isoformat(),
                    rule.updated_at.isoformat(),
                ),
            )
        return rule

    def save_brand_rule(self, rule: BrandRule) -> BrandRule:
        rule = BrandRule.model_validate(rule.model_dump())
        self.get_brand_profile(rule.brand_id)
        rule.updated_at = datetime.now(timezone.utc)
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE brand_rules SET channel = ?, article_type = ?, enabled = ?, "
                "priority = ?, rule_json = ?, updated_at = ? "
                "WHERE rule_id = ? AND brand_id = ?",
                (
                    rule.channel.value,
                    rule.article_type.value if rule.article_type else None,
                    int(rule.enabled),
                    rule.priority,
                    rule.model_dump_json(),
                    rule.updated_at.isoformat(),
                    rule.rule_id,
                    rule.brand_id,
                ),
            )
        if cursor.rowcount != 1:
            raise KeyError(rule.rule_id)
        return rule

    def get_brand_rule(self, rule_id: str) -> BrandRule | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT rule_json FROM brand_rules WHERE rule_id = ?",
                (rule_id,),
            ).fetchone()
        return BrandRule.model_validate_json(row["rule_json"]) if row else None

    def list_brand_rules(
        self,
        brand_id: str = DEFAULT_BRAND_ID,
        channel: BrandRuleChannel | str | None = None,
        enabled: bool | None = None,
    ) -> list[BrandRule]:
        conditions = ["brand_id = ?"]
        values: list[str | int] = [brand_id]
        if channel is not None:
            parsed_channel = BrandRuleChannel(channel)
            conditions.append("channel = ?")
            values.append(parsed_channel.value)
        if enabled is not None:
            conditions.append("enabled = ?")
            values.append(int(enabled))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT rule_json FROM brand_rules WHERE "
                + " AND ".join(conditions)
                + " ORDER BY priority DESC, updated_at DESC, rule_id DESC",
                values,
            ).fetchall()
        return [BrandRule.model_validate_json(row["rule_json"]) for row in rows]

    def add_feedback(
        self,
        content_item_id: str,
        dimension: str,
        note: str,
        signal: str = "prefer",
        scope: str = "story",
    ) -> None:
        if dimension not in FEEDBACK_DIMENSIONS:
            raise ValueError(f"Unsupported feedback dimension: {dimension}")
        if signal not in FEEDBACK_SIGNALS:
            raise ValueError(f"Unsupported feedback signal: {signal}")
        if scope not in FEEDBACK_SCOPES:
            raise ValueError(f"Unsupported feedback scope: {scope}")
        cleaned = note.strip()
        if not cleaned:
            raise ValueError("Feedback note must not be empty.")
        if self.get_item(content_item_id) is None:
            raise KeyError(content_item_id)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO editorial_feedback "
                "(content_item_id, dimension, signal, scope, note, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (content_item_id, dimension, signal, scope, cleaned, _now()),
            )

    def list_feedback(self, content_item_id: str) -> list[dict[str, str | int]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT feedback_id, dimension, signal, scope, note, created_at "
                "FROM editorial_feedback WHERE content_item_id = ? "
                "ORDER BY feedback_id DESC",
                (content_item_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_feedback_signals(
        self, brand_id: str = DEFAULT_BRAND_ID
    ) -> list[dict[str, str | int]]:
        """Return traceable feedback for review in the Brand Brain learning inbox."""

        with self._connect() as connection:
            rows = connection.execute(
                "SELECT f.feedback_id, f.content_item_id, f.dimension, f.signal, "
                "f.scope, f.note, f.created_at, i.packet_json "
                "FROM editorial_feedback AS f "
                "JOIN editorial_items AS i ON i.content_item_id = f.content_item_id "
                "WHERE i.brand_id = ? ORDER BY f.feedback_id DESC",
                (brand_id,),
            ).fetchall()
        signals = []
        for row in rows:
            item = dict(row)
            packet = EditorialPacket.model_validate_json(item.pop("packet_json"))
            item["article_type"] = packet.brief.article_type.value
            item["story_title"] = packet.brief.working_title
            signals.append(item)
        return signals

    def get_feedback_signal(self, feedback_id: int) -> dict[str, str | int] | None:
        return next(
            (
                signal
                for signal in self.list_feedback_signals()
                if signal["feedback_id"] == feedback_id
            ),
            None,
        )

    def save_draft(self, draft: ArticleDraft) -> None:
        if self.get_item(draft.content_item_id) is None:
            raise KeyError(draft.content_item_id)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO editorial_drafts "
                "(draft_id, content_item_id, draft_json, created_at) VALUES (?, ?, ?, ?)",
                (
                    draft.draft_id,
                    draft.content_item_id,
                    draft.model_dump_json(),
                    draft.created_at.isoformat(),
                ),
            )
            connection.execute(
                "UPDATE editorial_items SET status = 'selected', updated_at = ? "
                "WHERE content_item_id = ? AND status = 'needs_revision'",
                (_now(), draft.content_item_id),
            )

    def save_edited_draft(self, draft: ArticleDraft, parent_draft_id: str) -> None:
        """Save a human-edited version and invalidate any approval on its parent."""

        latest = self.get_latest_draft(draft.content_item_id)
        if latest is None or latest.draft_id != parent_draft_id:
            raise ValueError("The draft changed while it was being edited. Reload and try again.")
        if draft.parent_draft_id != parent_draft_id:
            raise ValueError("Edited draft lineage does not match its parent draft.")
        delivery = self.get_wordpress_delivery(parent_draft_id)
        if delivery and delivery.status == WordPressDeliveryStatus.DRAFT_CREATED:
            raise ValueError(
                "This version already has a WordPress draft. Edit it in WordPress or start a new story."
            )
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO editorial_drafts "
                "(draft_id, content_item_id, draft_json, created_at) VALUES (?, ?, ?, ?)",
                (
                    draft.draft_id,
                    draft.content_item_id,
                    draft.model_dump_json(),
                    draft.created_at.isoformat(),
                ),
            )
            cursor = connection.execute(
                "UPDATE editorial_items SET status = 'selected', updated_at = ? "
                "WHERE content_item_id = ?",
                (_now(), draft.content_item_id),
            )
        if cursor.rowcount != 1:
            raise KeyError(draft.content_item_id)

    def get_latest_draft(self, content_item_id: str) -> ArticleDraft | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT draft_json FROM editorial_drafts WHERE content_item_id = ? "
                "ORDER BY created_at DESC, draft_id DESC LIMIT 1",
                (content_item_id,),
            ).fetchone()
        return ArticleDraft.model_validate_json(row["draft_json"]) if row else None

    def list_drafts(self, content_item_id: str) -> list[ArticleDraft]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT draft_json FROM editorial_drafts WHERE content_item_id = ? "
                "ORDER BY created_at DESC, draft_id DESC",
                (content_item_id,),
            ).fetchall()
        return [ArticleDraft.model_validate_json(row["draft_json"]) for row in rows]

    def list_confirmed_required_facts(self, draft_id: str) -> set[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT fact_text FROM draft_fact_confirmations WHERE draft_id = ?",
                (draft_id,),
            ).fetchall()
        return {row["fact_text"] for row in rows}

    def confirm_required_facts(
        self,
        content_item_id: str,
        draft_id: str,
        facts: list[str],
    ) -> None:
        """Record human fact checks for the exact latest draft version."""

        record = self.get_item(content_item_id)
        latest = self.get_latest_draft(content_item_id)
        if record is None:
            raise KeyError(content_item_id)
        if latest is None or latest.draft_id != draft_id:
            raise ValueError("Facts can only be confirmed for the latest draft version.")
        allowed = set(record.packet.brief.required_facts)
        unknown = set(facts) - allowed
        if unknown:
            raise ValueError("A submitted fact is not part of this editorial brief.")
        with self._connect() as connection:
            connection.executemany(
                "INSERT INTO draft_fact_confirmations (draft_id, fact_text, confirmed_at) "
                "VALUES (?, ?, ?) ON CONFLICT(draft_id, fact_text) DO NOTHING",
                [(draft_id, fact, _now()) for fact in dict.fromkeys(facts)],
            )

    def create_social_campaign(
        self, campaign: SocialCampaign, posts: list[SocialPostDraft]
    ) -> SocialCampaign:
        """Persist one complete campaign for the exact latest approved article."""

        record = self.get_item(campaign.content_item_id)
        draft = self.get_latest_draft(campaign.content_item_id)
        decision = self.get_latest_decision(campaign.content_item_id)
        if record is None:
            raise KeyError(campaign.content_item_id)
        if (
            draft is None
            or draft.draft_id != campaign.article_draft_id
            or record.status != "approved"
            or decision is None
            or decision.draft_id != draft.draft_id
            or decision.outcome != DecisionOutcome.APPROVED
        ):
            raise ValueError("Social campaigns require the exact latest approved article.")
        if self.get_social_campaign(campaign.article_draft_id):
            raise ValueError("A social campaign already exists for this article version.")
        if {post.platform for post in posts} != set(SocialPlatform) or len(posts) != 3:
            raise ValueError("A campaign requires one draft for each social platform.")
        for post in posts:
            if (
                post.campaign_id != campaign.campaign_id
                or post.content_item_id != campaign.content_item_id
                or post.article_draft_id != campaign.article_draft_id
                or post.version != 1
                or post.parent_post_id is not None
            ):
                raise ValueError("Social draft does not belong to this campaign.")
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO social_campaigns "
                "(campaign_id, content_item_id, article_draft_id, campaign_json, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    campaign.campaign_id,
                    campaign.content_item_id,
                    campaign.article_draft_id,
                    campaign.model_dump_json(),
                    campaign.created_at.isoformat(),
                ),
            )
            connection.executemany(
                "INSERT INTO social_post_drafts "
                "(post_id, campaign_id, content_item_id, article_draft_id, platform, "
                "version, post_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        post.post_id,
                        post.campaign_id,
                        post.content_item_id,
                        post.article_draft_id,
                        post.platform.value,
                        post.version,
                        post.model_dump_json(),
                        post.created_at.isoformat(),
                    )
                    for post in posts
                ],
            )
        return campaign

    def get_social_campaign(self, article_draft_id: str) -> SocialCampaign | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT campaign_json FROM social_campaigns WHERE article_draft_id = ?",
                (article_draft_id,),
            ).fetchone()
        return SocialCampaign.model_validate_json(row["campaign_json"]) if row else None

    def set_social_campaign_public_url(
        self, campaign_id: str, public_article_url: str
    ) -> SocialCampaign:
        campaign = self.get_social_campaign_for_id(campaign_id)
        if campaign is None:
            raise KeyError(campaign_id)
        with self._connect() as connection:
            delivery_count = connection.execute(
                "SELECT COUNT(*) AS total FROM buffer_deliveries WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()["total"]
        if (
            campaign.public_article_url is not None
            and str(campaign.public_article_url) != public_article_url
            and delivery_count
        ):
            raise ValueError(
                "The public URL cannot change after Buffer delivery has started."
            )
        campaign = SocialCampaign.model_validate(
            {**campaign.model_dump(), "public_article_url": public_article_url}
        )
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE social_campaigns SET campaign_json = ? WHERE campaign_id = ?",
                (campaign.model_dump_json(), campaign_id),
            )
        if cursor.rowcount != 1:
            raise KeyError(campaign_id)
        return campaign

    def list_latest_social_posts(self, campaign_id: str) -> list[SocialPostDraft]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT post_json FROM social_post_drafts posts "
                "WHERE campaign_id = ? AND version = ("
                "SELECT MAX(version) FROM social_post_drafts latest "
                "WHERE latest.campaign_id = posts.campaign_id "
                "AND latest.platform = posts.platform) ORDER BY platform",
                (campaign_id,),
            ).fetchall()
        return [SocialPostDraft.model_validate_json(row["post_json"]) for row in rows]

    def list_social_post_versions(
        self, campaign_id: str, platform: SocialPlatform
    ) -> list[SocialPostDraft]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT post_json FROM social_post_drafts "
                "WHERE campaign_id = ? AND platform = ? ORDER BY version DESC",
                (campaign_id, platform.value),
            ).fetchall()
        return [SocialPostDraft.model_validate_json(row["post_json"]) for row in rows]

    def save_edited_social_post(
        self, post: SocialPostDraft, parent_post_id: str
    ) -> SocialPostDraft:
        versions = self.list_social_post_versions(post.campaign_id, post.platform)
        latest = versions[0] if versions else None
        if latest is None or latest.post_id != parent_post_id:
            raise ValueError(
                "The social draft changed while it was being edited. Reload and try again."
            )
        delivery = self.get_buffer_delivery(parent_post_id)
        if delivery and delivery.status in {
            BufferDeliveryStatus.PENDING,
            BufferDeliveryStatus.UNCERTAIN,
        }:
            raise ValueError(
                "This version may already be in Buffer and cannot be replaced until reconciled."
            )
        if (
            post.parent_post_id != parent_post_id
            or post.version != latest.version + 1
            or post.article_draft_id != latest.article_draft_id
            or post.content_item_id != latest.content_item_id
            or post.status != SocialPostStatus.DRAFT
        ):
            raise ValueError("Edited social draft lineage is invalid.")
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO social_post_drafts "
                "(post_id, campaign_id, content_item_id, article_draft_id, platform, "
                "version, post_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    post.post_id,
                    post.campaign_id,
                    post.content_item_id,
                    post.article_draft_id,
                    post.platform.value,
                    post.version,
                    post.model_dump_json(),
                    post.created_at.isoformat(),
                ),
            )
        return post

    def set_social_post_status(
        self, post_id: str, status: SocialPostStatus
    ) -> SocialPostDraft:
        post = self._get_social_post(post_id)
        versions = self.list_social_post_versions(post.campaign_id, post.platform)
        if not versions or versions[0].post_id != post_id:
            raise ValueError("Only the latest social draft can receive a review decision.")
        delivery = self.get_buffer_delivery(post_id)
        if delivery and delivery.status in {
            BufferDeliveryStatus.PENDING,
            BufferDeliveryStatus.UNCERTAIN,
            BufferDeliveryStatus.DELIVERED,
        }:
            raise ValueError("Buffer delivery has started, so this review state is locked.")
        post.status = status
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE social_post_drafts SET post_json = ? WHERE post_id = ?",
                (post.model_dump_json(), post_id),
            )
        if cursor.rowcount != 1:
            raise KeyError(post_id)
        return post

    def add_social_feedback(
        self,
        content_item_id: str,
        campaign_id: str,
        platform: SocialPlatform,
        signal: PreferenceSignal,
        note: str,
    ) -> None:
        cleaned = note.strip()
        if not cleaned:
            raise ValueError("Social feedback must not be empty.")
        campaign = self.get_social_campaign_for_id(campaign_id)
        if campaign is None or campaign.content_item_id != content_item_id:
            raise ValueError("Social campaign does not belong to this story.")
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO social_feedback "
                "(content_item_id, campaign_id, platform, signal, note, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    content_item_id,
                    campaign_id,
                    platform.value,
                    signal.value,
                    cleaned,
                    _now(),
                ),
            )

    def list_social_preferences(self) -> dict[str, list[str]]:
        """Return transparent, reusable feedback grouped by social platform."""

        with self._connect() as connection:
            rows = connection.execute(
                "SELECT platform, signal, note FROM social_feedback "
                "ORDER BY feedback_id DESC"
            ).fetchall()
        preferences = {platform.value: [] for platform in SocialPlatform}
        for rule in self.list_brand_rules(enabled=True):
            if rule.channel.value not in preferences:
                continue
            instruction = f"{rule.signal.value.title()}: {rule.instruction}"
            preferences[rule.channel.value].append(instruction)
        for row in rows:
            instruction = f"{str(row['signal']).title()}: {row['note']}"
            platform_preferences = preferences[row["platform"]]
            if (
                instruction not in platform_preferences
                and len(platform_preferences) < SOCIAL_PREFERENCE_LIMIT_PER_PLATFORM
            ):
                platform_preferences.append(instruction)
        return preferences

    def get_social_campaign_for_id(self, campaign_id: str) -> SocialCampaign | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT campaign_json FROM social_campaigns WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()
        return SocialCampaign.model_validate_json(row["campaign_json"]) if row else None

    def _get_social_post(self, post_id: str) -> SocialPostDraft:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT post_json FROM social_post_drafts WHERE post_id = ?",
                (post_id,),
            ).fetchone()
        if row is None:
            raise KeyError(post_id)
        return SocialPostDraft.model_validate_json(row["post_json"])

    def begin_buffer_delivery(
        self,
        post: SocialPostDraft,
        channel_id: str,
        mode: BufferDeliveryMode,
        final_text: str,
        scheduled_for: datetime | None,
    ) -> BufferDelivery:
        """Persist an exact delivery intent before any network mutation."""

        campaign = self.get_social_campaign_for_id(post.campaign_id)
        versions = self.list_social_post_versions(post.campaign_id, post.platform)
        if campaign is None or campaign.public_article_url is None:
            raise ValueError("Confirm the public article URL before Buffer delivery.")
        if not versions or versions[0].post_id != post.post_id:
            raise ValueError("Only the latest social version can be delivered to Buffer.")
        if post.status != SocialPostStatus.APPROVED:
            raise ValueError("Approve this exact social version before Buffer delivery.")
        existing = self.get_buffer_delivery(post.post_id)
        if existing:
            if existing.status == BufferDeliveryStatus.DELIVERED:
                return existing
            if existing.status in {
                BufferDeliveryStatus.PENDING,
                BufferDeliveryStatus.UNCERTAIN,
            }:
                raise ValueError(
                    "This delivery may already exist in Buffer. "
                    "Reconcile it there before doing anything else."
                )
            if (
                existing.channel_id != channel_id
                or existing.mode != mode
                or existing.final_text != final_text
                or existing.scheduled_for != scheduled_for
            ):
                raise ValueError(
                    "The failed delivery intent changed. Save a new social version before retrying."
                )
            existing.status = BufferDeliveryStatus.PENDING
            existing.attempts += 1
            existing.error_message = ""
            existing.updated_at = datetime.now(timezone.utc)
            self._save_buffer_delivery(existing)
            return existing
        delivery = BufferDelivery(
            content_item_id=post.content_item_id,
            campaign_id=post.campaign_id,
            post_id=post.post_id,
            platform=post.platform,
            channel_id=channel_id,
            mode=mode,
            final_text=final_text,
            scheduled_for=scheduled_for,
        )
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO buffer_deliveries "
                "(delivery_id, content_item_id, campaign_id, post_id, status, delivery_json, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    delivery.delivery_id,
                    delivery.content_item_id,
                    delivery.campaign_id,
                    delivery.post_id,
                    delivery.status.value,
                    delivery.model_dump_json(),
                    delivery.created_at.isoformat(),
                    delivery.updated_at.isoformat(),
                ),
            )
        return delivery

    def get_buffer_delivery(self, post_id: str) -> BufferDelivery | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT delivery_json FROM buffer_deliveries WHERE post_id = ?",
                (post_id,),
            ).fetchone()
        return BufferDelivery.model_validate_json(row["delivery_json"]) if row else None

    def complete_buffer_delivery(
        self,
        delivery_id: str,
        buffer_post_id: str,
        buffer_status: str,
        buffer_due_at: datetime | None,
    ) -> BufferDelivery:
        delivery = self._get_buffer_delivery_by_id(delivery_id)
        delivery.status = BufferDeliveryStatus.DELIVERED
        delivery.buffer_post_id = buffer_post_id
        delivery.buffer_status = buffer_status
        delivery.buffer_due_at = buffer_due_at
        delivery.error_message = ""
        delivery.updated_at = datetime.now(timezone.utc)
        self._save_buffer_delivery(delivery)
        return delivery

    def fail_buffer_delivery(
        self, delivery_id: str, error_message: str
    ) -> BufferDelivery:
        delivery = self._get_buffer_delivery_by_id(delivery_id)
        delivery.status = BufferDeliveryStatus.FAILED
        delivery.error_message = error_message.strip()[:500]
        delivery.updated_at = datetime.now(timezone.utc)
        self._save_buffer_delivery(delivery)
        return delivery

    def mark_buffer_delivery_uncertain(
        self, delivery_id: str, error_message: str
    ) -> BufferDelivery:
        delivery = self._get_buffer_delivery_by_id(delivery_id)
        delivery.status = BufferDeliveryStatus.UNCERTAIN
        delivery.error_message = error_message.strip()[:500]
        delivery.updated_at = datetime.now(timezone.utc)
        self._save_buffer_delivery(delivery)
        return delivery

    def _get_buffer_delivery_by_id(self, delivery_id: str) -> BufferDelivery:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT delivery_json FROM buffer_deliveries WHERE delivery_id = ?",
                (delivery_id,),
            ).fetchone()
        if row is None:
            raise KeyError(delivery_id)
        return BufferDelivery.model_validate_json(row["delivery_json"])

    def _save_buffer_delivery(self, delivery: BufferDelivery) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE buffer_deliveries SET status = ?, delivery_json = ?, updated_at = ? "
                "WHERE delivery_id = ?",
                (
                    delivery.status.value,
                    delivery.model_dump_json(),
                    delivery.updated_at.isoformat(),
                    delivery.delivery_id,
                ),
            )
        if cursor.rowcount != 1:
            raise KeyError(delivery.delivery_id)

    def record_decision(self, decision: DraftDecision) -> None:
        latest_draft = self.get_latest_draft(decision.content_item_id)
        if latest_draft is None:
            raise ValueError("No draft exists for this editorial item.")
        if latest_draft.draft_id != decision.draft_id:
            raise ValueError("Only the latest draft can receive an editorial decision.")
        if decision.quality_report.draft_id != decision.draft_id:
            raise ValueError("Quality report does not belong to this draft.")
        if (
            decision.outcome == DecisionOutcome.READY_FOR_APPROVAL
            and not decision.quality_report.can_approve
        ):
            raise ValueError("Draft has blocking quality failures and cannot enter approval.")
        if decision.outcome == DecisionOutcome.APPROVED and not decision.quality_report.can_approve:
            raise ValueError("Draft has blocking quality failures and cannot be approved.")
        if decision.outcome == DecisionOutcome.NEEDS_REVISION and not decision.notes.strip():
            raise ValueError("Revision requests require editor notes.")

        with self._connect() as connection:
            connection.execute(
                "INSERT INTO editorial_decisions "
                "(decision_id, content_item_id, draft_id, outcome, decision_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    decision.decision_id,
                    decision.content_item_id,
                    decision.draft_id,
                    decision.outcome.value,
                    decision.model_dump_json(),
                    decision.created_at.isoformat(),
                ),
            )
            connection.execute(
                "UPDATE editorial_items SET status = ?, updated_at = ? "
                "WHERE content_item_id = ?",
                (decision.outcome.value, _now(), decision.content_item_id),
            )

    def get_latest_decision(self, content_item_id: str) -> DraftDecision | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT decision_json FROM editorial_decisions WHERE content_item_id = ? "
                "ORDER BY created_at DESC, decision_id DESC LIMIT 1",
                (content_item_id,),
            ).fetchone()
        return DraftDecision.model_validate_json(row["decision_json"]) if row else None

    def list_decisions(self, content_item_id: str) -> list[DraftDecision]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT decision_json FROM editorial_decisions WHERE content_item_id = ? "
                "ORDER BY created_at DESC, decision_id DESC",
                (content_item_id,),
            ).fetchall()
        return [DraftDecision.model_validate_json(row["decision_json"]) for row in rows]

    def latest_revision_notes(self, content_item_id: str) -> list[str]:
        decision = self.get_latest_decision(content_item_id)
        latest_draft = self.get_latest_draft(content_item_id)
        if (
            decision
            and latest_draft
            and decision.outcome == DecisionOutcome.NEEDS_REVISION
            and decision.draft_id == latest_draft.draft_id
        ):
            return [decision.notes]
        return []

    def create_discord_request(
        self, request: DiscordApprovalRequest
    ) -> DiscordApprovalRequest:
        latest_draft = self.get_latest_draft(request.content_item_id)
        if latest_draft is None or latest_draft.draft_id != request.draft_id:
            raise ValueError("Only the latest draft can be sent to Discord.")
        record = self.get_item(request.content_item_id)
        if record is None:
            raise KeyError(request.content_item_id)
        if record.status not in {"selected", "ready_for_approval", "approved"}:
            raise ValueError("Only a reviewed draft can be shared with Discord.")
        pending = self.get_pending_discord_request(request.content_item_id)
        if pending is not None:
            raise ValueError("This draft already has a pending Discord approval request.")
        now = _now()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO discord_approval_requests "
                "(request_id, content_item_id, draft_id, status, request_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    request.request_id,
                    request.content_item_id,
                    request.draft_id,
                    request.status.value,
                    request.model_dump_json(),
                    request.requested_at.isoformat(),
                    now,
                ),
            )
        return request

    def update_discord_delivery(
        self, request_id: str, channel_id: str, message_id: str
    ) -> DiscordApprovalRequest:
        request = self.get_discord_request(request_id)
        if request is None:
            raise KeyError(request_id)
        request.channel_id = channel_id
        request.message_id = message_id
        self._save_discord_request(request)
        return request

    def mark_discord_delivery_failed(self, request_id: str) -> None:
        request = self.get_discord_request(request_id)
        if request is None:
            raise KeyError(request_id)
        request.status = DiscordApprovalStatus.DELIVERY_FAILED
        request.resolved_at = datetime.now(timezone.utc)
        self._save_discord_request(request)

    def get_discord_request(self, request_id: str) -> DiscordApprovalRequest | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT request_json FROM discord_approval_requests WHERE request_id = ?",
                (request_id,),
            ).fetchone()
        return DiscordApprovalRequest.model_validate_json(row["request_json"]) if row else None

    def get_latest_discord_request(
        self, content_item_id: str
    ) -> DiscordApprovalRequest | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT request_json FROM discord_approval_requests WHERE content_item_id = ? "
                "ORDER BY created_at DESC, request_id DESC LIMIT 1",
                (content_item_id,),
            ).fetchone()
        return DiscordApprovalRequest.model_validate_json(row["request_json"]) if row else None

    def get_pending_discord_request(
        self, content_item_id: str
    ) -> DiscordApprovalRequest | None:
        request = self.get_latest_discord_request(content_item_id)
        return request if request and request.status == DiscordApprovalStatus.PENDING else None

    def resolve_discord_request(
        self,
        request_id: str,
        status: DiscordApprovalStatus,
        actor_id: str,
        actor_name: str,
        revision_notes: str = "",
    ) -> DiscordApprovalRequest:
        request = self.get_discord_request(request_id)
        if request is None:
            raise KeyError(request_id)
        if request.status != DiscordApprovalStatus.PENDING:
            raise ValueError("This Discord approval request has already been resolved.")
        if status not in {
            DiscordApprovalStatus.ENDORSED,
            DiscordApprovalStatus.REVISION_SUGGESTED,
        }:
            raise ValueError("Unsupported Discord response.")
        cleaned_notes = revision_notes.strip()
        if status == DiscordApprovalStatus.REVISION_SUGGESTED and not cleaned_notes:
            raise ValueError("Suggested revisions require notes.")
        latest_draft = self.get_latest_draft(request.content_item_id)
        if latest_draft is None or latest_draft.draft_id != request.draft_id:
            raise ValueError("This approval request no longer refers to the latest draft.")
        request.status = status
        request.resolved_by_id = actor_id
        request.resolved_by_name = actor_name
        request.revision_notes = cleaned_notes
        request.resolved_at = datetime.now(timezone.utc)
        with self._connect() as connection:
            connection.execute(
                "UPDATE discord_approval_requests SET status = ?, request_json = ?, updated_at = ? "
                "WHERE request_id = ?",
                (status.value, request.model_dump_json(), _now(), request_id),
            )
        return request

    def _save_discord_request(self, request: DiscordApprovalRequest) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE discord_approval_requests SET status = ?, request_json = ?, updated_at = ? "
                "WHERE request_id = ?",
                (request.status.value, request.model_dump_json(), _now(), request.request_id),
            )
        if cursor.rowcount != 1:
            raise KeyError(request.request_id)

    def begin_wordpress_delivery(
        self, content_item_id: str, draft_id: str
    ) -> WordPressDelivery:
        record = self.get_item(content_item_id)
        latest_draft = self.get_latest_draft(content_item_id)
        latest_decision = self.get_latest_decision(content_item_id)
        if record is None:
            raise KeyError(content_item_id)
        if latest_draft is None or latest_draft.draft_id != draft_id:
            raise ValueError("Only the latest draft can be delivered to WordPress.")
        if (
            record.status != "approved"
            or latest_decision is None
            or latest_decision.draft_id != draft_id
            or latest_decision.outcome != DecisionOutcome.APPROVED
        ):
            raise ValueError("The exact draft must be approved in the workspace first.")
        existing = self.get_wordpress_delivery(draft_id)
        if existing:
            if existing.status == WordPressDeliveryStatus.DRAFT_CREATED:
                return existing
            existing.status = WordPressDeliveryStatus.PENDING
            existing.attempts += 1
            existing.error_message = ""
            existing.updated_at = datetime.now(timezone.utc)
            self._save_wordpress_delivery(existing)
            return existing
        delivery = WordPressDelivery(
            content_item_id=content_item_id,
            draft_id=draft_id,
        )
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO wordpress_deliveries "
                "(delivery_id, content_item_id, draft_id, status, delivery_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    delivery.delivery_id,
                    delivery.content_item_id,
                    delivery.draft_id,
                    delivery.status.value,
                    delivery.model_dump_json(),
                    delivery.created_at.isoformat(),
                    delivery.updated_at.isoformat(),
                ),
            )
        return delivery

    def get_wordpress_delivery(self, draft_id: str) -> WordPressDelivery | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT delivery_json FROM wordpress_deliveries WHERE draft_id = ?",
                (draft_id,),
            ).fetchone()
        return WordPressDelivery.model_validate_json(row["delivery_json"]) if row else None

    def complete_wordpress_delivery(
        self,
        delivery_id: str,
        post_id: int,
        post_url: str,
        editor_url: str,
    ) -> WordPressDelivery:
        delivery = self._get_wordpress_delivery_by_id(delivery_id)
        delivery.status = WordPressDeliveryStatus.DRAFT_CREATED
        delivery.post_id = post_id
        delivery.post_url = post_url
        delivery.editor_url = editor_url
        delivery.error_message = ""
        delivery.updated_at = datetime.now(timezone.utc)
        self._save_wordpress_delivery(delivery)
        return delivery

    def fail_wordpress_delivery(
        self, delivery_id: str, error_message: str
    ) -> WordPressDelivery:
        delivery = self._get_wordpress_delivery_by_id(delivery_id)
        delivery.status = WordPressDeliveryStatus.FAILED
        delivery.error_message = error_message.strip()[:500]
        delivery.updated_at = datetime.now(timezone.utc)
        self._save_wordpress_delivery(delivery)
        return delivery

    def _get_wordpress_delivery_by_id(self, delivery_id: str) -> WordPressDelivery:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT delivery_json FROM wordpress_deliveries WHERE delivery_id = ?",
                (delivery_id,),
            ).fetchone()
        if row is None:
            raise KeyError(delivery_id)
        return WordPressDelivery.model_validate_json(row["delivery_json"])

    def _save_wordpress_delivery(self, delivery: WordPressDelivery) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE wordpress_deliveries SET status = ?, delivery_json = ?, updated_at = ? "
                "WHERE delivery_id = ?",
                (
                    delivery.status.value,
                    delivery.model_dump_json(),
                    delivery.updated_at.isoformat(),
                    delivery.delivery_id,
                ),
            )
        if cursor.rowcount != 1:
            raise KeyError(delivery.delivery_id)

    def list_applicable_feedback(
        self,
        article_type: str | None = None,
        content_item_id: str | None = None,
    ) -> list[dict[str, str | int]]:
        """Return explicit feedback that applies to a future writing assignment."""

        with self._connect() as connection:
            rows = connection.execute(
                "SELECT f.feedback_id, f.content_item_id, f.dimension, f.signal, "
                "f.scope, f.note, f.created_at, i.packet_json "
                "FROM editorial_feedback AS f "
                "JOIN editorial_items AS i ON i.content_item_id = f.content_item_id "
                "ORDER BY f.feedback_id DESC"
            ).fetchall()

        applicable: list[dict[str, str | int]] = []
        for row in rows:
            source_type = EditorialPacket.model_validate_json(row["packet_json"]).brief.article_type.value
            scope = row["scope"]
            applies = scope == "global"
            applies = applies or (
                scope == "article_type" and article_type is not None and source_type == article_type
            )
            applies = applies or (
                scope == "story"
                and content_item_id is not None
                and row["content_item_id"] == content_item_id
            )
            if applies:
                item = dict(row)
                item.pop("packet_json")
                item["article_type"] = source_type
                applicable.append(item)
        return applicable

    @staticmethod
    def _validate_status(status: str) -> None:
        if status not in EDITORIAL_STATUSES:
            raise ValueError(f"Unsupported editorial status: {status}")

    @staticmethod
    def _record(row: sqlite3.Row) -> EditorialItemRecord:
        packet = EditorialPacket.model_validate(json.loads(row["packet_json"]))
        return EditorialItemRecord(
            packet=packet,
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            brand_id=row["brand_id"] if "brand_id" in row.keys() else DEFAULT_BRAND_ID,
        )
