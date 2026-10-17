const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../src/sage_faculty_twin/web/app.js'), 'utf8');
const start = source.indexOf('async function repairHistoricalCitationCards(');
const end = source.indexOf('\nfunction hydrateConversationInteractiveState', start);
async function run(status, hasLink=true) {
  const detail = { innerHTML: 'OLD truncated', textContent: 'OLD', querySelector: () => hasLink ? { getAttribute: () => 'https://example.org/wiki' } : null };
  const card = { dataset: {}, querySelector: () => detail, appendChild: node => card.notice = node.textContent };
  const answer = 'Original answer stays byte-for-byte unchanged';
  const root = { answer, querySelectorAll: () => [card], contains: () => true };
  let calls = 0;
  const sandbox = { document: { createElement: () => ({}) },
    apiRequest: async url => { calls++; assert.ok(url.startsWith('/wiki/citation?')); return {status, content:'# Complete\n\nTail', notice:'current source'}; },
    formatMessageContent: text => `<rendered>${text}</rendered>` };
  vm.runInNewContext(source.slice(start, end), sandbox);
  await sandbox.repairHistoricalCitationCards(root);
  assert.equal(root.answer, answer);
  if (!hasLink) { assert.equal(calls,0); assert.equal(card.dataset.citationFormat,'unresolved'); }
  else if (status === 'restored') { assert.ok(detail.innerHTML.includes('Tail')); assert.equal(card.dataset.citationFormat,'restored-current'); }
  else { assert.equal(card.dataset.citationFormat,'unavailable'); assert.ok(detail.textContent.includes('无法恢复')); }
}
(async () => { await run('restored'); await run('unavailable'); await run('restored', false); console.log('Historical citation hydration passed without changing answers or invoking chat'); })().catch(error => { console.error(error); process.exitCode=1; });
