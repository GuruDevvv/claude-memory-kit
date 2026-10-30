#!/usr/bin/env node
/**
 * Хук начала сессии (SessionStart). Независимые части, любая может отсутствовать:
 *   1) Автосинк с удалённым репозиторием — до первого сообщения.
 *      Безопасный режим: файлы обновляются, только если в папке нет несохранённых
 *      правок и изменения ложатся поверх без слияния. Иначе — только показывает, что пришло.
 *   2) HANDOFF.md проекта + последние записи его журнала из центральной памяти.
 *      Проект ищется вверх от рабочей папки: запуск из подпапки записку не теряет.
 *      Журнал приходит и без записки, если папка лежит под корнем проектов.
 *   3) Оглавление общей памяти (MEMORY.md) — всем; оглавление памяти проекта — тем,
 *      кто не подгружает его сам (Codex; Claude Code свою память проекта читает без хука).
 *   4) Режим precompact (`node session-start-context.js precompact`, событие PreCompact): перед сжатием
 *      разговора кладёт копию HANDOFF.md в <папка агента>/handoff-snapshots/<проект>/ и метку. Сказать агенту
 *      PreCompact ничего не может — Claude Code его вывод модели не показывает (документация хуков,
 *      сверено 14.10.2026). Поэтому напоминание приходит после сжатия: SessionStart видит свежую метку
 *      и пишет, где снимок и что дописать. Идея пользователя набора, 14.10.2026.
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

const SNAPSHOTS_KEEP = 10;               // снимков на проект; старые удаляются
const COMPACT_MARK_MAX_MS = 15 * 60 * 1000;  // метка старше — это уже не то сжатие

function snapshotDir(cfg, projectName) {
  // рядом с хуками, а не в памяти: память часто лежит под git, снимки записок туда не нужны
  return path.join(__dirname, '..', 'handoff-snapshots', projectName);
}

/** PreCompact: копия записки до сжатия и метка для SessionStart. Сжатие не останавливает никогда. */
function precompact(cfg, project) {
  const name = path.basename(project);
  const handoff = path.join(project, 'HANDOFF.md');
  const dir = snapshotDir(cfg, name);
  fs.mkdirSync(dir, { recursive: true });
  let file = '';
  if (fs.existsSync(handoff)) {
    const d = new Date();
    const pad = n => String(n).padStart(2, '0');
    const stamp = d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) + '-' +
      pad(d.getHours()) + pad(d.getMinutes()) + pad(d.getSeconds());
    file = path.join(dir, 'HANDOFF-' + stamp + '.md');
    fs.copyFileSync(handoff, file);
    const old = fs.readdirSync(dir).filter(f => /^HANDOFF-.*\.md$/.test(f)).sort();
    for (const f of old.slice(0, Math.max(0, old.length - SNAPSHOTS_KEEP))) fs.unlinkSync(path.join(dir, f));
  }
  fs.writeFileSync(path.join(dir, '.compact-mark.json'), JSON.stringify({ time: Date.now(), file }));
}

/** SessionStart сразу после сжатия: строка-напоминание или пусто. Метку снимаем — второй раз не скажем. */
function afterCompact(cfg, projectName) {
  const mark = path.join(snapshotDir(cfg, projectName), '.compact-mark.json');
  if (!fs.existsSync(mark)) return '';
  const m = JSON.parse(fs.readFileSync(mark, 'utf-8'));
  fs.unlinkSync(mark);
  if (Date.now() - m.time > COMPACT_MARK_MAX_MS) return '';
  return '### Разговор только что сжат\n\nЧасть деталей ушла в сжатие. ' +
    (m.file ? 'Записка до сжатия сохранена: ' + m.file.replace(/\\/g, '/') + '. ' : '') +
    'Если в сжатой части были решения, которых нет в HANDOFF.md, допиши их сейчас, пока помнишь.\n';
}

// Записи журнала с самой свежей первой: журнал, который дописывают вниз, переворачиваем по датам заголовков.
function newestFirst(entries) {
  const date = e => (e.match(/^\s*(\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2})?)/) || [])[1] || '';
  const a = date(entries[0]);
  const b = date(entries[entries.length - 1]);
  return a && b && a < b ? entries.slice().reverse() : entries;
}

function clip(text, max, note) {
  return text.length > max ? text.slice(0, max) + '\n\n[...' + note + ']' : text;
}

// Раздел оглавления по заголовку второго уровня, вместе с заголовком; нет такого — пусто.
function section(text, title) {
  const m = text.replace(/\r\n/g, '\n').match(new RegExp('^## ' + title + '[^\\n]*\\n[\\s\\S]*?(?=^## |(?![\\s\\S]))', 'm'));
  return m ? m[0].trim() : '';
}

try {
  const cfg = kit.loadConfig();
  if (!cfg) process.exit(0);

  // Тесный режим (ключ start_max_chars): Codex пропускает к модели около 6,5 тыс. знаков вывода хука,
  // середину вырезает (замер 14.10.2026: из 30 тыс. знаков дошло 6,6 тыс., раздел «Всегда» пропал).
  // Тогда отдаём главное: записку, одну запись журнала, раздел «Всегда» и адрес полного оглавления.
  const tight = parseInt(cfg.start_max_chars, 10) || 0;
  const HANDOFF_MAX = tight ? Math.round(tight * 0.38) : HANDOFF_MAX_CHARS;
  const JOURNAL_MAX = tight ? Math.round(tight * 0.16) : JOURNAL_MAX_CHARS;
  const ALWAYS_MAX = Math.round(tight * 0.32);

  const cwd = process.cwd();
  const project = kit.projectDir(cfg, cwd);
  const projectName = path.basename(project);
  if (process.argv[2] === 'precompact') {
    if (kit.isProject(cfg, project)) precompact(cfg, project);
    process.exit(0);
  }
  const parts = [];
  try {
    const note = afterCompact(cfg, projectName);
    if (note) parts.push(note);
  } catch (e) { /* метка битая — молчим */ }

  // --- 1. Автосинк (любая папка с репозиторием) ---
  if (cfg.sync_on_start !== false) {
    try {
      const sync = syncWithRemote(cwd);
      if (sync) parts.push(sync);
    } catch (e) { /* не блокируем старт */ }
  }

  // --- 2. HANDOFF.md + журнал проекта ---
  const handoffPath = path.join(project, 'HANDOFF.md');
  if (fs.existsSync(handoffPath)) {
    const handoff = fs.readFileSync(handoffPath, 'utf-8').trim();
    parts.push('### HANDOFF.md' + (kit.samePath(project, cwd) ? '' : ' (' + handoffPath + ')') + '\n\n' +
      clip(handoff, HANDOFF_MAX, 'HANDOFF обрезан: показано ' + HANDOFF_MAX + ' из ' + handoff.length +
        ' знаков. Прочитай HANDOFF.md целиком до начала работы') + '\n');
  }
  if (kit.isProject(cfg, project)) {
    const journalPath = kit.journalFile(cfg, project);
    const journalName = cfg.journal_path ? journalPath.replace(/\\/g, '/') : 'projects/' + projectName + '.md';
    const journalNote = cfg.journal_path ? ' (' + journalName + ')' : '';
    if (fs.existsSync(journalPath)) {
      const journal = fs.readFileSync(journalPath, 'utf-8');
      const first = journal.search(/^## \[/m); // вступление файла пропускаем
      if (first >= 0) {
        const entries = newestFirst(journal.slice(first).split(/^## \[/m).filter(e => e.trim().length > 0));
        const take = tight ? 1 : (cfg.journal_entries || 3);
        const recent = entries.slice(0, take).map(e => '## [' + e.trim()).join('\n\n');
        if (recent) {
          parts.push('### Последние записи журнала (' + Math.min(take, entries.length) + ' из ' + entries.length + ')' + journalNote + '\n\n' +
            clip(recent, JOURNAL_MAX, 'журнал обрезан — открой ' + journalName) + '\n');
        }
      } else if (cfg.journal_path && journal.trim()) {
        // свой журнал без заголовков «## [дата]»: границ записей не видно, отдаём конец файла
        parts.push('### Конец журнала' + journalNote + '\n\n' + journal.trim().slice(-JOURNAL_MAX) + '\n');
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
      if (text && tight) {
        const always = seen.length === 1 ? section(text, 'Всегда') : '';
        parts.push('### ' + title + '\n\n' + (always ? clip(always, ALWAYS_MAX, 'раздел обрезан') + '\n\n' : '') +
          'Оглавление целиком сюда не поместилось (' + text.length + ' знаков). Прочитай его до начала работы: ' +
          file.replace(/\\/g, '/') + '\n');
      } else if (text) {
        parts.push('### ' + title + ' (' + dir + ')\n\n' +
          clip(text, INDEX_MAX_CHARS, 'оглавление обрезано — открой MEMORY.md и INDEX.md') + '\n');
      }
    };
    add('Память: общее оглавление', cfg.central);
    if (cfg.inject_project_index) add('Память: оглавление этого проекта', kit.projectMemoryDir(cfg, project));
  }

  // --- 4. Установка разошлась с набором: говорим агенту сами, человек проверку запускать не будет ---
  try {
    const drift = kit.driftFromKit(cfg);
    if (drift.behind.length || drift.edited.length) {
      const lines = [];
      if (drift.behind.length) lines.push('набор обновился, здесь старые файлы: ' + drift.behind.join(', '));
      if (drift.edited.length) lines.push('файлы поправлены на месте мимо набора: ' + drift.edited.join(', '));
      parts.push('### memory-kit: установка разошлась с набором\n\n' + lines.join('\n') + '\n\n' +
        'Скажи об этом человеку одной строкой в первом ответе и предложи обновить. Порядок — в ' +
        path.join(cfg.kit_dir, 'INSTALL.md') + ', раздел «Обновление». Без его «да» файлы не меняй.\n');
    }
  } catch (e) { /* сверка не критична */ }

  // Метку пишем всегда: по её отсутствию агент понимает, что хуки не запускаются
  // (в Codex — не подтверждены через /hooks), и сам говорит об этом человеку.
  let out = kit.HOOKS_ALIVE + '\n\n' + (parts.length === 0 ? '' :
    '## Контекст проекта ' + projectName + ' (подгружено хуком начала сессии)\n\n' + parts.join('\n'));
  if (tight) out = clip(out, tight, 'дальше не поместилось');
  console.log(JSON.stringify({ hookSpecificOutput: { hookEventName: 'SessionStart', additionalContext: out } }));
} catch (e) {
  process.exit(0); // хук не мешает началу сессии
}
