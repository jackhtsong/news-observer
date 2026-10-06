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
