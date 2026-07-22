import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.editorial.source_control import SourceControlService, parse_terms
from src.editorial.web import create_app


def _config(tmp_path: Path) -> Path:
    source = (
        Path(__file__).resolve().parents[1]
        / "data"
        / "config.codequest.example.json"
    )
    destination = tmp_path / "config.json"
    shutil.copyfile(source, destination)
    return destination


def test_source_control_preserves_placeholders_and_creates_backup(tmp_path) -> None:
    path = _config(tmp_path)
    service = SourceControlService(path)

    service.update_filtering(
        score_threshold=7.5,
        time_window_hours=72,
        max_items=12,
        include_keywords="AI agents, Python\nAI agents",
        exclude_keywords="crypto price, celebrity",
        default_group_limit=3,
    )

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["ai"]["base_url"] == "${OLLAMA_BASE_URL}"
    assert raw["filtering"]["include_keywords"] == ["AI agents", "Python"]
    assert raw["filtering"]["exclude_keywords"] == ["crypto price", "celebrity"]
    assert path.with_suffix(".json.bak").is_file()


def test_source_control_adds_updates_and_deduplicates_rss(tmp_path) -> None:
    service = SourceControlService(_config(tmp_path))

    service.add_rss(
        name="Python Insider",
        url="https://blog.python.org/feeds/posts/default",
        category="python",
        enabled=True,
    )
    service.update_rss(
        3,
        name="Python Releases",
        url="https://blog.python.org/feeds/posts/default",
        category="developer-tools",
        enabled=False,
    )

    config = service.load()
    assert config.sources.rss[3].name == "Python Releases"
    assert config.sources.rss[3].enabled is False
    with pytest.raises(ValueError, match="already configured"):
        service.add_rss(
            name="Duplicate",
            url="https://blog.python.org/feeds/posts/default/",
            category="python",
            enabled=True,
        )


def test_source_control_workspace_edits_real_horizon_config(tmp_path) -> None:
    config_path = _config(tmp_path)
    client = TestClient(
        create_app(tmp_path / "editorial.sqlite3", source_config_path=config_path)
    )

    page = client.get("/sources")
    policy = client.post(
        "/sources/filtering",
        data={
            "score_threshold": "8",
            "time_window_hours": "36",
            "max_items": "7",
            "default_group_limit": "2",
            "include_keywords": "developer tools, Python",
            "exclude_keywords": "token price",
        },
        follow_redirects=False,
    )
    group = client.post(
        "/sources/groups",
        data={
            "key": "security-core",
            "name": "Security core",
            "categories": "security, supply-chain",
            "limit": "3",
        },
        follow_redirects=False,
    )

    assert page.status_code == 200
    assert "Shape what Horizon" in page.text
    assert "OpenAI News" in page.text
    assert policy.headers["location"].startswith("/sources?tab=topics")
    assert group.status_code == 303
    topics = client.get("/sources?tab=topics")
    assert "developer tools, Python" in topics.text
    assert "Security core" in topics.text
    assert "supply-chain" in topics.text


def test_source_control_rejects_invalid_group_and_missing_configuration(tmp_path) -> None:
    missing_client = TestClient(
        create_app(
            tmp_path / "editorial.sqlite3",
            source_config_path=tmp_path / "missing.json",
        )
    )
    assert "Discovery configuration" in missing_client.get("/sources").text
    assert parse_terms("AI, ai, Python\n") == ["AI", "Python"]

    service = SourceControlService(_config(tmp_path))
    with pytest.raises(ValueError, match="lowercase"):
        service.update_category_group(
            key="../../bad", name="Bad", categories="ai", limit=1
        )
