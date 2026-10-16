'use strict';

function percentage(value) { return Math.round(value * 10) / 10; }
function formatStatus(data, now = Date.now() / 1000) {
  if (!data || !Array.isArray(data.profiles)) throw new Error('Invalid dashboard response');
  if (!data.profiles.length) return {text: '$(pulse) AI usage: no accounts', tooltip: 'Register accounts with ai-sub add. Click to open the usage chart.'};
  const text = [], details = ['AI subscription usage — percentages used'];
  for (const account of data.profiles) {
    const windows = account.sample?.windows || [];
    let freshness = account.freshness;
    if (freshness === 'fresh') {
      const observed = account.sample?.observed;
      if (!Number.isFinite(observed) || now - observed > 300 || observed > now + 60) freshness = 'stale';
      else if (windows.some(w => !Number.isFinite(w.reset) || w.reset <= now)) freshness = 'refresh needed';
    }
    const summary = windows.map(w => `${w.minutes === 10080 ? '7d' : w.minutes ? `${w.minutes / 60}h` : w.label} ${percentage(w.used)}%`);
    const prefix = account.provider === 'codex' ? 'C' : 'A';
    const marker = account.sample?.blocked ? '!' : freshness !== 'fresh' && windows.length ? '~' : '';
    text.push(`${prefix}:${account.name} ${marker}${summary.join(' / ') || '?'}`);
    details.push(`${account.profile}${account.email ? ` (${account.email})` : ''}: ${summary.join(', ') || 'usage unknown'} — ${freshness}`);
    if (Number.isFinite(account.sample?.observed)) details.push(`  Usage observed ${new Date(account.sample.observed * 1000).toLocaleString()}`);
    details.push(`  ${freshness !== 'fresh' ? 'Fresh usage needed for automatic selection' : account.capacity}`);
  }
  details.push('', 'C = Codex · A = Claude · ? = missing · ~ = needs refresh · ! = provider block',
    'Readings older than 5 minutes are stale. Reading saved observations does not refresh provider usage.');
  return {text: '$(pulse) ' + text.join('  ·  '), tooltip: details.join('\n')};
}

function renderTemplate(template, data, nonce) {
  return template.replaceAll('__AI_SUB_NONCE__', nonce)
    .replace('__AI_SUB_MODE__', 'vscode')
    .replace('__AI_SUB_DATA__', JSON.stringify(data).replaceAll('<', '\\u003c').replaceAll('&', '\\u0026'));
}

module.exports = {formatStatus, renderTemplate};
