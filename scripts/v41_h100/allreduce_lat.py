#!/usr/bin/env python3
"""All-reduce latency across the node's GPUs at decode-sized messages.

Run only when the GPUs are free:  torchrun --nproc_per_node=4 allreduce_lat.py
"""
import time

import torch
import torch.distributed as dist

dist.init_process_group("nccl")
rank = dist.get_rank()
torch.cuda.set_device(rank)
for kib in (8, 32, 128, 512, 2048):
    x = torch.ones(kib * 512, dtype=torch.bfloat16, device="cuda")  # kib KiB of bf16
    for _ in range(20):
        dist.all_reduce(x)
    torch.cuda.synchronize()
    n = 200
    t = time.perf_counter()
    for _ in range(n):
        dist.all_reduce(x)
    torch.cuda.synchronize()
    us = (time.perf_counter() - t) / n * 1e6
    if rank == 0:
        print(f"{kib:6d} KiB  {us:8.1f} us per all_reduce")
dist.destroy_process_group()
