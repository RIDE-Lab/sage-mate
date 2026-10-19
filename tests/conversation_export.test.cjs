const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../src/sage_faculty_twin/web/app.js'),'utf8');
const code=source.slice(source.indexOf('function legacyMessageMarkdown('),source.indexOf('function renderPendingAssistantMessage('));
const text=value=>({nodeType:3,textContent:value});
const el=(tag,children=[],attrs={})=>({tagName:tag.toUpperCase(),childNodes:children,
  get children(){return children.filter(n=>n.nodeType!==3);},
  get textContent(){return children.map(n=>n.textContent).join('');},
  getAttribute:name=>attrs[name]||null,
  querySelectorAll(selector){assert.equal(selector,'tr');return children;}});
const message=(role,markdown,body=null,pending=false)=>({dataset:markdown===undefined?{}:{exportMarkdown:markdown},
  classList:{contains:name=>name==='message-'+role||(pending&&name==='message-pending')},querySelector:()=>body});
const root=messages=>({querySelectorAll:selector=>{assert(selector.includes(':scope >'));return messages;}});
let downloaded,blob,revoked,removed=false,cleanup;
const status={textContent:''};
const sandbox={Blob,URL:{createObjectURL:value=>{blob=value;return 'blob:owned';},revokeObjectURL:value=>revoked=value},
  apiRequest:()=>{throw new Error('Export must not fetch another session or send content');},
  document:{getElementById:()=>status,body:{appendChild:()=>{}},createElement:()=>({click(){downloaded=this.download;},remove(){removed=true;}})},
  setTimeout:callback=>cleanup=callback};
vm.createContext(sandbox); vm.runInContext(code,sandbox);
assert(source.includes('article.dataset.exportMarkdown = String(text ?? "")'));
assert(source.includes('container.dataset.exportMarkdown = sourceBlocksValid ? text : cleanedText'));
assert(source.includes('delete container.dataset.exportMarkdown'));
assert(source.includes('addEventListener("click", exportCurrentConversationMarkdown)'));
assert(source.includes('exportButton.disabled = !chatStream.querySelector(".message-user")'));
const long='长回答尾部🌟\n'.repeat(10000)+'```python\nprint("完整")\n```\n\n[来源](https://example.org/full?x=1&y=2)';
const messages=[message('assistant','欢迎页不属于问答'),message('user','# 原始问题\n\n- A\n- B'),
  message('assistant',long),message('user','第二个问题'),message('assistant','**第二个回答**')];
const markdown=sandbox.buildSessionMarkdown(root(messages),'标题\n不可注入标题');
assert(markdown.startsWith('# 标题 不可注入标题\n\n## 提问 1'));
assert(markdown.includes(long)); assert(markdown.endsWith('**第二个回答**\n'));
assert(!markdown.includes('欢迎页')); assert(markdown.includes('## 提问 2'));
assert.throws(()=>sandbox.buildSessionMarkdown(root([])),/暂无/);
assert.throws(()=>sandbox.buildSessionMarkdown(root([message('user','问题'),message('assistant',undefined)])),/不完整/);
const pending=sandbox.buildSessionMarkdown(root([message('user','进行中的问题'),message('assistant','部分不可冒充完整',null,true)]));
assert(pending.includes('尚在生成')); assert(!pending.includes('部分不可冒充完整'));
const legacy=el('div',[el('h2',[text('旧标题')]),el('p',[el('a',[text('完整链接')],{href:'https://example.org/full'}),el('strong',[text('强调')])]),
  el('pre',[text('```\n保留代码\n```')]),el('ul',[el('li',[text('父项'),el('ul',[el('li',[text('子项')])])])]),
  el('table',[el('tr',[el('th',[text('列A')]),el('th',[text('列B')])]),el('tr',[el('td',[text('a|b')]),el('td',[text('尾部')])])])]);
const old=sandbox.buildSessionMarkdown(root([message('user','旧问题'),message('assistant',undefined,legacy)]));
for(const expected of ['## 旧标题','[完整链接](https://example.org/full)','**强调**','````\n```\n保留代码\n```\n````','- 父项\n  - 子项','| --- | --- |','a\\|b','尾部'])assert(old.includes(expected),expected);
sandbox.chatStream=root(messages); sandbox.currentConversationTitle='验收';
sandbox.exportCurrentConversationMarkdown();
assert(/^twin-session-.*\.md$/.test(downloaded)); assert(removed); assert.equal(revoked,undefined);
cleanup(); assert.equal(revoked,'blob:owned'); assert(status.textContent.includes('已导出'));
(async()=>{assert((await blob.text()).includes(long));assert.equal(blob.type,'text/markdown;charset=utf-8');
  console.log('Session Markdown: exact multi-round content, long Unicode/code/links, legacy structure, pending/empty and actual download passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
