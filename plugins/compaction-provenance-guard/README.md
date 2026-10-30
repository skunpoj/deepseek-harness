# Compaction provenance guard

A standalone, additive DSH Bundle. Its single entry is
`skunpoj-compaction-provenance-guard`. It does not patch, disable, rename or
replace official DSH components. The repository root remains a deployment
reference; this subfolder is the plugin installation target.

## Purpose and limits

Before a completed compaction response reaches checkpoint adoption, check the
tested English claims that an assistant guide included opaque identifiers.
Identifiers appearing only in user text must not be credited to assistant
output. Unsupported or ambiguous attribution holds compaction and suppresses
recovery retry for that typed error. Ordinary chat bypasses the guard.

This is a bounded heuristic, not a general truth, recall, security or summary
quality verifier. It cannot guarantee detection across languages, paraphrases,
hidden/non-text content or identifiers outside its matcher. A hold can prevent
compaction until the conversation is reviewed. Existing summaries are not
rewritten. The existing compactor owns persistence; the plugin itself does not
read/write files, access credentials, run commands or make network requests.
It buffers up to 4 MiB of chunks and 262,144 text characters before releasing
successful compaction output. Source inspection is bounded at 32 MiB visible
text. Rejected candidate details exist only on the in-memory error object.

## Host requirements

Use the exact release matrix in [compatibility.json](compatibility.json).
Unlisted versions remain unknown. Node must satisfy `engines.node`.

| DSH version | Install | Native start / scoped guard | Uninstall | Rollback |
| --- | --- | --- | --- | --- |
| 0.2.1-rc.1 | Passed | Passed | Passed | Passed |
| 0.2.1-rc.2 | Passed | Passed | Passed | Passed |
| 0.2.1-rc.3 | Passed | Passed | Passed | Passed |

Tested on Windows with Node 24.18.1. Each case used a separate temporary home,
the actual DSH CLI/package manager and native profile loader. The real native
compactor adopted a supported synthetic checkpoint; explicit and coreference
misattributions were held without checkpoint replacement. Uninstall removed
the entry and restored unguarded synthetic stream behavior. The original
profile manifest was restored, newly created lock/workspace configuration
removed, and a disposable history sentinel remained unchanged. Official
module source hashes matched before and after. No real model calls, real
histories, browser application, Linux/macOS or live deployment were tested.

The package has no runtime dependencies. The host must
already provide both `llm` and `compaction`. No install/prepare lifecycle scripts
or model-provider keys are supplied. Do not install alongside another copy of
the deployment guard in the same profile.

## Install, start and uninstall

First pin the complete repository commit and inspect this subfolder and its
manifest. Use a **new disposable DSH_HOME**, never your current profile, for
initial acceptance. Replace the paths with your own absolute paths:

```sh
export DSH_HOME=/absolute/path/to/new-disposable-dsh-home
dsh plugin --profile guard-check add /absolute/path/to/dsh_setup/plugins/compaction-provenance-guard
dsh --profile guard-check --dump-config
# Start that disposable profile using your ordinary DSH launcher.
# The plugin must show active with its own entry ID.
dsh plugin --profile guard-check remove dsh-compaction-provenance-guard
dsh --profile guard-check --dump-config
```

The exact automated qualification procedure, its real host lifecycle and mock
stream boundary are documented in `test/qualify.mjs`. It never invokes a model
provider. CLI/profile installation and native plugin startup are separate
checks; passing them does not certify a browser application or real LLM summary.
Back up profile manifest, lockfile and patch before any non-disposable install.
Remove the bundle and restore the saved profile files for rollback. Do not
delete user histories or change upstream packages.

## Development

`npm test` runs bounded source/stream checks without a host or model.
`node --expose-internals test/qualify.mjs /absolute/path/to/isolated-host /absolute/path/to/new-output`
executes the isolated host lifecycle from the repository checkout. Keep personal configuration, secrets and
raw private deployment evidence outside this package.

For DSH Store monorepo discovery use manifest path
`plugins/compaction-provenance-guard/package.json` and install path
`plugins/compaction-provenance-guard`. Packaging and author tests do not imply
marketplace approval or independent security audit.
