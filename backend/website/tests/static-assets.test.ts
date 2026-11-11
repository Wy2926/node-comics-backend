import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { locales } from '../src/i18n';

test('language flags use explicit external assets instead of a per-page data URL', () => {
  const source = readFileSync(new URL('../src/components/LanguageFlag.astro', import.meta.url), 'utf8');
  const imports = [...source.matchAll(/from '\.\.\/assets\/flags\/([a-z]{2})\.svg\?url&no-inline'/g)];
  assert.equal(imports.length, locales.length);
  assert.equal(new Set(imports.map(match => match[1])).size, locales.length);
  assert.doesNotMatch(source, /\?raw|data:image|encodeURIComponent/);
  assert.match(source, /loading = 'lazy'/);
  for (const name of ['../src/layouts/Base.astro', '../src/components/PaymentSuccess.astro', '../src/components/UninstallPage.astro']) {
    const page = readFileSync(new URL(name, import.meta.url), 'utf8');
    assert.equal((page.match(/<LanguageFlag locale=\{locale\} loading="eager"\/>/g) ?? []).length, 1);
  }
});

test('local asset originals retain their recorded bytes across repository checkouts', () => {
  const assets = JSON.parse(readFileSync(new URL('../assets/provenance.json', import.meta.url), 'utf8'));
  // GitHubMark records the upstream SVG hash, not the Astro wrapper's bytes.
  const records: string[][] = assets.sha256_records.filter((row: string[]) => !row[0].includes('GitHubMark.astro'));
  assert.equal(records.length, 94);
  assert.equal(records.filter(row => row[0].includes('src/assets/guides/firefox/')).length, 6);
  for (const [source, ...details] of records) {
    const path = source.match(/\]\(([^)]+)\)/)?.[1] ?? `src/assets/home/${source.replaceAll('`', '')}`;
    const expected = details.at(-1)?.match(/[a-f0-9]{64}/)?.[0];
    assert.ok(expected, path);
    const original = readFileSync(new URL(`../${path}`, import.meta.url));
    assert.equal(createHash('sha256').update(original).digest('hex'), expected, path);
  }
});
