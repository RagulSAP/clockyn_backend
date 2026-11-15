#!/usr/bin/env node
'use strict';
/* Build kb/index.json: one entry per document of kb/faq, kb/guides and kb/playbook (metadata, headings, word count).
 *   node scripts/build-index.js           write kb/index.json
 *   node scripts/build-index.js --check   exit 1 when kb/index.json is stale
 * Deterministic: no timestamps (`generated` is the newest `updated` date), stable sort (faq, guides, playbook; then by path). */
const fs = require('node:fs');
const path = require('node:path');
const { ROOT, readText, walk, parseFrontMatter, listOf, headingsOf, countWords, writeIfChanged } = require('./lib/util');

const SOURCES = [['faq', 'faq'], ['guides', 'guide'], ['playbook', 'playbook']];

/** Slug of a document: faq files are `faq-<file name>`, guides and playbook use the file name. */
function slugOf(dir, rel) {
  const base = path.posix.basename(rel, '.md');
  return dir === 'faq' ? 'faq-' + base : base;
}

function buildIndex(root = ROOT) {
  const docs = [];
  for (const [dir] of SOURCES) {
    for (const rel of walk(path.join(root, 'kb', dir), (r) => r.endsWith('.md'))) {
      const text = readText(path.join(root, 'kb', dir, rel));
      const fm = parseFrontMatter(text);
      const d = fm.data;
      const headings = headingsOf(fm.body);
      docs.push({
        slug: slugOf(dir, rel),
        title: d.title || rel,
        summary: d.summary || '',
        category: d.category || '',
        audience: d.audience || '',
        source: d.source || '',
        app_path: d.app_path || '',
        plan: d.plan || 'all',
        handoff: d.handoff || '',
        tags: listOf(d.tags),
        updated: d.updated || '',
        related: listOf(d.related),
        path: `kb/${dir}/${rel}`,
        headings: headings.map((h) => ({ level: h.level, text: h.text, id: h.id })),
        words: countWords(fm.body),
        faq_questions: dir === 'faq' ? headings.filter((h) => h.level === 2).length : 0,
      });
    }
  }
  const order = (p) => (p.startsWith('kb/faq/') ? 0 : p.startsWith('kb/guides/') ? 1 : 2);
  docs.sort((a, b) => (order(a.path) - order(b.path)) || (a.path < b.path ? -1 : a.path > b.path ? 1 : 0));
  const generated = docs.map((d) => d.updated).filter(Boolean).sort().pop() || '';
  const counts = {};
  for (const d of docs) counts[d.source || 'unknown'] = (counts[d.source || 'unknown'] || 0) + 1;
  return { generated, count: docs.length, counts, faq_questions: docs.reduce((n, d) => n + d.faq_questions, 0), docs };
}

function render(index) {
  return JSON.stringify(index, null, 2) + '\n';
}

function main(argv) {
  const target = path.join(ROOT, 'kb', 'index.json');
  const content = render(buildIndex(ROOT));
  if (argv.includes('--check')) {
    const cur = fs.existsSync(target) ? fs.readFileSync(target, 'utf8') : '';
    if (cur !== content) { console.error('ERROR kb/index.json is out of date: run  node scripts/build-index.js'); return 1; }
    console.log('kb/index.json is up to date');
    return 0;
  }
  const wrote = writeIfChanged(target, content);
  const idx = JSON.parse(content);
  console.log(`${wrote ? 'wrote' : 'unchanged'} kb/index.json: ${idx.count} documents (${Object.entries(idx.counts).map(([k, v]) => `${k} ${v}`).join(', ')}), ${idx.faq_questions} FAQ questions`);
  return 0;
}

if (require.main === module) {
  try { process.exitCode = main(process.argv.slice(2)); } catch (e) { console.error('ERROR ' + e.message); process.exitCode = 1; }
}

module.exports = { buildIndex, slugOf, render };
