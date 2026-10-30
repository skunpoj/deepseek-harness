// Guard-only leaf: preserves existing compaction engine, instruction and framing.
import quality from './managed_compaction_quality.cjs';
export const inject=['llm','compaction'];
const DIRECTIVE='You are now acting as a compaction engine for this AI coding assistant.';
const hold=reason=>new quality.CompactionProvenanceError({status:'unknown',reason},[],[]);
const MAX_CHUNKS=131072,MAX_TEXT=262144,MAX_BYTES=4*1024*1024;
// Inspect only text used by the provenance check. Forward original chunks,
// including provider errors/replay data, without rebuilding the host response.
export function candidateText(chunks){
 const blocks=new Map();let finish='unknown';
 for(const chunk of chunks){
  if(chunk.type==='finish'){finish=chunk.reason?.kind;continue;}
  if(chunk.type==='usage')continue;
  if(!['block-start','text-delta','reasoning-delta','tool-call-delta','block-end'].includes(chunk.type))throw hold('Unknown compaction stream chunk');
  if(!Number.isInteger(chunk.index)||chunk.index<0)throw hold('Invalid compaction stream index');
  if(!blocks.has(chunk.index))blocks.set(chunk.index,{type:chunk.blockType??(chunk.type==='text-delta'?'text':chunk.type==='reasoning-delta'?'reasoning':chunk.type==='tool-call-delta'?'tool-call':chunk.block?.type),text:'',closed:false});
  const block=blocks.get(chunk.index);
  if(block.closed)continue;
  if(chunk.type==='block-end'){block.type=chunk.block?.type;block.text=chunk.block?.text??'';block.closed=true;}
  else if(chunk.type==='text-delta'||chunk.type==='reasoning-delta'){
   if(typeof chunk.text!=='string')throw hold('Invalid compaction text delta');
   block.text+=chunk.text;
  }
 }
 const summary=[...blocks.values()].filter(b=>b.type==='text').map(b=>({type:'text',text:b.text}));
 if(summary.some(b=>typeof b.text!=='string'))throw hold('Invalid compaction text block');
 return {finish,summary};
}
export default function compactionProvenanceLeaf(ctx){
 ctx.on('llm/stream',(options,next)=>{
  if(options.purpose!=='compaction')return next();
  return (async function*(){
   const messages=options.messages;
   if(!Array.isArray(messages)||messages.length===0)throw hold('Compaction guard requires visible source messages');
   const final=messages.at(-1);
   const directive=final?.role==='user'&&Array.isArray(final.content)&&final.content.some(b=>b.type==='text'&&b.text.startsWith(DIRECTIVE));
   if(!directive)throw hold('Compaction guard refuses unknown compaction input contract');
   const source=messages.slice(0,-1),chunks=[];let text=0,bytes=0;
   for await(const chunk of next()){
    options.signal?.throwIfAborted();
    if(chunks.length>=MAX_CHUNKS)throw hold('Compaction guard chunk budget exceeded');
    const encoded=JSON.stringify(chunk);bytes+=Buffer.byteLength(encoded??'');
    if(bytes>MAX_BYTES)throw hold('Compaction guard buffered stream budget exceeded');
    if(chunk.type==='text-delta'||chunk.type==='reasoning-delta')text+=chunk.text.length;
    if(text>MAX_TEXT)throw hold('Compaction guard text budget exceeded');
    chunks.push(chunk);
   }
   options.signal?.throwIfAborted();
   const {finish,summary}=candidateText(chunks);
   // Preserve provider failure semantics. Only complete, textual candidates reach provenance adoption checks.
   if(!['error','aborted','max-tokens'].includes(finish)&&summary.some(b=>b.text.trim()))quality.assertSummaryProvenance(source,summary,chunks);
   for(const chunk of chunks){options.signal?.throwIfAborted();yield chunk;}
  })();
 },{prepend:true});
 ctx.on('compaction/summary-error',(event,next)=>quality.isProvenanceHold(event.error)?false:next(),{prepend:true});
}
