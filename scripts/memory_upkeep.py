# -*- coding: utf-8 -*-
"""Уход за памятью в конце хода: обрезка журналов проектов и проверка ссылок из оглавлений.

Идеи пользователя набора (14.10.2026).

Журналы. Файл projects/<Проект>.md центральной памяти растёт без конца: у одного проекта за полгода
набралось 390 записей и 1,7 МБ. Хук старта показывает три последние, остальное читает только поиск.
Скрипт оставляет в журнале первые N записей (новые пишутся сверху), остальные переносит в начало
projects/archive/<Проект>.md. Ничего не удаляется; поиск журналы из архива видит.
  * Ключ "journal_keep" в memory-kit.json, по умолчанию 5; 0 — не обрезать.
  * Время правки журнала после обрезки возвращается прежним: по нему напоминания судят,
    писали ли журнал сегодня.
  * Журнал поменялся, пока мы его читали (параллельная сессия дописала запись), — не трогаем,
    обрежем в следующий раз.

Ссылки. После переименований и чисток строка оглавления может вести на файл, которого нет.
Проверяются MEMORY.md, MEMORY.head.md и INDEX.md во всех папках памяти. Ссылка ищется от папки
оглавления. Битые ссылки печатаются, код выхода 1 — хук конца хода скажет об этом агенту.

Запуск: python memory_upkeep.py [--quiet] [--dry]
"""
import json
import os
import re
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTRY = re.compile(r"(?m)^(?=## \[)")
LINK = re.compile(r"\]\(([^)\s#]+\.md)(?:#[^)]*)?\)")
INDEX_FILES = ("MEMORY.md", "MEMORY.head.md", "INDEX.md")
ARCHIVE_HEAD = "# Архив журнала {name}\n\nСтарые записи, новые сверху. Свежие записи — в ../{name}.md.\n\n"


def config():
    try:
        with open(os.path.join(BASE, "memory-kit.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def read(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def write_atomic(path, text):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return tmp


ENTRY_DATE = re.compile(r"## \[\s*(\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2})?)")


def split_journal(text):
    """(вступление, [записи]) — запись начинается строкой «## [дата]»."""
    parts = ENTRY.split(text)
    if parts and not parts[0].startswith("## ["):
        return parts[0], parts[1:]
    return "", parts


def trim_journal(path, keep, dry=False):
    """Сколько записей перенесено в архив."""
    st = os.stat(path)
    text = read(path)
    intro, entries = split_journal(text)
    if len(entries) <= keep:
        return 0
    dates = [ENTRY_DATE.match(e) for e in (entries[0], entries[-1])]
    if all(dates) and dates[0].group(1) < dates[1].group(1):
        return 0                                     # журнал дописывают вниз: первые записи здесь самые старые
    stay, move = entries[:keep], entries[keep:]
    if dry:
        return len(move)
    name = os.path.basename(path)[:-3]
    arch_dir = os.path.join(os.path.dirname(path), "archive")
    os.makedirs(arch_dir, exist_ok=True)
    arch = os.path.join(arch_dir, name + ".md")
    old_intro, old_entries = split_journal(read(arch)) if os.path.exists(arch) else ("", [])
    nl = "\r\n" if "\r\n" in text else "\n"
    head = old_intro if old_intro.strip() else ARCHIVE_HEAD.format(name=name).replace("\n", nl)
    moved = [e if e.endswith(nl) else e + nl for e in move]
    new_arch = head + "".join(moved) + "".join(old_entries)
    new_journal = intro + "".join(stay).rstrip("\r\n") + nl
    tmp_a, tmp_j = write_atomic(arch, new_arch), write_atomic(path, new_journal)
    try:
        now = os.stat(path)
        if (now.st_mtime_ns, now.st_size) != (st.st_mtime_ns, st.st_size):
            raise RuntimeError("журнал поменялся во время обрезки")
        os.replace(tmp_a, arch)
        os.replace(tmp_j, path)
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
    except Exception:
        for t in (tmp_a, tmp_j):
            if os.path.exists(t):
                os.remove(t)
        return 0
    return len(move)


def broken_links(mem):
    """[(оглавление, ссылка)] — ссылки из оглавлений папки памяти, которые ведут в пустоту."""
    out = []
    for f in INDEX_FILES:
        p = os.path.join(mem, f)
        if not os.path.exists(p):
            continue
        text = read(p)
        text = re.sub(r"(?s)```.*?```", "", text)
        text = re.sub(r"`[^`\n]*`", "", text)
        for m in LINK.finditer(text):
            link = m.group(1)
            if re.match(r"[a-z]+:|[A-Za-z]:|[/~]", link):
                continue                                 # адрес в сети или абсолютный путь — не наше
            if not os.path.exists(os.path.normpath(os.path.join(mem, link.replace("%20", " ")))):
                out.append((f, link))
    return out


def memory_dirs(cfg):
    dirs = []
    if cfg.get("central"):
        dirs.append(cfg["central"])
    root = cfg.get("projects_dir")
    if root and os.path.isdir(root):
        for name in sorted(os.listdir(root)):
            d = os.path.join(root, name, "memory")
            if os.path.isdir(d) and not any(os.path.normcase(os.path.abspath(d)) == os.path.normcase(os.path.abspath(x))
                                            for x in dirs):
                dirs.append(d)
    return dirs


def main(argv):
    quiet, dry = "--quiet" in argv, "--dry" in argv
    cfg = config()
    keep = int(cfg.get("journal_keep", 5) or 0)
    report = []
    journals = os.path.join(cfg.get("central") or "", "projects")
    if keep > 0 and cfg.get("central") and os.path.isdir(journals):
        for f in sorted(os.listdir(journals)):
            p = os.path.join(journals, f)
            if f.endswith(".md") and os.path.isfile(p):
                n = trim_journal(p, keep, dry)
                if n:
                    report.append("журнал %s: в архив %d записей" % (f[:-3], n))
    bad = []
    for mem in memory_dirs(cfg):
        bad += ["%s/%s → %s" % (os.path.basename(os.path.dirname(mem)), f, link) for f, link in broken_links(mem)]
    if not quiet:
        print("\n".join(report) or "журналы: обрезать нечего")
    if bad:
        print("Битые ссылки в оглавлениях памяти (%d):\n" % len(bad) + "\n".join("- " + b for b in bad[:20]))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
