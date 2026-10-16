const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../src/sage_faculty_twin/web/app.js'), 'utf8');
// Execute actual pure rendering functions, without a browser or application startup.
const functions = ['cleanAnswerBasisDetail', 'formatMessageContent', 'escapeHtml'];
const sandbox = {};
for (const name of functions) {
  const start = source.indexOf(`function ${name}(`);
  assert.ok(start >= 0);
  const end = source.indexOf('\nfunction ', start + 1);
  vm.runInNewContext(source.slice(start, end < 0 ? undefined : end), sandbox);
}
const markdown = '# Heading\n\n- first\n- second\n\n| Name | Value |\n| --- | --- |\n| a | b |\n\n```python\nprint(1)\n```\n\n' + 'long text\n'.repeat(150) + '[source](https://example.org/wiki)';
assert.equal(sandbox.cleanAnswerBasisDetail(markdown, 'Title'), markdown);
const html = sandbox.formatMessageContent(markdown);
for (const tag of ['<h1>Heading</h1>', '<ul>', '<table>', '<th>Name</th>', '<td>b</td>', '<pre>print(1)</pre>', 'href="https://example.org/wiki"']) assert.ok(html.includes(tag), tag);
for (const url of ['javascript:alert', 'java\tscript:alert', 'data:text/html,hello', 'vbscript:alert']) {
  assert.ok(!sandbox.formatMessageContent(`[bad](${url})`).includes('<a '));
}
assert.ok(!sandbox.formatMessageContent('<img src=x onerror=alert(1)>').includes('<img'));
console.log('Citation Markdown rendering, full-length preservation and unsafe-link tests passed');
