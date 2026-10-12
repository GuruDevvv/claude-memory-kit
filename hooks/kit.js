/**
 * Общие функции хуков memory-kit: чтение настроек и адреса папок памяти.
 * Настройки лежат рядом с папкой hooks: <папка агента>/memory-kit.json
 * (их пишет install.py). Нет файла настроек — хуки молча ничего не делают.
 */
const fs = require('fs');
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

module.exports = { loadConfig, slug, projectMemoryDir, samePath, inside, projectDir, isProject, HOOKS_ALIVE };
