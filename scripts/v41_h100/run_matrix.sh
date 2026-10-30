#!/usr/bin/env bash
# Per-workload x per-concurrency benchmark matrix against one OpenAI-compatible endpoint.
# Runs the same matrix for any engine (SGLang with or without speculation, DeepInfer), so results compare.
#
#   BASE_URL=http://127.0.0.1:30000/v1 MODEL=deepseek-ai/DeepSeek-V4.1-Flash \
#   METRICS_URL=http://127.0.0.1:30000/metrics OUT_DIR=results/x1 LABEL=sglang_dspark \
#   CONCURRENCY="1 4" WORKLOADS="code json maths chat_en chat_th" scripts/v41_h100/run_matrix.sh
#
# Optional: ENGINE_LOG_CMD='kubectl logs -n "$NS" "$POD" -c "$CONTAINER" --since-time=$T0'
#   If set, the engine log for each run's time window is saved next to the result and parsed.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
: "${BASE_URL:?set BASE_URL}" "${MODEL:?set MODEL}"
OUT_DIR="${OUT_DIR:-results/matrix}"; LABEL="${LABEL:-engine}"
CONCURRENCY="${CONCURRENCY:-1 4}"; WORKLOADS="${WORKLOADS:-code json maths chat_en chat_th}"
MAX_TOKENS="${MAX_TOKENS:-1024}"
mkdir -p "$OUT_DIR"
for w in $WORKLOADS; do
  for c in $CONCURRENCY; do
    n=$(( c * 4 > 8 ? c * 4 : 8 ))
    tag="${LABEL}_${w}_c${c}"
    T0=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    python3 "$HERE/stream_bench.py" --base-url "$BASE_URL" --model "$MODEL" \
      --prompts "$HERE/workloads/$w.jsonl" -c "$c" -n "$n" --max-tokens "$MAX_TOKENS" \
      ${METRICS_URL:+--metrics-url "$METRICS_URL"} --tag "$tag" --out-dir "$OUT_DIR" >/dev/null
    if [[ -n "${ENGINE_LOG_CMD:-}" ]]; then
      T0="$T0" bash -c "$ENGINE_LOG_CMD" > "$OUT_DIR/$tag.engine.log" 2>/dev/null || true
    fi
    echo "done $tag"
  done
done
python3 - "$OUT_DIR" <<'EOF'
import json, pathlib, sys
rows = [json.loads(p.read_text()) for p in sorted(pathlib.Path(sys.argv[1]).glob("*.summary.json"))]
f = lambda v, d=1: "-" if v is None else f"{v:.{d}f}"
print("| tag | c | per-user tok/s p50 | p10 | aggregate tok/s | TTFT p50 s | TTFT p95 s | queue before |")
print("| --- | --- | --- | --- | --- | --- | --- | --- |")
for r in rows:
    q = r.get("queue_before") or {}
    qs = ",".join(f"{k.split(':')[-1]}={v:g}" for k, v in q.items() if isinstance(v, (int, float))) or "-"
    print(f"| {r['tag']} | {r['concurrency']} | {f(r['per_user_decode_tok_s_p50'])} | {f(r['per_user_decode_tok_s_p10'])} | "
          f"{f(r['aggregate_out_tok_s'])} | {f(r['ttft_s_p50'], 2)} | {f(r['ttft_s_p95'], 2)} | {qs} |")
EOF
if ls "$OUT_DIR"/*.engine.log >/dev/null 2>&1; then
  python3 "$HERE/parse_decode_log.py" "$OUT_DIR"/*.engine.log | tee "$OUT_DIR/engine_summary.txt"
fi
