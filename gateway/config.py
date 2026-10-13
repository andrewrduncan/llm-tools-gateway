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
# Unused since image output moved to the websocket. It existed only for a
# fallback URL pointing at ComfyUI's /view endpoint, and there is no longer an
# output file for that endpoint to serve.
COMFY_PUBLIC_URL = os.environ.get("COMFY_PUBLIC_URL", COMFY_URL).rstrip("/")

# Unused since image output moved to the websocket: ComfyUI no longer writes an
# output file for the gateway to clean up. Kept so an existing deployment that
# sets it does not break, and because a future non-websocket output node would
# need it again.
COMFY_OUTPUT_DIR = os.environ.get("COMFY_OUTPUT_DIR", "")

# Bind-mount ComfyUI's INPUT dir here so an edit can remove its own source image.
# That source is the user's upload: working data, never indexed and never put in
# object storage, and it should not outlive the job that needed it. Without this
# every edited image accumulates in ComfyUI's input directory forever.
COMFY_INPUT_DIR = os.environ.get("COMFY_INPUT_DIR", "")
# Safety net for sources orphaned by a crash between upload and cleanup.
COMFY_INPUT_TTL = int(os.environ.get("COMFY_INPUT_TTL", "900"))

# ---- Video generation (WAN 2.2 A14B, MoE) ---------------------------------
# Off unless a deployment opts in. Unlike the image tools, this one cannot be
# gated by a file the gateway can see: the weights live in ComfyUI's models
# directory, which the gateway does not mount. An explicit flag is the honest
# gate -- a tool that is advertised but whose weights are absent fails at call
# time, which is exactly what "tools degrade, never break" forbids.
VIDEO_ENABLED = _b("VIDEO_ENABLED", "0")

# A14B, not TI2V-5B. The 5B model is documented as 720P-ONLY, and running it
# below that is not "lower quality" -- it is out of distribution and returns
# incoherent output (measured: green striping, no subject, at 640x352). Its
# native 1280x704 could not finish a 5s clip in 40 minutes on this hardware, so
# there is no setting at which 5B is both correct and affordable here.
#
# A14B is trained for 480P and 720P, so 832x480 is IN distribution, and it is
# the only WAN 2.2 variant with 4-step Lightning LoRAs. Measured side by side at
# 480x480/33f/seed 12345, 5B produced noise and A14B produced a photorealistic,
# temporally stable clip.
#
# A14B is a mixture of experts: a high-noise expert lays down structure and
# MOTION, a low-noise expert refines detail. Both are needed -- with only the
# low-noise expert the scene is correct but nearly static (measured: a boat that
# does not travel). See VIDEO_SPLIT_PROMPTS for why they run as two jobs.
VIDEO_VARIANT = os.environ.get("VIDEO_VARIANT", "t2v")       # t2v | i2v
VIDEO_UNET_HIGH = os.environ.get(
    "VIDEO_UNET_HIGH", "wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors")
VIDEO_UNET_LOW = os.environ.get(
    "VIDEO_UNET_LOW", "wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors")
# The t2v Lightning LoRAs are v1.1 and the i2v ones v1; the names are not
# symmetric, so neither can be derived from the other.
VIDEO_LORA_HIGH = os.environ.get(
    "VIDEO_LORA_HIGH", "wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors")
VIDEO_LORA_LOW = os.environ.get(
    "VIDEO_LORA_LOW", "wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors")
VIDEO_LORA_STRENGTH = float(os.environ.get("VIDEO_LORA_STRENGTH", "1.0"))
# Animating a supplied image needs the i2v weights: the t2v model has no image
# conditioning at all, so feeding it a start frame is not a degraded result,
# it is the wrong graph. A deployment that has only the t2v pair leaves these
# empty and the tool stops advertising the image parameter.
VIDEO_I2V_UNET_HIGH = os.environ.get(
    "VIDEO_I2V_UNET_HIGH", "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors")
VIDEO_I2V_UNET_LOW = os.environ.get(
    "VIDEO_I2V_UNET_LOW", "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors")
VIDEO_I2V_LORA_HIGH = os.environ.get(
    "VIDEO_I2V_LORA_HIGH", "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors")
VIDEO_I2V_LORA_LOW = os.environ.get(
    "VIDEO_I2V_LORA_LOW", "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors")
VIDEO_I2V_ENABLED = _b("VIDEO_I2V_ENABLED", "1")
VIDEO_CLIP = os.environ.get("VIDEO_CLIP", "umt5_xxl_fp8_e4m3fn_scaled.safetensors")
# A14B uses the Wan 2.1 VAE. Only TI2V-5B uses wan2.2_vae, and crossing them
# fails at runtime rather than degrading.
VIDEO_VAE = os.environ.get("VIDEO_VAE", "wan_2.1_vae.safetensors")

# Loading both 14B experts in ONE ComfyUI prompt reliably dies with
#   RuntimeError: HIP kernel launch failed: out of memory
# raised inside the fp8 dequantize kernel. This is NOT capacity: it reproduces
# with 95GB of 96GB free, with --reserve-vram lowered to 4, and at a latent as
# small as 480x480x33. ComfyUI stages both quantized models (13.6GB each) and
# never unloads the first. Running the experts as two queued prompts forces a
# teardown between them and costs ~6s.
VIDEO_SPLIT_PROMPTS = _b("VIDEO_SPLIT_PROMPTS", "1")

# MEASURED on gfx1151 (Strix Halo, 96GB VRAM), wall clock, full MoE, 4 steps:
#   480x480  33f (2s)   ->  0m79   (both experts, incl. teardown)
#   832x480  81f (5s)   -> ~20m    (stage 1 alone was 9m42)
# Cost is dominated by CLIP LENGTH, not resolution: 4.3x the pixel-frames cost
# ~16x the time, because attention is quadratic in token count. Shortening a
# clip buys far more than shrinking it.
#
# 832x480 is WAN's native 480P widescreen. Do not raise the cap to 720p without
# measuring -- at this scaling it is hours, not minutes.
VIDEO_MAX_WIDTH = int(os.environ.get("VIDEO_MAX_WIDTH", "832"))
VIDEO_MAX_HEIGHT = int(os.environ.get("VIDEO_MAX_HEIGHT", "480"))
# A14B runs at 16fps, NOT the 24 that TI2V-5B uses. Its own template derives
# length as floor(seconds * fps) + 1, which lands on the 4n+1 the latent needs.
VIDEO_FPS = float(os.environ.get("VIDEO_FPS", "16"))
VIDEO_MAX_SECONDS = float(os.environ.get("VIDEO_MAX_SECONDS", "5"))
# Default well under the cap. Defaulting to the maximum would make every
# unqualified "make me a video" a ~20 minute job instead of a ~1 minute one.
VIDEO_DEFAULT_SECONDS = float(os.environ.get("VIDEO_DEFAULT_SECONDS", "2"))
# 4 steps split evenly between the experts, and cfg 1.0 -- these are not tuning
# knobs. The Lightning LoRAs are step-distilled: they are TRAINED for 4 steps at
# cfg 1, and raising either degrades output rather than improving it.
VIDEO_STEPS = int(os.environ.get("VIDEO_STEPS", "4"))
VIDEO_BOUNDARY = int(os.environ.get("VIDEO_BOUNDARY", "2"))
VIDEO_CFG = float(os.environ.get("VIDEO_CFG", "1.0"))
VIDEO_SHIFT = float(os.environ.get("VIDEO_SHIFT", "5.0"))
VIDEO_SAMPLER = os.environ.get("VIDEO_SAMPLER", "euler")
VIDEO_SCHEDULER = os.environ.get("VIDEO_SCHEDULER", "simple")
VIDEO_NEGATIVE = os.environ.get(
    "VIDEO_NEGATIVE",
    "blurry, low quality, distorted, watermark, text, static, still image")
# A 5s clip is ~20 minutes of GPU. The gateway streams progress heartbeats, so
# the client connection survives; this only bounds the wait.
VIDEO_TIMEOUT = int(os.environ.get("VIDEO_TIMEOUT", "2400"))
# Private video cannot be inlined the way a private image is -- base64 of an
# MP4 would be megabytes of context on every subsequent turn. It is held in
# gateway memory and served from a URL that expires.
PRIVATE_VIDEO_TTL = int(os.environ.get("PRIVATE_VIDEO_TTL", "1800"))

S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "").rstrip("/")
S3_BUCKET = os.environ.get("S3_BUCKET", "images")
S3_KEY_ID = os.environ.get("S3_KEY_ID", "")
S3_SECRET = os.environ.get("S3_SECRET", "")
S3_REGION = os.environ.get("S3_REGION", "us-east-1")

# ---- history images -------------------------------------------------------
# Vision models turn one screenshot into tens of thousands of tokens, and the
# whole conversation is re-sent every turn. llama.cpp also disables prefix
# caching entirely for multimodal models, so every historical image is
# re-encoded from scratch on every request: a second image-editing turn was
# measured at a 105k-token prompt needing ~13 minutes of prefill.
#
# Keeping only the newest images and leaving a text placeholder for the rest
# preserves the thread of the conversation at ~15 tokens instead of ~15,000.
STRIP_HISTORY_IMAGES = _b("STRIP_HISTORY_IMAGES", "1")
KEEP_RECENT_IMAGES = int(os.environ.get("KEEP_RECENT_IMAGES", "1"))

# ---- private / ephemeral mode ---------------------------------------------
# Nothing generated in a private turn is written down: no object storage, no
# index row, no embedding, no memory. The image comes back inline in the
# transcript and disappears with it.
#
# Open WebUI marks a Temporary Chat with a chat id prefixed "temporary:" (older
# builds used "local:"), so its own toggle drives this with no extra UI. Clients
# with no such concept opt in with {"private": true} or a model name ending in
# PRIVATE_MODEL_SUFFIX.
PRIVATE_CHAT_ID_PREFIXES = tuple(p.strip() for p in os.environ.get(
    "PRIVATE_CHAT_ID_PREFIXES", "temporary:,local:").split(",") if p.strip())
PRIVATE_MODEL_SUFFIX = os.environ.get("PRIVATE_MODEL_SUFFIX", "-private")

# Which upstream models get a "-private" twin advertised in /v1/models. Empty
# means all of them, which suits a single-model deployment. Name them explicitly
# when only some models should be offered in private mode -- for example when
# one model is an uncensored build kept for private use and the stock model is
# not. A client can still request any "<model>-private" by name; this controls
# what is advertised, not what is accepted.
PRIVATE_TWIN_MODELS = {m.strip() for m in os.environ.get(
    "PRIVATE_TWIN_MODELS", "").split(",") if m.strip()}

# Withheld in private mode: every tool that reads or writes durable state. The
# model is never offered them, so it cannot leak into or out of the session.
PRIVATE_DISABLED_TOOLS = {t.strip() for t in os.environ.get(
    "PRIVATE_DISABLED_TOOLS",
    "remember,forget,recall,search_documents,search_images,delete_image"
).split(",") if t.strip()}

# An inline image lives in the context window, so size matters far more than it
# does for a stored one: a 1024x1024 PNG is ~1.5 MB and base64 adds a third.
#
# The format has to be decodable by the MODEL SERVER's vision stack, not just by
# the browser -- the image is fed back to the model as well as rendered. WEBP is
# the obvious choice on size and every browser reads it, but llama.cpp decodes
# images with stb_image, which has no WebP support, and rejects the request with
# "mtmd_helper_bitmap_init_from_buf: failed to decode webp buffer". Stick to
# JPEG or PNG unless you know your backend handles more.
PRIVATE_IMAGE_FORMAT = os.environ.get("PRIVATE_IMAGE_FORMAT", "JPEG").upper()
PRIVATE_IMAGE_QUALITY = int(os.environ.get("PRIVATE_IMAGE_QUALITY", "80"))

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
        "video":    bool(COMFY_URL) and VIDEO_ENABLED,
        # deleting needs both halves: the object store holds the image, the
        # index holds the row that makes it findable.
        "delete":   bool(S3_ENDPOINT and S3_KEY_ID and S3_SECRET
                         and PG_DSN and EMBED_URL),
    }
