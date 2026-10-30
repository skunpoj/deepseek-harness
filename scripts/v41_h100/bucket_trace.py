#!/usr/bin/env python3
"""Sum GPU kernel time by bucket from a torch-profiler Chrome trace (.json or .json.gz), one rank.

Usage: bucket_trace.py TRACE [--steps N]
Edit BUCKETS after reading the 'top 30 kernels' list for your build.
"""
import collections, gzip, json, re, sys

BUCKETS = [
    ("allreduce/comm", r"nccl|all_?reduce|allgather|reduce_scatter|cross_device|custom_ar|one_shot|two_shot"),
    ("indexer", r"indexer|mqa_logits|top_?k_?select|lightning_index"),
    ("moe", r"marlin|humming|moe|fused_experts|topk|grouped"),
    ("attention", r"flash|mla|attn|sparse_attn|softmax"),
    ("engram/host copy", r"engram|gather|memcpy|Memcpy|htod|HtoD|index_select"),
    ("gemm (dense)", r"gemm|cutlass|sm90_xmma|matmul|cublas"),
    ("norm/elementwise", r"norm|rope|silu|act|elementwise|vectorized|copy_|fill"),
]
COMPILED = [(n, re.compile(p, re.I)) for n, p in BUCKETS]

path = sys.argv[1]
steps = int(sys.argv[sys.argv.index("--steps") + 1]) if "--steps" in sys.argv else None
open_ = gzip.open if path.endswith(".gz") else open
events = json.load(open_(path, "rt")).get("traceEvents", [])
kernels = [e for e in events if e.get("ph") == "X" and e.get("cat") in ("kernel", "gpu_memcpy", "gpu_memset")]
if not kernels:
    sys.exit("no GPU kernel events found; was the profiler started with CUDA activities?")
by_name, by_bucket = collections.Counter(), collections.Counter()
for e in kernels:
    name, dur = e.get("name", "?"), float(e.get("dur", 0))
    by_name[name] += dur
    by_bucket[next((n for n, rx in COMPILED if rx.search(name)), "other")] += dur
span = max(e["ts"] + e.get("dur", 0) for e in kernels) - min(e["ts"] for e in kernels)
busy = sum(by_bucket.values())
print(f"wall span {span / 1000:.1f} ms, GPU busy {busy / 1000:.1f} ms ({100 * busy / span:.0f}%), idle {100 * (1 - busy / span):.0f}%")
for b, us in by_bucket.most_common():
    line = f"  {b:18s} {us / 1000:9.1f} ms  {100 * us / busy:5.1f}% of busy"
    if steps:
        line += f"  {us / 1000 / steps:7.2f} ms/step"
    print(line)
print("top 30 kernels:")
for n, us in by_name.most_common(30):
    print(f"  {us / 1000:8.2f} ms  {n[:110]}")
