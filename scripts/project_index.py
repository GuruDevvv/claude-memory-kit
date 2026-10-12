# -*- coding: utf-8 -*-
"""Собирает PROJECT_INDEX.md — список всех проектов одной таблицей.

Источник — файлы HANDOFF.md в папках проектов: дата из строки «Обновлено:», значок
из строки «## Статус:», первая строка раздела «## Следующий шаг». Руками таблицу
не ведут: агент обновляет HANDOFF, остальное делает скрипт (его зовёт хук конца хода).

Запуск:
  python project_index.py            # корень и память берутся из memory-kit.json
  python project_index.py --quiet    # печатать только проблемы
"""
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", ".idea", ".vscode"}
DEPTH = 2                # проекты лежат в корне или на уровень глубже (группы вроде _archive)
STEP_MAX = 90


def find_handoffs(root):
    found = []
    for level1 in sorted(os.listdir(root)):
        p1 = os.path.join(root, level1)
        if not os.path.isdir(p1) or level1 in SKIP:
            continue
        if os.path.exists(os.path.join(p1, "HANDOFF.md")):
            found.append((level1, os.path.join(p1, "HANDOFF.md")))
            continue
        if DEPTH < 2:
            continue
        try:
            inner = sorted(os.listdir(p1))
        except OSError:
            continue
        for level2 in inner:
            p2 = os.path.join(p1, level2)
            if os.path.isdir(p2) and level2 not in SKIP and os.path.exists(os.path.join(p2, "HANDOFF.md")):
                found.append((level1 + "/" + level2, os.path.join(p2, "HANDOFF.md")))
    return found


def parse(path):
    with open(path, "rb") as fh:
        text = fh.read().decode("utf-8", errors="replace").replace("\r\n", "\n")
    date = re.search(r"^Обновлено:\s*(\d{4}-\d{2}-\d{2})", text, re.M)
    status = re.search(r"^## Статус:\s*(\S+)", text, re.M)
    step = re.search(r"^## Следующий шаг\s*\n+([^\n#][^\n]*)", text, re.M)
    cell = (step.group(1).strip() if step else "").replace("|", "/")
    if len(cell) > STEP_MAX:
        cell = cell[:STEP_MAX].rstrip() + "…"
    return (date.group(1) if date else "—", status.group(1) if status else "", cell)


def main(argv):
    for stream in (sys.stdout, sys.stderr):     # чужая консоль может быть не в UTF-8
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    quiet = "--quiet" in argv
    try:
        with open(os.path.join(BASE, "memory-kit.json"), encoding="utf-8") as fh:
            cfg = json.load(fh)
    except Exception as e:
        print("project_index: нет настроек memory-kit.json (%s)" % type(e).__name__)
        return 1
    root, central = cfg["root"], cfg["central"]
    if not os.path.isdir(root):
        print("project_index: корневой папки проектов нет: " + root)
        return 1
    found = find_handoffs(root)
    if os.path.exists(os.path.join(root, "HANDOFF.md")):      # у самого корня тоже бывает HANDOFF
        found.append((os.path.basename(os.path.normpath(root)), os.path.join(root, "HANDOFF.md")))
    rows = [(name,) + parse(path) for name, path in found]
    rows.sort(key=lambda r: r[1] if r[1] != "—" else "0000", reverse=True)
    out = ["# Список проектов", "",
           "<!-- Собрано project_index.py из файлов HANDOFF.md. Руками не править. -->", "",
           "| Проект | Обновлён | Статус | Следующий шаг |",
           "|--------|----------|--------|---------------|"]
    out += ["| %s | %s | %s | %s |" % r for r in rows]
    body = "\n".join(out) + "\n"
    target = os.path.join(central, "PROJECT_INDEX.md")
    old = None
    if os.path.exists(target):
        with open(target, "rb") as fh:
            old = fh.read().decode("utf-8", errors="replace")
    if old != body:
        os.makedirs(central, exist_ok=True)
        with open(target + ".tmp", "wb") as fh:
            fh.write(body.encode("utf-8"))
        os.replace(target + ".tmp", target)
    if not quiet:
        print("проектов с HANDOFF.md: %d -> %s" % (len(rows), target))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
