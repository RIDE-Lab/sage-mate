const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../src/sage_faculty_twin/web/app.js'),'utf8');
const init=source.slice(source.indexOf('const bookingProposalChannel ='),source.indexOf('\n})();',source.indexOf('const bookingProposalChannel ='))+6);
const blocked={BroadcastChannel:class {constructor(){throw Error('blocked');}}};
vm.createContext(blocked);assert.equal(vm.runInContext(init+'\nbookingProposalChannel',blocked),null);
const id='a'.repeat(64);let cards=[],reads=0,published=[];
const context={bookingProposalResults:new Map(),bookingProposalVersions:new Map(),conversationHistoryScope:'owner',
 bookingProposalChannel:{postMessage:value=>published.push(value)},localStorage:{setItem:(key,value)=>published.push(JSON.parse(value))},
 captureConversationScroll:()=>17,restoreConversationScroll:value=>assert.equal(value,17),persistActiveConversationSnapshot(){},
 buildNotificationStatusHtml:()=>'',document:{visibilityState:'visible',querySelectorAll:()=>cards,
 createElement(){let html='';return {set innerHTML(value){html=value;},get firstElementChild(){return {innerHTML:html.replace(/^<section[^>]*>/,'').replace(/<\/section>$/,'')};}};}}};
vm.createContext(context);
for(const name of ['escapeHtml','buildBookingProposalHtml','buildBookingReceiptContent','rememberBookingObservation',
 'publishBookingInvalidation','receiveBookingInvalidation','refreshVisibleBookingProposals','refreshBookingProposals']){
 const start=source.indexOf((name==='refreshBookingProposals'?'async ':'')+'function '+name+'(');
 vm.runInContext(source.slice(start,source.indexOf('\n}\n',start)+3),context);
}
const card=()=>({dataset:{bookingProposal:id,bookingState:'pending',bookingExpires:'1'},innerHTML:'old pending',isConnected:true,matches:()=>true,closest:()=>null});
const result=status=>({message:'authoritative',proposal:{action_id:id,status,payload:{},expires_at:1}});
(async()=>{
 const c=card();cards=[c];context.bookingProposalResults.set(id,'stale pending');
 context.apiRequest=async(url,options)=>{assert(!options.method);reads++;return result('cancelled');};
 context.receiveBookingInvalidation({action_id:'not an action',html:'untrusted'});assert.equal(reads,0);
 context.receiveBookingInvalidation({action_id:id,status:'pending',html:'untrusted'});
 assert(!c.innerHTML.includes('old pending'),'signal must immediately freeze stale controls');
 await new Promise(setImmediate);assert(c.innerHTML.includes('已放弃草稿'));
 assert(!c.innerHTML.includes('untrusted'),'signal content is never rendered as authority');
 context.publishBookingInvalidation(id);assert.equal(published.length,2);
 for(const signal of published) assert.deepEqual(Object.keys(signal).sort(),['action_id','nonce']);
 c.dataset.bookingState='pending';c.dataset.bookingExpires='1';context.bookingProposalResults.clear();
 context.apiRequest=async()=>{reads++;return result('expired');};
 context.refreshVisibleBookingProposals({expiredOnly:true});await new Promise(setImmediate);
 assert(c.innerHTML.includes('草稿已过期'));assert(!c.innerHTML.includes('data-booking-proposal-confirm'));
 const count=reads;context.document.visibilityState='hidden';context.refreshVisibleBookingProposals();assert.equal(reads,count);
 context.document.visibilityState='visible';c.dataset.bookingState='pending';let resolve;
 context.apiRequest=async()=>{reads++;return new Promise(done=>resolve=done);};
 const first=context.refreshBookingProposals(c);await context.refreshBookingProposals(c);
 assert.equal(reads,count+1,'expiry/focus ticks coalesce, not duplicate concurrent queries');
 resolve(result('expired'));await first;
 assert(source.includes('addEventListener("storage"'));
 assert(source.includes('addEventListener("pageshow"'));
 assert(source.includes('addEventListener("visibilitychange", () => refreshVisibleBookingProposals())'));
 console.log('Booking tabs: signal-only invalidation, authoritative GET, expiry, hidden/focus/BFCache hooks and query coalescing passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
