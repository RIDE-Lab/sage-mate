const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../src/sage_faculty_twin/web/app.js'),'utf8');
let live=[],calls=[];
const context={conversationHistoryScope:'owner-a',bookingProposalResults:new Map(),
 bookingProposalVersions:new Map(),
 captureConversationScroll:()=>321,restoreConversationScroll:n=>assert.equal(n,321),
 persistActiveConversationSnapshot(){},buildNotificationStatusHtml:()=>'',
 document:{querySelectorAll:()=>live,createElement(){let html='';return {
  set innerHTML(value){html=value;},get firstElementChild(){return {innerHTML:html.replace(/^<section[^>]*>/,'').replace(/<\/section>$/,'')};}};}}};
vm.createContext(context);
for(const name of ['escapeHtml','buildBookingProposalHtml','buildBookingReceiptContent','rememberBookingObservation','refreshBookingProposals']){
 const start=source.indexOf((name==='refreshBookingProposals'?'async ':'')+'function '+name+'(');
 vm.runInContext(source.slice(start,source.indexOf('\n}\n',start)+3),context);
}
function card(){return {dataset:{bookingProposal:'same-action'},isConnected:true,
 innerHTML:'<strong>待你确认</strong><button data-booking-proposal-confirm>确认</button>',
 matches:()=>true,closest:()=>null};}
function response(status,extra={}){return {accepted:false,message:'authoritative',
 proposal:{action_id:'same-action',status,payload:{}},...extra};}
function api(handler){context.apiRequest=async(url,options)=>{
 calls.push(url);assert.equal(url,'/bookings/proposals/same-action');assert(!options.method,'status inspection must never POST');
 return handler();};}
(async()=>{
 let c=card();live=[c];api(()=>response('cancelled'));
 await context.refreshBookingProposals(c);
 assert(c.innerHTML.includes('已放弃草稿'));assert(!c.innerHTML.includes('data-booking-proposal-confirm'));
 context.bookingProposalResults.clear(); // Actual reload loses the in-page cache.
 c=card();live=[c];await context.refreshBookingProposals(c);
 assert(c.innerHTML.includes('已放弃草稿'),'stale historical pending HTML is not authoritative');
 c=card();live=[c];context.bookingProposalResults.clear();api(()=>{throw Error('network down');});
 await context.refreshBookingProposals(c);
 assert(c.innerHTML.includes('状态待核验'));assert(c.innerHTML.includes('data-booking-proposal-refresh'));
 assert(!c.innerHTML.includes('data-booking-proposal-confirm'));
 assert(!context.buildBookingProposalHtml({action_id:'same-action',status:'pending'}).includes('data-booking-proposal-confirm'));
 api(()=>response('cancelled'));await context.refreshBookingProposals(c);
 assert(c.innerHTML.includes('已放弃草稿'));
 c=card();live=[c];context.bookingProposalResults.clear();api(()=>response('expired'));
 await context.refreshBookingProposals(c);assert(c.innerHTML.includes('草稿已过期'));
 assert(!c.innerHTML.includes('data-booking-proposal-confirm'));
 assert(!context.buildBookingProposalHtml({action_id:'expired-local',status:'pending',expires_at:1,payload:{}}).includes('data-booking-proposal-confirm'));
 c=card();live=[c];context.bookingProposalResults.clear();
 api(()=>response('confirmed',{accepted:true,booking:{booking_id:'one',status:'已确认'},
  proposal:{action_id:'same-action',status:'confirmed',execution_status:'committed'}}));
 await context.refreshBookingProposals(c);assert(c.innerHTML.includes('已确认'));assert(!c.innerHTML.includes('等待老师审核'));
 c=card();live=[c];context.bookingProposalResults.clear();
 api(()=>response('confirmed',{proposal:{action_id:'same-action',status:'confirmed',execution_status:'awaiting_result',payload:{}}}));
 await context.refreshBookingProposals(c);assert(c.innerHTML.includes('执行结果待核验'));
 assert(c.innerHTML.includes('重试同一申请'));assert(!c.innerHTML.includes('data-booking-proposal-cancel'));
 let resolves=[];c=card();const copy=card();live=[c,copy];context.bookingProposalResults.clear();
 api(()=>new Promise(resolve=>resolves.push(resolve)));
 const first=context.refreshBookingProposals(c),second=context.refreshBookingProposals(copy);
 assert(!c.innerHTML.includes('data-booking-proposal-confirm'));
 resolves[0](response('cancelled'));await first;resolves[1](response('pending'));await second;
 assert(copy.innerHTML.includes('已放弃草稿'),'late pending snapshot cannot override the newer cancelled observation');
 c=card();live=[c];context.bookingProposalResults.clear();resolves=[];
 const stale=context.refreshBookingProposals(c);context.conversationHistoryScope='owner-b';
 resolves[0](response('pending'));await stale;
 assert.equal(context.bookingProposalResults.size,0,'old account read must not populate new identity cache');
 assert(!c.innerHTML.includes('data-booking-proposal-confirm'));
 assert(calls.length>5);
 console.log('Booking history: authoritative read-only refresh, cancelled recovery, unknown fail-closed, committed/awaiting distinction, races and identity isolation passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
