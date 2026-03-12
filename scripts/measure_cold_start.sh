#!/usr/bin/env bash
set -euo pipefail

# Measure cold-start penalty on Runpod Load Balancer endpoints.
# Methodology (important):
#   For each cycle:
#     1) Sleep longer than idle timeout to encourage scale-to-zero
#     2) Send first /tts request => measured as COLD
#     3) Send second immediate /tts request => measured as WARM
# This avoids probing /ping before cold request (which would pre-warm and skew results).

BASE_URL=""
IDLE_TIMEOUT=5
CYCLES=5
SLEEP_BUFFER=2
CONNECT_TIMEOUT=10
MAX_TIME=180
TEXT="Merhaba dunya, bu bir cold start olcum testidir."
VOICE="default"
SPEED="1.0"
FORMAT="wav"
SPLIT_LONG_TEXT="true"
MAX_CHARS_PER_CHUNK=180
OUT_CSV="./results/runpod_cold_start_metrics.csv"
SHOW_PING_AFTER_COLD="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base-url)
      BASE_URL="$2"; shift 2 ;;
    --idle-timeout)
      IDLE_TIMEOUT="$2"; shift 2 ;;
    --cycles)
      CYCLES="$2"; shift 2 ;;
    --sleep-buffer)
      SLEEP_BUFFER="$2"; shift 2 ;;
    --text)
      TEXT="$2"; shift 2 ;;
    --out-csv)
      OUT_CSV="$2"; shift 2 ;;
    --show-ping-after-cold)
      SHOW_PING_AFTER_COLD="true"; shift 1 ;;
    *)
      echo "Unknown arg: $1" >&2
      exit 1 ;;
  esac
done

if [[ -z "$BASE_URL" ]]; then
  echo "ERROR: --base-url is required" >&2
  exit 1
fi

if [[ -z "${RUNPOD_API_KEY:-}" ]]; then
  echo "ERROR: RUNPOD_API_KEY is required" >&2
  exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "ERROR: jq is required" >&2
  exit 1
fi

mkdir -p "$(dirname "$OUT_CSV")"

AUTH_HEADER="Authorization: Bearer ${RUNPOD_API_KEY}"
JSON_HEADER="Content-Type: application/json"

echo "cycle,phase,http_code,total_s,time_starttransfer_s,size_download_bytes" > "$OUT_CSV"

tts_body() {
  jq -nc \
    --arg text "$TEXT" \
    --arg voice "$VOICE" \
    --argjson speed "$SPEED" \
    --arg format "$FORMAT" \
    --argjson split_long_text "$SPLIT_LONG_TEXT" \
    --argjson max_chars_per_chunk "$MAX_CHARS_PER_CHUNK" \
    '{text:$text,voice:$voice,speed:$speed,format:$format,split_long_text:$split_long_text,max_chars_per_chunk:$max_chars_per_chunk}'
}

probe_ping() {
  curl -sS -o /dev/null \
    --connect-timeout "$CONNECT_TIMEOUT" \
    --max-time "$MAX_TIME" \
    -H "$AUTH_HEADER" \
    -w "%{http_code},%{time_total},%{time_starttransfer},%{size_download}" \
    "$BASE_URL/ping"
}

fail_auth_hint() {
  cat >&2 <<'EOF'
ERROR: Received HTTP 401 (unauthorized).
Check these:
1) RUNPOD_API_KEY is a real key, not a placeholder
2) Key is valid and not expired/revoked
3) Endpoint requires bearer auth and request includes Authorization header
EOF
}

measure_tts() {
  local cycle="$1"
  local phase="$2"
  local tmpfile
  tmpfile=$(mktemp)

  local metrics
  metrics=$(curl -sS -o "$tmpfile" \
    --connect-timeout "$CONNECT_TIMEOUT" \
    --max-time "$MAX_TIME" \
    -H "$AUTH_HEADER" \
    -H "$JSON_HEADER" \
    -X POST "$BASE_URL/tts" \
    --data "$(tts_body)" \
    -w "%{http_code},%{time_total},%{time_starttransfer},%{size_download}")

  local code total ttfb size
  code=$(echo "$metrics" | cut -d',' -f1)
  total=$(echo "$metrics" | cut -d',' -f2)
  ttfb=$(echo "$metrics" | cut -d',' -f3)
  size=$(echo "$metrics" | cut -d',' -f4)

  if [[ "$code" == "401" ]]; then
    fail_auth_hint
    if [[ -s "$tmpfile" ]]; then
      echo "Response:" >&2
      cat "$tmpfile" >&2
      echo >&2
    fi
    rm -f "$tmpfile"
    exit 1
  fi

  if [[ "$code" != "200" ]]; then
    echo "[$phase][$cycle] /tts failed, http=$code" >&2
    if [[ -s "$tmpfile" ]]; then
      echo "Response:" >&2
      cat "$tmpfile" >&2
      echo >&2
    fi
  fi

  echo "$cycle,$phase,$metrics" >> "$OUT_CSV"
  printf "[%s][%s] code=%s total=%ss ttfb=%ss bytes=%s\n" "$phase" "$cycle" "$code" "$total" "$ttfb" "$size"
  rm -f "$tmpfile"
}

printf "\nEndpoint: %s\nIdle timeout: %ss | Cycles: %s | Sleep buffer: %ss\n\n" \
  "$BASE_URL" "$IDLE_TIMEOUT" "$CYCLES" "$SLEEP_BUFFER"

for i in $(seq 1 "$CYCLES"); do
  echo "--- Cycle $i/$CYCLES ---"

  sleep_seconds=$((IDLE_TIMEOUT + SLEEP_BUFFER))
  echo "Sleeping ${sleep_seconds}s before cold probe..."
  sleep "$sleep_seconds"

  # First request after idle window: cold candidate
  measure_tts "$i" "cold"

  if [[ "$SHOW_PING_AFTER_COLD" == "true" ]]; then
    ping_metrics=$(probe_ping)
    ping_code=$(echo "$ping_metrics" | cut -d',' -f1)
    if [[ "$ping_code" == "401" ]]; then
      fail_auth_hint
      exit 1
    fi
    echo "ping metrics after cold request: ${ping_metrics}"
  fi

  # Immediate follow-up request: warm baseline
  measure_tts "$i" "warm"

done

awk -F',' '
NR==1 {next}
$2=="warm" {w_n++; w_total+=$4; w_ttfb+=$5}
$2=="cold" {c_n++; c_total+=$4; c_ttfb+=$5}
END {
  if (w_n>0) {
    printf("\nWarm avg total: %.3fs | avg TTFB: %.3fs (n=%d)\n", w_total/w_n, w_ttfb/w_n, w_n)
  }
  if (c_n>0) {
    printf("Cold avg total: %.3fs | avg TTFB: %.3fs (n=%d)\n", c_total/c_n, c_ttfb/c_n, c_n)
  }
  if (w_n>0 && c_n>0) {
    printf("Cold penalty (avg total): %.3fx\n", (c_total/c_n)/(w_total/w_n))
  }
}
' "$OUT_CSV"

echo "\nSaved CSV: $OUT_CSV"
