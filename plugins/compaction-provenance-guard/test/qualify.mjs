// Offline-provider acceptance: real installed DSH CLI, profile loader and
// Cordis lifecycle; only the stream adapter is synthetic. Never use real HOME.
import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
const run=promisify(execFile),sha=b=>createHash('sha256').update(b).digest('hex');
const host=path.resolve(process.argv[2]),out=path.resolve(process.argv[3]);
const pkg=fileURLToPath(new URL('../',import.meta.url));
assert.ok(process.execArgv.includes('--expose-internals'),'run node --expose-internals');
// A new output directory is mandatory: never overwrite any existing profile.
await fs.mkdir(out);
const home=path.join(out,'home'),profileDir=path.join(home,'profiles','guard-check');
await fs.mkdir(profileDir,{recursive:true});
const req=createRequire(path.join(host,'package.json'));
const anchor=req.resolve('@deepseek-ai/dsh/package.json');
const manifest=JSON.parse(await fs.readFile(anchor));
const bin=path.resolve(path.dirname(anchor),typeof manifest.bin==='string'?manifest.bin:manifest.bin.dsh);
const env={};
for(const key of ['PATH','Path','SystemRoot','SYSTEMROOT','ComSpec','COMSPEC','PATHEXT','TEMP','TMP'])if(process.env[key])env[key]=process.env[key];
Object.assign(env,{DSH_HOME:home,HOME:home,USERPROFILE:home,NODE_USE_SYSTEM_CA:'1',DO_NOT_TRACK:'1',DSH_TELEMETRY_DISABLED:'1',npm_config_ignore_scripts:'true',npm_config_registry:'https://registry.npmjs.org'});
const initial=JSON.stringify({name:'disposable-guard-profile',version:'1.0.0',private:true,dsh:{profile:{bundles:[]}}},null,2)+'\n';
await fs.writeFile(path.join(profileDir,'package.json'),initial);
const sentinel=Buffer.from('Disposable history sentinel; never loaded as a real session.\n');
await fs.writeFile(path.join(home,'history-sentinel'),sentinel);
const commands=[];
async function cli(args){
 try{const r=await run(process.execPath,[bin,'plugin','--profile','guard-check',...args],{env,cwd:out,timeout:180000,maxBuffer:2*1024*1024});commands.push({args,exitCode:0,stdout:r.stdout,stderr:r.stderr});}
 catch(e){commands.push({args,exitCode:e.code,stdout:e.stdout,stderr:e.stderr});await fs.writeFile(path.join(out,'commands.json'),JSON.stringify(commands,null,2));throw e;}
}
const bootMod=await import(pathToFileURL(req.resolve('@deepseek-ai/dsh-app-boot')).href);
const {default:Llm,LlmAdapter,createUserMessage,createMessage,BlockAssembler}=await import(pathToFileURL(req.resolve('@deepseek-ai/dsh-llm')).href);
const {default:Store,Session,SessionId}=await import(pathToFileURL(req.resolve('@deepseek-ai/dsh-session')).href);
const {default:Projection}=await import(pathToFileURL(req.resolve('@deepseek-ai/dsh-session-projection')).href);
const {default:Meter}=await import(pathToFileURL(req.resolve('@deepseek-ai/dsh-token-meter')).href);
const checks=[];let calls=0,ctx;
const {candidateText}=await import(new URL('../src/index.mjs',import.meta.url));
for(const chunks of [
 [{type:'text-delta',index:0,text:'a'},{type:'text-delta',index:0,text:'b'}],
 [{type:'block-start',index:0,blockType:'text'},{type:'text-delta',index:0,text:'partial'},{type:'block-end',index:0,block:{type:'text',text:'final'}}],
 [{type:'reasoning-delta',index:0,text:'reason'},{type:'tool-call-delta',index:1,id:'call',name:'fixture',argumentsDelta:'{}'},{type:'text-delta',index:2,text:'visible'}],
 [{type:'block-end',index:0,block:{type:'image',url:'data:image/png;base64,AA=='}}]
]){
 const stream=[...chunks,{type:'finish',reason:{kind:'stop'}}],assembler=new BlockAssembler();for(const chunk of stream)assembler.push(chunk);
 assert.deepEqual(candidateText(stream).summary,assembler.blocks().filter(b=>b.type==='text').map(b=>({type:'text',text:b.text})));
}
checks.push('four candidate-text extraction cases match official BlockAssembler');
const text='The assistant guide preserved both identifiers.';
let mockText=text;
const sourceBefore={};for(const name of ['dsh-app-boot','dsh-llm','dsh-compaction-basic'])sourceBefore[name]=sha(await fs.readFile(req.resolve('@deepseek-ai/'+name)));
class Mock extends LlmAdapter{
 async resolveModel(provider,model){return {provider,id:model,name:model,context:{contextWindow:32768}}}
 async *stream(){calls++;yield {type:'text-delta',index:0,text:mockText};yield {type:'finish',reason:{kind:'stop'}}}
}
const options={purpose:'compaction',provider:'synthetic',model:'synthetic',messages:[{role:'user',content:[{type:'text',text:'User supplied TASK_1234 TASK_5678.'}]},{role:'assistant',content:[{type:'text',text:'Generic guide without IDs.'}]},{role:'user',content:[{type:'text',text:'You are now acting as a compaction engine for this AI coding assistant. Condense.'}]}]};
options.messages=options.messages.map(m=>m.role==='user'?createUserMessage({content:m.content,source:{kind:'user'}}):createMessage({role:'assistant',content:m.content,source:{kind:'model',provider:'synthetic',model:'synthetic'}}));
const collect=async iter=>{const a=[];for await(const x of iter)a.push(x);return a};
async function start(expectGuard){
 const loaded=bootMod.loadProfileDirectory('dsh',profileDir,anchor);
 assert.deepEqual(loaded.skippedBundles,[],'bundle compatibility must not be skipped');
 const config=path.join(out,'base.json');
 await fs.writeFile(config,JSON.stringify([{id:'fixture-compactor',name:pathToFileURL(req.resolve('@deepseek-ai/dsh-compaction-basic')).href,config:{auto:false,compactionRetries:0,maxTokens:128}}]));
 const profile={dir:profileDir,home,patchPath:path.join(profileDir,'cordis.patch.yml'),installAnchor:anchor,overlays:[],startedBundles:[]};
 ctx=await bootMod.boot('dsh',config,bootMod.readProfilePatches('dsh',profile,loaded),async owner=>{
  new Llm(owner);new Store(owner);new Projection(owner);new Meter(owner);
 },pathToFileURL(path.join(profileDir,'anchor.mjs')).href);
 ctx.llm.registerAdapter(['synthetic'],new Mock());
 const entry=[...ctx.loader.entries()].find(e=>e.options.id==='skunpoj-compaction-provenance-guard');
 if(expectGuard){
  assert.equal(entry?.fiber?.state,2,'plugin actually active');
  assert.ok(ctx.compaction,'official compactor still present');
  let delivered=0,error;const before=JSON.stringify(options);
  try{for await(const chunk of ctx.llm.stream(options))delivered++;}catch(e){error=e;}
  await fs.writeFile(path.join(out,'guard-observation.json'),JSON.stringify({delivered,error:error?{name:error.name,code:error.code,message:error.message,stack:error.stack}:null},null,2));
  assert.equal(error?.code,'COMPACTION_IDENTIFIER_ATTRIBUTION_UNSUPPORTED');assert.equal(delivered,0);
  let recovery=0;assert.equal(await ctx.waterfall('compaction/summary-error',{error},()=>{recovery++;return true}),false);assert.equal(recovery,0);
  assert.equal(JSON.stringify(options),before,'source unchanged');
  assert.equal((await collect(ctx.llm.stream({...options,purpose:'agent-loop'})))[0].text,text);
  checks.push('active guard holds unsupported summary before delivery, no recovery retry; ordinary stream passes');
  for(const [label,candidate,held] of [
   ['supported','User supplied TASK_1234 TASK_5678. Assistant wrote a generic guide.',false],
   ['explicit','Assistant produced a guide containing TASK_1234 and TASK_5678.',true],
   ['coreference','The assistant guide preserved both identifiers.',true]
  ]){
   mockText=candidate;const session=Session.create(SessionId('plugin-disposable-'+label));
   for(let turn=1;turn<=3;turn++){
    session.append('turn/start',{turn});
    session.append('user/message',createUserMessage({content:[{type:'text',text:'User supplied TASK_1234 TASK_5678. '.repeat(80)}],source:{kind:'user'}}),{surfaceOp:'append'});
    session.append('step/start',{turn,step:1});
    session.append('assistant/message',{turn,step:1,stream:[],message:createMessage({role:'assistant',content:[{type:'text',text:'Assistant wrote a generic guide without identifiers. '.repeat(80)}],source:{kind:'model',provider:'synthetic',model:'synthetic'}})},{surfaceOp:'append'});
    session.append('step/end',{turn,step:1});session.append('turn/end',{turn,reason:{kind:'completed'}});
   }
   session.append('turn/start',{turn:4});
   const nodes=[...session.surface.nodes],events=JSON.stringify(session.snapshotEvents()),generation=session.surface.replaceGeneration,beforeCalls=calls;
   let error,result;try{result=await ctx.compaction.compactRegion(nodes[0],nodes[3],{session,options:{provider:'synthetic',model:'synthetic'}},new AbortController().signal)}catch(e){error=e;}
   assert.equal(calls,beforeCalls+1,'one native summary attempt');
   if(held){assert.match(error?.code??'',/^COMPACTION_IDENTIFIER_ATTRIBUTION_/);assert.equal(session.surface.replaceGeneration,generation);assert.equal(JSON.stringify(session.snapshotEvents().slice(0,JSON.parse(events).length)),events);assert.equal(session.snapshotEvents().filter(e=>e.type==='compaction/summary').length,0);}
   else{assert.ifError(error);assert.ok(result);assert.equal(session.surface.replaceGeneration,generation+1);assert.ok(session.snapshotEvents().some(e=>e.type==='compaction/summary'));}
   checks.push('native compaction '+label+': '+(held?'held without checkpoint/history replacement':'checkpoint adopted'));
  }
  mockText=text;
 }else{
  assert.equal(entry,undefined,'uninstall removes plugin entry');
  assert.equal((await collect(ctx.llm.stream(options)))[0].text,text,'baseline restored without guard');
  checks.push('uninstalled profile starts with baseline stream and no plugin entry');
 }
 await ctx.fiber.dispose();ctx=undefined;
}
try{
 await cli(['add',pkg,'--ignore-scripts']);
 const installed=JSON.parse(await fs.readFile(path.join(profileDir,'package.json')));
 assert.ok(installed.dsh.profile.bundles.includes('dsh-compaction-provenance-guard'));
 checks.push('actual DSH CLI add persisted bundle registration');
 await start(true);
 await cli(['remove','dsh-compaction-provenance-guard']);
 const removed=JSON.parse(await fs.readFile(path.join(profileDir,'package.json')));
 assert.ok(!removed.dsh.profile.bundles.includes('dsh-compaction-provenance-guard'));
 assert.equal(removed.dependencies?.['dsh-compaction-provenance-guard'],undefined);
 await start(false);
 await fs.writeFile(path.join(profileDir,'package.json'),initial);
 for(const name of ['pnpm-lock.yaml','pnpm-workspace.yaml'])await fs.unlink(path.join(profileDir,name)).catch(e=>{if(e.code!=='ENOENT')throw e});
 assert.equal(await fs.readFile(path.join(profileDir,'package.json'),'utf8'),initial);
 assert.equal(sha(await fs.readFile(path.join(home,'history-sentinel'))),sha(sentinel));
 checks.push('exact original profile manifest restored and disposable sentinel unchanged');
 const sources={};for(const name of ['dsh-app-boot','dsh-llm','dsh-compaction-basic'])sources[name]=sha(await fs.readFile(req.resolve('@deepseek-ai/'+name)));
 assert.deepEqual(sources,sourceBefore,'official module files unchanged');
 const result={passed:true,dshVersion:manifest.version,nodeVersion:process.version,platform:process.platform,scope:'Actual DSH CLI add/remove and native profile boot/disposal with synthetic adapter; no browser/full application/model-provider test',install:'passed',start:'passed',uninstall:'passed',rollback:'passed',checks,mockCalls:calls,realModelCalls:0,liveDeploymentChanges:0,sourceHashes:sources};
 await fs.writeFile(path.join(out,'result.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
}finally{if(ctx)await ctx.fiber.dispose();await fs.writeFile(path.join(out,'commands.json'),JSON.stringify(commands,null,2));}
