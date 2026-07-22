from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from main.agent.deadline import RunDeadlineExceeded


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def validation(_: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"ok":False,"error":"validation_error","message":"Request validation failed.","details":exc.errors()})
    @app.exception_handler(RunDeadlineExceeded)
    async def deadline(_: Request, exc: RunDeadlineExceeded):
        return JSONResponse(status_code=504, content={"ok":False,"error":"deadline_exceeded","message":str(exc)})
    @app.exception_handler(FileNotFoundError)
    async def missing(_: Request, exc: FileNotFoundError):
        return JSONResponse(status_code=404, content={"ok":False,"error":"not_found","message":str(exc)})
    @app.exception_handler(ValueError)
    async def invalid(_: Request, exc: ValueError):
        return JSONResponse(status_code=400, content={"ok":False,"error":"invalid_request","message":str(exc)})
    @app.exception_handler(Exception)
    async def unexpected(_: Request, exc: Exception):
        return JSONResponse(status_code=500, content={"ok":False,"error":"internal_error","message":"Internal server error."})
