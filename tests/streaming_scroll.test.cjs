const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../src/sage_faculty_twin/web/app.js'), 'utf8');
function productionFunction(name) {
    const start = source.indexOf(`function ${name}(`);
    assert(start >= 0, name);
    return source.slice(start, source.indexOf('\n}\n', start) + 3);
}
let height = 5000;
const stream = {scrollTop: 600, clientHeight: 700, get scrollHeight() {return height;},
    children: [], contains: () => true, getBoundingClientRect: () => ({top:0})};
const message = {dataset:{}, style:{}, classList:{remove(){},add(){}},
    set innerHTML(value) {height += 900;}, querySelector: () => null};
const context = {chatStream:stream, assistantLabel:'Twin', INLINE_WORKFLOW_TRACE_ENABLED:false,
    stopAnswerProcessingTicker(){}, hasMatchingSourceBlocks:()=>false,
    stripNotificationText:x=>x, parseStreamingThinkContent:x=>({main:x,think:''}),
    buildNotificationStatusHtml:()=>'', escapeHtml:x=>x, formatSourceAnswer:x=>x,
    uiIconSvg:()=>'', buildFeedbackSectionHtml:()=>'', buildRetryButtonHtml:()=>''};
vm.createContext(context);
if (source.includes('function captureConversationScroll(')) {
    vm.runInContext(productionFunction('captureConversationScroll') + productionFunction('restoreConversationScroll'), context);
}
vm.runInContext(productionFunction('renderAssistantMessage'), context);
for (const error of [false, true]) {
    stream.scrollTop = 600;
    context.renderAssistantMessage(message, '完整答案', [], [], [], null, error, null, [], false);
    assert.equal(stream.scrollTop, 600, 'completion/error must not pull history reader to bottom');
    stream.scrollTop = height - stream.clientHeight;
    context.renderAssistantMessage(message, '继续跟随', [], [], [], null, error, null, [], false);
    assert.equal(stream.scrollTop, height - stream.clientHeight, 'bottom reader must continue following');
}
console.log('Completion and error preserve history reading and resume following at bottom');

const growing = {set innerHTML(value) {height += 160;}, set textContent(value) {height += 80;},
    scrollHeight:200, scrollTop:0};
const section = {hidden:true, querySelector:()=>growing};
const pending = {querySelector: selector => selector === '#pending-thinking-text' ? section :
    selector === '#pending-typing-dots' ? {} : growing};
stream.querySelector = selector => selector.includes('thinking-trace-list') ? growing : pending;
Object.assign(context, {streamingAnswerBuffer:'', streamingThinkBuffer:'',
    formatMessageContent:x=>x, buildVisibleWorkflowSteps:()=>[{}],
    buildPendingWorkflowTraceCompactHtml:()=>'<p>progress</p>'});
for (const name of ['appendStreamingAnswerDelta','appendStreamingThinkDelta','syncPendingWorkflowTrace']) {
    vm.runInContext(productionFunction(name), context);
}
context.INLINE_WORKFLOW_TRACE_ENABLED = true;
for (const [name, args] of [['appendStreamingAnswerDelta',['流式回答']],
    ['appendStreamingThinkDelta',['推理更新']], ['syncPendingWorkflowTrace',[[{}]]]]) {
    stream.scrollTop = 600;
    context[name](...args);
    assert.equal(stream.scrollTop,600,name+' preserves reader');
    stream.scrollTop = height-stream.clientHeight;
    context[name](...args);
    assert.equal(stream.scrollTop,height-stream.clientHeight,name+' follows latest');
}
// An earlier expanded evidence card grows above the visible message.
let anchorY=900;
const anchor={getBoundingClientRect:()=>({top:anchorY-stream.scrollTop,bottom:anchorY-stream.scrollTop+200})};
stream.children=[anchor];
stream.scrollTop=600;
let snapshot=context.captureConversationScroll();
anchorY+=150;
context.restoreConversationScroll(snapshot);
assert.equal(stream.scrollTop,750,'preserve visible message offset when content above grows');
snapshot=context.captureConversationScroll();
anchorY+=100; stream.scrollTop+=100; // browser native scroll anchoring
context.restoreConversationScroll(snapshot);
assert.equal(stream.scrollTop,850,'do not double-correct native anchoring');
const previousStream=context.chatStream;
snapshot=context.captureConversationScroll();
context.chatStream={scrollTop:42};
context.restoreConversationScroll(snapshot);
assert.equal(context.chatStream.scrollTop,42,'stale snapshot must not scroll another session surface');
context.chatStream=previousStream;
console.log('Production deltas/progress: preserve reader, resume follow; anchor compensation and stale-root isolation passed');
