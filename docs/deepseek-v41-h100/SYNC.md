# Keeping the internal and public repositories in sync

The access-controlled internal repository (Azure DevOps) is the source of truth. This public repository receives a **sanitized copy of named paths only**. Never merge internal branches or history into it (see [PUBLICATION.md](../PUBLICATION.md)).

Paths covered by this procedure:

```
docs/deepseek-v41-h100/
scripts/v41_h100/
```

## One-time setup (internal side)

Keep the same two paths in the internal repository, plus a private results folder that is never exported:

```
docs/deepseek-v41-h100/        # same files as public; internal-only notes go in private/ below
scripts/v41_h100/
private/v41_h100/results/      # raw measurements, logs, traces (excluded by .gitignore: private/)
private/v41_h100/private_terms.txt  # internal domains, system names, usernames; one per line
```

## Every sync (agent with push rights to both remotes)

```bash
set -euo pipefail
INTERNAL=<path to internal clone>          # Azure DevOps working copy, up to date
PUBLIC=$(mktemp -d)/dsh_setup               # fresh public clone every time
git clone --depth 1 https://github.com/skunpoj/dsh_setup "$PUBLIC"
cd "$PUBLIC" && git checkout -b sync-v41-$(date -u +%Y%m%d)

# 1. Copy only the named paths
for p in docs/deepseek-v41-h100 scripts/v41_h100; do
  rm -rf "$PUBLIC/$p"; mkdir -p "$(dirname "$PUBLIC/$p")"; cp -a "$INTERNAL/$p" "$PUBLIC/$p"
done

# 2. Scan: built-in checks plus the private term list kept outside the public tree
python3 scripts/scan_public.py .
python3 scripts/check_public_release.py . --private-terms "$INTERNAL/private/v41_h100/private_terms.txt"
# Optional second scanner if installed
command -v gitleaks >/dev/null && gitleaks detect --no-git -s docs/deepseek-v41-h100 -s scripts/v41_h100 || true

# 3. Syntax checks
bash -n scripts/v41_h100/*.sh
python3 -m py_compile scripts/v41_h100/*.py

# 4. Review the diff by eye, then commit and open a pull request
git add docs/deepseek-v41-h100 scripts/v41_h100
git diff --cached --stat
git commit -m "Sync DeepSeek-V4.1 H100 plan and tools from internal"
git push -u origin HEAD
gh pr create --repo skunpoj/dsh_setup --fill
```

A reviewer approves the pull request before merge. After merge, record the public commit hash in the internal repository (`docs/deepseek-v41-h100/PUBLIC_SYNC.log`, internal copy only).

## Direction of changes

- Changes start internally and flow out through this procedure.
- If a change is made publicly first (for example, a fix from a pull request on GitHub), copy the same files back into the internal repository in a normal internal pull request. Do not pull GitHub history into the internal repository.
