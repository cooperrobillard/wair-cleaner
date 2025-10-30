import os, hashlib, logging
from io import BytesIO
from typing import Optional

from fastapi import FastAPI, File, UploadFile, HTTPException, Request, Query
from fastapi.responses import Response
import httpx
from PIL import Image
from rembg.bg import remove

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("wair-cleaner")

app = FastAPI()

@app.get("/")
def root():
    return {"ok": True, "service": "wair-cleaner"}

@app.get("/healthz")
def health():
    return {"ok": True}

@app.on_event("startup")
async def warm():
    try:
        buf = BytesIO()
        Image.new("RGB", (1, 1), (255, 255, 255)).save(buf, "PNG")
        remove(buf.getvalue())
        log.info("Model warmed")
    except Exception:
        log.exception("Warmup failed")

@app.api_route("/clean", methods=["GET", "POST"])
async def clean(
    request: Request,
    image_url: Optional[str] = Query(default=None),
    file: Optional[UploadFile] = File(default=None),
):
    token = request.headers.get("x-cleaner-token") or request.headers.get("X-Cleaner-Token")
    if not token or token != os.environ.get("CLEANER_TOKEN"):
        raise HTTPException(status_code=401, detail="invalid token")

    if not image_url and file is None:
        raise HTTPException(status_code=400, detail="provide image_url or file")

    try:
        if image_url:
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome Safari",
                "Accept": "image/*,*/*",
                "Referer": image_url,
            }
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, headers=headers) as client:
                r = await client.get(image_url)
                if r.status_code >= 400:
                    raise HTTPException(status_code=400, detail=f"fetch failed: {r.status_code}")
                raw = r.content
        else:
            raw = await file.read()
            if not raw:
                raise HTTPException(status_code=400, detail="empty upload")
    except HTTPException:
        raise
    except Exception:
        log.exception("fetch error")
        raise HTTPException(status_code=400, detail="failed to fetch source image")

    try:
        out = remove(raw)
    except Exception:
        log.exception("remove() failed")
        raise HTTPException(status_code=502, detail="clean failed")

    etag = hashlib.sha256(raw).hexdigest()
    headers = {"ETag": etag, "Cache-Control": "public, max-age=31536000, immutable"}
    return Response(content=out, media_type="image/png", headers=headers)
