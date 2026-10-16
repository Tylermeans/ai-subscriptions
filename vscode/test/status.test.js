'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {formatStatus, renderTemplate} = require('../status');

const fixture = {generated_at: 123, profiles: [
  {profile: 'codex/work', provider: 'codex', name: 'work', email: 'work@example.com', freshness: 'fresh',
   capacity: 'Both windows required', sample: {windows: [{minutes: 10080, used: 4}]}},
  {profile: 'claude/personal', provider: 'claude', name: 'personal', freshness: 'unknown', capacity: 'Usage needed', sample: null}
]};

test('right-side status represents every account without inventing missing usage', () => {
  const display = formatStatus(fixture);
  assert.match(display.text, /C:work 7d 4%/);
  assert.match(display.text, /A:personal \?/);
  assert.doesNotMatch(display.text, /0%/);
  assert.match(display.tooltip, /percentages used/);
});

test('old observations and provider blocks stay visible', () => {
  const data = structuredClone(fixture);
  data.profiles[0].freshness = 'stale';
  assert.match(formatStatus(data).text, /~7d 4%/);
  data.profiles[0].sample.blocked = true;
  assert.match(formatStatus(data).text, /!7d 4%/);
});

test('webview data cannot escape its JSON script element', () => {
  const template = fs.readFileSync(path.join(__dirname, '../media/dashboard.html'), 'utf8');
  const html = renderTemplate(template, {profiles: [{email: '</script><script>bad()</script>'}]}, 'abc123');
  assert.doesNotMatch(html, /<script>bad/);
  assert.match(html, /\\u003c\/script>/);
  assert.match(html, /nonce-abc123/);
  assert.doesNotMatch(html, /__AI_SUB_/);
  const scripts = [...html.matchAll(/<script nonce="[^"]+">([\s\S]*?)<\/script>/g)];
  assert.equal(scripts.length, 1);
  new vm.Script(scripts[0][1]);
});

test('empty or malformed responses never show healthy usage', () => {
  assert.match(formatStatus({profiles: []}).text, /no accounts/);
  assert.throws(() => formatStatus({}), /Invalid/);
});

test('extension activates on startup in the UI host and exposes machine settings only', () => {
  const pkg = require('../package.json');
  assert.deepEqual(pkg.activationEvents, ['onStartupFinished']);
  assert.deepEqual(pkg.extensionKind, ['ui']);
  for (const option of Object.values(pkg.contributes.configuration.properties)) assert.equal(option.scope, 'machine');
});
