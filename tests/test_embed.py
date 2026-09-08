"""Tests for kb.embed — serialization and embedding."""

import struct
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from kb.config import Config
from kb.embed import (
    _DUMMY_OPENAI_API_KEY,
    create_embedding_client,
    create_openai_client,
    deserialize_f32,
    embed_batch,
    is_local_openai_base_url,
    normalize_openai_base_url,
    openai_client_kwargs,
    serialize_f32,
)


class TestSerializeF32:
    def test_roundtrip(self):
        vec = [1.0, 2.5, -3.14, 0.0]
        serialized = serialize_f32(vec)
        assert isinstance(serialized, bytes)
        assert len(serialized) == 4 * len(vec)
        unpacked = list(struct.unpack(f"{len(vec)}f", serialized))
        for a, b in zip(vec, unpacked):
            assert abs(a - b) < 1e-5

    def test_empty_vector(self):
        assert serialize_f32([]) == b""

    def test_single_element(self):
        serialized = serialize_f32([42.0])
        assert len(serialized) == 4


class TestDeserializeF32:
    def test_roundtrip(self):
        vec = [1.0, 2.5, -3.14, 0.0]
        blob = serialize_f32(vec)
        result = deserialize_f32(blob)
        assert len(result) == len(vec)
        for a, b in zip(vec, result):
            assert abs(a - b) < 1e-5

    def test_empty(self):
        assert deserialize_f32(b"") == []

    def test_single_element(self):
        blob = serialize_f32([42.0])
        result = deserialize_f32(blob)
        assert len(result) == 1
        assert abs(result[0] - 42.0) < 1e-5


class TestEmbedBatch:
    def test_calls_openai_correctly(self):
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.data = [
            MagicMock(embedding=[0.1, 0.2]),
            MagicMock(embedding=[0.3, 0.4]),
        ]
        mock_client.embeddings.create.return_value = mock_resp

        cfg = Config(embed_model="test-model", embed_dims=2)
        result = embed_batch(mock_client, ["text1", "text2"], cfg)

        mock_client.embeddings.create.assert_called_once_with(
            model="test-model", input=["text1", "text2"], dimensions=2
        )
        assert result == [[0.1, 0.2], [0.3, 0.4]]


class TestNormalizeOpenaiBaseUrl:
    def test_strips_trailing_slash(self):
        assert (
            normalize_openai_base_url("http://localhost:1234/v1/")
            == "http://localhost:1234/v1"
        )

    def test_strips_whitespace_and_slash(self):
        assert (
            normalize_openai_base_url("  http://127.0.0.1:1234/v1/  ")
            == "http://127.0.0.1:1234/v1"
        )

    def test_empty_and_none(self):
        assert normalize_openai_base_url("") == ""
        assert normalize_openai_base_url("   ") == ""
        assert normalize_openai_base_url(None) == ""

    def test_no_trailing_slash_unchanged(self):
        assert (
            normalize_openai_base_url("http://localhost:1234/v1")
            == "http://localhost:1234/v1"
        )


class TestIsLocalOpenaiBaseUrl:
    def test_localhost(self):
        assert is_local_openai_base_url("http://localhost:1234/v1") is True

    def test_loopback_ip(self):
        assert is_local_openai_base_url("http://127.0.0.1:1234/v1") is True

    def test_local_with_whitespace_and_slash(self):
        assert is_local_openai_base_url("  http://localhost:1234/v1/  ") is True
        assert is_local_openai_base_url("  http://127.0.0.1:1234/v1/  ") is True

    def test_remote_is_not_local(self):
        assert is_local_openai_base_url("https://api.openai.com/v1") is False
        assert is_local_openai_base_url("https://api.example.com/v1") is False

    def test_empty_is_not_local(self):
        assert is_local_openai_base_url("") is False
        assert is_local_openai_base_url(None) is False

    def test_lookalike_host_is_not_local(self):
        # "localhost.evil.com" must not count as local.
        assert is_local_openai_base_url("http://localhost.evil.com/v1") is False


class TestLocalOpenaiClientNoApiKey:
    def test_localhost_without_api_key(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        cfg = Config(embed_method="openai", openai_base_url="http://localhost:1234/v1")
        client = create_openai_client(cfg)
        assert "localhost:1234" in str(client.base_url)
        assert client.api_key == _DUMMY_OPENAI_API_KEY

    def test_loopback_without_api_key(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        cfg = Config(embed_method="openai", openai_base_url="http://127.0.0.1:1234/v1")
        client = create_openai_client(cfg)
        assert "127.0.0.1:1234" in str(client.base_url)
        assert client.api_key == _DUMMY_OPENAI_API_KEY

    def test_local_with_whitespace_and_slash(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        cfg = Config(
            embed_method="openai",
            openai_base_url="  http://127.0.0.1:1234/v1/  ",
        )
        kwargs = openai_client_kwargs(cfg)
        assert kwargs["base_url"] == "http://127.0.0.1:1234/v1"
        client = create_openai_client(cfg)
        assert "127.0.0.1:1234" in str(client.base_url)

    def test_local_uses_env_key_when_present(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-local-present")
        cfg = Config(embed_method="openai", openai_base_url="http://localhost:1234/v1")
        kwargs = openai_client_kwargs(cfg)
        assert kwargs["api_key"] == "sk-test-local-present"


class TestRemoteOpenaiClientRequiresApiKey:
    def test_remote_without_api_key_raises(self, monkeypatch):
        from openai import OpenAIError

        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        cfg = Config(
            embed_method="openai", openai_base_url="https://api.example.com/v1"
        )
        with pytest.raises(OpenAIError):
            create_openai_client(cfg)

    def test_remote_with_api_key_succeeds(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-remote-present")
        cfg = Config(
            embed_method="openai", openai_base_url="https://api.example.com/v1/"
        )
        kwargs = openai_client_kwargs(cfg)
        # Remote keeps base_url but defers key handling to the SDK/env.
        assert kwargs["base_url"] == "https://api.example.com/v1"
        assert "api_key" not in kwargs
        client = create_openai_client(cfg)
        assert "api.example.com" in str(client.base_url)

    def test_default_without_base_url_still_requires_key(self, monkeypatch):
        from openai import OpenAIError

        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        cfg = Config(embed_method="openai", openai_base_url="")
        assert openai_client_kwargs(cfg) == {}
        with pytest.raises(OpenAIError):
            create_openai_client(cfg)


class TestEmbeddingClientDispatch:
    def test_local_method_returns_none(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        cfg = Config(embed_method="local", openai_base_url="http://localhost:1234/v1")
        assert create_embedding_client(cfg) is None

    def test_openai_method_local_url_returns_client(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        cfg = Config(embed_method="openai", openai_base_url="http://localhost:1234/v1")
        assert create_embedding_client(cfg) is not None


class TestOpenaiBaseUrlClientWiring:
    def test_search_core_passes_base_url(self, tmp_path, monkeypatch):
        from kb.db import connect

        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        cfg = Config(
            embed_method="openai",
            embed_dims=4,
            openai_base_url="http://localhost:1234/v1",
        )
        cfg.scope = "project"
        cfg.config_dir = tmp_path
        cfg.config_path = tmp_path / ".kb.toml"
        cfg.db_path = tmp_path / "kb.db"
        connect(cfg).close()

        from kb import api as api_mod

        with (
            patch.object(api_mod, "OpenAI") as mock_openai,
            patch.object(
                api_mod,
                "embed_batch",
                return_value=[[0.1] * 4],
            ),
        ):
            api_mod.search_core("hello", cfg, top_k=1)
        _, kwargs = mock_openai.call_args
        assert kwargs.get("base_url") == "http://localhost:1234/v1"

    def test_index_passes_base_url(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        cfg = Config(
            embed_method="openai",
            embed_dims=4,
            max_chunk_chars=5000,
            min_chunk_chars=10,
            openai_base_url="http://127.0.0.1:1234/v1/  ",
        )
        cfg.scope = "project"
        cfg.config_dir = tmp_path
        cfg.config_path = tmp_path / ".kb.toml"
        cfg.db_path = tmp_path / "kb.db"
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "a.md").write_text("# A\n\nContent here is long enough.")

        from kb import ingest as ingest_mod

        with patch.object(ingest_mod, "OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_resp = MagicMock()
            mock_resp.data = [MagicMock(embedding=[0.1] * 4)]
            mock_client.embeddings.create.return_value = mock_resp
            mock_openai.return_value = mock_client
            ingest_mod.index_directory(docs, cfg)
        _, kwargs = mock_openai.call_args
        assert kwargs.get("base_url") == "http://127.0.0.1:1234/v1"

    def test_config_toml_loads_base_url(self, tmp_path):
        from kb.config import _load_toml

        cfg_path = tmp_path / ".kb.toml"
        cfg_path.write_text(
            'embed_method = "openai"\nopenai_base_url = "http://localhost:1234/v1"\n'
        )
        cfg = _load_toml(cfg_path, "project")
        assert cfg.openai_base_url == "http://localhost:1234/v1"
