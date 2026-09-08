"""Regression: Windows Unicode stdio for kb search output."""

from __future__ import annotations

import inspect
import io
import json
import sys
from unittest.mock import patch

from kb.cli import cmd_search
from kb.config import Config
from kb.terminal import ensure_utf8_stdio


def _cp1252_stream() -> tuple[io.BytesIO, io.TextIOWrapper]:
    buf = io.BytesIO()
    # strict cp1252 raises UnicodeEncodeError on CJK — simulates legacy terminal.
    wrapper = io.TextIOWrapper(buf, encoding="cp1252", errors="strict")
    return buf, wrapper


class TestEnsureUtf8Stdio:
    def test_reconfigures_non_utf8_stdout(self, monkeypatch):
        buf, fake = _cp1252_stream()
        assert fake.encoding.lower().replace("_", "-") in ("cp1252", "windows-1252")
        monkeypatch.setattr(sys, "stdout", fake)
        ensure_utf8_stdio()
        assert fake.encoding.lower().replace("_", "-") == "utf-8"
        # CJK must now succeed (errors=replace, UTF-8 covers all Unicode).
        print("五行大义 征太歲 日本語テスト")
        fake.flush()
        buf.seek(0)
        assert "五行" in buf.getvalue().decode("utf-8")
        fake.detach()

    def test_unicode_search_output_no_encode_error(self, monkeypatch):
        buf, fake = _cp1252_stream()
        monkeypatch.setattr(sys, "stdout", fake)
        ensure_utf8_stdio()
        cfg = Config()
        fake_result = {
            "query": "五行",
            "filters": {},
            "timing_ms": {"embed": 1, "vec": 1, "fts": 1},
            "candidates": {"vec": 1, "fts": 1, "fused": 1},
            "results": [
                {
                    "rank": 1,
                    "doc_path": "canonical/BZ07.md",
                    "heading": "卷第五",
                    "similarity": 0.528,
                    "fts_rank": -2.0,
                    "rrf_score": 0.01,
                    "sources": ["vec"],
                    "text": "五行相承为帝也。易经乃上取伏羲。日本語テスト",
                }
            ],
        }
        with patch("kb.cli.search_core", return_value=fake_result):
            cmd_search("五行", cfg)  # must not raise UnicodeEncodeError
        fake.flush()
        buf.seek(0)
        raw = buf.getvalue().decode("utf-8")
        data = json.loads(raw)
        assert "五行相承" in data[0]["text"]
        fake.detach()

    def test_noop_when_reconfigure_unavailable(self, monkeypatch):
        class NoReconfig:
            def write(self, s):
                pass

            def flush(self):
                pass

        monkeypatch.setattr(sys, "stdout", NoReconfig())
        monkeypatch.setattr(sys, "stderr", object())
        ensure_utf8_stdio()  # must not raise

    def test_does_not_touch_locale_or_codepage(self):
        src = inspect.getsource(ensure_utf8_stdio)
        assert "setlocale" not in src
        assert "chcp" not in src.lower()
        assert "windll" not in src.lower()
        assert "SetConsole" not in src
        import kb.terminal as term

        mod_src = inspect.getsource(term)
        assert "import locale" not in mod_src
        assert "setlocale" not in mod_src

    def test_json_still_ensure_ascii_false(self):
        import kb.cli as cli_mod

        assert "ensure_ascii=False" in inspect.getsource(cli_mod.cmd_search)

    def test_main_calls_ensure_utf8_stdio(self, monkeypatch):
        import kb.cli as cli_mod

        called: list[bool] = []
        monkeypatch.setattr(cli_mod, "ensure_utf8_stdio", lambda: called.append(True))
        monkeypatch.setattr(sys, "argv", ["kb", "--help"])
        try:
            cli_mod.main()
        except SystemExit:
            pass
        assert called == [True]
