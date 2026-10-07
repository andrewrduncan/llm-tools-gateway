"""Server-side tools exposed to every client of the gateway."""
import os, json, base64, datetime, asyncio, secrets
from zoneinfo import ZoneInfo
import httpx
from . import memory as M
from .backends import s3

from .config import (SEARXNG_URL as SEARXNG, COMFY_URL as COMFY,
                     COMFY_PUBLIC_URL as COMFY_PUB,
                     PUBLIC_URL, DEFAULT_TZ, WORKFLOW_DIR, COMFY_OUTPUT_DIR,
                     WORKFLOW_GENERATE, WORKFLOW_EDIT, capabilities,
                     PRIVATE_IMAGE_FORMAT, PRIVATE_IMAGE_QUALITY,
                     COMFY_INPUT_DIR, COMFY_INPUT_TTL)
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
NAMES = {d["function"]["name"] for d in DEFS}

# Tools whose caller identity must be injected server-side. The model must NEVER
# be able to set `subject` -- otherwise it could read another user's memories.
SUBJECT_TOOLS = {"remember", "recall", "forget", "search_documents",
                 "search_images", "generate_image", "edit_image", "delete_image"}

# Tools whose behaviour changes in a private turn: they must not persist.
PRIVATE_AWARE_TOOLS = {"generate_image", "edit_image"}

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

    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"{COMFY}/prompt", json={"prompt": wf})
        if r.status_code != 200:
            return json.dumps({"error": f"ComfyUI rejected the job: {r.text[:300]}"})
        pid = r.json()["prompt_id"]
        for _ in range(150):                      # up to ~5 min
            await asyncio.sleep(2)
            h_ = (await c.get(f"{COMFY}/history/{pid}")).json()
            if pid not in h_:
                continue
            d = h_[pid]
            imgs = [i for o in d.get("outputs", {}).values() for i in o.get("images", [])]
            if imgs:
                fn = imgs[0]["filename"]
                raw = (await c.get(f"{COMFY}/view",
                                   params={"filename": fn, "type": "output"})).content
                url = f"{COMFY_PUB}/view?filename={fn}&type=output"   # fallback
                try:
                    # Durable object storage: a ComfyUI /view URL dies when its
                    # output dir is cleaned, silently breaking old chat images.
                    key = _img_key("gen")
                    url = await s3.put(key, raw, "image/png")
                    await M.index_file(s3.BUCKET, key, url, "image/png", len(raw),
                                       prompt=prompt, subject=subject, source_file=fn,
                                       image_b64=base64.b64encode(raw).decode())
                except Exception as e:
                    log_err = f"{type(e).__name__}: {e}"
                    return json.dumps({"status": "ok", "url": url, "seed": sd,
                                       "size": f"{w}x{h}",
                                       "markdown": f"![{prompt[:80]}]({url})",
                                       "warning": f"stored locally only: {log_err}",
                                       "note": "Include the markdown verbatim to show the image."})
                return json.dumps({
                    "status": "ok", "url": url, "seed": sd, "size": f"{w}x{h}",
                    "markdown": f"![{prompt[:80]}]({url})",
                    "note": "Include the markdown field verbatim in your reply to show the image."})
            st = d.get("status", {})
            if st.get("status_str") == "error":
                return json.dumps({"error": "generation failed", "detail": str(st)[:300]})
        return json.dumps({"error": "timed out after ~5 minutes"})


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

        r = await c.post(f"{COMFY}/prompt", json={"prompt": wf})
        if r.status_code != 200:
            _drop_edit_source(fname)
            return json.dumps({"error": f"ComfyUI rejected the edit: {r.text[:300]}"})
        pid = r.json()["prompt_id"]
        for _ in range(300):
            await asyncio.sleep(2)
            h_ = (await c.get(f"{COMFY}/history/{pid}")).json()
            if pid not in h_:
                continue
            d = h_[pid]
            imgs = [i for o in d.get("outputs", {}).values() for i in o.get("images", [])]
            if imgs:
                fn = imgs[0]["filename"]
                raw = (await c.get(f"{COMFY}/view",
                                   params={"filename": fn, "type": "output"})).content
                key = _img_key("edit")
                url = await s3.put(key, raw, "image/png")
                await M.index_file(s3.BUCKET, key, url, "image/png", len(raw),
                                   prompt=f"[edit] {instruction}", subject=subject, source_file=fn,
                                   image_b64=base64.b64encode(raw).decode())
                _drop_edit_source(fname)
                return json.dumps({
                    "status": "ok", "url": url, "seed": sd, "steps": st,
                    "markdown": f"![{instruction[:70]}]({url})",
                    "note": "Include the markdown field verbatim to show the edited image."})
            stt = d.get("status", {})
            if stt.get("status_str") == "error":
                _drop_edit_source(fname)
                return json.dumps({"error": "edit failed", "detail": str(stt)[:400]})
        _drop_edit_source(fname)
        return json.dumps({"error": "timed out"})



async def delete_image(image: str, subject=None):
    """Delete an image everywhere it exists.

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
    if COMFY_OUTPUT_DIR and row["source_file"]:
        p = os.path.join(COMFY_OUTPUT_DIR, os.path.basename(row["source_file"]))
        try:
            if os.path.exists(p):
                os.remove(p); removed.append("generator output")
        except Exception as e:
            failed.append(f"generator output: {type(e).__name__}: {e}")
    return json.dumps({"status": "ok" if not failed else "partial",
                       "deleted_from": removed, "errors": failed,
                       "prompt": row["prompt"],
                       "note": "The image is gone and its URL no longer resolves."})


DISPATCH = {"get_current_datetime": get_current_datetime, "web_search": web_search,
            "fetch_url": fetch_url, "browse_page": browse_page,
            "generate_image": generate_image, "edit_image": edit_image,
            "remember": remember, "recall": recall, "forget": forget,
            "search_documents": search_documents, "search_images": search_images,
            "delete_image": delete_image}

# image generation legitimately takes ~40s+; the default 90s cap is too tight
TIMEOUTS = {"generate_image": 330, "edit_image": 700}

async def run(name: str, args: dict, subject=None, source=None, private=False,
              conversation_image=None):
    """Execute a server tool. Returns (text, optional_image_b64)."""
    fn = DISPATCH[name]
    if name == "edit_image":
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
