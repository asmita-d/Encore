"""Local HTTP adapter for the shared Encore service."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Query, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.service import EncoreService, ServiceError


class ArtistInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)


def create_app(database=None):
    @asynccontextmanager
    async def lifespan(app):
        app.state.service = EncoreService(database)
        yield

    app = FastAPI(title="Encore", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(ServiceError)
    async def service_error(request, error):
        return JSONResponse(status_code=error.status_code, content={"detail": error.detail})

    @app.get("/health")
    def health():
        return app.state.service.health()

    @app.get("/artists")
    def artists():
        return app.state.service.artists()

    @app.post("/artists", status_code=201)
    def add_artist(artist: ArtistInput):
        return app.state.service.add_artist(artist.name)

    @app.delete("/artists/{artist_id}", status_code=204)
    def delete_artist(artist_id: int):
        app.state.service.delete_artist(artist_id)
        return Response(status_code=204)

    @app.get("/events")
    def events(artist: str | None = Query(default=None, description="Exact artist name, ignoring case"),
               status: str | None = Query(default=None, description="Provider status code, ignoring case")):
        return app.state.service.events(artist, status)

    @app.post("/refresh")
    def refresh():
        return app.state.service.refresh()

    return app


app = create_app()
