---
title: Log
type: index
updated: 2026-10-07
---

# Log

Append-only. Newest last.

## [2026-08-28] init | wiki created
Established schema (`CLAUDE.md`), overview, index, and first entity/concept/analysis
pages. Seeded from the build-out of the gateway and the vLLM→llama.cpp migration.

## [2026-08-30] change | delete_image, opaque keys, write-side partitioning
Added `s3.delete()` (the backend had no delete at all), `files.source_file`, and a
`delete_image` tool that removes all three copies of an image. Switched object keys
from `gen/{seed}-{dims}-{filename}` to `gen/{uuid4}.png` after publishing `/img`
through a reverse proxy made the old scheme enumerable.

Found and fixed a real authorization defect: `delete_image` first reused the *read*
partition predicate, so `OR subject IS NULL` let any caller delete shared images. A
test confirmed it by destroying one. `find_file` now takes `for_write` and requires
an exact subject match. New page: [image-lifecycle](concepts/image-lifecycle.md);
[subject-partitioning](concepts/subject-partitioning.md) updated.

## [2026-08-31] change | copyable image keys, URL repair, sampling dialects
uuid4 keys proved unguessable but not copyable: models retype image URLs and corrupt
them, so images rendered broken. Keys are now 16 hyphen-free characters (80 bits), and
`repair_image_urls()` rewrites any link that does not match what the tools returned —
the gateway no longer depends on the model transcribing correctly.

Found the anti-repetition guard had been inert since the engine migration: llama.cpp
ignores `repetition_penalty` (its name is `repeat_penalty`) and drops unknown keys
silently. Now sends both spellings plus DRY. New page:
[engine-parameter-dialects](concepts/engine-parameter-dialects.md).

Also: `server.py` was re-reading six env vars that `config.py` already defined, so the
two could drift; it now imports config, as the module docstring always claimed.

## [2026-10-07] change | private mode, quoting vs anti-loop, image context cost, CI

Added **private mode**: a turn that writes nothing. Persistence tools are withheld
from the tool list rather than discouraged, generated images arrive over the
websocket instead of being written and deleted, the gateway attaches them rather
than asking the model to emit base64, and tool arguments are redacted from logs.
New pages: [private-mode](concepts/private-mode.md),
[private-mode-leak-audit](analyses/private-mode-leak-audit.md).

Found the anti-repetition guard **corrupts quoted tool output**. DRY cannot tell
faithful quotation from a degenerate loop, so a date supplied by
`get_current_datetime` came back as October 06 **2178**. 0/4 correct with DRY at
any look-back, 3/3 without. This partly supersedes
[engine-parameter-dialects](concepts/engine-parameter-dialects.md), which
recorded adding DRY as a fix. New page:
[verbatim-vs-anti-loop](concepts/verbatim-vs-anti-loop.md).

A second image turn needed ~13 minutes of prefill: a 105k-token prompt, because
llama.cpp disables prefix caching for multimodal and re-encodes every historical
image every request. History images are now stripped to a text placeholder. New
page: [context-cost-of-images](concepts/context-cost-of-images.md).

`edit_image` required an `image_url` that private mode had made impossible to
obtain; the model invented imgur links to fill the slot. The gateway now resolves
the newest image in the conversation and injects it, as it already did for
`subject`.

Fixed `COMFY_PUB` (referenced, never defined — would have thrown on every
generation) and a function-local `import base64` shadowing the module-level one,
which broke every edit with `UnboundLocalError`. Both are the same class of bug
and are now covered by an undefined-name scan.

CI publishes the image to ghcr.io; deployments pull a tag instead of building
locally. New page: [ci-pipeline](entities/ci-pipeline.md). Added a root
`CLAUDE.md` covering invariants, workflow and known traps.

**Still open:** `workflow.load()` is never called, so the mounted workflow JSON
files are decorative and the README's "export your own and drop it in" is untrue.
