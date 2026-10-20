const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../src/sage_faculty_twin/web/app.js'), 'utf8');
const context = {submittedFeedbackExchangeIds:new Set(), uiIconSvg:()=>'<svg></svg>', chatStream:{contains:()=>true}, restoreFeedbackExchange:()=>{}};
for (const name of ['escapeHtml','buildCollapsibleSupportSectionHtml','buildFeedbackSectionHtml','handleMessageSectionEscape','attachFeedbackHandlers']) {
  const start=source.indexOf(`function ${name}(`);
  const next=/\n(?:async )?function /.exec(source.slice(start+1));
  const end=next ? start+1+next.index : source.length;
  assert.ok(start>=0); vm.runInNewContext(source.slice(start,end),context);
}
const markdown='<p>full source</p>'.repeat(1000);
const html=context.buildCollapsibleSupportSectionHtml({kicker:'Support', title:'本次回答依据', count:3, copy:'qualification', contentHtml:markdown, compact:true});
assert.ok(html.includes('message-section-compact'));
assert.ok(html.includes('aria-expanded="false"'));
assert.ok(html.includes('message-section-content" hidden'));
assert.ok(html.includes(markdown)); assert.ok(!html.includes('message-section-kicker'));
const missing=context.buildCollapsibleSupportSectionHtml({kicker:'Support',title:'本次未引用可核验材料', count:0,contentHtml:'gap'});
assert.ok(missing.includes('本次未引用可核验材料')); assert.ok(missing.includes('message-section-kicker'));
let clicked=0, focused=0, prevented=0;
const section={querySelector:()=>({click(){clicked++;},focus(){focused++;}})};
const event={key:'Escape',target:{closest:()=>section},preventDefault(){prevented++;},stopPropagation(){}};
context.handleMessageSectionEscape(event);
assert.equal(clicked,1); assert.equal(focused,1); assert.equal(prevented,1);
context.handleMessageSectionEscape({...event,key:'Enter'}); assert.equal(clicked,1);
const feedback=context.buildFeedbackSectionHtml('own-exchange',false);
assert.ok(feedback.includes('data-feedback-up')); assert.ok(feedback.includes('data-feedback-down'));
assert.ok(feedback.includes('message-feedback-form hidden')); assert.ok(feedback.includes('role="status"'));
assert.ok(!feedback.includes('这条回答有帮助吗')); assert.equal(context.buildFeedbackSectionHtml('own',true),'');
const handlers={}, status={textContent:''}; let returnedFocus=0, hidden=true;
const button=name=>({addEventListener(type,fn){handlers[name+type]=fn;},focus(){returnedFocus++;}});
const controls={'[data-feedback-up]':button('up'),'[data-feedback-down]':button('down'),'[data-feedback-submit]':button('submit'),'[data-feedback-cancel]':button('cancel'),
  '.message-feedback-form':{classList:{add(){hidden=true;},remove(){hidden=false;}}},'.message-feedback-status':status,'.message-feedback-textarea':{focus(){}}};
context.attachFeedbackHandlers({querySelector:selector=>controls[selector]},'own-exchange');
handlers.downclick(); assert.equal(hidden,false);
handlers.cancelclick(); assert.equal(hidden,true); assert.equal(status.textContent,''); assert.equal(returnedFocus,1);
console.log('Compact source/feedback actions retain full evidence, honest gaps and Escape focus restoration');
