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
console.log('Advanced details: full diagnostics folded, old nodes moved intact, web/mail/work exceptions exposed, live recovery and user open state preserved');
