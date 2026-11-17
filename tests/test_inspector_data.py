"""Run the real offline importer, including failed and cancelled user actions."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


def test_record_import_and_export():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is unavailable for optional frontend regression")
    web = Path(__file__).parents[1] / "src" / "web"
    script = (web / "inspector-data.js").read_text()
    strings = json.loads((web / "inspector.en.json").read_text())
    harness = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const { script, strings } = JSON.parse(fs.readFileSync(0, 'utf8'));
const nodes = new Map();
const $ = id => {
  if (!nodes.has(id)) nodes.set(id, {
    value: '', hidden: true, open: false, files: [], handlers: {},
    addEventListener(name, fn) { this.handlers[name] = fn; },
    showModal() { this.open = true; },
    close() { this.open = false; this.handlers.close?.(); },
  });
  return nodes.get(id);
};
let downloaded, downloadLink;
const context = vm.createContext({
  $, TextEncoder, Blob,
  t: (key, values = {}) => (strings[key] || key).replace(/\{(\w+)\}/g,
    (_, name) => String(values[name] ?? '')),
  selectedRecord: 0, records: [{ status: 'ok', reward: 1 }],
  element: () => (downloadLink = { click() { this.clicked = true; } }),
  URL: { createObjectURL(blob) { downloaded = blob; return 'blob:test'; }, revokeObjectURL() {} },
  setTimeout: fn => fn(),
});
context.replaceRecords = records => { context.records = records; context.selectedRecord = 0; };
vm.runInContext(script, context);
const parse = text => context.parseRecords(text);
assert.equal(parse('\uFEFF {"status":"invalid"}')[0].criteria.length, 0);
assert.equal(parse('[{"status":"ok"},{"status":"invalid"}]').length, 2);
assert.equal(parse('{"status":"ok"}\n\n{"status":"invalid"}\n').length, 2);
assert.throws(() => parse(''), /at least one/);
assert.throws(() => parse('[]'), /at least one/);
assert.throws(() => parse('{"status":"ok"}\ninvalid'), /line 2/);
assert.throws(() => parse('{"request":"unscored"}'), /unscored trajectory/);
assert.throws(() => parse('{"status":"ok","schema_version":2}'), /schema_version/);
assert.throws(() => parse('{"status":"ok","criteria":[null]}'), /must be objects/);
assert.throws(() => parse('{"status":"ok","metadata":[]}'), /must be an object/);
assert.throws(() => parse('{"status":"ok","reward":1e999}'), /non-finite/);
assert.throws(() => parse(' '.repeat(21 * 1024 * 1024) + 'x'), /20 MB/);
assert.throws(() => parse('x'.repeat(21 * 1024 * 1024)), /20 MB/);

async function run() {
  $('open-data').handlers.click();
  $('data-text').value = '{"request":"unscored"}';
  $('load-pasted').handlers.click();
  assert.equal(context.records[0].reward, 1, 'failed import preserves prior data');
  assert.equal($('data-dialog').open, true);
  assert.equal($('import-error').hidden, false);
  $('data-text').value = JSON.stringify({status: 'invalid', case: {request: 'new query'},
    metadata: {display_query: 'Only the displayed query', display_reasoning: {response: 'Already provided thought\nFull original text'}}});
  $('load-pasted').handlers.click();
  assert.equal(context.records[0].case.request, 'new query');
  assert.equal($('data-dialog').open, false);
  assert.match($('data-feedback').textContent, /Loaded 1 records/);

  $('export-record').handlers.click();
  const exported = JSON.parse(await downloaded.text());
  assert.equal(exported.case.request, 'new query');
  assert.equal(exported.metadata.display_query, 'Only the displayed query');
  assert.equal(exported.metadata.display_reasoning.response, 'Already provided thought\nFull original text');
  assert.deepEqual(JSON.parse(JSON.stringify(parse(JSON.stringify(exported))[0].metadata)), exported.metadata);
  assert.equal(downloadLink.clicked, true);
  assert.equal(downloadLink.download, 'reward-record-1.json');

  $('open-data').handlers.click();
  let resolve;
  $('data-file').files = [{ name: 'slow.json', size: 100,
    text: () => new Promise(done => { resolve = done; }) }];
  const pending = $('data-file').handlers.change();
  $('close-data').handlers.click();
  resolve('{"status":"ok","case":{"request":"stale"}}');
  await pending;
  assert.equal(context.records[0].case.request, 'new query', 'cancelled file cannot overwrite data');

  $('open-data').handlers.click();
  $('data-file').files = [{ name: 'two.jsonl', size: 100,
    text: async () => '{"status":"ok"}\n{"status":"invalid"}' }];
  await $('data-file').handlers.change();
  assert.equal(context.records.length, 2);
  assert.match($('data-feedback').textContent, /two.jsonl/);
}
run().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(
        [node, "-e", harness],
        input=json.dumps({"script": script, "strings": strings}),
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
