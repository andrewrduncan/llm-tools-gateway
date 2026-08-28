"""Minimal S3 (SigV4) PUT for Garage. Hand-rolled to avoid pulling boto3 into a
container that otherwise needs nothing else from it."""
import hashlib, hmac, datetime
import httpx
from ..config import (S3_ENDPOINT as ENDPOINT, S3_BUCKET as BUCKET,
                      S3_KEY_ID as KEY, S3_SECRET as SECRET,
                      S3_REGION as REGION, PUBLIC_URL as GATEWAY_PUBLIC)

ENDPOINT = os.environ.get("GARAGE_ENDPOINT", "http://garage:3900")
PUBLIC   = os.environ.get("GARAGE_PUBLIC",   "http://192.168.1.128:3900")
BUCKET   = os.environ.get("GARAGE_BUCKET",   "images")
KEY      = os.environ.get("GARAGE_KEY_ID", "")
SECRET   = os.environ.get("GARAGE_SECRET", "")
REGION   = os.environ.get("GARAGE_REGION", "garage")
GATEWAY_PUBLIC = os.environ.get("GATEWAY_PUBLIC", "http://192.168.1.128:8000")


def _sign(k, m):
    return hmac.new(k, m.encode(), hashlib.sha256).digest()


async def get(key: str):
    """Signed GET. Garage has no anonymous access, so the gateway fetches with
    credentials and re-serves -- that is what makes image URLs work in a browser."""
    host = ENDPOINT.split("://", 1)[1]
    t = datetime.datetime.now(datetime.timezone.utc)
    amz, ds = t.strftime("%Y%m%dT%H%M%SZ"), t.strftime("%Y%m%d")
    sha = hashlib.sha256(b"").hexdigest()
    cr = (f"GET\n/{BUCKET}/{key}\n\nhost:{host}\nx-amz-content-sha256:{sha}\n"
          f"x-amz-date:{amz}\n\nhost;x-amz-content-sha256;x-amz-date\n{sha}")
    scope = f"{ds}/{REGION}/s3/aws4_request"
    sts = f"AWS4-HMAC-SHA256\n{amz}\n{scope}\n" + hashlib.sha256(cr.encode()).hexdigest()
    sk = _sign(_sign(_sign(_sign(("AWS4" + SECRET).encode(), ds), REGION), "s3"), "aws4_request")
    sig = hmac.new(sk, sts.encode(), hashlib.sha256).hexdigest()
    headers = {"Host": host, "x-amz-date": amz, "x-amz-content-sha256": sha,
               "Authorization": (f"AWS4-HMAC-SHA256 Credential={KEY}/{scope}, "
                                 f"SignedHeaders=host;x-amz-content-sha256;x-amz-date, "
                                 f"Signature={sig}")}
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.get(f"{ENDPOINT}/{BUCKET}/{key}", headers=headers)
        r.raise_for_status()
        return r.content, r.headers.get("content-type", "application/octet-stream")


async def put(key: str, body: bytes, content_type: str = "application/octet-stream"):
    host = ENDPOINT.split("://", 1)[1]
    t = datetime.datetime.now(datetime.timezone.utc)
    amz, ds = t.strftime("%Y%m%dT%H%M%SZ"), t.strftime("%Y%m%d")
    sha = hashlib.sha256(body).hexdigest()
    cr = (f"PUT\n/{BUCKET}/{key}\n\ncontent-type:{content_type}\nhost:{host}\n"
          f"x-amz-content-sha256:{sha}\nx-amz-date:{amz}\n\n"
          f"content-type;host;x-amz-content-sha256;x-amz-date\n{sha}")
    scope = f"{ds}/{REGION}/s3/aws4_request"
    sts = f"AWS4-HMAC-SHA256\n{amz}\n{scope}\n" + hashlib.sha256(cr.encode()).hexdigest()
    sk = _sign(_sign(_sign(_sign(("AWS4" + SECRET).encode(), ds), REGION), "s3"), "aws4_request")
    sig = hmac.new(sk, sts.encode(), hashlib.sha256).hexdigest()
    headers = {
        "Host": host, "Content-Type": content_type,
        "x-amz-date": amz, "x-amz-content-sha256": sha,
        "Authorization": (f"AWS4-HMAC-SHA256 Credential={KEY}/{scope}, "
                          f"SignedHeaders=content-type;host;x-amz-content-sha256;x-amz-date, "
                          f"Signature={sig}"),
    }
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.put(f"{ENDPOINT}/{BUCKET}/{key}", content=body, headers=headers)
        r.raise_for_status()
    # Served via the gateway (/img/...), not Garage directly: Garage refuses
    # anonymous GETs, so a raw S3 URL renders as a broken image in any browser.
    return f"{GATEWAY_PUBLIC}/img/{key}"
