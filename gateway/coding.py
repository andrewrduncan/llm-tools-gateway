"""
Chat mode and coding mode: which models share the GPU, and the switch between.

    chat    the strong chat model; image and video tools available
    coding  the coder plus a small chat model; image and video tools withheld

The two configurations each fit the GPU; their union does not. So every
transition is ordered: the outgoing model is confirmed gone before anything
that needs its memory is allowed to load. Getting that order wrong does not
fail cleanly -- an overcommitted GPU tears down a device context, and on this
stack llama.cpp's vision encoder then fails silently inside a 200 response.

Model servers here are llama.cpp in router mode. The gateway drives them only
through their public API: GET /models for state, POST /models/unload to evict,
and an ordinary 1-token completion to load (the router autoloads by name).
"""
import asyncio, glob, logging, time
import httpx

from .config import (UPSTREAM, CODER_URL, CODER_MODEL, CODING_CHAT_MODEL,
                     CHAT_PRELOAD_MODEL, CODING_IDLE_MINUTES, COMFY_URL)

log = logging.getLogger("gateway")

# Shown at the top of every chat reply while coding, so a weaker answer or a
# missing image tool is never a mystery.
NOTE = ("_\U0001F4BB Coding session in progress: the small model is answering, "
        "and image and video generation are paused. Run `llm-mode chat` to end "
        "it now; it ends on its own after {idle} min without coding._\n\n")

UNLOAD_TIMEOUT = 180


class ModeBusy(Exception):
    """The switch cannot happen right now; the message says why."""


async def _models(base):
    """[(id, status)] from a llama.cpp router. Raises if unreachable."""
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{base}/models")
        r.raise_for_status()
        return [(m["id"], (m.get("status") or {}).get("value", "?"))
                for m in r.json().get("data", [])]


async def _resident(base):
    """Model ids holding (or about to hold) GPU memory on a router."""
    return [i for i, s in await _models(base) if s not in ("unloaded", "?")]


async def _unload_all(base, keep=()):
    """Evict every resident model except `keep`, and wait until it is gone.

    Waiting matters more than asking: the memory is not free until the router
    reports the model unloaded, and that is what the next load depends on.
    """
    deadline = time.time() + UNLOAD_TIMEOUT
    asked = set()
    while True:
        victims = [m for m in await _resident(base) if m not in keep]
        if not victims:
            return
        if time.time() > deadline:
            raise ModeBusy(f"Timed out waiting for {', '.join(victims)} to unload.")
        async with httpx.AsyncClient(timeout=30) as c:
            for m in victims:
                if m not in asked:
                    log.info("mode: unloading %s from %s", m, base)
                    await c.post(f"{base}/models/unload", json={"model": m})
                    asked.add(m)
        await asyncio.sleep(1)


async def _preload(base, model):
    """Load a model by asking it for one token. Errors are logged, not raised."""
    try:
        async with httpx.AsyncClient(timeout=600) as c:
            r = await c.post(f"{base}/v1/chat/completions", json={
                "model": model, "max_tokens": 1,
                "messages": [{"role": "user", "content": "ok"}]})
            log.info("mode: preloaded %s (HTTP %s)", model, r.status_code)
    except Exception as e:
        log.warning("mode: preload of %s failed: %s", model, e)


async def _comfy_busy():
    """True while ComfyUI has a render running or queued."""
    if not COMFY_URL:
        return False
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            q = (await c.get(f"{COMFY_URL}/queue")).json()
        return bool(q.get("queue_running") or q.get("queue_pending"))
    except Exception:
        return False        # ComfyUI down holds no GPU memory either


def _gpu_used_gb():
    for f in glob.glob("/sys/class/drm/card*/device/mem_info_vram_used"):
        try:
            return round(int(open(f).read()) / 1e9, 1)
        except Exception:
            pass
    return None


class ModeManager:
    def __init__(self):
        self.mode = "chat"
        self.since = time.time()
        self.reason = "startup"
        self.last_activity = time.time()
        self.in_flight = 0
        self.switching = None           # what a transition is doing right now
        self._lock = asyncio.Lock()
        self._tasks = set()

    @property
    def enabled(self):
        return bool(CODER_URL and CODER_MODEL and CODING_CHAT_MODEL)

    @property
    def coding(self):
        return self.mode == "coding"

    def note(self):
        return NOTE.format(idle=f"{CODING_IDLE_MINUTES:g}")

    def _spawn(self, coro):
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    # -- coder traffic accounting: an in-flight request is never idle ---------
    def begin(self):
        self.in_flight += 1
        self.last_activity = time.time()

    def end(self):
        self.in_flight = max(0, self.in_flight - 1)
        self.last_activity = time.time()

    # -- transitions ------------------------------------------------------------
    async def enter_coding(self, reason):
        """Make room for the coder. Returns once it is safe to load it."""
        if not self.enabled:
            raise ModeBusy("Coding mode is not configured on this gateway.")
        async with self._lock:
            self.last_activity = time.time()
            if self.coding:
                return False
            if await _comfy_busy():
                raise ModeBusy(
                    "An image or video render is using the GPU. Coding mode "
                    "can start once it finishes; try again in a few minutes.")
            # Flip first: from here every chat request is rewritten to the small
            # model, so nothing can ask for the big one while it is leaving.
            self.mode, self.since, self.reason = "coding", time.time(), reason
            self.switching = "unloading the chat model"
            log.info("mode: CODING (%s)", reason)
            try:
                await _unload_all(UPSTREAM, keep={CODING_CHAT_MODEL})
            except Exception as e:
                # The big model may still be resident, so the coder must not
                # load. Back out rather than gamble on the memory.
                self.mode, self.since, self.reason = "chat", time.time(), "enter failed"
                log.warning("mode: could not enter coding: %s", e)
                raise ModeBusy(f"Could not free the GPU for coding: {e}")
            finally:
                self.switching = None
            return True

    async def leave_coding(self, reason, force=False):
        """Unload the coder and hand the GPU back to chat."""
        async with self._lock:
            if not self.coding or (self.in_flight and not force):
                return False
            self.switching = "unloading the coder"
            try:
                await _unload_all(CODER_URL)
            except Exception as e:
                # Staying in coding mode is the safe failure: chat keeps working
                # on the small model, and nothing big loads beside the coder.
                log.warning("mode: could not leave coding: %s", e)
                return False
            finally:
                self.switching = None
            self.mode, self.since, self.reason = "chat", time.time(), reason
            log.info("mode: CHAT (%s)", reason)
        if CHAT_PRELOAD_MODEL:
            self._spawn(_preload(UPSTREAM, CHAT_PRELOAD_MODEL))
        return True

    async def switch(self, target):
        """Manual switch, from the admin endpoint."""
        if target == "code":
            await self.enter_coding("manual")
            # Load both now, so the first request of the session is not the
            # one that waits for them.
            self._spawn(_preload(CODER_URL, CODER_MODEL))
            self._spawn(_preload(UPSTREAM, CODING_CHAT_MODEL))
        elif target == "chat":
            await self.leave_coding("manual", force=True)
        else:
            raise ValueError("mode must be 'code' or 'chat'")

    async def _reconcile(self):
        """Adopt reality if it disagrees with our state.

        The gateway restarts on every image update, and that must not forget a
        coding session: a coder still resident while the gateway believes it is
        in chat mode would let the next chat request load the big model beside
        it. So a resident coder always means coding mode.
        """
        if self.coding:
            return
        try:
            resident = await _resident(CODER_URL)
        except Exception:
            return                       # coder server down holds no memory
        if not resident:
            return
        async with self._lock:
            if self.coding:
                return
            self.mode, self.since, self.reason = "coding", time.time(), "coder was already loaded"
            self.last_activity = time.time()
            log.info("mode: CODING (adopted: %s resident)", ", ".join(resident))
            self.switching = "unloading the chat model"
            try:
                await _unload_all(UPSTREAM, keep={CODING_CHAT_MODEL})
            except Exception as e:
                log.warning("mode: adopt could not unload chat model: %s", e)
            finally:
                self.switching = None

    async def run(self):
        """Background loop: reconcile with the servers, end idle sessions."""
        while True:
            try:
                await self._reconcile()
                idle = time.time() - self.last_activity
                if (self.coding and not self.in_flight
                        and idle > CODING_IDLE_MINUTES * 60):
                    await self.leave_coding(f"idle {idle / 60:.0f} min")
            except Exception:
                log.exception("mode: background loop")
            await asyncio.sleep(15)

    async def status(self):
        async def safe(base):
            # "id" when loaded, "id (loading)" while it is still coming up.
            try:
                return [i if st == "loaded" else f"{i} ({st})"
                        for i, st in await _models(base) if st not in ("unloaded", "?")]
            except Exception as e:
                return f"unreachable ({type(e).__name__})"
        idle = time.time() - self.last_activity
        return {
            "mode": self.mode,
            "since": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(self.since)),
            "reason": self.reason,
            "switching": self.switching,
            "coder_requests_in_flight": self.in_flight,
            "idle_minutes": round(idle / 60, 1) if self.coding else None,
            "ends_after_idle_minutes": CODING_IDLE_MINUTES if self.coding else None,
            "chat_answered_by": CODING_CHAT_MODEL if self.coding else "requested model",
            "loaded": {"chat": await safe(UPSTREAM),
                       "coder": await safe(CODER_URL) if CODER_URL else []},
            "gpu_used_gb": _gpu_used_gb(),
        }


MODE = ModeManager()
