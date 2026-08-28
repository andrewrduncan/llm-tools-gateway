"""
OpenAI-compatible gateway that gives EVERY client server-side tools.

Sits on :8000 in front of vLLM (:8001). Any client -- Open WebUI, opencode,
curl -- gets datetime / web_search / fetch_url / browse_page for free, with no
client configuration.

Client-supplied tools are passed through untouched and never executed here:
only tools this gateway owns are run locally. That is what keeps opencode's
file-editing tools working through the same endpoint.
"""
import os, json, uuid, time, logging, asyncio
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse, Response
import tools as T

UPSTREAM = os.environ.get("VLLM_URL", "http://host.docker.internal:8001")
KEYS_FILE = os.environ.get("KEYS_FILE", "/etc/llm-gateway/keys.json")
# Stream progress notices while gateway tools run. Uses reasoning_content, which
# clients RENDER but never EXECUTE -- unlike real tool_calls, which opencode would
# try to run and choke on. Open WebUI shows it as a collapsible "Thinking" block.
PROGRESS = os.environ.get("GATEWAY_PROGRESS", "1") not in ("0", "false", "")
# Coding agents default to temperature 0. Greedy decoding can enter a token cycle
# it cannot escape -- observed in opencode: the same paragraph repeated ~6 times
# until the turn was killed. A small repetition penalty breaks the cycle with
# negligible effect on determinism. Only applied when the client sets no
# anti-repetition parameter of its own.
REP_PENALTY = float(os.environ.get("DEFAULT_REPETITION_PENALTY", "1.1"))
# Greedy decoding (temperature 0) CANNOT escape a repetition cycle -- if the top
# token leads back to a prior state it re-enters deterministically, forever.
# Coding agents default to 0 for reproducibility. A small floor gives the sampler
# an escape route at negligible cost to determinism.
MIN_TEMP = float(os.environ.get("MIN_TEMPERATURE", "0.3"))
MAX_ROUNDS = int(os.environ.get("MAX_TOOL_ROUNDS", "6"))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("gateway")
app = FastAPI()


def is_internal_task(messages):
    """Open WebUI fires hidden LLM calls for chat titles / tags / follow-ups.
    They are recognisable by its prompt template. Injecting tools into those
    makes the model waste real tool calls on bookkeeping, so skip them."""
    for m in (messages or [])[-2:]:
        c = m.get("content")
        if isinstance(c, str) and "### Task:" in c and "### Guidelines:" in c:
            return True
    return False


ICONS = {"web_search": "\U0001F50D", "fetch_url": "\U0001F4C4",
         "browse_page": "\U0001F310", "generate_image": "\U0001F3A8",
         "edit_image": "\u2702\uFE0F", "search_images": "\U0001F5BC\uFE0F",
         "get_current_datetime": "\U0001F553", "remember": "\U0001F4BE",
         "recall": "\U0001F9E0", "forget": "\U0001F5D1\uFE0F",
         "search_documents": "\U0001F4DA"}


def _brief(args: dict, limit: int = 60) -> str:
    """Compact one-line arg summary for a progress notice."""
    parts = []
    for k, v in (args or {}).items():
        v = str(v)
        if len(v) > limit:
            v = v[:limit] + "..."
        parts.append(f"{k}={v}")
    return ", ".join(parts)[:160]


def _load_keys():
    """apiKey -> {subject, label}. Reloaded per call so keys can be edited live."""
    try:
        with open(KEYS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def identify(request):
    """Work out WHO is calling. Two mechanisms, deliberately separate:
       - Open WebUI forwards per-USER headers (one key, many humans)
       - other clients present a per-MACHINE api key
       Returns (subject, source). subject=None means the shared namespace."""
    h = request.headers
    uid = h.get("x-openwebui-user-id") or h.get("x-openwebui-user-email")
    if uid:
        return uid, "open-webui"
    auth = h.get("authorization", "")
    if auth.lower().startswith("bearer "):
        rec = _load_keys().get(auth[7:].strip())
        if rec:
            return rec.get("subject"), rec.get("label", "api-key")
    return None, "anonymous"


def merge_tools(client_tools):
    """Client tools win on name collision; ours are appended."""
    names = {t.get("function", {}).get("name") for t in (client_tools or [])}
    return list(client_tools or []) + [d for d in T.DEFS
                                       if d["function"]["name"] not in names]


async def call_upstream(client, body):
    r = await client.post(f"{UPSTREAM}/v1/chat/completions", json=body,
                          timeout=httpx.Timeout(600.0))
    r.raise_for_status()
    return r.json()


async def execute(tool_calls, subject=None, source=None):
    """Run our tools. Returns (tool_messages, images_to_inject)."""
    msgs, images = [], []
    for tc in tool_calls:
        fn = tc["function"]
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except Exception:
            args = {}
        log.info("tool: %s(%s) [subject=%s]", fn["name"], json.dumps(args)[:140], subject)
        text, img = await T.run(fn["name"], args, subject=subject, source=source)
        msgs.append({"role": "tool", "tool_call_id": tc["id"],
                     "name": fn["name"], "content": text})
        if img:
            images.append(img)
    return msgs, images


def sse(obj):
    return f"data: {json.dumps(obj)}\n\n".encode()


def chunk(cid, model, delta, finish=None):
    return {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
            "model": model, "choices": [{"index": 0, "delta": delta,
                                         "finish_reason": finish}]}


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    subject, source = identify(request)
    if REP_PENALTY > 1.0 and not any(
            k in body for k in ("repetition_penalty", "frequency_penalty",
                                "presence_penalty")):
        body["repetition_penalty"] = REP_PENALTY
    if MIN_TEMP > 0 and float(body.get("temperature") or 0) < MIN_TEMP:
        body["temperature"] = MIN_TEMP
    stream = bool(body.pop("stream", False))
    want_usage = bool((body.pop("stream_options", None) or {}).get("include_usage"))
    client_tools = body.get("tools")
    body["tools"] = merge_tools(client_tools)
    if not body.get("tool_choice"):
        body["tool_choice"] = "auto"
    if is_internal_task(body.get("messages")):
        # bookkeeping call -- proxy verbatim, no tools, no loop
        body["stream"] = stream
        body.pop("tools", None)
        body.pop("tool_choice", None)
        async with httpx.AsyncClient() as c:
            if not stream:
                return JSONResponse(await call_upstream(c, body))
        async def passthru():
            async with httpx.AsyncClient() as c:
                async with c.stream("POST", f"{UPSTREAM}/v1/chat/completions",
                                    json=body, timeout=httpx.Timeout(600.0)) as r:
                    async for line in r.aiter_lines():
                        if line:
                            yield (line + "\n\n").encode()
        return StreamingResponse(passthru(), media_type="text/event-stream")

    client_names = {t.get("function", {}).get("name") for t in (client_tools or [])}
    if client_names:
        log.info("client sent %d tool(s): %s", len(client_names), sorted(client_names))
    messages = list(body.get("messages") or [])
    model = body.get("model", "unknown")

    def mine_only(tcs):
        """True if every tool call belongs to this gateway."""
        return tcs and all(t["function"]["name"] in T.NAMES
                           and t["function"]["name"] not in client_names for t in tcs)

    async def apply_progress(msg, tcs):
        """Same as apply(), but yields human-readable progress strings so the
        streaming path can surface what the gateway is doing. Without this the
        client sees up to ~45s of total silence during image generation."""
        messages.append({"role": "assistant", "content": msg.get("content"),
                         "tool_calls": tcs})
        images = []
        for tc in tcs:
            fn = tc["function"]
            name = fn["name"]
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                args = {}
            icon = ICONS.get(name, "\U0001F527")
            yield f"{icon} {name}({_brief(args)})\n"
            log.info("tool: %s(%s) [subject=%s]", name, json.dumps(args)[:140], subject)
            t0 = time.time()
            # Run the tool as a task so we can emit heartbeats while it works.
            # generate_image takes ~50s; without this the client goes silent and
            # the user cannot tell "working" from "hung".
            task = asyncio.create_task(T.run(name, args, subject=subject, source=source))
            while True:
                done, _ = await asyncio.wait({task}, timeout=10)
                if done:
                    break
                yield f"   ... still running ({time.time() - t0:.0f}s)\n"
            text, img = task.result()
            dt = time.time() - t0
            ok = "error" not in (text or "")[:80].lower()
            yield f"   {'done' if ok else 'FAILED'} in {dt:.1f}s\n"
            messages.append({"role": "tool", "tool_call_id": tc["id"],
                             "name": name, "content": text})
            if img:
                images.append(img)
        for im in images:
            messages.append({"role": "user", "content": [
                {"type": "text", "text": "Screenshot from browse_page:"},
                {"type": "image_url",
                 "image_url": {"url": "data:image/png;base64," + im}}]})

    async def apply(msg, tcs):
        """Append the assistant turn, run the tools, inject any screenshots."""
        messages.append({"role": "assistant", "content": msg.get("content"),
                         "tool_calls": tcs})
        tool_msgs, images = await execute(tcs, subject=subject, source=source)
        messages.extend(tool_msgs)
        for img in images:
            messages.append({"role": "user", "content": [
                {"type": "text", "text": "Screenshot from browse_page:"},
                {"type": "image_url",
                 "image_url": {"url": "data:image/png;base64," + img}}]})

    # ---------------------------------------------------------- non-streaming
    if not stream:
        async with httpx.AsyncClient() as client:
            for _ in range(MAX_ROUNDS):
                body["messages"] = messages
                data = await call_upstream(client, body)
                msg = data["choices"][0]["message"]
                tcs = msg.get("tool_calls") or []
                if not mine_only(tcs):
                    return JSONResponse(data)
                await apply(msg, tcs)
            body["messages"] = messages
            body["tool_choice"] = "none"
            return JSONResponse(await call_upstream(client, body))

    # ---------------------------------------------------------- streaming
    async def gen():
        cid = "chatcmpl-" + uuid.uuid4().hex[:24]
        usage_total = {}
        yield sse(chunk(cid, model, {"role": "assistant", "content": ""}))
        async with httpx.AsyncClient() as client:
            for _ in range(MAX_ROUNDS):
                body["messages"] = messages
                body["stream"] = True
                # A tool turn makes several upstream calls; usage must be SUMMED
                # or the client under-reports the true cost of the turn.
                body["stream_options"] = {"include_usage": True}
                acc_tcs, content, finish = {}, [], "stop"
                # Hold early content back: if this turn turns out to be a tool
                # call, that text was just narration ("I'll check...") and the
                # user should never see it. Flush once it is clearly a real
                # answer, then stream live for the rest of the turn.
                buf, buf_len, dropped = [], 0, False
                FLUSH_AT = 200
                async with client.stream("POST", f"{UPSTREAM}/v1/chat/completions",
                                         json=body,
                                         timeout=httpx.Timeout(600.0)) as r:
                    async for line in r.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        payload = line[6:]
                        if payload == "[DONE]":
                            break
                        obj = json.loads(payload)
                        u = obj.get("usage")
                        if u:
                            for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                                usage_total[k] = usage_total.get(k, 0) + (u.get(k) or 0)
                        if not obj.get("choices"):
                            continue          # usage-only chunk carries no delta
                        ch = obj["choices"][0]
                        d = ch.get("delta") or {}
                        if d.get("content"):
                            content.append(d["content"])
                            if dropped:
                                pass                      # narration -- discard
                            elif buf is not None:
                                buf.append(d["content"])
                                buf_len += len(d["content"])
                                if buf_len >= FLUSH_AT:   # real answer, go live
                                    for piece in buf:
                                        yield sse(chunk(cid, model, {"content": piece}))
                                    buf = None
                            else:
                                yield sse(chunk(cid, model, {"content": d["content"]}))
                        for tc in (d.get("tool_calls") or []):
                            if buf is not None:
                                buf, dropped = [], True    # drop the preamble
                            i = tc.get("index", 0)
                            slot = acc_tcs.setdefault(
                                i, {"id": None, "type": "function",
                                    "function": {"name": "", "arguments": ""}})
                            if tc.get("id"):
                                slot["id"] = tc["id"]
                            f = tc.get("function") or {}
                            if f.get("name"):
                                slot["function"]["name"] += f["name"]
                            if f.get("arguments"):
                                slot["function"]["arguments"] += f["arguments"]
                        if ch.get("finish_reason"):
                            finish = ch["finish_reason"]
                tcs = [acc_tcs[k] for k in sorted(acc_tcs)]
                if not tcs and buf:
                    for piece in buf:                      # real answer, flush it
                        yield sse(chunk(cid, model, {"content": piece}))
                if not tcs:
                    yield sse(chunk(cid, model, {}, finish=finish))
                    if want_usage and usage_total:
                        yield sse({"id": cid, "object": "chat.completion.chunk",
                                   "created": int(time.time()), "model": model,
                                   "choices": [], "usage": usage_total})
                    yield b"data: [DONE]\n\n"
                    return
                if not mine_only(tcs):
                    # client owns these -- hand them back verbatim
                    yield sse(chunk(cid, model, {"tool_calls": tcs}))
                    yield sse(chunk(cid, model, {}, finish="tool_calls"))
                    if want_usage and usage_total:
                        yield sse({"id": cid, "object": "chat.completion.chunk",
                                   "created": int(time.time()), "model": model,
                                   "choices": [], "usage": usage_total})
                    yield b"data: [DONE]\n\n"
                    return
                async for note in apply_progress({"content": "".join(content) or None}, tcs):
                    if PROGRESS:
                        yield sse(chunk(cid, model, {"reasoning_content": note}))
            yield sse(chunk(cid, model, {}, finish="stop"))
            yield b"data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/img/{key:path}")
async def serve_image(key: str):
    """Re-serve a Garage object. Garage has no anonymous access, so image URLs
    must come through here to be viewable in a browser / Open WebUI."""
    import s3
    try:
        data, ctype = await s3.get(key)
    except Exception as e:
        return Response(content=f"not found: {e}", status_code=404)
    return Response(content=data, media_type=ctype,
                    headers={"Cache-Control": "public, max-age=31536000"})


@app.get("/health")
async def health():
    return {"status": "ok", "tools": sorted(T.NAMES), "upstream": UPSTREAM,
            "api_keys_loaded": len(_load_keys())}


@app.post("/admin/index")
async def admin_index(request: Request):
    """Ingest a document for RAG. Not a model-facing tool -- an operator endpoint.
       {"uri": "...", "title": "...", "text": "...", "subject": null}"""
    import memory as M
    b = await request.json()
    text = b.get("text", "")
    size = int(b.get("chunk_size", 1200))
    chunks = [text[i:i + size] for i in range(0, len(text), size)] or [""]
    return await M.index_document(b.get("uri"), b.get("title"), chunks,
                                  subject=b.get("subject"))


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def passthrough(path: str, request: Request):
    """Everything else (/v1/models, /metrics, ...) goes straight to vLLM."""
    async with httpx.AsyncClient() as client:
        r = await client.request(
            request.method, f"{UPSTREAM}/{path}",
            content=await request.body(),
            headers={k: v for k, v in request.headers.items()
                     if k.lower() not in ("host", "content-length")},
            params=request.query_params, timeout=httpx.Timeout(600.0))
    return Response(content=r.content, status_code=r.status_code,
                    media_type=r.headers.get("content-type"))
