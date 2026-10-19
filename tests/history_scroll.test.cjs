const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../src/sage_faculty_twin/web/app.js'), 'utf8');
function classes(initial = []) {
    const values = new Set(initial);
    return {add: x => values.add(x), remove: x => values.delete(x),
        contains: x => values.has(x), toggle: (x, on) => on ? values.add(x) : values.delete(x)};
}
let messages = false;
const shell = {classList: classes(['chat-empty'])};
const greeting = {classList: classes()};
const button = {};
const body = {classList: classes()};
const context = {chatShell: shell, chatStream: {
    querySelector: () => messages ? {} : null,
    querySelectorAll: () => messages ? [{}] : [],
}, document: {body, getElementById: id => id === 'export-conversation' ? button : greeting}};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('function updateChatEmptyState()'), source.indexOf('// Reserve the actual composer height')), context);
vm.runInContext(source.slice(source.indexOf('function syncConversationMode()'), source.indexOf('// Older local snapshots')), context);
context.syncConversationMode();
assert(shell.classList.contains('chat-empty'));
assert.equal(button.disabled, true);
// Restoring saved HTML does not call appendMessage.
messages = true;
context.syncConversationMode();
assert(!shell.classList.contains('chat-empty'), 'restored history must not retain centered welcome layout');
assert(body.classList.contains('conversation-active'));
assert(greeting.classList.contains('hidden'));
assert.equal(button.disabled, false);
messages = false;
context.syncConversationMode();
assert(shell.classList.contains('chat-empty'));
assert(!body.classList.contains('conversation-active'));
console.log('History restore: welcome → saved conversation → empty layout transitions passed');
