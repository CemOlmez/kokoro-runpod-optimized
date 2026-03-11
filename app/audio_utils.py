import base64
import io

import numpy as np
import soundfile as sf


def concat_audio(chunks: list[np.ndarray]) -> np.ndarray:
    if not chunks:
        return np.array([], dtype=np.float32)
    if len(chunks) == 1:
        return chunks[0]
    return np.concatenate(chunks, axis=0)


def encode_wav_bytes(audio: np.ndarray, sample_rate: int) -> bytes:
    buffer = io.BytesIO()
    sf.write(buffer, audio, sample_rate, format="WAV", subtype="PCM_16")
    return buffer.getvalue()


def to_base64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")
