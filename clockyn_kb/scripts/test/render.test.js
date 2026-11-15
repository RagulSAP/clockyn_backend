'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { slugify, parseFrontMatter, headingsOf, stripCode, countWords, isRealDate } = require('../lib/util');
const { renderFaqFiles, keywordsFor, safeAnswer } = require('../lib/faqrender');
const { loadRows, diffWithDisk, writeAll } = require('../faq-to-kb');

const row = (over) => ({ id: 1, bucket: 'Plans & Payments', category: 'Refer & Earn', question: 'What is it?', answer: 'It is a thing.', sort_order: 10, status: 1, ...over });

test('slugify follows the contract', () => {
  assert.equal(slugify('Shifts, Holidays & Rules'), 'shifts-holidays-and-rules');
  assert.equal(slugify('Set up & Account'), 'set-up-and-account');
  assert.equal(slugify('  Refer & Earn!! '), 'refer-and-earn');
  assert.equal(slugify('CRM & Leads'), 'crm-and-leads');
  assert.equal(slugify(''), '');
});

test('front matter: first colon splits, errors are reported', () => {
  const ok = parseFrontMatter('---\ntitle: How do I add staff: quickly?\ntags: a, b\n---\nBody');
  assert.equal(ok.data.title, 'How do I add staff: quickly?');
  assert.equal(ok.body, 'Body');
  assert.equal(parseFrontMatter('no front matter').errors.length, 1);
  assert.match(parseFrontMatter('---\ntitle: x\n').errors[0], /not closed/);
  assert.match(parseFrontMatter('---\ntitle: a\ntitle: b\n---\n').errors[0], /repeated/);
  assert.match(parseFrontMatter('---\nnonsense\n---\n').errors[0], /key: value/);
});

test('headings and code stripping', () => {
  const body = '## One\ntext\n```\n## not a heading\n```\n### Two `## inline`\n## One';
  const h = headingsOf(body);
  assert.deepEqual(h.map((x) => [x.level, x.id]), [[2, 'one'], [3, 'two-inline'], [2, 'one-2']]);
  assert.equal(stripCode('a\n```\nx\n').unclosedFence, true);
  assert.equal(countWords('one two\nthree'), 3);
  assert.equal(isRealDate('2026-02-30'), false);
  assert.equal(isRealDate('2026-11-15'), true);
});

test('safeAnswer escapes lines that would read as headings and reports them', () => {
  const r = safeAnswer('ok\n# Title\n  ## sub\nnot#heading');
  assert.equal(r.escaped, 2);
  assert.equal(r.text, 'ok\n\\# Title\n  \\## sub\nnot#heading');
  assert.equal(safeAnswer('plain').escaped, 0);
});

test('renderer: one file per category, question headings, front matter, order by sort_order then position', () => {
  const rows = [
    row({ question: 'Second?', answer: 'B', sort_order: 20 }),
    row({ question: 'First?', answer: 'A\n\n1. one\n2. two', sort_order: 10 }),
    row({ category: 'Plans & Billing', question: 'Price?', answer: 'Shown in the app.', sort_order: 5 }),
    row({ question: 'Hidden?', answer: 'x', status: 0 }),
  ];
  const { files, stats } = renderFaqFiles(rows, '2026-11-15');
  assert.deepEqual([...files.keys()], ['kb/faq/plans-and-payments/plans-and-billing.md', 'kb/faq/plans-and-payments/refer-and-earn.md']);
  const refer = files.get('kb/faq/plans-and-payments/refer-and-earn.md');
  assert.ok(refer.startsWith('---\ntitle: Refer & Earn\nsummary: Answers to 2 common questions about Refer & Earn.\ncategory: Plans & Payments\naudience: all\nsource: faq\nplan: prime-elite\nhandoff: offer\ntags: '));
  assert.ok(refer.indexOf('## First?') < refer.indexOf('## Second?'));
  assert.ok(refer.includes('## First?\n\nA\n\n1. one\n2. two\n\n## Second?\n\nB\n'));
  assert.ok(!refer.includes('Hidden?'));
  assert.equal(stats.find((s) => s.category === 'Refer & Earn').questions, 2);
  assert.ok(files.get('kb/faq/plans-and-payments/plans-and-billing.md').includes('plan: all\nhandoff: offer'));
});

test('renderer: handoff table, an empty answer fails, a category clash fails', () => {
  const { files } = renderFaqFiles([row({ category: 'Leave & Requests', bucket: 'Attendance & Time' })], '2026-01-01');
  assert.ok(files.get('kb/faq/attendance-and-time/leave-and-requests.md').includes('handoff: none'));
  assert.throws(() => renderFaqFiles([row({ answer: '  ' })], '2026-01-01'), /empty answer/);
  assert.throws(() => renderFaqFiles([row({ category: 'A & B' }), row({ category: 'A and B' })], '2026-01-01'), /same file/);
});

test('renderer is deterministic and keywords are stable', () => {
  const rows = [row({ question: 'How do I add a staff member?' }), row({ question: 'Can a manager add staff?', id: 2 })];
  const a = renderFaqFiles(rows, '2026-11-15');
  const b = renderFaqFiles(rows.slice().reverse().map((r, i) => ({ ...r })), '2026-11-15');
  assert.equal(JSON.stringify([...a.files]), JSON.stringify([...renderFaqFiles(rows, '2026-11-15').files]));
  assert.equal(a.files.size, b.files.size);
  assert.deepEqual(keywordsFor('Refer & Earn', ['How do I add a staff member?', 'Can a manager add staff?']), ['refer', 'earn', 'add', 'staff', 'manager', 'member']);
});

test('rows.json path: loadRows reads the intermediate and the generated files can be written, checked and cleaned', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'kb-render-'));
  try {
    const rowsFile = path.join(tmp, 'rows.json');
    fs.writeFileSync(rowsFile, JSON.stringify({ generated: '2026-12-01', source: 'db', rows: [
      { id: 7, bucket: 'Help & Fixes', category: 'Troubleshooting', question: 'It is slow?', answer: 'Check your network.', sort_order: 2, status: 1 },
      { id: 8, bucket: 'Help & Fixes', category: 'Troubleshooting', question: 'Old?', answer: 'x', sort_order: 1, status: 0 },
    ] }));
    const loaded = loadRows({ rows: rowsFile });
    assert.equal(loaded.generated, '2026-12-01');
    const { files } = renderFaqFiles(loaded.rows, loaded.generated);
    assert.equal(files.size, 1);
    const root = path.join(tmp, 'repo');
    writeAll(root, files);
    assert.deepEqual(diffWithDisk(root, files), { changed: [], missing: [], stale: [] });
    fs.writeFileSync(path.join(root, 'kb/faq/help-and-fixes/troubleshooting.md'), 'edited by hand');
    fs.writeFileSync(path.join(root, 'kb/faq/help-and-fixes/old.md'), 'stale');
    const d = diffWithDisk(root, files);
    assert.deepEqual(d.changed, ['kb/faq/help-and-fixes/troubleshooting.md']);
    assert.deepEqual(d.stale, ['kb/faq/help-and-fixes/old.md']);
    const res = writeAll(root, files);
    assert.equal(res.written, 1);
    assert.equal(res.removed, 1);
    assert.deepEqual(diffWithDisk(root, files), { changed: [], missing: [], stale: [] });
    assert.throws(() => loadRows({ rows: path.join(tmp, 'nope.json') }));
    fs.writeFileSync(rowsFile, '{"rows": [{"bucket": 1}]}');
    assert.throws(() => loadRows({ rows: rowsFile }), /must be a string/);
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});
