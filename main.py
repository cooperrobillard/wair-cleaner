import base64
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
        raise HTTPException(status_code=400, detail={"error": "unsupported or corrupt image"})
    except Exception:
        log.exception("Pillow decode/encode failed")
        raise HTTPException(status_code=400, detail={"error": "failed to parse image"})

@app.api_route("/clean", methods=["GET", "POST"])
async def clean(
    request: Request,
    image_url: Optional[str] = Query(default=None),
    file: Optional[UploadFile] = File(default=None),
    return_mode: Optional[str] = Query(default=None, alias="return"),
):
    token = request.headers.get("x-cleaner-token") or request.headers.get("X-Cleaner-Token")
    if not token or token != os.environ.get("CLEANER_TOKEN"):
        log.warning("clean: invalid token supplied")
        raise HTTPException(status_code=401, detail={"error": "invalid token"})

    if not image_url and file is None:
        log.warning("clean: missing input (no image_url or file)")
        raise HTTPException(status_code=400, detail={"error": "provide image_url or file"})

    # Fetch source bytes
    try:
        if file is not None:
            if file.content_type and not file.content_type.startswith("image/"):
                log.warning("clean: upload with non-image content-type %s", file.content_type)
                raise HTTPException(status_code=400, detail={"error": "upload must be an image"})
            raw = await file.read()
            if not raw:
                log.warning("clean: empty upload from %s", file.filename)
                raise HTTPException(status_code=400, detail={"error": "empty upload"})
            source = "upload"
        elif image_url:
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome Safari",
                "Accept": "image/*,*/*",
                "Referer": image_url,
            }
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, headers=headers) as client:
                r = await client.get(image_url)
                if r.status_code >= 400:
                    log.warning("clean: fetch failed for %s with %s", image_url, r.status_code)
                    raise HTTPException(status_code=400, detail={"error": "fetch failed", "status_code": r.status_code})
                raw = r.content
            if not raw:
                log.warning("clean: fetched URL had no body %s", image_url)
                raise HTTPException(status_code=400, detail={"error": "empty download"})
            source = "url"
        else:
            log.warning("clean: neither file nor image_url provided after validation")
            raise HTTPException(status_code=400, detail={"error": "provide image_url or file"})
    except HTTPException:
        raise
    except Exception:
        log.exception("fetch error")
        raise HTTPException(status_code=400, detail={"error": "failed to fetch source image"})

    log.info("clean: source=%s bytes=%d", source, len(raw))

    # Pre-validate & normalize input to a manageable PNG
    src_png = _ensure_png_bytes(raw)

    # Background removal
    try:
        out = remove(src_png, session=SESSION)
    except Exception as e:
        log.exception("remove() failed")
        error_detail = {
            "error": "clean failed",
            "exception": str(e),
            "exception_type": e.__class__.__name__,
        }
        if DEBUG:
            return JSONResponse(status_code=502, content=error_detail)
        raise HTTPException(status_code=502, detail=error_detail)

    etag = hashlib.sha256(src_png).hexdigest()
    headers = {"ETag": etag, "Cache-Control": "public, max-age=31536000, immutable"}
    if return_mode == "json":
        encoded = base64.b64encode(out).decode("ascii")
        log.info("clean: returning json payload for source=%s", source)
        return {"ok": True, "source": source, "etag": etag, "png_b64": encoded}
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


@app.post("/debug-echo")
async def debug_echo(request: Request, file: UploadFile = File(...)):
    token = request.headers.get("x-cleaner-token") or request.headers.get("X-Cleaner-Token")
    if not token or token != os.environ.get("CLEANER_TOKEN"):
        log.warning("debug-echo: invalid token supplied")
        raise HTTPException(status_code=401, detail={"error": "invalid token"})
    raw = await file.read()
    size = len(raw)
    log.info("debug-echo: filename=%s content_type=%s size=%d", file.filename, file.content_type, size)
    return {"filename": file.filename, "content_type": file.content_type, "size": size}
