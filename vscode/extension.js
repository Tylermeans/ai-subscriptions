'use strict';
const vscode = require('vscode');
const {execFile} = require('node:child_process');
const {randomBytes} = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {formatStatus, renderTemplate} = require('./status');

function activate(context) {
  const item = vscode.window.createStatusBarItem('aiSubscriptions.usage', vscode.StatusBarAlignment.Right, 100);
  item.name = 'AI Subscription Usage';
  item.command = 'aiSubscriptions.showUsage';
  item.text = '$(pulse) AI usage: loading';
  item.show();
  let panel, timer, ageTimer, reading, fetching, disposed = false;
  let latest = {version: 1, generated_at: Date.now() / 1000, profiles: []};
  let lastError = null;
  const children = new Set();
  // Only user/machine settings may select a program or account state location.
  // Never execute a path contributed by the repository currently open in VS Code.
  function setting(name, fallback) {
    return vscode.workspace.getConfiguration('aiSubscriptions').inspect(name)?.globalValue ?? fallback;
  }
  function execute(args, timeout = 15000) {
    const configured = setting('executablePath', '');
    const installed = path.join(os.homedir(), '.local', 'bin', 'ai-sub');
    const executable = configured || (fs.existsSync(installed) ? installed : 'ai-sub');
    const directory = setting('stateDirectory', '');
    if ((configured && !path.isAbsolute(configured)) || (directory && !path.isAbsolute(directory))) {
      return Promise.reject(new Error('Use absolute paths in AI Subscription Usage User settings.'));
    }
    return new Promise((resolve, reject) => {
      const child = execFile(executable, args, {cwd: os.homedir(), env: {...process.env, ...(directory ? {AI_SUB_HOME: directory} : {})},
        timeout, maxBuffer: 2 * 1024 * 1024, windowsHide: true}, (error, stdout) => {
        children.delete(child);
        if (error) reject(new Error('Could not read ai-sub. Check its installation, Python, and AI Subscription Usage User settings.'));
        else {
          try { resolve(JSON.parse(stdout)); }
          catch { reject(new Error('The installed ai-sub returned an unsupported response. Update the terminal tool and reload VS Code.')); }
        }
      });
      children.add(child);
    });
  }
  function showError(message) {
    lastError = message;
    item.text = '$(warning) AI usage unavailable';
    item.tooltip = message + '\nClick to open the usage chart. Last displayed measurements may be stale.';
    panel?.webview.postMessage({type: 'error', message});
  }
  async function refresh() {
    if (disposed) return;
    if (reading) return reading;
    reading = (async () => {
      try {
        const data = await execute(['dashboard', '--json']);
        const status = formatStatus(data);
        if (disposed) return;
        latest = data; lastError = null;
        item.text = status.text; item.tooltip = status.tooltip;
        panel?.webview.postMessage({type: 'snapshot', data});
      } catch (error) { if (!disposed) showError(error.message); }
    })();
    try { await reading; } finally { reading = null; }
  }
  async function fetchCodex() {
    if (fetching || disposed) return;
    fetching = true;
    try {
      await execute(['status', 'codex', '--refresh', '--json'], 120000);
      await refresh();
    } catch (error) { if (!disposed) showError(error.message); }
    finally { fetching = false; panel?.webview.postMessage({type: 'fetchDone'}); }
  }
  function showChart() {
    if (panel) { panel.reveal(); return; }
    panel = vscode.window.createWebviewPanel('aiSubscriptionUsage', 'AI Subscription Usage', vscode.ViewColumn.Beside,
      {enableScripts: true, localResourceRoots: [], retainContextWhenHidden: true});
    const template = fs.readFileSync(path.join(context.extensionPath, 'media', 'dashboard.html'), 'utf8');
    panel.webview.html = renderTemplate(template, latest, randomBytes(16).toString('hex'));
    panel.webview.onDidReceiveMessage(message => {
      if (message?.type === 'ready') {
        panel?.webview.postMessage({type: 'snapshot', data: latest});
        if (lastError) panel?.webview.postMessage({type: 'error', message: lastError});
      } else if (message?.type === 'refresh') void refresh();
      else if (message?.type === 'fetchCodex') void fetchCodex();
    }, null, context.subscriptions);
    panel.onDidDispose(() => { panel = null; }, null, context.subscriptions);
    void refresh();
  }
  function schedule() {
    clearInterval(timer);
    clearInterval(ageTimer);
    const seconds = Math.max(5, Math.min(300, Number(setting('refreshSeconds', 15)) || 15));
    timer = setInterval(() => void refresh(), seconds * 1000);
    // Expire cached readings even when display polling is slow or temporarily stalled.
    ageTimer = setInterval(() => {
      if (disposed || lastError) return;
      const status = formatStatus(latest);
      item.text = status.text; item.tooltip = status.tooltip;
    }, 1000);
    void refresh();
  }
  context.subscriptions.push(item,
    vscode.commands.registerCommand('aiSubscriptions.showUsage', showChart),
    vscode.commands.registerCommand('aiSubscriptions.refresh', refresh),
    vscode.commands.registerCommand('aiSubscriptions.fetchCodex', fetchCodex),
    vscode.workspace.onDidChangeConfiguration(event => { if (event.affectsConfiguration('aiSubscriptions')) schedule(); }),
    {dispose() { disposed = true; clearInterval(timer); clearInterval(ageTimer); for (const child of children) child.kill(); panel?.dispose(); }});
  schedule();
}

module.exports = {activate};
