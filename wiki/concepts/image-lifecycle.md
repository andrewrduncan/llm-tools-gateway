---
title: Image lifecycle and the three copies
type: concept
tags: [images, storage, deletion, privacy]
updated: 2026-10-07
sources: [gateway/tools.py, gateway/memory.py, gateway/backends/s3.py, schema.sql]
verified_at: 2026-10-07
---

# Image lifecycle and the three copies

One generated image exists in **three independent places**, written by
`generate_image` / `edit_image` (`gateway/tools.py`). This describes a *normal*
turn; a private turn writes **none of them** — see [[private-mode]].

| Copy | Written by | Removing it stops |
|---|---|---|
| Generator output file | ComfyUI itself | nothing user-visible |
| Object store | `s3.put()` | the public URL resolving |
| Index row | `memory.index_file()` | the image being findable |

They are written together and are **not** removed together by anything except
`delete_image`. Deleting through the generator's own UI — the most intuitive place
to look — clears the copy that matters least: the object keeps serving and the row
keeps pointing at it.

`files.source_file` exists solely so the generator-side filename survives; without
it the third copy is unreachable once the response is gone.

## Keys must be opaque AND copyable

Keys were originally `gen/{seed}-{w}x{h}-{filename}`. Every component except the
seed is predictable, the seed is 32 bits, and a caller-supplied seed makes the whole
key deterministic — acceptable while the endpoint is LAN-only, enumerable the moment
it is published.

The first fix was `uuid4`, and it broke image rendering. **Models retype URLs rather
than copying them**, and uuid4's hyphen groups are re-grouped in the process. Observed
with Gemma 4:

```
returned by the tool: https://host/img/gen/37980a9e-764e-4990-aacc-f92b1a5f3be7.png
emitted by the model: https://host.img/gen/3798-0a9e-764-e499-0aacc-f92b-1a5f-3be7.png
```

Both the path separator and the key were corrupted; the image rendered as a broken
link. The previous descriptive keys had survived this because their structure was
regular enough to retype correctly.

Keys are now 16 characters from a 32-symbol lowercase alphabet with no hyphens and
no `l/o/0/1` lookalikes — 80 bits, still far beyond brute force, and materially
easier to reproduce verbatim.

**Do not rely on the model getting it right.** `repair_image_urls()` in
`gateway/server.py` rewrites any markdown image link that does not match a URL the
tools actually returned this turn, and appends one if the model linked nothing at
all. The gateway owns the tool result, so it owns the rendering.

## The serving endpoint is unauthenticated

`/img/{key}` re-serves any object, because the object store refuses anonymous
access and a browser has no credentials. Opaque keys mean nobody can enumerate,
but **anyone holding a URL can fetch it indefinitely** — a shared link stays live
until the object is deleted. Obscurity is not authorization; a deployment that
needs real access control has to add it at this endpoint.

See [[subject-partitioning]] for why deleting uses a stricter rule than reading.
