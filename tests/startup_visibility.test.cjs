const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../src/sage_faculty_twin/web/index.html'), 'utf8');

// A timer placed after render-blocking CSS is not a reliable visibility fallback:
// the parser can wait on that CSS before even registering the timer. Background
// tabs can also delay timer delivery. The static shell must stay visible without
// executing any script or resolving a FontFaceSet promise.
const head = html.slice(0, html.indexOf('</head>'));
assert(!/\bhtml\s*\{[^}]*\bopacity\s*:\s*0\b/s.test(head));
assert(!/\bhtml\s*\{[^}]*\bvisibility\s*:\s*hidden\b/s.test(head));
assert(!/document\.documentElement\.style\.(opacity|visibility)\s*=/.test(head));
assert(!head.includes('document.fonts.ready'));
assert(head.includes('media="print"'));
assert(head.includes("onload=\"this.media='all'\""));
assert(html.includes('<body'));
assert(html.includes('请写下你的问题'));
console.log('Static first paint does not depend on font promises or timers');
