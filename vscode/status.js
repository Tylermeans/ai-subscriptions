'use strict';

function percentage(value) { return Math.round(value * 10) / 10; }
function formatStatus(data) {
  if (!data || !Array.isArray(data.profiles)) throw new Error('Invalid dashboard response');
  if (!data.profiles.length) return {text: '$(pulse) AI usage: no accounts', tooltip: 'Register accounts with ai-sub add. Click to open the usage chart.'};
  const text = [], details = ['AI subscription usage — percentages used'];
  for (const account of data.profiles) {
    const windows = account.sample?.windows || [];
    const summary = windows.map(w => `${w.minutes === 10080 ? '7d' : w.minutes ? `${w.minutes / 60}h` : w.label} ${percentage(w.used)}%`);
    const prefix = account.provider === 'codex' ? 'C' : 'A';
    const marker = account.sample?.blocked ? '!' : account.freshness !== 'fresh' && windows.length ? '~' : '';
    text.push(`${prefix}:${account.name} ${marker}${summary.join(' / ') || '?'}`);
    details.push(`${account.profile}${account.email ? ` (${account.email})` : ''}: ${summary.join(', ') || 'usage unknown'} — ${account.freshness}`);
    details.push(`  ${account.capacity}`);
  }
  details.push('', 'C = Codex · A = Claude · ? = missing · ~ = needs refresh · ! = provider block',
    'Click to open all account charts. Reading saved observations does not refresh provider usage.');
  return {text: '$(pulse) ' + text.join('  ·  '), tooltip: details.join('\n')};
}

function renderTemplate(template, data, nonce) {
  return template.replaceAll('__AI_SUB_NONCE__', nonce)
    .replace('__AI_SUB_MODE__', 'vscode')
    .replace('__AI_SUB_DATA__', JSON.stringify(data).replaceAll('<', '\\u003c').replaceAll('&', '\\u0026'));
}

module.exports = {formatStatus, renderTemplate};
