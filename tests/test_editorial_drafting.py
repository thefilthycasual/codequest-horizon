import asyncio
import json

import pytest

from src.ai.client import AIClient
from src.editorial.drafting import (
    ArticleDraftGenerator,
    DraftGenerationError,
    build_draft_prompt,
    create_ollama_cloud_draft_generator,
)
from src.editorial.preferences import build_preference_profile
from src.editorial.store import EditorialStore

from test_editorial_workspace import _packet


class FakeWriter(AIClient):
    def __init__(self, response: dict | str):
        self.response = response
        self.calls = []

    async def complete(self, system, user, temperature=None, max_tokens=None) -> str:
        self.calls.append(
            {
                "system": system,
                "user": user,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        return self.response if isinstance(self.response, str) else json.dumps(self.response)


def _response(source_ids=None):
    return {
        "title": "What the public beta means for code review",
        "dek": "The new tool focuses on review and local developer workflows.",
        "sections": [
            {
                "heading": "What changed",
                "paragraphs": [
                    {
                        "text": "Example released a public beta focused on code review.",
                        "source_ids": source_ids or ["S1"],
                    }
                ],
            }
        ],
    }


def test_generator_grounds_draft_and_snapshots_preferences(tmp_path) -> None:
    store = EditorialStore(tmp_path / "editorial.sqlite3")
    packet = _packet()
    store.save_packet(packet)
    store.add_feedback(
        packet.brief.content_item_id,
        "tone",
        "Avoid generic hype.",
        signal="avoid",
        scope="global",
    )
    profile = build_preference_profile(store, packet.brief.article_type)
    writer = FakeWriter(_response())

    draft = asyncio.run(
        ArticleDraftGenerator(writer, "writer-cloud").generate(
            packet,
            profile,
            ["Make the independent test limitation explicit."],
        )
    )
    store.save_draft(draft)

    assert draft.source_map["S1"] == packet.evidence.sources[0].url
    assert draft.preference_rules[0].instruction == "Avoid generic hype."
    assert draft.revision_notes == ["Make the independent test limitation explicit."]
    assert store.get_latest_draft(packet.brief.content_item_id).draft_id == draft.draft_id
    assert "Avoid generic hype." in writer.calls[0]["user"]
    assert '"id": "S1"' in writer.calls[0]["user"]
    assert "Make the independent test limitation explicit." in writer.calls[0]["user"]
    assert writer.calls[0]["temperature"] == 0.2


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (_response(["S99"]), "unknown evidence IDs"),
        (_response(["S2"]), "primary source"),
        ("not json", "invalid draft structure"),
    ],
)
def test_generator_rejects_untraceable_or_invalid_output(response, message, tmp_path) -> None:
    packet = _packet()
    writer = FakeWriter(response)

    with pytest.raises(DraftGenerationError, match=message):
        asyncio.run(
            ArticleDraftGenerator(writer, "writer-cloud").generate(
                packet,
                build_preference_profile(
                    EditorialStore(tmp_path / "editorial.sqlite3"),
                    packet.brief.article_type,
                ),
            )
        )


def test_prompt_marks_source_material_as_evidence(tmp_path) -> None:
    packet = _packet()
    profile = build_preference_profile(EditorialStore(tmp_path / "editorial.sqlite3"))

    prompt = build_draft_prompt(packet, profile)

    assert "EDITORIAL ASSIGNMENT" in prompt
    assert "WRITING PREFERENCES" in prompt
    assert "EVIDENCE" in prompt
    assert str(packet.evidence.sources[0].url) in prompt


def test_ollama_writer_requires_cloud_credentials(monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.setenv("OLLAMA_MODEL_WRITER", "writer")
    monkeypatch.setenv("OLLAMA_API_KEY", "secret")

    with pytest.raises(DraftGenerationError, match="HTTPS Ollama Cloud"):
        create_ollama_cloud_draft_generator()


def test_ollama_writer_accepts_https_cloud_config(monkeypatch) -> None:
    captured = {}
    fake_client = FakeWriter(_response())
    monkeypatch.setenv("OLLAMA_BASE_URL", "https://ollama.example")
    monkeypatch.setenv("OLLAMA_MODEL_WRITER", "writer-cloud")
    monkeypatch.setenv("OLLAMA_API_KEY", "secret")

    def fake_create(config):
        captured["config"] = config
        return fake_client

    monkeypatch.setattr("src.editorial.drafting.create_ai_client", fake_create)

    generator = create_ollama_cloud_draft_generator()

    assert generator.model_name == "writer-cloud"
    assert captured["config"].base_url == "https://ollama.example"
