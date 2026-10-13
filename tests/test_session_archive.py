# -*- coding: utf-8 -*-
"""Проверка архива разговоров на песочнице. Запуск из папки набора: python tests/test_session_archive.py"""
import importlib.util
import io
import json
import os
import sys
import tempfile
import time

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(KIT, "scripts", name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sa, ms = load("session_archive"), load("memory_search")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("OK   " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))


box = tempfile.mkdtemp(prefix="sa_")
sa.SOURCE, sa.ARCHIVE = os.path.join(box, "projects"), os.path.join(box, "sessions")


def session(slug, sid, entries, age_minutes=120):
    p = os.path.join(sa.SOURCE, slug, sid + ".jsonl")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with io.open(p, "w", encoding="utf-8", newline="\n") as fh:
        for e in entries:
            fh.write(json.dumps(e, ensure_ascii=False, separators=(",", ":")) + "\n")
    t = time.time() - age_minutes * 60
    os.utime(p, (t, t))
    return p


def user(text, **kw):
    return dict({"type": "user", "timestamp": "2026-09-01T10:00:00.000Z", "cwd": "E:\\Work\\Shop",
                 "message": {"role": "user", "content": text}}, **kw)


def agent(blocks):
    return {"type": "assistant", "timestamp": "2026-09-01T10:01:00.000Z", "cwd": "E:\\Work\\Shop",
            "message": {"role": "assistant", "content": blocks}}


p = session("E--Work-Shop", "aaaaaaaa-1111", [
    {"type": "mode", "x": 1},
    user("почему корзина считает скидку дважды? пароль: Qwerty12345 <system-reminder>служебное</system-reminder>"),
    agent([{"type": "thinking", "thinking": "тайные размышления"},
           {"type": "text", "text": "Скидка применяется в двух местах."},
           {"type": "tool_use", "name": "Bash", "input": {"command": "cat secret.env"}}]),
    user([{"type": "tool_result", "content": "ВЫВОД ИНСТРУМЕНТА sk-ant-abcdefghijklmnopqrstuvwxyz"}]),
    agent([{"type": "text", "text": "Убрал второе применение, решили оставить расчёт в корзине."}]),
    user("помощник говорит", isSidechain=True),
])
session("E--Work-Shop", "bbbbbbbb-2222", [user("свежая сессия ещё идёт")], age_minutes=1)
session("E--Work-Shop", "cccccccc-3333", [{"type": "mode", "x": 1}])

done, empty = sa.sweep()
files = [f for f in os.listdir(os.path.join(sa.ARCHIVE, "E--Work-Shop")) if f.endswith(".md")]
text = io.open(os.path.join(sa.ARCHIVE, "E--Work-Shop", files[0]), encoding="utf-8").read() if files else ""
check("законченная сессия в архиве, идущая и пустая — нет", done == 1 and empty == 1 and len(files) == 1, f"{done} {empty} {files}")
check("есть слова человека и агента", "скидку дважды" in text and "решили оставить расчёт" in text)
check("нет вывода инструментов, размышлений, служебного и помощников",
      not any(x in text for x in ("ВЫВОД ИНСТРУМЕНТА", "тайные", "служебное", "cat secret", "помощник говорит")), text)
check("пароль вырезан", "Qwerty12345" not in text and "<скрыто>" in text)
check("обычная фраза со словом «токен» цела", "токен устарел давно" in sa.mask("токен устарел давно"))
check("повторный проход ничего не трогает", sa.sweep() == (0, 0))

with io.open(p, "a", encoding="utf-8", newline="\n") as fh:
    fh.write(json.dumps(user("а что с доставкой?"), ensure_ascii=False, separators=(",", ":")) + "\n")
t = time.time() - 3600
os.utime(p, (t, t))                                   # запись сессии выросла час назад…
old = os.path.join(sa.ARCHIVE, "E--Work-Shop", files[0])
os.utime(old, (t - 3600, t - 3600))                   # …а в архив её забирали ещё раньше
done, _ = sa.sweep()
files = [f for f in os.listdir(os.path.join(sa.ARCHIVE, "E--Work-Shop")) if f.endswith(".md")]
text = io.open(os.path.join(sa.ARCHIVE, "E--Work-Shop", files[0]), encoding="utf-8").read()
check("сессию продолжили — архив обновлён, файл один", done == 1 and len(files) == 1 and "доставкой" in text)

os.remove(p)
check("запись сессии стёрли — архив остался", sa.sweep() == (0, 0) and os.path.exists(os.path.join(sa.ARCHIVE, "E--Work-Shop", files[0])))

hits = ms.search("почему скидка в корзине", [sa.ARCHIVE], 3, None, True)
check("поиск находит разговор и показывает проект с датой",
      bool(hits) and hits[0]["kind"] == "session" and hits[0]["title"].startswith("Shop, 2026-09-01"), str(hits)[:300])

# записи Codex: другая раскладка, папка проекта внутри записи
sa.CODEX_SOURCE = os.path.join(box, "codex")


def codex(sid, source, entries, age_minutes=120):
    p = os.path.join(sa.CODEX_SOURCE, "2026", "09", "02", "rollout-2026-09-02T10-00-00-%s.jsonl" % sid)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    rows = [{"timestamp": "2026-09-02T10:00:00.000Z", "type": "session_meta",
             "payload": {"id": sid, "cwd": "E:\\Work\\Shop", "source": source}}]
    for role, texts in entries:
        kind = "output_text" if role == "assistant" else "input_text"
        rows.append({"timestamp": "2026-09-02T10:01:00.000Z", "type": "response_item",
                     "payload": {"type": "message", "role": role, "content": [{"type": kind, "text": t} for t in texts]}})
    with io.open(p, "w", encoding="utf-8", newline="\n") as fh:
        for e in rows:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
    t = time.time() - age_minutes * 60
    os.utime(p, (t, t))
    return p


codex("11111111-aaaa-bbbb-cccc-000000000001", "cli", [
    ("developer", ["служебная вставка разработчика"]),
    ("user", ["# AGENTS.md instructions for E:\\Work\\Shop\nправила проекта"]),
    ("user", ["<environment_context><cwd>E:\\Work\\Shop</cwd></environment_context>"]),
    ("user", ["\n# Files mentioned by the user:\n\n## a.png: C:/tmp/a.png\n\n## My request for Codex:\nпочини оплату картой\n"]),
    ("assistant", ["Оплата падала из-за старого ключа, заменил."])])
codex("22222222-aaaa-bbbb-cccc-000000000002", "exec", [("user", ["служебный вызов из скрипта"]), ("assistant", ["ответ"])])
codex("11111111-aaaa-bbbb-cccc-000000000003", "vscode", [("user", ["вторая сессия той же минуты"]), ("assistant", ["да"])])
done, empty = sa.sweep_codex()
files = [f for f in os.listdir(os.path.join(sa.ARCHIVE, "E--Work-Shop")) if f.endswith("_00000001.md")]
text = io.open(os.path.join(sa.ARCHIVE, "E--Work-Shop", files[0]), encoding="utf-8").read() if files else ""
check("Codex: разговоры с человеком в архиве, служебный вызов — нет", done == 2 and empty == 1 and len(files) == 1, f"{done} {empty} {files}")
check("Codex: сессии с общим началом id не затирают друг друга",
      os.path.exists(os.path.join(sa.ARCHIVE, "E--Work-Shop", "2026-09-02_00000003.md")))
check("Codex: есть просьба и ответ, нет правил, окружения и списка файлов",
      "почини оплату картой" in text and "старого ключа" in text and "Агент: Codex" in text
      and not any(x in text for x in ("AGENTS.md", "environment_context", "a.png", "служебная")), text)
check("Codex: повторный проход ничего не трогает", sa.sweep_codex() == (0, 0))
hits = ms.search("оплата картой старый ключ", [sa.ARCHIVE], 3, None, True)
check("Codex: поиск находит разговор", bool(hits) and hits[0]["title"].startswith("Shop, 2026-09-02"), str(hits)[:300])

print(f"\nитого: {sum(results)} из {len(results)}")
sys.exit(0 if all(results) else 1)
