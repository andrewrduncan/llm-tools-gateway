# Wiki schema

This directory is an **LLM-maintained knowledge layer**. You (the LLM) own it
entirely: create pages, update cross-references, keep it consistent.

## Three layers

1. **Raw sources** — the code, benchmarks, upstream docs. Read, never rewrite here.
2. **This wiki** — generated markdown: summaries, entities, concepts, analyses.
3. **This schema** — how the wiki is structured and how to work in it.

## Layout

```
index.md          catalog of every page, one line each, grouped by category
log.md            append-only operations record
overview.md       the synthesis; start here
conventions.md    project usage preferences
sources/          summaries of external material (docs, benchmarks, issues)
entities/         concrete things: components, services, tools, models
concepts/         ideas and patterns: tool injection, partitioning, bandwidth limits
analyses/         comparisons, decisions, post-mortems
```

## Rules

**Front matter on every page.**
```yaml
---
title: ...
type: entity | concept | source | analysis
tags: [...]
updated: YYYY-MM-DD
sources: [path or URL, ...]     # what this claim rests on
verified_at: YYYY-MM-DD         # when it was last checked against reality
---
```

**Cite or omit.** Every non-obvious claim carries a source: a file path, a
command and its output, or a URL. A number with no provenance is a liability —
prefer no page to an unsourced one.

**Never copy mutable state into prose.** Do not write "the model is
Qwen3-Coder-30B". Write "the active model is set by `UPSTREAM_URL` and the
server's `--alias`" and point at where it lives. Document *shapes*, not values.

**Supersede, don't delete.** When something is corrected, mark what it replaces
and keep the history visible. Wrong-then-fixed is more useful than a clean page
that hides the trap.

**Surface contradictions.** If a new source disagrees with a page, quote both
sentences and flag it rather than silently picking one.

**Drift detection.** `verified_at` older than the code it describes means the page
is suspect. A lint pass should list stale pages, orphans, and broken references.

**Update `index.md` and `log.md` on every change.** The index is the map; the log
is the history.

## Workflows

- **ingest** — read a source, write/refresh `sources/`, update touched pages, add
  to `index.md`, append to `log.md`.
- **query** — answer from the wiki; if the wiki cannot answer, say so and note the
  gap in `log.md`.
- **lint** — find stale `verified_at`, orphan pages, unsourced claims, contradictions.
