#!/usr/bin/env node
'use strict';
/* Export the CUSTOMER documents for an AWS Bedrock Knowledge Base (S3 data source).
 *   node scripts/export-bedrock.js [--out dist/bedrock] [--include-playbook]
 * Copies kb/faq/** and kb/guides/** to <out>/faq/** and <out>/guides/** (same file names), adds a sidecar `<file>.md.metadata.json`
 *   {"metadataAttributes": {"slug","title","category","audience","source","plan","handoff","app_path","updated","tags"}}
 * (all values are strings, empty ones are left out, tags are comma-joined, each sidecar stays under 10 KB) and writes manifest.json.
 * The internal playbook is NOT part of the customer data source: with --include-playbook it is copied to <out>/playbook/ so it can be
 * used as the assistant's instructions (keep it out of the customer data source). The output folder is rebuilt on every run; the script
 * refuses to delete a non-empty folder that it did not create (no manifest.json). */
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { ROOT, readText, walk, parseFrontMatter } = require('./lib/util');
const { slugOf } = require('./build-index');

const ATTRS = ['slug', 'title', 'category', 'audience', 'source', 'plan', 'handoff', 'app_path', 'updated', 'tags'];
const MAX_SIDECAR = 10 * 1024;

function sidecarFor(slug, data) {
  const attrs = {};
  const values = { slug, ...data, plan: data.plan || 'all' };
  for (const k of ATTRS) {
    let v = values[k];
    if (v === undefined || v === null) continue;
    v = String(v).trim();
    if (k === 'tags') v = v.split(',').map((t) => t.trim()).filter(Boolean).join(',');
    if (v) attrs[k] = v;
  }
  const json = JSON.stringify({ metadataAttributes: attrs }, null, 2) + '\n';
  if (Buffer.byteLength(json) >= MAX_SIDECAR) throw new Error(`metadata of ${slug} is ${Buffer.byteLength(json)} bytes (limit 10 KB)`);
  return json;
}

function exportBedrock(root, outDir, opts = {}) {
  const dirs = ['faq', 'guides'].concat(opts.includePlaybook ? ['playbook'] : []);
  if (fs.existsSync(outDir) && fs.readdirSync(outDir).length) {
    if (!fs.existsSync(path.join(outDir, 'manifest.json'))) throw new Error(`${outDir} is not empty and was not created by this script (no manifest.json): refusing to delete it`);
    fs.rmSync(outDir, { recursive: true, force: true });
  }
  fs.mkdirSync(outDir, { recursive: true });
  const manifest = [];
  for (const dir of dirs) {
    for (const rel of walk(path.join(root, 'kb', dir), (r) => r.endsWith('.md'))) {
      const text = readText(path.join(root, 'kb', dir, rel));
      const fm = parseFrontMatter(text);
      if (fm.errors.length) throw new Error(`kb/${dir}/${rel}: ${fm.errors[0]}`);
      const slug = slugOf(dir, rel);
      const target = path.join(outDir, dir, rel);
      fs.mkdirSync(path.dirname(target), { recursive: true });
      fs.writeFileSync(target, text, 'utf8');
      const sidecar = sidecarFor(slug, fm.data);
      fs.writeFileSync(target + '.metadata.json', sidecar, 'utf8');
      manifest.push({
        slug, key: `${dir}/${rel.replace(/\\/g, '/')}`, source: fm.data.source || '', bytes: Buffer.byteLength(text),
        sha256: crypto.createHash('sha256').update(text).digest('hex'),
      });
    }
  }
  manifest.sort((a, b) => (a.key < b.key ? -1 : 1));
  fs.writeFileSync(path.join(outDir, 'manifest.json'), JSON.stringify({ files: manifest.length, includePlaybook: !!opts.includePlaybook, documents: manifest }, null, 2) + '\n', 'utf8');
  return manifest;
}

function main(argv) {
  const i = argv.indexOf('--out');
  const out = path.resolve(ROOT, i >= 0 ? argv[i + 1] : path.join('dist', 'bedrock'));
  const includePlaybook = argv.includes('--include-playbook');
  const manifest = exportBedrock(ROOT, out, { includePlaybook });
  const customer = manifest.filter((m) => m.source !== 'playbook').length;
  console.log(`exported ${manifest.length} documents (${customer} customer, ${manifest.length - customer} playbook) with metadata files to ${out}`);
  console.log('\nUpload (placeholders: use your own bucket; keep the playbook out of the customer data source):');
  console.log(`  aws s3 sync "${path.join(out, 'faq')}" s3://<your-bucket>/clockyn-kb/faq/ --delete`);
  console.log(`  aws s3 sync "${path.join(out, 'guides')}" s3://<your-bucket>/clockyn-kb/guides/ --delete`);
  if (includePlaybook) console.log(`  aws s3 sync "${path.join(out, 'playbook')}" s3://<your-bucket>/clockyn-playbook/ --delete   # NOT the customer data source`);
  console.log('Then start an ingestion job for the Knowledge Base data source (console or the StartIngestionJob API).');
  return 0;
}

if (require.main === module) {
  try { process.exitCode = main(process.argv.slice(2)); } catch (e) { console.error('ERROR ' + e.message); process.exitCode = 1; }
}

module.exports = { exportBedrock, sidecarFor };
