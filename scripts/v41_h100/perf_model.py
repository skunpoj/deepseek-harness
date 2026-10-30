#!/usr/bin/env python3
"""Step-time model for DeepSeek-V4.1-Flash decode on 4x H100 NVL, with derivations.

Every projection here is derived from three measured sources and stated assumptions:
  M1  our SGLang + DSpark measurements (27-28 Oct 2026, 4x H100 NVL, TP4, --ignore-eos, 512 output tokens)
  M2  DeepInfer PR #92 measurements on 4x H20-3e (DP4 x EP4, DSpark on, random ids, 1024 in / 512 out)
  M3  checkpoint facts: 384 routed experts/layer, top-6, hidden 5120, expert intermediate 2304, MXFP4 experts,
      40 main MoE layers + 1 drafter MoE layer, attention FP8 5.2 GiB, shared/dense ~4.4 GiB
Nothing projected here has been measured on our node. These are conditional scenarios,
not validated H100 forecasts, physical ceilings, or permission to run a workload.
Run: python3 perf_model.py
"""
import itertools

# ---------------------------------------------------------------- M1: SGLang measured (per-user decode tok/s p50)
SGLANG = {1: 68.3, 2: 49.8, 4: 31.9, 8: 19.9}            # concurrency -> per-user tok/s (all requests running)
SGLANG_AGG_PLATEAU = 155.0                                # measured aggregate at c >= 8 (cap = 8 running requests)
ACCEPT = (3.3, 3.9, 4.6)                                  # DSpark accepted tokens per verify step, from server log

# ---------------------------------------------------------------- M2: #92 on 4x H20-3e
H20 = {1: 209.0, 32: 2285 / 32, 64: 2959 / 64}           # concurrency -> per-user tok/s (closed loop, default intervals)

# ---------------------------------------------------------------- M3 + hardware
HBM_TBS = {"H100 NVL": 3.9, "H20-3e": 4.0}                # spec bandwidth, TB/s
EFF_BW = 0.75                                            # assumed fraction of spec bandwidth, not measured on this path
EXPERT_MB = 3 * 2304 * 5120 * 0.5 / 1e6 * (1 + 1 / 16)   # w1,w2,w3 MXFP4 + E8M0 scales, MB per expert
N_EXPERT, TOPK, LAYERS = 384, 6, 40
DENSE_GB = (5.2 + 4.4) * 1.0737                          # attention + shared/dense, GiB -> GB, read once per step
LM_HEAD_GB = 1.3                                         # vocab x hidden BF16, read once per step (estimate)


def fit_line(points):
    """Least squares y = a + b x."""
    n = len(points); sx = sum(x for x, _ in points); sy = sum(y for _, y in points)
    sxx = sum(x * x for x, _ in points); sxy = sum(x * y for x, y in points)
    b = (n * sxy - sx * sy) / (n * sxx - sx * sx); a = (sy - b * sx) / n
    return a, b


def distinct_experts(picks: float) -> float:
    """Expected distinct count under uniform independent routing, not a hard bound."""
    return N_EXPERT * (1 - (1 - 1 / N_EXPERT) ** picks)


def floors():
    print("== 1. Analytical traffic scenarios (uniform independent routing; assumed bandwidth)")
    for tokens in (5, 40, 160):                          # verify tokens per step: c=1, c=8, c=32 with block 5
        d = distinct_experts(tokens * TOPK)
        tp_gb = (d * EXPERT_MB / 1e3 * LAYERS + DENSE_GB + LM_HEAD_GB) / 4      # TP4: everything split in 4
        ep_gb = d * EXPERT_MB / 1e3 * LAYERS / 4 + DENSE_GB + LM_HEAD_GB        # DP attention: dense replicated
        bw = HBM_TBS["H100 NVL"] * EFF_BW
        print(f"  {tokens:4d} tokens/step: {d:5.1f} distinct experts/layer; "
              f"TP4 {tp_gb:5.2f} GB -> {tp_gb / bw:5.2f} ms;  DP4xEP4 {ep_gb:5.2f} GB -> {ep_gb / bw:5.2f} ms")
    print("  + TP4 all-reduces across the PCIe link between pairs: 2 per layer x 40 layers x 25-40 us = 2-3 ms")
    print("  => Under these assumptions, TP4 traffic time at c=1 is about 4-6 ms; this is not a strict latency floor\n")


def sglang_fit():
    print("== 2. SGLang: step time vs running requests (from M1)")
    rows = []
    for a in ACCEPT:
        pts = [(b, 1000 * a / tps) for b, tps in SGLANG.items()]   # step_ms = accept / per-user tok/s
        alpha, beta = fit_line(pts)
        rows.append((a, alpha, beta))
        resid = max(abs(alpha + beta * b - s) for b, s in pts)
        print(f"  accept {a}: step(b) = {alpha:5.1f} ms + {beta:5.2f} ms x b   (max residual {resid:.1f} ms); "
              f"conditional model asymptote a/beta = {1000 * a / beta:5.0f} tok/s")
    print("  The per-request term beta (about 17-23 ms) dominates: each extra running request adds as much time\n"
          "  as a third of a whole fitted step. The asymptote applies only if this linear model and acceptance remain valid.\n")
    return rows


def path_a(rows):
    print("== 3. Path A projections")
    print("  A3 (cap 16/32, speculation on): aggregate = a*b / (alpha + beta*b)")
    for a, alpha, beta in rows:
        vals = [a * b / (alpha + beta * b) * 1000 for b in (8, 16, 32)]
        print(f"    accept {a}: b=8 {vals[0]:5.0f}, b=16 {vals[1]:5.0f}, b=32 {vals[2]:5.0f} tok/s")
    print("  A4 (speculation off at high load): one token per request per step. Unknown split of beta; illustrative scenarios, not proven bounds:")
    a, alpha, beta = rows[1]
    for label, alpha_off, beta_off in (("per-request cost scales with verified tokens (beta/5)", alpha * 0.7, beta / 5),
                                       ("half of it does (beta/2.5)", alpha * 0.8, beta / 2.5),
                                       ("per-request cost is fixed (beta)", alpha * 0.9, beta)):
        vals = [b / (alpha_off + beta_off * b) * 1000 for b in (16, 32, 64)]
        print(f"    {label:55s}: b=16 {vals[0]:4.0f}, b=32 {vals[1]:4.0f}, b=64 {vals[2]:4.0f} tok/s; "
              f"per user at b=32 {vals[1] / 32:4.1f}")
    print("  => 200+ aggregate on path A needs the per-request cost to shrink when speculation is off.\n")


def path_b(rows):
    print("== 4. Path B projections (remove SGLang's per-request and fixed overheads)")
    a = rows[1][0]
    for label, alpha, beta in (("today (fit, accept 3.9)", rows[1][1], rows[1][2]),
                               ("B2: per-request term down to DeepInfer-like 3-6 ms", rows[1][1], 4.5),
                               ("B2+B3: fixed term also down to 15-25 ms", 20.0, 4.5),
                               ("B2+B3 best case", 15.0, 3.0)):
        c1 = a / (alpha + beta) * 1000
        agg = {b: a * b / (alpha + beta * b) * 1000 for b in (8, 16, 32)}
        print(f"  {label:52s}: c=1 {c1:4.0f} tok/s; aggregate b=8 {agg[8]:4.0f}, b=16 {agg[16]:4.0f}, b=32 {agg[32]:4.0f}")
    print("  Which terms shrink is exactly what the B1 profile measures.\n")


def path_c():
    print("== 5. Path C: DeepInfer #92 + #200 on 4x H100 NVL, scaled from M2")
    # H20 step per rank-batch b_r (DP4: c requests spread over 4 ranks), using accept 3.5 for absolute times
    a = 3.5
    pts = [(max(1, c // 4) if c > 1 else 1, 1000 * a / tps) for c, tps in H20.items()]
    alpha_h20, beta_h20 = fit_line(pts)
    print(f"  #92 on H20 (accept {a} assumed): step(b_rank) = {alpha_h20:4.1f} ms + {beta_h20:4.2f} ms x b_rank")
    cases = {
        # name: (hardware factor on H20 step, extra fixed ms for EP over PCIe P2P, assumed extra ms per rank-local request; shared-fabric traffic unvalidated)
        "optimistic": (0.80, 2.0, 0.36),
        "central":    (0.95, 5.0, 0.60),
        "pessimistic": (1.05, 10.0, 0.90),
    }
    print("  Illustrative step = H20 step x factor + EP terms; shared PCIe contention and UVA cost are not validated")
    for name, (hw, ep_fixed, ep_per_req) in cases.items():
        out = []
        for c in (1, 16, 32):
            br = 1 if c == 1 else c // 4
            step = (alpha_h20 + beta_h20 * br) * hw + ep_fixed + ep_per_req * br
            per_user = 1000 * a / step
            out.append((c, per_user, per_user * c))
        print(f"    {name:11s}: " + "; ".join(f"c={c}: {pu:4.0f} per user, {ag:5.0f} total" for c, pu, ag in out))
    print("  Memory warning: direct #200 adds GPU-resident Engram scales; all residency and transient costs must fit.")
    print("  EP byte terms use rank-local batch here, not measured whole-node link traffic. UVA cost is unmeasured on H100.")
    print("  Valid only if: P2P works across the pairs, DeepEP EP4 runs with GIN off, memory fits, and real-prompt\n"
          "  acceptance matches #92's random-id runs. Accept cancels in the ratio to #92; it does not cancel in the\n"
          "  added PCIe terms.\n")


if __name__ == "__main__":
    floors()
    r = sglang_fit()
    path_a(r)
    path_b(r)
    path_c()
