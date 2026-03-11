# Kokoro TTS for Runpod Serverless (Load Balancing Endpoint)

Production-ready FastAPI service that runs Kokoro TTS with **PyTorch + CUDA** for **Runpod Serverless Load Balancing** endpoints (real-time HTTP), optimized for **FLEX-only workers**.

## Why this architecture
- Uses standard HTTP (`FastAPI + Uvicorn`) so Runpod LB can route synchronous requests directly.
- Avoids queue-based `/run` and `/runsync` patterns.
- Uses PyTorch CUDA inference (not ONNX) with startup preload.
- Supports flex-only economics (`active workers = 0`) while minimizing cold-start pain by loading once per worker startup.

## Project structure
- `app/main.py` API routes, lifecycle startup preload, error handling, logging
- `app/config.py` environment-driven settings
- `app/schemas.py` request/response models and strict validation
- `app/tts_service.py` Kokoro PyTorch pipeline loading + synthesis + chunking path
- `app/text_splitter.py` deterministic sentence-aware chunking
- `app/audio_utils.py` WAV encoding/base64 helpers
- `app/health_state.py` readiness state
- `app/health_server.py` optional separate health port sidecar
- `requirements.txt`
- `Dockerfile`
- `.env.example`

## API
### `GET /ping`
- Returns `204` while initializing
- Returns `200` when ready
- Returns `503` on startup failure

### `GET /meta`
Returns model and service metadata:
- loaded state
- sample rate
- response mode
- supported voices (if configured)

### `POST /tts`
Request body:
```json
{
  "text": "Merhaba dünya",
  "voice": "default",
  "speed": 1.0,
  "format": "wav",
  "split_long_text": true,
  "max_chars_per_chunk": 180
}
```

Response mode is controlled with `RESPONSE_MODE`:
- `binary`: raw `audio/wav`
- `json_base64`: JSON payload with `audio_base64`

Headers:
- `X-Request-ID`
- `X-Processing-Time`
- `X-Chunk-Count`
- `X-Voice`

## Validation rules
- Empty text rejected (`422`)
- Max text length enforced by `MAX_TEXT_CHARS` (`413`)
- Speed range `0.5..2.0`
- Format must be `wav`
- Voice validated when `SUPPORTED_VOICES` is configured
- Unknown fields rejected

## Observability
Structured JSON logs include:
- startup phases and timings
- request received
- text preprocessing start/end
- inference start/end and per chunk timings
- audio encoding start/end
- request complete total time
- request timeouts and failures

## Local run
1. Create environment and install:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Set environment:
```bash
cp .env.example .env
set -a; source .env; set +a
```

3. Run server:
```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
```

## Docker build and run
```bash
docker build -t kokoro-runpod-tts:latest .
```

```bash
docker run --gpus all --rm -p 8000:8000 --env-file .env kokoro-runpod-tts:latest
```

## Runpod deployment (Load Balancing endpoint)
1. Push this image to your container registry.
2. In Runpod, create a **Serverless Endpoint** configured for **Load Balancing** (HTTP), not queue handler mode.
3. Set worker type to **FLEX** only.
4. Set `active workers = 0`.
5. Configure container image to your pushed image.
6. Set exposed container port to `8000` (or your `PORT`).
7. Set health path to `/ping`.
8. Add environment variables from `.env.example`.
9. Deploy endpoint.
10. Verify readiness by polling `/ping` until `200`.
11. Send `POST /tts` requests through the endpoint URL.

## Required Runpod endpoint settings
- Endpoint type: `Serverless Load Balancing`
- Worker mode: `FLEX only`
- Active workers: `0`
- Exposed port: `PORT` value (default `8000`)
- Health check path: `/ping`
- Health check port: same as exposed port, or `PORT_HEALTH` if split mode is enabled

## Environment variables
- `PORT` server port for FastAPI
- `PORT_HEALTH` optional separate health server port (if different from `PORT`)
- `LOG_LEVEL` `DEBUG|INFO|WARNING|ERROR`
- `RESPONSE_MODE` `binary|json_base64`
- `DEFAULT_VOICE` fallback for `voice=default`
- `DEFAULT_SPEED` fallback speed
- `ENABLE_TEXT_SPLITTING` `true|false`
- `MAX_CHARS_PER_CHUNK` upper bound for chunking
- `SAMPLE_RATE` output sample rate metadata
- `MODEL_LANG` Kokoro language code (default `en-us`)
- `KOKORO_REPO_ID` model repo ID (default `hexgrad/Kokoro-82M`)
- `KOKORO_DEVICE` `auto|cuda|cpu` (`auto` picks CUDA when available)
- `ALLOW_CPU_FALLBACK` when `true`, falls back to CPU if CUDA was requested but unavailable
- `SUPPORTED_VOICES` optional CSV allowlist for strict voice validation
- `MAX_TEXT_CHARS` hard request text limit
- `REQUEST_TIMEOUT_SECONDS` timeout guard for each request

## Exposed ports
- Main API: `PORT` (default `8000`)
- Optional health sidecar: `PORT_HEALTH` when different from `PORT`

## Health behavior
- During cold start and model preload: `/ping -> 204`
- Ready: `/ping -> 200`
- Startup failure: `/ping -> 503`

## Example curl tests
### Health
```bash
curl -i http://127.0.0.1:8000/ping
```

### Metadata
```bash
curl -s http://127.0.0.1:8000/meta | jq .
```

### TTS binary response
```bash
curl -sS -X POST http://127.0.0.1:8000/tts \
  -H 'Content-Type: application/json' \
  -d '{"text":"Merhaba dunya","voice":"default","speed":1.0,"format":"wav","split_long_text":true,"max_chars_per_chunk":180}' \
  --output output.wav -D -
```

### TTS JSON base64 response
```bash
curl -sS -X POST http://127.0.0.1:8000/tts \
  -H 'Content-Type: application/json' \
  -d '{"text":"Merhaba dunya","voice":"default","speed":1.0,"format":"wav","split_long_text":true,"max_chars_per_chunk":180}'
```
(Use `RESPONSE_MODE=json_base64` for this mode.)

## Troubleshooting
### Worker never becomes healthy
- Check logs for `worker_startup_failed`.
- Confirm `KOKORO_DEVICE=cuda` and GPU worker type is used.
- Ensure model repo (`KOKORO_REPO_ID`) is reachable from worker.

### Port mismatch
- Ensure Runpod exposed port equals `PORT`.
- If using separate health port, set routing health port to `PORT_HEALTH`.

### Model load failure
- Check logs for CUDA availability mismatch.
- Verify PyTorch CUDA runtime matches worker GPU environment.

### Timeout
- Reduce `MAX_TEXT_CHARS`.
- Enable splitting and lower `MAX_CHARS_PER_CHUNK`.
- Increase `REQUEST_TIMEOUT_SECONDS` carefully.

### Empty audio
- Validate non-empty normalized text.
- Try another voice.
- Check inference logs for chunk failures.

### Cold start latency
- Keep image lean and dependency set fixed.
- Tune max workers and idle timeout for expected traffic bursts.

## Flex-only cold start tradeoffs
Flex-only lowers baseline cost but introduces sporadic cold starts. This project minimizes impact by loading model assets once at startup, exposing clear readiness signals (`/ping`), and avoiding per-request initialization.

## Recommended initial Runpod settings
- `active workers`: `0`
- `max workers`: `3`
- `idle timeout`: `10s`
- `exposed port`: `8000`
- `health path`: `/ping`
- Request testing:
1. `GET /ping` until `200`
2. `GET /meta` to verify loaded state
3. `POST /tts` short text first, then longer text with chunking enabled

## Future optimization options
1. Keep one tiny warm-up request after cold boot to prime CUDA kernels.
2. Use shorter text chunks for tighter tail latency.
3. Benchmark voices and pick a low-latency default.
4. Tune worker `max workers` based on observed p95 latency.
5. Add streaming output mode for long-form text.
6. Add autoscaling policies tuned to burst patterns.
