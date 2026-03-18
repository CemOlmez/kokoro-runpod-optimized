import os
from dataclasses import dataclass
from functools import lru_cache


def _get_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    app_name: str
    app_version: str
    host: str
    port: int
    port_health: int
    log_level: str
    response_mode: str
    default_voice: str
    default_speed: float
    enable_text_splitting: bool
    max_chars_per_chunk: int
    sample_rate: int
    model_lang: str
    kokoro_repo_id: str
    kokoro_device: str
    allow_cpu_fallback: bool
    supported_voices: list[str]
    max_text_chars: int
    request_timeout_seconds: float


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    response_mode = os.getenv("RESPONSE_MODE", "binary").strip().lower()
    if response_mode not in {"binary", "json_base64"}:
        response_mode = "binary"

    return Settings(
        app_name=os.getenv("APP_NAME", "kokoro-runpod-tts"),
        app_version=os.getenv("APP_VERSION", "1.0.0"),
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        port_health=int(os.getenv("PORT_HEALTH", os.getenv("PORT", "8000"))),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        response_mode=response_mode,
        default_voice=os.getenv("DEFAULT_VOICE", "af_heart"),
        default_speed=float(os.getenv("DEFAULT_SPEED", "1.0")),
        enable_text_splitting=_get_bool("ENABLE_TEXT_SPLITTING", True),
        max_chars_per_chunk=int(os.getenv("MAX_CHARS_PER_CHUNK", "180")),
        sample_rate=int(os.getenv("SAMPLE_RATE", "24000")),
        model_lang=os.getenv("MODEL_LANG", "en-us"),
        kokoro_repo_id=os.getenv("KOKORO_REPO_ID", "hexgrad/Kokoro-82M"),
        kokoro_device=os.getenv("KOKORO_DEVICE", "cuda").strip().lower(),
        allow_cpu_fallback=_get_bool("ALLOW_CPU_FALLBACK", True),
        supported_voices=[v.strip() for v in os.getenv("SUPPORTED_VOICES", "").split(",") if v.strip()],
        max_text_chars=int(os.getenv("MAX_TEXT_CHARS", "4000")),
        request_timeout_seconds=float(os.getenv("REQUEST_TIMEOUT_SECONDS", "25")),
    )
