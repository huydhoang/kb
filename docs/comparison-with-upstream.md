# Comparison with upstream

This repo (`huydhoang/kb`) is an independently maintained hard fork of
[`ariel-frischer/kb`](https://github.com/ariel-frischer/kb/).
The core storage engine (chunking, sqlite-vec storage, reranking, `kb ask` RAG
pipeline) is shared; `kb search` intentionally diverges (see below).
The differences below are verified against upstream `main`
(`src/kb/api.py`, `src/kb/cli.py`, `src/kb/config.py`, `src/kb/embed.py`,
`src/kb/extract.py`, `src/kb/ingest.py`, `src/kb/terminal.py`, `README.md`).

Upstream's own maintenance note reads: *"This is a personal tool I've
open-sourced. I may or may not respond to issues/PRs. Fork freely."*
That is the rationale for this fork: changes needed here ship here,
with no expectation of merging back upstream.

## Feature comparison

| Area | Upstream `ariel-frischer/kb` | This fork |
|---|---|---|
| Secrets | `OPENAI_API_KEY` / `OPENAI_BASE_URL` from process env or `~/.config/kb/secrets.toml` (env wins) | Same, plus project `.env` autoloaded at CLI startup via `python-dotenv` (walk-up from cwd, works from subprocess/agent shells). Precedence: process env > project `.env` > `secrets.toml` (`override=False`, never prints/exposes values), so `secrets.toml` is optional |
| Index scoping | `sources` are directories only; exclusion via `.kbignore` only | Same directory-only `sources`, plus `include_patterns` whitelist (`Config`, `.kb.toml`) and persistent `kb index <dir> --include "PAT"` scoped indexing. `.kbignore` always wins over includes |
| `kb index DIR` without flags | One-off index of the given dirs, config untouched | Unchanged |
| `kb index --include` | Not present | Persistent: merges dirs into `sources` (never drops existing entries) and patterns into `include_patterns` (union), saves `.kb.toml`, then indexes the requested scope |
| Incremental indexing | MD5 per file/chunk; unchanged skipped, changed reprocessed | Unchanged; include-excluded files are filtered before extract/hash/embed, and re-running a scope does not re-embed unchanged files |
| Windows config encoding | Bare TOML load (fails opaquely on ANSI-encoded files) | `ConfigError` with UTF-8 guidance for `.kb.toml` / `secrets.toml` / `.env` |
| Text ingestion encoding | Platform-default decoding (CJK mojibake on Windows, e.g. `å`, `ç`) | Explicit UTF-8 (`encoding="utf-8"`) for Markdown/text/HTML/SRT/RTF extractors; no `latin-1` repair hack; Unicode preserved to SQLite/FTS/output |
| Console output encoding | Platform-default stdout/stderr (CJK `UnicodeEncodeError` on Windows terminals) | CLI startup reconfigures stdout/stderr to UTF-8 via `reconfigure(encoding="utf-8", errors="replace")` when supported (`ensure_utf8_stdio()`); no locale or code-page changes; search text and `ensure_ascii=False` JSON untouched |
| Local OpenAI-compatible endpoint | API key required even for localhost servers | `openai_base_url` config (`Config`, `.kb.toml`); `localhost`/`127.0.0.1` endpoints work without an API key (harmless internal dummy key, never printed); honored by `search`, `ask`, and `index` |
| `kb search` lifecycle | HyDE + optional query expansion before retrieval (chat-model dependent) | Pure retrieval: query embedding → vector + FTS → RRF fusion. Never calls chat/HyDE/expansion; a chat-model outage has zero effect. `kb ask` keeps the full RAG pipeline |
| `kb search` default output | Human-readable + `--json` opt-in full metadata | Bare compact JSON `[{"text", "score", "path"}]` by default (`--json` alias, `ensure_ascii=False`); `--print` for human-readable; `--csv`/`--md` kept; diagnostics go to stderr so stdout stays parseable |
| `score` semantics | `similarity` + `rrf_score` + internals exposed | `score` = cosine similarity (`1 - cosine distance`), normalized-BM25 fallback for FTS-only matches; `rrf_score` stays an internal ranking concern |
| Install | `uv tool install --from "git+https://github.com/ariel-frischer/kb.git" ...` | Repointed to `huydhoang/kb` with corrected `uv tool install "kb[extra] @ git+https://..."` syntax |
| Everything else (ask, fts, rerank, tags, MCP, formats) | As documented in upstream README | Same behavior (`kb_search` MCP doc updated to pure retrieval) |

## Details

### Project `.env` support

`load_secrets()` (`src/kb/config.py`) explicitly loads the project's `.env`
at CLI startup (`cli.main`; MCP server via `_get_config`, so `kb search` and
`kb ask` behave identically), even when launched from subprocesses/agent
shells that do not load `.env` themselves. Discovery walks up from the cwd
(same convention as `.kb.toml` discovery) to the project/environment root and
parses with `python-dotenv` (`load_dotenv(..., override=False)`), so
explicitly exported vars are never overwritten. Precedence: process env >
project `.env` > `~/.config/kb/secrets.toml`. Values are never printed and
never exposed in JSON output, diagnostics, errors, or logs (covered by
`tests/test_dotenv_autoload.py`, including a subprocess test without a
parent-exported `OPENAI_API_KEY`). Use it for per-project `OPENAI_API_KEY` /
`OPENAI_BASE_URL` (e.g. LMStudio at `http://localhost:1234/v1`).

### Include-filter scoped indexing

For large source trees, index a subset first and broaden later:

```toml
# .kb.toml
sources = ["canonical"]
include_patterns = ["BZ*.md"]
```

```bash
kb add canonical
kb index canonical --include "BZ*.md"   # persists scope, indexes BZ subset
kb index                                # indexes per .kb.toml
```

Rules: patterns use the same fnmatch semantics as `.kbignore` (relative path +
filename); filtering happens before extraction so excluded files cost nothing;
`.kbignore` overrides includes; empty `include_patterns` preserves upstream
behavior. See `docs/kbignore.md` for the two workflows and precedence.

### UTF-8 ingestion + reindex note

Upstream text extractors decoded with the platform default, which corrupts
valid UTF-8 CJK sources on Windows. This fork decodes text explicitly as UTF-8
(`src/kb/extract.py`: Markdown/text/code, HTML, SRT/VTT, RTF; `.kbignore`
likewise). Source files need no changes. Databases built before the fix still
contain mojibake — rebuild once with `kb reset` + `kb index`; incremental
indexing (skip unchanged, re-embed changed) is unchanged.

### UTF-8 stdout/stderr at startup

`ensure_utf8_stdio()` (`src/kb/terminal.py`), called first in `cli.main`,
reconfigures `sys.stdout`/`sys.stderr` to UTF-8 (`errors="replace"`) whenever
`reconfigure` is available, so `kb search` can emit Chinese/Japanese/etc.
from agent shells and Windows terminals without `UnicodeEncodeError`.
Rules: reconfigure-only — no locale or system code-page changes; search
result text and `ensure_ascii=False` JSON encoding unchanged; silently
no-ops on platforms/streams where `reconfigure` is unavailable (covered by
`tests/test_unicode_stdio.py`, including a simulated non-UTF-8 stdout).

### Local `openai_base_url` without API key

Set `openai_base_url = "http://localhost:1234/v1"` in `.kb.toml` to point
embeddings/chat at a local OpenAI-compatible server (e.g. LM Studio).
`openai_client_kwargs()` (`src/kb/embed.py`) passes `base_url` through, and
for loopback hosts (`localhost`, `127.0.0.1`) supplies a harmless internal
dummy key only when `OPENAI_API_KEY` is unset, since the OpenAI SDK insists
on one — the value is never printed or exposed. Remote custom base URLs keep
the normal API-key requirement. Applies to `kb search`, `kb ask`, and
`kb index` (covered by `tests/test_embed.py`).

### `kb search`: pure retrieval + compact contract

```bash
kb search "征太歲"            # [{"text": "...", "score": 0.528, "path": "..."}]
kb search "征太歲" --print   # human-readable, same ranked objects
```

Rules: no chat model, no HyDE, no query expansion anywhere in the search
lifecycle (`--expand`/`--no-expand` and the MCP `expand` flag are accepted for
backward compatibility but ignored; `hyde_enabled`/`query_expand` only affect
`kb ask`). Ranking stays internal RRF; the public `score` is the intuitive
relevance value, not `rrf_score`. Top-k applies to both output modes.

## Which should you choose?

- **Upstream** — you want the original author's line, minimal surface, and are
  fine with env/`secrets.toml` secrets and exclude-only indexing.
- **This fork** — you want project-local `.env` secrets, keyless local
  OpenAI-compatible endpoints, incremental include-filtered indexing of large
  trees, CJK-safe UTF-8 ingestion and console output, and fast
  deterministic `kb search` (compact JSON, no LLM), and accept tracking this
  fork instead of upstream.

## Sync policy

No automatic sync with upstream is promised. Upstream fixes may be ported here
on a case-by-case basis; fork-specific features will not be pushed upstream.
