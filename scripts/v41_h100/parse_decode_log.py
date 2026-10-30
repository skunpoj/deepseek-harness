#!/usr/bin/env python3
"""Summarise SGLang 'Decode batch' log lines: running requests, accept length, generation throughput.

Usage: parse_decode_log.py ENGINE_LOG [ENGINE_LOG ...]

est_step_ms = batch x accept_len / gen_throughput  (the measured time per speculative verify step).
Log wording differs between SGLang builds; if nothing parses, the first 'Decode batch' line is printed
so the patterns below can be adjusted.
"""
import collections, re, statistics, sys

RUN = re.compile(r"#running-req[s]?:\s*(\d+)")
ACC = re.compile(r"accept[ _-]?len(?:gth)?[^0-9]{0,4}([0-9.]+)", re.I)
GEN = re.compile(r"gen throughput \(token/s\):\s*([0-9.]+)", re.I)

for path in sys.argv[1:]:
    by_batch = collections.defaultdict(lambda: {"acc": [], "gen": []})
    sample = None
    for line in open(path, errors="replace"):
        if "Decode batch" not in line:
            continue
        sample = sample or line.strip()
        r, a, g = RUN.search(line), ACC.search(line), GEN.search(line)
        if not r or not g:
            continue
        b = int(r.group(1))
        by_batch[b]["gen"].append(float(g.group(1)))
        if a:
            by_batch[b]["acc"].append(float(a.group(1)))
    print(f"== {path}")
    if not by_batch:
        print("   no parsable 'Decode batch' lines; sample:", sample)
        continue
    print("   batch  lines  accept_len_p50  gen_tok_s_p50  est_step_ms")
    for b in sorted(by_batch):
        d = by_batch[b]
        acc = statistics.median(d["acc"]) if d["acc"] else float("nan")
        gen = statistics.median(d["gen"])
        step = 1000 * b * acc / gen if gen and d["acc"] else float("nan")
        print(f"   {b:5d}  {len(d['gen']):5d}  {acc:14.2f}  {gen:13.1f}  {step:11.1f}")
