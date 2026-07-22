"""Review-first featured-image generation behind a small provider boundary."""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

import httpx

from .models import ArticleDraft


SUPPORTED_IMAGE_SIZES = {"1536x1024", "1024x1024", "1024x1536"}
SUPPORTED_IMAGE_QUALITIES = {"low", "medium", "high"}
SUPPORTED_IMAGE_STYLES = {
    "editorial illustration",
    "clean 3D illustration",
    "cinematic technology photograph",
    "minimal abstract composition",
}


class ImageGenerationError(ValueError):
    """Raised when image generation is unavailable or returns an unsafe result."""


@dataclass(frozen=True)
class ImageGenerationConfig:
    provider: str = "openai"
    model: str = "gpt-image-2"
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    enabled: bool = False

    @classmethod
    def from_env(cls) -> "ImageGenerationConfig":
        return cls(
            provider=os.getenv("IMAGE_GENERATION_PROVIDER", "openai").strip().lower(),
            model=os.getenv("IMAGE_GENERATION_MODEL", "gpt-image-2").strip(),
            api_key=os.getenv("OPENAI_API_KEY", "").strip(),
            base_url=os.getenv(
                "IMAGE_GENERATION_BASE_URL", "https://api.openai.com/v1"
            ).strip().rstrip("/"),
            enabled=os.getenv("IMAGE_GENERATION_ENABLED", "false").strip().lower()
            in {"1", "true", "yes", "on"},
        )

    @property
    def ready(self) -> bool:
        return bool(
            self.enabled
            and self.provider == "openai"
            and self.model
            and self.api_key
            and self.base_url
        )

    @property
    def readiness_note(self) -> str:
        if not self.enabled:
            return "Image generation is off until it is enabled by an administrator."
        if self.provider != "openai":
            return f"The {self.provider or 'selected'} provider is not installed yet."
        if not self.api_key:
            return "Add an OpenAI API key to enable image generation."
        if not self.model:
            return "Choose an image model before generating."
        return f"Ready · {self.provider.title()} · {self.model}"


@dataclass(frozen=True)
class GeneratedImageResult:
    image_bytes: bytes
    mime_type: str
    prompt: str


class ImageGenerator(Protocol):
    provider_name: str
    model_name: str

    async def generate(
        self,
        *,
        prompt: str,
        size: str,
        quality: str,
    ) -> GeneratedImageResult: ...


def build_featured_image_prompt(
    draft: ArticleDraft,
    *,
    creative_direction: str,
    style: str,
) -> str:
    if style not in SUPPORTED_IMAGE_STYLES:
        raise ImageGenerationError("Choose one of the available visual styles.")
    direction = creative_direction.strip()
    brand = draft.brand_profile_snapshot
    brand_context = (
        f"Brand: {brand.name}. Audience: {brand.audience or 'developers and technology readers'}. "
        f"Visual voice: {brand.voice_summary or 'credible, modern, useful, and restrained'}."
        if brand
        else "Audience: developers and technology readers. Visual voice: credible, modern, useful, and restrained."
    )
    return (
        "Create a polished 3:2 editorial featured image for a technology article.\n"
        f"Article title: {draft.title}\n"
        f"Article summary: {draft.dek}\n"
        f"{brand_context}\n"
        f"Visual style: {style}.\n"
        f"Creative direction: {direction or 'Translate the central article idea into one clear visual metaphor.'}\n"
        "Composition: one strong focal point, generous negative space, premium publication quality, "
        "and a layout that still works when cropped for website and social previews. "
        "Do not add readable text, logos, brand marks, fake interface screenshots, watermarks, "
        "or unsupported factual claims. Do not imitate a living artist."
    )


class OpenAIImageGenerator:
    provider_name = "openai"

    def __init__(
        self,
        config: ImageGenerationConfig,
        client: httpx.AsyncClient | None = None,
    ):
        if not config.ready:
            raise ImageGenerationError(config.readiness_note)
        parsed = urlsplit(config.base_url)
        if parsed.scheme != "https" or parsed.hostname in {"localhost", "127.0.0.1"}:
            raise ImageGenerationError(
                "IMAGE_GENERATION_BASE_URL must be a secure HTTPS provider endpoint."
            )
        self.config = config
        self.model_name = config.model
        self._client = client

    async def generate(
        self,
        *,
        prompt: str,
        size: str,
        quality: str,
    ) -> GeneratedImageResult:
        if size not in SUPPORTED_IMAGE_SIZES:
            raise ImageGenerationError("Choose a supported image size.")
        if quality not in SUPPORTED_IMAGE_QUALITIES:
            raise ImageGenerationError("Choose low, medium, or high image quality.")
        if not prompt.strip() or len(prompt) > 8_000:
            raise ImageGenerationError("The image prompt must be between 1 and 8,000 characters.")

        async def request(client: httpx.AsyncClient) -> GeneratedImageResult:
            response = await client.post(
                f"{self.config.base_url}/images/generations",
                headers={"Authorization": f"Bearer {self.config.api_key}"},
                json={
                    "model": self.config.model,
                    "prompt": prompt,
                    "size": size,
                    "quality": quality,
                    "n": 1,
                    "output_format": "png",
                },
            )
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            encoded = data[0].get("b64_json") if isinstance(data, list) and data else None
            if not isinstance(encoded, str):
                raise ImageGenerationError("The image provider returned no image data.")
            try:
                content = base64.b64decode(encoded, validate=True)
            except (ValueError, TypeError) as exc:
                raise ImageGenerationError(
                    "The image provider returned invalid image data."
                ) from exc
            if not content.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ImageGenerationError("The image provider returned an unexpected format.")
            if len(content) > 10 * 1024 * 1024:
                raise ImageGenerationError("The generated image is larger than 10 MB.")
            return GeneratedImageResult(content, "image/png", prompt)

        if self._client is not None:
            return await request(self._client)
        async with httpx.AsyncClient(timeout=180) as client:
            return await request(client)


def create_configured_image_generator() -> ImageGenerator:
    config = ImageGenerationConfig.from_env()
    if config.provider == "openai":
        return OpenAIImageGenerator(config)
    raise ImageGenerationError(
        f"The {config.provider or 'selected'} image provider is not installed yet."
    )
