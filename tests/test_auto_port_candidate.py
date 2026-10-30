import importlib.util
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location('auto_port', Path(__file__).resolve().parents[1] / 'scripts/auto_port_candidate.py')
auto = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(auto)
INTEGRITY = 'sha512-' + 'A' * 86 + '=='


def contract(baseline='1.0.0-rc.2'):
    return {'baselineVersion': baseline, 'policy': 'p', 'targets': [
        {'path': 'dsh-a/lib/index.js', 'packageVersion': baseline, 'sha256': 'a' * 64, 'registryIntegrity': 'old', 'patched': True},
        {'path': 'dsh-b/lib/index.js', 'packageVersion': baseline, 'sha256': 'b' * 64, 'registryIntegrity': 'old', 'patched': False}]}


def comparison(candidate='1.0.0-rc.5', **overrides):
    rows = [{'path': 'dsh-a/lib/index.js', 'status': 'changed-package-contract', 'expectedSha256': 'a' * 64, 'actualSha256': 'a' * 64, 'packageVersion': candidate},
            {'path': 'dsh-b/lib/index.js', 'status': 'changed-package-contract', 'expectedSha256': 'b' * 64, 'actualSha256': 'b' * 64, 'packageVersion': candidate}]
    for row in rows:
        row.update(overrides.get(row['path'], {}))
    return {'candidateVersion': candidate, 'targets': [r for r in rows if not overrides.get(r['path'], {}).get('drop')],
            'packages': [{'name': '@deepseek-ai/dsh-a', 'integrity': INTEGRITY}, {'name': '@deepseek-ai/dsh-b', 'integrity': INTEGRITY}]}


class AutoPortTests(unittest.TestCase):
    def test_byte_identical_newer_release_is_eligible(self):
        self.assertEqual(auto.eligibility(contract(), comparison()), [])

    def test_changed_bytes_refuse(self):
        reasons = auto.eligibility(contract(), comparison(**{'dsh-a/lib/index.js': {'status': 'changed', 'actualSha256': 'c' * 64}}))
        self.assertEqual(reasons, ['dsh-a/lib/index.js: changed'])

    def test_same_status_but_different_hash_refuses(self):
        reasons = auto.eligibility(contract(), comparison(**{'dsh-b/lib/index.js': {'actualSha256': 'c' * 64}}))
        self.assertEqual(reasons, ['dsh-b/lib/index.js: bytes changed'])

    def test_missing_target_or_coverage_refuses(self):
        self.assertIn('candidate comparison does not cover exactly the reviewed targets',
                      auto.eligibility(contract(), comparison(**{'dsh-b/lib/index.js': {'drop': True}})))
        self.assertTrue(auto.eligibility(contract(), comparison(**{'dsh-a/lib/index.js': {'status': 'missing', 'actualSha256': None}})))

    def test_equal_older_or_invalid_versions_refuse(self):
        self.assertIn('candidate equals the reviewed pin', auto.eligibility(contract(), comparison('1.0.0-rc.2')))
        self.assertIn('candidate is older than the reviewed pin', auto.eligibility(contract(), comparison('1.0.0-rc.1')))
        self.assertEqual(auto.eligibility(contract(), comparison('not-a-version')), ['invalid candidate version'])

    def test_module_version_must_match_candidate(self):
        reasons = auto.eligibility(contract(), comparison(**{'dsh-a/lib/index.js': {'packageVersion': '1.0.0-rc.4'}}))
        self.assertEqual(reasons, ['dsh-a/lib/index.js: module version 1.0.0-rc.4 is not 1.0.0-rc.5'])

    def test_semver_precedence(self):
        ordered = ['0.2.1-alpha.5', '0.2.1-rc.2', '0.2.1-rc.10', '0.2.1', '0.2.2-alpha.2', '0.10.0']
        self.assertEqual(sorted(ordered, key=auto.version_key), ordered)

    def test_contract_rerecords_versions_and_integrity_but_keeps_hashes(self):
        updated = auto.candidate_contract(contract(), comparison())
        self.assertEqual(updated['baselineVersion'], '1.0.0-rc.5')
        self.assertEqual([t['sha256'] for t in updated['targets']], ['a' * 64, 'b' * 64])
        self.assertEqual({t['packageVersion'] for t in updated['targets']}, {'1.0.0-rc.5'})
        self.assertEqual({t['registryIntegrity'] for t in updated['targets']}, {INTEGRITY})
        self.assertEqual([t['patched'] for t in updated['targets']], [True, False])

    def test_missing_registry_integrity_refuses(self):
        bad = comparison()
        bad['packages'] = bad['packages'][:1]
        with self.assertRaises(ValueError):
            auto.candidate_contract(contract(), bad)

    def test_replace_once_preserves_crlf_and_requires_single_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'README.md'
            path.write_bytes(b'a\r\nThe approved DSH application is 1.0.0 with x.\r\nz\r\n')
            auto.replace_once(path, 'application is 1.0.0 ', 'application is 1.0.1 ')
            self.assertEqual(path.read_bytes(), b'a\r\nThe approved DSH application is 1.0.1 with x.\r\nz\r\n')
            with self.assertRaises(ValueError):
                auto.replace_once(path, 'not present', 'x')


class AutoPortWorkflowTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1]

    def test_workflow_only_opens_pr_after_eligibility_and_never_merges(self):
        text = (self.ROOT / '.github/workflows/upstream-auto-port.yml').read_text(encoding='utf-8')
        self.assertIn("steps.detect.outputs.eligible == 'true'", text)
        self.assertIn('scripts/auto_port_candidate.py --comparison port-evidence/source-diff.json --output port-evidence/eligibility.json\n', text)
        self.assertIn('--apply', text)
        self.assertIn('/actions/workflows/upstream-candidate.yml/dispatches', text)
        for forbidden in ('/merge', 'auto_merge', 'gh pr merge', 'docker push', 'kubectl'):
            self.assertNotIn(forbidden, text)
        uses = [line.split('uses:')[1].strip() for line in text.splitlines() if 'uses:' in line]
        self.assertTrue(uses and all(__import__('re').fullmatch(r'[\w./-]+@[0-9a-f]{40}', u) for u in uses), uses)

    def test_qualification_workflow_stays_read_only(self):
        text = (self.ROOT / '.github/workflows/upstream-candidate.yml').read_text(encoding='utf-8')
        self.assertNotIn('contents: write', text)


if __name__ == '__main__':
    unittest.main()
