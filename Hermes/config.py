from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    HERMES_PORT: int = 7777
    HERMES_HOST: str = "0.0.0.0"

    DATABASE_URL: str = "postgresql+asyncpg://mao:mao@postgres:5432/mao_db"
    REDIS_URL: str = "redis://redis:6379"
    QDRANT_URL: str = "http://qdrant:6333"

    OPENROUTER_API_KEY: str
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    # Default model — Claude Haiku (быстрый + дешёвый). Перекрывается через
    # llm_router.pick_model(complexity) когда задача сложнее.
    OPENROUTER_MODEL: str = "anthropic/claude-sonnet-4-6"

    # ── Model tiers — llm_router выбирает по complexity ───────────
    OPENROUTER_MODEL_HAIKU: str = "anthropic/claude-haiku-4-5"
    OPENROUTER_MODEL_SONNET: str = "anthropic/claude-sonnet-4-6"
    OPENROUTER_MODEL_OPUS: str = "anthropic/claude-opus-4-7"
    # Fallback не-Anthropic — если OpenRouter временно недоступен для Claude
    OPENROUTER_MODEL_FALLBACK: str = "nousresearch/hermes-3-llama-3.1-70b"
    # Free tier — для демо BRO и любых не-критичных публичных surface.
    # OpenRouter раздаёт Llama-3.3-70B-instruct по нулевой цене (rate-limited,
    # но достаточно для одиночных демо-ассистентов). Меняй на :free-аналог,
    # если Meta уберёт листинг.
    OPENROUTER_MODEL_FREE: str = "meta-llama/llama-3.3-70b-instruct:free"

    # ── Visual Agent: media generation models (via OpenRouter) ────
    # Nano Banana — primary image model for slides/lifestyle/infographics.
    # NB: the slug lost its "-preview" suffix; verified live against
    # OpenRouter /models (output_modalities contains "image"). Newer
    # options if quality demands: google/gemini-3-pro-image-preview,
    # openai/gpt-5-image.
    OPENROUTER_IMAGE_MODEL: str = "google/gemini-2.5-flash-image"
    # ── VIDEO via fal.ai (Kling 3.0) ──────────────────────────────
    # OpenRouter exposes NO video models, so video runs through fal.ai
    # which hosts Kling. Image-to-video = the start frame is one of our
    # generated card slides (or an uploaded product/avatar element), so
    # the товар/лицо identity carries into the motion with no distortion.
    # Set FAL_KEY in Hermes/.env to enable; empty = video disabled
    # gracefully (factory still does the image card set).
    FAL_KEY: str = ""
    FAL_QUEUE_BASE: str = "https://queue.fal.run"
    # Kling 3.0 Pro image-to-video. Alternatives:
    #   fal-ai/kling-video/v3/standard/image-to-video  (cheaper)
    #   fal-ai/kling-video/v2.6/pro/image-to-video
    FAL_KLING_MODEL: str = "fal-ai/kling-video/v3/pro/image-to-video"
    # OpenRouter video slugs kept ONLY as dead placeholders — not used.
    OPENROUTER_VIDEO_MODEL: str = "fal:kling-v3-pro"
    OPENROUTER_VIDEO_FALLBACK_MODEL: str = "fal:kling-v3-standard"
    # Prompt-builder LLM (used to convert briefs → Higgsfield-style prompts).
    # Matches OPENROUTER_MODEL_SONNET — kept separate so visual prompt
    # builder can be tuned independently in future.
    OPENROUTER_PROMPT_MODEL: str = "anthropic/claude-sonnet-4-5"

    # ── MinIO (S3-compatible object storage) ──────────────────────
    MINIO_ENDPOINT: str = "http://minio:9000"
    MINIO_ROOT_USER: str = "mao"
    MINIO_ROOT_PASSWORD: str = ""
    MINIO_BUCKET: str = "mao-visual"
    MINIO_PUBLIC_URL: str = "http://localhost:9000"  # for URL responses

    APP_ENV: str = "development"
    LOG_LEVEL: str = "info"

    # Optional Fernet key for local encryption (Phase 1+)
    FERNET_MASTER_KEY: Optional[str] = None

    # ── Internal auth: Backend → Hermes ───────────────────────────
    # Shared secret. Backend sends X-Internal-Token on every Hermes call;
    # Hermes middleware (see main.py) validates with constant-time compare.
    # MUST be set in production. In development, if empty, middleware logs
    # a warning every request but does not refuse — to avoid breaking dev.
    HERMES_INTERNAL_TOKEN: str = ""

    # ── Embeddings (Phase 3A Legal RAG) ───────────────────────────
    # Default: self-hosted BGE-M3 via `fastembed` (FREE, runs inside the
    # Hermes container, no external API call, good Russian support, ~568 MB
    # ONNX model downloaded on first use). 1024-dim vectors.
    #
    # Alt providers (kept for fallback or A/B):
    #   EMBEDDING_PROVIDER=openai → uses OPENAI_API_KEY + text-embedding-3-small (1536-dim)
    #   EMBEDDING_PROVIDER=voyage → uses VOYAGE_API_KEY + voyage-3-lite (1024-dim, ~$0.02/1M)
    #
    # Switching providers requires a Qdrant collection rebuild (dim mismatch).
    EMBEDDING_PROVIDER: str = "bge"  # 'bge' | 'openai' | 'voyage'
    # `paraphrase-multilingual-mpnet-base-v2` — multilingual sentence-transformer
    # supported by fastembed. 768-dim, ~1 GB ONNX, ~30 ms/query on CPU, solid
    # Russian quality (better than MiniLM, smaller than e5-large which is 2.24 GB).
    # Good trade-off for laptop-class server (5 GB RAM total).
    # If you want better quality and can afford the model size, switch to
    # `intfloat/multilingual-e5-large` (1024-dim) and bump EMBEDDING_DIMENSIONS.
    EMBEDDING_MODEL: str = "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"
    EMBEDDING_DIMENSIONS: int = 768  # mpnet=768, e5-large=1024, openai=1536, voyage=1024
    OPENAI_API_KEY: str = ""               # only used when EMBEDDING_PROVIDER=openai
    VOYAGE_API_KEY: str = ""               # only used when EMBEDDING_PROVIDER=voyage

    # Qdrant collection name for legal corpus. One collection, scope/tenant
    # filtered via Qdrant payload filters.
    LEGAL_QDRANT_COLLECTION: str = "legal_corpus"

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
