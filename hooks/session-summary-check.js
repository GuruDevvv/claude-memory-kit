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

const STOP_WORDS = ['стоп', 'всё', 'все', 'завершай', 'пока', 'stop'];
const TAIL_BYTES = 600000;

/** Текст сообщения: строка либо склейка текстовых частей; служебные вставки вырезаны. */
function textOf(entry) {
  const c = entry && entry.message && entry.message.content;
  const raw = typeof c === 'string' ? c
    : Array.isArray(c) ? c.filter(x => x && x.type === 'text').map(x => x.text || '').join('\n') : '';
  return raw.replace(/<system-reminder>[\s\S]*?<\/system-reminder>/g, '').trim();
}

/**
 * Человек сказал «стоп», а в ответе агента нет раздела «Промпт» — ритуал конца сессии не доведён.
 * Смотрим хвост записи сессии: последнее сообщение человека и всё, что агент написал после него.
 * Запись не читается или устроена иначе (другой агент) — молчим.
 */
function endRitualMissing(input, cfg) {
  if (cfg.guard === false || (cfg.guard && cfg.guard.end_prompt === false)) return false;
  if (input.stop_hook_active || !input.transcript_path) return false;   // второй раз не держим: петли не будет
  let tail;
  try {
    const size = fs.statSync(input.transcript_path).size;
    const fd = fs.openSync(input.transcript_path, 'r');
    const buf = Buffer.alloc(Math.min(size, TAIL_BYTES));
    fs.readSync(fd, buf, 0, buf.length, size - buf.length);
    fs.closeSync(fd);
    tail = buf.toString('utf-8');
  } catch (e) {
    return false;
  }
  const entries = [];
  for (const line of tail.split('\n')) {
    try {
      const e = JSON.parse(line);
      if ((e.type === 'user' || e.type === 'assistant') && !e.isSidechain && !e.isMeta) entries.push(e);
    } catch (e) { /* обрезанная первая строка хвоста */ }
  }
  let last = -1;
  for (let i = entries.length - 1; i >= 0; i--) {
    if (entries[i].type === 'user' && textOf(entries[i])) { last = i; break; }
  }
  if (last < 0) return false;
  const said = textOf(entries[last]).toLowerCase().replace(/[.!…\s]+$/, '');
  if (!(cfg.stop_words || STOP_WORDS).includes(said)) return false;
  const answer = entries.slice(last + 1).filter(e => e.type === 'assistant').map(textOf).join('\n');
  return !/^#{1,3}\s*Промпт/m.test(answer);
}

function logEvent(cfg, row) {
  try {
    if (cfg.metrics === false || !cfg.central) return;
    const dir = path.join(cfg.central, '.metrics');
    fs.mkdirSync(dir, { recursive: true });
    const ts = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 19);
    fs.appendFileSync(path.join(dir, 'events.jsonl'), JSON.stringify(Object.assign({ ts, tool: cfg.tool }, row)) + '\n');
  } catch (e) { /* журнал не критичен */ }
}

try {
  const cfg = kit.loadConfig();
  if (!cfg) process.exit(0);

  const input = readStdin();
  if (endRitualMissing(input, cfg)) {
    logEvent(cfg, { event: 'guard', key: 'end_prompt', blocked: true,
      session: String(input.session_id || '').slice(0, 8), project: path.basename(kit.projectDir(cfg, process.cwd())) });
    console.log(JSON.stringify({
      decision: 'block',
      reason: 'Человек попросил закончить сессию, а в ответе нет раздела «## Промпт». Ритуал конца сессии ' +
        'не доведён: пройди его шаги из правил и выведи «## Промпт» с текстом для начала следующей сессии.',
    }));
    process.exit(0);
  }

  const today = new Date().toISOString().slice(0, 10);
  const flag = path.join(os.tmpdir(), 'memory-kit-reminded-' + (cfg.tool || 'agent') + '-' + today);
  if (fs.existsSync(flag)) process.exit(0);

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
