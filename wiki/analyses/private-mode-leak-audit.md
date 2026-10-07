---
title: Private mode leak audit
type: analysis
tags: [privacy, testing, post-mortem]
updated: 2026-10-07
sources: [gateway/server.py, gateway/tools.py]
verified_at: 2026-10-07
---

# Private mode leak audit

Private mode was built feature by feature and each piece worked in isolation.
Every failure found afterwards was at a **seam** — and each was found only by
running the real path and then sweeping every layer for traces, never by
reasoning about the code.

## Method

Generate and edit an image in a private turn using a unique token
(`ZARQUON-7741`), then grep for it across: Postgres (`memories`, `files`
including the `prompt` column, `documents`), the object store, ComfyUI's output
/ input / temp directories, the client's own database, and **container logs on
the host**.

A unique nonsense token is the right probe. Content is not what persists —
metadata and artifacts are — and a distinctive string proves absence with no
false positives.

## What leaked, in the order found

**Nothing was private at all.** The first run used the client's Temporary Chat
toggle, which never reaches the backend. Every counter moved: a memory written,
an index row, an object stored, a generator file kept. See [[private-mode]] for
why that route cannot work.

**The prompt was in the container log.** Storage was clean, and then
`tool: generate_image({"prompt": "A banner with the text 'ZARQUON-7741'..."})`
sat in `/var/lib/docker/containers/<id>/<id>-json.log` on the host, outliving
the conversation. Arguments are now redacted on private turns.

**Edit sources accumulated forever.** The pre-extraction gateway swept them; the
refactor dropped the sweep while the deployment still bind-mounted the directory
for code that no longer existed. A user's own upload — never indexed, never
stored — sat in ComfyUI's input directory indefinitely. Now uploaded to a
tmpfs-backed temp directory, deleted on every exit path, and cleared by ComfyUI
at startup.

**Images were written then deleted.** The first implementation let ComfyUI write
the PNG and removed it afterwards. That is cleanup, not privacy: it leaves a
window, fails open on a crash between write and delete, and unlink does not
scrub blocks. Replaced with `SaveImageWebsocket`.

## Failures that were not leaks

Worth recording because they shaped the design.

**The model emitted base64.** With no URL to show, it tried to write the data
URI itself and streamed thousands of junk characters. Instructing it not to was
never going to hold — displaying an image is the obviously correct behaviour and
it had no other way. The gateway attaches the image instead.

**The model invented URLs.** `edit_image` required an `image_url` that private
mode had made impossible to obtain. Asked to edit "this image" it produced
`i.imgur.com/5k0Zc1S.jpg`, then `1i1i1i1.jpg`, then `removed.png` — imgur being
the shape it reaches for, and `removed.png` a real placeholder that appears
throughout scraped pages. A tool that requires something unobtainable does not
fail cleanly; it gets fabricated input.

**A misattributed error caused a hallucination loop.** An `UnboundLocalError`
surfaced as "could not read the source image". The model read that as a bad URL
and spent five tool calls hunting for a better one. An error that points at the
wrong cause is worse than a blunt one.

## Conclusions

- **A guarantee that depends on cleanup is not a guarantee.** Prefer never
  writing. Prefer withholding over instructing.
- **Logs are a storage layer.** They were the last thing checked and the only
  thing still leaking after storage was clean.
- **Audit the seams, not the features.** Every failure here was an interaction
  between two things that each worked.
- **Removing a capability creates obligations elsewhere.** Private mode removed
  every way an image could have an address, and a tool still demanded one.
