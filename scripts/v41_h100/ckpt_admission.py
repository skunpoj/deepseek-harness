#!/usr/bin/env python3
"""Per-GPU weight bytes for a DeepSeek-V4.1-Flash checkpoint on N GPUs, from safetensors headers only.

No tensor data is read and no GPU is used.
Usage: ckpt_admission.py CHECKPOINT_DIR [--gpus 4]

Edit PLACEMENT to match the engine's real placement rules (which tensors are expert-sharded,
replicated per rank, or kept in host memory). Weights are only part of the budget: add the
drafter if replicated, KV cache, communication buffers, CUDA context, allocator reserve and the
load-time repack peak before calling anything a fit.
"""
import collections, json, pathlib, re, struct, sys

root = pathlib.Path(sys.argv[1])
G = int(sys.argv[sys.argv.index("--gpus") + 1]) if "--gpus" in sys.argv else 4
PLACEMENT = [  # first match wins
    (r"engram", "host"),
    (r"mtp.*\.experts\.", "ep_shard"),
    (r"mtp", "replicate"),
    (r"\.experts\.", "ep_shard"),
    (r".*", "replicate"),
]
rules = [(re.compile(p, re.I), how) for p, how in PLACEMENT]
per, per_cat = collections.Counter(), collections.Counter()
for shard in sorted(root.glob("*.safetensors")):
    with open(shard, "rb") as fh:
        hdr = json.loads(fh.read(struct.unpack("<Q", fh.read(8))[0]))
    for name, meta in hdr.items():
        if name == "__metadata__":
            continue
        size = meta["data_offsets"][1] - meta["data_offsets"][0]
        how = next(h for rx, h in rules if rx.search(name))
        cat = "mtp" if re.search("mtp", name, re.I) else how
        per_cat[(cat, how)] += size
        per[how] += size
GiB = 1024 ** 3
for (cat, how), b in sorted(per_cat.items()):
    print(f"{cat:10s} {how:10s} {b / GiB:8.2f} GiB total")
gpu = per["replicate"] + per["ep_shard"] / G
print(f"per-GPU weights: {gpu / GiB:.2f} GiB (replicated {per['replicate'] / GiB:.2f} + sharded "
      f"{per['ep_shard'] / G / GiB:.2f}); host: {per['host'] / GiB:.2f} GiB")
