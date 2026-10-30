'use strict';
const crypto=require('node:crypto');
const sha=text=>crypto.createHash('sha256').update(text).digest('hex');
// Limited visible-text guard: this does not establish general semantic faithfulness.
const GUARD_LIMITS = Object.freeze({ textBytes:33554432, messages:100000, identifiers:65536, summaryChars:262144 });
function opaqueIds(text) {
  const found=new Set();
  for(const hit of text.matchAll(/[A-Za-z0-9][A-Za-z0-9_.:-]{5,254}[A-Za-z0-9]/g)) {
    const value=hit[0];
    if((value.includes('_')&&/[A-Z0-9]/.test(value))||(/[-:]/.test(value)&&/[0-9]/.test(value)))found.add(value);
  }
  return found;
}
function unquoteClaims(line) {
  let out='';
  for(let i=0;i<line.length;i++) {
    const ch=line[i];
    if((ch==='"'||ch==='“'||(ch==="'"&&!/[A-Za-z0-9]/.test(line[i-1]||'')))) {
      const end=line.indexOf(ch==='“'?'”':ch,i+1);
      if(end>i&&/\s/.test(line.slice(i+1,end))){out+=' ';i=end;continue;}
    }
    out+=ch;
  }
  return out;
}
function identifierClaims(text) {
  const claims=[];let fence=false;
  for(const raw of text.split('\n')) {
    if(/^\s*(?:\x60\x60\x60|~~~)/.test(raw)){fence=!fence;continue;}
    if(fence||/^\s*>/.test(raw))continue;
    for(const line of unquoteClaims(raw).split(/[.!?;]\s+/)) {
    if(/^\s*(?:[-*]\s*)?(?:User\s+(?:rejected|denied|disputed|corrected)\s+(?:the\s+)?assistant\s+(?:assertion|claim)|It\s+is\s+false\s+that)\b/i.test(line))continue;
    const actor=/\bassistant\b.{0,240}\b(?:produced|delivered|wrote|written|provided|generated|prepared|created)\b/i.test(line)||(/\b(?:guide|answer|response|output|report)\b/i.test(line)&&/\b(?:was|were)\s+(?:produced|delivered|written|provided|generated|prepared|created)\b/i.test(line)&&/\b(?:assistant\s+(?:assertion|claim|output)|produced\s+by\s+(?:the\s+)?assistant)\b/i.test(line))||/\bassistant(?:(?:'s)?\s+(?:guide|answer|output|response|report))?\s+(?:did\s+not\s+|didn't\s+|never\s+)?(?:included|preserved|contained|mentioned|incorporated)\b/i.test(line);
    if(!actor)continue;
    const included=/\b(?:includ(?:e[sd]?|ing)|incorporat(?:e[sd]?|ing)|preserv(?:e[sd]?|ing)|contain(?:s|ed|ing)?|mention(?:s|ed|ing)?|carr(?:y|ies|ied|ying)|ke(?:ep|eps|pt|eping))\b/ig;
    let positive=false;
    for(const m of line.matchAll(included)) {
      const prefix=line.slice(Math.max(0,m.index-45),m.index);
      if(/(?:\b(?:not|never|without)\s+(?:\w+\s+){0,2}|\b(?:did|does|is|was|has|had)n't\s+)$/.test(prefix.toLowerCase()))continue;
      positive=true;
    }
    if(positive&&(opaqueIds(line).size||/\b(?:identifiers?|references?|ids|markers?)\b/i.test(line)))claims.push({line,ids:opaqueIds(line),all:/\ball\s+(?:(?:of\s+)?the\s+)?(?:identifiers?|references?|ids|markers?)\b/i.test(line),both:/\b(?:both|(?:the\s+)?two)\s+(?:identifiers?|references?|ids|markers?)\b/i.test(line)});
    }
  }
  return claims;
}
function assessIdentifierAttribution(messages,summary) {
  const text=summary.filter(b=>b.type==='text').map(b=>b.text).join('\n');
  if(text.length>GUARD_LIMITS.summaryChars)return {status:'unknown',reason:'summary-budget',coverage:'limited-identifier-attribution'};
  const claims=identifierClaims(text);if(!claims.length)return {status:'not-applicable',coverage:'limited-identifier-attribution'};
  if(!Array.isArray(messages)||!messages.length||messages.length>GUARD_LIMITS.messages)return {status:'unknown',reason:'source-messages-unavailable-or-budget',coverage:'limited-identifier-attribution'};
  const users=new Set(),assistants=new Set();let bytes=0,visible=0,incomplete=false;
  for(const message of messages) {
    if(!['user','assistant','system','tool','developer'].includes(message.role)){incomplete=true;continue;}
    if(message.role!=='user'&&message.role!=='assistant')continue;
    const blocks=typeof message.content==='string'?[{type:'text',text:message.content}]:message.content;
    if(!Array.isArray(blocks)){incomplete=true;continue;}
    for(const block of blocks) {
      if(block.type!=='text'||typeof block.text!=='string'){if(message.role==='assistant')incomplete=true;continue;}
      bytes+=Buffer.byteLength(block.text,'utf8');if(bytes>GUARD_LIMITS.textBytes)return {status:'unknown',reason:'source-text-budget',coverage:'limited-identifier-attribution'};
      visible++;const target=message.role==='user'?users:assistants;
      for(const id of opaqueIds(block.text)){target.add(id);if(target.size>GUARD_LIMITS.identifiers)return {status:'unknown',reason:'identifier-budget',coverage:'limited-identifier-attribution'};}
    }
  }
  if(!visible)return {status:'unknown',reason:'source-visible-text-unavailable',coverage:'limited-identifier-attribution'};
  const only=[...users].filter(id=>!assistants.has(id)),onlySet=new Set(only);let ambiguous=false;
  for(const claim of claims) {
    let missing=[...claim.ids].filter(id=>onlySet.has(id));
    if(!claim.ids.size) {
      if(claim.all){missing=only;if(!users.size&&!assistants.size)ambiguous=true;}
      else if(claim.both){
        if(users.size===2&&only.length===2&&assistants.size===0)missing=only;
        else if(only.length||assistants.size<2)ambiguous=true;
      }
      else if(only.length||!assistants.size)ambiguous=true;
    }
    if(missing.length) {
      if(incomplete)return {status:'unknown',reason:'assistant-source-not-fully-visible',coverage:'limited-identifier-attribution'};
      return {status:'unsupported',reason:'user-only-identifiers-attributed-to-assistant',coverage:'limited-identifier-attribution',claimSHA256:sha(claim.line),identifierSHA256:missing.map(sha),sourceTextBytes:bytes};
    }
    if([...claim.ids].some(id=>!users.has(id)&&!assistants.has(id)))ambiguous=true;
  }
  if(ambiguous)return {status:'unknown',reason:'identifier-reference-ambiguous-or-unavailable',coverage:'limited-identifier-attribution'};
  return {status:'clear',coverage:'limited-identifier-attribution',sourceTextBytes:bytes};
}
class CompactionProvenanceError extends Error {
  constructor(assessment,summary,rawOutput) {
    super('Compaction held: '+assessment.reason+'; original history is not replaced.');
    this.name='CompactionProvenanceError';this.code=assessment.status==='unsupported'?'COMPACTION_IDENTIFIER_ATTRIBUTION_UNSUPPORTED':'COMPACTION_IDENTIFIER_ATTRIBUTION_UNKNOWN';this.retryable=false;this.assessment=Object.freeze({...assessment});
    Object.defineProperty(this,'rejectedCandidate',{value:Object.freeze({summary:[...summary],rawOutput:[...rawOutput]}),enumerable:false});
  }
}
function isProvenanceHold(error) {return error instanceof CompactionProvenanceError;}
function assertSummaryProvenance(messages,summary,rawOutput) {
  const result=assessIdentifierAttribution(messages,summary);
  if(result.status==='unsupported'||result.status==='unknown')throw new CompactionProvenanceError(result,summary,rawOutput);
  return result;
}


module.exports={assessIdentifierAttribution,assertSummaryProvenance,isProvenanceHold,CompactionProvenanceError};
