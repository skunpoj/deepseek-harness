"""Prepare a DSH pin port automatically only when no reviewed source changed.

A release is eligible when every reviewed upstream target in
upstream_patch_contract.json is byte-identical in the candidate (only package
versions and registry integrity moved) and the candidate is newer than the pin.
The port is then mechanical: re-record the contract, re-run the configurator on
a pristine install, export the reviewed patch set and move the Dockerfile pin.
Any changed or missing target refuses the automatic port; that release needs a
human source review. Nothing here merges, publishes an image or deploys.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from export_upstream_patchset import export  # noqa: E402

VERSION = re.compile(r'([0-9]+)\.([0-9]+)\.([0-9]+)(?:-([A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*))?')
SAFE_STATUSES = {'compatible', 'changed-package-contract'}


def version_key(version):
    """SemVer precedence key; prerelease identifiers compare numerically when numeric."""
    match = VERSION.fullmatch(version)
    if not match:
        raise ValueError('Invalid version: ' + version)
    major, minor, patch, pre = match.groups()
    if pre is None:
        return (int(major), int(minor), int(patch), 1, ())
    parts = tuple((0, int(p), '') if p.isdigit() else (1, 0, p) for p in re.split(r'[.-]', pre))
    return (int(major), int(minor), int(patch), 0, parts)


def eligibility(contract, comparison):
    """Return the reasons an automatic port is refused; empty means eligible."""
    reasons = []
    baseline, candidate = contract['baselineVersion'], comparison.get('candidateVersion', '')
    if not VERSION.fullmatch(candidate):
        return ['invalid candidate version']
    if candidate == baseline:
        reasons.append('candidate equals the reviewed pin')
    elif version_key(candidate) < version_key(baseline):
        reasons.append('candidate is older than the reviewed pin')
    rows = {row['path']: row for row in comparison.get('targets', [])}
    if sorted(rows) != sorted(t['path'] for t in contract['targets']):
        reasons.append('candidate comparison does not cover exactly the reviewed targets')
    for target in contract['targets']:
        row = rows.get(target['path'])
        if row is None:
            continue
        if row.get('status') not in SAFE_STATUSES:
            reasons.append(f"{target['path']}: {row.get('status')}")
        elif row.get('actualSha256') != target['sha256']:
            reasons.append(f"{target['path']}: bytes changed")
        elif row.get('packageVersion') != candidate:
            reasons.append(f"{target['path']}: module version {row.get('packageVersion')} is not {candidate}")
    return reasons


def candidate_contract(contract, comparison):
    """Re-record the reviewed contract for a byte-identical candidate."""
    candidate = comparison['candidateVersion']
    integrity = {p['name']: p['integrity'] for p in comparison.get('packages', [])}
    targets = []
    for target in contract['targets']:
        name = '@deepseek-ai/' + target['path'].split('/')[0]
        if not re.fullmatch(r'sha512-[A-Za-z0-9+/]{86}==', integrity.get(name, '')):
            raise ValueError('Missing registry integrity for ' + name)
        targets.append({**target, 'packageVersion': candidate, 'registryIntegrity': integrity[name]})
    return {**contract, 'baselineVersion': candidate, 'targets': targets}


def replace_once(path, old, new):
    """Byte-preserving single replacement (keeps CRLF files as they are)."""
    data = path.read_bytes()
    if data.count(old.encode()) != 1:
        raise ValueError(f'Expected exactly one {old!r} in {path.name}')
    path.write_bytes(data.replace(old.encode(), new.encode()))


def port(contract, comparison, workdir):
    """Install the candidate, apply the configurator and write the reviewed port into the checkout."""
    baseline, candidate = contract['baselineVersion'], comparison['candidateVersion']
    docker = ROOT / 'Dockerfile'
    pi_ai = re.findall(r'^ARG PI_AI_VERSION=(\S+)$', docker.read_text(encoding='utf-8'), re.M)
    if len(pi_ai) != 1:
        raise ValueError('Expected one PI_AI_VERSION pin')
    prefix = workdir / 'prefix'
    subprocess.run(['npm', 'install', '-g', '--prefix', str(prefix), '--ignore-scripts', '@deepseek-ai/dsh@' + candidate], check=True)
    patched = prefix / 'lib/node_modules/@deepseek-ai/dsh'
    subprocess.run(['npm', 'install', '--prefix', str(patched), '--no-save', '--omit=dev', '--ignore-scripts',
                    '--package-lock=false', '@earendil-works/pi-ai@' + pi_ai[0]], check=True)
    pristine = workdir / 'pristine'
    shutil.copytree(patched, pristine, symlinks=True)
    updated = candidate_contract(contract, comparison)
    contract_file = ROOT / 'scripts/upstream_patch_contract.json'
    contract_file.write_text(json.dumps(updated, indent=2) + '\n', encoding='utf-8')
    env = {'PATH': os.environ['PATH'], 'DSH_PACKAGE_DIR': str(patched),
           'DSH_HOME': str(workdir / 'home'), 'HOME': str(workdir / 'home')}
    subprocess.run(['node', str(ROOT / 'scripts/verify_upstream_contract.js')], check=True, env=env)
    subprocess.run(['node', str(ROOT / 'scripts/configure_models.js')], check=True, env=env, stdout=subprocess.DEVNULL)
    previous = json.loads((ROOT / f'patches/dsh-{baseline}/manifest.json').read_text(encoding='utf-8'))
    record = export(updated, pristine, patched, ROOT / f'patches/dsh-{candidate}')
    before = {t['path']: t['patchedSHA256'] for t in previous['targets'] if t['patched']}
    after = {t['path']: t['patchedSHA256'] for t in record['targets'] if t['patched']}
    if before != after:
        raise ValueError('Configured output differs from the reviewed port; refusing automatic port')
    shutil.rmtree(ROOT / f'patches/dsh-{baseline}')
    replace_once(docker, 'ARG DSH_VERSION=' + baseline, 'ARG DSH_VERSION=' + candidate)
    readme = ROOT / 'README.md'
    if f'The approved DSH application is {baseline} ' in readme.read_text(encoding='utf-8', errors='replace'):
        replace_once(readme, f'The approved DSH application is {baseline} ', f'The approved DSH application is {candidate} ')
    return {'candidateVersion': candidate, 'previousPin': baseline, 'patchSHA256': record['patchSHA256'],
            'patchedTargetsUnchanged': sorted(after), 'merged': False, 'deployed': False}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--comparison', type=Path, required=True, help='source-diff.json from check_upstream.py')
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--apply', action='store_true', help='Write the port into the checkout when eligible')
    args = ap.parse_args()
    contract = json.loads((ROOT / 'scripts/upstream_patch_contract.json').read_text(encoding='utf-8'))
    comparison = json.loads(args.comparison.read_text(encoding='utf-8'))
    reasons = eligibility(contract, comparison)
    result = {'candidateVersion': comparison.get('candidateVersion'), 'reviewedPin': contract['baselineVersion'],
              'eligible': not reasons, 'refusalReasons': reasons, 'applied': False}
    if args.apply and not reasons:
        with tempfile.TemporaryDirectory(prefix='dsh-auto-port-') as tmp:
            result.update(port(contract, comparison, Path(tmp)), applied=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
