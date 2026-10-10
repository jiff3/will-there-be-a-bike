"""Optional single-origin static delivery for the managed deployment image."""

from pathlib import Path

from fastapi import FastAPI
from starlette.exceptions import HTTPException
from starlette.responses import FileResponse, Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope


class FrontendFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        # StaticFiles uses the host separator; URL policy must be portable.
        path = path.replace("\\", "/")
        if any(part.startswith(".") for part in Path(path).parts):
            raise HTTPException(status_code=404)
        try:
            response = await super().get_response(path, scope)
        except HTTPException as error:
            # Client routes may fall back; API errors and missing bundles may not.
            if error.status_code != 404 or scope["method"] not in {"GET", "HEAD"}:
                raise
            if path.split("/", 1)[0] in {"api", "assets", "docs", "openapi.json"}:
                raise
            if ".." in Path(path).parts or Path(path).suffix:
                raise
            response = FileResponse(Path(str(self.directory)) / "index.html")
        response.headers["Cache-Control"] = (
            "public, max-age=31536000, immutable" if path.startswith("assets/") else "no-cache"
        )
        return response


def install_frontend(application: FastAPI, directory: Path | None) -> None:
    if directory is None:
        return
    if not (directory / "index.html").is_file():
        raise RuntimeError("FRONTEND_DIST_PATH does not contain a built index.html")
    application.mount("/", FrontendFiles(directory=directory, html=True), name="frontend")
