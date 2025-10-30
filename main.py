import os, hashlib, logging
from io import BytesIO
from typing import Optional

from fastapi import FastAPI, File, UploadFile, HTTPException, Request, Query
from fastapi.responses import Response, JSONResponse
import httpx
from PIL import Image, UnidentifiedImageError
from rembg import remove, new_session

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("wair-cleaner")

# Config
MODEL_NAME = os.environ.get("REMBG_MODEL", "u2netp")  # keep memory low
MAX_SIDE = int(os.environ.get("MAX_SIDE", "2048"))    # downscale huge inputs
DEBUG = os.environ.get("DEBUG", "0") == "1"

# Load model once
SESSION = new_session(MODEL_NAME)

app = FastAPI()

@app.get("/")
def root():
    return {"ok": True, "service": "wair-cleaner", "model": MODEL_NAME}

@app.get("/healthz")
def health():
    return {"ok": True}

def _downscale_if_needed(img: Image.Image) -> Image.Image:
    w, h = img.size
    m = max(w, h)
    if m <= MAX_SIDE:
        return img
    scale = MAX_SIDE / float(m)
    new_size = (max(1, int(w*scale)), max(1, int(h*scale)))
    return img.resize(new_size, Image.LANCZOS)

def _ensure_png_bytes(raw: bytes) -> bytes:
    # Decode with Pillow (catches non-images), optionally downscale, re-encode PNG
    try:
        with Image.open(BytesIO(raw)) as im:
            im.load()  # force decode
            im = _downscale_if_needed(im.convert("RGBA"))
            buf = BytesIO()
            im.save(buf, format="PNG")
            return buf.getvalue()
    except UnidentifiedImageError:
        raise HTTPException(status_code=400, detail="unsupported or corrupt image")
    except Exception:
        log.exception("Pillow decode/encode failed")
        raise HTTPException(status_code=400, detail="failed to parse image")

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

    # Fetch source bytes
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

    # Pre-validate & normalize input to a manageable PNG
    src_png = _ensure_png_bytes(raw)

    # Background removal
    try:
        out = remove(src_png, session=SESSION)
    except Exception as e:
        log.exception("remove() failed")
        if DEBUG:
            return JSONResponse(status_code=502, content={"error": "clean failed", "exception": str(e)})
        raise HTTPException(status_code=502, detail="clean failed")

    etag = hashlib.sha256(src_png).hexdigest()
    headers = {"ETag": etag, "Cache-Control": "public, max-age=31536000, immutable"}
    return Response(content=out, media_type="image/png", headers=headers)

# Optional tiny self-test route (does not require token)
@app.get("/selftest")
def selftest():
    # 1x1 white
    buf = BytesIO()
    Image.new("RGBA", (1, 1), (255, 255, 255, 255)).save(buf, "PNG")
    try:
        _ = remove(buf.getvalue(), session=SESSION)
        return {"ok": True, "model": MODEL_NAME}
    except Exception as e:
        log.exception("selftest failed")
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


@app.get("/diag")
def diag():
    try:
        import onnxruntime as ort
        info = {
            "onnxruntime_version": getattr(ort, "__version__", "unknown"),
            "available_providers": ort.get_available_providers(),
            "device": getattr(ort, "get_device", lambda: "unknown")(),
            "model": MODEL_NAME,
        }
        return info
    except Exception as e:
        log.exception("diag failed")
        return JSONResponse(status_code=500, content={"error": str(e)})
