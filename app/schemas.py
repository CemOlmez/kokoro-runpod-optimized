from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TTSRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    text: str = Field(min_length=1, max_length=4000)
    voice: str = Field(default="default", min_length=1, max_length=64)
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    format: str = Field(default="wav")
    split_long_text: bool = True
    max_chars_per_chunk: int = Field(default=180, ge=50, le=1000)

    @field_validator("format")
    @classmethod
    def validate_format(cls, value: str) -> str:
        normalized = value.lower()
        if normalized != "wav":
            raise ValueError("Only 'wav' format is supported")
        return normalized


class JSONTTSResponse(BaseModel):
    success: bool = True
    format: str = "wav"
    sample_rate: int
    processing_ms: int
    chunk_count: int
    audio_base64: str


class MetaResponse(BaseModel):
    app_name: str
    version: str
    loaded: bool
    initializing: bool
    sample_rate: int
    response_mode: str
    supported_formats: list[str]
    supported_voices: list[str]
    default_voice: str
    default_speed: float
    model_ref: str


class ErrorResponse(BaseModel):
    success: bool = False
    error: str
    detail: Optional[str] = None
    request_id: Optional[str] = None
