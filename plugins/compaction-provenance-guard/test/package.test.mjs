import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import quality from '../src/managed_compaction_quality.cjs';
import {candidateText} from '../src/index.mjs';
const root=new URL('../',import.meta.url);
test('additive manifest, own ID, no install hooks or runtime dependencies',async()=>{
 const p=JSON.parse(await fs.readFile(new URL('package.json',root)));
 assert.equal(p.dsh.bundle.patch,'./cordis.patch.yml');
 assert.equal(p.name,'dsh-compaction-provenance-guard');
 assert.equal(p.dependencies,undefined);assert.equal(p.peerDependencies,undefined);
 for(const key of ['preinstall','install','postinstall','prepare'])assert.equal(p.scripts[key],undefined);
 const patch=await fs.readFile(new URL('cordis.patch.yml',root),'utf8');
 assert.equal(patch,'- insert:\n    - id: skunpoj-compaction-provenance-guard\n      name: dsh-compaction-provenance-guard\n');
 const src=await fs.readFile(new URL('src/managed_compaction_quality.cjs',root),'utf8');
 assert.ok(!src.includes('patchPristine'));assert.ok(!src.includes('transformSource'));
});
test('user-only identifiers cannot be credited to assistant; supported evidence passes',()=>{
 const source=[{role:'user',content:'TASK_1234 TASK_5678'},{role:'assistant',content:'Generic guide.'}];
 for(const text of ['Assistant produced a guide containing TASK_1234 and TASK_5678.','The assistant guide preserved both identifiers.'])
 assert.throws(()=>quality.assertSummaryProvenance(source,[{type:'text',text}],[]),e=>quality.isProvenanceHold(e)&&!e.retryable);
 assert.equal(quality.assessIdentifierAttribution(source,[{type:'text',text:'User supplied TASK_1234 TASK_5678; assistant wrote a generic guide.'}]).status,'not-applicable');
 assert.equal(quality.assessIdentifierAttribution([{role:'assistant',content:'TASK_1234 TASK_5678'}],[{type:'text',text:'Assistant produced a guide containing TASK_1234 TASK_5678.'}]).status,'clear');
});
test('completed blocks replace partial text and original input is unchanged',()=>{
 const chunks=[{type:'text-delta',index:0,text:'partial'},{type:'block-end',index:0,block:{type:'text',text:'final'}},{type:'text-delta',index:0,text:'ignored'},{type:'reasoning-delta',index:1,text:'private reason'},{type:'finish',reason:{kind:'stop'}}];
 const before=JSON.stringify(chunks);assert.deepEqual(candidateText(chunks),{finish:'stop',summary:[{type:'text',text:'final'}]});assert.equal(JSON.stringify(chunks),before);
});
test('unknown stream schema is held rather than silently accepted',()=>{
 assert.throws(()=>candidateText([{type:'new-upstream-shape'}]),e=>quality.isProvenanceHold(e));
});
