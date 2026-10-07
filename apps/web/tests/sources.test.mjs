import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
const output = ts.transpileModule(readFileSync(new URL('../src/lib/sources.ts', import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 } }).outputText;
const { sourceLabel } = await import(`data:text/javascript;base64,${Buffer.from(output).toString('base64')}`);

test('sources read as outlet names on cards', () => {
 assert.equal(sourceLabel('bbc'), 'BBC');
 assert.equal(sourceLabel('guardian'), 'THE GUARDIAN');
 assert.equal(sourceLabel('aljazeera'), 'AL JAZEERA');
 assert.equal(sourceLabel('conflict_csv'), 'GDELT');
 assert.equal(sourceLabel('acled'), 'ACLED', 'unknown sources keep their id');
});

test('GDELT stories name the outlet that published the article', () => {
 const url = 'https://news.wjct.org/national-news/2026-10-05/republicans-and-democrats-find-a-unifying-target';
 assert.equal(sourceLabel('conflict_csv', url), 'NEWS.WJCT.ORG VIA GDELT');
 assert.equal(sourceLabel('conflict_csv', 'https://www.example.com/a'), 'EXAMPLE.COM VIA GDELT', 'www. is dropped');
 assert.equal(sourceLabel('conflict_csv', null), 'GDELT', 'no link: just the feed');
 assert.equal(sourceLabel('conflict_csv', 'javascript:alert(1)'), 'GDELT', 'only http(s) links name an outlet');
 assert.equal(sourceLabel('bbc', 'https://www.bbc.co.uk/news/world-1'), 'BBC', 'direct feeds keep their own name');
});
