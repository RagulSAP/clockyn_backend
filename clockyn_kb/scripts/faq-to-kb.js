#!/usr/bin/env node
'use strict';
/* Generate kb/faq/** from the FAQ.
 *   node scripts/faq-to-kb.js                       FAQ.sql (+ FAQ_referral_update.sql edits) -> kb/faq/**
 *   node scripts/faq-to-kb.js --sql <path>          another FAQ.sql (the update file next to it is used when it exists)
 *   node scripts/faq-to-kb.js --rows rows.json      rows exported from the live database (scripts/export-faq-from-db.py)
 *   node scripts/faq-to-kb.js --check               exit 1 when kb/faq differs from what would be generated
 * kb/faq is GENERATED: never edit it by hand (edit the FAQ in the console FAQ Hub, export it, and run this script). */
const fs = require('node:fs');
const path = require('node:path');
const { ROOT, readText, writeIfChanged, walk, today } = require('./lib/util');
const { parseInserts, applyUpdateFile, generatedDate } = require('./lib/faqsql');
const { renderFaqFiles } = require('./lib/faqrender');

function argValue(argv, name) {
  const i = argv.indexOf(name);
  return i >= 0 ? argv[i + 1] : null;
}

/** Read the rows from the SQL seed files or from a rows.json. Returns { rows, generated, notes, source }. */
function loadRows(opts) {
  if (opts.rows) {
    const j = JSON.parse(readText(opts.rows));
    if (!j || !Array.isArray(j.rows)) throw new Error('rows.json must be {"generated": "YYYY-MM-DD", "rows": [...]}');
    const rows = j.rows.map((r, i) => {
      for (const k of ['bucket', 'category', 'question', 'answer']) if (typeof r[k] !== 'string') throw new Error(`rows.json row ${i}: "${k}" must be a string`);
      return { id: r.id, bucket: r.bucket, category: r.category, question: r.question, answer: r.answer,
        sort_order: Number(r.sort_order || 0), status: r.status === undefined ? 1 : Number(r.status) };
    });
    return { rows, generated: j.generated || today(), notes: [], source: opts.rows };
  }
  const sqlPath = path.resolve(opts.sql);
  const seed = readText(sqlPath);
  let rows = parseInserts(seed);
  const notes = [];
  const updatePath = path.join(path.dirname(sqlPath), 'FAQ_referral_update.sql');
  if (fs.existsSync(updatePath)) {
    const applied = applyUpdateFile(rows, readText(updatePath));
    rows = applied.rows;
    notes.push(...applied.notes);
  }
  return { rows, generated: generatedDate(seed) || today(), notes, source: sqlPath };
}

/** Compare the generated set with kb/faq on disk. Returns { changed[], missing[], stale[] }. */
function diffWithDisk(root, files) {
  const changed = [];
  const missing = [];
  for (const [rel, content] of files) {
    const abs = path.join(root, rel);
    if (!fs.existsSync(abs)) missing.push(rel);
    else if (fs.readFileSync(abs, 'utf8') !== content) changed.push(rel);
  }
  const stale = walk(path.join(root, 'kb', 'faq'), (r) => r.endsWith('.md')).map((r) => 'kb/faq/' + r).filter((r) => !files.has(r));
  return { changed, missing, stale };
}

function writeAll(root, files) {
  let written = 0;
  for (const [rel, content] of files) if (writeIfChanged(path.join(root, rel), content)) written++;
  const stale = walk(path.join(root, 'kb', 'faq'), (r) => r.endsWith('.md')).map((r) => 'kb/faq/' + r).filter((r) => !files.has(r));
  for (const rel of stale) fs.unlinkSync(path.join(root, rel));
  // remove directories left empty
  const dir = path.join(root, 'kb', 'faq');
  if (fs.existsSync(dir)) for (const d of fs.readdirSync(dir)) {
    const full = path.join(dir, d);
    if (fs.statSync(full).isDirectory() && fs.readdirSync(full).length === 0) fs.rmdirSync(full);
  }
  return { written, removed: stale.length };
}

function main(argv) {
  const check = argv.includes('--check');
  const opts = { rows: argValue(argv, '--rows'), sql: argValue(argv, '--sql') || path.join(ROOT, '..', 'FAQ.sql') };
  if (!opts.rows && !fs.existsSync(opts.sql)) {
    const msg = `FAQ source not found: ${path.resolve(opts.sql)}`;
    if (check) { console.log('SKIP faq-to-kb --check: ' + msg); return 0; }
    console.error('ERROR ' + msg);
    return 1;
  }
  const loaded = loadRows(opts);
  const live = loaded.rows.filter((r) => r.status === undefined || Number(r.status) === 1);
  const { files, stats, notes } = renderFaqFiles(loaded.rows, loaded.generated);
  const written = stats.reduce((n, s) => n + s.questions, 0);
  if (written !== live.length) throw new Error(`rows written (${written}) differ from rows parsed (${live.length})`);

  for (const n of [...loaded.notes, ...notes]) console.log('NOTE ' + n);
  if (check) {
    const d = diffWithDisk(ROOT, files);
    const bad = d.changed.length + d.missing.length + d.stale.length;
    for (const f of d.changed) console.error('ERROR out of date: ' + f);
    for (const f of d.missing) console.error('ERROR missing: ' + f);
    for (const f of d.stale) console.error('ERROR stale (not generated any more): ' + f);
    if (bad) { console.error('kb/faq is out of date: run  node scripts/faq-to-kb.js'); return 1; }
    console.log(`kb/faq is up to date (${stats.length} files, ${written} questions)`);
    return 0;
  }
  const res = writeAll(ROOT, files);
  console.log(`source: ${loaded.source}\nupdated date: ${loaded.generated}`);
  console.log('\nbucket'.padEnd(26) + 'category'.padEnd(30) + 'questions'.padStart(10) + 'words'.padStart(8));
  for (const s of stats) console.log(s.bucket.padEnd(25) + ' ' + s.category.padEnd(29) + String(s.questions).padStart(9) + String(s.words).padStart(8));
  console.log(`\n${stats.length} files, ${written} questions (parsed ${live.length}); ${res.written} written, ${res.removed} stale removed`);
  return 0;
}

if (require.main === module) {
  try { process.exitCode = main(process.argv.slice(2)); } catch (e) { console.error('ERROR ' + e.message); process.exitCode = 1; }
}

module.exports = { loadRows, diffWithDisk, writeAll, main };
