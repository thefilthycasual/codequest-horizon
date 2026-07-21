"""Small SQLite repository for CodeQuest editorial state."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .models import ArticleDraft, EditorialPacket, PreferenceScope, PreferenceSignal


EDITORIAL_STATUSES = (
    "candidate",
    "selected",
    "needs_revision",
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

    def get_item(self, content_item_id: str) -> EditorialItemRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM editorial_items WHERE content_item_id = ?",
                (content_item_id,),
            ).fetchone()
        return self._record(row) if row else None

    def set_status(self, content_item_id: str, status: str) -> None:
        self._validate_status(status)
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
