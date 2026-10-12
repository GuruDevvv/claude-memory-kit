# -*- coding: utf-8 -*-
"""Проверка хуков и установщика на песочнице: своя домашняя папка, свой корень проектов.

Запуск из папки набора: python tests/test_hooks.py
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import time

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("OK   " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    io.open(path, "w", encoding="utf-8", newline="\n").write(text)


box = tempfile.mkdtemp(prefix="mkh_")
home, root = os.path.join(box, "home"), os.path.join(box, "work")
for d in (os.path.join(home, ".claude"), os.path.join(home, ".codex"), root):
    os.makedirs(d)
env = dict(os.environ, PYTHONIOENCODING="utf-8")


def installer(*args):
    r = subprocess.run([sys.executable, os.path.join(KIT, "install.py"), "--home", home, "--root", root] + list(args),
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    return r.returncode, r.stdout + r.stderr


def start_hook(cwd, tool=".claude"):
    r = subprocess.run(["node", os.path.join(home, tool, "hooks", "session-start-context.js")],
                       cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        return json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
    except Exception:
        return ""


def stop_hook(cwd):
    for f in os.listdir(tempfile.gettempdir()):          # напоминание — раз в день, сбрасываем отметку
        if f.startswith("memory-kit-reminded-"):
            try:
                os.remove(os.path.join(tempfile.gettempdir(), f))
            except OSError:
                pass
    r = subprocess.run(["node", os.path.join(home, ".claude", "hooks", "session-summary-check.js")],
                       cwd=cwd, input="{}", capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.stdout


code, out = installer("--tool", "both")
check("установка в песочницу", code == 0, out[-400:])
central = json.load(open(os.path.join(home, ".claude", "memory-kit.json"), encoding="utf-8"))["central"]

proj = os.path.join(root, "Shop")
write(os.path.join(proj, "HANDOFF.md"), "# HANDOFF — Shop\nзаписка магазина\n")
write(os.path.join(proj, "src", "deep", "a.txt"), "x")
write(os.path.join(root, "HANDOFF.md"), "# HANDOFF — корень\nзаписка корня\n")
write(os.path.join(central, "projects", "Shop.md"), "# Журнал\n\n## [2026-10-12 10:00]\nзапись журнала магазина\n")
bare = os.path.join(root, "Bare")
write(os.path.join(bare, "x.txt"), "x")
write(os.path.join(central, "projects", "Bare.md"), "# Журнал\n\n## [2026-10-12 10:00]\nзапись журнала без записки\n")

ctx = start_hook(proj)
check("из папки проекта: записка и журнал", "записка магазина" in ctx and "запись журнала магазина" in ctx)
ctx = start_hook(os.path.join(proj, "src", "deep"))
check("из подпапки проекта: записка и журнал", "записка магазина" in ctx and "запись журнала магазина" in ctx, ctx[:300])
check("из подпапки: записка корня не подменяет записку проекта", "записка корня" not in ctx)
ctx = start_hook(root)
check("из корня проектов: записка корня", "записка корня" in ctx and "записка магазина" not in ctx)
ctx = start_hook(bare)
check("проект без записки: журнал всё равно приходит", "запись журнала без записки" in ctx, ctx[:300])
check("метка хука есть всегда", "memory-kit: хуки работают" in ctx)

# напоминание: запись в чужом журнале не закрывает свой проект
old = time.time() - 3 * 86400
os.utime(os.path.join(central, "projects", "Shop.md"), (old, old))
write(os.path.join(proj, "HANDOFF.md"), "# HANDOFF — Shop\nзаписка магазина\n")      # записка свежая
out = stop_hook(os.path.join(proj, "src"))
check("журнал проекта не тронут, чужой свежий: напоминание есть", "Shop.md" in out, out[:300])

code, out = installer("--tool", "both", "--check")
check("проверка установки проходит", code == 0, out[-600:])
check("проверка смотрит метку и оглавление", "отдаёт метку" in out and "оглавление доходит" in out, out[-600:])
check("проверка смотрит потолок Codex", "потолок Codex" in out, out[-600:])

# пустой ответ хука — провал проверки
hook = os.path.join(home, ".claude", "hooks", "session-start-context.js")
keep = io.open(hook, encoding="utf-8").read()
write(hook, "process.exit(0);\n")
code, out = installer("--tool", "both", "--check")
check("хук молчит: проверка установки это ловит", code != 0 and "[НЕТ] хук начала сессии" in out, out[-600:])
write(hook, keep)

# правила Codex у потолка: установщик предупреждает
rules = os.path.join(home, ".codex", "AGENTS.md")
write(rules, io.open(rules, encoding="utf-8").read() + "\n" + "ы" * 16000 + "\n")
code, out = installer("--tool", "codex")
check("правила Codex больше потолка: установщик предупреждает", "Codex читает только первые" in out, out[-600:])
code, out = installer("--tool", "codex", "--check")
check("и проверка установки это ловит", code != 0 and "потолок Codex" in out, out[-600:])

print(f"\nитого: {sum(results)} из {len(results)}")
sys.exit(0 if all(results) else 1)
