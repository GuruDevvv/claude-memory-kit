# -*- coding: utf-8 -*-
"""Отставание описания устройства проекта от кода.

Описание по стандарту: карта в CLAUDE.md между метками <!-- arch:begin --> и <!-- arch:end -->
и подробный docs/architecture.md. Считает структурные изменения после последнего коммита, который
менял описание: появилась или исчезла папка с кодом (до второго уровня), тронут файл запуска
(requirements.txt, *.service, docker-compose, nginx-конфиг…), карта в AGENTS.md разошлась с CLAUDE.md.
Точка отсчёта берётся из истории git, а не из даты в файле: правка описания в одном коммите
с кодом засчитывается сразу. Сравнивается рабочее дерево, включая незакоммиченное.

Запуск:
  python arch_drift.py <папка> [...]            одна строка на проект
  python arch_drift.py --list <папка>           плюс перечень изменений
  python arch_drift.py --json <папка>           машинно
  python arch_drift.py --hook --since ISO <папка>
      для хука конца хода: JSON {"events": [...]} только с тем, что тронуто после ISO
      (коммиты и незакоммиченные правки этой сессии). Описание правили в сессии — пусто.
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

CODE_EXT = {'.py', '.js', '.mjs', '.cjs', '.ts', '.tsx', '.jsx', '.go', '.rs', '.sh', '.ps1', '.cmd', '.bat',
            '.sql', '.dart', '.kt', '.swift', '.php', '.rb', '.vue', '.svelte'}
INFRA_NAMES = {'package.json', 'requirements.txt', 'pyproject.toml', 'dockerfile', 'docker-compose.yml',
               'docker-compose.yaml', '.env.example', 'crontab', 'pubspec.yaml', 'vite.config.js', 'vite.config.ts',
               '.mcp.json', 'ecosystem.config.js', 'wrangler.toml'}
INFRA_EXT = {'.service', '.timer', '.conf'}
# Разовые работы и служебное. Граница по имени грубая (урок Вивека 23.10: разовые работы переезжают
# в подпапки), поэтому хук останавливает один раз и спрашивает, а не краснеет навсегда.
SKIP_SEG = re.compile(r'^(_.*|\..*|tests?|__pycache__|node_modules|venv|\.venv|dist|build|out|archive|attic|tmp|temp'
                      r'|prototypes?|scratch(pad)?|proba.*|research|docs|examples?)$', re.I)
DEPTH = 2  # bot/app/handlers и bot/app/db — одна часть bot/app
ARCH_DOC = 'docs/architecture.md'
MAP_RE = re.compile(r'<!-- arch:begin -->(.*?)<!-- arch:end -->', re.S)


def run(repo, *args, check=True):
    r = subprocess.run(['git', '-C', repo, *args], capture_output=True, text=True, encoding='utf-8',
                       errors='replace', stdin=subprocess.DEVNULL)
    if check and r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or 'git failed')
    return r.stdout if r.returncode == 0 else None


def kind(path):
    parts = path.replace('\\', '/').split('/')
    if any(SKIP_SEG.match(p) for p in parts[:-1]):
        return None
    name = parts[-1].lower()
    if name in INFRA_NAMES or os.path.splitext(name)[1] in INFRA_EXT:
        return 'infra'
    if os.path.splitext(name)[1] in CODE_EXT and not name.startswith(('test_', '_')):
        return 'code'
    return None


def part_of(path):
    return '/'.join((os.path.dirname(path.replace('\\', '/')) or '.').split('/')[:DEPTH])


def section(text):
    m = MAP_RE.search(text or '')
    return m.group(1).strip() if m else None


def map_at(repo, rev, name='CLAUDE.md'):
    return section(run(repo, 'show', f'{rev}:{name}', check=False))


def read_map(repo, name):
    try:
        with open(os.path.join(repo, name), encoding='utf-8') as f:
            return section(f.read())
    except FileNotFoundError:
        return 'нет файла'


def map_base(repo):
    """Последний коммит, где поменялось СОДЕРЖИМОЕ карты в CLAUDE.md, а не любой байт файла:
    массовая правка CLAUDE.md иначе ложно освежает описание."""
    for sha in run(repo, 'log', '-200', '--format=%H', '--', 'CLAUDE.md').split():
        cur = map_at(repo, sha)
        if cur is None:
            continue
        parent = (run(repo, 'rev-parse', '-q', '--verify', sha + '^', check=False) or '').strip()
        if not parent or map_at(repo, parent) != cur:
            return sha
    return None


def find_base(repo):
    cands = []
    sha = run(repo, 'log', '-1', '--format=%H', '--', ARCH_DOC).strip()
    if sha:
        cands.append((ARCH_DOC, sha))
    sha = map_base(repo)
    if sha:
        cands.append(('карта в CLAUDE.md', sha))
    if not cands:  # старый формат: без меток считаем по файлу CLAUDE.md целиком
        sha = run(repo, 'log', '-1', '--format=%H', '--', 'CLAUDE.md').strip()
        if sha:
            cands.append(('CLAUDE.md целиком', sha))
    if not cands:
        return None, None
    pos = {s: i for i, s in enumerate(run(repo, 'rev-list', '--topo-order', 'HEAD').split())}
    return min(cands, key=lambda c: pos.get(c[1], 10 ** 9))


def worktree_files(repo):
    """Файлы рабочего дерева: отслеживаемые, которые существуют, плюс новые неигнорируемые."""
    tracked = [p for p in run(repo, 'ls-files', '-z').split('\0') if p and os.path.exists(os.path.join(repo, p))]
    new = [p for p in run(repo, 'ls-files', '-z', '-o', '--exclude-standard').split('\0') if p]
    return tracked, new


def drift(repo):
    run(repo, 'rev-parse', '--git-dir')
    doc, base = find_base(repo)
    if not base:
        return {'repo': repo, 'doc': None, 'events': [], 'commits_after': None, 'doc_date': None}
    doc_date = run(repo, 'log', '-1', '--format=%cs', base).strip()
    commits_after = int(run(repo, 'rev-list', '--count', f'{base}..HEAD').strip() or 0)
    # Состояние «тогда» против «сейчас», а не история: файл, убранный и возвращённый, шума не даёт.
    # Единица — папка с кодом: новая папка меняет карту, новый файл в старой (ещё одна миграция) — нет.
    before = {part_of(n) for n in run(repo, 'ls-tree', '-r', '--name-only', base).splitlines() if kind(n) == 'code'}
    tracked, new = worktree_files(repo)
    now = {part_of(n) for n in tracked + new if kind(n) == 'code'}
    events = [{'st': 'A', 'kind': 'dir', 'path': d + '/'} for d in sorted(now - before)]
    events += [{'st': 'D', 'kind': 'dir', 'path': d + '/'} for d in sorted(before - now)]
    for line in run(repo, 'diff', '--no-renames', '--name-status', base).splitlines():
        st, _, path = line.partition('\t')
        if kind(path) == 'infra':
            events.append({'st': st[0], 'kind': 'infra', 'path': path})
    events += [{'st': 'A', 'kind': 'infra', 'path': p} for p in new if kind(p) == 'infra']
    # Codex читает AGENTS.md, а не CLAUDE.md: карта в нём обязана совпадать с картой в CLAUDE.md
    cmap, amap = read_map(repo, 'CLAUDE.md'), read_map(repo, 'AGENTS.md')
    if cmap not in (None, 'нет файла') and amap != 'нет файла' and amap != cmap:
        events.append({'st': 'M', 'kind': 'agents', 'path': 'AGENTS.md: карта не совпадает с CLAUDE.md'})
    return {'repo': repo, 'doc': doc, 'doc_date': doc_date, 'commits_after': commits_after, 'events': events}


def touched_since(repo, since):
    """Пути, тронутые после since: в коммитах и незакоммиченные (по времени правки файла)."""
    out = set(p for p in (run(repo, 'log', f'--since={since}', '--no-renames', '--name-only', '--format=') or '')
              .splitlines() if p)
    t0 = datetime.fromisoformat(since.replace('Z', '+00:00')).timestamp()
    for line in run(repo, 'status', '--porcelain', '-uall', '--no-renames').splitlines():
        path = line[3:].strip().strip('"')
        full = os.path.join(repo, path)
        try:
            if os.path.getmtime(full) >= t0:
                out.add(path)
        except OSError:
            out.add(path)  # удалён: время не узнать, считаем тронутым
    return out


def session_events(repo, since):
    """События отставания, к которым причастна эта сессия. Описание правили в сессии — пусто."""
    if read_map(repo, 'CLAUDE.md') in (None, 'нет файла'):
        return []  # проект без карты: проверка не включена
    touched = touched_since(repo, since)
    # Описание правили в этой сессии (карта в рабочем дереве или в коммитах, либо подробный файл):
    # структура считается сверенной, остаётся только проверка, что AGENTS.md получил ту же карту
    updated = (read_map(repo, 'CLAUDE.md') != map_at(repo, 'HEAD') or ARCH_DOC in touched
               or ('CLAUDE.md' in touched and map_changed_since(repo, since)))
    res = drift(repo)
    hit = []
    for e in res['events']:
        if updated and e['kind'] != 'agents':
            continue
        if e['kind'] == 'dir':
            pre = e['path'].rstrip('/')
            if any(kind(p) == 'code' and part_of(p) == pre for p in touched):
                hit.append(e)
        elif e['kind'] == 'infra':
            if e['path'] in touched:
                hit.append(e)
        elif e['kind'] == 'agents':
            if 'CLAUDE.md' in touched or 'AGENTS.md' in touched:
                hit.append(e)
    return hit


def map_changed_since(repo, since):
    """Содержимое карты в CLAUDE.md менялось коммитами после since."""
    shas = (run(repo, 'log', f'--since={since}', '--format=%H', '--', 'CLAUDE.md') or '').split()
    for sha in shas:
        parent = (run(repo, 'rev-parse', '-q', '--verify', sha + '^', check=False) or '').strip()
        if not parent or map_at(repo, sha) != map_at(repo, parent):
            return True
    return False


def main(argv):
    sys.stdout.reconfigure(encoding='utf-8')
    if '--hook' in argv:
        i = argv.index('--since')
        since, repo = argv[i + 1], [a for a in argv[i + 2:] if not a.startswith('--')][0]
        try:
            print(json.dumps({'events': session_events(repo, since)}, ensure_ascii=False))
        except Exception as e:  # сбой обязан дойти до хука, а не выглядеть «всё в порядке»
            print(json.dumps({'error': str(e)[:300]}, ensure_ascii=False))
        return
    as_json, listing = '--json' in argv, '--list' in argv
    res = []
    for r in [a for a in argv if not a.startswith('--')]:
        try:
            res.append(drift(r))
        except Exception as e:
            res.append({'repo': r, 'error': str(e)})
    if as_json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return
    for x in res:
        name = os.path.basename(os.path.normpath(x['repo']))
        if 'error' in x:
            print(f'{name}\tОШИБКА\t{x["error"][:80]}')
        elif not x['doc']:
            print(f'{name}\tописания нет')
        else:
            print(f'{name}\t{x["doc"]} {x["doc_date"]}\tкоммитов после: {x["commits_after"]}\t'
                  f'структурных изменений: {len(x["events"])}')
            if listing:
                for e in x['events']:
                    print(f'   {e["st"]} {e["kind"]:6} {e["path"]}')


if __name__ == '__main__':
    main(sys.argv[1:])
