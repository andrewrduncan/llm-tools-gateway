"""Server-side tools exposed to every client of the gateway."""
import os, json, base64, datetime, asyncio, secrets
from zoneinfo import ZoneInfo
import httpx
from . import memory as M
from .backends import s3

from .config import (SEARXNG_URL as SEARXNG, COMFY_URL as COMFY,
                     PUBLIC_URL, DEFAULT_TZ, WORKFLOW_DIR,
                     WORKFLOW_GENERATE, WORKFLOW_EDIT, capabilities,
                     PRIVATE_IMAGE_FORMAT, PRIVATE_IMAGE_QUALITY,
                     COMFY_INPUT_DIR, COMFY_INPUT_TTL,
                     VIDEO_ENABLED, VIDEO_VARIANT, VIDEO_CLIP, VIDEO_VAE,
                     VIDEO_UNET_HIGH, VIDEO_UNET_LOW, VIDEO_LORA_HIGH,
                     VIDEO_LORA_LOW, VIDEO_LORA_STRENGTH, VIDEO_SPLIT_PROMPTS,
                     VIDEO_MAX_WIDTH, VIDEO_MAX_HEIGHT, VIDEO_FPS,
                     VIDEO_MAX_SECONDS, VIDEO_DEFAULT_SECONDS, VIDEO_STEPS,
                     VIDEO_BOUNDARY, VIDEO_CFG, VIDEO_SHIFT, VIDEO_SAMPLER,
                     VIDEO_SCHEDULER, VIDEO_NEGATIVE, VIDEO_TIMEOUT,
                     VIDEO_I2V_UNET_HIGH, VIDEO_I2V_UNET_LOW,
                     VIDEO_I2V_LORA_HIGH, VIDEO_I2V_LORA_LOW, VIDEO_I2V_ENABLED,
                     PRIVATE_VIDEO_TTL)
# URL the CLIENT's browser will use -- must be the LAN address, not the
# compose service name, or images render as broken links.
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"


# Image keys must be BOTH unguessable and easy for a model to copy verbatim.
# uuid4 satisfied the first and failed the second: models retype the URL instead
# of copying it and re-group the hyphens (observed: 37980a9e-764e-4990... emitted
# as 3798-0a9e-764-e499-...), producing a dead link. Lowercase alphanumerics only.
_KEY_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"   # no l/o/0/1 lookalikes


def _img_key(prefix: str) -> str:
    return f"{prefix}/{''.join(secrets.choice(_KEY_ALPHABET) for _ in range(16))}.png"


# ---------------------------------------------------------------- definitions
DEFS = [
    {"type": "function", "function": {
        "name": "get_current_datetime",
        "description": "Get the current date and time. Use whenever the user asks about "
                       "the current date, time, day of week, or anything time-relative.",
        "parameters": {"type": "object", "properties": {
            "timezone": {"type": "string",
                         "description": f"IANA timezone, e.g. America/New_York. Default {DEFAULT_TZ}."}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "web_search",
        "description": "Search the web. Use for current events, recent information, or any "
                       "fact you are not certain about. Returns titles, URLs and snippets.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "description": "Default 6, max 15."}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "fetch_url",
        "description": "Fetch a URL and return its text content. Fast, but does NOT run "
                       "JavaScript. For JS-heavy or blocked sites use browse_page instead.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string"},
            "max_chars": {"type": "integer", "description": "Default 6000."}},
            "required": ["url"]}}},
    {"type": "function", "function": {
        "name": "browse_page",
        "description": "Load a page in a real Chromium browser with JavaScript enabled. "
                       "Use for sites that block simple fetches, require rendering, or are "
                       "single-page apps. Can optionally return a screenshot the model can see.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string"},
            "screenshot": {"type": "boolean",
                           "description": "If true, capture the page and show it to the model."},
            "full_page": {"type": "boolean", "description": "Full-page screenshot. Default false."},
            "wait_for": {"type": "string", "description": "Optional CSS selector to wait for."},
            "max_chars": {"type": "integer", "description": "Default 6000."}},
            "required": ["url"]}}},
    {"type": "function", "function": {
        "name": "generate_image",
        "description": "Generate an image from a text description using a local "
                       "Qwen-Image diffusion model. Use when the user asks you to draw, "
                       "create, generate, or make a picture/image/illustration. Takes about "
                       "40 seconds. IMPORTANT: after calling this, include the returned "
                       "markdown in your reply verbatim so the image is displayed.",
        "parameters": {"type": "object", "properties": {
            "prompt": {"type": "string",
                       "description": "Detailed description of the image. Be specific about "
                                      "subject, style, lighting and composition. This model is "
                                      "excellent at rendering text inside images -- put any "
                                      "wanted text in quotes."},
            "width":  {"type": "integer", "description": "Default 1024. Use 1328x768 for landscape."},
            "height": {"type": "integer", "description": "Default 1024."},
            "seed":   {"type": "integer", "description": "Optional, for reproducibility."}},
            "required": ["prompt"]}}},
    {"type": "function", "function": {
        "name": "remember",
        "description": "Save a durable fact, preference, or piece of context for later. "
                       "Use when the user says to remember something, or states a lasting "
                       "preference/fact about themselves or their work. Do NOT use for "
                       "trivia answerable by web_search.",
        "parameters": {"type": "object", "properties": {
            "content": {"type": "string", "description": "The fact, written as a clear standalone sentence."},
            "shared":  {"type": "boolean",
                        "description": "true = visible to everyone; false/omitted = private to this user."},
            "tags":    {"type": "array", "items": {"type": "string"}}},
            "required": ["content"]}}},
    {"type": "function", "function": {
        "name": "recall",
        "description": "Search saved memories about this user and their environment. "
                       "ALWAYS call this FIRST, before answering, whenever the user asks "
                       "about anything specific to them: their preferences, their setup, "
                       "their credentials, their projects, their home or office, names, "
                       "passwords, or ANY fact you could not possibly know from training "
                       "data. Also call it when they refer to something discussed before. "
                       "It is fast and cheap - when in doubt, CALL IT. Only say you do not "
                       "know AFTER this returns no results.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "description": "Default 6."}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "forget",
        "description": "Delete a saved memory, by id or by describing it. Use only when the "
                       "user explicitly asks to forget or correct something.",
        "parameters": {"type": "object", "properties": {
            "memory_id": {"type": "integer"},
            "query":     {"type": "string", "description": "Describe the memory to remove."}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "search_documents",
        "description": "Semantic search over indexed documents (RAG). Use for questions about "
                       "the user's own documents, notes, or previously ingested material.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "description": "Default 5."}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "search_images",
        "description": "Find previously generated or stored images by describing them in words. "
                       "Returns image URLs plus ready-to-use markdown. Include the markdown "
                       "verbatim in your reply so the images display.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Describe the image you are looking for."},
            "limit": {"type": "integer", "description": "Default 6."}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "delete_image",
        "description": "Permanently delete a generated image. Removes every copy: the stored "
                       "object (so its public URL stops resolving), the search index entry, and "
                       "the generator's output file. Use search_images first if the user refers "
                       "to an image without giving a URL. This cannot be undone.",
        "parameters": {"type": "object", "properties": {
            "image": {"type": "string",
                      "description": "The image URL, object key, or filename to delete."}},
            "required": ["image"]}}},
    {"type": "function", "function": {
        "name": "edit_image",
        "description": "Edit an EXISTING image using a natural-language instruction "
                       "(change colours, replace objects, alter style/lighting, remove or "
                       "add things, change backgrounds). If the user means an image "
                       "already in this conversation - one they attached, or one you "
                       "just generated - OMIT image_url entirely and it is resolved "
                       "automatically. NEVER invent a URL. "
                       "For creating a brand new image from nothing, use generate_image. "
                       "Takes about a minute. Include the returned markdown verbatim.",
        "parameters": {"type": "object", "properties": {
            "image_url":   {"type": "string",
                            "description": "Optional. Only for an image NOT in this "
                                           "conversation. Omit it to edit the image "
                                           "already here."},
            "instruction": {"type": "string",
                            "description": "What to change, e.g. 'make the sail blue and add seagulls'."},
            "steps":       {"type": "integer", "description": "Default 4 (Lightning)."},
            "seed":        {"type": "integer"}},
            "required": ["instruction"]}}},
]

if capabilities()["video"]:
    # Appended rather than written inline because DEFS is otherwise static: the
    # whole list sits in an identical prefix on every request so prefix caching
    # makes it nearly free. A deployment without video simply never grows it.
    DEFS.append(
        {"type": "function", "function": {
            "name": "generate_video",
            "description": f"Generate a SHORT video clip (max {int(VIDEO_MAX_SECONDS)} seconds) from a text "
                           "description, optionally animating an existing image. Use when "
                           "the user asks for a video, clip, animation, or asks to make a "
                           "picture move. To animate an image already in this conversation "
                           "- one they attached or you just generated - OMIT image_url and "
                           "it is resolved automatically. NEVER invent a URL. "
                           f"This is VERY SLOW and cost grows far faster than length: about "
                           f"a minute for a 2 second clip, but roughly twenty minutes for a "
                           f"{int(VIDEO_MAX_SECONDS)} second one. It holds the GPU for the whole render, so "
                           f"image generation and chat stall behind it. ALWAYS prefer the "
                           f"shortest clip that satisfies the request, and leave `seconds` "
                           f"unset unless the user asked for a specific length. Do NOT call "
                           f"it speculatively; only when a video was actually asked for. It "
                           f"cannot produce clips longer than {int(VIDEO_MAX_SECONDS)} seconds; if more is "
                           f"wanted, say so rather than calling this repeatedly. "
                           f"Include the returned markdown verbatim.",
            "parameters": {"type": "object", "properties": {
                "prompt":    {"type": "string",
                              "description": "What happens in the clip. Describe MOTION, not "
                                             "just the scene - what moves, in which direction, "
                                             "and how the camera behaves."},
                "image_url": {"type": "string",
                              "description": "Optional. Only for an image NOT in this "
                                             "conversation. Omit it to animate the image "
                                             "already here."},
                "seconds":   {"type": "number",
                              "description": f"Clip length in seconds, 1-{int(VIDEO_MAX_SECONDS)}. "
                                             f"Default {int(VIDEO_DEFAULT_SECONDS)}. Each extra second "
                                             f"costs disproportionately more; do not raise this "
                                             f"without being asked."},
                "width":     {"type": "integer",
                              "description": f"Default {VIDEO_MAX_WIDTH}. Multiple of 16. Larger is not allowed."},
                "height":    {"type": "integer",
                              "description": f"Default {VIDEO_MAX_HEIGHT}. Multiple of 16. Larger is not allowed."},
                "seed":      {"type": "integer", "description": "Optional, for reproducibility."}},
                "required": ["prompt"]}}})

NAMES = {d["function"]["name"] for d in DEFS}

# Tools whose caller identity must be injected server-side. The model must NEVER
# be able to set `subject` -- otherwise it could read another user's memories.
SUBJECT_TOOLS = {"remember", "recall", "forget", "search_documents",
                 "search_images", "generate_image", "edit_image", "delete_image",
                 "generate_video"}

# Tools whose behaviour changes in a private turn: they must not persist.
PRIVATE_AWARE_TOOLS = {"generate_image", "edit_image", "generate_video"}

# Tools that may act on an image the model cannot address. Like `subject`, the
# resolved image comes from the request, never from the model.
CONVERSATION_IMAGE_TOOLS = {"edit_image", "generate_video"}

# ---------------------------------------------------------------- helpers
def _drop_edit_source(fname: str):
    """Delete an edit's uploaded source once the job no longer needs it.

    The source is the user's own upload. It is never indexed and never stored,
    so leaving it in ComfyUI's input directory is pure residue -- and in a
    private turn it is residue of something that was supposed to leave no trace.
    """
    if not (COMFY_INPUT_DIR and fname):
        return
    # fname is a LoadImage reference like "edit-src-123.png [temp]"
    base = os.path.basename(fname.split(" [")[0])
    try:
        os.remove(os.path.join(COMFY_INPUT_DIR, base))
    except OSError:
        pass


def _sweep_edit_sources(older_than: int = None):
    """Remove sources orphaned by a crash between upload and cleanup."""
    if not COMFY_INPUT_DIR:
        return
    import time as _t
    cutoff = _t.time() - (older_than if older_than is not None else COMFY_INPUT_TTL)
    try:
        for n in os.listdir(COMFY_INPUT_DIR):
            if not n.startswith("edit-src-"):
                continue
            p = os.path.join(COMFY_INPUT_DIR, n)
            try:
                if os.path.getmtime(p) < cutoff:
                    os.remove(p)
            except OSError:
                pass
    except OSError:
        pass


def _to_websocket_output(wf: dict) -> dict:
    """Swap a workflow's disk-writing output node for the websocket one.

    SaveImage writes the PNG into ComfyUI's output directory before the gateway
    ever sees it. Deleting it afterwards is a promise that fails on a crash, a
    timeout, or a permissions change; SaveImageWebsocket means the bytes never
    reach a filesystem at all. Derived from the live workflow rather than kept
    as a second JSON file, so the two cannot drift apart.
    """
    out = {}
    for nid, node in wf.items():
        if node.get("class_type") in ("SaveImage", "SaveImageAdvanced"):
            out[nid] = {"class_type": "SaveImageWebsocket",
                        "inputs": {"images": node["inputs"]["images"]}}
        else:
            out[nid] = node
    return out


async def _comfy_generate_ws(wf: dict, timeout: float = 300.0):
    """Run a workflow and collect its image over the websocket.

    Returns (png_bytes, error). ComfyUI frames images exactly like previews: an
    8-byte header (binary type, then image format) followed by the PNG.
    """
    import uuid as _uuid
    import websockets
    cid = str(_uuid.uuid4())
    ws_url = (COMFY.replace("https://", "wss://").replace("http://", "ws://")
              + f"/ws?clientId={cid}")
    try:
        async with websockets.connect(ws_url, max_size=None, open_timeout=30) as ws:
            # Queue only AFTER the socket is open, or the early frames are lost.
            async with httpx.AsyncClient(timeout=60) as c:
                r = await c.post(f"{COMFY}/prompt",
                                 json={"prompt": wf, "client_id": cid})
                if r.status_code != 200:
                    return None, f"ComfyUI rejected the job: {r.text[:300]}"
                pid = r.json()["prompt_id"]
            raw = None
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout
            while loop.time() < deadline:
                msg = await asyncio.wait_for(ws.recv(),
                                             timeout=max(1.0, deadline - loop.time()))
                if isinstance(msg, (bytes, bytearray)):
                    if len(msg) > 8:
                        raw = bytes(msg[8:])        # strip the 8-byte header
                    continue
                try:
                    d = json.loads(msg)
                except Exception:
                    continue
                if d.get("type") == "execution_error":
                    return None, str(d.get("data"))[:300]
                if d.get("type") == "executing":
                    dd = d.get("data") or {}
                    if dd.get("node") is None and dd.get("prompt_id") == pid:
                        break                        # this prompt is finished
            return (raw, None) if raw else (None, "no image received over websocket")
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def _inline_image(raw: bytes):
    """Re-encode for inclusion in the transcript and return (data_uri_b64, mime).

    A stored image is fetched once by the browser; an inline one lives in the
    context window and is re-sent on every subsequent turn, so the size of this
    payload is a running cost rather than a one-off.
    """
    fmt = PRIVATE_IMAGE_FORMAT or "WEBP"
    try:
        from PIL import Image
        import io as _io
        img = Image.open(_io.BytesIO(raw))
        if fmt in ("JPEG", "JPG") and img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        buf = _io.BytesIO()
        img.save(buf, format=fmt, quality=PRIVATE_IMAGE_QUALITY)
        out = buf.getvalue()
        # Only keep the re-encode if it actually helped.
        if len(out) < len(raw):
            return base64.b64encode(out).decode(), f"image/{fmt.lower()}"
    except Exception:
        pass   # Pillow missing or format unsupported -- fall back to the original
    return base64.b64encode(raw).decode(), "image/png"



def _clean_html(html: str, max_chars: int) -> str:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style", "noscript", "svg", "nav", "footer", "header", "form"]):
        t.decompose()
    text = "\n".join(l.strip() for l in soup.get_text("\n").splitlines() if l.strip())
    return text[:max_chars]

# ---------------------------------------------------------------- tools
async def get_current_datetime(timezone: str = None) -> str:
    tz = timezone or DEFAULT_TZ
    try: z = ZoneInfo(tz)
    except Exception: z, tz = ZoneInfo(DEFAULT_TZ), DEFAULT_TZ
    now = datetime.datetime.now(z)
    return json.dumps({
        "iso": now.isoformat(), "timezone": tz,
        "human": now.strftime("%A, %B %d, %Y at %I:%M %p %Z"),
        "utc_iso": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    })

async def web_search(query: str, max_results: int = 6) -> str:
    n = max(1, min(int(max_results or 6), 15))
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
        r = await c.get(f"{SEARXNG}/search",
                        params={"q": query, "format": "json", "safesearch": 0},
                        headers={"User-Agent": UA})
        r.raise_for_status()
        data = r.json()
    out = [{"title": x.get("title"), "url": x.get("url"),
            "snippet": (x.get("content") or "")[:400]}
           for x in (data.get("results") or [])[:n]]
    if not out:
        return json.dumps({"results": [], "note": "No results."})
    return json.dumps({"query": query, "results": out}, ensure_ascii=False)

async def fetch_url(url: str, max_chars: int = 6000) -> str:
    async with httpx.AsyncClient(timeout=30, follow_redirects=True,
                                 headers={"User-Agent": UA}) as c:
        r = await c.get(url)
        ct = r.headers.get("content-type", "")
        if "html" in ct:
            body = _clean_html(r.text, int(max_chars or 6000))
        else:
            body = r.text[:int(max_chars or 6000)]
    return json.dumps({"url": str(r.url), "status": r.status_code,
                       "content": body}, ensure_ascii=False)

async def browse_page(url: str, screenshot: bool = False, full_page: bool = False,
                      wait_for: str = None, max_chars: int = 6000):
    """Returns (text_result, optional_base64_png)."""
    from playwright.async_api import async_playwright
    shot_b64 = None
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            ctx = await browser.new_context(user_agent=UA, viewport={"width": 1280, "height": 900})
            page = await ctx.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            try: await page.wait_for_load_state("networkidle", timeout=8000)
            except Exception: pass
            if wait_for:
                try: await page.wait_for_selector(wait_for, timeout=10000)
                except Exception: pass
            title = await page.title()
            html = await page.content()
            if screenshot:
                shot_b64 = base64.b64encode(
                    await page.screenshot(full_page=bool(full_page), type="png")).decode()
            final = page.url
        finally:
            await browser.close()
    return json.dumps({"url": final, "title": title,
                       "content": _clean_html(html, int(max_chars or 6000)),
                       "screenshot_attached": bool(shot_b64)}, ensure_ascii=False), shot_b64

async def generate_image(prompt: str, width: int = 1024, height: int = 1024,
                         seed: int = None, subject=None, private: bool = False) -> str:
    """Drive ComfyUI's API. Mirrors the verified Qwen-Image + Lightning workflow."""
    w = max(256, min(int(width or 1024), 1536))
    h = max(256, min(int(height or 1024), 1536))
    sd = int(seed) if seed is not None else int.from_bytes(os.urandom(4), "big")
    wf = {
      "1":{"class_type":"UNETLoader","inputs":{
            "unet_name":"qwen_image_2512_fp8_e4m3fn.safetensors","weight_dtype":"fp8_e4m3fn"}},
      "2":{"class_type":"CLIPLoader","inputs":{
            "clip_name":"qwen_2.5_vl_7b_fp8_scaled.safetensors","type":"qwen_image"}},
      "3":{"class_type":"VAELoader","inputs":{"vae_name":"qwen_image_vae.safetensors"}},
      "4":{"class_type":"LoraLoaderModelOnly","inputs":{
            "lora_name":"qwen-image-2512-lightning-8steps.safetensors",
            "strength_model":1.0,"model":["1",0]}},
      "5":{"class_type":"CLIPTextEncode","inputs":{"text":prompt,"clip":["2",0]}},
      "6":{"class_type":"CLIPTextEncode","inputs":{"text":"","clip":["2",0]}},
      "7":{"class_type":"EmptySD3LatentImage","inputs":{"width":w,"height":h,"batch_size":1}},
      "8":{"class_type":"KSampler","inputs":{"seed":sd,"steps":8,"cfg":1.0,
            "sampler_name":"euler","scheduler":"simple","denoise":1.0,
            "model":["4",0],"positive":["5",0],"negative":["6",0],"latent_image":["7",0]}},
      "9":{"class_type":"VAEDecode","inputs":{"samples":["8",0],"vae":["3",0]}},
      "10":{"class_type":"SaveImage","inputs":{"filename_prefix":"gen","images":["9",0]}},
    }
    if private:
        # Private turns never let the image reach a filesystem: the output node
        # is swapped for SaveImageWebsocket and the bytes arrive over the socket.
        raw, err = await _comfy_generate_ws(_to_websocket_output(wf))
        if err:
            return json.dumps({"error": "generation failed", "detail": err})
        b64, mime = _inline_image(raw)
        return json.dumps({
            "status": "ok", "seed": sd, "size": f"{w}x{h}", "private": True,
            "note": "The image has ALREADY been attached to your reply "
                    "automatically. Do NOT output a URL, a markdown image, or "
                    "base64 data of any kind -- just describe the image in "
                    "words. It was never written to disk and has no URL."}), (b64, mime)

    # Normal turns take the same websocket path as private ones. ComfyUI's
    # SaveImage writes the PNG to disk AND embeds the full workflow -- prompt
    # text included -- in a tEXt chunk, so every stored image carried a readable
    # copy of what was asked for. SaveImageWebsocket writes nothing and attaches
    # no metadata; the gateway holds the bytes and decides where they go.
    raw, err = await _comfy_generate_ws(_to_websocket_output(wf))
    if err:
        return json.dumps({"error": "generation failed", "detail": err})
    try:
        key = _img_key("gen")
        url = await s3.put(key, raw, "image/png")
        await M.index_file(s3.BUCKET, key, url, "image/png", len(raw),
                           prompt=prompt, subject=subject, source_file=None,
                           image_b64=base64.b64encode(raw).decode())
    except Exception as e:
        # There is no on-disk copy to fall back to any more, which is the point:
        # a failure here means the image is gone, not quietly left somewhere.
        return json.dumps({"error": "could not store the image",
                           "detail": f"{type(e).__name__}: {e}"})
    return json.dumps({
        "status": "ok", "url": url, "seed": sd, "size": f"{w}x{h}",
        "markdown": f"![{prompt[:80]}]({url})",
        "note": "Include the markdown field verbatim in your reply to show the image."})


async def remember(content, shared=False, tags=None, subject=None, source=None):
    return await M.remember(content, subject=subject, source=source,
                            tags=tags, shared=bool(shared))

async def recall(query, limit=6, subject=None):
    return json.dumps(await M.recall(query, subject=subject, limit=limit), default=str)

async def forget(memory_id=None, query=None, subject=None):
    return json.dumps(await M.forget(memory_id=memory_id, query=query, subject=subject),
                      default=str)

async def search_documents(query, limit=5, subject=None):
    return json.dumps(await M.search_documents(query, subject=subject, limit=limit),
                      default=str)

async def search_images(query, limit=6, subject=None):
    r = await M.search_images(query, subject=subject, limit=limit)
    r["note"] = "Include each markdown field verbatim so the images display."
    return json.dumps(r, default=str)


async def _comfy_upload(client, raw: bytes, name: str) -> str:
    """Push bytes into ComfyUI's TEMP dir and return a LoadImage reference.

    temp rather than input, because an edit source is working data that should
    not outlive the job. ComfyUI owns its temp directory and clears it on
    startup, so a source survives a gateway crash by at most one restart -- and
    with the temp dir mounted as tmpfs it was never on disk to begin with.
    LoadImage addresses non-input files as "<name> [temp]".
    """
    r = await client.post(f"{COMFY}/upload/image",
                          files={"image": (name, raw, "image/png")},
                          data={"overwrite": "true", "type": "temp"})
    r.raise_for_status()
    j = r.json()
    nm = j.get("name", name)
    sub = j.get("subfolder") or ""
    ref = f"{sub}/{nm}" if sub else nm
    return f"{ref} [temp]" if (j.get("type") or "temp") == "temp" else ref


async def edit_image(instruction: str, image_url: str = None, steps: int = 4,
                     seed: int = None, subject=None, private: bool = False,
                     conversation_image: str = None) -> str:
    """Mirrors ComfyUI's official image_qwen_image_edit_2509 template. Every node
    here matters -- an earlier hand-rolled version omitted ModelSamplingAuraFlow
    and CFGNorm and produced a faithful COPY of the input with the instruction
    silently ignored."""
    sd = int(seed) if seed is not None else int.from_bytes(os.urandom(4), "big")
    st = max(2, min(int(steps or 4), 20))
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as c:
        # Prefer the conversation's own image. A model asked to edit "this"
        # has no way to know its address, so a URL it supplies is invented
        # unless it is one the gateway handed it earlier.
        ref = image_url
        if conversation_image and not (image_url or "").startswith((PUBLIC_URL, "data:")):
            ref = conversation_image
        if not ref:
            return json.dumps({"error": "no image to edit",
                               "note": "Attach an image, or generate one first."})
        try:
            if ref.startswith("data:"):
                src = base64.b64decode(ref.split(",", 1)[1])
            else:
                src = (await c.get(ref)).content
            if len(src) < 500:
                return json.dumps({"error": f"source image invalid ({len(src)} bytes)"})
        except Exception as e:
            return json.dumps({"error": f"could not read the source image: "
                                        f"{type(e).__name__}: {e}"})
        _sweep_edit_sources()      # clear anything a previous crash orphaned
        fname = await _comfy_upload(c, src, f"edit-src-{sd}.png")

        wf = {
          "1":{"class_type":"UNETLoader","inputs":{
                "unet_name":"qwen_image_edit_2509_fp8_e4m3fn.safetensors",
                "weight_dtype":"fp8_e4m3fn"}},
          # 4-step Lightning: without it this runs ~428s instead of ~40s
          "2":{"class_type":"LoraLoaderModelOnly","inputs":{
                "lora_name":"Qwen-Image-Edit-2509-Lightning-4steps-V1.0-bf16.safetensors",
                "strength_model":1.0,"model":["1",0]}},
          # flow-matching shift -- omitting this is why the first attempt no-op'd
          "3":{"class_type":"ModelSamplingAuraFlow","inputs":{"model":["2",0],"shift":3.0}},
          "4":{"class_type":"CFGNorm","inputs":{"model":["3",0],"strength":1.0}},
          "5":{"class_type":"CLIPLoader","inputs":{
                "clip_name":"qwen_2.5_vl_7b_fp8_scaled.safetensors","type":"qwen_image"}},
          "6":{"class_type":"VAELoader","inputs":{"vae_name":"qwen_image_vae.safetensors"}},
          "7":{"class_type":"LoadImage","inputs":{"image":fname}},
          # snaps the input to a resolution the model expects
          "8":{"class_type":"FluxKontextImageScale","inputs":{"image":["7",0]}},
          "9":{"class_type":"TextEncodeQwenImageEditPlus","inputs":{
                "clip":["5",0],"prompt":instruction,"vae":["6",0],"image1":["8",0]}},
          "10":{"class_type":"TextEncodeQwenImageEditPlus","inputs":{
                "clip":["5",0],"prompt":"","vae":["6",0],"image1":["8",0]}},
          "11":{"class_type":"VAEEncode","inputs":{"pixels":["8",0],"vae":["6",0]}},
          "12":{"class_type":"KSampler","inputs":{
                "seed":sd,"steps":st,"cfg":1.0,"sampler_name":"euler",
                "scheduler":"simple","denoise":1.0,"model":["4",0],
                "positive":["9",0],"negative":["10",0],"latent_image":["11",0]}},
          "13":{"class_type":"VAEDecode","inputs":{"samples":["12",0],"vae":["6",0]}},
          "14":{"class_type":"SaveImage","inputs":{"filename_prefix":"edit","images":["13",0]}},
        }
        if private:
            # Same guarantee as generate_image: the result never reaches a
            # filesystem. The uploaded source had to, because LoadImage has no
            # base64 variant -- it goes to ComfyUI's temp dir (tmpfs) and is
            # removed the moment the job is done.
            raw, err = await _comfy_generate_ws(_to_websocket_output(wf))
            _drop_edit_source(fname)
            if err:
                return json.dumps({"error": "edit failed", "detail": err})
            b64, mime = _inline_image(raw)
            return json.dumps({
                "status": "ok", "seed": sd, "steps": st, "private": True,
                "note": "The edited image has ALREADY been attached to your reply "
                        "automatically. Do NOT output a URL, a markdown image, or "
                        "base64 data -- just describe the result in words."}), (b64, mime)

        # Websocket for both flows -- see generate_image. No output file, and no
        # workflow metadata baked into the bytes we are about to store.
        raw, err = await _comfy_generate_ws(_to_websocket_output(wf))
        _drop_edit_source(fname)
        if err:
            return json.dumps({"error": "edit failed", "detail": err})
        try:
            key = _img_key("edit")
            url = await s3.put(key, raw, "image/png")
            await M.index_file(s3.BUCKET, key, url, "image/png", len(raw),
                               prompt=f"[edit] {instruction}", subject=subject,
                               source_file=None,
                               image_b64=base64.b64encode(raw).decode())
        except Exception as e:
            return json.dumps({"error": "could not store the edited image",
                               "detail": f"{type(e).__name__}: {e}"})
        return json.dumps({
            "status": "ok", "url": url, "seed": sd, "steps": st,
            "markdown": f"![{instruction[:70]}]({url})",
            "note": "Include the markdown field verbatim to show the edited image."})



async def delete_image(image: str, subject=None):
    """Delete an image everywhere it exists.

    Two places since generation moved to the websocket: the object and the index
    row. There is no generator-side copy any more -- ComfyUI never writes one.

    Partial failures are reported rather than swallowed: an object that is gone
    but still indexed leaves a dangling search result pointing at a dead URL.
    """
    row = await M.find_file(image, subject, for_write=True)
    if not row:
        return json.dumps({"status": "not_found",
                           "note": f"No image you own matches {image!r}. Shared images are "
                                   f"viewable by anyone but deletable only by their creator."})
    removed, failed = [], []
    try:
        await s3.delete(row["object_key"]); removed.append("object storage")
    except Exception as e:
        failed.append(f"object storage: {type(e).__name__}: {e}")
    try:
        await M.forget_file(row["id"]); removed.append("search index")
    except Exception as e:
        failed.append(f"search index: {type(e).__name__}: {e}")
    return json.dumps({"status": "ok" if not failed else "partial",
                       "deleted_from": removed, "errors": failed,
                       "prompt": row["prompt"],
                       "note": "The image is gone and its URL no longer resolves."})


# ------------------------------------------------------------------ video
# A private video cannot be inlined the way a private image is: base64 of an
# MP4 is megabytes, and an inlined image is re-sent on EVERY later turn. So the
# bytes stay in gateway memory and are served from a URL that expires.
#
# Not single-fetch, deliberately. The obvious "delete on first retrieval" rule
# breaks playback: a browser issues a HEAD or a ranged GET before it plays, and
# seeking issues more, so the first request is rarely the only one. Expiry is
# the guarantee instead, and it still never touches a disk.
_PRIVATE_VIDEOS: dict = {}


def _private_video_put(raw: bytes, mime: str = "video/mp4") -> str:
    import time as _t
    now = _t.time()
    for k, v in list(_PRIVATE_VIDEOS.items()):          # expire on write
        if v[2] < now:
            _PRIVATE_VIDEOS.pop(k, None)
    tok = "".join(secrets.choice(_KEY_ALPHABET) for _ in range(20))
    _PRIVATE_VIDEOS[tok] = (raw, mime, now + PRIVATE_VIDEO_TTL)
    return tok


def private_video_get(tok: str):
    import time as _t
    v = _PRIVATE_VIDEOS.get(tok)
    if not v:
        return None
    if v[2] < _t.time():
        _PRIVATE_VIDEOS.pop(tok, None)
        return None
    return v[0], v[1]


def _video_frames(seconds: float) -> int:
    """Frame count for a duration, snapped to the 4n+1 the latent requires.

    A14B's own template derives length as floor(seconds * fps) + 1, which at
    16fps already lands on 4n+1 for whole seconds. Fractional requests are
    rounded to the nearest valid length rather than floored, so 5s is 81
    frames and not 77.
    """
    n = int(float(seconds) * VIDEO_FPS) + 1
    return max(5, int(round((n - 1) / 4)) * 4 + 1)


async def _comfy_stage(c: httpx.AsyncClient, wf: dict, timeout: float):
    """Queue one prompt and wait for it. Returns (outputs, error).

    Split out from the old single-shot helper because A14B runs as two jobs:
    both 14B experts in one prompt OOM inside the fp8 dequantize kernel
    regardless of free VRAM. See VIDEO_SPLIT_PROMPTS in config.
    """
    import uuid as _uuid
    try:
        r = await c.post(f"{COMFY}/prompt",
                         json={"prompt": wf, "client_id": str(_uuid.uuid4())})
        if r.status_code != 200:
            return None, f"ComfyUI rejected the job: {r.text[:300]}"
        pid = r.json()["prompt_id"]
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        await asyncio.sleep(3)
        try:
            h = (await c.get(f"{COMFY}/history/{pid}")).json()
        except Exception:
            continue
        if pid in h:
            entry = h[pid]
            st = entry.get("status") or {}
            if st.get("status_str") == "error":
                return None, json.dumps(st.get("messages"))[:400]
            return (entry.get("outputs") or {}), None
    return None, f"timed out after {int(timeout)}s"


def _first_output(outputs: dict, *keys):
    """First file an output node reported under any of `keys`."""
    for node_out in (outputs or {}).values():
        for k in keys:
            for f in (node_out.get(k) or []):
                if f.get("filename"):
                    return f
    return None


def _wan_expert(expert: str, i2v: bool) -> dict:
    """Loader -> Lightning LoRA -> ModelSamplingSD3 chain for one expert."""
    if i2v:
        unet = VIDEO_I2V_UNET_HIGH if expert == "high" else VIDEO_I2V_UNET_LOW
        lora = VIDEO_I2V_LORA_HIGH if expert == "high" else VIDEO_I2V_LORA_LOW
    else:
        unet = VIDEO_UNET_HIGH if expert == "high" else VIDEO_UNET_LOW
        lora = VIDEO_LORA_HIGH if expert == "high" else VIDEO_LORA_LOW
    return {
      "u":  {"class_type": "UNETLoader",
             "inputs": {"unet_name": unet, "weight_dtype": "default"}},
      # LoraLoaderModelOnly, not LoraLoader: the Lightning LoRAs carry no text
      # encoder weights, and the CLIP-aware loader errors on them.
      "ld": {"class_type": "LoraLoaderModelOnly",
             "inputs": {"lora_name": lora, "strength_model": VIDEO_LORA_STRENGTH,
                        "model": ["u", 0]}},
      # WAN is flow-matching; without the shift the sampler returns mush.
      "ms": {"class_type": "ModelSamplingSD3",
             "inputs": {"shift": VIDEO_SHIFT, "model": ["ld", 0]}},
      "clip": {"class_type": "CLIPLoader",
               "inputs": {"clip_name": VIDEO_CLIP, "type": "wan"}},
      "vae": {"class_type": "VAELoader", "inputs": {"vae_name": VIDEO_VAE}},
    }


def _sweep_video_outputs():
    """Reap rendered videos and handoff latents a previous call left behind.

    A cancelled request (the user closing the chat mid-render) now leaves its
    file rather than deleting it, because a finished clip is worth far more
    than the RAM it occupies -- the directory is tmpfs, so an orphan costs
    memory, not disk, and clears on restart regardless. This bounds that.

    Deliberately generous: anything younger than the timeout could still belong
    to a job in flight, and deleting that is the bug this exists to avoid.
    """
    import time as _t
    # local import, matching _drop_output_file: COMFY_OUTPUT_DIR is not in this
    # module's top-level import list, and referencing it without this raises
    # NameError on every video call
    from .config import COMFY_OUTPUT_DIR
    if not COMFY_OUTPUT_DIR:
        return
    cutoff = _t.time() - max(VIDEO_TIMEOUT * 2, 3600)
    try:
        root = os.path.realpath(COMFY_OUTPUT_DIR)
        for sub, _dirs, files in os.walk(root):
            for fn in files:
                if not fn.endswith((".mp4", ".latent", ".webm")):
                    continue
                pth = os.path.join(sub, fn)
                try:
                    if os.path.getmtime(pth) < cutoff:
                        os.remove(pth)
                except OSError:
                    pass
    except Exception:
        pass


def _drop_output_file(meta: dict):
    """Delete ComfyUI's rendered file once the gateway holds the bytes.

    Best-effort and never fatal: the directory is tmpfs, so anything missed
    dies at the next restart rather than accumulating on disk.
    """
    from .config import COMFY_OUTPUT_DIR
    if not COMFY_OUTPUT_DIR:
        return
    try:
        pth = os.path.join(COMFY_OUTPUT_DIR, meta.get("subfolder", ""), meta["filename"])
        if os.path.commonpath([os.path.realpath(pth),
                               os.path.realpath(COMFY_OUTPUT_DIR)]) == os.path.realpath(COMFY_OUTPUT_DIR):
            os.remove(pth)
    except Exception:
        pass


async def generate_video(prompt: str, image_url: str = None, seconds: float = None,
                         width: int = None, height: int = None, seed: int = None,
                         subject=None, private: bool = False,
                         conversation_image: str = None) -> str:
    """WAN 2.2 A14B. Text-to-video, or image-to-video when a source is given.

    Runs as TWO queued ComfyUI prompts, not one. A14B is a mixture of experts:
    a high-noise expert lays down structure and motion over the first half of
    the steps, a low-noise expert refines detail over the second. Loading both
    in a single prompt dies in the fp8 dequantize kernel with an out-of-memory
    error that has nothing to do with free VRAM, so stage one writes its latent
    to ComfyUI's output directory and stage two picks it up from there. That
    directory is tmpfs and shared with this container, so the handoff never
    touches disk and needs no copy: LoadLatent resolves a "<name> [output]"
    annotation directly.
    """
    w = max(256, min(int(width or VIDEO_MAX_WIDTH), VIDEO_MAX_WIDTH)) // 16 * 16
    h = max(256, min(int(height or VIDEO_MAX_HEIGHT), VIDEO_MAX_HEIGHT)) // 16 * 16
    secs = max(1.0, min(float(seconds or VIDEO_DEFAULT_SECONDS), VIDEO_MAX_SECONDS))
    frames = _video_frames(secs)
    sd = int(seed) if seed is not None else int.from_bytes(os.urandom(4), "big")
    steps = max(2, VIDEO_STEPS)
    bound = max(1, min(VIDEO_BOUNDARY, steps - 1))

    _sweep_video_outputs()

    start_ref = None
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as c:
        # Same rule as edit_image: a model asked to animate "this" cannot know
        # the image's address, so any URL it supplies is invented unless the
        # gateway handed it over earlier.
        ref = image_url
        if conversation_image and not (image_url or "").startswith((PUBLIC_URL, "data:")):
            ref = conversation_image
        if ref and not VIDEO_I2V_ENABLED:
            return json.dumps({"error": "image-to-video is not enabled on this "
                                        "deployment; describe the scene in the "
                                        "prompt instead"})
        if ref:
            try:
                src = base64.b64decode(ref.split(",", 1)[1]) if ref.startswith("data:") \
                      else (await c.get(ref)).content
                if len(src) < 500:
                    return json.dumps({"error": f"source image invalid ({len(src)} bytes)"})
                _sweep_edit_sources()
                start_ref = await _comfy_upload(c, src, f"vid-src-{sd}.png")
            except Exception as e:
                return json.dumps({"error": f"could not read the source image: "
                                            f"{type(e).__name__}: {e}"})

        i2v = bool(start_ref)
        # VIDEO_TIMEOUT budgets the WHOLE render, not each half. Two stages each
        # given the full value would let a job run to twice the configured cap
        # and outlive the dispatch timeout in run().
        _loop = asyncio.get_running_loop()
        _deadline = _loop.time() + VIDEO_TIMEOUT
        def _left():
            return max(30.0, _deadline - _loop.time())
        # random, not seed- or time-derived: two concurrent calls must not
        # collide on the handoff latent, and a repeated seed is legitimate
        tag = "vid-" + "".join(secrets.choice(_KEY_ALPHABET) for _ in range(12))

        def conditioning(g: dict) -> tuple:
            """Text encode, plus the image conditioning i2v needs.

            WanImageToVideo rewrites BOTH conditionings and emits the latent, so
            an i2v graph cannot simply reuse the text encodes. Stage two rebuilds
            it for its own conditioning and then discards its latent in favour of
            the one stage one saved.
            """
            g["pos"] = {"class_type": "CLIPTextEncode",
                        "inputs": {"text": prompt, "clip": ["clip", 0]}}
            g["neg"] = {"class_type": "CLIPTextEncode",
                        "inputs": {"text": VIDEO_NEGATIVE, "clip": ["clip", 0]}}
            if not i2v:
                g["lat"] = {"class_type": "EmptyHunyuanLatentVideo",
                            "inputs": {"width": w, "height": h,
                                       "length": frames, "batch_size": 1}}
                return ["pos", 0], ["neg", 0], ["lat", 0]
            g["img"] = {"class_type": "LoadImage", "inputs": {"image": start_ref}}
            g["i2v"] = {"class_type": "WanImageToVideo",
                        "inputs": {"width": w, "height": h, "length": frames,
                                   "batch_size": 1, "positive": ["pos", 0],
                                   "negative": ["neg", 0], "vae": ["vae", 0],
                                   "start_image": ["img", 0]}}
            return ["i2v", 0], ["i2v", 1], ["i2v", 2]

        # ---- stage 1: high-noise expert, steps 0..bound --------------------
        g1 = _wan_expert("high", i2v)
        p1, n1, l1 = conditioning(g1)
        g1["ks"] = {"class_type": "KSamplerAdvanced", "inputs": {
            "add_noise": "enable", "noise_seed": sd, "steps": steps,
            "cfg": VIDEO_CFG, "sampler_name": VIDEO_SAMPLER,
            "scheduler": VIDEO_SCHEDULER, "start_at_step": 0,
            "end_at_step": bound,
            # the low-noise expert finishes the schedule, so the partially
            # denoised latent must keep its remaining noise
            "return_with_leftover_noise": "enable",
            "model": ["ms", 0], "positive": p1, "negative": n1, "latent_image": l1}}
        g1["sv"] = {"class_type": "SaveLatent",
                    "inputs": {"filename_prefix": f"vidlat/{tag}", "samples": ["ks", 0]}}

        outs, err = await _comfy_stage(c, g1, _left())
        if err:
            if start_ref:
                _drop_edit_source(start_ref.split(" [")[0])
            return json.dumps({"error": "video generation failed in the motion "
                                        "pass", "detail": err})
        lat = _first_output(outs, "latents")
        if not lat:
            if start_ref:
                _drop_edit_source(start_ref.split(" [")[0])
            return json.dumps({"error": "the motion pass produced no latent"})

        # LoadLatent reads from the INPUT directory unless the name carries an
        # annotation; "[output]" points it at the shared tmpfs instead, so the
        # latent never has to be copied anywhere.
        latname = os.path.join(lat.get("subfolder", ""), lat["filename"])

        # ---- stage 2: low-noise expert, steps bound..steps -----------------
        g2 = _wan_expert("low", i2v)
        p2, n2, _ = conditioning(g2)
        g2["ll"] = {"class_type": "LoadLatent",
                    "inputs": {"latent": f"{latname} [output]"}}
        g2["ks"] = {"class_type": "KSamplerAdvanced", "inputs": {
            "add_noise": "disable", "noise_seed": 0, "steps": steps,
            "cfg": VIDEO_CFG, "sampler_name": VIDEO_SAMPLER,
            "scheduler": VIDEO_SCHEDULER, "start_at_step": bound,
            "end_at_step": steps, "return_with_leftover_noise": "disable",
            "model": ["ms", 0], "positive": p2, "negative": n2,
            "latent_image": ["ll", 0]}}
        g2["dec"] = {"class_type": "VAEDecode",
                     "inputs": {"samples": ["ks", 0], "vae": ["vae", 0]}}
        g2["cv"] = {"class_type": "CreateVideo",
                    "inputs": {"images": ["dec", 0], "fps": VIDEO_FPS}}
        # h264/mp4 rather than the default: it is the only combination every
        # browser plays inline without a download prompt.
        g2["sav"] = {"class_type": "SaveVideo", "inputs": {
            "video": ["cv", 0], "filename_prefix": "vid",
            "format": "mp4", "codec": "h264"}}

        outs2, err2 = await _comfy_stage(c, g2, _left())
        _drop_output_file(lat)                     # the latent is scratch
        if start_ref:
            _drop_edit_source(start_ref.split(" [")[0])
        if err2:
            return json.dumps({"error": "video generation failed in the detail "
                                        "pass", "detail": err2})
        meta = _first_output(outs2, "images", "videos")
        if not meta:
            return json.dumps({"error": "the detail pass produced no video file"})

        try:
            v = await c.get(f"{COMFY}/view", params={
                "filename": meta["filename"],
                "subfolder": meta.get("subfolder", ""),
                "type": meta.get("type", "output")})
            v.raise_for_status()
            raw = v.content
        except Exception as e:
            # Deliberately leave the file: a render costs minutes of GPU and the
            # output directory is tmpfs, so an orphan is cheap and recoverable
            # while a deleted one is not. _sweep_video_outputs() reaps it later.
            return json.dumps({"error": "could not read the rendered video",
                               "detail": f"{type(e).__name__}: {e}"})
        # NOT a finally. A finally also runs when the client disconnects --
        # asyncio.CancelledError is a BaseException, so it sails past
        # `except Exception` but still triggers cleanup on the way out, which
        # destroyed a finished 5s clip that had cost 20 minutes of GPU. Delete
        # only once the bytes are actually in hand.
        _drop_output_file(meta)

    dur = round((frames - 1) / VIDEO_FPS, 2)
    if private:
        tok = _private_video_put(raw, "video/mp4")
        url = f"{PUBLIC_URL}/tmpvid/{tok}.mp4"
        return json.dumps({
            "status": "ok", "url": url, "seed": sd, "size": f"{w}x{h}",
            "seconds": dur, "private": True,
            # The URL goes BETWEEN the tags, not in src=. Open WebUI matches
            # /<video[^>]*>([\s\S]*?)<\/video>/ and takes capture group 1 as the
            # source, so <video src="..."></video> has empty inner content and
            # renders as literal text instead of a player. The link below is the
            # fallback for clients that strip the tag entirely.
            # URL between the tags, not src= -- see the note in the private branch.
        "markdown": f"<video>{url}</video>\n\n[Download the clip]({url})",
            "note": f"Include the markdown field verbatim. This video was never "
                    f"written to disk and the link stops working in "
                    f"{PRIVATE_VIDEO_TTL // 60} minutes."})

    try:
        key = f"vid/{''.join(secrets.choice(_KEY_ALPHABET) for _ in range(16))}.mp4"
        url = await s3.put(key, raw, "video/mp4")
        await M.index_file(s3.BUCKET, key, url, "video/mp4", len(raw),
                           prompt=prompt, subject=subject, source_file=None,
                           image_b64=None)
    except Exception as e:
        return json.dumps({"error": "could not store the video",
                           "detail": f"{type(e).__name__}: {e}"})
    return json.dumps({
        "status": "ok", "url": url, "seed": sd, "size": f"{w}x{h}", "seconds": dur,
        "markdown": f"<video>{url}</video>\n\n[Download the clip]({url})",
        "note": "Include the markdown field verbatim in your reply to show the video."})


DISPATCH = {"get_current_datetime": get_current_datetime, "web_search": web_search,
            "fetch_url": fetch_url, "browse_page": browse_page,
            "generate_image": generate_image, "edit_image": edit_image,
            "remember": remember, "recall": recall, "forget": forget,
            "search_documents": search_documents, "search_images": search_images,
            "delete_image": delete_image, "generate_video": generate_video}

# image generation legitimately takes ~40s+; the default 90s cap is too tight
TIMEOUTS = {"generate_image": 330, "edit_image": 700,
            "generate_video": VIDEO_TIMEOUT + 60}

async def run(name: str, args: dict, subject=None, source=None, private=False,
              conversation_image=None):
    """Execute a server tool. Returns (text, optional_image_b64)."""
    fn = DISPATCH[name]
    if name in CONVERSATION_IMAGE_TOOLS:
        # SECURITY/CORRECTNESS: like subject, this comes from the request. The
        # model cannot know the address of an image it was merely shown, so any
        # URL it supplies for one is a guess; the conversation's own image wins.
        args.pop("conversation_image", None)
        args["conversation_image"] = conversation_image
    if name in PRIVATE_AWARE_TOOLS:
        # SECURITY: like subject, this comes from the request, never the model.
        args.pop("private", None)
        args["private"] = private
    if name in SUBJECT_TOOLS:
        # SECURITY: identity comes from the request, never from the model.
        args.pop("subject", None)
        args["subject"] = subject
        if name == "remember":
            args["source"] = source
    try:
        res = await asyncio.wait_for(fn(**args), timeout=TIMEOUTS.get(name, 90))
        if isinstance(res, dict):
            res = json.dumps(res, default=str)
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"}), None
    return res if isinstance(res, tuple) else (res, None)
