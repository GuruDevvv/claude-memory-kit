#!/usr/bin/env node
/**
 * Хук конца хода (Stop): напоминает сохранить итоги, если сессия была рабочей, а
 *   1) в журнал проектов сегодня ничего не записано, или
 *   2) HANDOFF.md текущего проекта не обновлялся больше двух часов.
 * Не блокирует и не заставляет: только строка-напоминание. Не чаще раза в день.
 * На коротком разговоре молчит — длину сессии оценивает по размеру её записи.
 */
const fs = require('fs');
const os = require('os');
const path = require('path');
const kit = require('./kit.js');

const HANDOFF_STALE_HOURS = 2;
const MIN_TRANSCRIPT_BYTES = 150000; // короче — это разговор, а не работа

function readStdin() {
  try {
    return JSON.parse(fs.readFileSync(0, 'utf-8') || '{}');
  } catch (e) {
    return {};
  }
}

try {
  const cfg = kit.loadConfig();
  if (!cfg) process.exit(0);

  const today = new Date().toISOString().slice(0, 10);
  const flag = path.join(os.tmpdir(), 'memory-kit-reminded-' + (cfg.tool || 'agent') + '-' + today);
  if (fs.existsSync(flag)) process.exit(0);

  const input = readStdin();
  if (input.transcript_path) {
    try {
      if (fs.statSync(input.transcript_path).size < MIN_TRANSCRIPT_BYTES) process.exit(0);
    } catch (e) { /* записи нет — проверяем как обычно */ }
  }

  const todayStart = new Date(today).getTime();
  const journals = path.join(cfg.central, 'projects');
  const projectPath = kit.projectDir(cfg, process.cwd());
  const project = path.basename(projectPath);
  const fresh = f => {
    try { return fs.statSync(path.join(journals, f)).mtimeMs >= todayStart; } catch (e) { return false; }
  };
  let journalToday = true; // папки нет — не ворчим
  if (fs.existsSync(journals)) {
    // В проекте смотрим его журнал: запись в чужой не значит, что этот сохранён.
    // Вне проекта (разовая папка) хватает записи в любом.
    journalToday = kit.isProject(cfg, projectPath)
      ? fresh(project + '.md')
      : fs.readdirSync(journals).filter(f => f.endsWith('.md')).some(fresh);
  }

  let handoffAge = 0;
  try {
    handoffAge = (Date.now() - fs.statSync(path.join(projectPath, 'HANDOFF.md')).mtimeMs) / 3600000;
  } catch (e) { /* у проекта HANDOFF нет */ }

  const reasons = [];
  if (!journalToday) {
    reasons.push('в журнал сегодня ничего не записано (' + path.join(journals, project + '.md') + ')');
  }
  if (handoffAge > HANDOFF_STALE_HOURS) {
    reasons.push('HANDOFF.md проекта ' + project + ' не обновлялся ' + handoffAge.toFixed(1) + ' ч');
  }
  if (reasons.length === 0) process.exit(0);

  fs.writeFileSync(flag, '1');
  console.log(JSON.stringify({
    systemMessage: 'Память: ' + reasons.join('; ') + '. Скажи «стоп» — агент сохранит итоги сессии.',
  }));
} catch (e) {
  process.exit(0); // хук не мешает завершению
}
