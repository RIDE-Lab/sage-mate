const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../src/sage_faculty_twin/web/app.js'), 'utf8');
const implementation = source.slice(source.indexOf('function chatCancellationMessage('),
  source.indexOf('globalThis.addEventListener("pagehide"'));
assert.ok(implementation.includes('timeoutMs: 35000'));
const sandbox = {activeWorkflowRequestId: 'request', activeChatUserCancelled: false,
  activeChatCancellationPromise: null, activeChatAbortController: {abort() {}},
  stopWorkflowTraceStream() {}, apiRequest: async () => ({request_id: 'request', status: 'cancelled', cancelled: true})};
vm.createContext(sandbox);
vm.runInContext(implementation, sandbox);

async function run() {
  assert.match(sandbox.chatCancellationMessage({status: 'cancelled', cancelled: true}), /已确认/);
  for (const outcome of [null, {}, {status: 'cancelling', cancelled: false},
    {status: 'protected'}, {status: 'terminal'}, {cancelled: true}]) {
    assert.doesNotMatch(sandbox.chatCancellationMessage(outcome), /已确认停止/);
  }
  assert.equal((await sandbox.cancelActiveChatRequest()).cancelled, true);
  sandbox.apiRequest = async () => ({request_id: 'another', status: 'cancelled', cancelled: true});
  assert.equal(await sandbox.cancelActiveChatRequest(), null);
  sandbox.apiRequest = async () => {throw new Error('network failed');};
  assert.equal(await sandbox.cancelActiveChatRequest(), null);

  const start = source.indexOf('            if (error?.cancelled || activeChatUserCancelled) {');
  const end = source.indexOf('            // 429 can mean', start);
  let settle;
  const rendered = [];
  sandbox.activeChatCancellationPromise = new Promise(resolve => {settle = resolve;});
  Object.assign(sandbox, {error: {cancelled: true}, pendingMessage: {}, explicitDeepMode: false,
    activeWorkflowSteps: [], activeExecutionEvents: [], activeWorkEvents: [], sageCompanionController: null,
    renderPendingAssistantMessage: (_, text) => rendered.push(text),
    renderWorkflowTraceError() {}, renderAssistantMessage: (_, text) => rendered.push(text)});
  const pending = vm.runInContext(`(async () => {for (let i=0;i<1;i++){${source.slice(start, end)}}})()`, sandbox);
  await Promise.resolve();
  assert.equal(rendered.length, 1);
  assert.match(rendered[0], /等待后台确认/);
  settle({status: 'cancelling', cancelled: false});
  await pending;
  assert.match(rendered[1], /暂不能保证任务已停止/);
  console.log('Cancellation UI waits for durable ack and never treats local abort, pending or failure as confirmed');
}
const watchdog = setTimeout(() => {console.error('Cancellation acceptance did not settle'); process.exitCode = 1;}, 1500);
run().then(() => clearTimeout(watchdog), error => {
  clearTimeout(watchdog); console.error(error); process.exitCode = 1;
});
