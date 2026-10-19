const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../src/sage_faculty_twin/web/app.js'), 'utf8');
const context = {};
vm.createContext(context);
const escapeStart = source.indexOf('function escapeHtml(');
vm.runInContext(source.slice(escapeStart, source.indexOf('\nfunction ', escapeStart+1)), context);
vm.runInContext(source.slice(source.indexOf('function buildNativeExecutionHtml('),
    source.indexOf('function syncNativeExecutionDisplay(')), context);
let seq = 0;
const event = (node, extra={}) => ({version:1, event_id:'e'+(++seq), sequence:seq,
    trace_id:'trace', request_id:'request', execution_id:'attempt-'+seq,
    parent_execution_id:null, program_uri:'flow://actual', program_rev:'v1', node_id:node,
    operator_key:'same label', state:'completed', next_nodes:[], upstream_node_ids:[],
    downstream_node_ids:[], ...extra});
const a = event('a',{execution_id:'parent',downstream_node_ids:['b'],next_nodes:[
    {program_uri:'flow://actual',program_rev:'v1',node_id:'b'}]});
const b = event('b',{upstream_node_ids:['a'],state:'started'});
const router = event('r',{parent_execution_id:'parent',program_uri:'flow://child',next_nodes:[
    {program_uri:'flow://child',program_rev:'v1',node_id:'left'},
    {program_uri:'flow://child',program_rev:'v1',node_id:'right'}]});
const left = event('left',{parent_execution_id:'parent',program_uri:'flow://child'});
const right = event('right',{parent_execution_id:'parent',program_uri:'flow://child',state:'failed'});
let model = context.buildNativeWorkflowModel([a,b,router,left,right]);
assert.equal(model.length,2);
assert.deepEqual(Array.from(model[0].layers, x=>Array.from(x)),[
    ['["flow://actual","v1","a"]'],['["flow://actual","v1","b"]']]);
assert.equal(model[0].edges.size,1);
assert.equal([...model[0].edges.values()][0].routed,true);
assert.equal(model[1].parentEvent.execution_id,'parent');
assert.equal(model[1].layers[1].length,2); // Fork comes from routed refs, not labels or same wave.
// Actual SAGE nested calls allocate another event group, within the same trace.
const nestedRequest = {...router, request_id:'nested-event-group'};
model = context.buildNativeWorkflowModel([a,b,nestedRequest]);
assert.equal(model.find(x=>x.request==='nested-event-group').parentEvent,a);
assert(context.buildNativeExecutionHtml([a,b,nestedRequest]).includes('含 1 个子工作流'));
// Malformed duplicate execution identities are ambiguous, never guessed.
const collision = {...a, request_id:'collision'};
model = context.buildNativeWorkflowModel([a,collision,nestedRequest]);
assert.equal(model.find(x=>x.request==='nested-event-group').parentEvent,null);
assert(!context.buildNativeExecutionHtml([a,collision,nestedRequest]).includes('含 1 个子工作流'));
let html = context.buildNativeExecutionHtml([a,b,router,left,right]);
for (const text of ['主工作流','子工作流','嵌套于 same label','节点明细与原生元数据',
    'data-flow-edge="routed"','data-flow-state="failed"','data-flow-state="started"',
    '节点同层不代表同时执行']) assert(html.includes(text),text);
const missing = context.buildNativeWorkflowModel([a])[0];
assert.equal([...missing.nodes.values()].find(x=>x.id==='b').attempts.length,0);
assert(context.buildNativeExecutionHtml([a]).includes('未观测到执行'));
const isolated = context.buildNativeWorkflowModel([event('a'),event('b')])[0];
assert.equal(isolated.edges.size,0); // Neither ordering nor repeated labels imply an edge.
const repeated = context.buildNativeWorkflowModel([a,event('a',{state:'failed'})])[0];
assert.equal([...repeated.nodes.values()].find(x=>x.id==='a').attempts.length,2);
assert(context.buildNativeExecutionHtml([a,event('a',{state:'failed'})]).includes('data-flow-state="multiple"'));
const other = event('a',{trace_id:'other',execution_id:'parent'});
const child = event('x',{trace_id:'third',parent_execution_id:'parent'});
model = context.buildNativeWorkflowModel([a,other,child]);
assert.equal(model.find(x=>x.trace==='third').parentEvent,null); // No cross-request parent guess.
const cycle = [event('a',{downstream_node_ids:['b']}),event('b',{downstream_node_ids:['a']})];
assert(context.buildNativeExecutionHtml(cycle).includes('存在回环'));
const cross = event('x',{next_nodes:[{program_uri:'flow://different',program_rev:'v2',node_id:'y'}]});
assert.equal([...context.buildNativeWorkflowModel([cross])[0].nodes.values()].find(n=>n.id==='y').uri,'flow://different');
assert(!context.buildNativeExecutionHtml([event('x',{operator_key:'<script>alert(1)</script>'})]).includes('<script>'));
assert(context.buildNativeExecutionHtml([{version:1,execution_id:'legacy',sequence:1,state:'started'}]).includes('没有完整的原生拓扑字段'));
assert(!html.includes('WORKFLOW_PHASE_DEFINITIONS'));
// Nine sequential graph layers fit three compact rows, without losing an edge.
const chain = Array.from({length:9}, (_,i)=>event('chain-'+i,{operator_key:'operator-'+i,
    next_nodes:i===8 ? [] : [{program_uri:'flow://actual',program_rev:'v1',node_id:'chain-'+(i+1)}]}));
const compact = context.buildNativeExecutionHtml(chain);
assert.equal((compact.match(/data-flow-node=/g)||[]).length,9);
assert.equal((compact.match(/data-flow-edge=/g)||[]).length,8);
assert(Number(compact.match(/viewBox="0 0 600 ([\d.]+)"/)[1]) < 400);
assert(compact.includes('9 节点 · 8 连线'));
assert(compact.includes('折行只改变布局，不增加连线'));
assert(compact.includes('<details class="native-flow-identity">'));
assert(compact.includes('tabindex="0"')); // Narrow-screen keyboard scrolling.
const longName = '完整保留中文节点名ABCDEFGHIJKLMNOPQRSTUVWXYZ';
const longHtml = context.buildNativeExecutionHtml([event('long',{operator_key:longName})]);
assert.equal(Array.from(longHtml.matchAll(/class="native-flow-label">([^<]*)<\/text>/g), m=>m[1]).join(''),longName);
const css = fs.readFileSync(path.join(__dirname, '../src/sage_faculty_twin/web/styles.css'), 'utf8');
assert(/\.message-workflow-summary \.message-section-content\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\)/.test(css));
assert(/\.native-flow-scroll\s*\{[^}]*overflow-x:\s*auto;[^}]*min-width:\s*0/.test(css));
console.log('Authoritative topology, nested forks, missing nodes, retries, cycles and escaping passed');
