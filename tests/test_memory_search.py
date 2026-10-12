# -*- coding: utf-8 -*-
"""Проверка поиска по памяти на песочнице. Запуск из папки набора: python tests/test_memory_search.py"""
import importlib.util
import io
import os
import sys
import tempfile

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("ms", os.path.join(KIT, "scripts", "memory_search.py"))
ms = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ms)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("OK   " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))


def write(rel, text):
    p = os.path.join(mem, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    io.open(p, "w", encoding="utf-8", newline="\n").write(text)


def files(query, **kw):
    return [h["file"] for h in ms.search(query, [mem], **kw)]


mem = tempfile.mkdtemp(prefix="ms_")
write("feedback/feedback_commit.md", "---\nname: c\nmetadata:\n  type: feedback\n  status: правило\n  seen: 3\n"
      "  index_line: \"Коммитить свои правки поимённо, без add -A\"\n---\n\nтело про коммиты\n")
write("feedback/feedback_tree.md", "---\nname: t\nmetadata:\n  type: feedback\n"
      "  index_line: \"Ёлку ставить в декабре\"\n---\n\nтело\n")
write("ref/server.md", "---\nname: s\ndescription: Как зайти на сервер и выкатить сайт\n---\n\nssh и pm2\n")
write("projects/Shop.md", "# Журнал\n\n## [2026-10-01 10:00]\nчинили корзину, коммит не прошёл\n\n"
      "## [2026-09-01 10:00]\nставили оплату\n")
write("MEMORY.md", "- коммит коммит коммит\n")
write("INDEX.md", "- коммит\n")

got = files("как коммичу правки")
check("окончания: «как коммичу правки» находит «коммитить свои правки»", "feedback/feedback_commit.md" in got, str(got))
check("правило стоит выше записи журнала", files("коммит")[0] == "feedback/feedback_commit.md", str(files("коммит")))
check("ё и е — одна буква", "feedback/feedback_tree.md" in files("елка"))
check("справочник находится по описанию", "ref/server.md" in files("выкатить сайт на сервер"))
check("журнал делится на записи", [h["anchor"] for h in ms.search("оплата", [mem])] == ["2026-09-01 10:00"])
check("оглавление и каталог в поиск не попадают", not any(f in ("MEMORY.md", "INDEX.md") for f in files("коммит", limit=10)))
check("фильтр по виду", files("коммит", kind="journal") == ["projects/Shop.md"])
check("пустой и мусорный запрос не роняют поиск", ms.search("и в на", [mem]) == [] and ms.search('"(*', [mem]) == [])
check("слова, которых нет", files("квазар") == [])

print(f"\nитого: {sum(results)} из {len(results)}")
sys.exit(0 if all(results) else 1)
