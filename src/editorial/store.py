"""Small SQLite repository for CodeQuest editorial state."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .models import (
    ArticleDraft,
    DecisionOutcome,
    DraftDecision,
    DiscordApprovalRequest,
    DiscordApprovalStatus,
    EditorialPacket,
    PreferenceScope,
    PreferenceSignal,
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class EditorialItemRecord:
    packet: EditorialPacket
    status: str
    created_at: str
    updated_at: str


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

    def _initialize(self) -> None:
        with self._connect() as connection:
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
        if decision and decision.outcome == DecisionOutcome.NEEDS_REVISION:
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
        )
