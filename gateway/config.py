"""
Central configuration. Every deployment-specific value lives here and nowhere
else, so the gateway runs in front of ANY OpenAI-compatible backend
(vLLM, llama.cpp, Ollama, LM Studio, TGI, ...) without code changes.

Design rule: a tool is only offered to the model if its backend is configured.
A user with nothing but llama.cpp still gets `get_current_datetime` and
`fetch_url`; they never see broken image or memory tools.
"""
import os


def _b(name: str, default: str = "") -> bool:
    return os.environ.get(name, default).lower() not in ("", "0", "false", "no")


# ---- upstream model server (the only required setting) --------------------
UPSTREAM = os.environ.get("UPSTREAM_URL", "http://localhost:8001").rstrip("/")

# Public base URL of THIS gateway. Used to build image URLs that a browser can
# reach, so it must be routable from the client, not from inside the container.
PUBLIC_URL = os.environ.get("GATEWAY_PUBLIC_URL", "http://localhost:8000").rstrip("/")

# ---- behaviour ------------------------------------------------------------
MAX_TOOL_ROUNDS = int(os.environ.get("MAX_TOOL_ROUNDS", "6"))
PROGRESS = _b("GATEWAY_PROGRESS", "1")          # stream tool progress
PROGRESS_HEARTBEAT = int(os.environ.get("PROGRESS_HEARTBEAT_SECONDS", "10"))

# Greedy decoding cannot escape a repetition cycle; these are applied only when
# the client sets no sampling controls of its own.
REPETITION_PENALTY = float(os.environ.get("DEFAULT_REPETITION_PENALTY", "1.1"))
MIN_TEMPERATURE = float(os.environ.get("MIN_TEMPERATURE", "0.3"))
# DRY penalises repeated multi-token SEQUENCES rather than single tokens, which is
# the shape real loops take (a line or block cycling). llama.cpp only; ignored
# elsewhere. Its own default look-back of 64 is too short to see a repeating block.
DRY_MULTIPLIER = float(os.environ.get("DRY_MULTIPLIER", "0.8"))
DRY_PENALTY_LAST_N = int(os.environ.get("DRY_PENALTY_LAST_N", "1024"))

# ---- identity -------------------------------------------------------------
KEYS_FILE = os.environ.get("KEYS_FILE", "/etc/llm-tools-gateway/keys.json")
TRUST_USER_HEADERS = _b("TRUST_USER_HEADERS", "1")
USER_ID_HEADERS = [h.strip().lower() for h in os.environ.get(
    "USER_ID_HEADERS", "x-openwebui-user-id,x-openwebui-user-email,x-user-id"
).split(",") if h.strip()]

# ---- optional backends ----------------------------------------------------
SEARXNG_URL = os.environ.get("SEARXNG_URL", "").rstrip("/")
PG_DSN = os.environ.get("PG_DSN", "")
EMBED_URL = os.environ.get("EMBED_URL", "").rstrip("/")
COMFY_URL = os.environ.get("COMFY_URL", "").rstrip("/")
# URL the CLIENT's browser uses to reach ComfyUI. Only used as a fallback when
# object storage is not configured -- a compose service name like
# http://comfyui:8188 is unreachable from a browser, so set this to a LAN or
# public address if you rely on the fallback.
COMFY_PUBLIC_URL = os.environ.get("COMFY_PUBLIC_URL", COMFY_URL).rstrip("/")

# Optional: bind-mount ComfyUI's output dir here and delete_image can remove the
# generator's own copy too. Unset simply means that third copy is left alone.
COMFY_OUTPUT_DIR = os.environ.get("COMFY_OUTPUT_DIR", "")

S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "").rstrip("/")
S3_BUCKET = os.environ.get("S3_BUCKET", "images")
S3_KEY_ID = os.environ.get("S3_KEY_ID", "")
S3_SECRET = os.environ.get("S3_SECRET", "")
S3_REGION = os.environ.get("S3_REGION", "us-east-1")

DEFAULT_TZ = os.environ.get("DEFAULT_TZ", "UTC")
PLAYWRIGHT = _b("ENABLE_BROWSER", "1")

# ComfyUI workflows are USER-SUPPLIED JSON, not baked into the code -- model
# filenames and node graphs differ per install.
WORKFLOW_DIR = os.environ.get("WORKFLOW_DIR", "/workflows")
WORKFLOW_GENERATE = os.environ.get("WORKFLOW_GENERATE", "generate.json")
WORKFLOW_EDIT = os.environ.get("WORKFLOW_EDIT", "edit.json")


def capabilities() -> dict:
    """Which tool groups can actually run, given what's configured."""
    import os.path as _p
    wf = lambda f: _p.exists(_p.join(WORKFLOW_DIR, f))
    return {
        "clock":    True,                                   # always available
        "fetch":    True,                                   # stdlib HTTP only
        "search":   bool(SEARXNG_URL),
        "browser":  PLAYWRIGHT,
        "memory":   bool(PG_DSN and EMBED_URL),
        "storage":  bool(S3_ENDPOINT and S3_KEY_ID and S3_SECRET),
        "generate": bool(COMFY_URL) and wf(WORKFLOW_GENERATE),
        "edit":     bool(COMFY_URL) and wf(WORKFLOW_EDIT),
        # deleting needs both halves: the object store holds the image, the
        # index holds the row that makes it findable.
        "delete":   bool(S3_ENDPOINT and S3_KEY_ID and S3_SECRET
                         and PG_DSN and EMBED_URL),
    }
