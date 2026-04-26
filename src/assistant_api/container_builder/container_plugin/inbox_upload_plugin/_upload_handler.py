import os
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse

CONTAINER_PATH = os.environ["INBOX_CONTAINER_PATH"]
ENDPOINT_PATH = os.environ["INBOX_ENDPOINT_PATH"]
PORT = int(os.environ["INBOX_PORT"])

app = FastAPI()


def _is_safe_filename(filename: str) -> bool:
    if not filename:
        return False
    if filename.startswith("/"):
        return False
    if ".." in filename:
        return False
    return True


@app.post(ENDPOINT_PATH)
async def upload_file(file: UploadFile = File(...)):
    if not _is_safe_filename(file.filename):
        return JSONResponse(status_code=400, content={"detail": "unsafe filename"})

    inbox_dir = os.path.join(CONTAINER_PATH, "inbox")
    os.makedirs(inbox_dir, exist_ok=True)
    file_path = os.path.join(inbox_dir, file.filename)
    content = await file.read()
    with open(file_path, "wb") as f:
        f.write(content)
    return {"path": file_path}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT)
