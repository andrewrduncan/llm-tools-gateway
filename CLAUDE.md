# llm-tools-gateway

An OpenAI-compatible proxy that gives **any** local model real tools — web search,
browsing, memory, RAG, image generation — with zero client-side setup. It sits in
front of a model server and owns the tool loop.

```
any OpenAI-compatible client
          │
          ▼
   llm-tools-gateway  ──►  search · fetch · headless browser
          │                memory (pgvector) · RAG · image gen/edit
          ▼
   model server           (llama.cpp · vLLM · Ollama · …)
```

`wiki/` is an LLM-maintained knowledge layer with the reasoning behind all of
this. Read `wiki/index.md` before a non-trivial change; `wiki/CLAUDE.md` governs
how to write in it.

---

## Invariants — breaking these breaks the product

**Client tools are never executed here.** Tools the client sent are merged into
the request and handed back to that client to run. Only tools in `T.DEFS` execute
server-side. Drop the split and agentic clients break completely, because the
gateway would swallow their file-edit calls.

**Identity is injected, never accepted.** `subject` comes from the request
headers or the API key, never from the model. `SUBJECT_TOOLS` in `tools.py` lists
what gets it. If the model could set it, it could read another user's memories.
`private` and `conversation_image` follow the same rule for the same reason.

**The tool list is static.** ~1,400 tokens of schemas sit in an identical prefix
on every request, so prefix caching makes them nearly free. Any dynamic tool
selection — a `get_tools` call, keyword routing — destroys that and costs more
than it saves.

**The gateway owns rendering.** A model cannot reliably reproduce a URL, let
alone 150 KB of base64. `repair_image_urls()` fixes mangled links and
`attach_inline()` attaches private images directly. Never make correct output
depend on the model transcribing something.

**Tools degrade, never break.** A tool whose backend is unconfigured is not
offered. See `config.capabilities()`. The model must never see a tool that
cannot run.

---

## How work ships

```
edit  →  git push  →  GitHub Actions  →  ghcr.io/<owner>/llm-tools-gateway:latest
                                      →  deployment pulls (Watchtower, ~3 min)
```

Tags: `:latest` on main, `:sha-xxxxxxx` immutable for rollback, `:vX.Y.Z` and
`:vX.Y` on git tags. Pin to `:vX.Y` for a stable line.

**CI must be green before a deployment pulls.** Pulling mid-build silently
deploys the previous image and produces confusing test results. Wait for the run
to complete, then pull.

---

## Development style

**Configuration over code.** Everything deployment-specific lives in `config.py`
and the environment. No hostnames, model filenames or paths in logic.

**Document shapes, not values.** "The active model is whatever the upstream
advertises", never "the model is Qwen3-VL-30B".

**Tool descriptions are part of the product.** They steer behaviour more than the
implementation does. `recall` was ignored entirely until its description became
directive. Be explicit and imperative; say what NOT to do when the model's
instinct is wrong (`edit_image`: "NEVER invent a URL").

**Error messages steer the model too.** A misattributed error is worse than a
blunt one. "Could not read the source image" for what was actually a scoping bug
sent the model hunting for better URLs and it invented five of them. If an error
is not actionable by the model, say so plainly.

**Make guarantees structural, not instructed.** Withhold a tool rather than
telling the model not to call it. Never write a file rather than deleting it
afterwards. Attach an image rather than asking the model to emit it. Anything
phrased as "the model should not…" will eventually not hold.

**Measure before claiming.** Every performance number in the wiki carries a
command or a cited source. Validate projections against something already
measured before trusting them on something new.

---

## Verifying a change

Local, no services needed:

```bash
python3 -m compileall -q gateway/          # syntax
python3 -c "import ast, builtins; ..."     # undefined-name scan (see below)
```

An undefined-name scan is worth running on every change — it caught `COMFY_PUB`,
which would have thrown on every image generation. A function-local `import X`
that shadows a module-level import is the same class of bug and has bitten twice
(`base64` in `edit_image`); check for it explicitly.

Against a running deployment, exercise the real path rather than unit-testing
around it. Most failures in this codebase appeared at the seams:

- generation and editing, private and normal
- a second image turn (history and context growth)
- streaming **and** non-streaming — they are separate code paths and have broken
  independently
- then sweep every persistence layer for traces

---

## Known dead code and traps

**`workflow.load()` is never called.** `WORKFLOW_GENERATE` / `WORKFLOW_EDIT` only
gate `capabilities()` by checking the files exist; the graphs are hardcoded dicts
in `tools.py`. The README promises you can drop in exported JSON. You cannot.
Either wire the loader up or correct the README.

**Anti-loop sampling and verbatim quotation are in conflict.** DRY penalises
repeated sequences, and copying a date out of a tool result *is* a repeated
sequence. See `wiki/concepts/verbatim-vs-anti-loop.md`.

**`LoadImage` has no base64 variant.** An edit source must exist as a file for
ComfyUI to read, which is why it goes to a tmpfs-backed temp dir rather than
being passed inline.

**Multimodal disables prefix caching in llama.cpp** (`cache_reuse is not
supported by multimodal`). Every image in history is re-encoded on every turn.
This is why history images are stripped.
