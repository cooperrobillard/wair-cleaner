from fastapi import FastAPI, UploadFile, File, HTTPException, Header, Form, Request
from fastapi.responses import Response
import httpx, hashlib, os
from rembg import remove

app = FastAPI()
TOKEN = os.getenv("CLEANER_TOKEN", "")

@app.get("/healthz")
def healthz():
    return {"ok": True}

# Accept both GET (query) and POST (form or multipart)
@app.api_route("/clean", methods=["GET", "POST"])
async def clean(
    request: Request,
    image_url: str | None = Form(default=None),      # binds x-www-form-urlencoded
    file: UploadFile | None = File(default=None),    # binds multipart file
    x_cleaner_token: str | None = Header(default=None),
):
    if TOKEN and x_cleaner_token != TOKEN:
        raise HTTPException(status_code=401, detail="unauthorized")

    # If not provided as form, allow query param (GET /clean?image_url=...)
    if not image_url:
        image_url = request.query_params.get("image_url")

    if not image_url and file is None:
        raise HTTPException(status_code=400, detail="provide image_url or file")

    # Fetch bytes (from URL or uploaded file)
    if image_url:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            r = await client.get(image_url)
            r.raise_for_status()
            raw = r.content
    else:
        raw = await file.read()

    # Run rembg (returns PNG bytes with alpha)
    out = remove(raw)

    # Cache hints
    etag = hashlib.sha256(raw).hexdigest()
    headers = {"ETag": etag, "Cache-Control": "public, max-age=31536000, immutable"}

    return Response(content=out, media_type="image/png", headers=headers)
