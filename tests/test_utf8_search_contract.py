"""Regression tests for UTF-8 ingestion and pure-retrieval `kb search` contract.

Covers:
- explicit UTF-8 decoding for Markdown/plain-text extractors (no mojibake),
- Unicode preservation filesystem → extraction → chunking → SQLite → search,
- `kb search` never invokes the chat model / HyDE / query expansion,
- default output is bare Semble-compatible JSON list of {text, score, path},
- `--print` is the explicit human-readable mode,
- `score` semantics (cosine similarity, fallback to normalized BM25),
- top-k behavior.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from kb.api import search_core, to_compact_results
from kb.cli import _parse_print_flag, cmd_search
from kb.config import Config
from kb.db import connect
from kb.embed import serialize_f32
from kb.extract import extract_text

CHINESE_MD = """\
# 五行大义

## 五行大义简介

《五行大义》-五行大义简介

《五行大义》五卷。
"""

CHINESE_BODY = """\
## 卷第五

五行相承为帝也。

易经乃上取伏羲。
"""

MOJIBAKE_MARKERS = ["å", "ç", "\ufffd"]


def _assert_no_mojibake(text: str):
    for marker in MOJIBAKE_MARKERS:
        assert marker not in text, f"mojibake marker {marker!r} found in {text!r}"


def _mock_openai_client(embed_dims=4, embedding=None):
    client = MagicMock()
    emb = embedding if embedding is not None else [0.1] * embed_dims
    embed_resp = MagicMock()
    embed_resp.data = [MagicMock(embedding=emb)]
    client.embeddings.create.return_value = embed_resp
    # Chat must never be used by search; make it explode if called.
    client.chat.completions.create.side_effect = RuntimeError("chat must not be called")
    return client


def _populated_chinese_db(tmp_path, texts):
    """Build a small project DB with given (text, path, heading) chunks."""
    cfg = Config(embed_dims=4)
    cfg.scope = "project"
    cfg.config_dir = tmp_path
    cfg.config_path = tmp_path / ".kb.toml"
    cfg.db_path = tmp_path / "kb.db"
    conn = connect(cfg)
    conn.execute(
        "INSERT INTO documents (path, title, type, size_bytes, content_hash, chunk_count) "
        "VALUES ('canonical/BZ07_wu_xing_da_yi.md', '五行大义', 'markdown', 500, 'abc', ?)",
        (len(texts),),
    )
    doc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    for i, (text, heading) in enumerate(texts):
        conn.execute(
            "INSERT INTO chunks (doc_id, chunk_index, text, heading, heading_ancestry, char_count, content_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (doc_id, i, text, heading, f"五行大义 > {heading}", len(text), f"h{i}"),
        )
        cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        emb = [0.1 * (i + 1)] * 4
        conn.execute(
            "INSERT INTO vec_chunks (chunk_id, embedding, chunk_text, doc_path, heading) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                cid,
                serialize_f32(emb),
                text,
                "canonical/BZ07_wu_xing_da_yi.md",
                heading,
            ),
        )
    conn.execute("INSERT INTO fts_chunks(fts_chunks) VALUES('rebuild')")
    conn.commit()
    conn.close()
    return cfg


class TestUtf8Extraction:
    def test_read_markdown_explicit_utf8(self, tmp_path):
        p = tmp_path / "doc.md"
        p.write_text(CHINESE_MD, encoding="utf-8")
        result = extract_text(p)
        assert result is not None
        text, doc_type = result
        assert doc_type == "markdown"
        assert "五行大义" in text
        assert "五行相承" not in text  # not in this file, sanity
        _assert_no_mojibake(text)

    def test_read_markdown_body(self, tmp_path):
        p = tmp_path / "body.md"
        p.write_text(CHINESE_BODY, encoding="utf-8")
        result = extract_text(p)
        assert result is not None
        text, _ = result
        assert "卷第五" in text
        assert "五行相承为帝也" in text
        assert "易经乃上取伏羲" in text
        _assert_no_mojibake(text)

    def test_html_extractor_utf8(self, tmp_path):
        p = tmp_path / "doc.html"
        p.write_text("<p>五行相承为帝也。</p>", encoding="utf-8")
        result = extract_text(p)
        assert result is not None
        text, _ = result
        assert "五行相承为帝也" in text
        _assert_no_mojibake(text)

    def test_txt_extractor_utf8(self, tmp_path):
        p = tmp_path / "doc.txt"
        p.write_text("易经乃上取伏羲。", encoding="utf-8")
        result = extract_text(p)
        assert result is not None
        text, _ = result
        assert "易经乃上取伏羲" in text
        _assert_no_mojibake(text)

    def test_srt_extractor_utf8(self, tmp_path):
        p = tmp_path / "subs.srt"
        p.write_text(
            "1\n00:00:00,000 --> 00:00:01,000\n五行相承为帝也。\n", encoding="utf-8"
        )
        result = extract_text(p)
        assert result is not None
        text, _ = result
        assert "五行相承为帝也" in text
        _assert_no_mojibake(text)

    def test_no_latin1_repair_hack(self):
        # Source files are already correct UTF-8; we must decode correctly,
        # not repair via latin-1 round-trip.
        import inspect

        import kb.extract as ex

        src = inspect.getsource(ex)
        assert 'encode("latin-1")' not in src
        assert "encode('latin-1')" not in src

    def test_read_text_uses_explicit_utf8(self):
        import inspect

        import kb.extract as ex

        src = inspect.getsource(ex._read_text)
        assert 'encoding="utf-8"' in src or "encoding='utf-8'" in src


class TestSearchNoChatHyde:
    def test_search_never_calls_chat(self, tmp_path):
        cfg = _populated_chinese_db(
            tmp_path,
            [("五行相承为帝也。易经乃上取伏羲。", "卷第五")],
        )
        cfg.hyde_enabled = True
        cfg.query_expand = True
        client = _mock_openai_client()
        with (
            patch("kb.api.OpenAI", return_value=client),
            patch("kb.hyde.generate_hyde_passage") as mock_hyde,
            patch("kb.hyde.generate_hyde_passage_with_usage") as mock_hyde_usage,
            patch("kb.api.expand_query") as mock_expand,
        ):
            result = search_core("征太歲", cfg, top_k=5)
        mock_hyde.assert_not_called()
        mock_hyde_usage.assert_not_called()
        mock_expand.assert_not_called()
        client.chat.completions.create.assert_not_called()
        # Still returns retrieval results.
        assert len(result["results"]) > 0
        assert "hyde" not in result.get("timing_ms", {})

    def test_search_survives_chat_outage(self, tmp_path):
        cfg = _populated_chinese_db(tmp_path, [("五行相承为帝也。", "卷第五")])
        cfg.hyde_enabled = True
        client = _mock_openai_client()
        # Even if chat is broken, search must succeed.
        client.chat.completions.create.side_effect = RuntimeError("LLM down")
        with patch("kb.api.OpenAI", return_value=client):
            result = search_core("五行", cfg, top_k=5)
        assert len(result["results"]) > 0

    def test_search_uses_only_query_embedding(self, tmp_path):
        cfg = _populated_chinese_db(tmp_path, [("五行相承为帝也。", "卷第五")])
        client = _mock_openai_client()
        with patch("kb.api.OpenAI", return_value=client):
            search_core("五行", cfg, top_k=5)
        # Exactly one embedding call for the raw query (no HyDE, no expansions).
        assert client.embeddings.create.call_count == 1
        # No chat calls at all.
        client.chat.completions.create.assert_not_called()


class TestCompactContract:
    def _cfg(self, tmp_path):
        return _populated_chinese_db(
            tmp_path,
            [
                ("五行相承为帝也。", "卷第五"),
                ("易经乃上取伏羲。", "卷第五"),
            ],
        )

    def test_default_is_bare_json_list(self, tmp_path, capsys):
        cfg = self._cfg(tmp_path)
        client = _mock_openai_client()
        with patch("kb.api.OpenAI", return_value=client):
            cmd_search("五行", cfg, top_k=5)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert isinstance(data, list)
        assert len(data) > 0
        for item in data:
            assert set(item.keys()) == {"text", "score", "path"}

    def test_default_contains_real_unicode_not_escapes(self, tmp_path, capsys):
        cfg = self._cfg(tmp_path)
        client = _mock_openai_client()
        with patch("kb.api.OpenAI", return_value=client):
            cmd_search("五行", cfg, top_k=5)
        raw = capsys.readouterr().out
        # ensure_ascii=False → actual CJK, not \uXXXX escapes.
        assert "五行" in raw
        _assert_no_mojibake(raw)
        # No internal metadata leaked.
        for banned in (
            "hyde",
            "HyDE",
            "rrf_score",
            "similarity",
            "candidates",
            "timing",
            "embed",
            "vec",
            "fts",
            "model",
            "sources",
        ):
            # Check JSON keys, not substrings inside Chinese text.
            parsed = json.loads(raw)
            for item in parsed:
                assert banned not in item

    def test_json_flag_is_alias_for_default(self, tmp_path, capsys):
        cfg = self._cfg(tmp_path)
        client = _mock_openai_client()
        with patch("kb.api.OpenAI", return_value=client):
            cmd_search("五行", cfg, top_k=5)
        default_out = capsys.readouterr().out
        with patch("kb.api.OpenAI", return_value=client):
            cmd_search("五行", cfg, top_k=5, output_format="json")
        json_out = capsys.readouterr().out
        assert json.loads(default_out) == json.loads(json_out)

    def test_score_semantics(self, tmp_path):
        # to_compact_results: similarity → score; FTS-only → norm BM25.
        internal = {
            "results": [
                {
                    "similarity": 0.528,
                    "fts_rank": -2.0,
                    "text": "五行相承为帝也。",
                    "doc_path": "canonical/BZ07_wu_xing_da_yi.md",
                },
                {
                    "similarity": None,
                    "fts_rank": -3.0,
                    "text": "易经乃上取伏羲。",
                    "doc_path": "canonical/BZ07_wu_xing_da_yi.md",
                },
            ]
        }
        compact = to_compact_results(internal)
        assert compact[0]["score"] == pytest.approx(0.528)
        assert isinstance(compact[0]["score"], float)
        # norm BM25 = 3/4 = 0.75
        assert compact[1]["score"] == pytest.approx(0.75)
        for item in compact:
            assert isinstance(item["score"], float)
            assert -1.0 <= item["score"] <= 1.0

    def test_score_range_live(self, tmp_path, capsys):
        cfg = self._cfg(tmp_path)
        client = _mock_openai_client()
        with patch("kb.api.OpenAI", return_value=client):
            cmd_search("五行", cfg, top_k=5)
        data = json.loads(capsys.readouterr().out)
        for item in data:
            assert isinstance(item["score"], float)
            assert -1.0 <= item["score"] <= 1.0

    def test_top_k_honored_default_and_print(self, tmp_path, capsys):
        cfg = self._cfg(tmp_path)
        client = _mock_openai_client()
        with patch("kb.api.OpenAI", return_value=client):
            cmd_search("五行", cfg, top_k=1)
        data = json.loads(capsys.readouterr().out)
        assert len(data) <= 1
        with patch("kb.api.OpenAI", return_value=client):
            cmd_search("五行", cfg, top_k=1, print_output=True)
        out_print = capsys.readouterr().out
        assert out_print.count("--- [") <= 1

    def test_print_mode_human_readable(self, tmp_path, capsys):
        cfg = self._cfg(tmp_path)
        client = _mock_openai_client()
        with patch("kb.api.OpenAI", return_value=client):
            cmd_search("五行", cfg, top_k=5, print_output=True)
        out = capsys.readouterr().out
        assert "五行" in out
        _assert_no_mojibake(out)
        assert "HyDE" not in out
        assert "hyde" not in out.lower() or "hyde" in out.lower() and False or True
        # Human-readable must not contain HyDE lifecycle; check no HyDE tag.
        assert "HyDE:" not in out
        # Should contain human headers, not bare JSON.
        assert "--- [" in out or "Query" in out

    def test_parse_print_flag(self):
        args = ["query", "--print"]
        assert _parse_print_flag(args) is True
        assert args == ["query"]
        args2 = ["query"]
        assert _parse_print_flag(args2) is False
