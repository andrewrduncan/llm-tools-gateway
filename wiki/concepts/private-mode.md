---
title: Private mode
type: concept
tags: [privacy, images, memory, tools]
updated: 2026-10-07
sources: [gateway/config.py, gateway/server.py, gateway/tools.py]
verified_at: 2026-10-07
---

# Private mode

A turn that leaves nothing behind. The reliable way to achieve that is **not to
write anything in the first place** — deleting afterwards means trusting that
every copy was found, and that the cleanup ran at all.

## Triggering

In priority order (`is_private()` in `gateway/server.py`):

1. `{"private": true}` in the request body
2. A chat id prefixed with any of `PRIVATE_CHAT_ID_PREFIXES` (default
   `temporary:,local:`)
3. A model name ending in `PRIVATE_MODEL_SUFFIX` (default `-private`)

The suffix is stripped before the request reaches the model server, which has
never heard of it.

**The chat-id route does not work with Open WebUI**, despite that client having
exactly the right concept (Temporary Chat, which prefixes ids with `temporary:`).
Its OpenAI router filters requests through an explicit parameter allowlist that
does not include `chat_id`, and pops `metadata` before forwarding
(`routers/openai.py`, verified against 0.11.4). Nothing identifying the chat can
reach the backend. Verified by test: a Temporary Chat still called `remember()`
and still stored a generated image. The model-suffix route exists for this.

`/v1/models` advertises a `-private` twin of each model named in
`PRIVATE_TWIN_MODELS` (empty = all), so private mode is selectable in any client
that reads the model list. A client filtering its picker to advertised models —
Open WebUI does — will never show an entry the gateway does not list.

## What changes

| | Normal | Private |
|---|---|---|
| Memory / RAG tools | offered | **withheld from the tool list** |
| Image → object store, index row, embedding | yes | **none** |
| Image on disk | ComfyUI writes it | **never written** |
| Image delivery | `/img/{key}` URL | **inline, dies with the chat** |
| Tool arguments in logs | logged | **`<redacted>`** |

## Each guarantee is structural

Anything phrased as "the model should not…" eventually will.

**Tools are withheld, not forbidden.** `PRIVATE_DISABLED_TOOLS` is filtered out
of the tool list. The model's own words when asked to remember something:
*"I cannot use a 'remember' tool as it is not listed among the available
functions."* Nothing to instruct, nothing to override.

**Images are never written, not written-then-deleted.** The workflow's
`SaveImage` node is swapped for `SaveImageWebsocket` and bytes arrive as binary
frames (8-byte header, then the image). Cleanup that can fail is not privacy.
Verified by polling the output directory twice a second across a full
generation: zero files.

**The gateway attaches the image, the model never emits it.** A private image
has no URL, so a model asked to display one tries to write the data URI itself —
it reconstructs the JPEG header from memory, emits the standard quantisation
table, then degenerates into `YGBgYGBg` forever. `attach_inline()` adds the real
image and strips any data URI the model produced, which is junk by definition.

**Logs are redacted.** The first audit found the image prompt in full in the
container log, which sits on the host in plaintext and outlives the
conversation. The tool *name* is still logged; what it was asked to do is not.

## The one weaker edge

`edit_image` must materialise its source as a file, because ComfyUI's
`LoadImage` has no base64 variant. It goes to ComfyUI's temp directory, which is
tmpfs-backed, so it is never on disk — but it is in RAM for the job's duration,
and tmpfs can be swapped under memory pressure. Generation has no such window;
editing does. See [[image-lifecycle]].

## Inline images are a running cost

An inline image lives in the context window and is re-sent every turn, so it is
re-encoded on every request — llama.cpp disables prefix caching entirely for
multimodal. It is therefore re-encoded as `PRIVATE_IMAGE_FORMAT` (JPEG by
default) at `PRIVATE_IMAGE_QUALITY`, and history images are stripped. See
[[context-cost-of-images]].

**WebP is the obvious choice and does not work.** llama.cpp decodes images with
stb_image, which has no WebP support, and fails with
`mtmd_helper_bitmap_init_from_buf: failed to decode webp buffer`. The format
must satisfy the *model server's* vision stack, not just the browser.
