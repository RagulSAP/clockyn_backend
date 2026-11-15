#!/usr/bin/env node
'use strict';
/* `npm test`: runs every scripts/test/*.test.js with node:test (explicit file list, so it works on every Node version and OS)
 * and, when Python is available, the unittest of the database export helper. */
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const dir = path.join(__dirname, 'test');
const files = fs.readdirSync(dir).filter((f) => f.endsWith('.test.js')).sort().map((f) => path.join(dir, f));
let code = spawnSync(process.execPath, ['--test', ...files], { stdio: 'inherit' }).status || 0;

const py = fs.readdirSync(dir).filter((f) => /^test_.*\.py$/.test(f)).sort();
for (const exe of ['python', 'python3']) {
  const probe = spawnSync(exe, ['--version'], { stdio: 'ignore' });
  if (probe.error || probe.status !== 0) continue;
  for (const f of py) {
    const r = spawnSync(exe, ['-I', path.join(dir, f)], { stdio: 'inherit' });
    code = code || r.status || 0;
  }
  break;
}
process.exitCode = code;
