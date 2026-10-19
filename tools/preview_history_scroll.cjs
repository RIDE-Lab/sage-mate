// Synthetic, non-private fixture using production CSS and layout synchronization.
const fs = require('node:fs');
const path = require('node:path');
const web = path.join(__dirname, '../src/sage_faculty_twin/web');
const app = fs.readFileSync(path.join(web, 'app.js'), 'utf8');
const empty = app.slice(app.indexOf('function updateChatEmptyState()'), app.indexOf('// Reserve the actual composer height'));
const sync = app.slice(app.indexOf('function syncConversationMode()'), app.indexOf('// Older local snapshots'));
process.stdout.write(`<!doctype html><meta charset="utf-8"><style>${fs.readFileSync(path.join(web, 'styles.css'))}\n${fs.readFileSync(path.join(web, 'minimal.1009.css'))}
body{display:block;height:100vh;margin:0} .chat-shell{height:calc(100vh - 50px)}nav{height:50px}.message{min-height:180px}
</style><nav><button id="restore">恢复八轮历史</button><button id="theme">切换主题</button><span id="result"></span></nav>
<main class="chat-shell chat-empty"><section id="chat-stream" class="chat-stream" tabindex="0" role="region" aria-label="当前会话"><div id="welcome-greeting">欢迎页</div></section></main>
<script>const chatShell=document.querySelector('.chat-shell'),chatStream=document.querySelector('#chat-stream');${empty}${sync}
document.querySelector('#restore').onclick=()=>{chatStream.innerHTML=Array.from({length:8},(_,i)=>'<article class="message message-user">问题 '+(i+1)+'</article><article class="message message-ready">完整回答 '+(i+1)+'<p>合成滚动验收内容</p></article>').join('');syncConversationMode();document.querySelector('#result').textContent='已恢复 8 轮 / 16 条';};
document.querySelector('#theme').onclick=()=>document.documentElement.dataset.theme=document.documentElement.dataset.theme==='dark'?'light':'dark';</script>`);
