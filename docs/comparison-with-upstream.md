# Comparison with upstream

This repo (`huydhoang/kb`) is an independently maintained hard fork of
[`ariel-frischer/kb`](https://github.com/ariel-frischer/kb/).
The core engine (hybrid search, HyDE, reranking, sqlite-vec storage) is shared;
the differences below are verified against upstream `main`
(`src/kb/cli.py`, `src/kb/config.py`, `src/kb/ingest.py`, `README.md`).

Upstream's own maintenance note reads: *"This is a personal tool I've
open-sourced. I may or may not respond to issues/PRs. Fork freely."*
That is the rationale for this fork: changes needed here ship here,
with no expectation of merging back upstream.

## Feature comparison

| Area | Upstream `ariel-frischer/kb` | This fork |
|---|---|---|
| Secrets | `OPENAI_API_KEY` / `OPENAI_BASE_URL` from process env or `~/.config/kb/secrets.toml` (env wins) | Same, plus project `.env` (walk-up from cwd). Precedence: process env > project `.env` > `secrets.toml`, so `secrets.toml` is optional |
| Index scoping | `sources` are directories only; exclusion via `.kbignore` only | Same directory-only `sources`, plus `include_patterns` whitelist (`Config`, `.kb.toml`) and persistent `kb index <dir> --include "PAT"` scoped indexing. `.kbignore` always wins over includes |
| `kb index DIR` without flags | One-off index of the given dirs, config untouched | Unchanged |
| `kb index --include` | Not present | Persistent: merges dirs into `sources` (never drops existing entries) and patterns into `include_patterns` (union), saves `.kb.toml`, then indexes the requested scope |
| Incremental indexing | MD5 per file/chunk; unchanged skipped, changed reprocessed | Unchanged; include-excluded files are filtered before extract/hash/embed, and re-running a scope does not re-embed unchanged files |
| Windows config encoding | Bare TOML load (fails opaquely on ANSI-encoded files) | `ConfigError` with UTF-8 guidance for `.kb.toml` / `secrets.toml` / `.env` |
| Install | `uv tool install --from "git+https://github.com/ariel-frischer/kb.git" ...` | Repointed to `huydhoang/kb` with corrected `uv tool install "kb[extra] @ git+https://..."` syntax |
| Everything else (search, ask, fts, rerank, HyDE, tags, MCP, formats) | As documented in upstream README | Same behavior |

## Details

### Project `.env` support

`load_secrets()` (`src/kb/config.py`) additionally looks for a project `.env`
by walking up from the cwd (same convention as `.kb.toml` discovery) and parses
it without new dependencies (blank lines, `#` comments, `export` prefix,
single/double quotes). Use it for per-project `OPENAI_API_KEY` /
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

## Which should you choose?

- **Upstream** — you want the original author's line, minimal surface, and are
  fine with env/`secrets.toml` secrets and exclude-only indexing.
- **This fork** — you want project-local `.env` secrets and incremental
  include-filtered indexing of large trees, and accept tracking this fork
  instead of upstream.

## Sync policy

No automatic sync with upstream is promised. Upstream fixes may be ported here
on a case-by-case basis; fork-specific features will not be pushed upstream.
