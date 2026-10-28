const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../src/sage_faculty_twin/web/app.js'), 'utf8');
const start = source.indexOf('function handleRetryRequestClick(event)');
const end = source.indexOf('\nfunction buildRetryButtonHtml', start);
const handler = source.slice(start, end);
function test({changedOwner = false, busy = false, changedConversation = false} = {}) {
  const message = {}, questionMessage = {};
  const context = {requestId: 'stable-id', payload: {conversation_id: 'session-a', question: 'Original', deep_thinking: true},
    files: [{name:'fixture.txt'}], account: 'owner@example.test', message, questionMessage, originalQuestion: 'Original'};
  const button = {closest: () => message};
  const input = {dispatchEvent(){}};
  const scope = {activeChatAbortController: busy ? {} : null, currentUserAccountEmail: changedOwner ? 'other' : context.account,
    activeConversationId: changedConversation ? 'other-session' : 'session-a', chatStream:{contains:()=>true},
    chatRetryContexts: new WeakMap([[message, context]]), document:{getElementById:()=>input},
    Event: class {}, chatSubmitButton: {}, pendingChatRetry:null, lastFailedQuestion: 'Unrelated later error', submissions:[],
  };
  scope.chatForm = {requestSubmit(){scope.submissions.push(scope.pendingChatRetry);scope.activeChatAbortController = {};}};
  vm.runInNewContext(handler, scope);
  const event = {target:{closest:()=>button}};
  scope.handleRetryRequestClick(event);
  scope.handleRetryRequestClick(event);
  if (changedOwner || busy || changedConversation) {
    assert.equal(scope.submissions.length, 0);
  } else {
    assert.equal(scope.submissions.length, 1);
    assert.equal(scope.submissions[0], context);
    assert.equal(input.value, 'Original');
    // Execute the real submit payload/id selection against changed current controls.
    const payloadStart = source.indexOf('    const payload = retry?.payload || {');
    const payloadEnd = source.indexOf('    const requestController', payloadStart);
    const submit = {...scope, retry: context, pendingChatAttachments: [],
      createConversationId(){throw Error('retry must not allocate a new ID');}};
    vm.runInNewContext(source.slice(payloadStart, payloadEnd) + '\nglobalThis.result={payload, submittedFiles, workflowRequestId};', submit);
    assert.equal(submit.result.workflowRequestId, 'stable-id');
    assert.equal(submit.result.payload, context.payload);
    assert.equal(submit.result.submittedFiles, context.files);
  }
}
test(); test({changedOwner:true}); test({busy:true}); test({changedConversation:true});
console.log('Manual retry keeps the original identity, immutable payload, files and message; double click/identity change blocked.');
const friendlyStart = source.indexOf('function friendlyHttpErrorMessage(');
const friendlyEnd = source.indexOf('\nfunction setInlineStatus', friendlyStart);
const scope = {};
vm.runInNewContext(source.slice(friendlyStart, friendlyEnd), scope);
assert.match(scope.friendlyHttpErrorMessage(503, '', 'answer_outcome_unknown'), /尚未核验/);
assert.doesNotMatch(scope.friendlyHttpErrorMessage(503, '', 'answer_outcome_unknown'), /稍后重试/);
