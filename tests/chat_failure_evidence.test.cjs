const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../src/sage_faculty_twin/web/app.js'), 'utf8');
const native = [{version:1,event_id:'native-failure',state:'failed'}];
const work = [{version:1,event_id:'durable-failure',state:'failed'}];
const rendered = [];
let stopped = 0;
const context = {activeExecutionEvents:native,activeWorkEvents:work,activeWorkflowSteps:[],
  activeWorkflowMessage:{},pendingMessage:{},streamingDeliveredResponse:null,
  payload:{question:'public question'},error:{code:'answer_validation_rejected',message:'quality rejected'},
  sageCompanionController:null,stopWorkflowTraceStream(){stopped++;},
  renderWorkflowTraceError(){},renderAssistantMessage(...args){rendered.push(args);},
  noteConversationAnswerPreview(){},persistActiveConversationSnapshot(){}};
vm.createContext(context);
const start=source.indexOf('            if (streamingDeliveredResponse) break;');
const end=source.indexOf('\n        }\n    }\n    setChatSubmitLoading',start);
vm.runInContext(`for(let i=0;i<1;i++){${source.slice(start,end)}}`,context);
assert.equal(stopped,0,'HTTP rejection must not close SSE before its ordered failure events');
assert.strictEqual(rendered[0][11],native);
assert.strictEqual(rendered[0][12],work);
const handler=source.slice(source.indexOf('function handleWorkflowStreamEvent('),source.indexOf('function handleWorkflowTraceToggle('));
vm.runInContext(handler,context);
context.handleWorkflowStreamEvent({type:'error',code:'answer_validation_rejected',message:'quality rejected'});
assert.equal(stopped,1,'durable terminal record closes stream');
assert.strictEqual(rendered[1][0],context.activeWorkflowMessage);
assert.strictEqual(rendered[1][11],native);
assert.strictEqual(rendered[1][12],work);
const friendlyStart=source.indexOf('function friendlyHttpErrorMessage(');
vm.runInContext(source.slice(friendlyStart,source.indexOf('\nfunction ',friendlyStart+1)),context);
assert.match(context.friendlyHttpErrorMessage(503,'secret upstream body','answer_validation_rejected'),/未通过来源/);
assert.doesNotMatch(context.friendlyHttpErrorMessage(503,'secret upstream body','answer_validation_rejected'),/secret|后端临时不可用/);
assert.match(context.friendlyHttpErrorMessage(503,'secret upstream body'),/后端临时不可用/);
let settled=0,visualSettled=0;
context.renderWorkflowTrace=()=>{};
context.sageCompanionController={setRequestActive(value){assert.equal(value,false);},
  recordAnswerCompleted(){settled++;return false;},
  setState(state,message){assert.equal(state,'idle');assert.equal(message,'回答已完成。');visualSettled++;}};
context.handleWorkflowStreamEvent({type:'complete'});
assert.equal(settled,1,'durable completion clears a late background thinking state');
assert.equal(visualSettled,1,'idempotent answer counting must not skip visual settlement');
console.log('Durable validation failure retains native evidence across HTTP/SSE ordering and safe classification');
