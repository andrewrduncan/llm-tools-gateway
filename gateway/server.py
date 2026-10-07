"""
OpenAI-compatible gateway that gives EVERY client server-side tools.

Sits on :8000 in front of vLLM (:8001). Any client -- Open WebUI, opencode,
curl -- gets datetime / web_search / fetch_url / browse_page for free, with no
client configuration.

Client-supplied tools are passed through untouched and never executed here:
only tools this gateway owns are run locally. That is what keeps opencode's
file-editing tools working through the same endpoint.
"""
import os, re, json, uuid, time, logging, asyncio
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse, Response
from . import tools as T

# All deployment settings come from config.py -- server.py re-reading os.environ
# duplicated those definitions and let the two drift apart.
from .config import (UPSTREAM, KEYS_FILE, PROGRESS, MAX_TOOL_ROUNDS as MAX_ROUNDS,
                     REPETITION_PENALTY as REP_PENALTY,
                     MIN_TEMPERATURE as MIN_TEMP,
                     DRY_MULTIPLIER, DRY_PENALTY_LAST_N as DRY_LAST_N,
                     PRIVATE_CHAT_ID_PREFIXES, PRIVATE_MODEL_SUFFIX,
                     PRIVATE_DISABLED_TOOLS)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("gateway")
app = FastAPI()



IMG_MD = re.compile(r'!\[([^\]]*)\]\(([^)\s]+)\)')


def repair_image_urls(text, urls):
    """Rewrite markdown image links to the URLs the tools actually returned.

    Models retype image URLs instead of copying them, and corrupt them: observed
    '.../img/gen/x.png' emitted as '...com.img/gen/x.png' with the key re-grouped.
    The gateway knows the real URL, so it should not depend on the model
    reproducing it. Links that already match are left alone.
    """
    if not urls:
        return text
    valid, pending = set(urls), list(urls)

    def sub(m):
        alt, got = m.group(1), m.group(2)
        if got in valid:
            return m.group(0)
        repl = pending.pop(0) if pending else urls[-1]
        log.info("repaired mangled image url: %s -> %s", got, repl)
        return f"![{alt}]({repl})"
    out = IMG_MD.sub(sub, text)
    if not IMG_MD.search(out):          # tool made an image, model linked nothing
        out = (out or "").rstrip() + "\n\n" + "".join(f"![generated image]({u})" for u in urls)
    return out


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


def is_private(body) -> bool:
    """Should this turn leave no trace?

    Open WebUI's own Temporary Chat toggle is the primary trigger: it prefixes
    the chat id with "temporary:" and never saves the conversation. Honouring
    that prefix makes the whole stack agree on what private means, instead of
    asking the user to remember a second switch.
    """
    if body.get("private") is True:
        return True
    cid = str(body.get("chat_id")
              or (body.get("metadata") or {}).get("chat_id") or "")
    if cid and PRIVATE_CHAT_ID_PREFIXES and cid.startswith(PRIVATE_CHAT_ID_PREFIXES):
        return True
    if PRIVATE_MODEL_SUFFIX and str(body.get("model") or "").endswith(PRIVATE_MODEL_SUFFIX):
        return True
    return False


# A model cannot reproduce a 200 KB data URI. Asked to display a private image
# it reconstructs the JPEG header from memory, emits the standard quantisation
# table, then degenerates into a repeating loop -- minutes of streaming garbage.
# So the gateway attaches the image itself and strips anything URI-shaped the
# model produced on its own, which is junk by definition.
_MODEL_DATA_URI = re.compile(
    r"!?\[[^\]]*\]\(\s*data:image/[^)]*\)"
    r"|data:image/[a-zA-Z]+;base64,[A-Za-z0-9+/=\s]{80,}")


def attach_inline(text, uris):
    """Strip any model-invented data URI, then attach the real images."""
    text = _MODEL_DATA_URI.sub("", text or "").rstrip()
    for u in uris:
        text += f"\n\n![generated image]({u})"
    return text


def image_label(tool_name: str) -> str:
    return ("Screenshot from browse_page:" if tool_name == "browse_page"
            else f"Image produced by {tool_name}:")


def inject_images(messages, images):
    """Attach tool-produced images so a vision model can actually see them."""
    for label, b64, mime in images:
        messages.append({"role": "user", "content": [
            {"type": "text", "text": label},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}]})


def as_image(img, tool_name):
    """Normalise a tool's image return into (label, base64, mime)."""
    b64, mime = img if isinstance(img, tuple) else (img, "image/png")
    return image_label(tool_name), b64, mime


def merge_tools(client_tools, private=False):
    """Client tools win on name collision; ours are appended.

    In a private turn the persistence tools are not offered at all. Withholding
    them beats instructing the model not to use them: it cannot call what it
    cannot see.
    """
    names = {t.get("function", {}).get("name") for t in (client_tools or [])}
    ours = [d for d in T.DEFS
            if not (private and d["function"]["name"] in PRIVATE_DISABLED_TOOLS)]
    return list(client_tools or []) + [d for d in ours
                                       if d["function"]["name"] not in names]


async def call_upstream(client, body):
    r = await client.post(f"{UPSTREAM}/v1/chat/completions", json=body,
                          timeout=httpx.Timeout(600.0))
    r.raise_for_status()
    return r.json()


def log_args(args, private: bool) -> str:
    """Tool arguments for the log -- withheld entirely on a private turn.

    Container logs outlive the conversation and sit on the host in plaintext, so
    logging a prompt defeats the point of a mode whose promise is that nothing
    is written down. The tool name is still recorded: knowing that an image was
    generated is operationally useful and reveals nothing about what it was.
    """
    return "<redacted>" if private else json.dumps(args)[:140]


async def execute(tool_calls, subject=None, source=None, private=False):
    """Run our tools. Returns (tool_messages, images_to_inject)."""
    msgs, images = [], []
    for tc in tool_calls:
        fn = tc["function"]
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except Exception:
            args = {}
        log.info("tool: %s(%s) [subject=%s%s]", fn["name"], log_args(args, private),
                 subject, " PRIVATE" if private else "")
        text, img = await T.run(fn["name"], args, subject=subject, source=source,
                                private=private)
        msgs.append({"role": "tool", "tool_call_id": tc["id"],
                     "name": fn["name"], "content": text})
        if img:
            images.append(as_image(img, fn["name"]))
    return msgs, images


def sse(obj):
    return f"data: {json.dumps(obj)}\n\n".encode()


def chunk(cid, model, delta, finish=None):
    return {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
            "model": model, "choices": [{"index": 0, "delta": delta,
                                         "finish_reason": finish}]}


# Anti-loop penalties and verbatim quotation are in direct conflict. DRY
# penalises repeated multi-token SEQUENCES -- but copying "October 06, 2026" out
# of a tool result IS a repeated sequence, so DRY suppresses the correct tokens
# and the model substitutes plausible-looking ones from its training prior.
# Measured on Qwen3-VL-30B asked to restate a date supplied by get_current_datetime:
#   no penalties .................... 3/3 correct
#   repetition_penalty 1.1 .......... 3/4 correct
#   DRY (any dry_penalty_last_n) .... 0/4 correct  -- invented 2023, 2024, 2021
# Shrinking the look-back does not help: the quoted text is only tens of tokens
# back, so any useful window still covers it. The only fix is not to apply these
# once the turn contains material the model is supposed to reproduce exactly.
_ANTI_LOOP_KEYS = ("repetition_penalty", "repeat_penalty",
                   "dry_multiplier", "dry_penalty_last_n")


def has_tool_results(messages) -> bool:
    """True once this turn carries tool output the model must quote faithfully."""
    for m in messages or []:
        if m.get("role") == "tool" or m.get("tool_calls"):
            return True
    return False


def drop_anti_loop(body):
    """Remove anti-loop sampling so tool output can be reproduced verbatim."""
    for k in _ANTI_LOOP_KEYS:
        body.pop(k, None)
    return body


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    subject, source = identify(request)
    private = is_private(body)
    body.pop("private", None)          # our flag, not an upstream parameter
    # A "-private" model entry is how a client with no private-chat concept opts
    # in -- Open WebUI among them: it filters the request through a parameter
    # allowlist that has no chat_id, and pops metadata entirely, so its own
    # Temporary Chat toggle cannot reach a backend. The suffix is ours, not the
    # upstream's, so strip it before forwarding or the model server 404s.
    if (private and PRIVATE_MODEL_SUFFIX
            and str(body.get("model") or "").endswith(PRIVATE_MODEL_SUFFIX)):
        body["model"] = body["model"][:-len(PRIVATE_MODEL_SUFFIX)]
    if private:
        log.info("PRIVATE turn [subject=%s chat_id=%s] -- no storage, no memory tools",
                 subject, body.get("chat_id")
                 or (body.get("metadata") or {}).get("chat_id"))
    if REP_PENALTY > 1.0 and not has_tool_results(body.get("messages")) and not any(
            k in body for k in ("repetition_penalty", "repeat_penalty",
                                "frequency_penalty", "presence_penalty")):
        # Engines disagree on the name and SILENTLY DROP the one they do not know:
        # vLLM wants repetition_penalty, llama.cpp wants repeat_penalty. Sending
        # only one spelling leaves the other engine with NO penalty at all, and
        # repetition loops come straight back after an engine swap.
        body["repetition_penalty"] = REP_PENALTY      # vLLM / TGI
        body["repeat_penalty"] = REP_PENALTY          # llama.cpp
        # DRY penalises repeated multi-token SEQUENCES, which is the real failure
        # mode (a line pattern cycling), not single repeated tokens.
        if DRY_MULTIPLIER > 0:
            body.setdefault("dry_multiplier", DRY_MULTIPLIER)
            body.setdefault("dry_penalty_last_n", DRY_LAST_N)
    if MIN_TEMP > 0 and float(body.get("temperature") or 0) < MIN_TEMP:
        body["temperature"] = MIN_TEMP
    stream = bool(body.pop("stream", False))
    want_usage = bool((body.pop("stream_options", None) or {}).get("include_usage"))
    client_tools = body.get("tools")
    body["tools"] = merge_tools(client_tools, private=private)
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
        # From here the turn carries tool output the model must quote exactly,
        # so the anti-loop penalties have to come off (see _ANTI_LOOP_KEYS).
        drop_anti_loop(body)
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
            log.info("tool: %s(%s) [subject=%s%s]", name, log_args(args, private),
                     subject, " PRIVATE" if private else "")
            t0 = time.time()
            # Run the tool as a task so we can emit heartbeats while it works.
            # generate_image takes ~50s; without this the client goes silent and
            # the user cannot tell "working" from "hung".
            task = asyncio.create_task(T.run(name, args, subject=subject,
                                             source=source, private=private))
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
            if name in ("generate_image", "edit_image"):
                try:
                    u = (json.loads(text) or {}).get("url")
                    if u:
                        turn_images.append(u)
                except Exception:
                    pass
            if img:
                images.append(as_image(img, name))
        inject_images(messages, images)
        if private:
            inline_images.extend(f"data:{m};base64,{b}" for (l, b, m) in images
                                 if l.startswith("Image produced by"))

    turn_images = []     # image URLs produced this turn, for repair_image_urls
    inline_images = []   # private images the gateway attaches itself

    async def apply(msg, tcs):
        """Append the assistant turn, run the tools, inject any screenshots."""
        messages.append({"role": "assistant", "content": msg.get("content"),
                         "tool_calls": tcs})
        # From here the turn carries tool output the model must quote exactly,
        # so the anti-loop penalties have to come off (see _ANTI_LOOP_KEYS).
        drop_anti_loop(body)
        tool_msgs, images = await execute(tcs, subject=subject, source=source,
                                          private=private)
        for tm in tool_msgs:
            if tm.get("name") in ("generate_image", "edit_image"):
                try:
                    u = (json.loads(tm.get("content") or "{}") or {}).get("url")
                    if u:
                        turn_images.append(u)
                except Exception:
                    pass
        messages.extend(tool_msgs)
        inject_images(messages, images)
        if private:
            inline_images.extend(f"data:{m};base64,{b}" for (l, b, m) in images
                                 if l.startswith("Image produced by"))

    # ---------------------------------------------------------- non-streaming
    if not stream:
        async with httpx.AsyncClient() as client:
            for _ in range(MAX_ROUNDS):
                body["messages"] = messages
                data = await call_upstream(client, body)
                msg = data["choices"][0]["message"]
                tcs = msg.get("tool_calls") or []
                if not mine_only(tcs):
                    if not tcs:
                        m0 = data["choices"][0]["message"]
                        if turn_images:
                            m0["content"] = repair_image_urls(m0.get("content") or "", turn_images)
                        if inline_images:
                            m0["content"] = attach_inline(m0.get("content"), inline_images)
                    return JSONResponse(data)
                await apply(msg, tcs)
            body["messages"] = messages
            body["tool_choice"] = "none"
            data = await call_upstream(client, body)
            m0 = data["choices"][0]["message"]
            if turn_images:
                m0["content"] = repair_image_urls(m0.get("content") or "", turn_images)
            if inline_images:
                m0["content"] = attach_inline(m0.get("content"), inline_images)
            return JSONResponse(data)

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
                                # hold everything if an image URL may need repairing
                                if buf_len >= FLUSH_AT and not turn_images:
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
                if not tcs and (buf or inline_images):
                    whole = "".join(buf)
                    if turn_images:
                        whole = repair_image_urls(whole, turn_images)
                    if inline_images:
                        whole = attach_inline(whole, inline_images)
                    yield sse(chunk(cid, model, {"content": whole}))
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
    from .backends import s3
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
    from . import memory as M
    b = await request.json()
    text = b.get("text", "")
    size = int(b.get("chunk_size", 1200))
    chunks = [text[i:i + size] for i in range(0, len(text), size)] or [""]
    return await M.index_document(b.get("uri"), b.get("title"), chunks,
                                  subject=b.get("subject"))


@app.get("/v1/models")
async def models(request: Request):
    """Advertise a "-private" twin of every upstream model.

    Private mode needs to be selectable from clients that cannot pass a flag of
    their own. Open WebUI is the case in point: it filters its model list down
    to what the backend actually advertises, so an entry that is not listed here
    never appears in the picker no matter what is configured locally. Listing
    the twin here makes private mode available everywhere at once -- the picker,
    opencode, a curl one-liner -- with no per-client setup.

    The suffix is stripped again before the request reaches the model server.
    """
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as c:
        r = await c.get(f"{UPSTREAM}/v1/models",
                        headers={k: v for k, v in request.headers.items()
                                 if k.lower() == "authorization"})
    try:
        data = r.json()
    except Exception:
        return Response(content=r.content, status_code=r.status_code,
                        media_type=r.headers.get("content-type"))
    if PRIVATE_MODEL_SUFFIX and isinstance(data.get("data"), list):
        twins = [{**m, "id": str(m["id"]) + PRIVATE_MODEL_SUFFIX}
                 for m in data["data"]
                 if m.get("id") and not str(m["id"]).endswith(PRIVATE_MODEL_SUFFIX)]
        data["data"] = data["data"] + twins
    return JSONResponse(data, status_code=r.status_code)


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
