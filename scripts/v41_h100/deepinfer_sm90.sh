#!/usr/bin/env bash
# DeepInfer on SM90 (H100) built from the community Hopper port, DeepInfer PR #92.
# Subcommands, in the order they are run:
#   facts       host/container facts that decide the gates (driver, memlock, RDMA, huge pages, topology)
#   fetch       clone DeepInfer; branch h100 = PR #92 (Hopper port) merged with #200 (UVA host Engram fetch);
#               also fetches #147 (OpenAI server) and #148 for reference
#   build       CPU-side build for sm90 in a fresh venv (no GPU needed)
#   collect     pytest collection only (no GPU needed)
#   probes      p2p / lsa / pin probes (scripts/v41_h100/sm90_probes.py); needs all GPUs free
#   gpu-tests   #92's SM90 suite + DeepEP EP test on 4 GPUs without GIN (needs all GPUs free)
#   offline     first model load + short offline generation, DSpark off then on (needs all GPUs free)
#
# Environment:
#   SRC (default ./deepinfer-sm90)   VENV (default ./venv-deepinfer-sm90)   RESULTS (default ./results/e6)
#   MODEL_DIR  local snapshot of deepseek-ai/DeepSeek-V4.1-Flash-1017 (revision 1bae0e10...)
#   ENGRAM_ENV extra "KEY=VALUE ..." exported before the offline run (optional)
#
# Why these settings (from reading #92, #200 and DeepEP 93eb6eb):
#   - Engram defaults to DeepEP's GIN RDMA fetch, which needs an RDMA NIC. Without one, use #200:
#     DEEPINFER_ENGRAM_UVA_FETCH=1 keeps the tables in /dev/shm (about 196 GB) and reads them over PCIe.
#   - DeepEP's EPBuffer initialises NCCL GIN unless EP_DISABLE_GIN=1. Without GIN, every EP rank must be in
#     one NCCL load/store (LSA) domain, i.e. CUDA peer access between all 4 GPUs, including across the pairs.
set -euo pipefail
SRC="${SRC:-$PWD/deepinfer-sm90}"; VENV="${VENV:-$PWD/venv-deepinfer-sm90}"; RESULTS="${RESULTS:-$PWD/results/e6}"
mkdir -p "$RESULTS"
cmd="${1:-}"; shift || true

case "$cmd" in
facts)
  { echo "# driver"; nvidia-smi --query-gpu=index,name,driver_version,memory.total,compute_cap --format=csv,noheader || true
    echo "# topology"; nvidia-smi topo -m || true
    echo "# peer-to-peer read / write across all GPU pairs"; nvidia-smi topo -p2p r || true; nvidia-smi topo -p2p w || true
    echo "# locked-memory limit (ulimit -l, KiB or unlimited)"; ulimit -l
    echo "# RDMA devices"; ls /dev/infiniband 2>/dev/null || echo "none"
    echo "# huge pages"; grep -E "HugePages_Total|HugePages_Free|Hugepagesize" /proc/meminfo
    echo "# host memory (GiB)"; free -g | awk '/Mem/{print "total="$2" used="$3" avail="$7}'
    echo "# capabilities"; grep -E "CapEff|Seccomp" /proc/self/status
  } 2>&1 | tee "$RESULTS/facts.txt" ;;
fetch)
  [[ -d "$SRC/.git" ]] || git clone https://github.com/deepseek-ai/DeepInfer.git "$SRC"
  cd "$SRC"
  git fetch origin +pull/92/head:pr92 +pull/200/head:pr200 +pull/147/head:pr147 +pull/148/head:pr148
  git checkout -B h100 pr92
  git -c user.name=h100 -c user.email=h100@localhost merge --no-ff --no-edit pr200   # merged cleanly on 28 Oct 2026
  git submodule update --init --recursive
  for b in pr92 pr200 pr147 pr148 h100; do echo "$b $(git rev-parse "$b")"; done | tee "$RESULTS/commits.txt" ;;
build)
  python3.12 -m venv "$VENV"; source "$VENV/bin/activate"; cd "$SRC"
  export DEEPINFER_ACCELERATOR=nvidia DEEPINFER_CUDA_ARCH=sm90
  ./build.sh 2>&1 | tee "$RESULTS/build.log"
  pip install 'humming-kernels==0.1.13'   # 0.1.13 fixes a misaligned read reported on H200; #92 validated 0.1.12
  python -c "import deepinfer, torch; print('import ok', torch.__version__, torch.version.cuda)" | tee -a "$RESULTS/build.log" ;;
collect)
  source "$VENV/bin/activate"; cd "$SRC"
  pytest --collect-only -q tests/sm90 tests/models/test_sm90_moe.py tests/distributed/test_cp_manager.py | tail -3 | tee "$RESULTS/collect.txt" ;;
probes)
  source "$VENV/bin/activate"; P="$(cd "$(dirname "$0")" && pwd)/sm90_probes.py"
  python "$P" p2p 2>&1 | tee "$RESULTS/probe_p2p.txt"
  EP_DISABLE_GIN=1 torchrun --nproc_per_node=4 "$P" lsa 2>&1 | tee "$RESULTS/probe_lsa.txt"
  python "$P" pin --gib "${PIN_GIB:-8}" 2>&1 | tee "$RESULTS/probe_pin.txt" ;;
gpu-tests)
  source "$VENV/bin/activate"; cd "$SRC"
  nvidia-smi topo -m | tee "$RESULTS/topo.txt"
  pytest -q tests/sm90 tests/models/test_sm90_moe.py tests/distributed/test_cp_manager.py 2>&1 | tee "$RESULTS/sm90_suite.txt" | tail -5
  # DeepEP V2 EP dispatch/combine on 4 GPUs, NVLink/LSA path only (no RDMA NIC): GIN off, hybrid mode off
  ( cd vendor/DeepEP && EP_DISABLE_GIN=1 timeout 900 python tests/ep/test_ep.py --num-processes 4 --allow-hybrid-mode 0 \
      --hidden 4096 --num-topk 6 --num-experts 256 ) 2>&1 | tail -40 | tee "$RESULTS/deepep_ep4.txt"
  # Engram UVA unit test from #200 (2 GPUs)
  DEEPINFER_ENGRAM_UVA_FETCH=1 timeout 900 python -m pytest -q tests/distributed/test_engram_uva.py 2>&1 | tail -5 | tee "$RESULTS/engram_uva.txt" ;;
offline)
  : "${MODEL_DIR:?set MODEL_DIR}"; source "$VENV/bin/activate"; cd "$SRC"
  export DEEPINFER_HF_MODEL_DIR="$MODEL_DIR" DEEPINFER_SM90_MOE_BACKEND=humming NCCL_NVLS_ENABLE=0 DEEPINFER_CP_COMM_BACKEND=nccl
  export DEEPINFER_ENGRAM_UVA_FETCH=1 EP_DISABLE_GIN=1
  shm=$(df -BG --output=avail /dev/shm | tail -1 | tr -dc 0-9)
  (( shm >= 200 )) || { echo "/dev/shm has ${shm} GiB free; #200 needs about 196 GB for the Engram tables" >&2; exit 1; }
  ( while sleep 5; do free -g | awk '/Mem/{print strftime("%T"), $3}'; done ) > "$RESULTS/host_mem_timeline.txt" & HMON=$!
  [[ -n "${ENGRAM_ENV:-}" ]] && export ${ENGRAM_ENV}
  ( while sleep 5; do nvidia-smi --query-gpu=index,memory.used --format=csv,noheader; done ) > "$RESULTS/mem_timeline.csv" & MON=$!
  trap 'kill $MON $HMON 2>/dev/null || true' EXIT
  python examples/offline_inference.py --help > "$RESULTS/offline_help.txt" 2>&1 || true
  DEEPINFER_USE_NEXTN_DECODE=0 timeout 3600 python examples/offline_inference.py --ep 4 2>&1 | tee "$RESULTS/offline_no_dspark.log"   # #92 defaults NEXTN to 5
  DEEPINFER_USE_NEXTN_DECODE=5 DEEPINFER_ENABLE_MTP_VARLEN=1 \
    timeout 1800 python examples/offline_inference.py --ep 4 2>&1 | tee "$RESULTS/offline_dspark.log" ;;
*) sed -n '2,23p' "$0"; exit 2 ;;
esac
