// Generate a loopback-only browser fixture from the real production functions.
// No chat/booking/mail endpoint is called. Synthetic data is visibly labelled.
const fs=require('node:fs'),path=require('node:path');
const web=path.join(__dirname,'../src/sage_faculty_twin/web');
const source=fs.readFileSync(path.join(web,'app.js'),'utf8');
const output=process.argv[2];
if(!output)throw Error('Provide a new output directory');
fs.mkdirSync(output,{recursive:true});
const names=['escapeHtml','responseWorkEntries','buildWorkAttentionHtml','buildExecutionAttentionHtml',
  'buildResponseWorkSummaryHtml','buildWebResearchStatusHtml','buildWorkflowStatusSummaryHtml',
  'buildWorkStatusHtml','buildWorkAttemptHtml','updateMessageExecutionEvidence','consolidateMessageDiagnostics',
  'captureConversationScroll','restoreConversationScroll','syncLatestConversationButton','scrollConversationToLatest',
  'buildNotificationStatusHtml','buildCollapsibleSupportSectionHtml','hydrateExecutionEvidence'];
const functions=names.map(name=>{
  const start=source.indexOf(`${name==='hydrateExecutionEvidence'?'async ':''}function ${name}(`);
  if(start<0)throw Error(name);
  return source.slice(start,source.indexOf('\n}\n',start)+3);
}).join('\n');
for(const [from,to] of [['styles.css','base.css'],['minimal.1009.css','theme.css']])
  fs.copyFileSync(path.join(web,from),path.join(output,to));
fs.writeFileSync(path.join(output,'index.html'),`<!doctype html><html lang="zh" data-theme="light"><meta charset="utf-8">
<title>Twin 异常与确认 · 隔离验收</title><link rel="stylesheet" href="base.css"><link rel="stylesheet" href="theme.css">
<style>body{display:block;margin:0;padding:12px}header{height:112px}header button{padding:6px;margin:3px}
#chat-stream{height:calc(100vh - 145px);overflow:auto;max-width:850px;margin:auto;display:block;padding:10px}
.message-frame{max-width:760px;margin:0 auto}#metrics{font:14px monospace;white-space:pre-wrap} .history{height:780px;line-height:2}
</style><header><strong>隔离验收 · 合成异常，不会预约或发送邮件</strong><br>
<button id="update">开始延迟后台更新（6 秒后；请上翻）</button><button id="unavailable">执行状态查询失败</button>
<button id="recover">恢复状态查询</button><button id="theme">切换明暗</button>
<div id="metrics" role="status">等待操作</div></header>
<main id="chat-stream" tabindex="0"><div class="history message-user">历史阅读锚点 A<br>这是用于检查后台更新时阅读位置的合成历史。<br>不会自动滚动，也不会自动打开详情。</div>
<article class="message-assistant message-ready" data-exchange-id="synthetic"><div class="message-frame">
<div class="message-main-copy"><h2>合成回答正文</h2><p>正文应先读，下面的失败和待确认操作必须在高级详情外。</p></div>
<section class="response-evidence"><details><summary>回答依据 · 合成测试数据</summary>无真实知识引用</details></section>
<div id="fixture"></div></div></article><div style="height:450px">历史阅读锚点 B · 答案之后</div></main>
<button id="chat-scroll-latest" hidden><span class="chat-latest-notice"></span>回到最新</button>
<script>
let chatStream=document.getElementById('chat-stream'),conversationFollowingLatest=false;
const buildNativeExecutionHtml=()=>'<p>此夹具不验收拓扑图；仅验收真实状态渲染与更新。</p>',buildWorkflowChipRowHtml=()=>'';
const uiIconSvg=()=>'',activeWorkflowMessage=null,executionHistoryGeneration=0;
const restoredExecutionStreams=new Set();
let queryFailed=false;
const resolveApiOrigin=async()=>'',apiRequest=async()=>{if(queryFailed)throw Error('synthetic unavailable');return {stream_closed:true,work_status:{version:1,tasks:[{task_id:'post_answer',status:'done'}]}};};
${functions}
const message=document.querySelector('.message-assistant'),root={querySelectorAll:()=>[message],contains:node=>node===message};
document.getElementById('fixture').outerHTML=buildWorkflowStatusSummaryHtml([],[],[],null,
buildNotificationStatusHtml({status:'failed',summary:'合成邮件发送失败，未发送任何邮件。'})+
buildCollapsibleSupportSectionHtml({kicker:'待确认',title:'需要你确认的操作（合成）',countLabel:'需确认',sectionClassName:'message-section-follow-up',copy:'尚未执行；不调用预约接口。',contentHtml:'<p>预约时间需要用户确认，此处仅显示。</p>'})+
buildWebResearchStatusHtml([{key:'web_search',status:'failed',summary:'合成联网失败，不能声称已核实来源。'}]));
consolidateMessageDiagnostics(message);
document.addEventListener('click',event=>{const button=event.target.closest('.message-section-toggle');if(!button)return;
const section=button.closest('.message-section'),open=section.dataset.expanded!=='true';section.dataset.expanded=String(open);
button.setAttribute('aria-expanded',String(open));section.querySelector('.message-section-content').hidden=!open;});
function measure(label,before){const section=message.querySelector('.message-workflow-summary');
document.getElementById('metrics').textContent=label+' | scrollTop='+Math.round(chatStream.scrollTop)+(before===undefined?'':' Δ='+Math.round(chatStream.scrollTop-before))+' | advanced='+section.dataset.expanded;}
document.getElementById('update').onclick=()=>{measure('已安排更新');setTimeout(()=>{const top=chatStream.scrollTop;
updateMessageExecutionEvidence(message,[],[],{version:1,tasks:[{task_id:'post_answer',status:'blocked'}]});measure('后台阻止事件已到达',top);},6000);};
document.getElementById('unavailable').onclick=async()=>{queryFailed=true;const top=chatStream.scrollTop;await hydrateExecutionEvidence(root);measure('状态查询失败提示已更新',top);};
document.getElementById('recover').onclick=async()=>{queryFailed=false;const top=chatStream.scrollTop;await hydrateExecutionEvidence(root);measure('状态恢复已更新',top);};
document.getElementById('theme').onclick=()=>document.documentElement.dataset.theme=document.documentElement.dataset.theme==='dark'?'light':'dark';
document.getElementById('chat-scroll-latest').onclick=scrollConversationToLatest;
chatStream.addEventListener('scroll',()=>{conversationFollowingLatest=false;});measure('初始');
</script></html>`);
console.log('Synthetic browser fixture generated in '+output);
