"""Embedding helpers."""

from __future__ import annotations

import os
import struct
import urllib.parse
import warnings

from openai import OpenAI

from .config import Config

# Lazy-loaded SentenceTransformer model cache (same pattern as rerank.py)
_embed_model_cache: dict[str, object] = {}

# Hosts treated as local OpenAI-compatible servers (no API key required).
_LOCAL_OPENAI_HOSTS = frozenset({"localhost", "127.0.0.1"})

# Harmless placeholder used only when the OpenAI SDK insists on an api_key
# for local endpoints. Never derived from a real secret.
_DUMMY_OPENAI_API_KEY = "local-dummy-key"


def normalize_openai_base_url(raw: str | None) -> str:
    """Normalize a configured OpenAI base URL.

    Strips surrounding whitespace and trailing slashes so values like
    ``"  http://localhost:1234/v1/  "`` and ``"http://localhost:1234/v1"``
    map to the same endpoint. Returns ``""`` when unset.
    """
    if not raw:
        return ""
    text = str(raw).strip()
    if not text:
        return ""
    return text.rstrip("/")


def is_local_openai_base_url(base_url: str | None) -> bool:
    """Return True when a base URL points at a local loopback endpoint."""
    normalized = normalize_openai_base_url(base_url)
    if not normalized:
        return False
    try:
        parsed = urllib.parse.urlparse(normalized)
        host = parsed.hostname
        if host is None and "://" not in normalized:
            host = urllib.parse.urlparse("http://" + normalized).hostname
        if not host:
            return False
        return host.lower() in _LOCAL_OPENAI_HOSTS
    except Exception:
        return False


def openai_client_kwargs(cfg: Config) -> dict:
    """Build ``OpenAI(...)`` kwargs from ``cfg.openai_base_url``.

    - No base URL: ``{}`` (default OpenAI behavior, API key required).
    - Local base URL (localhost / 127.0.0.1): ``base_url`` plus the
      existing ``OPENAI_API_KEY`` when set, otherwise a harmless dummy
      key so the SDK does not raise. The key value is never printed.
    - Remote custom base URL: ``base_url`` only, so the SDK retains its
      normal ``OPENAI_API_KEY`` requirement.
    """
    base_url = normalize_openai_base_url(getattr(cfg, "openai_base_url", ""))
    if not base_url:
        return {}
    if is_local_openai_base_url(base_url):
        api_key = os.environ.get("OPENAI_API_KEY") or _DUMMY_OPENAI_API_KEY
        return {"base_url": base_url, "api_key": api_key}
    return {"base_url": base_url}


def create_openai_client(cfg: Config) -> OpenAI:
    """Create an OpenAI client honoring ``cfg.openai_base_url``."""
    return OpenAI(**openai_client_kwargs(cfg))


def create_embedding_client(cfg: Config) -> OpenAI | None:
    """Create the client used for embeddings, or None for local method."""
    if cfg.embed_method == "local":
        return None
    return create_openai_client(cfg)


def serialize_f32(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def deserialize_f32(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def _get_device() -> str:
    """Pick the best available torch device."""
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


def _get_embed_model(model_name: str):
    """Load and cache a SentenceTransformer model, using GPU if available."""
    if model_name not in _embed_model_cache:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise ImportError(
                "sentence-transformers is required for local embeddings. "
                "Install it with: pip install 'kb[local-embed]' or pip install sentence-transformers"
            )
        device = _get_device()
        # Suppress noisy HF Hub warnings during model load
        _prev = os.environ.get("HF_HUB_DISABLE_IMPLICIT_TOKEN")
        os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=".*unauthenticated.*")
            _embed_model_cache[model_name] = SentenceTransformer(
                model_name, device=device
            )
        if _prev is None:
            os.environ.pop("HF_HUB_DISABLE_IMPLICIT_TOKEN", None)
        else:
            os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = _prev
    return _embed_model_cache[model_name]


def local_embed_batch(
    texts: list[str], cfg: Config, *, is_query: bool = False
) -> list[list[float]]:
    """Embed texts using a local SentenceTransformer model.

    Passes prompt_name="query" for query embeddings only when the model
    declares prompt templates (e.g. arctic-embed). Models without prompts
    (e.g. Granite R2) get plain encode().
    Truncates to cfg.embed_dims if set lower than the model's native output.
    """
    model = _get_embed_model(cfg.local_embed_model)
    kwargs: dict = {"normalize_embeddings": True}
    if is_query and getattr(model, "prompts", None):
        kwargs["prompt_name"] = "query"
    embeddings = model.encode(texts, **kwargs)

    result = [emb.tolist() for emb in embeddings]

    # Matryoshka truncation if embed_dims < model output
    native_dims = len(result[0]) if result else 0
    if native_dims and cfg.embed_dims < native_dims:
        result = [emb[: cfg.embed_dims] for emb in result]

    return result


def local_embed_dims(cfg: Config) -> int:
    """Return effective embedding dimensions for a local model.

    Auto-detects native model dims. Returns min(cfg.embed_dims, native) to
    support Matryoshka truncation while preventing dimension mismatches
    (e.g. default 1536 vs model's 768).
    """
    model = _get_embed_model(cfg.local_embed_model)
    native = model.get_sentence_embedding_dimension()
    return min(cfg.embed_dims, native)


def embed_batch(
    client: OpenAI | None,
    texts: list[str],
    cfg: Config,
    *,
    is_query: bool = False,
) -> list[list[float]]:
    """Embed a batch of texts using the configured method.

    Dispatches to local_embed_batch() or OpenAI API based on cfg.embed_method.
    """
    if cfg.embed_method == "local":
        return local_embed_batch(texts, cfg, is_query=is_query)

    resp = client.embeddings.create(
        model=cfg.embed_model, input=texts, dimensions=cfg.embed_dims
    )
    return [d.embedding for d in resp.data]
