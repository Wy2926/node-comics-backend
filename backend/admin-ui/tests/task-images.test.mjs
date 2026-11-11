import assert from 'node:assert/strict';
import {test} from 'node:test';
import {readFileSync} from 'node:fs';
import ts from 'typescript';

function load(path, imports = {}) {
  const {outputText} = ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), {
    compilerOptions: {target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS},
  });
  const module = {exports: {}};
  new Function('require', 'module', 'exports', outputText)(name => imports[name], module, module.exports);
  return module.exports;
}
globalThis.sessionStorage = {getItem: () => 'private-admin-token'};
const api = load('../src/api.ts');

async function fixture(representation, options = {}) {
  const original = new Blob(['original'], {type: 'image/png'}), artifact = new Blob(['artifact'], {type: 'image/webp'});
  const result = {kind: representation === 'original' ? 'no_text' : 'translated', representation,
    artifact: representation === 'original' ? null : {path: '/artifact'}};
  const descriptor = {original: options.expired ? null : '/original', original_error: options.expired ? '原图已过期' : null,
    input_expires_at: '2026-11-12T00:00:00Z', result, result_error: null, result_expires_at: '2026-11-18T00:00:00Z'};
  const requests = [], composed = [];
  const controller = new AbortController();
  const previous = globalThis.fetch;
  globalThis.fetch = async (path, init) => {
    requests.push(path);
    assert.equal(init.cache, 'no-store');
    assert.equal(init.headers.Authorization, 'Bearer private-admin-token');
    assert.equal(init.signal, controller.signal);
    if (path.includes('/images')) return Response.json(descriptor);
    if (options.abort) controller.abort();
    if (path === '/original' && options.failure) return Response.json({error: {message: '读取失败'}}, {status: options.failure});
    return new Response(path === '/original' ? original : artifact);
  };
  const {loadTaskImages} = load('../src/taskImagePreview.ts', {'./api': api,
    '../../shared/translation-images/materialize': {materializeResult: async (...args) => {
      composed.push(args);
      if (options.invalid) throw Error('翻译文件校验失败');
      return representation === 'original' ? args[1] : artifact;
    }},
  });
  try {return {preview: await loadTaskImages('job', controller.signal), requests, composed};}
  finally {globalThis.fetch = previous;}
}

for (const representation of ['overlay-v1', 'overlay-tiles-v1']) {
  test(`${representation} loads authenticated inputs and uses the shared full-image compositor`, async () => {
    const {preview, requests, composed} = await fixture(representation);
    assert.deepEqual(requests, ['/v1/admin/monitor/tasks/job/images', '/original', '/artifact']);
    assert.equal(await preview.original.text(), 'original');
    assert.ok(preview.translated);
    assert.equal(await composed[0][1].text(), 'original');
    assert.equal(await composed[0][2].text(), 'artifact');
    assert.equal(preview.inputExpiresAt, '2026-11-12T00:00:00Z');
  });
}

test('no-text preview uses only the original', async () => {
  const {preview, requests} = await fixture('original');
  assert.equal(preview.original, preview.translated);
  assert.equal(preview.noText, true);
  assert.equal(requests.length, 2);
});

test('full-image result remains visible after original expiry or storage failure', async () => {
  for (const options of [{expired: true}, {failure: 503}]) {
    const {preview, composed} = await fixture('full-image-v1', options);
    assert.equal(preview.original, undefined);
    assert.ok(preview.originalError && preview.translated);
    assert.equal(composed[0][1], undefined);
  }
});

test('missing original never presents an overlay artifact as a complete translation', async () => {
  const {preview, composed, requests} = await fixture('overlay-v1', {expired: true});
  assert.equal(preview.translated, undefined);
  assert.match(preview.resultError, /无法合成完整译图/);
  assert.equal(composed.length, 0);
  assert.deepEqual(requests, ['/v1/admin/monitor/tasks/job/images']);
});

test('authorization loss and cancellation propagate instead of leaving stale previews', async () => {
  await assert.rejects(fixture('overlay-tiles-v1', {failure: 403}), error => api.authError(error));
  await assert.rejects(fixture('overlay-tiles-v1', {abort: true}), {name: 'AbortError'});
});

test('invalid artifacts keep the original and report verification failure', async () => {
  const {preview} = await fixture('overlay-v1', {invalid: true});
  assert.ok(preview.original);
  assert.equal(preview.translated, undefined);
  assert.match(preview.resultError, /校验失败/);
});
