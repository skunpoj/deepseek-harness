#!/usr/bin/env python3
"""Per-user streaming benchmark for an OpenAI-compatible chat endpoint (SGLang, DeepInfer #147 server, ...).

Measures per request: time to first token (TTFT), decode tokens/s after the first token, inter-chunk gaps.
Optionally records the server's queue state before and after the run from a Prometheus /metrics URL, so
results taken while other traffic was running can be recognised.

Examples
  export BASE_URL=http://127.0.0.1:30000/v1 MODEL=deepseek-ai/DeepSeek-V4.1-Flash API_KEY=EMPTY
  python3 stream_bench.py -c 1 -n 8 --prompts workloads/code.jsonl --tag sglang_code_c1
  python3 stream_bench.py -c 8 -n 32 --prompts workloads/chat_en.jsonl --metrics-url http://127.0.0.1:30000/metrics

Notes
  * --ignore-eos forces exactly --max-tokens per request but lowers speculative-decoding acceptance; report it.
  * Speculative decoding streams tokens in bursts; use the per-request decode tok/s, not single chunk gaps.
"""
import argparse, asyncio, json, os, pathlib, re, statistics, time

import httpx

DEFAULT_PROMPTS = [
    "Explain how a central bank's policy rate transmits to mortgage rates, step by step.",
    "Write a Python function that parses ISO-8601 durations and include unit tests.",
    "Summarise the trade-offs between tensor parallelism and expert parallelism for MoE inference.",
    "Draft a 300-word memo on liquidity coverage ratio reporting for a mid-size bank.",
]

QUEUE_METRICS = re.compile(r"^(sglang:num_running_reqs|sglang:num_queue_reqs|sglang:token_usage)\{?[^ ]*\s+([0-9.eE+-]+)", re.M)


async def read_queue(client, url):
    if not url:
        return None
    try:
        r = await client.get(url, timeout=10)
        return {m.group(1): float(m.group(2)) for m in QUEUE_METRICS.finditer(r.text)}
    except Exception as exc:  # metrics are optional
        return {"error": str(exc)[:200]}


async def one(client, args, prompt):
    body = {
        "model": args.model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": args.max_tokens, "temperature": args.temperature, "stream": True,
        "stream_options": {"include_usage": True},
    }
    if args.ignore_eos:
        body["ignore_eos"] = True
    t0 = time.perf_counter(); first = last = None; gaps = []; usage = None
    async with client.stream("POST", f"{args.base_url}/chat/completions", json=body) as r:
        r.raise_for_status()
        async for line in r.aiter_lines():
            if not line.startswith("data: ") or line.endswith("[DONE]"):
                continue
            chunk = json.loads(line[6:])
            if chunk.get("usage"):
                usage = chunk["usage"]
            for ch in chunk.get("choices", []):
                d = ch.get("delta", {})
                if d.get("content") or d.get("reasoning_content"):
                    now = time.perf_counter()
                    if first is None:
                        first = now
                    elif last is not None:
                        gaps.append(now - last)
                    last = now
    end = time.perf_counter()
    out_tok = (usage or {}).get("completion_tokens") or 0
    decode_s = (end - first) if first else float("nan")
    return {
        "ttft_s": (first - t0) if first else None,
        "total_s": end - t0,
        "completion_tokens": out_tok,
        "decode_tok_s": (out_tok - 1) / decode_s if first and out_tok > 1 and decode_s > 0 else None,
        "p99_gap_ms": 1000 * sorted(gaps)[int(0.99 * (len(gaps) - 1))] if gaps else None,
    }


def pct(rows, key, q):
    v = sorted(x[key] for x in rows if x.get(key) is not None)
    return v[int(q * (len(v) - 1))] if v else None


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default=os.environ.get("BASE_URL", "http://127.0.0.1:30000/v1"))
    p.add_argument("--model", default=os.environ.get("MODEL", "deepseek-ai/DeepSeek-V4.1-Flash"))
    p.add_argument("-c", "--concurrency", type=int, default=1)
    p.add_argument("-n", "--requests", type=int, default=8)
    p.add_argument("--max-tokens", type=int, default=512)
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument("--ignore-eos", action="store_true")
    p.add_argument("--prompts", help="JSONL file with one {\"prompt\": ...} per line")
    p.add_argument("--metrics-url", default=os.environ.get("METRICS_URL"))
    p.add_argument("--tag", default="run")
    p.add_argument("--out-dir", default=os.environ.get("OUT_DIR", "results"))
    args = p.parse_args()

    prompts = DEFAULT_PROMPTS
    if args.prompts:
        prompts = [json.loads(l)["prompt"] for l in open(args.prompts, encoding="utf-8") if l.strip()]

    headers = {"Authorization": f"Bearer {os.environ.get('API_KEY', 'EMPTY')}"}
    sem = asyncio.Semaphore(args.concurrency)
    async with httpx.AsyncClient(timeout=1800, headers=headers) as client:
        queue_before = await read_queue(client, args.metrics_url)

        async def guarded(i):
            async with sem:
                return await one(client, args, prompts[i % len(prompts)])

        t0 = time.perf_counter()
        res = await asyncio.gather(*(guarded(i) for i in range(args.requests)))
        wall = time.perf_counter() - t0
        queue_after = await read_queue(client, args.metrics_url)

    ok = [x for x in res if x["decode_tok_s"]]
    summary = {
        "tag": args.tag, "concurrency": args.concurrency, "requests": len(res),
        "prompts_file": args.prompts, "max_tokens": args.max_tokens, "ignore_eos": args.ignore_eos,
        "per_user_decode_tok_s_p50": pct(ok, "decode_tok_s", 0.5),
        "per_user_decode_tok_s_p10": pct(ok, "decode_tok_s", 0.1),
        "ttft_s_p50": pct(ok, "ttft_s", 0.5), "ttft_s_p95": pct(ok, "ttft_s", 0.95),
        "p99_gap_ms_p50": pct(ok, "p99_gap_ms", 0.5),
        "aggregate_out_tok_s": sum(x["completion_tokens"] for x in res) / wall,
        "queue_before": queue_before, "queue_after": queue_after,
        "measured_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    out = pathlib.Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    with open(out / f"{args.tag}.jsonl", "w") as f:
        for x in res:
            f.write(json.dumps(x) + "\n")
    (out / f"{args.tag}.summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
