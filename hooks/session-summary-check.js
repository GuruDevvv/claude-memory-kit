#!/usr/bin/env node
/**
 * Хук конца хода (Stop): напоминает сохранить итоги, если сессия была рабочей, а
 *   1) в журнал проектов сегодня ничего не записано, или
 *   2) HANDOFF.md текущего проекта не обновлялся больше двух часов.
 * Не блокирует и не заставляет: только строка-напоминание. Не чаще раза в день.
 * После «стоп» один раз держит конец хода, если нет раздела «Промпт» или в репозитории текущего
 * проекта остались правки без коммита и незапушенные ветки, или в другом репозитории лежат
 * незапушенные коммиты, которые сессия сделала сама.
 * На коротком разговоре молчит — длину сессии оценивает по размеру её записи.
 */
const fs = require('fs');
const os = require('os');
const path = require('path');
const cp = require('child_process');
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

/** Хвост записи сессии: сообщения человека и агента по порядку. Не читается — пусто. */
function readEntries(input) {
  if (!input.transcript_path) return [];
  let tail;
  try {
    const size = fs.statSync(input.transcript_path).size;
    const fd = fs.openSync(input.transcript_path, 'r');
    const buf = Buffer.alloc(Math.min(size, TAIL_BYTES));
    fs.readSync(fd, buf, 0, buf.length, size - buf.length);
    fs.closeSync(fd);
    tail = buf.toString('utf-8');
  } catch (e) {
    return [];
  }
  const entries = [];
  for (const line of tail.split('\n')) {
    try {
      const e = JSON.parse(line);
      if ((e.type === 'user' || e.type === 'assistant') && !e.isSidechain && !e.isMeta) entries.push(e);
    } catch (e) { /* обрезанная первая строка хвоста */ }
  }
  return entries;
}

/**
 * Номер последнего сообщения человека, если это слово конца сессии («стоп»), иначе -1.
 * Второй заход хука (stop_hook_active) не считается: держим один раз, петли не будет.
 */
function stopIndex(input, entries, cfg) {
  if (input.stop_hook_active) return -1;
  for (let i = entries.length - 1; i >= 0; i--) {
    if (entries[i].type !== 'user' || !textOf(entries[i])) continue;
    const said = textOf(entries[i]).toLowerCase().replace(/[.!…\s]+$/, '');
    return (cfg.stop_words || STOP_WORDS).includes(said) ? i : -1;
  }
  return -1;
}

function guardOn(cfg, key) {
  return !(cfg.guard === false || (cfg.guard && cfg.guard[key] === false));
}

const PROMPT_RE = /^#{1,3}\s*Промпт/m;
const FLUSH_WAIT_MS = 1500;

/** Есть ли «Промпт» в ответах агента после последнего сообщения человека с текстом. */
function promptAfterLastUser(entries) {
  for (let i = entries.length - 1; i >= 0; i--) {
    if (entries[i].type === 'user' && textOf(entries[i])) {
      return PROMPT_RE.test(entries.slice(i + 1).filter(e => e.type === 'assistant').map(textOf).join('\n'));
    }
  }
  return false;
}

/**
 * После «стоп» в ответе агента нет раздела «Промпт» — ритуал конца сессии не доведён.
 * Хук запускается раньше, чем последний ответ попадает в запись сессии (замер 26.10.2026:
 * «## Промпт» был в ответе, хук его не увидел, агент выводил промпт второй раз). Поэтому сперва
 * последний ответ из входа хука (`last_assistant_message`), затем запись, а не нашлось — запись
 * ещё раз после паузы.
 */
function promptMissing(input, entries) {
  if (typeof input.last_assistant_message === 'string' && PROMPT_RE.test(input.last_assistant_message)) return false;
  if (promptAfterLastUser(entries)) return false;
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, FLUSH_WAIT_MS);
  return !promptAfterLastUser(readEntries(input));
}

/** git без оболочки и без приглашений пароля; ошибка — исключение. */
function git(cwd, args) {
  return cp.execFileSync('git', args, {
    cwd, timeout: 4000, stdio: ['ignore', 'pipe', 'ignore'], encoding: 'utf-8', windowsHide: true,
    env: Object.assign({}, process.env, { GIT_TERMINAL_PROMPT: '0', GCM_INTERACTIVE: 'never' }),
  }).trim();
}

/**
 * Репозиторий текущего проекта (папка с HANDOFF.md вверх от рабочей) или пусто.
 * Только он: прочие репозитории, которые сессия задела по пути (настройки, память, соседние проекты),
 * часто несут чужие правки параллельных сессий, коммитить и пушить их по «стоп» нельзя.
 */
function projectRepo(cfg, cwd) {
  try {
    return path.resolve(git(kit.projectDir(cfg, cwd), ['rev-parse', '--show-toplevel']));
  } catch (e) {
    return '';
  }
}

const MAX_REPOS = 8; // больше чужих репозиториев за сессию не сверяем: хук держит конец хода

/** Путь из команды оболочки в обычный: /e/VibeCoding → E:/VibeCoding. */
function shellPath(p) {
  const m = /^\/([a-zA-Z])(\/.*)?$/.exec(p);
  return m ? m[1].toUpperCase() + ':' + (m[2] || '/') : p;
}

/**
 * Другие репозитории, где сессия сама делала коммиты: папки файлов, которые агент правил,
 * и папки из команд с `git commit` (`cd X`, `git -C X`). Сверяются только коммиты,
 * сделанные после начала сессии, и только на ветках с копией на сервере.
 * Незакоммиченное там не в счёт: это могут быть чужие правки параллельных сессий.
 */
function otherCommitRepos(entries, since, skip) {
  const dirs = new Set();
  for (const e of entries) {
    if (e.type !== 'assistant' || !Array.isArray(e.message && e.message.content)) continue;
    for (const c of e.message.content) {
      if (!c || c.type !== 'tool_use' || !c.input) continue;
      const f = c.input.file_path || c.input.notebook_path;
      if (typeof f === 'string' && path.isAbsolute(f)) dirs.add(path.dirname(f));
      const cmd = c.input.command;
      if (typeof cmd === 'string' && /git\b[^\n]*\bcommit\b/.test(cmd)) {
        for (const m of cmd.matchAll(/(?:\bcd|\bgit\s+-C)\s+("([^"]+)"|'([^']+)'|([^\s;&|]+))/g)) {
          dirs.add(shellPath(m[2] || m[3] || m[4]));
        }
      }
    }
  }
  const repos = new Map();
  for (const d of dirs) {
    if (repos.size >= MAX_REPOS) break;
    try {
      if (!path.isAbsolute(d) || !fs.existsSync(d)) continue;
      const top = path.resolve(git(d, ['rev-parse', '--show-toplevel']));
      if (top.toLowerCase() !== (skip || '').toLowerCase()) repos.set(top.toLowerCase(), top);
    } catch (e) { /* не репозиторий */ }
  }
  const out = [];
  for (const repo of repos.values()) {
    try {
      const refs = git(repo, ['for-each-ref', '--format=%(refname:short)|%(upstream:short)', 'refs/heads']);
      for (const line of refs.split('\n').filter(Boolean)) {
        const [branch, upstream] = line.split('|');
        if (!upstream) continue;
        const n = parseInt(git(repo, ['rev-list', '--count', '--since=' + since, upstream + '..' + branch]), 10) || 0;
        if (n > 0) out.push(repo.replace(/\\/g, '/') + ': ветка ' + branch + ', коммитов этой сессии не на сервере: ' + n);
      }
    } catch (e) { /* сверка не критична */ }
  }
  return out;
}

/** Начало сессии: время создания её записи, иначе первая метка в хвосте, иначе сутки назад. */
function sessionStart(input, entries) {
  try {
    const t = fs.statSync(input.transcript_path).birthtimeMs;
    if (t > 0) return new Date(t).toISOString();
  } catch (e) { /* нет записи */ }
  const first = entries.find(e => e.timestamp);
  return first ? first.timestamp : new Date(Date.now() - 86400000).toISOString();
}

/**
 * Что в репозитории не доведено до сервера: правки отслеживаемых файлов без коммита и ветки,
 * которые ушли вперёд или не запушены вовсе. Новые неотслеживаемые файлы не в счёт:
 * это часто мусор, решать про него человеку. Без удалённого репозитория — только незакоммиченное.
 */
function gitLeftovers(repo) {
  const out = [];
  try {
    const changed = git(repo, ['status', '--porcelain']).split('\n').filter(l => l && !l.startsWith('??'));
    if (changed.length) out.push(changed.length + ' файл(ов) с правками без коммита');
  } catch (e) { return out; }
  try {
    if (!git(repo, ['remote'])) return out;
    const refs = git(repo, ['for-each-ref', '--format=%(refname:short)|%(upstream:short)|%(upstream:track)', 'refs/heads']);
    for (const line of refs.split('\n').filter(Boolean)) {
      const [branch, upstream, track] = line.split('|');
      const ahead = /ahead (\d+)/.exec(track || '');
      if (upstream && ahead) out.push('ветка ' + branch + ' впереди сервера на ' + ahead[1]);
      else if (!upstream) {
        const n = parseInt(git(repo, ['rev-list', '--count', branch, '--not', '--remotes']), 10) || 0;
        if (n > 0) out.push('ветка ' + branch + ' не на сервере (' + n + ' коммит(ов))');
      }
    }
  } catch (e) { /* сверка веток не критична */ }
  return out;
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
  const entries = readEntries(input);
  const last = stopIndex(input, entries, cfg);
  if (last >= 0) {
    const session = String(input.session_id || '').slice(0, 8);
    const project = path.basename(kit.projectDir(cfg, process.cwd()));
    const reasons = [];
    if (guardOn(cfg, 'end_prompt') && promptMissing(input, entries)) {
      logEvent(cfg, { event: 'guard', key: 'end_prompt', blocked: true, session, project });
      reasons.push('Человек попросил закончить сессию, а в ответе нет раздела «## Промпт». Ритуал конца сессии ' +
        'не доведён: пройди его шаги из правил и выведи «## Промпт» с текстом для начала следующей сессии.');
    }
    if (guardOn(cfg, 'end_git')) {
      const repo = projectRepo(cfg, process.cwd());
      const left = repo ? gitLeftovers(repo) : [];
      if (left.length) {
        logEvent(cfg, { event: 'guard', key: 'end_git', blocked: true, session, project });
        reasons.push('В репозитории проекта ' + repo.replace(/\\/g, '/') + ' осталось недоведённое до сервера: ' +
          left.join('; ') + '.\nЗакоммить свои правки поимённо (не всё подряд) и запушь. Чужое, незнакомое ' +
          'или то, что пушить нельзя, не трогай, а назови в отчёте: что запушено и почему остальное нет. ' +
          'Другие репозитории по «стоп» не коммить и не пушь.');
      }
      const mine = otherCommitRepos(entries, sessionStart(input, entries), repo);
      if (mine.length) {
        logEvent(cfg, { event: 'guard', key: 'end_git', blocked: true, session, project, other: mine.length });
        reasons.push('В других репозиториях остались коммиты, сделанные за эту сессию, но не отправленные на сервер:\n- ' +
          mine.join('\n- ') + '\nЕсли коммиты твои — запушь (сервер ушёл вперёд — сначала слей его изменения). ' +
          'Незакоммиченное там не трогай. Чужие коммиты не пушь, назови в отчёте.');
      }
    }
    if (reasons.length) {
      console.log(JSON.stringify({ decision: 'block', reason: reasons.join('\n\n') }));
      process.exit(0);
    }
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
