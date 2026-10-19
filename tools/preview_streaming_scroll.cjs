// Browser regression uses real production renderers with synthetic data only.
const fs=require('node:fs'),path=require('node:path');
const web=path.join(__dirname,'../src/sage_faculty_twin/web');
const app=fs.readFileSync(path.join(web,'app.js'),'utf8');
const html=fs.readFileSync(path.join(web,'index.html'),'utf8');
const latestButton=html.match(/<button type="button" id="chat-scroll-latest"[\s\S]*?<\/button>/)[0];
function fn(name) {const start=app.indexOf('function '+name+'(');if(start<0)throw Error(name);return app.slice(start,app.indexOf('\n}\n',start)+3);}
const functions=['captureConversationScroll','restoreConversationScroll','parseStreamingThinkContent',
    'syncLatestConversationButton','scrollConversationToLatest',
    'appendStreamingAnswerDelta','appendStreamingThinkDelta','renderAssistantMessage','updateMessageExecutionEvidence'].map(fn).join('\n');
process.stdout.write(`<!doctype html><meta charset="utf-8"><style>${fs.readFileSync(path.join(web,'styles.css'))}\n${fs.readFileSync(path.join(web,'minimal.1009.css'))}
body{display:block;height:100vh;margin:0}nav{height:56px;display:flex;gap:12px;align-items:center}.chat-shell{height:calc(100vh - 56px)}.message{min-height:150px}.message-body{white-space:pre-wrap}#status{font-variant-numeric:tabular-nums}.composer-shell{flex-shrink:0;min-height:76px;text-align:center}
</style><nav><button id="start">开始持续生成</button><button id="finish">完成回答</button><button id="evidence">更新前文证据</button><button id="theme">切换主题</button><span id="status">就绪</span></nav>
<svg hidden><symbol id="icon-chevron-down" viewBox="0 0 24 24"><path d="m7 9.5 5 5 5-5"/></symbol></svg>
<main class="chat-shell"><section id="chat-stream" class="chat-stream" tabindex="0" role="region" aria-label="当前会话"></section><form class="composer-shell">${latestButton}<span>输入问题…</span></form></main><script>
const chatStream=document.querySelector('#chat-stream'),assistantLabel='Twin';let INLINE_WORKFLOW_TRACE_ENABLED=false;
const escapeHtml=s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
const formatMessageContent=escapeHtml,formatSourceAnswer=(text)=>escapeHtml(text),hasMatchingSourceBlocks=()=>false,stripNotificationText=x=>x;
const stopAnswerProcessingTicker=()=>{},buildNotificationStatusHtml=()=>'',buildFeedbackSectionHtml=()=>'',buildRetryButtonHtml=()=>'',uiIconSvg=()=>'',formatAnswerProcessingDuration=()=> 'fixture';
const buildNativeExecutionHtml=()=>'<p>'+('新增前文证据。'.repeat(300))+'</p>',buildWorkAttemptHtml=()=>'';
let streamingAnswerBuffer='',streamingThinkBuffer='',timer=null;
${functions}
document.querySelector('#chat-scroll-latest').addEventListener('click',scrollConversationToLatest);
chatStream.addEventListener('scroll',()=>syncLatestConversationButton(),{passive:true});
chatStream.innerHTML=Array.from({length:12},(_,i)=>'<article class="message message-user"><div class="message-body">历史问题 '+(i+1)+'</div></article><article class="message message-assistant message-ready"><div class="message-frame"><div class="message-body">历史完整回答 '+(i+1)+'：'+('用于验证长会话阅读位置。'.repeat(50))+'</div><div class="message-workflow-summary"><div class="message-section-content"><div class="message-native-execution"></div></div></div></div></article>').join('');
const pending=document.createElement('article');pending.className='message message-assistant message-pending';pending.dataset.startedAt='';
pending.innerHTML='<div class="message-frame"><div class="streaming-answer-body message-body"></div><div id="pending-thinking-text" hidden><div class="thinking-text-body"></div></div></div>';chatStream.appendChild(pending);chatStream.scrollTop=chatStream.scrollHeight;
const status=document.querySelector('#status');let count=0;
document.querySelector('#start').onclick=()=>{if(timer)return;timer=setInterval(()=>{count++;appendStreamingAnswerDelta('第 '+count+' 段：模型正在生成回答，历史阅读不应被打断。\\n');status.textContent='生成中 '+count+' 段';},120);};
document.querySelector('#finish').onclick=()=>{clearInterval(timer);timer=null;renderAssistantMessage(pending,streamingAnswerBuffer+'\\n完整回答结束。',[],[],[],null,false,null,[],false);status.textContent='已完成 '+count+' 段';};
document.querySelector('#evidence').onclick=()=>{updateMessageExecutionEvidence(chatStream.querySelector('.message-assistant'),[{}],[]);status.textContent='已更新前文证据';};
document.querySelector('#theme').onclick=()=>document.documentElement.dataset.theme=document.documentElement.dataset.theme==='dark'?'light':'dark';
</script>`);
