#!/usr/bin/env node
/**
 * Хук начала сессии (SessionStart). Независимые части, любая может отсутствовать:
 *   1) Автосинк с удалённым репозиторием — до первого сообщения.
 *      Безопасный режим: файлы обновляются, только если в папке нет несохранённых
 *      правок и изменения ложатся поверх без слияния. Иначе — только показывает, что пришло.
 *   2) HANDOFF.md проекта + последние записи его журнала из центральной памяти.
 *   3) Оглавление общей памяти (MEMORY.md) — всем; оглавление памяти проекта — тем,
 *      кто не подгружает его сам (Codex; Claude Code свою память проекта читает без хука).
 * Любая ошибка (нет сети, нет репозитория, нет HANDOFF) молча пропускается:
 * начало сессии не блокируется никогда.
 */
const fs = require('fs');
const path = require('path');
const cp = require('child_process');
const kit = require('./kit.js');

// Раздельные бюджеты: большой HANDOFF не выдавливает журнал из контекста.
const HANDOFF_MAX_CHARS = 5000;
const JOURNAL_MAX_CHARS = 3500;
const INDEX_MAX_CHARS = 24000;

// Хук задерживает начало сессии, поэтому сеть ждём скупо.
const FETCH_TIMEOUT_MS = 10000;
const GIT_TIMEOUT_MS = 5000;
const SYNC_LOG_MAX_LINES = 20;
const SYNC_LOG_MAX_CHARS = 1500;

/**
 * Запуск git без оболочки: на Windows cmd.exe не съедает %-подстановки и кавычки.
 * Окружение глушит запросы пароля: иначе fetch повиснет на приглашении,
 * которого никто не увидит, и съест весь таймаут.
 */
function git(cwd, args, timeout) {
  return cp.execFileSync('git', args, {
    cwd,
    timeout: timeout || GIT_TIMEOUT_MS,
    stdio: ['ignore', 'pipe', 'ignore'],
    encoding: 'utf-8',
    windowsHide: true,
    env: Object.assign({}, process.env, {
      GIT_TERMINAL_PROMPT: '0',
      GCM_INTERACTIVE: 'never',
      GIT_ASKPASS: 'echo',
      GIT_SSH_COMMAND: 'ssh -o BatchMode=yes -o ConnectTimeout=8',
    }),
  }).trim();
}

function syncWithRemote(cwd) {
  // Не репозиторий, или ветка не связана с сервером — тянуть нечего
  let upstream;
  try {
    git(cwd, ['rev-parse', '--is-inside-work-tree']);
    upstream = git(cwd, ['rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{upstream}']);
  } catch (e) {
    return '';
  }
  if (!upstream || upstream.indexOf('/') < 0) return '';
  const remote = upstream.slice(0, upstream.indexOf('/'));

  try {
    git(cwd, ['fetch', remote, '--quiet'], FETCH_TIMEOUT_MS);
  } catch (e) {
    return ''; // нет сети или доступа — молча
  }

  let ahead = 0;
  let behind = 0;
  try {
    const pair = git(cwd, ['rev-list', '--left-right', '--count', 'HEAD...' + upstream]).split(/\s+/);
    ahead = parseInt(pair[0], 10) || 0;
    behind = parseInt(pair[1], 10) || 0;
  } catch (e) {
    return '';
  }
  if (behind === 0) return ''; // нового нет — молчим

  let log = '';
  try {
    log = git(cwd, ['log', 'HEAD..' + upstream, '--pretty=%ad  %an: %s', '--date=format:%m-%d %H:%M']);
  } catch (e) { /* список не критичен */ }
  let lines = log ? log.split('\n') : [];
  const total = lines.length;
  if (lines.length > SYNC_LOG_MAX_LINES) lines = lines.slice(0, SYNC_LOG_MAX_LINES);
  let block = lines.join('\n');
  if (block.length > SYNC_LOG_MAX_CHARS) block = block.slice(0, SYNC_LOG_MAX_CHARS);
  if (total > lines.length) block += '\n[...ещё ' + (total - lines.length) + ', см. git log HEAD..' + upstream + ']';

  // Обновлять файлы можно, только если дерево чистое и мы не разошлись с сервером
  let dirty = true;
  try {
    dirty = git(cwd, ['status', '--porcelain']).length > 0;
  } catch (e) { /* не смогли проверить — считаем, что правки есть */ }

  if (!dirty && ahead === 0) {
    try {
      git(cwd, ['merge', '--ff-only', upstream]);
      return '### Автосинк: подтянуто ' + behind + ' изменений с ' + upstream + '\n\n' +
        'Файлы на диске УЖЕ обновлены (несохранённых правок не было, слияния не потребовалось).\n' +
        'Спрашивать разрешения не нужно, это сделано автоматически при старте.\n\n' + block + '\n';
    } catch (e) { /* не сложилось — перейдём в режим «только показать» */ }
  }

  const reason = dirty
    ? 'в папке есть несохранённые правки'
    : 'локальная ветка ушла вперёд на ' + ahead + ', нужно слияние';
  return '### Автосинк: есть ' + behind + ' новых изменений на ' + upstream + ', файлы НЕ тронуты\n\n' +
    'Не обновил автоматически, потому что ' + reason + '. История скачана, файлы как были.\n' +
    'Если владельцу это важно — предложить обновиться, но не делать молча.\n\n' + block + '\n';
}

function clip(text, max, note) {
  return text.length > max ? text.slice(0, max) + '\n\n[...' + note + ']' : text;
}

try {
  const cfg = kit.loadConfig();
  if (!cfg) process.exit(0);

  const cwd = process.cwd();
  const projectName = path.basename(cwd);
  const parts = [];

  // --- 1. Автосинк (любая папка с репозиторием) ---
  if (cfg.sync_on_start !== false) {
    try {
      const sync = syncWithRemote(cwd);
      if (sync) parts.push(sync);
    } catch (e) { /* не блокируем старт */ }
  }

  // --- 2. HANDOFF.md + журнал проекта ---
  const handoffPath = path.join(cwd, 'HANDOFF.md');
  if (fs.existsSync(handoffPath)) {
    const handoff = fs.readFileSync(handoffPath, 'utf-8').trim();
    parts.push('### HANDOFF.md\n\n' +
      clip(handoff, HANDOFF_MAX_CHARS, 'HANDOFF обрезан — открой HANDOFF.md полностью') + '\n');

    const journalPath = path.join(cfg.central, 'projects', projectName + '.md');
    if (fs.existsSync(journalPath)) {
      const journal = fs.readFileSync(journalPath, 'utf-8');
      const first = journal.search(/^## \[/m); // вступление файла пропускаем
      if (first >= 0) {
        const entries = journal.slice(first).split(/^## \[/m).filter(e => e.trim().length > 0);
        const take = cfg.journal_entries || 3;
        const recent = entries.slice(0, take).map(e => '## [' + e.trim()).join('\n\n');
        if (recent) {
          parts.push('### Последние записи журнала (' + Math.min(take, entries.length) + ' из ' + entries.length + ')\n\n' +
            clip(recent, JOURNAL_MAX_CHARS, 'журнал обрезан — открой projects/' + projectName + '.md') + '\n');
        }
      }
    }
  }

  // --- 3. Оглавления памяти ---
  if (cfg.inject_index !== false) {
    const seen = [];
    const add = (title, dir) => {
      const file = path.join(dir, 'MEMORY.md');
      if (seen.some(s => kit.samePath(s, file)) || !fs.existsSync(file)) return;
      seen.push(file);
      const text = fs.readFileSync(file, 'utf-8').trim();
      if (text) {
        parts.push('### ' + title + ' (' + dir + ')\n\n' +
          clip(text, INDEX_MAX_CHARS, 'оглавление обрезано — открой MEMORY.md и INDEX.md') + '\n');
      }
    };
    add('Память: общее оглавление', cfg.central);
    if (cfg.inject_project_index) add('Память: оглавление этого проекта', kit.projectMemoryDir(cfg, cwd));
  }

  if (parts.length === 0) process.exit(0); // нечего сказать — молчим

  console.log(JSON.stringify({
    hookSpecificOutput: {
      hookEventName: 'SessionStart',
      additionalContext: '## Контекст проекта ' + projectName + ' (подгружено хуком начала сессии)\n\n' + parts.join('\n'),
    },
  }));
} catch (e) {
  process.exit(0); // хук не мешает началу сессии
}
