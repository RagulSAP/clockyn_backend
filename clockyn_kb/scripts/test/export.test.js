'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { exportBedrock, sidecarFor } = require('../export-bedrock');

const doc = (o = {}) => '---\n' + Object.entries({ title: 'T', summary: 'S.', category: 'Guides', audience: 'admin', source: 'guide', handoff: 'offer', tags: 'a, b ,c', updated: '2026-11-15', ...o })
  .map(([k, v]) => `${k}: ${v}`).join('\n') + '\n---\nBody.\n';

function fixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'kb-export-'));
  const put = (rel, text) => { fs.mkdirSync(path.dirname(path.join(root, rel)), { recursive: true }); fs.writeFileSync(path.join(root, rel), text); };
  put('kb/faq/help-and-fixes/troubleshooting.md', doc({ title: 'Troubleshooting', category: 'Help & Fixes', audience: 'all', source: 'faq', plan: 'all' }));
  put('kb/guides/add-staff.md', doc({ app_path: 'Staff > Add Staff' }));
  put('kb/playbook/rules.md', doc({ audience: 'bot', source: 'playbook', category: 'Playbook' }));
  return root;
}

test('sidecar: strings only, empty values left out, tags comma-joined, plan defaults to all', () => {
  const json = JSON.parse(sidecarFor('add-staff', { title: 'T', category: 'Guides', audience: 'admin', source: 'guide', handoff: 'none', tags: 'a, b ,c', updated: '2026-11-15', app_path: '' }));
  assert.deepEqual(Object.keys(json), ['metadataAttributes']);
  assert.deepEqual(json.metadataAttributes, { slug: 'add-staff', title: 'T', category: 'Guides', audience: 'admin', source: 'guide', plan: 'all', handoff: 'none', updated: '2026-11-15', tags: 'a,b,c' });
  for (const v of Object.values(json.metadataAttributes)) assert.equal(typeof v, 'string');
});

test('sidecar over 10 KB is refused', () => {
  assert.throws(() => sidecarFor('x', { title: 'T', tags: 'a'.repeat(11000) }), /limit 10 KB/);
});

test('export copies customer documents with sidecars and a manifest; the playbook only on request', () => {
  const root = fixture();
  const out = path.join(root, 'dist', 'bedrock');
  try {
    const m = exportBedrock(root, out);
    assert.deepEqual(m.map((x) => x.key), ['faq/help-and-fixes/troubleshooting.md', 'guides/add-staff.md']);
    assert.ok(fs.existsSync(path.join(out, 'guides', 'add-staff.md')));
    const side = JSON.parse(fs.readFileSync(path.join(out, 'guides', 'add-staff.md.metadata.json'), 'utf8'));
    assert.equal(side.metadataAttributes.app_path, 'Staff > Add Staff');
    assert.equal(side.metadataAttributes.slug, 'add-staff');
    assert.equal(JSON.parse(fs.readFileSync(path.join(out, 'faq', 'help-and-fixes', 'troubleshooting.md.metadata.json'), 'utf8')).metadataAttributes.slug, 'faq-troubleshooting');
    assert.ok(!fs.existsSync(path.join(out, 'playbook')));
    const manifest = JSON.parse(fs.readFileSync(path.join(out, 'manifest.json'), 'utf8'));
    assert.equal(manifest.files, 2);
    assert.match(manifest.documents[0].sha256, /^[0-9a-f]{64}$/);

    const m2 = exportBedrock(root, out, { includePlaybook: true });      // rebuilds the folder it created
    assert.equal(m2.length, 3);
    assert.ok(fs.existsSync(path.join(out, 'playbook', 'rules.md.metadata.json')));
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test('export refuses to delete a non-empty folder it did not create', () => {
  const root = fixture();
  const out = path.join(root, 'precious');
  try {
    fs.mkdirSync(out);
    fs.writeFileSync(path.join(out, 'keep.txt'), 'mine');
    assert.throws(() => exportBedrock(root, out), /refusing to delete/);
    assert.equal(fs.readFileSync(path.join(out, 'keep.txt'), 'utf8'), 'mine');
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});
