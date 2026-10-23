const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../src/sage_faculty_twin/web/app.js'), 'utf8');
function fn(name) {
  const start=source.indexOf(`function ${name}(`);
  return source.slice(start,source.indexOf('\n}\n',start)+3);
}
const context={buildNativeExecutionHtml:()=>'<p>actual graph slot</p>',
  buildWorkAttemptHtml:()=>'<p>actual task log slot</p>',buildWorkflowChipRowHtml:()=>'',
  captureConversationScroll:()=>null,restoreConversationScroll(){}};
vm.createContext(context);
for(const name of ['escapeHtml','responseWorkEntries','buildWorkAttentionHtml','buildExecutionAttentionHtml','buildResponseWorkSummaryHtml',
  'buildWebResearchStatusHtml','buildWorkflowStatusSummaryHtml','consolidateMessageDiagnostics','updateMessageExecutionEvidence',
  'buildResponseEvidenceModel','buildResponseEvidenceHtml','buildNotificationStatusHtml','buildCollapsibleSupportSectionHtml','renderAssistantMessage']) {
  vm.runInContext(fn(name),context);
}
const extra='<div class="message-processing-meta">duration</div><p>full diagnostics</p>';
const normal=context.buildWorkflowStatusSummaryHtml([{key:'web_search',status:'skipped',summary:'offline'}],[],[],null,extra);
assert(normal.indexOf('offline')>normal.indexOf('<div class="message-section-content" hidden>'));
assert(normal.indexOf(extra)>normal.indexOf('<div class="message-section-content" hidden>'));
assert(normal.includes('<strong class="message-section-title">高级详情</strong>'));
const failed=context.buildWorkflowStatusSummaryHtml([{key:'web_search',status:'failed',summary:'honest failure'}]);
assert(failed.indexOf('honest failure')<failed.indexOf('<section class="message-workflow-summary'));
const execution={version:1,trace_id:'t',request_id:'r',program_uri:'p',program_rev:'v',node_id:'n',sequence:1,state:'failed'};
assert.match(context.buildExecutionAttentionHtml([execution]),/执行失败/);
assert.equal(context.buildExecutionAttentionHtml([execution,{...execution,sequence:2,state:'completed'}]),'','later successful retry clears old node failure');
assert(!context.buildExecutionAttentionHtml([{...execution,operator_key:'<script>'}]).includes('<script>'));
Object.assign(context,{assistantLabel:'Twin',INLINE_WORKFLOW_TRACE_ENABLED:true,isUserAuthenticated:false,
  stopAnswerProcessingTicker(){},hasMatchingSourceBlocks:()=>false,stripNotificationText:text=>text,
  parseStreamingThinkContent:text=>({main:text,think:''}),formatSourceAnswer:text=>text,
  buildFollowUpActionHtml:()=>'<p>explicit action</p>',buildFeedbackSectionHtml:()=>'',
  buildRetryButtonHtml:()=>'',uiIconSvg:()=>''});
function renderNotification(status,actions=[]){
  const container={dataset:{},style:{},classList:{remove(){},add(){}}};
  context.renderAssistantMessage(container,'original answer',[],actions,[],
    {notification:{status,summary:'notification record'}},false,null,[],false);
  return container.innerHTML;
}
for(const state of ['sent','queued']){
  const html=renderNotification(state);
  assert(html.indexOf('notification-card')>html.indexOf('<div class="message-section-content" hidden>'));
}
for(const state of ['failed','skipped','unknown']){
  const html=renderNotification(state);
  assert(html.indexOf('notification-card')<html.indexOf('<section class="message-workflow-summary'));
}
const suggested=renderNotification('sent',[{status:'suggested'}]);
assert(suggested.indexOf('message-section-follow-up')<suggested.indexOf('<section class="message-workflow-summary'));
const queued=renderNotification('sent',[{status:'queued'}]);
assert(queued.indexOf('message-section-follow-up')>queued.indexOf('<div class="message-section-content" hidden>'));

// Small tree fixture, with real move/contain/remove semantics. Execute the
// production migration and live updater; do not substitute their decisions.
class Node {
  constructor(classes='',dataset={}){this.classes=classes.split(' ');this.dataset=dataset;this.children=[];this.attrs={};this.hidden=false;}
  matches(selector){
    if(selector.includes(':not(.notification-card-failed)')&&this.classes.includes('notification-card-failed'))return false;
    selector=selector.replace(':not(.notification-card-failed)','');
    const classes=[...selector.matchAll(/\.([\w-]+)/g)].map(m=>m[1]);
    const attribute=/\[data-web-status="([^"]+)"\]/.exec(selector);
    return classes.every(c=>this.classes.includes(c))&&(!attribute||this.dataset.webStatus===attribute[1]);
  }
  querySelectorAll(selector){return this.children.flatMap(c=>[...(selector.split(',').some(s=>c.matches(s.trim()))?[c]:[]),...c.querySelectorAll(selector)]);}
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
  contains(node){return node===this||this.children.some(c=>c.contains(node));}
  append(...nodes){for(const n of nodes){n.remove();n.parent=this;this.children.push(n);}}
  remove(){if(this.parent){this.parent.children=this.parent.children.filter(n=>n!==this);this.parent=null;}}
  before(node){const parent=this.parent;node.remove();const at=parent.children.indexOf(this);parent.children.splice(at,0,node);node.parent=parent;}
  insertAdjacentHTML(position,html){
    let node;
    if(html.includes('class="response-work-attention"'))node=new Node('response-work-attention');
    else if(html.includes('class="response-execution-attention"'))node=new Node('response-execution-attention');
    else if(html.includes('class="response-status-attention"'))node=new Node('response-status-attention');
    else if(html.includes('class="response-work-summary"'))node=new Node('response-work-summary',{workAttention:/data-work-attention="([^"]+)"/.exec(html)[1]});
    else throw Error('unexpected HTML fixture: '+html);
    node.html=html;
    if(position==='beforebegin')this.before(node);
    else if(position==='afterend'){const parent=this.parent;const next=parent.children[parent.children.indexOf(this)+1];if(next)next.before(node);else parent.append(node);}
    else if(position==='afterbegin'){this.append(node);this.children.unshift(this.children.pop());}
    else throw Error(position);
  }
  set outerHTML(html){this.insertAdjacentHTML('beforebegin',html);this.remove();}
}
const message=new Node('message-ready'),frame=new Node('message-frame');message.append(frame);
const body=new Node('message-main-copy'),evidence=new Node('response-evidence');
const timing=new Node('message-processing-meta'),divider=new Node('message-reply-divider');
const web=new Node('web-research-status',{webStatus:'skipped'}),webFailure=new Node('web-research-status',{webStatus:'failed'});
const mail=new Node('notification-card notification-card-sent'),mailFailure=new Node('notification-card notification-card-failed');
const work=new Node('response-work-summary',{workAttention:'false'}),thinking=new Node('thinking-text-section');
const advanced=new Node('message-workflow-summary',{expanded:'false'}),content=new Node('message-section-content');
const title=new Node('message-section-title'),preview=new Node('message-inline-process-preview');
const native=new Node('message-native-execution');content.append(native);advanced.append(title,preview,content);
frame.append(body,evidence,timing,divider,web,work,mail,mailFailure,thinking,advanced);content.append(webFailure);
body.original='complete answer';evidence.original='complete source and original link';
context.consolidateMessageDiagnostics(message);
for(const node of [timing,divider,web,work,mail,thinking])assert(content.contains(node));
for(const node of [body,evidence,mailFailure,webFailure])assert.equal(node.parent,frame);
assert.equal(title.textContent,'高级详情');assert(preview.hidden);
advanced.dataset.expanded='true';
context.consolidateMessageDiagnostics(message);assert.equal(advanced.dataset.expanded,'true');
const event=state=>({version:1,namespace:'n',request_id:'r',task_id:'post_answer',attempt:1,state});
context.updateMessageExecutionEvidence(message,[],[event('failed')]);
assert.equal(frame.querySelector('.response-work-attention').parent,frame,'late failure escapes the folded detail');
assert(content.contains(frame.querySelector('.response-work-summary')));
assert.equal(advanced.dataset.expanded,'true','late events must not close or open details');
context.updateMessageExecutionEvidence(message,[],[event('failed')],{version:1,tasks:[{task_id:'post_answer',status:'done'}]});
assert.equal(frame.querySelector('.response-work-attention'),null,'authoritative recovery removes stale warning');
assert.equal(body.original,'complete answer');assert.equal(evidence.original,'complete source and original link');

// An old snapshot can already have actionable notices nested under the closed
// advanced section. Promote by typed state, never by guessing words in a reply.
const skippedMail=new Node('notification-card-skipped'),confirmation=new Node('message-section-follow-up');
const knownQueued=new Node('message-section-follow-up',{actionAttention:'false'});
const uncertainOperation=new Node('response-operation'),knownOperation=new Node('response-operation',{operationAttention:'false'});
content.append(mailFailure,skippedMail,confirmation,knownQueued,uncertainOperation,knownOperation);
advanced.dataset.expanded='false';content.hidden=true;
context.consolidateMessageDiagnostics(message);
for(const node of [mailFailure,skippedMail,confirmation,uncertainOperation])assert.equal(node.parent,frame);
for(const node of [knownQueued,knownOperation])assert.equal(node.parent,content);
assert.equal(advanced.dataset.expanded,'false');assert(content.hidden);

// A legacy answer without an evidence panel must still expose late failures.
evidence.remove();
for(const state of ['failed','blocked','unknown']) {
  context.updateMessageExecutionEvidence(message,[execution],[],{version:1,tasks:[{task_id:'post_answer',status:state}]});
  for(const cls of ['response-work-attention','response-execution-attention'])assert.equal(frame.querySelector('.'+cls).parent,frame);
  assert.equal(advanced.dataset.expanded,'false');assert(content.hidden);
}
// Exercise the production capture/restore functions around this real updater,
// with growing content and both closed and user-opened advanced details.
let height=5000;
context.chatStream={scrollTop:600,clientHeight:700,get scrollHeight(){return height;},children:[],getBoundingClientRect:()=>({top:0})};
context.conversationFollowingLatest=false;context.document={getElementById:()=>null};
for(const name of ['captureConversationScroll','restoreConversationScroll','syncLatestConversationButton'])vm.runInContext(fn(name),context);
Object.defineProperty(native,'innerHTML',{set(){height+=300;}});
for(const expanded of ['false','true']) {
  advanced.dataset.expanded=expanded;content.hidden=expanded==='false';
  for(const state of ['failed','done']) {
    context.updateMessageExecutionEvidence(message,[],[],{version:1,tasks:[{task_id:'post_answer',status:state}]});
    assert.equal(context.chatStream.scrollTop,600,'late work must not scroll history');
    assert.equal(advanced.dataset.expanded,expanded);assert.equal(content.hidden,expanded==='false');
  }
}
assert.match(suggested,/data-action-attention="true"/);
assert.match(queued,/data-action-attention="false"/);
console.log('Advanced details: full diagnostics folded, old nodes moved intact, web/mail/work exceptions exposed, live recovery and user open state preserved');

const hydrateStart=source.indexOf('async function hydrateExecutionEvidence(');
vm.runInContext(source.slice(hydrateStart,source.indexOf('\n}\n',hydrateStart)+3),context);
Object.assign(context,{executionHistoryGeneration:0,activeWorkflowMessage:null,resolveApiOrigin:async()=>'',
  apiRequest:async()=>{throw Error('synthetic unavailable');}});
const root={querySelectorAll:()=>[message],contains:node=>node===message};
message.dataset.exchangeId='synthetic';
(async()=>{
  advanced.dataset.expanded='false';content.hidden=true;
  await context.hydrateExecutionEvidence(root);
  assert.equal(frame.querySelector('.response-status-attention').parent,frame,'unverifiable status is not hidden');
  await context.hydrateExecutionEvidence(root);
  assert.equal(frame.querySelectorAll('.response-status-attention').length,1,'no duplicate warning');
  assert.equal(context.chatStream.scrollTop,600);assert(content.hidden);assert.equal(advanced.dataset.expanded,'false');
  context.apiRequest=async()=>({work_status:{version:1,tasks:[{task_id:'post_answer',status:'done'}]},stream_closed:true});
  await context.hydrateExecutionEvidence(root);
  assert.equal(frame.querySelector('.response-status-attention'),null,'verified snapshot clears unavailable warning');
  assert.equal(context.chatStream.scrollTop,600);assert(content.hidden);assert.equal(advanced.dataset.expanded,'false');
  const streams=[];
  context.restoredExecutionStreams=new Set();
  context.EventSource=class {constructor(){streams.push(this);}close(){this.closed=true;}};
  context.apiRequest=async()=>({work_status:{version:1,tasks:[{task_id:'post_answer',status:'running'}]},request_id:'synthetic',stream_closed:false});
  await context.hydrateExecutionEvidence(root);
  streams[0].onerror();
  assert.equal(frame.querySelector('.response-status-attention').parent,frame,'transport error is not hidden');
  assert.equal(context.chatStream.scrollTop,600);assert(content.hidden);
  streams[0].onmessage({data:JSON.stringify({type:'error'})});
  assert(streams[0].closed);assert.equal(context.restoredExecutionStreams.size,0);
  context.executionHistoryGeneration++;
  frame.querySelector('.response-status-attention').remove();
  streams[0].onerror();
  assert.equal(frame.querySelector('.response-status-attention'),null,'stale stream cannot create new notices');
  console.log('History status unavailable and recovery are visible, deduplicated and nonintrusive');
})().catch(error=>{console.error(error);process.exitCode=1;});
