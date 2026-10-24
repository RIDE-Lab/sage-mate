const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../src/sage_faculty_twin/web/app.js'),'utf8');
const calls=[];
const context={captureConversationScroll:()=>600,restoreConversationScroll:x=>assert.equal(x,600),
  bookingProposalResults:new Map(),
  publishBookingInvalidation(){},
  async refreshBookingProposals(){},
  conversationHistoryScope:'fixture',
  persistActiveConversationSnapshot(){},buildNotificationStatusHtml:()=>'',document:{createTextNode:text=>text,querySelectorAll:()=>[]}};
vm.createContext(context);
for(const name of ['escapeHtml','buildBookingProposalHtml','buildBookingReceiptContent','rememberBookingObservation','handleBookingProposalClick']){
  const start=source.indexOf((name==='handleBookingProposalClick'?'async ':'')+'function '+name+'(');
  vm.runInContext(source.slice(start,source.indexOf('\n}\n',start)+3),context);
}
const draft={action_id:'sealed-id',status:'pending',payload:{student_name:'<script>',student_email:'synthetic@example.test',topic:'Synthetic',preferred_start:'2030-01-07T10:00:00',preferred_end:'2030-01-07T11:00:00'}};
const html=context.buildBookingProposalHtml(draft);
assert(html.includes('尚未提交'));assert(html.includes('确认并提交预约申请'));assert(html.includes('放弃草稿'));
assert(!html.includes('<script>'));assert.equal(calls.length,0,'rendering a draft must never submit');
assert(!context.buildBookingProposalHtml({...draft,status:'cancelled'}).includes('data-booking-proposal-confirm'));
assert(!context.buildBookingProposalHtml({...draft,status:'confirmed'}).includes('草稿已放弃'));
function fixture(cancel){
  const status={textContent:'',dataset:{},append(value){this.textContent+=value;}};
  const heading={textContent:'待你确认 · 尚未提交'};
  const operation={textContent:'操作状态 · 尚未提交'};
  const card={dataset:{bookingProposal:draft.action_id},querySelector:selector=>selector==='strong'?heading:status,querySelectorAll:()=>buttons,closest:selector=>selector==='.message-assistant'?{querySelector:()=>operation}:null};
  const button={disabled:false,closest:()=>card,hasAttribute:()=>cancel};
  const buttons=[button,{disabled:false}];
  let rendered=null;
  Object.defineProperty(card,'innerHTML',{get:()=>rendered ?? `<strong>${heading.textContent}</strong><p>${status.textContent}</p><button ${button.disabled?'disabled':''}>确认</button>`,set:value=>{rendered=value;}});
  return {card,status,heading,operation,buttons,event:{target:{closest:selector=>selector==='[data-booking-proposal-refresh]'?null:button}}};
}
(async()=>{
  let item=fixture(true);
  const pending=()=>({proposal:{...draft}});
  context.apiRequest=async(url,options)=>{if(!options.method)return pending();calls.push([url,JSON.parse(options.body)]);return {message:'已放弃预约草稿',proposal:{status:'cancelled'}};};
  await context.handleBookingProposalClick(item.event);
  assert.equal(calls.at(-1)[0],'/bookings/cancel-proposal');assert(item.card.innerHTML.includes('已放弃草稿'));
  assert(context.buildBookingProposalHtml(draft).includes('已放弃草稿'),'late canonical answer must not resurrect cancelled controls');
  assert(!context.buildBookingProposalHtml(draft).includes('data-booking-proposal-confirm'));
  item=fixture(false);
  context.apiRequest=async(url,options)=>{if(!options.method)return pending();calls.push([url,JSON.parse(options.body)]);throw Error('synthetic timeout');};
  await context.handleBookingProposalClick(item.event);
  assert(item.status.textContent.includes('可能已经登记'));assert(item.status.textContent.includes('重试本条确认'));
  assert.equal(item.status.dataset.state,'error');assert(item.buttons.every(button=>!button.disabled));
  assert(item.heading.textContent.includes('待核验'));assert(!item.heading.textContent.includes('尚未提交'));
  assert(item.operation.textContent.includes('待核验'));assert(!item.operation.textContent.includes('尚未提交'));
  assert(!context.buildBookingProposalHtml(draft).includes('尚未提交'));
  assert.deepEqual(calls.at(-1),['/bookings/confirm-proposal',{action_id:'sealed-id'}]);
  item=fixture(false);
  context.apiRequest=async(url,options)=>{if(!options.method)return pending();throw Object.assign(Error('草稿已过期，未提交'),{status:409,code:'confirmation_expired'});};
  await context.handleBookingProposalClick(item.event);
  assert(item.heading.textContent.includes('草稿已过期'));assert(!item.status.textContent.includes('可能已经登记'));
  assert(item.buttons.every(button=>button.disabled));
  for (const state of ['cancelled','expired','confirmed']) {
    item=fixture(false);let posts=0;
    context.apiRequest=async(url,options)=>{assert(!options.method);posts+=Number(!!options.method);
      return {accepted:state==='confirmed',message:'canonical '+state,
        proposal:{action_id:draft.action_id,status:state,execution_status:state==='confirmed'?'committed':undefined},
        ...(state==='confirmed'?{booking:{booking_id:'only-one',status:'待确认'}}:{})};};
    await context.handleBookingProposalClick(item.event);
    assert.equal(posts,0,'stale sibling tab must never POST a terminal action');
    assert(!item.card.innerHTML.includes('data-booking-proposal-confirm'));
  }
  item=fixture(false);let resolve;
  const live={dataset:{bookingProposal:draft.action_id},innerHTML:'late pending copy'};
  context.document.querySelectorAll=()=>[live];
  context.apiRequest=async(url,options)=>{if(!options.method)return pending();calls.push(['deferred']);return await new Promise(done=>{resolve=done;});};
  const inFlight=context.handleBookingProposalClick(item.event);await new Promise(setImmediate);const count=calls.length;
  assert(context.buildBookingProposalHtml(draft).includes('disabled'),'late render cannot enable an in-flight confirmation');
  await context.handleBookingProposalClick(item.event);assert.equal(calls.length,count,'double click cannot dispatch twice');
  resolve({accepted:false,message:'所选时间不可用，未登记。'});await inFlight;
  assert(item.card.innerHTML.includes('申请未被接受'));assert(!item.card.innerHTML.includes('申请已登记'));
  assert(live.innerHTML.includes('申请未被接受'),'receipt updates a card replaced by canonical rendering');
  item=fixture(false);
  const oldScope=context.handleBookingProposalClick(item.event);
  await new Promise(setImmediate);
  context.conversationHistoryScope='other-account';context.bookingProposalResults.clear();
  resolve({accepted:true,message:'old scope only'});await oldScope;
  assert.equal(context.bookingProposalResults.size,0,'in-flight result must not repopulate another identity cache');
  assert(!item.card.innerHTML.includes('old scope only'));
  console.log('Booking drafts: explicit consent only, cancellation, honest ambiguous failure, rejected results and duplicate-click fence passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
