# Include / Ignore Patterns

`sources` in `.kb.toml` are directories only (no globs). Use `include_patterns`
to whitelist subsets and `.kbignore` to exclude files.

## Two workflows

1. Incremental add + normal index
   - `kb add <dir>` persists directories to `sources`.
   - `kb index` indexes everything in `.kb.toml` (empty `include_patterns` = all files).
   - Re-indexing is incremental: unchanged files are skipped via MD5, changed
     files are reprocessed, unchanged chunks are reused.
2. Scoped index (persistent)
   - `kb index <dir> --include "PATTERN"` merges the directory into `sources`
     (existing entries are never dropped) and merges the pattern into
     `include_patterns`, saves `.kb.toml`, then indexes the requested scope.
   - Existing `sources` are preserved so the TOML keeps describing the full
     scope represented by the DB. The directory entry stays directory-only;
     it is never replaced by a glob.
   - `include_patterns` is global: on later full `kb index` runs it applies to
     every source. Supply additional `--include` patterns to broaden the scope
     (a file must match at least one pattern). To return to full indexing,
     clear `include_patterns` and re-run `kb index`.

## Precedence

1. Extension filter first (supported formats only).
2. `include_patterns` (if non-empty, a file must match at least one pattern;
   empty = all files). Filtering happens before extraction, hashing,
   chunking, and embedding, so excluded files cost nothing.
3. `.kbignore` always wins over `include_patterns`.

## How it works

- Patterns use **fnmatch** glob syntax (like shell globs, not full gitignore)
- Lines starting with `#` are comments, blank lines are ignored
- Each pattern matches against the **relative path** from the source directory and the **filename**
- Directory patterns end with `/` — matches any file under that directory
- Lookup: checks `<source-dir>/.kbignore`, then `<source-dir>/../.kbignore` (first match wins)

## Common patterns

### Obsidian / note-taking

```
.obsidian/
.trash/
templates/
*.excalidraw.md
```

### Development docs

```
node_modules/
vendor/
dist/
build/
_site/
.venv/
CHANGELOG.md
CHANGELOG-*.md
```

### Work-in-progress

```
drafts/
WIP-*
*.draft.md
*.tmp.md
TODO.md
```

### Generated / auto-created

```
*.gen.md
*.auto.md
api-reference/
_generated/
```

### Large / generated files

```
*.min.js
*.bundle.js
package-lock.json
yarn.lock
*.min.css
*.map
```

### Private / sensitive

```
internal/
private/
*-secret*
.env*
```

## Example `.kbignore`

A typical setup for a repo with docs, notes, and generated content:

```
# Tooling
.obsidian/
node_modules/
.venv/

# Not useful to index
CHANGELOG.md
LICENSE

# Work in progress
drafts/
*.draft.md
WIP-*

# Generated
_generated/
api-reference/
```

## Pattern matching details

| Pattern | Matches | Doesn't match |
|---|---|---|
| `drafts/` | `drafts/foo.md`, `drafts/sub/bar.md` | `my-drafts/foo.md` |
| `*.draft.md` | `notes.draft.md`, `sub/notes.draft.md` | `notes.md` |
| `WIP-*` | `WIP-feature.md` | `my-WIP-feature.md` (path) |
| `CHANGELOG.md` | `CHANGELOG.md` | `docs/CHANGELOG.md` (path only matches filename) |

Note: filename patterns (no `/`) match against both the full relative path and the bare filename. Directory patterns (trailing `/`) only match against the relative path prefix.
