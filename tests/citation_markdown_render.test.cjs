const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../src/sage_faculty_twin/web/app.js'), 'utf8');
// Execute actual pure rendering functions, without a browser or application startup.
const functions = ['cleanAnswerBasisDetail', 'formatMessageContent', 'escapeHtml', 'buildAnswerBasisItemHtml', 'buildFollowUpActionHtml'];
const sandbox = {formatAnswerBasisCard: item => item, formatFollowUpActionCard: item => item};
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
const code = '# Not a heading\n**not bold**\n[not a link](https://example.org)\n    indented';
assert.equal(sandbox.formatMessageContent('```text\n' + code + '\n```'), '<pre>' + code + '</pre>');
assert.equal(sandbox.formatMessageContent('`**literal**`'), '<code>**literal**</code>');
assert.ok(sandbox.formatMessageContent('#### Heading\n\n* first\n+ second').includes('<h4>Heading</h4>'));
assert.ok(sandbox.formatMessageContent('* first\n+ second').includes('<li>second</li>'));
assert.ok(sandbox.buildAnswerBasisItemHtml({label:'Wiki', title:'Test', detail:'# Body', source_url:'https://example.org/wiki'}).includes('data-citation-url="https://example.org/wiki"'));
assert.ok(sandbox.buildFollowUpActionHtml({label:'Task', title:'Read', detail:'Next'}).includes('follow-up-action'));
