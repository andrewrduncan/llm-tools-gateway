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

