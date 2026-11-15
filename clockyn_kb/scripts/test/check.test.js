'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { checkKb } = require('../check-kb');
const { buildIndex } = require('../build-index');

const words = (n) => Array.from({ length: n }, (_, i) => 'word' + (i % 50)).join(' ');
const fm = (o) => '---\n' + Object.entries({
  title: 'A title', summary: 'One sentence.', category: 'Set up & Account', audience: 'admin', source: 'guide', handoff: 'none',
  tags: 'one, two', updated: '2026-11-15', ...o,
}).filter(([, v]) => v !== null).map(([k, v]) => `${k}: ${v}`).join('\n') + '\n---\n';
const GUIDE = (extra = '', o = {}) => fm(o) + 'Intro text.\n\n## Do it\n\n1. Tap Add.\n\n' + words(260) + '\n\n' + extra +
  '\n## Common questions\n\n### One?\nA.\n### Two?\nA.\n### Three?\nA.\n### Four?\nA.\n\n## What can go wrong\n\nNothing.\n\n## When to contact support\n\nHave your company name ready.\n';
const FAQ = (o = {}, body = '## How do I do it?\n\nTap Add.\n\n## Why?\n\nBecause.\n') =>
  fm({ title: 'Getting Started', category: 'Set up & Account', audience: 'all', source: 'faq', ...o }) + body;
const PLAYBOOK = fm({ title: 'Rules', category: 'Playbook', audience: 'bot', source: 'playbook' }) + 'Always cite the article. ' + words(50) + '\n';

function tree(files) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'kb-check-'));
  for (const [rel, content] of Object.entries(files)) {
    fs.mkdirSync(path.dirname(path.join(root, rel)), { recursive: true });
    fs.writeFileSync(path.join(root, rel), content);
  }
  return root;
}
const withTree = (files, fn) => {
  const root = tree(files);
  try { return fn(root); } finally { fs.rmSync(root, { recursive: true, force: true }); }
};
const msgs = (r) => r.errors.map((e) => e.msg);
const has = (r, re) => msgs(r).some((m) => re.test(m));

const GOOD = { 'kb/faq/set-up-and-account/getting-started.md': FAQ(), 'kb/guides/add-staff.md': GUIDE(), 'kb/playbook/rules.md': PLAYBOOK };

test('a clean tree passes', () => withTree(GOOD, (root) => {
  const r = checkKb(root);
  assert.deepEqual(msgs(r), []);
  assert.equal(r.stats.documents, 3);
  assert.equal(r.stats.faqQuestions, 2);
}));

test('missing directories and a missing eval file are warnings, not errors', () => withTree({ 'kb/faq/set-up-and-account/getting-started.md': FAQ() }, (root) => {
  const r = checkKb(root);
  assert.deepEqual(msgs(r), []);
  assert.ok(r.warns.some((w) => /MISSING directory/.test(w.msg)));
  assert.ok(r.warns.some((w) => /MISSING/.test(w.msg) && w.file === 'eval/questions.jsonl'));
}));

test('front matter rules', () => {
  withTree({ ...GOOD, 'kb/guides/bad.md': GUIDE('', { audience: 'everyone', handoff: 'maybe', updated: '2026-13-40', tags: '' }) }, (root) => {
    const r = checkKb(root);
    assert.ok(has(r, /audience must be one of/));
    assert.ok(has(r, /handoff must be one of/));
    assert.ok(has(r, /updated must be a real date/));
    assert.ok(has(r, /tags must list/));
  });
  withTree({ 'kb/guides/nofm.md': '## Just text\n' }, (root) => assert.ok(has(checkKb(root), /no front matter/)));
  withTree({ 'kb/guides/g.md': GUIDE('', { summary: 'x'.repeat(201), title: 'y'.repeat(81), source: 'faq' }) }, (root) => {
    const r = checkKb(root);
    assert.ok(has(r, /summary is 201/));
    assert.ok(has(r, /title is 81/));
    assert.ok(has(r, /does not match the directory/));
  });
  withTree({ 'kb/guides/g.md': GUIDE('', { handoff: null }) }, (root) => assert.ok(has(checkKb(root), /"handoff" is required/)));
});

test('playbook documents must be audience bot and category Playbook; bot is for the playbook only', () => {
  withTree({ 'kb/playbook/p.md': fm({ source: 'playbook', audience: 'all', category: 'Guides' }) + words(60) }, (root) => {
    const r = checkKb(root);
    assert.ok(has(r, /audience: bot/));
    assert.ok(has(r, /category: Playbook/));
  });
  withTree({ 'kb/guides/g.md': GUIDE('', { audience: 'bot' }) }, (root) => assert.ok(has(checkKb(root), /only for kb\/playbook/)));
});

test('slugs: unique, kebab-case file names, faq directory and file match the category', () => {
  withTree({ ...GOOD, 'kb/guides/getting-started.md': GUIDE() }, (root) => assert.ok(!has(checkKb(root), /also used by/)));   // faq-getting-started vs getting-started: different slugs
  withTree({ 'kb/guides/Bad_Name.md': GUIDE() }, (root) => assert.ok(has(checkKb(root), /kebab-case/)));
  withTree({ 'kb/faq/wrong-dir/getting-started.md': FAQ() }, (root) => assert.ok(has(checkKb(root), /does not match category/)));
  withTree({ 'kb/faq/set-up-and-account/other.md': FAQ() }, (root) => assert.ok(has(checkKb(root), /does not match title/)));
});

test('links: kb: links and related must resolve, external links must be https and allow-listed', () => {
  withTree({ ...GOOD, 'kb/guides/links.md': GUIDE('See [x](kb:nope) and [y](http://insecure.test/a) and [z](https://other.test/b) and [w](#top).\n', { related: 'ghost, links' }) }, (root) => {
    const r = checkKb(root);
    assert.ok(has(r, /unknown document "nope"/));
    assert.ok(has(r, /must be https/));
    assert.ok(has(r, /URL not allowed/));
    assert.ok(has(r, /unsupported link target "#top"/));
    assert.ok(has(r, /related slug "ghost" does not exist/));
    assert.ok(has(r, /related lists the document itself/));
  });
  withTree({ ...GOOD, 'kb/guides/ok.md': GUIDE('See [add staff](kb:add-staff) and [docs](https://help.allowed.test/x).\n'), 'scripts/allowlist.json': '{"hosts": ["allowed.test"]}' }, (root) => assert.deepEqual(msgs(checkKb(root)), []));
});

test('body rules: raw HTML, H1, skipped heading levels, unclosed fence, TODO', () => {
  withTree({ 'kb/guides/g.md': GUIDE('<b>bold</b> and <!-- note -->\n# Title again\n\n#### Deep\nTODO later\n```\nnever closed\n') }, (root) => {
    const r = checkKb(root);
    assert.ok(has(r, /raw HTML is not allowed/));
    assert.ok(has(r, /do not use a # H1/));
    assert.ok(has(r, /heading level jumps/));
    assert.ok(has(r, /never closed/));
    assert.ok(has(r, /TODO/));
  });
  withTree({ 'kb/guides/g.md': GUIDE('Use `<b>` in code and\n```\n<div>fenced</div>\n```\nfine.\n') }, (root) => assert.deepEqual(msgs(checkKb(root)), []));
});

test('guides: word range and the three required sections', () => {
  withTree({ 'kb/guides/short.md': fm() + 'Too short.\n\n## Common questions\n\n### a?\nb\n' }, (root) => {
    const r = checkKb(root);
    assert.ok(has(r, /guide has \d+ words/));
    assert.ok(has(r, /missing the section "## What can go wrong"/));
    assert.ok(has(r, /missing the section "## When to contact support"/));
  });
  withTree({ 'kb/guides/long.md': GUIDE(words(1600) + '\n') }, (root) => assert.ok(has(checkKb(root), /guide has \d+ words/)));
});

test('faq files: only ## sections, no empty answers, no text before the first question', () => {
  withTree({ 'kb/faq/set-up-and-account/getting-started.md': FAQ({}, 'Intro text.\n\n## Q one?\n\n## Q two?\n\nA.\n\n### nested\nx\n') }, (root) => {
    const r = checkKb(root);
    assert.ok(has(r, /empty answer for: Q one\?/));
    assert.ok(has(r, /only have ## question sections/));
    assert.ok(has(r, /text before the first ## question/));
  });
});

test('sensitive content: legacy names, secrets, e-mail, phone numbers, URLs', () => {
  const legacy = ['pun' + 'ch' + 'app', 'gen' + 'sharp', 'gen' + '_admin', 'pa' + '_frontend'];
  const bad = GUIDE(`Legacy ${legacy[0]} and ${legacy[1]} and ${legacy[2]} and ${legacy[3]}.\n` +
    'Key AKIAABCDEFGHIJKLMNOP and password: hunter22 and token abcDEF123abcDEF123abcDEF123abcDEF123xx.\n' +
    'Write to help@clockyn.test or ok@example.com or call +91 98765 43210 or 044-2345-6789 on www.site.test.\n');
  withTree({ 'kb/guides/g.md': bad }, (root) => {
    const r = checkKb(root);
    assert.equal(r.errors.filter((e) => /legacy product name/.test(e.msg)).length, 4);
    assert.ok(has(r, /AWS access key/));
    assert.ok(has(r, /password assignment/));
    assert.ok(has(r, /long token-like/));
    assert.ok(has(r, /e-mail address not allowed.*help@clockyn\.test/));
    assert.ok(!has(r, /ok@example\.com/));
    assert.ok(has(r, /phone-number-like value not allowed: \+91 98765 43210/));
    assert.ok(has(r, /phone-number-like value not allowed: 044-2345-6789/));
    assert.ok(has(r, /web address not allowed/));
  });
  withTree({ 'kb/guides/g.md': GUIDE('Call 044 12345678 or mail help@clockyn.test.\n'), 'scripts/allowlist.json': '{"phones": ["04412345678"], "emails": ["help@clockyn.test"]}' }, (root) => assert.deepEqual(msgs(checkKb(root)), []));
  withTree({ ...GOOD, 'README.md': 'About ' + legacy[0] + '\n' }, (root) => assert.ok(checkKb(root).errors.some((e) => e.file === 'README.md')));
});

test('long slugs inside kb: links are not mistaken for tokens; plain numbers and amounts are not phone numbers', () => withTree({
  ...GOOD,
  'kb/guides/buying-renewing-and-changing-your-plan-with-credit.md': GUIDE(),
  'kb/guides/x.md': GUIDE('Read [more](kb:buying-renewing-and-changing-your-plan-with-credit). It costs ₹1,00,000 and was on 2026-11-15 order 12345.\n'),
}, (root) => assert.deepEqual(msgs(checkKb(root)), [])));

test('policy and internal words are warnings only, and not reported in the playbook', () => withTree({
  ...GOOD, 'kb/guides/w.md': GUIDE('We cannot promise a refund. Our server is busy.\n'),
  'kb/playbook/p2.md': fm({ source: 'playbook', audience: 'bot', category: 'Playbook' }) + 'Never promise a refund or a guarantee. ' + words(50) + '\n',
}, (root) => {
  const r = checkKb(root);
  assert.deepEqual(msgs(r), []);
  assert.ok(r.warns.some((w) => /policy-sensitive/.test(w.msg) && w.file === 'kb/guides/w.md'));
  assert.ok(r.warns.some((w) => /internal word/.test(w.msg)));
  assert.ok(!r.warns.some((w) => w.file === 'kb/playbook/p2.md' && /policy|internal word/.test(w.msg)));
}));

test('eval questions: JSON, ids, expected docs, handoff, coverage warnings', () => {
  const good = JSON.stringify({ id: 'q001', question: 'how do i add staff', expected_docs: ['faq-getting-started', 'add-staff'], handoff: 'none' });
  withTree({ ...GOOD, 'eval/questions.jsonl': good + '\n' }, (root) => {
    const r = checkKb(root);
    assert.deepEqual(msgs(r), []);
    assert.equal(r.stats.eval.faqCovered, 1);
    assert.equal(r.stats.eval.guidesCovered, 1);
  });
  const bad = ['not json', JSON.stringify({ id: 'q1', question: 'ok?', expected_docs: ['ghost'], handoff: 'sometimes' }),
    JSON.stringify({ id: 'q1', question: 'a long enough question', expected_docs: [], handoff: 'none' }), '[1]'].join('\n');
  withTree({ ...GOOD, 'eval/questions.jsonl': bad }, (root) => {
    const r = checkKb(root);
    assert.ok(has(r, /invalid JSON/));
    assert.ok(has(r, /at least 6 characters/));
    assert.ok(has(r, /slug "ghost" does not exist/));
    assert.ok(has(r, /handoff must be one of/));
    assert.ok(has(r, /duplicate id q1/));
    assert.ok(has(r, /non-empty array/));
    assert.ok(has(r, /must be a JSON object/));
  });
  withTree({ ...GOOD, 'eval/questions.jsonl': JSON.stringify({ id: 'q9', question: 'something else entirely', expected_docs: ['add-staff'], handoff: 'offer' }) + '\n' }, (root) => {
    assert.ok(checkKb(root).warns.some((w) => /no eval question expects this FAQ category/.test(w.msg)));
  });
});

test('index is deterministic and describes the documents', () => withTree(GOOD, (root) => {
  const a = buildIndex(root);
  const b = buildIndex(root);
  assert.equal(JSON.stringify(a), JSON.stringify(b));
  assert.deepEqual(a.docs.map((d) => d.slug), ['faq-getting-started', 'add-staff', 'rules']);
  assert.equal(a.docs[0].faq_questions, 2);
  assert.equal(a.generated, '2026-11-15');
  assert.deepEqual(a.docs[1].tags, ['one', 'two']);
  assert.equal(a.docs[1].plan, 'all');
  assert.ok(a.docs[1].headings.some((h) => h.text === 'Common questions' && h.level === 2));
}));
