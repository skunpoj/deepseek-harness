import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import { pathToFileURL } from 'node:url';

const oldCall = 'this.ctx.llm.resolveModelInfoFrom(current.provider, current.model)';
const newCall = 'this.ctx.llm.resolveModelInfo(current.provider, current.model)';

export function rewriteImageModelLookup(source, api) {
  if (!api || api.resolveModelInfo !== 'function' || api.resolveModelInfoFrom !== 'undefined') {
    throw new Error('IMAGE_MODEL_LOOKUP_API_DRIFT');
  }
  const oldCount = source.split(oldCall).length - 1;
  const newCount = source.split(newCall).length - 1;
  if (oldCount === 0 && newCount === 1) return { source, changed: false };
  if (oldCount !== 1 || newCount !== 0) throw new Error('IMAGE_MODEL_LOOKUP_SOURCE_DRIFT');
  return { source: source.replace(oldCall, newCall), changed: true };
}

export async function configureImageModelLookup(packageRoot) {
  const base = path.resolve(packageRoot);
  const llmEntry = path.join(base, 'node_modules/@deepseek-ai/dsh-llm/lib/index.js');
  const commandPath = path.join(base, 'node_modules/@deepseek-ai/dsh-api-session-controller/lib/types/commands.js');
  const { LlmRuntime } = await import(pathToFileURL(llmEntry).href);
  const publicMethod = LlmRuntime?.prototype?.resolveModelInfo;
  if (typeof publicMethod !== 'function' || !publicMethod.toString().includes('this.registration(provider)')) {
    throw new Error('IMAGE_MODEL_LOOKUP_REGISTRATION_CONTRACT_DRIFT');
  }
  const before = await fs.readFile(commandPath, 'utf8');
  if (Buffer.byteLength(before) > 1048576) throw new Error('IMAGE_MODEL_LOOKUP_FILE_BOUND');
  const result = rewriteImageModelLookup(before, {
    resolveModelInfo: typeof publicMethod,
    resolveModelInfoFrom: typeof LlmRuntime.prototype.resolveModelInfoFrom,
  });
  if (result.changed) {
    if (await fs.readFile(commandPath, 'utf8') !== before) throw new Error('IMAGE_MODEL_LOOKUP_CONCURRENT_CHANGE');
    await fs.writeFile(commandPath, result.source);
  }
  const readback = await fs.readFile(commandPath, 'utf8');
  if (readback !== result.source) throw new Error('IMAGE_MODEL_LOOKUP_READBACK');
  const digest = value => crypto.createHash('sha256').update(value).digest('hex');
  return { scope: 'BUILD_TIME_IMAGE_MODEL_LOOKUP_COMPATIBILITY', changed: result.changed,
    beforeSHA256: digest(before), afterSHA256: digest(readback), modelCalls: 0,
    preservesModelCapabilityCheck: true };
}

if (typeof process !== 'undefined' && process.argv[1] &&
    import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  configureImageModelLookup(process.argv[2] || '/usr/local/lib/node_modules/@deepseek-ai/dsh')
    .then(result => console.log(JSON.stringify(result)))
    .catch(error => { console.error(error.message); process.exitCode = 1; });
}
