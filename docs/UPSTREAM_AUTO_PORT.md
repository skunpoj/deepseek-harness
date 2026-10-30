# Automatic DSH pin ports

The DSH package version is pinned in `Dockerfile` (`ARG DSH_VERSION`) because the image patches specific upstream files. `scripts/upstream_patch_contract.json` records the reviewed bytes of each of those files.

## What runs automatically

| Workflow | Schedule | Writes |
| --- | --- | --- |
| DSH upstream candidate (`upstream-candidate.yml`) | Daily 02:17 UTC, every push/PR | None; builds and qualifies the checked-out pin or a requested version |
| DSH upstream auto-port (`upstream-auto-port.yml`) | Daily 03:47 UTC, or manual | One branch and one pull request per eligible release |

The auto-port opens a pull request only when every reviewed upstream target is byte-identical in the new release and the release is newer than the pin. It then re-records the contract with the new package versions and registry integrity, re-runs `scripts/configure_models.js` on a pristine install, refuses if the patched files differ from the reviewed port, exports `patches/dsh-<version>/`, moves the Dockerfile pin and dispatches the candidate workflow on the branch so the full image qualification runs.

A release that changes, adds or removes any reviewed target is refused and listed in the run summary. That release needs a human-reviewed port: inspect the changed files, adapt `scripts/configure_models.js` and its tests, then update the contract and patch export in a pull request.

Nothing merges, publishes an image or deploys automatically. Merge the pull request after the candidate workflow passes.

## Repository setting

Pull requests opened by `GITHUB_TOKEN` require **Settings → Actions → General → Workflow permissions → Allow GitHub Actions to create and approve pull requests**. Without it the push succeeds but opening the pull request fails visibly in the run.

## Manual run

Actions → DSH upstream auto-port → Run workflow; leave the version empty to track npm `latest`, or enter an explicit version.
