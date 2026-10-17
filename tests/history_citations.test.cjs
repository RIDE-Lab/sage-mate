const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../src/sage_faculty_twin/web/app.js'), 'utf8');
const start = source.indexOf('async function repairHistoricalCitationCards(');
const end = source.indexOf('\nfunction hydrateConversationInteractiveState', start);
async function run(status, hasLink=true, headerText='Wiki: Source') {
  const detail = { innerHTML: 'OLD truncated', textContent: 'OLD', querySelector: () => hasLink ? { textContent:headerText, getAttribute: () => 'https://example.org/wiki' } : null };
  let removedLegacyNotice = false;
  const card = { dataset: {}, querySelectorAll: () => [{textContent:'已按当前公开来源恢复完整引用；原回答未改动。', remove: () => { removedLegacyNotice = true; }}], querySelector: selector => selector === '.message-basis-detail' ? detail : card.notice, appendChild: node => card.notice = node };
  const answer = 'Original answer stays byte-for-byte unchanged';
  const root = { answer, querySelectorAll: () => [card], contains: () => true };
  let calls = 0;
  const sandbox = { document: { createElement: () => ({dataset:{}}) },
    apiRequest: async url => { calls++; assert.ok(url.startsWith('/wiki/citation?')); return {status, content:'# Complete\n\nTail', notice:'current source'}; },
    formatMessageContent: text => `<rendered>${text}</rendered>` };
  vm.runInNewContext(source.slice(start, end), sandbox);
  await sandbox.repairHistoricalCitationCards(root);
  assert.equal(root.answer, answer);
  if (hasLink && headerText === 'Wiki: Source') assert.ok(removedLegacyNotice);
  if (!hasLink || headerText !== 'Wiki: Source') { assert.equal(calls,0); assert.equal(card.dataset.citationFormat,'unresolved'); }
  else if (status === 'restored') { assert.ok(detail.innerHTML.includes('Tail')); assert.equal(card.dataset.citationFormat,'restored-current'); await sandbox.repairHistoricalCitationCards(root); assert.equal(calls,2); assert.equal(detail.hidden,false); status='unavailable'; await sandbox.repairHistoricalCitationCards(root); assert.equal(detail.hidden,true); assert.equal(card.dataset.citationFormat,'unavailable'); }
  else { assert.equal(card.dataset.citationFormat,'unavailable'); assert.ok(detail.textContent.includes('无法恢复')); }
}
(async () => { await run('restored'); await run('unavailable'); await run('restored', false); await run('restored', true, 'Arbitrary document link'); console.log('Historical citation hydration passed without changing answers or invoking chat'); })().catch(error => { console.error(error); process.exitCode=1; });
