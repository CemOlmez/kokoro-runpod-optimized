import json
import logging
import time
from dataclasses import dataclass

import numpy as np
import torch
from kokoro import KPipeline

from app.config import Settings
from app.text_splitter import normalize_text, split_text


class TTSError(Exception):
    pass


@dataclass
class SynthesisResult:
    audio: np.ndarray
    sample_rate: int
    chunk_count: int


class TTSService:
    def __init__(self, settings: Settings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger
        self.pipeline: KPipeline | None = None
        self.loaded = False
        self.supported_voices: list[str] = list(settings.supported_voices)

    def load(self) -> int:
        startup_t0 = time.perf_counter()
        requested_device = self.settings.kokoro_device
        cuda_available = torch.cuda.is_available()
        resolved_device = requested_device

        if requested_device == "auto":
            resolved_device = "cuda" if cuda_available else "cpu"
        elif requested_device == "cuda" and not cuda_available:
            if self.settings.allow_cpu_fallback:
                resolved_device = "cpu"
                self._log(
                    "cuda_unavailable_fallback_to_cpu",
                    requested_device=requested_device,
                    resolved_device=resolved_device,
                )
            else:
                raise RuntimeError("KOKORO_DEVICE=cuda but CUDA is not available")

        self._log(
            "startup_begin",
            repo_id=self.settings.kokoro_repo_id,
            requested_device=requested_device,
            resolved_device=resolved_device,
            lang_code=self.settings.model_lang,
        )

        model_t0 = time.perf_counter()
        self.pipeline = KPipeline(
            lang_code=self.settings.model_lang,
            repo_id=self.settings.kokoro_repo_id,
            device=resolved_device,
        )
        model_ms = int((time.perf_counter() - model_t0) * 1000)

        self.loaded = True
        total_ms = int((time.perf_counter() - startup_t0) * 1000)
        self._log(
            "startup_complete",
            startup_ms=total_ms,
            model_init_ms=model_ms,
            voice_count=len(self.supported_voices),
            sample_rate=self.settings.sample_rate,
            cuda_available=torch.cuda.is_available(),
        )
        return total_ms

    def synthesize(
        self,
        text: str,
        voice: str,
        speed: float,
        split_long_text: bool,
        max_chars_per_chunk: int,
        request_id: str,
    ) -> SynthesisResult:
        if not self.loaded or not self.pipeline:
            raise TTSError("Model not initialized")

        normalized = normalize_text(text)
        if not normalized:
            raise TTSError("Text cannot be empty")

        resolved_voice = self.resolve_voice(voice)

        preprocess_t0 = time.perf_counter()
        self._log("text_preprocess_start", request_id=request_id)
        chunks = self._chunk_text(normalized, split_long_text, max_chars_per_chunk)
        self._log(
            "text_preprocess_end",
            request_id=request_id,
            preprocess_ms=int((time.perf_counter() - preprocess_t0) * 1000),
            chunk_count=len(chunks),
            chars=len(normalized),
        )

        audio_chunks: list[np.ndarray] = []
        inference_t0 = time.perf_counter()
        self._log("inference_start", request_id=request_id, chunk_count=len(chunks), voice=resolved_voice)

        for idx, chunk in enumerate(chunks, start=1):
            chunk_start = time.perf_counter()
            try:
                produced_any = False
                for result in self.pipeline(
                    chunk,
                    voice=resolved_voice,
                    speed=speed,
                    split_pattern=None,
                ):
                    produced_any = True
                    chunk_audio = np.asarray(result.audio, dtype=np.float32)
                    if chunk_audio.ndim != 1:
                        chunk_audio = chunk_audio.flatten()
                    audio_chunks.append(chunk_audio)

                if not produced_any:
                    raise TTSError(f"No audio generated for chunk {idx}")
            except TTSError:
                raise
            except Exception as exc:  # pragma: no cover
                raise TTSError(f"Inference failed at chunk {idx}: {exc}") from exc

            self._log(
                "inference_chunk_done",
                request_id=request_id,
                chunk_index=idx,
                chunk_ms=int((time.perf_counter() - chunk_start) * 1000),
            )

        self._log(
            "inference_end",
            request_id=request_id,
            inference_ms=int((time.perf_counter() - inference_t0) * 1000),
        )

        merged = np.concatenate(audio_chunks) if audio_chunks else np.array([], dtype=np.float32)
        return SynthesisResult(audio=merged, sample_rate=self.settings.sample_rate, chunk_count=len(chunks))

    def resolve_voice(self, requested_voice: str) -> str:
        candidate = self.settings.default_voice if requested_voice == "default" else requested_voice

        if self.supported_voices and candidate not in self.supported_voices:
            voices = ", ".join(self.supported_voices)
            raise TTSError(f"Unsupported voice '{candidate}'. Supported voices: {voices}")

        return candidate

    def _chunk_text(self, text: str, split_enabled: bool, max_chars_per_chunk: int) -> list[str]:
        if not split_enabled:
            return [text]
        return split_text(text, max_chars_per_chunk)

    def _log(self, event: str, **fields: object) -> None:
        payload = {"event": event, **fields}
        self.logger.info(json.dumps(payload, ensure_ascii=True))
