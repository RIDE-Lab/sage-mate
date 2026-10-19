// Generate an isolated static QA page from REAL content-free SAGE events.
// This imports no browser state, production identity, or model credentials.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const web = path.join(root, 'src/sage_faculty_twin/web');
const source = fs.readFileSync(path.join(web, 'app.js'), 'utf8');
const events = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const context = {};
vm.createContext(context);
const start = source.indexOf('function escapeHtml(');
vm.runInContext(source.slice(start,source.indexOf('\nfunction ',start+1)),context);
vm.runInContext(source.slice(source.indexOf('function buildNativeExecutionHtml('),
    source.indexOf('function syncNativeExecutionDisplay(')),context);
const styles = ['styles.css','minimal.1009.css'].map(name=>fs.readFileSync(path.join(web,name),'utf8')).join('\n');
const symbols = fs.readFileSync(path.join(web,'index.html'),'utf8').match(/<symbol\b[\s\S]*?<\/symbol>/g).join('\n');
const theme = process.argv[3] === 'dark' ? 'dark' : 'light';
process.stdout.write(`<!doctype html><html lang="zh-CN" data-theme="${theme}"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Twin 工作流隔离视觉验收</title>
<style>${styles}\nbody{display:block;overflow:auto;height:auto;padding:32px 18px}main{max-width:760px;margin:auto}h1{font-size:18px}p.qa-notice{color:var(--text-secondary);font-size:13px}section.qa-card{border:1px solid var(--line);border-radius:16px;padding:18px;background:var(--surface-solid)}</style>
<svg style="position:absolute;width:0;height:0;overflow:hidden" aria-hidden="true">${symbols}</svg>
<main><h1>完整路线图 · 已执行路径高亮</h1><p class="qa-notice">隔离视觉验收：真实 SAGE 拓扑与执行记录，业务端口为无副作用测试替身；不代表模型问答或生产上线验收。</p>
<section class="qa-card">${context.buildNativeExecutionHtml(events)}</section></main></html>`);
