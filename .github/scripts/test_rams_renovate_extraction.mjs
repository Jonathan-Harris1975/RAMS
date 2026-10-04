// Extract only: never look up versions, compile locks or write dependencies.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const pinnedVersion = '44.132.5';
let packageRoot;
for (const directory of (process.env.PATH || '').split(path.delimiter)) {
  const candidate = path.resolve(directory, '..', 'renovate');
  try {
    const metadata = JSON.parse(await fs.readFile(path.join(candidate, 'package.json'), 'utf8'));
    if (metadata.name === 'renovate' && metadata.version === pinnedVersion) {
      packageRoot = candidate;
      break;
    }
  } catch {
    // Other PATH entries do not contain the pinned npm package.
  }
}
assert.ok(packageRoot, 'Run through npm exec with the pinned Renovate package');
const load = async (file) => import(pathToFileURL(path.join(packageRoot, 'dist', file)).href);
const { GlobalConfig } = await load('config/global.js');
const { extractAllPackageFiles } = await load('modules/manager/pip-compile/extract.js');
const { extractHeaderCommand } = await load('modules/manager/pip-compile/common.js');
const { constructPipCompileCmd } = await load('modules/manager/pip-compile/artifacts.js');
GlobalConfig.set({ localDir: process.cwd(), platform: 'local' });
const config = JSON.parse(await fs.readFile('renovate.json', 'utf8'));
const pattern = config['pip-compile'].managerFilePatterns[0];
const matcher = new RegExp(pattern.slice(1, -1));
const locks = [
  'requirements-runtime.lock', 'requirements-bootstrap.txt', 'requirements-build.txt',
  'requirements-dev.txt', 'requirements-lock-tools.txt',
];
const sources = [
  'requirements.in', 'requirements-bootstrap.in', 'requirements-build.in',
  'requirements-dev.in', 'requirements-lock-tools.in',
];
assert.equal(config.pip_requirements.enabled, false, 'Avoid competing extraction of compiled locks');
for (const lock of locks) {
  assert.ok(matcher.test(lock), `Unmatched canonical lock: ${lock}`);
  const content = await fs.readFile(lock, 'utf8');
  const args = extractHeaderCommand(content, lock);
  const command = constructPipCompileCmd(args);
  assert.ok(command.includes('--generate-hashes'));
  assert.ok(command.includes('--no-emit-index-url'));
  assert.equal(args.outputFile, lock);
  assert.equal(args.sourceFiles.length, 1);
  assert.ok(sources.includes(args.sourceFiles[0]));
}
for (const source of sources) assert.equal(matcher.test(source), false);
assert.equal(matcher.test('other/requirements.txt'), false);
const files = await extractAllPackageFiles({}, locks);
assert.equal(files.length, 5, 'Extract all five authoritative source inputs');
assert.deepEqual(files.map((file) => file.packageFile).sort(), sources.sort());
for (const lock of locks) assert.ok(files.some((file) => file.lockFiles.includes(lock)));
const runtime = files.find((file) => file.packageFile === 'requirements.in');
assert.ok(runtime.lockFiles.includes('requirements-runtime.lock'));
assert.ok(runtime.lockFiles.includes('requirements-dev.txt'), 'Propagate runtime updates to the included development lock');
assert.ok(runtime.deps.some((dep) => dep.depName === 'uvicorn'));
assert.ok(runtime.deps.some((dep) => dep.depName === 'psycopg'));
console.log('PASS: Renovate 44.132.5 extracts all five canonical sources and outputs, propagates runtime locks, and accepts hashed compile commands; no lookup or regeneration performed');
