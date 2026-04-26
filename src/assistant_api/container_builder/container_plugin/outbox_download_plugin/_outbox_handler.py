import os
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

CONTAINER_PATH = os.environ["OUTBOX_CONTAINER_PATH"]
LIST_ENDPOINT_PATH = os.environ["OUTBOX_LIST_ENDPOINT_PATH"]
DOWNLOAD_ENDPOINT_PATH = os.environ["OUTBOX_DOWNLOAD_ENDPOINT_PATH"]
PORT = int(os.environ["OUTBOX_PORT"])

_OUTBOX_DIR = os.path.join(CONTAINER_PATH, "outbox")

app = FastAPI()


def _is_safe_filename(filename: str) -> bool:
    if not filename:
        return False
    if filename.startswith("/"):
        return False
    if ".." in filename:
        return False
    return True


@app.get(LIST_ENDPOINT_PATH)
async def list_files():
    try:
        entries = os.listdir(_OUTBOX_DIR)
    except FileNotFoundError:
        entries = []
    files = [e for e in entries if os.path.isfile(os.path.join(_OUTBOX_DIR, e))]
    return JSONResponse(content=files)


@app.get(f"{DOWNLOAD_ENDPOINT_PATH}/{{filename:path}}")
async def download_file(filename: str):
    if not _is_safe_filename(filename):
        return JSONResponse(
            status_code=400,
            content={"detail": "unsafe filename"},
        )

    file_path = os.path.join(_OUTBOX_DIR, filename)

    if not os.path.isfile(file_path):
        return JSONResponse(
            status_code=404,
            content={"detail": "file not found"},
        )

    try:
        return FileResponse(
            path=file_path,
            filename=filename,
            background=_remove_file_after_response(file_path),
        )
    except Exception:
        return JSONResponse(
            status_code=404,
            content={"detail": "file not found"},
        )


class _RemoveFileBackgroundTask:
    def __init__(self, file_path: str) -> None:
        self._file_path = file_path

    def __call__(self) -> None:
        try:
            os.unlink(self._file_path)
        except FileNotFoundError:
            pass


def _remove_file_after_response(file_path: str) -> _RemoveFileBackgroundTask:
    return _RemoveFileBackgroundTask(file_path)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT)
