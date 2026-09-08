"""Tests for project .env autoload at CLI startup (python-dotenv).

Covers: subprocess without parent-exported OPENAI_API_KEY can obtain config
from .env; explicit env wins; no secret leakage into JSON/diagnostics.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from kb.cli import cmd_ask, cmd_search
from kb.config import Config, load_secrets

_DOTENV_KEY = "sk-test-dotenv-autoload-456"
_EXPLICIT_KEY = "sk-test-explicit-123"


def _clean_env_without_key() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("OPENAI_API_KEY", None)
    # Ensure dotenv loading itself is not disabled.
    env.pop("PYTHON_DOTENV_DISABLED", None)
    return env


def _write_dotenv(project_dir: Path, api_key: str) -> Path:
    p = project_dir / ".env"
    p.write_text(
        f'OPENAI_API_KEY="{api_key}"\nOPENAI_BASE_URL="https://example.invalid/v1"\n',
        encoding="utf-8",
    )
    return p


class TestSubprocessDotenvAutoload:
    def test_subprocess_obtains_key_from_dotenv_without_parent_export(
        self, tmp_path, monkeypatch
    ):
        """Core contract: fresh subprocess with cwd=project root sees .env key."""
        _write_dotenv(tmp_path, _DOTENV_KEY)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        code = (
            "from kb.config import load_secrets; "
            "load_secrets(); "
            "import os; "
            "print(os.environ.get('OPENAI_API_KEY', '<missing>'))"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=tmp_path,
            env=_clean_env_without_key(),
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == _DOTENV_KEY
        # load_secrets must never print the value itself.
        assert _DOTENV_KEY not in proc.stderr

    def test_subprocess_explicit_env_wins_over_dotenv(self, tmp_path):
        _write_dotenv(tmp_path, _DOTENV_KEY)
        env = _clean_env_without_key()
        env["OPENAI_API_KEY"] = _EXPLICIT_KEY
        code = (
            "from kb.config import load_secrets; "
            "load_secrets(); "
            "import os; "
            "print(os.environ.get('OPENAI_API_KEY', '<missing>'))"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == _EXPLICIT_KEY

    def test_walk_up_from_subdirectory(self, tmp_path, monkeypatch):
        _write_dotenv(tmp_path, _DOTENV_KEY)
        sub = tmp_path / "sub" / "deep"
        sub.mkdir(parents=True)
        monkeypatch.chdir(sub)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setattr("kb.config.SECRETS_PATH", tmp_path / "no-secrets.toml")
        load_secrets()
        assert os.environ.get("OPENAI_API_KEY") == _DOTENV_KEY


class TestCliStartupAppliesEqually:
    def test_main_calls_load_secrets_for_search_and_ask(self, tmp_path, monkeypatch):
        """main() must autoload .env before dispatching search or ask."""
        import kb.cli as cli_mod

        for argv in (["kb", "search", "q"], ["kb", "ask", "q"]):
            called = []
            monkeypatch.setattr(
                cli_mod, "load_secrets", lambda *a, **k: called.append(True)
            )
            monkeypatch.setattr(cli_mod, "find_config", lambda: Config())
            monkeypatch.setattr(cli_mod, "search_core", lambda *a, **k: {"results": []})
            monkeypatch.setattr(
                cli_mod,
                "ask_core",
                lambda *a, **k: {
                    "question": "q",
                    "answer": "a",
                    "model": "m",
                    "bm25_shortcut": False,
                    "timing_ms": {
                        "hyde": 0,
                        "embed": 1,
                        "search": 1,
                        "generate": 1,
                    },
                    "tokens": {"prompt": 1, "completion": 1},
                    "sources": [],
                },
            )
            monkeypatch.setattr(sys, "argv", argv)
            try:
                cli_mod.main()
            except SystemExit:
                pass
            assert called, f"load_secrets not called for {argv[1]}"


class TestNoSecretLeakage:
    def test_search_compact_json_never_contains_key(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setenv("OPENAI_API_KEY", _DOTENV_KEY)
        cfg = Config()
        cfg.db_path = tmp_path / "missing.db"
        fake = {
            "query": "q",
            "filters": {},
            "timing_ms": {"embed": 1, "vec": 1, "fts": 1},
            "candidates": {"vec": 1, "fts": 1, "fused": 1},
            "results": [
                {
                    "rank": 1,
                    "doc_path": "d.md",
                    "heading": "H",
                    "similarity": 0.5,
                    "fts_rank": -1.0,
                    "rrf_score": 0.01,
                    "sources": ["vec"],
                    "text": "hello",
                }
            ],
        }
        with patch("kb.cli.search_core", return_value=fake):
            cmd_search("q", cfg)
            captured = capsys.readouterr()
        assert _DOTENV_KEY not in captured.out
        assert _DOTENV_KEY not in captured.err
        data = json.loads(captured.out)
        assert set(data[0].keys()) == {"text", "score", "path"}

    def test_ask_json_never_contains_key(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("OPENAI_API_KEY", _DOTENV_KEY)
        cfg = Config()
        fake = {
            "question": "q",
            "answer": "a",
            "model": "gpt-4o-mini",
            "bm25_shortcut": False,
            "rerank": None,
            "filters": {},
            "timing_ms": {"hyde": 0, "embed": 1, "search": 1, "generate": 1},
            "tokens": {"prompt": 1, "completion": 1},
            "sources": [],
            "result_count": 0,
            "filtered_count": 0,
        }
        with patch("kb.cli.ask_core", return_value=fake):
            cmd_ask("q", cfg, output_format="json")
        captured = capsys.readouterr()
        assert _DOTENV_KEY not in captured.out
        assert _DOTENV_KEY not in captured.err
        assert _DOTENV_KEY not in json.dumps(json.loads(captured.out))
