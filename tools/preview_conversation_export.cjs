// Isolated synthetic download QA using the application's actual exporter.
const fs=require('node:fs');
const path=require('node:path');
const web=path.resolve(__dirname,'../src/sage_faculty_twin/web');
const app=fs.readFileSync(path.join(web,'app.js'),'utf8');
const index=fs.readFileSync(path.join(web,'index.html'),'utf8');
const exporter=app.slice(app.indexOf('function legacyMessageMarkdown('),app.indexOf('function renderPendingAssistantMessage('));
const button=index.match(/<button type="button" id="export-conversation"[\s\S]*?<\/button>/)[0].replace(' disabled','');
const escape=text=>text.replaceAll('&','&amp;').replaceAll('"','&quot;').replaceAll('<','&lt;').replaceAll('>','&gt;');
const question='请解释 Wiki 和私有知识的区别。\n\n请保留来源链接。';
const answer='## 两类数据源\n\n- **Wiki**：公开资料。\n- **私有知识**：仅授权身份可读取。\n\n[完整 Wiki](https://lab.sage.org.ai/sage-wiki/)\n\n```python\nprint("中文代码完整保留")\n```';
const theme=process.argv[2]==='dark'?'dark':'light';
const css=['styles.css','minimal.1009.css'].map(name=>fs.readFileSync(path.join(web,name),'utf8')).join('\n');
process.stdout.write(`<!doctype html><html lang="zh" data-theme="${theme}"><meta charset="utf-8"><title>Twin 会话导出隔离验收</title>
<style>${css}\nbody{display:block;overflow:auto;height:auto;padding:28px}main{max-width:760px;margin:auto}.preview-head{display:flex;align-items:center;justify-content:space-between}.preview-head h1{font-size:20px}.message{margin:24px 0;padding:20px;background:var(--surface-solid);border:1px solid var(--line);border-radius:12px}.session-export-button .ui-icon{width:18px;height:18px;fill:none;stroke:currentColor;stroke-width:1.7}</style>
<main><header class="preview-head"><h1>当前会话 · Markdown 导出</h1>${button}</header><p>隔离验收：以下是合成问答，不读取真实会话或调用模型。</p>
<div id="chat-stream"><article class="message message-user" data-export-markdown="${escape(question)}"><h3>提问 1</h3><div class="message-body">请解释 Wiki 和私有知识的区别。<br>请保留来源链接。</div></article>
<article class="message message-assistant" data-export-markdown="${escape(answer)}"><h3>回答 1</h3><div class="message-body"><h2>两类数据源</h2><ul><li><strong>Wiki</strong>：公开资料。</li><li><strong>私有知识</strong>：仅授权身份可读取。</li></ul><p><a href="https://lab.sage.org.ai/sage-wiki/">完整 Wiki</a></p><pre>print("中文代码完整保留")</pre></div></article>
<article class="message message-user" data-export-markdown="第二轮问题"><h3>提问 2</h3><div class="message-body">第二轮问题</div></article>
<article class="message message-assistant" data-export-markdown="第二轮完整回答。"><h3>回答 2</h3><div class="message-body">第二轮完整回答。</div></article></div>
<span id="conversation-export-status" class="session-export-status" role="status"></span></main>
<script>const chatStream=document.getElementById('chat-stream');const currentConversationTitle='会话导出验收';${exporter}
document.getElementById('export-conversation').addEventListener('click',exportCurrentConversationMarkdown);</script></html>`);
