#!/usr/bin/env node
/**
 * Хук конца хода (Stop): напоминает сохранить итоги, если сессия была рабочей, а
 *   1) в журнал проектов сегодня ничего не записано, или
 *   2) HANDOFF.md текущего проекта не обновлялся больше двух часов.
 * Не блокирует и не заставляет: только строка-напоминание. Не чаще раза в день.
 * После «стоп» один раз держит конец хода, если нет раздела «Промпт» или в репозитории текущего
 * проекта остались правки без коммита и незапушенные ветки, или в другом репозитории лежат
 * незапушенные коммиты, которые сессия сделала сама.
 * В любом ходе держит конец хода, если агент правил HANDOFF.md и тот длиннее 5 000 знаков.
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

/** Служебные вставки Codex идут с ролью user рядом со словами человека. */
const CODEX_SERVICE_RE = /^\s*(?:<[a-z_][\w-]*[\s>]|# AGENTS\.md instructions|>>> )/;

/**
 * Строка записи Codex в привычный вид записи Claude Code, остальное — null.
 * Команды и вызовы инструментов лежат строкой: для поиска `git commit` и `cd X` этого достаточно.
 */
function fromCodex(e) {
  const p = e && e.type === 'response_item' && e.payload;
  if (!p) return null;
  const base = { timestamp: e.timestamp };
  if (p.type === 'message' && (p.role === 'user' || p.role === 'assistant') && Array.isArray(p.content)) {
    const parts = p.content.map(c => (c && c.text) || '');
    const text = (p.role === 'user' ? parts.filter(t => !CODEX_SERVICE_RE.test(t)) : parts).join('\n').trim();
    if (!text) return null;
    return Object.assign(base, { type: p.role, message: { content: [{ type: 'text', text }] } });
  }
  if (p.type === 'function_call' || p.type === 'custom_tool_call') {
    const command = String(p.arguments || p.input || '');
    return Object.assign(base, { type: 'assistant', message: { content: [{ type: 'tool_use', name: p.name, input: { command } }] } });
  }
  return null;
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
      const raw = JSON.parse(line);
      const e = fromCodex(raw) || raw;
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
        // Codex: рабочая папка команды — отдельным полем
        for (const m of cmd.matchAll(/\bworkdir\\?["']?\s*:\s*\\?["']([^"'\n]+?)\\?["']/g)) {
          dirs.add(m[1].replace(/\\\\/g, '\\'));
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

const ARCH_SCRIPT = path.join(__dirname, '..', 'scripts', 'arch_drift.py');
const ARCH_MARK = '<!-- arch:begin -->';

/** Репозитории, которых касалась сессия: проект и папки правленых файлов и команд с git. */
function sessionRepos(cfg, entries) {
  const dirs = new Set([kit.projectDir(cfg, process.cwd())]);
  for (const e of entries) {
    if (e.type !== 'assistant' || !Array.isArray(e.message && e.message.content)) continue;
    for (const c of e.message.content) {
      if (!c || c.type !== 'tool_use' || !c.input) continue;
      const f = c.input.file_path || c.input.notebook_path;
      if (typeof f === 'string' && path.isAbsolute(f)) dirs.add(path.dirname(f));
      const cmd = c.input.command;
      if (typeof cmd === 'string' && /\bgit\b/.test(cmd)) {
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
      repos.set(top.toLowerCase(), top);
    } catch (e) { /* не репозиторий */ }
  }
  return [...repos.values()];
}

const ARCH_LABEL = { dir: { A: 'новая часть', D: 'исчезла часть' }, infra: 'изменён файл запуска', agents: '' };

/**
 * Карта устройства (стандарт _lab/architecture-standard) отстала от кода в этой сессии:
 * появилась или исчезла папка с кодом, тронут файл запуска, AGENTS.md разошёлся с CLAUDE.md.
 * Только репозитории с меткой карты в CLAUDE.md; один раз за сессию. Сбой проверки не молчит.
 * Возвращает { reason } для остановки, { notice } для строки-предупреждения или null.
 */
function archCheck(cfg, input, entries) {
  if (input.stop_hook_active || !guardOn(cfg, 'arch_map')) return null;
  const sid = String(input.session_id || '').replace(/[^\w-]/g, '').slice(0, 40);
  const flag = sid ? path.join(os.tmpdir(), 'memory-kit-arch-' + sid) : '';
  if (flag && fs.existsSync(flag)) return null;
  const repos = sessionRepos(cfg, entries).filter(r => {
    try { return fs.readFileSync(path.join(r, 'CLAUDE.md'), 'utf-8').includes(ARCH_MARK); } catch (e) { return false; }
  });
  if (!repos.length) return null;
  const since = sessionStart(input, entries);
  const pythons = [cfg.python, 'python', 'python3', 'py'].filter(Boolean);
  const lines = [], errors = [];
  for (const repo of repos) {
    let out = null, err = 'Python не найден';
    for (const py of pythons) {
      try {
        out = cp.execFileSync(py, ['-I', ARCH_SCRIPT, '--hook', '--since', since, repo], {
          timeout: 15000, stdio: ['ignore', 'pipe', 'pipe'], encoding: 'utf-8', windowsHide: true,
          env: Object.assign({}, process.env, { PYTHONIOENCODING: 'utf-8' }),
        });
        break;
      } catch (e) { if (e.code !== 'ENOENT') { err = String(e.message || e).split('\n')[0]; break; } }
    }
    const name = path.basename(repo);
    let res = null;
    try { res = JSON.parse(out); } catch (e) { res = null; }
    if (!res || typeof res !== 'object') { errors.push(name + ': ' + err); continue; }
    if (res.error) { errors.push(name + ': ' + res.error); continue; }
    for (const ev of res.events || []) {
      const label = ev.kind === 'dir' ? ARCH_LABEL.dir[ev.st] || 'изменена часть' : ARCH_LABEL[ev.kind];
      lines.push(name + ': ' + (label ? label + ' `' + ev.path + '`' : ev.path));
    }
  }
  if (!lines.length && !errors.length) return null;
  if (flag) { try { fs.writeFileSync(flag, '1'); } catch (e) { /* без отметки спросим ещё раз */ } }
  try { // отметки прошлых сессий старше недели
    for (const f of fs.readdirSync(os.tmpdir())) {
      if (!f.startsWith('memory-kit-arch-')) continue;
      const p = path.join(os.tmpdir(), f);
      if (Date.now() - fs.statSync(p).mtimeMs > 7 * 86400000) fs.unlinkSync(p);
    }
  } catch (e) { /* уборка не критична */ }
  const session = sid.slice(0, 8);
  if (!lines.length) {
    logEvent(cfg, { event: 'guard', key: 'arch_map', blocked: false, error: true, session });
    return { notice: 'Проверка карты устройства не сработала: ' + errors.join('; ') };
  }
  logEvent(cfg, { event: 'guard', key: 'arch_map', blocked: true, session, count: lines.length });
  return {
    reason: 'В этой сессии поменялось устройство проекта, а карта «Как устроено» не тронута:\n- ' +
      lines.join('\n- ') + '\nОбнови карту в CLAUDE.md (между метками arch:begin/arch:end) и docs/architecture.md, ' +
      'если он есть; если AGENTS.md написан отдельно — тот же блок туда дословно. Закоммить вместе с кодом. ' +
      'Если устройство на деле не поменялось (разовый скрипт, смена версии), обнови дату «сверено» в карте ' +
      'и скажи об этом одной строкой. Второй раз за сессию не остановлю.',
  };
}

const HANDOFF_MAX_CHARS = 5000; // столько записки показывает хук начала сессии (session-start-context.js)

/**
 * В этом ходе агент правил записку HANDOFF.md, а она длиннее того, что увидит следующая сессия.
 * Правку ищем по вызовам инструментов после последнего сообщения человека: Edit/Write с путём к
 * HANDOFF.md — этот файл; команда оболочки или правка Codex со словом HANDOFF.md — записка проекта.
 * Возвращает текст причины для остановки или null.
 */
function handoffSizeCheck(cfg, input, entries) {
  if (input.stop_hook_active || !guardOn(cfg, 'handoff_size')) return null;
  let start = -1;
  for (let i = entries.length - 1; i >= 0; i--) {
    if (entries[i].type === 'user' && textOf(entries[i])) { start = i; break; }
  }
  const files = new Set();
  const projectHandoff = path.join(kit.projectDir(cfg, process.cwd()), 'HANDOFF.md');
  for (const e of entries.slice(start + 1)) {
    const c = e.type === 'assistant' && e.message && Array.isArray(e.message.content) ? e.message.content : [];
    for (const part of c) {
      if (!part || part.type !== 'tool_use' || !part.input) continue;
      const fp = part.input.file_path || part.input.notebook_path;
      if (fp && path.basename(String(fp)) === 'HANDOFF.md') files.add(path.resolve(String(fp)));
      else if (!fp && /HANDOFF\.md/.test(String(part.input.command || ''))) files.add(projectHandoff);
    }
  }
  const max = Number(cfg.handoff_max) || HANDOFF_MAX_CHARS;
  const over = [];
  for (const f of files) {
    let len = 0;
    try { len = fs.readFileSync(f, 'utf-8').length; } catch (e) { continue; }
    if (len > max) over.push(f.replace(/\\/g, '/') + ' — ' + len + ' знаков');
  }
  if (!over.length) return null;
  logEvent(cfg, { event: 'guard', key: 'handoff_size', blocked: true, session: String(input.session_id || '').slice(0, 8) });
  return 'Записка длиннее ' + max + ' знаков, следующая сессия увидит только начало:\n- ' + over.join('\n- ') +
    '\nОставь в записке только открытое: закрытые задачи убери, сверив, что они есть в журнале проекта ' +
    '(нет — допиши туда); подробности (хэши, суммы, кому что отправлено) — в журнал или документ задачи. ' +
    '«Следующий шаг» и «Промпт» держи наверху.';
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
  let arch = null;
  try { arch = archCheck(cfg, input, entries); } catch (e) { arch = null; }
  let handoff = null;
  try { handoff = handoffSizeCheck(cfg, input, entries); } catch (e) { handoff = null; }
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
    if (arch && arch.reason) reasons.push(arch.reason);
    if (handoff) reasons.push(handoff);
    if (reasons.length) {
      console.log(JSON.stringify({ decision: 'block', reason: reasons.join('\n\n') }));
      process.exit(0);
    }
  }
  if ((arch && arch.reason) || handoff) {
    const why = [arch && arch.reason, handoff].filter(Boolean).join('\n\n');
    console.log(JSON.stringify({ decision: 'block', reason: why }));
    process.exit(0);
  }
  if (arch && arch.notice) {
    console.log(JSON.stringify({ systemMessage: arch.notice }));
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
  const journalFile = kit.journalFile(cfg, projectPath);
  if (kit.journalOverride(cfg, projectPath) && kit.isProject(cfg, projectPath)) {
    try { journalToday = fs.statSync(journalFile).mtimeMs >= todayStart; } catch (e) { journalToday = false; }
  } else if (fs.existsSync(journals)) {
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
    reasons.push('в журнал сегодня ничего не записано (' + journalFile + ')');
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
