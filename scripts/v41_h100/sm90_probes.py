#!/usr/bin/env python3
"""Small probes that decide whether DeepInfer (#92 + #200) can run on a 4-GPU Hopper node
whose GPUs are NVLink-bridged in pairs. Each probe needs a CUDA context, so run them while
the GPUs are free (start of a maintenance window), from the DeepInfer venv.

  python3 sm90_probes.py p2p                 # CUDA peer access matrix + a 256 MiB copy per pair
  torchrun --nproc_per_node=4 sm90_probes.py lsa   # NCCL load/store (LSA) domain DeepEP will use
  python3 sm90_probes.py pin --gib 8         # /dev/shm mmap + cudaHostRegister, as #200 does
  python3 sm90_probes.py pin --gib 200       # the full Engram size (needs /dev/shm >= 200 GiB)

How to read them:
  p2p  every pair True       -> DeepEP can treat all 4 GPUs as one LSA domain (with EP_DISABLE_GIN=1)
       any cross-pair False  -> LSA domain is 2; DeepEP then needs NCCL GIN (an RDMA NIC) between pairs.
                                Without a NIC, EP4 needs a non-DeepEP (NCCL all-to-all) expert path.
  lsa  nvl_ranks == 4        -> pass;  nvl_ranks == 2 -> as above
  pin  status 0              -> host-memory Engram via UVA (#200) can be registered in this container
"""
import argparse, os, resource, sys, tempfile, time


def p2p():
    import torch
    n = torch.cuda.device_count()
    print(f"devices: {n}; " + ", ".join(torch.cuda.get_device_name(i) for i in range(n)))
    ok = True
    for i in range(n):
        row = []
        for j in range(n):
            row.append("  -  " if i == j else (" yes " if torch.cuda.can_device_access_peer(i, j) else " NO  "))
            ok &= i == j or torch.cuda.can_device_access_peer(i, j)
        print(f"GPU{i}: " + "".join(row))
    size = 256 << 20
    for i in range(n):
        for j in range(n):
            if i >= j:
                continue
            a = torch.empty(size, dtype=torch.uint8, device=f"cuda:{i}")
            b = torch.empty(size, dtype=torch.uint8, device=f"cuda:{j}")
            for _ in range(3):
                b.copy_(a)
            torch.cuda.synchronize(i); torch.cuda.synchronize(j)
            t = time.perf_counter()
            for _ in range(10):
                b.copy_(a)
            torch.cuda.synchronize(i); torch.cuda.synchronize(j)
            gbs = 10 * size / (time.perf_counter() - t) / 1e9
            print(f"copy GPU{i}->GPU{j}: {gbs:6.1f} GB/s")
            del a, b
    print("RESULT p2p:", "all pairs peer-accessible" if ok else "some pairs NOT peer-accessible")


def lsa():
    import torch, torch.distributed as dist
    os.environ.setdefault("EP_DISABLE_GIN", "1")
    dist.init_process_group("nccl")
    r = dist.get_rank()
    torch.cuda.set_device(int(os.environ.get("LOCAL_RANK", r)))
    dist.all_reduce(torch.zeros(1, device="cuda"))
    import deep_ep
    rdma, nvl = deep_ep.get_physical_domain_size(dist.group.WORLD)
    if r == 0:
        print(f"RESULT lsa: world={dist.get_world_size()} rdma_ranks={rdma} nvl_ranks={nvl} "
              f"({'one LSA domain: DeepEP NVLink-path kernels can run with EP_DISABLE_GIN=1' if nvl == dist.get_world_size() else 'split domain: cross-domain traffic needs NCCL GIN / RDMA'})")
    dist.destroy_process_group()


def pin(gib: int):
    import torch
    soft, hard = resource.getrlimit(resource.RLIMIT_MEMLOCK)
    fmt = lambda v: "unlimited" if v == resource.RLIM_INFINITY else f"{v >> 10} KiB"
    print(f"RLIMIT_MEMLOCK soft={fmt(soft)} hard={fmt(hard)}")
    st = os.statvfs("/dev/shm")
    print(f"/dev/shm free: {st.f_bavail * st.f_frsize / 2**30:.1f} GiB")
    import mmap
    nbytes = gib << 30
    fd, path = tempfile.mkstemp(dir="/dev/shm", prefix="uva_probe_")
    try:
        os.ftruncate(fd, nbytes)
        m = mmap.mmap(fd, nbytes, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE)
        t = torch.frombuffer(m, dtype=torch.uint8)
        t.fill_(1)  # touch every page, as the real table write does
        cudart = torch.cuda.cudart()
        torch.cuda.init()
        t0 = time.perf_counter()
        status = int(cudart.cudaHostRegister(t.data_ptr(), nbytes, 3))  # Portable | Mapped, as in #200
        dt = time.perf_counter() - t0
        print(f"cudaHostRegister({gib} GiB) status={status} in {dt:.1f} s")
        if status == 0:
            cudart.cudaHostUnregister(t.data_ptr())
        del t
        m.close()
        print("RESULT pin:", "OK" if status == 0 else "FAILED (container limit or driver refusal)")
    finally:
        os.close(fd)
        os.unlink(path)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("probe", choices=["p2p", "lsa", "pin"])
    ap.add_argument("--gib", type=int, default=8)
    a = ap.parse_args()
    {"p2p": p2p, "lsa": lsa, "pin": lambda: pin(a.gib)}[a.probe]()
