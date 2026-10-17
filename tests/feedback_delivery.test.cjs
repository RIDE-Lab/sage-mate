const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(process.argv[2] || path.join(__dirname, '../src/sage_faculty_twin/web/app.js'), 'utf8');
const start = source.indexOf('async function submitFeedbackExchange(');
const end = source.indexOf('\nfunction renderWorkflowTrace(', start);
assert.ok(start >= 0 && end > start);
const code = source.slice(start, end);
function setup(apiRequest) {
  let sequence = 0;
  const sandbox = {apiRequest, feedbackAttempts: new Map(), submittedFeedbackExchangeIds: new Set(),
    crypto: {randomUUID: () => `request-${++sequence}`}, setTimeout: resolve => resolve()};
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox);
  const ui = {container: {isConnected: true}, status: {}, upButton: {}, downButton: {},
    form: {classList: {add() {}}}, submittedMessage: 'Saved'};
  return {sandbox, ui, run: payload => sandbox.submitFeedbackExchange('exchange', payload, ui)};
}
function pending() {
  return Object.assign(new Error('pending'), {status: 503, responseBody: JSON.stringify({
    detail: {code: 'feedback_knowledge_pending', feedback_saved: true, operation_id: 'op'}})});
}
const result = {exchange_id: 'exchange', knowledge_write_backs: [{document_id: 'doc'}]};
(async () => {
  const calls = [];
  let fail = true;
  const retry = setup(async (url, options) => {
    calls.push(JSON.parse(options.body));
    if (fail) { fail = false; throw new Error('network'); }
    return result;
  });
  await retry.run({rating: 'up'});
  assert.equal(retry.ui.upButton.disabled, false);
  await retry.run({rating: 'up'});
  assert.equal(calls[0].request_id, calls[1].request_id);
  assert.equal(retry.ui.upButton.disabled, true);

  let posts = 0, gets = 0;
  const delivery = setup(async (url, options) => {
    if (options) { posts++; throw pending(); }
    gets++;
    assert.equal(url, '/chat/feedback/op?exchange_id=exchange');
    return {operation_id: 'op', status: gets === 1 ? 'pending' : 'completed', feedback: result};
  });
  await delivery.run({rating: 'up'});
  assert.equal(posts, 1); assert.equal(gets, 2);
  assert.ok(delivery.ui.status.textContent.includes('1 条'));

  let release;
  const duplicate = setup(() => new Promise(resolve => { release = resolve; }));
  const first = duplicate.run({rating: 'up'});
  await duplicate.run({rating: 'up'});
  assert.equal(duplicate.sandbox.feedbackAttempts.size, 1);
  release(result); await first;

  const detached = setup(async () => { throw pending(); });
  detached.ui.container.isConnected = false;
  await detached.run({rating: 'up'});
  assert.equal(detached.sandbox.submittedFeedbackExchangeIds.size, 0);
  assert.equal(detached.ui.upButton.disabled, false);

  let attempts = 0;
  const bounded = setup(async (url, options) => {
    if (options) { attempts++; throw pending(); }
    return {operation_id: 'op', status: 'pending', feedback: result};
  });
  await bounded.run({rating: 'up'});
  assert.ok(bounded.ui.status.textContent.includes('不会重复提交'));
  await bounded.run({rating: 'up'});
  assert.equal(attempts, 1);
  assert.equal(bounded.sandbox.submittedFeedbackExchangeIds.size, 0);
  let postCount = 0, failRead = true;
  const readRetry = setup(async (url, options) => {
    if (options) { postCount++; throw pending(); }
    if (failRead) { failRead = false; throw new Error('offline'); }
    return {operation_id: 'op', status: 'completed', feedback: result};
  });
  await readRetry.run({rating: 'up'});
  assert.ok(readRetry.ui.status.textContent.includes('反馈已保存'));
  await readRetry.run({rating: 'up'});
  assert.equal(postCount, 1);
  assert.equal(readRetry.ui.upButton.disabled, true);

  let finish;
  const logout = setup(() => new Promise(resolve => { finish = resolve; }));
  const inFlight = logout.run({rating: 'up'});
  logout.sandbox.feedbackAttempts.clear();
  finish(result); await inFlight;
  assert.equal(logout.sandbox.submittedFeedbackExchangeIds.size, 0);
  console.log('Feedback delivery: stable retry, polling, duplicate suppression, detach and bounded resume passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
