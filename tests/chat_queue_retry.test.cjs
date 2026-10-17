const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../src/sage_faculty_twin/web/app.js'), 'utf8');
const start = source.indexOf('            if (canRetry) {');
const end = source.indexOf('            if ((error?.status === 429', start);
assert.ok(start > 0 && end > start);
const branch = source.slice(start, end);

async function verify(active) {
  const reopened = [];
  const sandbox = {
    canRetry: true, error: {status: 429, retryAfterSeconds: 2},
    pendingMessage: {}, explicitDeepMode: false, activeWorkflowStream: active,
    renderPendingAssistantMessage() {}, sageCompanionController: null,
    setTimeout: callback => callback(),
    createConversationId() { throw new Error('queued retry created a new identity'); },
    stopWorkflowTraceStream() { throw new Error('queued retry closed its live delivery observer'); },
    openWorkflowTraceStream: async id => reopened.push(id),
  };
  await vm.runInNewContext(`(async () => {
    const workflowRequestIdActive = 'stable-request';
    for (let attempt = 0; attempt < 1; attempt++) { ${branch} }
  })()`, sandbox);
  assert.deepEqual(reopened, active ? [] : ['stable-request']);
}
async function verifyExhaustedRetry() {
  const stop = source.indexOf('            if (streamingDeliveredResponse) break;', end);
  assert.ok(stop > end);
  const messages = [];
  const sandbox = {
    error: {status: 429}, isTransientNetworkError: false, activeWorkflowStream: {},
    streamingDeliveredResponse: null, explicitDeepMode: false, pendingMessage: {},
    renderPendingAssistantMessage: (_, message) => messages.push(message),
  };
  vm.runInNewContext(`for (let attempt = 0; attempt < 1; attempt++) {
    ${source.slice(end, stop)}
    throw new Error('durably queued request fell through to terminal error');
  }`, sandbox);
  assert.equal(messages.length, 1);
  assert.match(messages[0], /仍在排队/);
}
Promise.all([verify({}), verify(null), verifyExhaustedRetry()]).then(
  () => console.log('Queued retry preserves request identity and active event observer'),
  error => { console.error(error); process.exitCode = 1; },
);
