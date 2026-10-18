const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../src/sage_faculty_twin/web/app.js'), 'utf8');
// Execute actual pure rendering functions, without a browser or application startup.
const functions = ['cleanAnswerBasisDetail', 'formatMessageContent', 'escapeHtml', 'buildAnswerBasisItemHtml', 'buildFollowUpActionHtml'];
const sandbox = {URL, formatAnswerBasisCard: item => item, formatFollowUpActionCard: item => item};
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
const base = 'https://source.example/docs/readme.md';
for (const [relative, expected] of [
  ['./LICENSE', 'https://source.example/docs/LICENSE'],
  ['../guide', 'https://source.example/guide'],
  ['/root', 'https://source.example/root'],
  ['#section', 'https://source.example/docs/readme.md#section'],
  ['DEVELOPER.md', 'https://source.example/docs/DEVELOPER.md'],
  ['?a=1&b=2', 'https://source.example/docs/readme.md?a=1&amp;b=2'],
]) {
  const md = `[source](${relative})`;
  assert.ok(sandbox.formatMessageContent(md, base).includes(`href="${expected}"`));
  assert.ok(!sandbox.formatMessageContent(md).includes('<a '));
  assert.ok(sandbox.buildAnswerBasisItemHtml({label:'Wiki', title:'Source', detail:md, source_url:base}).includes(`href="${expected}"`));
}
for (const unsafeBase of ['javascript:alert(1)', 'file:///tmp/source', 'https://user:password@example.org/']) {
  assert.ok(!sandbox.formatMessageContent('[source](./target)', unsafeBase).includes('<a '));
}
assert.ok(!sandbox.formatMessageContent('[source](https://user:password@example.org/)').includes('<a '));
const code = '# Not a heading\n**not bold**\n[not a link](https://example.org)\n    indented';
assert.equal(sandbox.formatMessageContent('```text\n' + code + '\n```'), '<pre>' + code + '</pre>');
assert.equal(sandbox.formatMessageContent('`**literal**`'), '<code>**literal**</code>');
assert.ok(sandbox.formatMessageContent('#### Heading\n\n* first\n+ second').includes('<h4>Heading</h4>'));
assert.ok(sandbox.formatMessageContent('* first\n+ second').includes('<li>second</li>'));
assert.ok(sandbox.buildAnswerBasisItemHtml({label:'Wiki', title:'Test', detail:'# Body', source_url:'https://example.org/wiki'}).includes('data-citation-url="https://example.org/wiki"'));
assert.ok(sandbox.buildFollowUpActionHtml({label:'Task', title:'Read', detail:'Next'}).includes('follow-up-action'));
assert.equal(sandbox.formatMessageContent('- Parent\n    * Child\n    * Child two\n- Next'), '<ul><li>Parent<ul><li>Child</li><li>Child two</li></ul></li><li>Next</li></ul>');
assert.equal(sandbox.formatMessageContent('1. Parent\n    - Child\n        1. Deep\n2. Next'), '<ol><li>Parent<ul><li>Child<ol><li>Deep</li></ol></li></ul></li><li>Next</li></ol>');
assert.equal(sandbox.formatMessageContent('```text\n- Not a list\n    * Literal\n```'), '<pre>- Not a list\n    * Literal</pre>');
