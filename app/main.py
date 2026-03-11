import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response

from app.audio_utils import encode_wav_bytes, to_base64
from app.config import get_settings
from app.health_server import start_health_server, stop_health_server
from app.health_state import health_state
from app.schemas import ErrorResponse, JSONTTSResponse, MetaResponse, TTSRequest
from app.tts_service import TTSError, TTSService

settings = get_settings()


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("kokoro_api")
    logger.setLevel(settings.log_level)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.handlers = [handler]
    logger.propagate = False
    return logger


logger = configure_logging()
service = TTSService(settings=settings, logger=logger)


def log_event(event: str, **fields: object) -> None:
    logger.info(json.dumps({"event": event, **fields}, ensure_ascii=True))


@asynccontextmanager
async def lifespan(_: FastAPI):
    startup_begin = time.perf_counter()
    log_event(
        "app_lifespan_start",
        app_name=settings.app_name,
        version=settings.app_version,
        pid=os.getpid(),
        host=settings.host,
        port=settings.port,
        response_mode=settings.response_mode,
    )

    try:
        if settings.port_health != settings.port:
            started = start_health_server(settings.host, settings.port_health)
            if started:
                log_event("health_sidecar_started", host=settings.host, port_health=settings.port_health)

        startup_ms = await asyncio.to_thread(service.load)
        health_state.set_ready(startup_ms)
        log_event("worker_ready", startup_ms=startup_ms)
    except Exception as exc:  # pragma: no cover
        health_state.set_failed(str(exc))
        log_event(
            "worker_startup_failed",
            error=str(exc),
            startup_ms=int((time.perf_counter() - startup_begin) * 1000),
        )
        raise

    yield
    if settings.port_health != settings.port:
        stop_health_server()


app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    return JSONResponse(
        status_code=422,
        content=ErrorResponse(
            error="validation_error",
            detail=str(exc),
            request_id=request_id,
        ).model_dump(),
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(
            error="http_error",
            detail=str(exc.detail),
            request_id=request_id,
        ).model_dump(),
    )


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    request.state.request_id = request_id
    start = time.perf_counter()

    try:
        response = await call_next(request)
    except Exception:
        log_event("request_failed_unhandled", request_id=request_id, path=request.url.path)
        raise

    duration_ms = int((time.perf_counter() - start) * 1000)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Processing-Time"] = str(duration_ms)
    return response


@app.get("/ping")
async def ping() -> Response:
    if health_state.initializing:
        return Response(status_code=204)
    if health_state.ready:
        return Response(status_code=200)
    return Response(status_code=503)


@app.get("/meta", response_model=MetaResponse)
async def meta() -> MetaResponse:
    return MetaResponse(
        app_name=settings.app_name,
        version=settings.app_version,
        loaded=service.loaded,
        initializing=health_state.initializing,
        sample_rate=settings.sample_rate,
        response_mode=settings.response_mode,
        supported_formats=["wav"],
        supported_voices=service.supported_voices,
        default_voice=settings.default_voice,
        default_speed=settings.default_speed,
        model_ref=settings.kokoro_repo_id,
    )


@app.post("/tts")
async def tts(request: Request, payload: TTSRequest):
    request_id = request.state.request_id
    req_t0 = time.perf_counter()
    log_event("request_received", request_id=request_id, path="/tts")

    if not service.loaded or not health_state.ready:
        raise HTTPException(status_code=503, detail="Service initializing")

    if len(payload.text.strip()) > settings.max_text_chars:
        raise HTTPException(
            status_code=413,
            detail=f"Text too long. Max allowed characters: {settings.max_text_chars}",
        )

    if payload.voice != "default" and service.supported_voices and payload.voice not in service.supported_voices:
        raise HTTPException(status_code=422, detail=f"Unsupported voice '{payload.voice}'")

    split_enabled = settings.enable_text_splitting and payload.split_long_text
    max_chars = min(payload.max_chars_per_chunk, settings.max_chars_per_chunk)

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(
                service.synthesize,
                payload.text,
                payload.voice,
                payload.speed,
                split_enabled,
                max_chars,
                request_id,
            ),
            timeout=settings.request_timeout_seconds,
        )
    except asyncio.TimeoutError:
        log_event("request_timeout", request_id=request_id)
        raise HTTPException(status_code=504, detail="Inference timeout")
    except TTSError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    encode_t0 = time.perf_counter()
    log_event("audio_encoding_start", request_id=request_id)
    wav_bytes = await asyncio.to_thread(encode_wav_bytes, result.audio, result.sample_rate)
    encode_ms = int((time.perf_counter() - encode_t0) * 1000)
    log_event("audio_encoding_end", request_id=request_id, encode_ms=encode_ms, bytes=len(wav_bytes))

    total_ms = int((time.perf_counter() - req_t0) * 1000)
    log_event(
        "request_complete",
        request_id=request_id,
        total_ms=total_ms,
        chunk_count=result.chunk_count,
        voice=payload.voice,
    )

    if settings.response_mode == "json_base64":
        body = JSONTTSResponse(
            success=True,
            format="wav",
            sample_rate=result.sample_rate,
            processing_ms=total_ms,
            chunk_count=result.chunk_count,
            audio_base64=to_base64(wav_bytes),
        )
        return JSONResponse(
            status_code=200,
            content=body.model_dump(),
            headers={
                "X-Chunk-Count": str(result.chunk_count),
                "X-Voice": payload.voice,
                "X-Processing-Time": str(total_ms),
            },
        )

    return Response(
        content=wav_bytes,
        media_type="audio/wav",
        headers={
            "X-Chunk-Count": str(result.chunk_count),
            "X-Voice": payload.voice,
            "X-Processing-Time": str(total_ms),
        },
        status_code=200,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, log_level=settings.log_level.lower())
