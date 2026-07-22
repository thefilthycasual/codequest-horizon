import asyncio
import base64
import json

import httpx
from fastapi.testclient import TestClient

from src.editorial.image_generation import (
    GeneratedImageResult,
    ImageGenerationConfig,
    OpenAIImageGenerator,
    build_featured_image_prompt,
)
from src.editorial.store import EditorialStore
from src.editorial.web import create_app

from test_editorial_wordpress import _StubPublisher, _approved_store


PNG_BYTES = b"\x89PNG\r\n\x1a\nreview-first-image"


class _StubImageGenerator:
    provider_name = "openai"
    model_name = "test-image-model"

    def __init__(self):
        self.requests = []

    async def generate(self, *, prompt, size, quality):
        self.requests.append((prompt, size, quality))
        return GeneratedImageResult(PNG_BYTES, "image/png", prompt)


def _ready_config() -> ImageGenerationConfig:
    return ImageGenerationConfig(
        provider="openai",
        model="test-image-model",
        api_key="test-key",
        enabled=True,
    )


def test_featured_image_prompt_is_article_and_brand_grounded(tmp_path) -> None:
    _store, _packet, draft = _approved_store(tmp_path / "editorial.sqlite3")
    prompt = build_featured_image_prompt(
        draft,
        creative_direction="Use a single orange pathway through modular developer tools.",
        style="editorial illustration",
    )

    assert draft.title in prompt
    assert draft.dek in prompt
    assert "orange pathway" in prompt
    assert "Do not add readable text" in prompt
    assert "Do not imitate a living artist" in prompt


def test_openai_image_adapter_uses_current_images_endpoint() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers["authorization"]
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"data": [{"b64_json": base64.b64encode(PNG_BYTES).decode()}]},
        )

    async def generate():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await OpenAIImageGenerator(_ready_config(), client).generate(
                prompt="A safe editorial technology illustration",
                size="1536x1024",
                quality="medium",
            )

    result = asyncio.run(generate())

    assert captured["url"] == "https://api.openai.com/v1/images/generations"
    assert captured["auth"] == "Bearer test-key"
    assert captured["payload"]["model"] == "test-image-model"
    assert captured["payload"]["output_format"] == "png"
    assert result.image_bytes == PNG_BYTES


def test_image_studio_keeps_candidate_local_until_explicit_wordpress_upload(
    tmp_path,
) -> None:
    db_path = tmp_path / "editorial.sqlite3"
    image_dir = tmp_path / "generated-images"
    store, packet, _draft = _approved_store(db_path)
    image_generator = _StubImageGenerator()
    wordpress = _StubPublisher()
    client = TestClient(
        create_app(
            db_path,
            wordpress_publisher_factory=lambda: wordpress,
            image_generator_factory=lambda: image_generator,
            image_config_factory=_ready_config,
            generated_image_dir=image_dir,
        )
    )
    item_id = packet.brief.content_item_id

    generated = client.post(
        f"/items/{item_id}/images/generate",
        data={
            "creative_direction": "Show a clear orange path through a toolchain.",
            "style": "editorial illustration",
            "size": "1536x1024",
            "quality": "medium",
        },
        follow_redirects=False,
    )

    assert generated.status_code == 303
    assets = store.list_generated_images(item_id)
    assert len(assets) == 1
    asset = assets[0]
    assert asset.wordpress_media_id is None
    assert wordpress.uploaded is None
    assert (image_dir / asset.filename).read_bytes() == PNG_BYTES
    image_response = client.get(f"/generated-images/{asset.asset_id}")
    assert image_response.status_code == 200
    assert image_response.content == PNG_BYTES
    page = client.get(f"/items/{item_id}?tab=delivery&channel=images")
    assert "AI IMAGE STUDIO" in page.text
    assert "Generated images stay local" in page.text
    assert asset.asset_id in page.text
    wordpress_page = client.get(f"/items/{item_id}?tab=delivery&channel=wordpress")
    assert "AI IMAGE STUDIO" not in wordpress_page.text
    assert "Images (1)" in wordpress_page.text

    uploaded = client.post(
        f"/items/{item_id}/images/{asset.asset_id}/wordpress",
        data={"alt_text": "An orange path connecting developer tools"},
        follow_redirects=False,
    )

    assert uploaded.status_code == 303
    assert wordpress.uploaded is not None
    assert wordpress.uploaded[4] == "An orange path connecting developer tools"
    settings = store.get_wordpress_publishing_settings(item_id)
    assert settings.featured_media_id == 31
    saved_asset = store.get_generated_image(asset.asset_id)
    assert saved_asset is not None
    assert saved_asset.wordpress_media_id == 31


def test_image_studio_is_disabled_without_explicit_enablement(tmp_path) -> None:
    db_path = tmp_path / "editorial.sqlite3"
    _store, packet, _draft = _approved_store(db_path)
    client = TestClient(
        create_app(
            db_path,
            image_config_factory=lambda: ImageGenerationConfig(enabled=False),
            generated_image_dir=tmp_path / "generated-images",
        )
    )

    page = client.get(
        f"/items/{packet.brief.content_item_id}?tab=delivery&channel=images"
    )

    assert page.status_code == 200
    assert "Image generation is off" in page.text
    assert "Generate one candidate" in page.text
    assert "disabled" in page.text
