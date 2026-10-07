---
title: CI and image publishing
type: entity
tags: [ci, deployment, operations]
updated: 2026-10-07
sources: [.github/workflows/docker.yml]
verified_at: 2026-10-07
---

# CI and image publishing

GitHub Actions builds the image on push and publishes it to
`ghcr.io/<owner>/llm-tools-gateway`. Pull requests build but do not push.

| Tag | Meaning |
|---|---|
| `:latest` | newest commit on the default branch; what an auto-updater follows |
| `:sha-xxxxxxx` | immutable, for pinning or rollback |
| `:vX.Y.Z` / `:vX.Y` | on git tags, for pinning to a release line |

## Why it exists

Before this, deployments built the image locally. That meant compiling the
Playwright base on every change, and the image could never be auto-updated —
which is also how a deployment silently drifted ~155 lines behind this
repository until the gap was noticed.

## Operational notes

**Wait for the run to complete before pulling.** A pull mid-build silently
deploys the previous image, and the resulting test is a measurement of old code.
This produced several confusing results before it was recognised.

**The package must be public** for an unauthenticated host to pull it. Verify
with an anonymous token request rather than the API, which reports differently
depending on the caller's scopes.

**Pin the runner.** `ubuntu-latest` migrates between OS versions on a schedule
set by GitHub. A job that publishes the image a production box deploys should
not change host OS unannounced.

## Deployment

A deployment pins `image:` to a tag and lets its own updater pull. The gateway
is stateless — all state is in Postgres, the object store and mounted
workflows — so a rolling replacement is safe at any time.
