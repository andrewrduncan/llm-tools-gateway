# llm-tools-gateway

**Give any local LLM real tools — web search, browsing, memory, RAG, image
generation — without configuring a single client.**

A small OpenAI-compatible proxy that sits in front of your model server. It
injects tool definitions into every request, executes the tools server-side, and
returns the finished answer. Every client that talks to it — Open WebUI, opencode,
Aider, `curl`, your own code — gets the same capabilities with **zero client-side
setup**.

```
any OpenAI-compatible client
          │
          ▼
   llm-tools-gateway  ──►  web search · fetch · headless browser
          │                memory (pgvector) · RAG · image gen/edit
          ▼
   your model server      (vLLM · llama.cpp · Ollama · LM Studio · TGI …)
```

## Why

Tools in the OpenAI API are **client-supplied per request**. That means every
client re-implements them, and any client that can't host tools never gets them.
Moving tools to the endpoint fixes that once:

- Add a tool → every client has it, immediately
- Swap the model or the whole inference engine → tools are unaffected
- Clients that can't run tools (plain `curl`, minimal SDKs) get them anyway

## Backend-agnostic by design

The gateway speaks only OpenAI HTTP, so anything implementing
`/v1/chat/completions` works:

```bash
UPSTREAM_URL=http://localhost:8000   # vLLM
UPSTREAM_URL=http://localhost:8080   # llama.cpp (llama-server)
UPSTREAM_URL=http://localhost:11434  # Ollama
```

## Tools degrade gracefully

**Every tool is optional and self-disabling.** Configure only what you have; the
model is offered only what can actually run.

| Tool | Requires | Without it |
|---|---|---|
| `get_current_datetime` | — | always on |
| `fetch_url` | — | always on |
| `web_search` | `SEARXNG_URL` | hidden |
| `browse_page` | Playwright image | hidden |
| `remember` / `recall` / `forget` | `PG_DSN` + `EMBED_URL` | hidden |
| `search_documents` (RAG) | `PG_DSN` + `EMBED_URL` | hidden |
| `generate_image` / `edit_image` | `COMFY_URL` + a workflow | hidden |
| `search_images` | above + `S3_*` | hidden |
| `delete_image` | `PG_DSN` + `EMBED_URL` + `S3_*` | hidden |

Run it with nothing but `UPSTREAM_URL` and you still get date/time and URL
fetching. Nobody sees a broken tool.

## Quick start

```bash
cp .env.example .env          # set UPSTREAM_URL at minimum
docker compose up -d
curl localhost:8000/health    # lists the tools that are actually live
```

Point your client at `http://localhost:8000/v1` instead of the model server.

## Multi-user memory

Memories are partitioned by subject:

```
write →  subject = caller identity, or NULL when explicitly shared
read  →  WHERE subject = :caller OR subject IS NULL
```

Personal memories stay personal; `NULL` is shared with everyone.

**Identity uses two separate mechanisms, deliberately:**

- **Per-user** — a UI like Open WebUI forwards `X-OpenWebUI-User-Id`, so one API
  key serves many humans and new users need no gateway change.
- **Per-machine** — CLI clients present an API key mapped to a subject in
  `keys.json`.

`subject` is **always injected server-side** and stripped from anything the model
supplies. If the model could set it, it could read another user's memories.

## Progress feedback

Slow tools stream progress over `reasoning_content`, so a 50-second image
generation never looks like a hang:

```
🎨 generate_image(prompt=a red sailboat at sunrise)
   ... still running (10s)
   done in 52.0s
```

`reasoning_content` is *rendered* by clients but never *executed* — unlike real
`tool_calls`, which a coding agent would try to run and fail on.

## ComfyUI workflows are yours

Image workflows are JSON in `workflows/`, not baked into the code, because node
graphs and model filenames differ per install. Export from ComfyUI
(**Workflow → Export (API)**) and drop it in. Placeholders `{{prompt}}`,
`{{width}}`, `{{height}}`, `{{seed}}`, `{{steps}}`, `{{instruction}}`, `{{image}}`
are substituted at call time.

## Documentation

`wiki/` is an LLM-maintained knowledge base covering architecture, decisions and
hard-won operational detail. Start at [`wiki/overview.md`](wiki/overview.md).

## License

Apache-2.0
