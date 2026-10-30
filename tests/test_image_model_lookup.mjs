import assert from 'node:assert/strict';
import { rewriteImageModelLookup } from '../scripts/configure_image_model_lookup.mjs';
const oldCall = 'this.ctx.llm.resolveModelInfoFrom(current.provider, current.model)';
const newCall = 'this.ctx.llm.resolveModelInfo(current.provider, current.model)';
const api = { resolveModelInfo: 'function', resolveModelInfoFrom: 'undefined' };
const cases = [];
function check(name, fn) { fn(); cases.push(name); }
check('uses public provider-id lookup and preserves capability rejection', () => {
  const source = 'const model = await ' + oldCall + '; if (!model.inputModalities.includes("image")) throw new Error("MODEL_DOES_NOT_SUPPORT_IMAGES");';
  const expected = source.replace(oldCall, newCall);
  assert.equal(rewriteImageModelLookup(source, api).source, expected);
});
check('already corrected source is unchanged', () => assert.equal(rewriteImageModelLookup(newCall, api).changed, false));
check('unknown source refuses', () => assert.throws(() => rewriteImageModelLookup('changed upstream', api), /SOURCE_DRIFT/));
check('duplicate old calls refuse', () => assert.throws(() => rewriteImageModelLookup(oldCall + oldCall, api), /SOURCE_DRIFT/));
check('mixed old and new calls refuse', () => assert.throws(() => rewriteImageModelLookup(oldCall + newCall, api), /SOURCE_DRIFT/));
check('missing public method refuses', () => assert.throws(() => rewriteImageModelLookup(oldCall, {...api, resolveModelInfo: 'undefined'}), /API_DRIFT/));
check('new upstream From method requires review', () => assert.throws(() => rewriteImageModelLookup(oldCall, {...api, resolveModelInfoFrom: 'function'}), /API_DRIFT/));
export const result = { passed: cases.length, cases, modelCalls: 0, runtimePatched: false };
