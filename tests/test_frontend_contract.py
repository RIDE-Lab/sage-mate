"""Frontend-to-API contract test.

Validates that every DOM element ID referenced inside an API POST body
construction in app.js actually exists in index.html. This catches the
exact class of bug where a required Pydantic field (e.g. visitor_profile)
sends an empty/null value because the DOM element was removed from HTML,
resulting in a 422 Unprocessable Entity error.
"""
from __future__ import annotations

import re
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parents[1] / "src" / "sage_faculty_twin" / "web"


def test_consent_modal_is_above_floating_companion():
    css = (WEB_DIR / "styles.css").read_text()
    companion = (WEB_DIR / "companion.css").read_text()
    overlay = re.search(r"\.modal-overlay\s*\{([^}]+)\}", css).group(1)
    panel = re.search(r"\.modal-panel\s*\{\s*position: fixed;([^}]+)\}", css).group(1)
    overlay_z = int(re.search(r"z-index:\s*(\d+)", overlay).group(1))
    panel_z = int(re.search(r"z-index:\s*(\d+)", panel).group(1))
    floating_max = max(map(int, re.findall(r"z-index:\s*(\d+)", companion)))
    assert panel_z > overlay_z > floating_max


def test_native_work_history_refresh_preserves_answer_and_closes_stale_scope():
    import subprocess
    script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync(process.argv[1],'utf8');
const calls=[],streams=[];
let releaseSnapshot;
const context={chatStream:null, conversationFollowingLatest:false, document:{getElementById:()=>null}, escapeHtml:s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;'),
    buildNativeExecutionHtml:()=>'<p>foreground</p>', persistActiveConversationSnapshot:()=>calls.push('persist'),
    resolveApiOrigin:async()=>'https://testserver',
    apiRequest:async url=>{calls.push(url); return await new Promise(resolve=>{releaseSnapshot=resolve;});},
    EventSource:class {constructor(url){this.url=url;streams.push(this);}close(){this.closed=true;}}};
vm.createContext(context);
for(const name of ['responseWorkEntries','buildWorkAttentionHtml','buildExecutionAttentionHtml','buildResponseWorkSummaryHtml']) {
    const summaryStart=source.indexOf('function '+name+'(');
    vm.runInContext(source.slice(summaryStart,source.indexOf('\nfunction ',summaryStart+1)),context);
}
for(const name of ['captureConversationScroll','restoreConversationScroll','syncLatestConversationButton']) {
    const start=source.indexOf('function '+name+'(');
    vm.runInContext(source.slice(start,source.indexOf('\n}\n',start)+3),context);
}
vm.runInContext('let activeWorkEvents=[]; let activeExecutionEvents=[]; let activeWorkflowRequestId=null; let activeWorkflowMessage=null; const restoredExecutionStreams=new Set(); let executionHistoryGeneration=0;',context);
vm.runInContext(source.slice(source.indexOf('function mergeWorkEventLists('),source.indexOf('function buildVisibleWorkflowSteps(')),context);
const event=(state,id=state)=>({version:1,event_id:id,namespace:'post',request_id:'owned',task_id:'post_answer',attempt:1,state,dependency_task_ids:[],dependency_mode:'success'});
assert(context.buildWorkAttemptHtml([]).includes('不推断已经完成'));
assert(context.buildWorkAttemptHtml([event('started')]).includes('后台处理中'));
assert(!context.buildWorkAttemptHtml([event('started')]).includes('data-work-state="completed"'));
assert(context.buildWorkAttemptHtml([event('completed'),event('started')]).includes('data-work-state="completed"'));
assert(context.buildWorkAttemptHtml([event('completed'),event('failed')]).includes('data-work-state="unknown"'));
assert(context.buildWorkAttemptHtml([{...event('started'),task_id:'<script>'}]).includes('&lt;script&gt;'));
assert(!context.buildWorkAttemptHtml([{...event('started'),task_id:'<script>'}]).includes('<script>'));
assert(context.buildWorkAttemptHtml([event('completed')]).includes('不代表外部邮件'));
assert.equal(context.mergeWorkEventLists([event('started')],[event('started'),event('completed')]).length,2);
const status=(task)=>({version:1,observed_at:123,tasks:[{task_id:'post_answer',attempt:1,dependencies_ready:true,claimable:false,...task}]});
assert(context.buildWorkStatusHtml(status({status:'pending',dependencies_ready:false})).includes('等待前置任务'));
assert(context.buildWorkStatusHtml(status({status:'pending'})).includes('等待重试时间'));
assert(context.buildWorkStatusHtml(status({status:'running',lease_expired:true})).includes('租约过期，等待回收'));
assert(!context.buildWorkStatusHtml(status({status:'running',lease_expired:true})).includes('已持久化完成'));
assert(context.buildWorkStatusHtml(status({status:'failed',last_failure_code:'request_cancelled'})).includes('已取消'));
assert(context.buildWorkStatusHtml(status({status:'done',last_failure_code:'temporarily_unavailable'})).includes('已持久化完成'));
const native={innerHTML:''},content={querySelector:()=>native,insertAdjacentHTML:()=>calls.push('work-html')};
const summary={dataset:{},querySelector:selector=>selector==='.message-section-content'?content:null};
const message={dataset:{exchangeId:'exchange'},answer:'original answer',citations:'original complete citations',
    querySelector:selector=>selector==='.message-workflow-summary'?summary:null};
const root={scrollTop:600,scrollHeight:5000,clientHeight:700,children:[message],
    getBoundingClientRect:()=>({top:0}),querySelectorAll:()=>[message],contains:node=>node===message};
message.getBoundingClientRect=()=>({top:1000-root.scrollTop,bottom:1200-root.scrollTop});
context.chatStream=root;
const turn=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
    const hydrated=context.hydrateExecutionEvidence(root);
    await turn();
    releaseSnapshot({execution_events:[],work_events:[event('started')],request_id:'public',stream_closed:false});
    await hydrated;
    assert.equal(streams.length,1);
    assert(native.innerHTML.includes('后台处理中'));
    streams[0].onmessage({data:JSON.stringify({type:'work-event',event:event('completed')})});
    assert(native.innerHTML.includes('data-work-state="completed"'));
    assert.equal(message.answer,'original answer');assert.equal(message.citations,'original complete citations');
    assert.equal(root.scrollTop,600,'background hydration must preserve history reading position');
    assert(calls.filter(c=>!['persist','work-html'].includes(c)).every(c=>c.startsWith('/chat/execution?')));
    context.stopRestoredExecutionStreams();
    assert(streams[0].closed);
    const saved=native.innerHTML;
    streams[0].onmessage({data:JSON.stringify({type:'work-event',event:event('failed')})});
    assert.equal(native.innerHTML,saved);
    const stale=context.hydrateExecutionEvidence(root);
    await turn();context.stopRestoredExecutionStreams();
    releaseSnapshot({execution_events:[],work_events:[event('failed')],request_id:'old',stream_closed:false});
    await stale;
    assert.equal(streams.length,1);assert.equal(native.innerHTML,saved);
    const closed=context.hydrateExecutionEvidence(root);
    await turn();releaseSnapshot({execution_events:[],work_events:[event('completed')],work_status:status({status:'done'}),request_id:'closed',stream_closed:true});
    await closed;
    assert.equal(streams.length,1); // No redundant replay of a closed answer stream.
    assert(native.innerHTML.includes('已持久化完成'));
    context.chatStream=root;context.message=message;
    vm.runInContext('activeWorkflowMessage=message; activeWorkflowRequestId="one";',context);
    const oldActive=context.refreshActiveWorkStatus();await turn();
    vm.runInContext('activeWorkflowRequestId="two";',context);
    const savedCurrent=native.innerHTML;
    releaseSnapshot({work_events:[event('failed')],work_status:status({status:'failed'})});await oldActive;
    assert.equal(native.innerHTML,savedCurrent);
    const current=context.refreshActiveWorkStatus();await turn();
    releaseSnapshot({work_events:[event('completed')],work_status:status({status:'done'})});await current;
    assert(native.innerHTML.includes('已持久化完成'));
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    subprocess.run(["node", "-e", script, str(WEB_DIR / "app.js")], check=True, timeout=20)


def test_legacy_workflow_cache_loses_guessed_topology_not_answer_or_business_notes():
    import subprocess
    script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync(process.argv[1],'utf8'),removed=[],notes=[{text:'note one'},{text:'note two'}];
const preview={textContent:'6/6 完成'},group={querySelector:()=>({children:notes}),replaceWith:(...children)=>{assert.deepEqual(children,notes);removed.push('group');}};
const old={dataset:{},querySelector:selector=>selector==='.message-inline-process-preview'?preview:null,
    querySelectorAll:selector=>selector==='.workflow-step-chip-group'?[group]:[{remove:()=>removed.push('rail')}]};
const current={dataset:{executionFormat:'native-v1'},querySelector:()=>null,querySelectorAll:()=>{throw Error('native evidence must not be rewritten');}};
let evidenceHtml='';
let placedAfterAnswer=0;
const evidence={dataset:{evidenceView:'v2'},querySelector:selector=>selector==='details'?{}:null};
const message={querySelector:selector=>selector==='.response-evidence'?(evidenceHtml?evidence:null):
    selector==='.message-main-copy'?{insertAdjacentHTML:(position,html)=>{assert.equal(position,'afterend');evidenceHtml=html;},after:node=>{assert.equal(node,evidence);placedAfterAnswer++;}}:null,querySelectorAll:()=>[]};
const root={answer:'original answer',citation:'full original citation',querySelectorAll:selector=>selector==='.message-ready'?[message]:[old,current]};
const context={isUserAuthenticated:false,currentUserAccountProfile:''};vm.createContext(context);
for(const name of ['escapeHtml','buildResponseEvidenceModel','buildResponseEvidenceHtml']) {
    const start=source.indexOf('function '+name+'(');
    vm.runInContext(source.slice(start,source.indexOf('\n}\n',start)+3),context);
}
vm.runInContext(source.slice(source.indexOf('function repairLegacyWorkflowSummaries('),source.indexOf('async function hydrateExecutionEvidence(')),context);
context.repairLegacyWorkflowSummaries(root);
assert(evidenceHtml.includes('未记录来源覆盖情况'));
assert.equal(old.dataset.executionFormat,'legacy-unrecorded');
assert(preview.textContent.includes('无原生执行证据'));
assert.deepEqual(removed,['rail','group']);
assert.equal(root.answer,'original answer');assert.equal(root.citation,'full original citation');
context.repairLegacyWorkflowSummaries(root);assert.equal(removed.length,2);
assert.equal(placedAfterAnswer,2,'old evidence follows the preserved answer on every hydration');
'''
    subprocess.run(["node", "-e", script, str(WEB_DIR / "app.js")], check=True, timeout=20)


def test_native_execution_timeline_uses_attempt_identity_not_labels_or_completion_guess():
    import subprocess
    script = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const source = fs.readFileSync(process.argv[1], 'utf8');
const context = {escapeHtml:s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;')};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('function buildNativeExecutionHtml('),source.indexOf('function syncNativeExecutionDisplay(')), context);
let sequence = 0;
const event = (id,state,key) => ({version:1,event_id:'event-'+(++sequence),sequence,execution_id:id,state,operator_key:key,program_uri:'flow://synthetic',next_nodes:[],wave_size:2});
const html = context.buildNativeExecutionHtml([event('one','started','same'), event('two','started','same'),event('one','failed','same')]);
assert.equal((html.match(/<li /g)||[]).length,2);
assert(html.includes('执行失败') && html.includes('执行中'));
assert(!html.includes('data-execution-state="completed"'));
assert(html.includes('同批调度 2 个节点') && html.includes('同批调度不等于同时执行'));
assert(context.buildNativeExecutionHtml([]).includes('不按步骤名称推断'));
assert(!context.buildNativeExecutionHtml([event('x','recovered','<script>')]).includes('<script>'));
assert(context.buildNativeExecutionHtml([event('x','cancelled','safe')]).includes('已取消'));
const started = event('out-of-order','started','safe'), ended = event('out-of-order','completed','safe');
assert(context.buildNativeExecutionHtml([ended,started]).includes('data-execution-state="completed"'));
vm.runInContext('let activeExecutionEvents = []',context);
vm.runInContext(source.slice(source.indexOf('function mergeNativeExecutionEvents('),source.indexOf('function buildVisibleWorkflowSteps(')),context);
context.mergeNativeExecutionEvents([started]);
context.mergeNativeExecutionEvents([started,ended]);
assert.equal(vm.runInContext('activeExecutionEvents.length',context),2);
assert.equal(vm.runInContext('activeExecutionEvents[0].event_id',context),started.event_id);
assert(source.includes('payload.type === "execution-event"'));
assert(source.includes('response.execution_events || []'));
assert(source.includes('exchange.execution_events || []'));
'''
    subprocess.run(["node", "-e", script, str(WEB_DIR / "app.js")], check=True, timeout=20)


def test_native_workflow_graph_uses_authoritative_topology():
    import subprocess
    subprocess.run(["node", "--test", str(WEB_DIR.parents[2] / "tests/native_workflow_graph.test.cjs")],
                   check=True, timeout=20)


def test_response_evidence_layers_and_same_answer_comparison(tmp_path):
    import hashlib
    import json
    import subprocess

    root = WEB_DIR.parents[2]
    subprocess.run(['node', '--test', str(root / 'tests/response_evidence.test.cjs')], check=True, timeout=20)
    subprocess.run(['node', str(root / 'tools/compare_response_evidence.cjs'), str(tmp_path)], check=True, timeout=20)
    report = json.loads((tmp_path / 'comparison.json').read_text())
    assert report['humanEvaluation'] == 'deferred by user'
    assert len(report['cases']) == 6
    for case in report['cases']:
        assert all(case['technicalOnly'].values())
        assert case['answerHash'] == hashlib.sha256(case['answer'].encode()).hexdigest()
        assert set(case['arms']) == {'A', 'B', 'C'}


def test_business_notes_cannot_reintroduce_inferred_phase_topology():
    import subprocess
    script = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const source = fs.readFileSync(process.argv[1], 'utf8');
const context = {};
vm.createContext(context);
assert(!source.includes('WORKFLOW_PHASE_DEFINITIONS'));
assert(!source.includes('function buildWorkflowPhaseRailHtml('));
assert(!source.includes('function deriveWorkflowPhaseStates('));
assert(!source.includes('function inferWorkflowPhaseKey('));
vm.runInContext(source.slice(source.indexOf('function normalizeWorkflowStepStatus('),
    source.indexOf('function stripNotificationText(')), context);
assert.equal(context.normalizeWorkflowStepStatus('failed'),'failed');
assert.equal(context.normalizeWorkflowStepStatus('error'),'failed');
assert.equal(context.normalizeWorkflowStepStatus('queued'),'queued');
assert.equal(context.normalizeWorkflowStepStatus('retrying'),'active');
assert.equal(context.normalizeWorkflowStepStatus(undefined),'unknown');
assert.equal(context.normalizeWorkflowStepStatus('future-status'),'unknown');
assert.equal(context.normalizeWorkflowStepStatus('skipped'),'skipped');
context.buildWorkflowStepChipHtml = step => step.key;
vm.runInContext(source.slice(source.indexOf('function buildWorkflowChipRowHtml('),
    source.indexOf('function buildWorkflowStepChipHtml(')), context);
assert.equal(context.buildWorkflowChipRowHtml([{key:'memory_retrieve',parallel_group:'retrieval'},
    {key:'follow_up_plan',parallel_group:'post_answer'}]),'memory_retrievefollow_up_plan');
assert(!source.includes('function inferWorkflowParallelGroup('));
assert(!source.includes('function buildWorkflowDagDisplaySteps('));
assert(!source.includes('WORKFLOW_PARALLEL_GROUP_FALLBACK_RULES'));
assert(!source.includes('WORKFLOW_PARALLEL_GROUP_LABELS'));
'''
    subprocess.run(["node", "-e", script, str(WEB_DIR / "app.js")], check=True, timeout=20)


def test_natural_starters_cover_all_roles_and_failed_catalog_fallback():
    import subprocess
    subprocess.run(["node", str(WEB_DIR.parents[2] / "tests/natural_starters.test.cjs")],
                   check=True, timeout=20)



def test_authenticated_account_has_slack_binding_entry():
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    panel = html.split('id="user-session-panel"', 1)[1].split('class="drawer-separator"', 1)[0]
    assert 'id="open-slack-account-link"' in panel
    script = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    handler = script.split('getElementById("open-slack-account-link")', 1)[1].split('});', 1)[0]
    assert 'openAccountView("login")' in handler
    account_view = script.split('function openAccountView(', 1)[1].split('function switchAccountTab', 1)[0]
    assert 'refreshSlackTwinLinkStatus()' in account_view


def test_no_evidence_answer_explains_absence_instead_of_hiding_support() -> None:
    app_js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    assert 'externalEvidenceRequired ? "未附可核验来源"' in app_js
    assert '当前无法核对事实依据。可补充资料或指定要查询的来源。' in app_js
    assert '${escapeHtml(model.gap)}' in app_js


def test_answer_basis_is_collapsed_by_default() -> None:
    app_js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    answer_basis = app_js.split('function buildResponseEvidenceHtml(', 1)[1].split('\nfunction ', 1)[0]
    assert '<details><summary>回答依据' in answer_basis
    assert '<details open' not in answer_basis
    assert '${sourceDetailsHtml}' in answer_basis


def test_self_contained_answer_does_not_show_missing_evidence_warning() -> None:
    app_js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    assert 'externalEvidenceRequired = true' in app_js
    assert '(!sourceCount && externalEvidenceRequired)' in app_js
    assert 'data.external_evidence_required !== false' in app_js
    assert 'response.external_evidence_required !== false' in app_js


def test_chat_messages_share_centered_composer_column() -> None:
    theme = (WEB_DIR / "minimal.1009.css").read_text(encoding="utf-8")
    stream = theme.split(".chat-stream {", 1)[1].split("}", 1)[0]
    message = theme.split(".message {", 1)[1].split("}", 1)[0]
    composer = theme.split(".composer-inner {", 1)[1].split("}", 1)[0]
    assert "width: 100%" in stream
    assert "width: min(100%, 760px)" in message
    assert "margin-inline: auto" in message
    assert "width: min(100%, 760px)" in composer


def test_mobile_source_scroll_targets_respect_composer_clearance() -> None:
    css = (WEB_DIR / "styles.css").read_text(encoding="utf-8")
    assert "scroll-padding-bottom: calc(var(--chat-composer-height, 140px) + 12px)" in css


def test_long_source_identifiers_cannot_widen_support_grid() -> None:
    css = (WEB_DIR / "styles.css").read_text(encoding="utf-8")
    grid = css.split(".message-basis-list {", 1)[1].split("}", 1)[0]
    card = css.split(".message-basis-item {", 1)[1].split("}", 1)[0]
    assert "grid-template-columns: minmax(0, 1fr)" in grid
    assert "min-width: 0" in card
    assert "overflow-wrap: anywhere" in card


def test_chat_stop_control_cancels_http_and_server_request() -> None:
    app_js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    styles = (WEB_DIR / "styles.css").read_text(encoding="utf-8")

    assert 'chatSubmitButton.dataset.mode = loading ? "stop" : "send"' in app_js
    assert 'activeChatAbortController?.abort("user-cancelled")' in app_js
    assert "/chat/cancel?request_id=" in app_js
    assert 'globalThis.addEventListener("pagehide"' in app_js
    assert ".send-button.is-sending .send-button-spinner" in styles

# Each entry: (endpoint, list_of_dom_ids_used_in_payload)
# These IDs are read by app.js to construct JSON POST bodies.
API_PAYLOAD_CONTRACTS = [
    ("/auth/admin/login", ["admin-username", "admin-password"]),
    ("/auth/user/register", [
        "user-register-name",
        "user-register-email",
        "user-register-profile",
        "user-register-password",
    ]),
    ("/auth/user/login", ["user-login-email", "user-login-password"]),
    ("/chat", [
        "chat-question",
        "student-name",
        "student-email",
        "course-context",
        "visitor-profile",
        "deep-thinking-checkbox",
    ]),
    ("/knowledge", [
        "knowledge-title",
        "knowledge-source",
        "knowledge-content",
        "knowledge-tags",
    ]),
    ("/bookings/proposals", [
        "booking-student-name",
        "booking-email",
        "booking-topic",
        "booking-start",
        "booking-end",
    ]),
    ("/suggestions", ["suggestion-message", "suggestion-category"]),
]

# Known missing IDs that exist in app.js but not in index.html.
# These use optional chaining in JS and do NOT affect API payloads.
# This count must only decrease over time (ratchet).
KNOWN_MISSING_ID_CEILING = 43


def _extract_html_ids(html: str) -> set[str]:
    return set(re.findall(r'id="([^"]+)"', html))


def _extract_js_element_ids(js: str) -> set[str]:
    return set(re.findall(r'getElementById\("([^"]+)"\)', js))


def test_api_payload_dom_ids_exist_in_html() -> None:
    """Every DOM ID used to build an API POST payload must exist in HTML."""
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    html_ids = _extract_html_ids(html)

    missing: list[tuple[str, str]] = []
    for endpoint, dom_ids in API_PAYLOAD_CONTRACTS:
        for dom_id in dom_ids:
            if dom_id not in html_ids:
                missing.append((endpoint, dom_id))

    assert not missing, (
        "DOM IDs used in API payloads are missing from index.html:\n"
        + "\n".join(f"  {endpoint}: #{dom_id}" for endpoint, dom_id in missing)
    )


def test_missing_element_ids_do_not_grow() -> None:
    """The number of JS getElementById refs missing from HTML must not increase."""
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    js = (WEB_DIR / "app.js").read_text(encoding="utf-8")

    html_ids = _extract_html_ids(html)
    js_ids = _extract_js_element_ids(js)
    missing = js_ids - html_ids

    assert len(missing) <= KNOWN_MISSING_ID_CEILING, (
        f"Missing ID count grew to {len(missing)} (ceiling is {KNOWN_MISSING_ID_CEILING}).\n"
        f"New missing IDs:\n"
        + "\n".join(f"  #{mid}" for mid in sorted(missing))
    )


def test_conversation_history_storage_is_scoped_per_account() -> None:
    """Local conversation history must be partitioned by guest/user scope."""
    js = (WEB_DIR / "app.js").read_text(encoding="utf-8")

    assert "function resolveConversationHistoryStorageScope()" in js
    assert "function switchConversationHistoryScope(nextScope" in js
    assert "return `${CHAT_HISTORY_STORAGE_KEY}:${scope || \"guest\"}`;" in js
    assert "return `${CHAT_HISTORY_META_STORAGE_KEY}:${scope || \"guest\"}`;" in js


def test_history_sync_uses_authenticated_session_email_only() -> None:
    js = (WEB_DIR / "app.js").read_text(encoding="utf-8")

    assert "let currentUserAccountEmail = \"\";" in js
    assert "currentUserAccountEmail = authenticated ? String(session.account?.email || \"\").trim().toLowerCase() : \"\";" in js
    assert "return currentUserAccountEmail || \"\";" in js
    assert "student_email" not in js[js.index("async function syncConversationHistoryFromServer"):js.index("function setHistoryRailCollapsed")]


def test_send_button_uses_animation_state() -> None:
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    css = (WEB_DIR / "styles.css").read_text(encoding="utf-8")
    js = (WEB_DIR / "app.js").read_text(encoding="utf-8")

    assert "send-button-label" in html
    assert "send-button-icon" in html
    assert "send-button-spinner" in html
    assert "function setChatSubmitLoading(isLoading)" in js
    assert "chatSubmitButton.classList.toggle(\"is-sending\", loading)" in js
    assert "chatSubmitButton.textContent = \"发送中\"" not in js
    assert ".send-button.is-sending" in css
    assert "@keyframes send-spinner-spin" in css
    assert "@keyframes send-button-pulse" in css


def test_chat_scrollbar_is_native_accessible_and_edge_aligned() -> None:
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    css = (WEB_DIR / "styles.css").read_text(encoding="utf-8")
    js = (WEB_DIR / "app.js").read_text(encoding="utf-8")

    assert 'id="chat-scroll-rail"' not in html
    assert 'id="chat-stream" class="chat-stream" tabindex="0" role="region"' in html
    assert "width: 100%;" in css[css.index(".chat-stream {"):css.index(".chat-stream:has")]
    stream_css = css[css.index(".chat-stream {"):css.index(".chat-stream:has")]
    assert "scrollbar-width: auto;" in stream_css
    assert "scrollbar-width: none;" not in stream_css
    assert "scrollbar-gutter: stable;" in stream_css
    assert "syncChatScrollRail" not in js
    assert "justify-content: safe center;" in css


def test_latest_button_is_explicit_accessible_and_outside_history() -> None:
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    css = (WEB_DIR / "styles.css").read_text(encoding="utf-8")
    js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    assert html.index('id="chat-form"') < html.index('id="chat-scroll-latest"')
    assert 'type="button" id="chat-scroll-latest"' in html
    assert 'aria-label="回到最新" aria-controls="chat-stream" hidden' in html
    assert 'class="chat-latest-notice" aria-live="polite" aria-atomic="true" hidden' in html
    assert '.composer-shell > .chat-latest-button {' in css
    assert 'bottom: calc(100% + 8px);' in css
    assert 'addEventListener("click", scrollConversationToLatest)' in js
    assert 'if (label.textContent !== notice)' in js


def test_interface_icons_share_one_accessible_svg_system() -> None:
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    css = (WEB_DIR / "styles.css").read_text(encoding="utf-8")
    js = (WEB_DIR / "app.js").read_text(encoding="utf-8")

    assert 'class="ui-icon-sprite"' in html
    for symbol in (
        "menu", "companion", "dice", "brain", "globe-search", "mic",
        "send", "workflow", "copy", "retry", "sparkle", "bean",
    ):
        assert f'id="icon-{symbol}"' in html
    assert 'href="#icon-brain"' in html
    assert 'href="#icon-globe-search"' in html
    assert 'href="#icon-dice"' in html
    assert "function uiIconSvg(name, className = \"\")" in js
    assert 'uiIconSvg("copy", "ui-icon-sm")' in js
    assert 'function buildWorkflowPhaseIconSvg(' not in js
    assert ".ui-icon {" in css
    assert ".ui-icon-dot {" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "🧠 深度思考" not in html
    assert "🌐 联网检索" not in html
    assert "🎲" not in html
    assert "👤" not in js


def test_sage_companion_is_local_accessible_and_lifecycle_driven() -> None:
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    css = (WEB_DIR / "styles.css").read_text(encoding="utf-8")
    companion_css = (WEB_DIR / "companion.css").read_text(encoding="utf-8")
    js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    companion_js = (WEB_DIR / "companion.js").read_text(encoding="utf-8")

    assert 'id="sage-companion"' in html
    assert 'id="sage-companion-toggle"' in html
    assert 'aria-controls="sage-companion-panel"' in html
    assert 'aria-grabbed="false"' in html
    assert "拖动小 Sage 移动" in html
    assert 'id="sage-companion-message"' in html
    assert 'aria-live="polite"' in html
    assert "名字、外观与互动进度仅保存在当前浏览器" in html
    assert 'id="open-companion-settings"' in html
    assert 'id="sage-companion-name-input"' in html
    assert 'id="sage-companion-temperament"' in html
    assert 'id="sage-companion-sound"' in html
    assert 'id="sage-companion-answers"' in html
    assert 'id="sage-companion-streak"' in html
    assert 'id="sage-companion-quest-text"' in html
    assert 'data-companion-action="complete-quest"' in html
    assert "sage-companion-accessory-crown" in html
    assert 'role="tablist"' in html
    assert 'data-companion-tab="companion"' in html
    assert 'data-companion-tab="customize"' in html
    assert 'id="sage-companion-scroll-cue"' in html

    assert '<script src="./companion.js" defer></script>' in html
    assert '<link rel="stylesheet" href="./companion.css" />' in html
    assert 'const STORAGE_KEY = "sageMateCompanion:v3";' in companion_js
    assert 'Object.freeze(["sageMateCompanion:v2", "sageMateCompanion:v1"])' in companion_js
    assert "localStorage.setItem(STORAGE_KEY" in companion_js
    assert "globalThis.SageCompanion = Object.freeze" in companion_js
    assert "${message}。点击" not in companion_js
    assert "${message} 点击${action}伙伴面板" in companion_js
    assert 'sageCompanionController?.setState("thinking"' in js
    assert js.count("sageCompanionController?.recordAnswerCompleted();") == 2
    # HTTP and answer_done count the answer idempotently; durable completion
    # additionally settles a late background visual state without recounting.
    assert js.count("sageCompanionController?.recordAnswerCompleted()") == 3
    assert 'if (sageCompanionController?.recordAnswerCompleted() === false)' in js
    assert 'sageCompanionController.setState("idle", "回答已完成。")' in js
    assert 'sageCompanionController?.setState("worried"' in js
    persist_block = companion_js[companion_js.index("persist() {"):companion_js.index("idleMessage() {")]
    assert "question:" not in persist_block
    assert "fetch(" not in companion_js
    assert "XMLHttpRequest" not in companion_js
    assert "question:" not in companion_js
    assert "answer:" not in companion_js

    assert ".sage-companion-toggle" not in css
    assert "body.onboarding-active .sage-companion" in companion_css
    assert ".chat-shell.view-active .sage-companion" in companion_css
    assert '@media (prefers-reduced-motion: reduce)' in companion_css
    assert '.sage-companion[data-appearance="mint"]' in companion_css
    assert ".sage-companion-tabs" in companion_css
    assert "handleTabKeydown(event)" in companion_js
    assert "setPointerCapture" in companion_js
    assert "saveManualPosition()" in companion_js
    assert "positionX: null" in companion_js
    assert "canWander()" in companion_js
    assert "this.motionQuery?.matches" in companion_js
    assert "prefers-reduced-motion: reduce" in companion_css
    assert "sage-companion-panel-enter" in companion_css
    assert "sage-companion-bond-shine" in companion_css
    assert '.sage-companion[data-stage="confidant"]' in companion_css
    assert "@keyframes sage-companion-spark" in companion_css
    assert ".sage-companion-footprint" in companion_css
    assert ".sage-companion-quest" in companion_css
    assert ".sage-companion.is-dragging" in companion_css
    assert ".sage-companion.is-walking" in companion_css
    assert "touch-action: none" in companion_css
    assert "@keyframes sage-companion-walk" in companion_css
    assert 'data-panel-side="below"' in companion_css


def test_local_code_setup_does_not_block_or_auto_open_profile_modal() -> None:
    js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    setup_fn = js[js.index("function shouldShowSageMateSetup"):js.index("async function maybeOpenSageMateSetup")]

    assert "if (!isLocalCodeSetupUrl) return false;" in setup_fn
    assert "return !data.api_key_set" not in setup_fn
    assert "const localCodeConfigPromise = maybeOpenSageMateSetup();" in js
    assert "await maybeOpenSageMateSetup();" not in js
    assert "initializePage().catch(" in js


def test_auto_scientist_keeps_research_profile_in_frontend_payloads() -> None:
    js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    onboarding_fn = js[js.index("function currentOnboardingProfile"):js.index("function currentChatVisitorProfile")]
    visitor_fn = js[js.index("function currentChatVisitorProfile"):js.index("function hasCompletedOnboarding")]

    assert 'if (profile === "auto_scientist") return "auto_scientist";' in onboarding_fn
    assert 'if (profile === "auto_scientist") return "lab_member";' in visitor_fn
    assert "visitor_profile: currentChatVisitorProfile()," in js


def test_account_entry_stays_in_sidebar_not_topbar() -> None:
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    js = (WEB_DIR / "app.js").read_text(encoding="utf-8")

    assert 'id="sidebar-user-icon"' in html
    assert 'id="topbar-user-badge" class="topbar-user-badge hidden"' in html
    assert 'topbarUserBadge.classList.remove("hidden"' not in js
    assert 'topbarUserBadge.classList.add("hidden")' in js


def test_empty_chat_onboarding_sits_before_chat_stream() -> None:
    """Faculty Twin onboarding uses the left empty column during guided mode."""
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    css = (WEB_DIR / "styles.css").read_text(encoding="utf-8")
    empty_chat_blocks = re.findall(r"(?m)^\.chat-shell\.chat-empty \{\n(.*?)\n\}", css, re.S)

    assert empty_chat_blocks
    assert html.index('id="onboarding-card"') < html.index('id="chat-stream"')
    assert all("justify-content: center;" not in block for block in empty_chat_blocks)
    assert all("align-items: center;" not in block for block in empty_chat_blocks)
    assert ".chat-shell.chat-empty .onboarding-card" in css
    assert ".chat-shell.chat-empty .composer-shell" in css
    assert "width: min(100%, var(--content-column-max));" in css


def test_active_onboarding_hides_empty_stream_to_keep_guidance_focused() -> None:
    css = (WEB_DIR / "styles.css").read_text(encoding="utf-8")
    selector = "body.onboarding-active .chat-shell.chat-empty .chat-stream"
    block_match = re.search(rf"{re.escape(selector)} \{{\n(.*?)\n\}}", css, re.S)

    assert block_match, f"Missing CSS block for {selector}"
    assert "display: none;" in block_match.group(1)


def test_pending_assistant_process_surface_uses_answer_column_width() -> None:
    css = (WEB_DIR / "styles.css").read_text(encoding="utf-8")
    selector = ".message-assistant.message-pending .message-bubble"
    block_match = re.search(rf"{re.escape(selector)} \{{\n(.*?)\n\}}", css, re.S)

    assert block_match, f"Missing CSS block for {selector}"
    assert "width: min(100%, 800px);" in block_match.group(1)


def test_active_onboarding_uses_one_centered_column_even_after_chat_starts() -> None:
    css = (WEB_DIR / "styles.css").read_text(encoding="utf-8")
    shell_selector = "body.onboarding-active .chat-shell"
    selector = "body.onboarding-active .chat-shell .onboarding-card"
    shell_block = re.search(rf"{re.escape(shell_selector)} \{{\n(.*?)\n\}}", css, re.S)
    block_match = re.search(rf"{re.escape(selector)} \{{\n(.*?)\n\}}", css, re.S)

    assert shell_block, f"Missing CSS block for {shell_selector}"
    assert "display: flex;" in shell_block.group(1)
    assert "flex-direction: column;" in shell_block.group(1)
    assert "grid-template-columns:" not in shell_block.group(1)
    assert block_match, f"Missing CSS block for {selector}"
    assert "width: min(100%, 720px);" in block_match.group(1)
    assert "margin: clamp(16px, 4vh, 36px) auto 12px;" in block_match.group(1)
    assert "grid-column:" not in block_match.group(1)


def test_restoring_a_conversation_hides_the_empty_state_onboarding() -> None:
    js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    restore_fn = js[
        js.index("async function restoreConversationFromHistory"):
        js.index("async function fetchConversationTranscript")
    ]
    transcript_fn = js[
        js.index("function renderConversationTranscript"):
        js.index("function ", js.index("function renderConversationTranscript") + 1)
    ]

    assert 'chatStream.querySelector(".message-user")' in restore_fn
    assert "hideOnboardingCard();" in restore_fn
    assert "hideOnboardingCard();" in transcript_fn

    hide_fn = js[js.index("function hideOnboardingCard"):js.index("let onboardingHintTimer")]
    assert 'document.getElementById("seed-chips")?.classList.add("hidden");' in hide_fn


def test_default_chat_is_quiet_and_guidance_is_opt_in() -> None:
    js = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    initialize_fn = js[js.index("async function initializePage"):js.index("initializePage().catch")]
    session_fn = js[js.index("function applyUserSession"):js.index("async function loadManagedServices")]
    restart_fn = js[js.index("function restartOnboarding"):js.index("// --- iOS Safari")]

    assert "startOnboarding(" not in initialize_fn
    assert "startOnboarding(" not in session_fn
    assert "showDefaultLandingContent();" in initialize_fn
    assert "startOnboarding(profile, { force: true });" in restart_fn


def test_advanced_composer_controls_are_collapsed_into_one_tools_disclosure() -> None:
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    css = (WEB_DIR / "styles.css").read_text(encoding="utf-8")

    assert '<details class="composer-tools-disclosure">' in html
    assert 'class="composer-tools-summary"' in html
    assert html.index('class="composer-tools-summary"') < html.index('class="composer-tools-row"')
    assert ".composer-tools-disclosure:not([open]) .composer-tools-row" in css
    assert ".composer-tools-disclosure:has(input:checked) .composer-tools-active-label" in css


def test_diagnostics_do_not_compete_with_the_core_chat_surface() -> None:
    css = (WEB_DIR / "styles.css").read_text(encoding="utf-8")

    assert re.search(
        r"\.app-stack-banner,\s*\n\.app-version-badge \{\s*\n\s*display: none;",
        css,
    )


def test_minimal_theme_is_shared_across_primary_and_secondary_surfaces() -> None:
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    theme = (WEB_DIR / "minimal.1009.css").read_text(encoding="utf-8")

    assert html.index("styles.4222.css") < html.index("minimal.1009.css")

    assert "--bg: #ffffff;" in theme
    assert "--accent: #191918;" in theme
    assert "--shadow: none;" in theme
    assert ".sidebar,\n.sidebar-rail" in theme
    assert ".composer-row" in theme
    assert ".message-user .message-bubble" in theme
    assert ".onboarding-card," in theme
    assert ".chat-shell.view-active .onboarding-card" in theme
    assert ".inline-status," in theme
    assert ".list-card::before," in theme
    assert ".workflow-shell-head" in theme
    assert ".modal-panel," in theme
    assert "backdrop-filter: none;" in theme
    assert "linear-gradient" not in theme


def test_completed_answers_keep_a_collapsed_inline_process_summary() -> None:
    js = (WEB_DIR / "app.js").read_text(encoding="utf-8")

    assert "const INLINE_WORKFLOW_TRACE_ENABLED = true;" in js
    summary_fn = js[
        js.index("function buildWorkflowStatusSummaryHtml"):
        js.index("function buildVisibleWorkflowSteps")
    ]
    assert 'class="message-workflow-summary message-section"' in summary_fn
    assert 'aria-expanded="false"' in summary_fn
    assert '<strong class="message-section-title">详情</strong>' in summary_fn
    assert '<div class="message-section-content" hidden>' in summary_fn
    assert summary_fn.index('<div class="message-section-content" hidden>') < summary_fn.index(
        "buildNativeExecutionHtml(executionEvents)"
    )
    assert "buildWorkflowPhaseRailHtml({" not in summary_fn
    assert "原生执行状态未记录" in summary_fn


def test_live_progress_uses_one_compact_surface_inside_the_message() -> None:
    js = (WEB_DIR / "app.js").read_text(encoding="utf-8")

    pending_fn = js[
        js.index("function renderPendingAssistantMessage"):
        js.index("function updatePendingAssistantMessage")
    ]
    compact_fn = js[
        js.index("function buildPendingWorkflowTraceCompactHtml"):
        js.index("function buildWorkflowChipRowHtml")
    ]
    assert 'aria-label="高级执行详情"' in pending_fn
    assert '<details class="thinking-trace response-advanced"' in pending_fn
    assert "thinking-phase-rail" not in pending_fn
    assert "thinking-trace-chip-row" not in compact_fn
    assert "业务记录" in compact_fn
    assert "已完成 ${completedSteps.length} 步" not in compact_fn
    native_sync = js[js.index("function syncNativeExecutionDisplay("):js.index("function mergeNativeExecutionEvents(")]
    assert '.message-pending .thinking-trace-list' in native_sync
    assert 'buildNativeExecutionHtml(activeExecutionEvents)' in native_sync


def test_compact_process_summary_hides_details_until_requested() -> None:
    css = (WEB_DIR / "styles.css").read_text(encoding="utf-8")

    assert ".message-workflow-summary .message-section-content[hidden]" in css
    assert ".message-workflow-summary .message-inline-process-preview" in css
    assert "text-overflow: ellipsis;" in css
    assert ".message-process-icon" in css


def test_active_onboarding_collapses_to_viewport_safe_mobile_column() -> None:
    css = (WEB_DIR / "styles.css").read_text(encoding="utf-8")
    responsive_start = css.index(
        "/* Keep guided onboarding inside tablet and phone visual viewports. */"
    )
    responsive_end = css.index("/* --- Onboarding guided question card --- */")
    responsive_css = css[responsive_start:responsive_end]

    assert "@media (max-width: 920px)" in responsive_css
    assert "display: flex;" in responsive_css
    assert "flex-direction: column;" in responsive_css
    assert "flex: 0 0 auto;" in responsive_css
    assert "width: min(100%, var(--content-column-max));" in responsive_css
    assert "padding: var(--mobile-panel-padding);" in responsive_css
    assert "overflow-x: hidden;" in responsive_css
    assert "position: relative;" in responsive_css
    assert "left: auto;" in responsive_css
    assert "right: auto;" in responsive_css
    assert "bottom: auto;" in responsive_css
    assert "minmax(280px" not in responsive_css


def test_cross_route_theme_contract_uses_split_semantic_roles() -> None:
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    css = (WEB_DIR / "styles.css").read_text(encoding="utf-8")

    for role in ("info", "success", "warning", "error"):
        assert f"--{role}-surface:" in css
        assert f"--{role}-text:" in css
        assert f"--{role}-border:" in css
    assert "--control-border:" in css
    assert "--focus-ring:" in css
    assert "--success:" not in css
    assert "--warning:" not in css
    assert "--error:" not in css
    assert 'id="sidebar-user-icon" class="rail-user rail-btn" title="账号设置"' in html
    assert 'aria-label="账号设置"' in html
    assert re.search(r'href="\./styles\.4222\.css\?rev=[^"\s]+"', html)
    assert re.search(r'src="\./app\.4222\.js\?rev=[\w-]+"', html)
