'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

async function harness(fail = false) {
  const commands = new Map(), calls = [], updates = [], listeners = [];
  const item = {show() { this.shown = true; }, dispose() { this.disposed = true; }};
  const disposable = () => ({dispose() {}});
  const panel = {webview: {postMessage(message) { updates.push(message); }, onDidReceiveMessage(fn) { listeners.push(fn); return disposable(); }},
    onDidDispose() { return disposable(); }, reveal() {}, dispose() {}};
  const vscode = {
    StatusBarAlignment: {Right: 2}, ViewColumn: {Beside: -2},
    window: {createStatusBarItem(id, alignment) { item.alignment = alignment; return item; }, createWebviewPanel() { return panel; }},
    commands: {registerCommand(name, callback) { commands.set(name, callback); return disposable(); }},
    workspace: {
      getConfiguration() { return {inspect() { return {workspaceValue: '/malicious/workspace-program'}; }}; },
      onDidChangeConfiguration() { return disposable(); }
    }
  };
  const fixture = {version: 1, generated_at: Date.now() / 1000, profiles: [
    {provider: 'codex', name: 'work', profile: 'codex/work', freshness: 'fresh', capacity: 'Weekly only',
     sample: {windows: [{minutes: 10080, used: 4}]}}
  ]};
  const module = {exports: {}};
  const source = fs.readFileSync(path.join(__dirname, '../extension.js'), 'utf8');
  vm.runInNewContext(source, {module, console, process, Buffer, setInterval: () => 1, clearInterval() {},
    require(name) {
      if (name === 'vscode') return vscode;
      if (name === 'node:child_process') return {execFile(executable, args, options, callback) {
        calls.push({executable, args, options});
        queueMicrotask(() => callback(fail ? new Error('sensitive stderr that must not leak') : null, JSON.stringify(fixture)));
        return {kill() {}};
      }};
      if (name === './status') return require('../status');
      return require(name);
    }});
  const context = {extensionPath: path.join(__dirname, '..'), subscriptions: []};
  module.exports.activate(context);
  await new Promise(resolve => setImmediate(resolve));
  return {commands, calls, item, panel, listeners, updates, close() { context.subscriptions.forEach(s => s.dispose()); }};
}

test('startup creates a visible right-aligned indicator and reads only saved observations', async () => {
  const h = await harness();
  try {
    assert.equal(h.item.alignment, 2);
    assert.equal(h.item.shown, true);
    assert.match(h.item.text, /7d 4%/);
    assert.deepEqual(Array.from(h.calls[0].args), ['dashboard', '--json']);
    assert.notEqual(h.calls[0].executable, '/malicious/workspace-program');
    assert.equal(h.calls[0].options.shell, undefined);
    h.commands.get('aiSubscriptions.showUsage')();
    assert.match(h.panel.webview.html, /Subscription usage/);
    assert.match(h.panel.webview.html, /default-src 'none'/);
  } finally { h.close(); }
});

test('fetching provider usage requires the explicit refresh command', async () => {
  const h = await harness();
  try {
    assert.equal(h.calls.length, 1);
    await h.commands.get('aiSubscriptions.fetchCodex')();
    assert.deepEqual(Array.from(h.calls[1].args), ['status', 'codex', '--refresh', '--json']);
    assert.deepEqual(Array.from(h.calls[2].args), ['dashboard', '--json']);
  } finally { h.close(); }
});

test('missing CLI keeps a visible error indicator without leaking subprocess errors', async () => {
  const h = await harness(true);
  try {
    assert.equal(h.item.shown, true);
    assert.match(h.item.text, /unavailable/);
    assert.doesNotMatch(h.item.tooltip, /sensitive stderr/);
  } finally { h.close(); }
});
