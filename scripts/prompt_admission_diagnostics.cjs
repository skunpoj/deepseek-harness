'use strict';
// Upstream reports every unexpected prompt-admission failure (for example an
// image that cannot be stored or a model lookup error) as
// "prompt rejected (session/agent-busy)" and logs nothing. Keep the error code
// for client compatibility, but add a short redacted cause to the message and
// log the redacted stack to the container log.
const QUOTES = ['"', "'"];
const original = q => `throw new RemoteError(${q}session/agent-busy${q}, ${q}prompt rejected${q}, { reason: String(error) });`;
const replacement = q => `{ const dshDetail = String(error && error.message || error).replace(/(token|key|secret|password)=\\S+/gi, ${q}$1=<redacted>${q}).slice(0, 240); console.error(${q}[dsh] prompt admission failed: ${q} + (error && error.stack ? String(error.stack).replace(/(token|key|secret|password)=\\S+/gi, ${q}$1=<redacted>${q}).slice(0, 2000) : dshDetail)); throw new RemoteError(${q}session/agent-busy${q}, ${q}prompt rejected: ${q} + dshDetail, { reason: String(error) }); }`;

/**
 * Rewrite the single prompt-admission throw in one session-controller module.
 * @param {string} source module text
 * @returns {{source: string, changed: boolean}} rewritten text; unchanged when already applied
 * @throws {Error} PROMPT_ADMISSION_SOURCE_DRIFT unless exactly one original or rewritten throw exists
 */
function rewritePromptAdmission(source) {
  let changed = false, matched = 0;
  for (const q of QUOTES) {
    const before = source.split(original(q)).length - 1;
    const after = source.split(replacement(q)).length - 1;
    if (before === 0 && after === 0) continue;
    if (before > 1 || after > 1 || (before === 1 && after === 1)) throw new Error('PROMPT_ADMISSION_SOURCE_DRIFT');
    if (before === 1) { source = source.replace(original(q), replacement(q)); changed = true; }
    matched += 1;
  }
  if (matched !== 1) throw new Error('PROMPT_ADMISSION_SOURCE_DRIFT');
  return { source, changed };
}

module.exports = { rewritePromptAdmission };
