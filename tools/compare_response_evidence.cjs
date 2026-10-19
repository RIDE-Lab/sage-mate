// Offline controlled presentation comparison. No model calls, auth or telemetry.
// Generated cases are explicit fixtures, NEVER production-user evidence.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
const output = process.argv[2];
if (!output) throw Error('Usage: node tools/compare_response_evidence.cjs OUTPUT_DIRECTORY');
const root = path.join(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'src/sage_faculty_twin/web/app.js'), 'utf8');
const context = {};
vm.createContext(context);
for (const name of ['escapeHtml','buildResponseEvidenceModel','buildResponseEvidenceHtml','buildResponseWorkSummaryHtml','buildWorkStatusHtml','buildWorkAttemptHtml','buildCollapsibleSupportSectionHtml']) {
    const start = source.indexOf(`function ${name}(`);
    vm.runInContext(source.slice(start, source.indexOf('\n}\n', start) + 3), context);
}
vm.runInContext(source.slice(source.indexOf('function buildNativeExecutionHtml('), source.indexOf('function syncNativeExecutionDisplay(')), context);
const coverage = {version:1,method:'source_line_witness',requirement_count:2,missing_parts:['性能数据及测量条件是什么？']};
const cases = [
    {id:'partial',question:'功能和性能如何？',answer:'该系统支持来源约束回答。',
        input:{basisItems:[{source_url:'https://example.org/full/source'}],evidenceCoverage:coverage},events:[],
        questionForUser:'现有材料是否已经覆盖性能和测量条件？',expected:'尚缺来源依据',cue:'1 项问题尚缺来源依据'},
    {id:'covered',question:'这个系统是什么？',answer:'该系统用于研究。',
        input:{answerBlocks:[{kind:'source',source_id:'s'}],evidenceCoverage:{...coverage,missing_parts:[]}},events:[],
        questionForUser:'匹配到来源行能否视为独立事实核验？',expected:'不能',cue:'不是独立事实核验'},
    {id:'booking',question:'预约会议处理得怎样？',answer:'已整理会议请求。',
        input:{externalEvidenceRequired:false,bookingResult:{accepted:true,booking:{status:'pending'}}},events:[],
        questionForUser:'会议已确认且通知已送达了吗？',expected:'未确认',cue:'不等于会议已确认或通知已送达'},
    {id:'background',question:'这轮处理完成了吗？',answer:'回答已生成。',input:{externalEvidenceRequired:false},
        events:[{version:1,namespace:'fixture',request_id:'fixture',task_id:'post_answer',attempt:1,state:'failed'}],
        questionForUser:'回答生成是否代表后台处理成功？',expected:'不是，后台失败',cue:'失败'},
    {id:'suggestion',question:'下一步做什么？',answer:'建议补充实验记录。',
        input:{externalEvidenceRequired:false,followUpActions:[{status:'suggested'}]},events:[],
        questionForUser:'建议的操作已经执行了吗？',expected:'尚未执行',cue:'尚未执行'},
    {id:'legacy',question:'以前回答的依据还可核对吗？',answer:'这是一条旧回答。',
        input:{externalEvidenceRequired:false},events:[],questionForUser:'没有记录可否判断事实已经核实？',expected:'不能，未知',cue:'未记录来源覆盖情况'},
];
const escape = context.escapeHtml;
const rows = cases.map(item => {
    const answer = `<div class="comparison-answer">${escape(item.answer)}</div>`;
    const event = {version:1,event_id:'fixture',sequence:1,execution_id:'fixture',trace_id:'fixture',request_id:'fixture',
        program_uri:'fixture://not-production',program_rev:'1',node_id:'answer',operator_key:'compose_answer',
        state:'completed',next_nodes:[],upstream_node_ids:[],downstream_node_ids:[]};
    const fixtureSources = item.input.basisItems?.length ? context.buildCollapsibleSupportSectionHtml({
        kicker:'Fixture',title:'来源与记录',count:item.input.basisItems.length,
        copy:'本例为合成来源记录，不是真实知识资料。',contentHtml:'<p>fixture source · https://example.org/full/source</p>',
    }) : '';
    const arms = {
        A: answer,
        B: answer + context.buildNativeExecutionHtml([event]) + context.buildWorkAttemptHtml(item.events),
        C: context.buildResponseEvidenceHtml(context.buildResponseEvidenceModel(item.input))
            + context.buildResponseWorkSummaryHtml(item.events) + answer
            + `<details><summary>高级详情 · 工作流</summary>${context.buildNativeExecutionHtml([event])}${context.buildWorkAttemptHtml(item.events)}</details>` + fixtureSources,
    };
    const answerHash = crypto.createHash('sha256').update(item.answer).digest('hex');
    assert(arms.C.includes(item.cue));
    for (const html of Object.values(arms)) assert(html.includes(answer));
    return {...item, answerHash, arms,
        technicalOnly:{sameCanonicalAnswer:true,sameFixtureInput:true,conciseCuePresent:arms.C.includes(item.cue)}};
});
fs.mkdirSync(output,{recursive:true});
fs.writeFileSync(path.join(output,'comparison.json'), JSON.stringify({
    evidenceType:'synthetic technical fixtures, NOT a human study',conditions:{A:'answer only',B:'full graph',C:'layered evidence'},
    humanEvaluation:'deferred by user',cases:rows,
},null,2));
const cards = rows.map((row,index) => `<article class="message-ready"><h2>案例 ${index+1} · ${escape(row.question)}</h2>
    ${['A','B','C'].map(arm=>`<section class="comparison-arm" data-arm="${arm}" hidden>${row.arms[arm]}</section>`).join('')}
    <p><strong>请判断：</strong>${escape(row.questionForUser)}</p>
    <label>你的判断 <input autocomplete="off" aria-label="案例 ${index+1} 的判断"></label>
    <details><summary>主持人评分依据（作答前不要打开）</summary>${escape(row.expected)}</details></article>`).join('');
fs.writeFileSync(path.join(output,'index.html'), `<!doctype html><html lang="zh-CN" data-theme="dark"><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1"><title>Twin 展示对照 · 技术夹具</title>
    <style>${fs.readFileSync(path.join(root,'src/sage_faculty_twin/web/styles.css'),'utf8')}
    body{display:block!important;overflow:auto!important;padding:24px;background:#101827;color:#f4f7ff;max-width:880px;margin:auto;height:auto!important}
    article{border:1px solid #53617b;border-radius:12px;padding:18px;margin:20px 0}h1{font-size:22px}h2{font-size:16px}
    p,label,input,button,summary{font-size:14px}input,button{color:#f4f7ff;background:#24334e;border:1px solid #8292ae;padding:8px;border-radius:6px}
    .comparison-answer{white-space:pre-wrap;padding:16px 0}details{margin-top:12px}label{display:block}</style>
    <h1>同答案展示对照</h1><p>合成技术案例；没有调用或操作真实会议/邮件。不收集或发送任何作答数据。真人验收尚未开展。</p>
    <p>展示条件 <button data-choice="A">A</button> <button data-choice="B">B</button> <button data-choice="C">C</button></p>
    <p id="condition" role="status"></p>${cards}
    <script>const chatStream=document.body;
    ${source.slice(source.indexOf('function handleMessageSectionToggle('),source.indexOf('// Task 4: Copy-to-clipboard handler'))}
    document.body.addEventListener('click',handleMessageSectionToggle);
    function select(arm){document.querySelectorAll('[data-arm]').forEach(el=>el.hidden=el.dataset.arm!==arm);document.querySelector('#condition').textContent='当前条件 '+arm;}
    document.querySelectorAll('[data-choice]').forEach(button=>button.addEventListener('click',()=>select(button.dataset.choice)));
    select(new URLSearchParams(location.search).get('condition')?.match(/^[ABC]$/)?.[0]||'A');</script></html>`);
console.log(JSON.stringify({cases:rows.length,allTechnicalChecksPassed:true,humanEvaluation:'not run',output}));
