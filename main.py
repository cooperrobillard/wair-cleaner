from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.responses import Response
import httpx
from rembg import remove
import hashlib

app = FastAPI(title="wair-cleaner", version="1.0.0")

@app.get("/healthz")
async def healthz():
    return {"ok": True}

@app.api_route("/clean", methods=["GET","POST"])
async def clean(
    file: UploadFile | None = File(default=None),
    image_url: str | None = Query(default=None, description="URL to an image")
):
    if not file and not image_url:
        raise HTTPException(status_code=400, detail="Provide either a file or image_url")
    if file and image_url:
        raise HTTPException(status_code=400, detail="Provide only one of file or image_url")

    if image_url:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            r = await client.get(image_url)
            r.raise_for_status()
            raw = r.content
    else:
        raw = await file.read()

    out = remove(raw)

    etag = hashlib.sha256(raw).hexdigest()
    headers = {"ETag": etag, "Cache-Control": "public, max-age=31536000, immutable"}

    return Response(content=out, media_type="image/png", headers=headers)
