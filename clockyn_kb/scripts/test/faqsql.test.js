'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { tokenize, parseInserts, parseUpdates, generatedDate, applyUpdateFile } = require('../lib/faqsql');

const esc = (s) => String(s).replace(/'/g, "''");     // the SQL rule: a single quote is written twice
const ins = (bucket, cat, q, a, so = 10, st = 1) =>
  `INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status)\n` +
  `SELECT '${esc(bucket)}', '${esc(cat)}', '${esc(q)}', '${esc(a)}', ${so}, ${st}\n` +
  `WHERE NOT EXISTS (SELECT 1 FROM clockyn_dev.support_faq WHERE lower(question) = lower('${esc(q)}'));\n`;

test('tokenize unescapes doubled quotes and drops comments outside strings only', () => {
  const t = tokenize("-- a comment with 'quote'\nSELECT 'it''s', 'a -- not a comment';");
  assert.deepEqual(t.strings, ["it's", 'a -- not a comment']);
  assert.ok(!t.code.includes('comment'));
});

test('tokenize fails loudly on an unterminated string', () => {
  assert.throws(() => tokenize("SELECT 'oops"), /unterminated/);
});

test('parseInserts reads quotes, multi-line answers, semicolons, rupee signs and unicode exactly', () => {
  const answer = "Line one with a friend's code; and a semicolon.\n1. Step one\n2. Step two ₹500 – café\n\n# looks like a heading";
  const rows = parseInserts('-- header\nBEGIN;\n' + ins('Plans & Payments', 'Refer & Earn', "What's a code?", answer, 3311, 1) + 'COMMIT;\n');
  assert.equal(rows.length, 1);
  assert.equal(rows[0].bucket, 'Plans & Payments');
  assert.equal(rows[0].category, 'Refer & Earn');
  assert.equal(rows[0].question, "What's a code?");
  assert.equal(rows[0].answer, "Line one with a friend's code; and a semicolon.\n1. Step one\n2. Step two ₹500 – café\n\n# looks like a heading");
  assert.equal(rows[0].sort_order, 3311);
  assert.equal(rows[0].status, 1);
});

test('parseInserts keeps file order and reads several statements with comments between them', () => {
  const sql = ins('B', 'C', 'Q1', 'A1', 20) + '\n-- ───── next ─────\n' + ins('B', 'C', 'Q2', 'A2', 10);
  const rows = parseInserts(sql);
  assert.deepEqual(rows.map((r) => r.question), ['Q1', 'Q2']);
});

test('parseInserts refuses an INSERT it cannot read', () => {
  assert.throws(() => parseInserts("INSERT INTO clockyn_dev.support_faq (faq_bucket, category, question, answer, sort_order, status) VALUES ('a','b','c','d',1,1);"), /could be read/);
});

test('parseInserts refuses another column order', () => {
  const sql = "INSERT INTO s.support_faq (category, faq_bucket, question, answer, sort_order, status) SELECT 'a', 'b', 'c', 'd', 1, 1 WHERE NOT EXISTS (SELECT 1);";
  assert.throws(() => parseInserts(sql), /unexpected column list/);
});

test('parseUpdates reads the answer and the question key', () => {
  const sql = "UPDATE clockyn_dev.support_faq SET answer = 'New ''text''.\nSecond line', updated_at = now()\nWHERE lower(question) = lower('What is it?');";
  assert.deepEqual(parseUpdates(sql), [{ answer: "New 'text'.\nSecond line", question: 'What is it?' }]);
});

test('generatedDate finds the header date', () => {
  assert.equal(generatedDate('-- 397 questions. Generated 2026-11-15.\n'), '2026-11-15');
  assert.equal(generatedDate('-- nothing'), null);
});

test('applyUpdateFile: identical text is silent, changed text and new questions are reported', () => {
  const rows = parseInserts(ins('B', 'C', 'Same?', 'one') + ins('B', 'C', 'Changed?', 'old'));
  const upd = "UPDATE s.support_faq SET answer = 'one', updated_at = now() WHERE lower(question) = lower('same?');\n" +
    "UPDATE s.support_faq SET answer = 'new', updated_at = now() WHERE lower(question) = lower('Changed?');\n" +
    "UPDATE s.support_faq SET answer = 'x', updated_at = now() WHERE lower(question) = lower('Gone?');\n" + ins('B', 'C', 'Added?', 'fresh', 30) + ins('B', 'C', 'same?', 'dup');
  const r = applyUpdateFile(rows, upd);
  assert.equal(r.rows.length, 3);
  assert.equal(r.rows.find((x) => x.question === 'Changed?').answer, 'new');
  assert.equal(r.rows.find((x) => x.question === 'Same?').answer, 'one');
  assert.equal(r.notes.length, 3);
  assert.ok(r.notes.some((n) => /changes the answer of: Changed\?/.test(n)));
  assert.ok(r.notes.some((n) => /not in FAQ\.sql/.test(n)));
  assert.ok(r.notes.some((n) => /adds the question: Added\?/.test(n)));
});
