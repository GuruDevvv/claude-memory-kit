/**
 * Общие функции хуков memory-kit: чтение настроек и адреса папок памяти.
 * Настройки лежат рядом с папкой hooks: <папка агента>/memory-kit.json
 * (их пишет install.py). Нет файла настроек — хуки молча ничего не делают.
 */
const fs = require('fs');
const os = require('os');
const path = require('path');

function loadConfig() {
  try {
    const p = path.join(__dirname, '..', 'memory-kit.json');
    return JSON.parse(fs.readFileSync(p, 'utf-8'));
  } catch (e) {
    return null;
  }
}

/** Имя папки памяти для пути — то же правило, что у Claude Code: всё, кроме букв и цифр, в дефис. */
function slug(dir) {
  return path.resolve(dir).replace(/[^A-Za-z0-9]/g, '-');
}

/** Папка памяти проекта для рабочей папки cwd. Регистр буквы диска на Windows гуляет — ищем без учёта регистра. */
function projectMemoryDir(cfg, cwd) {
  const want = slug(cwd).toLowerCase();
  try {
    for (const name of fs.readdirSync(cfg.projects_dir)) {
      if (name.toLowerCase() === want) return path.join(cfg.projects_dir, name, 'memory');
    }
  } catch (e) { /* папки ещё нет */ }
  return path.join(cfg.projects_dir, slug(cwd), 'memory');
}

/**
 * Файл журнала проекта. По умолчанию <central>/projects/<Проект>.md. Ключ journal_path задаёт свой адрес:
 * {project} заменяется именем проекта, относительный путь считается от папки проекта.
 */
function journalFile(cfg, project) {
  const name = path.basename(project);
  if (!cfg.journal_path) return path.join(cfg.central, 'projects', name + '.md');
  const p = String(cfg.journal_path).split('{project}').join(name).replace(/^~(?=[\\/]|$)/, os.homedir());
  return path.resolve(project, p);
}

function samePath(a, b) {
  return path.resolve(a).toLowerCase() === path.resolve(b).toLowerCase();
}

/** Путь b лежит внутри a (или совпадает). */
function inside(a, b) {
  const rel = path.relative(path.resolve(a), path.resolve(b));
  return rel === '' || (!rel.startsWith('..') && !path.isAbsolute(rel));
}

/**
 * Папка проекта для рабочей папки cwd. Агента часто запускают из подпапки (src, docs):
 * поднимаемся до ближайшей папки с HANDOFF.md. Выше корня проектов не идём, а сам корень
 * проектом считаем, только если запущены прямо в нём: иначе его записка подменила бы записку проекта.
 * Ничего не нашли — проектом остаётся сама cwd.
 */
function projectDir(cfg, cwd) {
  const start = path.resolve(cwd);
  const root = cfg && cfg.root ? path.resolve(cfg.root) : null;
  let dir = start;
  for (let i = 0; i < 12; i++) {
    if (root && samePath(dir, root) && !samePath(dir, start)) break;
    if (fs.existsSync(path.join(dir, 'HANDOFF.md'))) return dir;
    const up = path.dirname(dir);
    if (up === dir) break;
    dir = up;
  }
  return start;
}

/** Папка похожа на проект: в ней есть записка либо она лежит под корнем проектов. */
function isProject(cfg, dir) {
  if (fs.existsSync(path.join(dir, 'HANDOFF.md'))) return true;
  return !!(cfg && cfg.root) && inside(cfg.root, dir) && !samePath(cfg.root, dir);
}

// Метка «хук старта сработал». Ту же строку ищет агент по разделу правил (install.py, HOOKCHECK).
const HOOKS_ALIVE = 'memory-kit: хуки работают';

// Файлы набора в папке агента. Тот же список — в install.py и scripts/kit_check.py.
const KIT_FILES = {
  hooks: ['kit.js', 'session-start-context.js', 'session-summary-check.js', 'memory-index-hook.py',
    'memory-recall-hook.py'],
  scripts: ['memory_index.py', 'memory_search.py', 'session_archive.py', 'project_index.py', 'kit_check.py',
    'memory_report.py', 'memory_upkeep.py',
    'find-zombies.ps1'],
};

/**
 * Чем установленные файлы отличаются от папки набора (ключ kit_dir в настройках).
 * Возвращает { behind: [...], edited: [...] }: набор ушёл вперёд либо файл поправили на месте.
 * Различаем по времени правки; перевод строк не в счёт. Нет kit_dir или папки — пусто.
 */
function driftFromKit(cfg) {
  const out = { behind: [], edited: [] };
  if (!cfg || !cfg.kit_dir) return out;
  const text = p => fs.readFileSync(p, 'utf-8').split('\r\n').join('\n');
  for (const sub of Object.keys(KIT_FILES)) {
    for (const name of KIT_FILES[sub]) {
      const mine = path.join(__dirname, '..', sub, name);
      const theirs = path.join(cfg.kit_dir, sub, name);
      try {
        if (!fs.existsSync(theirs)) continue;
        if (!fs.existsSync(mine)) { out.behind.push(sub + '/' + name); continue; }
        if (text(mine) === text(theirs)) continue;
        (fs.statSync(theirs).mtimeMs >= fs.statSync(mine).mtimeMs ? out.behind : out.edited).push(sub + '/' + name);
      } catch (e) { /* не смогли прочитать — молчим */ }
    }
  }
  return out;
}

module.exports = { journalFile, loadConfig, slug, projectMemoryDir, samePath, inside, projectDir, isProject, driftFromKit,
  KIT_FILES, HOOKS_ALIVE };
