#!/usr/bin/env node
'use strict';
/* Validate the knowledge base. Exit code 1 on any ERROR; WARN lines are for the owner to read.
 *   node scripts/check-kb.js [--verbose]      (verbose prints every WARN line instead of a sample per file)
 * Rules: see docs/CONTRACT.md. The legacy product names are assembled from fragments below so that this file itself never contains them. */
const fs = require('node:fs');
const path = require('node:path');
const { ROOT, readText, walk, parseFrontMatter, listOf, stripCode, headingsOf, countWords, slugify, isRealDate } = require('./lib/util');
const { slugOf } = require('./build-index');

const FAQ_BUCKETS = ['Set up & Account', 'Attendance & Time', 'Pay & Expenses', 'Team & Customers', 'Plans & Payments', 'Help & Fixes'];
const ENUMS = {
  audience: ['admin', 'manager', 'staff', 'all', 'bot'],
  source: ['faq', 'guide', 'playbook'],
  plan: ['all', 'prime-elite', 'elite', 'core-only'],
  handoff: ['none', 'offer', 'required'],
};
const REQUIRED = ['title', 'summary', 'category', 'audience', 'source', 'handoff', 'tags', 'updated'];
const KNOWN = new Set([...REQUIRED, 'plan', 'app_path', 'related']);
const DIRS = { faq: 'faq', guides: 'guide', playbook: 'playbook' };
const GUIDE_SECTIONS = ['common questions', 'what can go wrong', 'when to contact support'];

// legacy product names, built from fragments (same patterns as the Clockyn repos' brand check)
const f = (...p) => p.join('');
const LEGACY = [
  new RegExp(f('pun', 'ch', '[\\s_.-]*', 'app'), 'i'),
  new RegExp(f('gen', '[\\s_.-]*', 'sharp'), 'i'),
  new RegExp(f('gen', '_', 'admin'), 'i'),
  new RegExp(f('pa', '_', 'frontend'), 'i'),
  new RegExp(f('pa', '_', 'backend'), 'i'),
];

const INTERNAL_WORDS = /\b(console|endpoints?|API|databases?|tables?|servers?|backend|migrations?)\b/i;
const POLICY_WORDS = /\b(refunds?|refunded|SLA|guarantee[ds]?|legal|within \d+ (?:hours?|days?))\b/i;
const HTML_RE = /<\/?[A-Za-z][A-Za-z0-9-]*(?:\s[^<>]*)?\/?>|<!--/;
const URL_RE = /https?:\/\/[^\s)\]>"'`]+/gi;
const WWW_RE = /\bwww\.[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}\b/gi;
const EMAIL_RE = /[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})/g;
const PHONE_RE = /(?<![\w.])\+?\d[\d ()-]{8,}\d(?![\w])/g;
const LINK_RE = /\[[^\]\n]*\]\(([^)\s]*)\)/g;
const SECRETS = [
  [/AKIA[0-9A-Z]{16}/, 'an AWS access key id'],
  [/-----BEGIN [A-Z ]*PRIVATE KEY-----/, 'a private key header'],
  [/\bpassword\s*[:=]\s*\S+/i, 'a password assignment'],
  [/(?=[A-Za-z0-9+/_-]*\d)(?=[A-Za-z0-9+/_-]*[A-Za-z])[A-Za-z0-9+/_-]{32,}/, 'a long token-like string'],
];

function loadAllowlist(root) {
  const file = path.join(root, 'scripts', 'allowlist.json');
  const base = { hosts: [], emails: [], email_domains: [], phones: [] };
  if (!fs.existsSync(file)) return base;
  try { return { ...base, ...JSON.parse(readText(file)) }; } catch (e) { return { ...base, _error: e.message }; }
}

/**
 * @returns {{errors: object[], warns: object[], docs: object[], stats: object}}
 */
function checkKb(root = ROOT, opts = {}) {
  const errors = [];
  const warns = [];
  const err = (file, line, msg) => errors.push({ file, line, msg });
  const warn = (file, line, msg) => warns.push({ file, line, msg });
  const allow = loadAllowlist(root);
  if (allow._error) err('scripts/allowlist.json', 0, 'cannot be read: ' + allow._error);

  // ---- collect documents
  const docs = [];
  for (const dir of Object.keys(DIRS)) {
    const base = path.join(root, 'kb', dir);
    if (!fs.existsSync(base)) { warn(`kb/${dir}`, 0, 'MISSING directory (no documents yet)'); continue; }
    for (const rel of walk(base)) {
      const file = `kb/${dir}/${rel}`;
      if (!rel.endsWith('.md')) { warn(file, 0, 'not a .md file (ignored)'); continue; }
      const text = readText(path.join(base, rel));
      const fm = parseFrontMatter(text);
      docs.push({ dir, rel, file, text, fm, slug: slugOf(dir, rel), data: fm.data, body: fm.body, bodyLine: fm.bodyLine });
    }
  }
  const slugs = new Map();
  for (const d of docs) {
    if (slugs.has(d.slug)) err(d.file, 0, `slug "${d.slug}" is also used by ${slugs.get(d.slug)}`);
    else slugs.set(d.slug, d.file);
  }

  // ---- per document
  for (const d of docs) {
    const { file, data, body } = d;
    const at = (i) => d.bodyLine + i;                          // 1-based line in the file of body line index i
    for (const m of d.fm.errors) err(file, 1, m);
    for (const k of REQUIRED) if (!(k in data) || data[k] === '') err(file, 1, `front matter key "${k}" is required`);
    for (const k of Object.keys(data)) if (!KNOWN.has(k)) warn(file, 1, `unknown front matter key "${k}"`);
    for (const k of Object.keys(ENUMS)) {
      if (k in data && !ENUMS[k].includes(data[k])) err(file, 1, `${k} must be one of ${ENUMS[k].join(' | ')} (got "${data[k]}")`);
    }
    if (data.title && data.title.length > 80) err(file, 1, `title is ${data.title.length} characters (max 80)`);
    if (data.summary && data.summary.length > 200) err(file, 1, `summary is ${data.summary.length} characters (max 200)`);
    if (data.updated && !isRealDate(data.updated)) err(file, 1, `updated must be a real date YYYY-MM-DD (got "${data.updated}")`);
    if ('tags' in data && listOf(data.tags).length === 0) err(file, 1, 'tags must list at least one word');
    for (const t of listOf(data.tags)) if (t !== t.toLowerCase()) err(file, 1, `tag "${t}" must be lower-case`);
    if (data.source && data.source !== DIRS[d.dir]) err(file, 1, `source "${data.source}" does not match the directory kb/${d.dir} (expected ${DIRS[d.dir]})`);
    if (d.dir === 'playbook') {
      if (data.audience && data.audience !== 'bot') err(file, 1, 'playbook documents must have audience: bot');
      if (data.category && data.category !== 'Playbook') err(file, 1, 'playbook documents must have category: Playbook');
    } else if (data.audience === 'bot') {
      err(file, 1, 'audience: bot is only for kb/playbook');
    }
    if (d.dir === 'faq') {
      const parts = d.rel.split('/');
      if (parts.length !== 2) err(file, 0, 'faq files live at kb/faq/<bucket>/<category>.md');
      else {
        if (data.category && parts[0] !== slugify(data.category)) err(file, 1, `directory "${parts[0]}" does not match category "${data.category}"`);
        if (data.title && parts[1] !== slugify(data.title) + '.md') err(file, 1, `file name "${parts[1]}" does not match title "${data.title}" (expected ${slugify(data.title)}.md)`);
      }
      if (data.category && !FAQ_BUCKETS.includes(data.category)) warn(file, 1, `category "${data.category}" is not one of the known FAQ buckets`);
    } else {
      if (d.rel.includes('/')) err(file, 0, 'guides and playbook documents are flat files directly in their directory');
      if (!/^[a-z0-9]+(-[a-z0-9]+)*$/.test(path.posix.basename(d.rel, '.md'))) err(file, 0, 'file name must be kebab-case (a-z, 0-9, -)');
      if (d.dir === 'guides' && data.category && ![...FAQ_BUCKETS, 'Guides'].includes(data.category)) err(file, 1, `category must be one of the FAQ buckets or "Guides" (got "${data.category}")`);
    }
    for (const r of listOf(data.related)) {
      if (r === d.slug) err(file, 1, 'related lists the document itself');
      else if (!slugs.has(r)) err(file, 1, `related slug "${r}" does not exist`);
    }

    // ---- body
    const code = stripCode(body);
    if (code.unclosedFence) err(file, at(0), 'a fenced code block is never closed');
    const lines = code.text.split('\n');
    const heads = headingsOf(body);
    let prev = 1;
    for (const h of heads) {
      if (h.level === 1) err(file, at(h.line - 1), 'do not use a # H1 (the title comes from the front matter)');
      else if (h.level > prev + 1) err(file, at(h.line - 1), `heading level jumps from ${prev} to ${h.level}: ${h.text}`);
      prev = h.level;
    }
    lines.forEach((line, i) => {
      const ln = at(i);
      if (HTML_RE.test(line)) err(file, ln, 'raw HTML is not allowed: ' + line.trim().slice(0, 60));
      if (/\b(TODO|FIXME|lorem ipsum)\b/i.test(line)) err(file, ln, 'TODO / FIXME / lorem ipsum left in the text');
      for (const re of LEGACY) if (re.test(line)) err(file, ln, 'legacy product name found: ' + line.trim().slice(0, 60));
      const noTargets = line.replace(/\]\([^)]*\)/g, '](...)');
      for (const [re, what] of SECRETS) if (re.test(noTargets)) err(file, ln, `looks like ${what}`);
      for (const mm of line.matchAll(LINK_RE)) {
        const target = mm[1];
        if (target.startsWith('kb:')) { if (!slugs.has(target.slice(3))) err(file, ln, `internal link to unknown document "${target.slice(3)}"`); }
        else if (/^http:\/\//i.test(target)) { /* reported once below as a URL that is not https */ }
        else if (!/^https:\/\//i.test(target)) err(file, ln, `unsupported link target "${target}" (use kb:slug or https://...)`);
      }
      for (const mm of line.matchAll(URL_RE)) {
        const u = mm[0];
        if (/^http:\/\//i.test(u)) { err(file, ln, 'external links and URLs must be https: ' + u); continue; }
        let host = '';
        try { host = new URL(u).hostname.toLowerCase(); } catch { /* malformed */ }
        if (!allow.hosts.some((h) => host === h || host.endsWith('.' + h))) err(file, ln, `URL not allowed (add its host to scripts/allowlist.json): ${u}`);
      }
      for (const mm of line.matchAll(WWW_RE)) {
        if (!allow.hosts.some((h) => mm[0].toLowerCase().endsWith(h))) err(file, ln, `web address not allowed: ${mm[0]}`);
      }
      for (const mm of line.matchAll(EMAIL_RE)) {
        const dom = mm[1].toLowerCase();
        if (dom !== 'example.com' && !allow.email_domains.includes(dom) && !allow.emails.includes(mm[0].toLowerCase())) err(file, ln, `e-mail address not allowed (use name@example.com): ${mm[0]}`);
      }
      for (const mm of line.matchAll(PHONE_RE)) {
        const digits = mm[0].replace(/\D/g, '');
        if (digits.length >= 10 && !allow.phones.includes(mm[0].trim()) && !allow.phones.includes(digits)) err(file, ln, `phone-number-like value not allowed: ${mm[0].trim()}`);
      }
      if (d.dir !== 'playbook') {
        if (INTERNAL_WORDS.test(line)) warn(file, ln, 'internal word in a customer document: ' + line.trim().slice(0, 90));
        if (POLICY_WORDS.test(line)) warn(file, ln, 'policy-sensitive wording, owner to review: ' + line.trim().slice(0, 90));
      }
    });

    if (d.dir === 'faq') {
      const h2 = heads.filter((h) => h.level === 2);
      for (const h of heads) if (h.level !== 2) err(file, at(h.line - 1), `FAQ files only have ## question sections (found ${'#'.repeat(h.level)} ${h.text})`);
      if (h2.length === 0) err(file, at(0), 'no ## question sections');
      const pre = lines.slice(0, h2.length ? h2[0].line - 1 : lines.length).join('').trim();
      if (pre) err(file, at(0), 'text before the first ## question');
      h2.forEach((h, i) => {
        const end = i + 1 < h2.length ? h2[i + 1].line - 1 : lines.length;
        if (!lines.slice(h.line, end).join('').trim()) err(file, at(h.line - 1), `empty answer for: ${h.text}`);
      });
      const seen = new Set();
      for (const h of h2) { const k = h.text.toLowerCase(); if (seen.has(k)) err(file, at(h.line - 1), `question repeated in this file: ${h.text}`); seen.add(k); }
    }
    if (d.dir === 'guides') {
      const words = countWords(body);
      if (words < 250 || words > 1500) err(file, at(0), `guide has ${words} words (must be 250-1500)`);
      const names = heads.filter((h) => h.level === 2).map((h) => h.text.toLowerCase());
      for (const s of GUIDE_SECTIONS) if (!names.includes(s)) err(file, at(0), `guide is missing the section "## ${s.replace(/^./, (c) => c.toUpperCase())}"`);
      const cq = heads.findIndex((h) => h.level === 2 && h.text.toLowerCase() === 'common questions');
      if (cq >= 0) {
        let n = 0;
        for (let i = cq + 1; i < heads.length && heads[i].level > 2; i++) if (heads[i].level === 3) n++;
        if (n < 4 || n > 8) warn(file, at(heads[cq].line - 1), `Common questions has ${n} questions (contract: 4-8)`);
      }
    }
    if (d.dir === 'playbook') {
      const words = countWords(body);
      if (words < 40) warn(file, at(0), `playbook document is very short (${words} words)`);
    }
  }

  // ---- eval questions
  const evalFile = path.join(root, 'eval', 'questions.jsonl');
  const evalStats = { questions: 0, faqCovered: 0, faqTotal: 0, guidesCovered: 0, guidesTotal: 0 };
  if (!fs.existsSync(evalFile)) warn('eval/questions.jsonl', 0, 'MISSING (no evaluation questions yet)');
  else {
    const ids = new Set();
    const covered = new Set();
    readText(evalFile).split('\n').forEach((raw, i) => {
      if (!raw.trim()) return;
      const ln = i + 1;
      let q;
      try { q = JSON.parse(raw); } catch (e) { err('eval/questions.jsonl', ln, 'invalid JSON: ' + e.message); return; }
      evalStats.questions++;
      if (!q || typeof q !== 'object' || Array.isArray(q)) { err('eval/questions.jsonl', ln, 'each line must be a JSON object'); return; }
      if (typeof q.id !== 'string' || !q.id) err('eval/questions.jsonl', ln, 'id is required');
      else if (ids.has(q.id)) err('eval/questions.jsonl', ln, `duplicate id ${q.id}`);
      else ids.add(q.id);
      if (typeof q.question !== 'string' || q.question.trim().length < 6) err('eval/questions.jsonl', ln, 'question must be a string of at least 6 characters');
      if (!Array.isArray(q.expected_docs) || q.expected_docs.length === 0) err('eval/questions.jsonl', ln, 'expected_docs must be a non-empty array of slugs');
      else for (const s of q.expected_docs) { if (!slugs.has(s)) err('eval/questions.jsonl', ln, `expected_docs slug "${s}" does not exist`); else covered.add(s); }
      if (!ENUMS.handoff.includes(q.handoff)) err('eval/questions.jsonl', ln, `handoff must be one of ${ENUMS.handoff.join(' | ')}`);
      for (const re of LEGACY) if (re.test(raw)) err('eval/questions.jsonl', ln, 'legacy product name found');
    });
    const faq = docs.filter((d) => d.dir === 'faq');
    const guides = docs.filter((d) => d.dir === 'guides');
    evalStats.faqTotal = faq.length;
    evalStats.guidesTotal = guides.length;
    evalStats.faqCovered = faq.filter((d) => covered.has(d.slug)).length;
    evalStats.guidesCovered = guides.filter((d) => covered.has(d.slug)).length;
    for (const d of faq) if (!covered.has(d.slug)) warn(d.file, 0, 'no eval question expects this FAQ category');
    for (const d of guides) if (!covered.has(d.slug)) warn(d.file, 0, 'no eval question expects this guide');
  }

  // ---- legacy names outside kb (docs, eval, README)
  const extra = [];
  if (fs.existsSync(path.join(root, 'README.md'))) extra.push('README.md');
  for (const r of walk(path.join(root, 'docs'), (x) => x.endsWith('.md'))) extra.push('docs/' + r);
  for (const rel of extra) {
    readText(path.join(root, rel)).split('\n').forEach((line, i) => {
      for (const re of LEGACY) if (re.test(line)) err(rel, i + 1, 'legacy product name found: ' + line.trim().slice(0, 60));
    });
  }

  const stats = { documents: docs.length, byDir: {}, words: 0, faqQuestions: 0, eval: evalStats };
  for (const d of docs) {
    const w = countWords(d.body);
    stats.words += w;
    stats.byDir[d.dir] = stats.byDir[d.dir] || { docs: 0, words: 0 };
    stats.byDir[d.dir].docs++;
    stats.byDir[d.dir].words += w;
    if (d.dir === 'faq') stats.faqQuestions += headingsOf(d.body).filter((h) => h.level === 2).length;
  }
  return { errors, warns, docs, stats };
}

function main(argv) {
  const verbose = argv.includes('--verbose');
  const { errors, warns, stats } = checkKb(ROOT);
  const fmt = (x) => `${x.file}${x.line ? ':' + x.line : ''}  ${x.msg}`;
  for (const e of errors) console.error('ERROR ' + fmt(e));
  // WARN lines: a sample per file and rule unless --verbose
  const shown = new Map();
  let hidden = 0;
  for (const w of warns) {
    const key = w.file + '|' + w.msg.split(':')[0];
    const n = shown.get(key) || 0;
    shown.set(key, n + 1);
    if (verbose || n < 2) console.log('WARN  ' + fmt(w));
    else hidden++;
  }
  if (hidden) console.log(`WARN  ... ${hidden} more warning lines hidden (run with --verbose)`);
  console.log('\nsection'.padEnd(12) + 'documents'.padStart(10) + 'words'.padStart(9));
  for (const [dir, s] of Object.entries(stats.byDir)) console.log(dir.padEnd(11) + String(s.docs).padStart(10) + String(s.words).padStart(9));
  console.log(`total      ${String(stats.documents).padStart(10)}${String(stats.words).padStart(9)}   (${stats.faqQuestions} FAQ questions)`);
  const e = stats.eval;
  if (e.questions) console.log(`eval: ${e.questions} questions; FAQ categories covered ${e.faqCovered}/${e.faqTotal}; guides covered ${e.guidesCovered}/${e.guidesTotal}`);
  console.log(`\n${errors.length} error(s), ${warns.length} warning(s)`);
  return errors.length ? 1 : 0;
}

if (require.main === module) {
  try { process.exitCode = main(process.argv.slice(2)); } catch (e) { console.error('ERROR ' + e.stack); process.exitCode = 1; }
}

module.exports = { checkKb, LEGACY, FAQ_BUCKETS };
