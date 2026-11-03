# -*- coding: utf-8 -*-
"""Карта устройства: скрипт arch_drift.py и остановка в хуке конца хода.
Каждая проверка в обе стороны: заведомо отставшее держит, заведомо сверенное пропускает.

Запуск из папки набора: python tests/test_arch.py
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import time

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(KIT, "scripts"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import arch_drift  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("OK   " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    io.open(path, "w", encoding="utf-8", newline="\n").write(text)


def g(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=master"]
                          + list(args), cwd=cwd, capture_output=True, text=True, stdin=subprocess.DEVNULL)


MAP = "# P\n<!-- arch:begin -->\n## Как устроено\n- `bot/` — бот\n<!-- arch:end -->\n"
box = tempfile.mkdtemp(prefix="mka_")


def make(name, claude=MAP):
    repo = os.path.join(root, name)
    os.makedirs(repo)
    g(repo, "init", "-q")
    write(os.path.join(repo, "CLAUDE.md"), claude)
    write(os.path.join(repo, "HANDOFF.md"), "# HANDOFF\n")
    write(os.path.join(repo, "bot", "main.py"), "x")
    g(repo, "add", ".")
    g(repo, "commit", "-q", "-m", "init")
    return repo


def commit(repo, msg="c"):
    g(repo, "add", ".")
    g(repo, "commit", "-q", "-m", msg)


# ── скрипт: отставание от последней правки описания ──
root = os.path.join(box, "unit")
os.makedirs(root)

r = make("u1")
write(os.path.join(r, "server", "app.py"), "x")
commit(r)
check("скрипт: новая часть без карты — отставание", [e["path"] for e in arch_drift.drift(r)["events"]] == ["server/"])

r = make("u2")
write(os.path.join(r, "server", "app.py"), "x")
write(os.path.join(r, "CLAUDE.md"), MAP.replace("- `bot/` — бот", "- `bot/` — бот\n- `server/` — сайт"))
commit(r)
check("скрипт: карта в том же коммите — чисто", arch_drift.drift(r)["events"] == [])

r = make("u3")
write(os.path.join(r, "server", "app.py"), "x")
commit(r)
write(os.path.join(r, "CLAUDE.md"), MAP + "\n## Правила\n- что-то\n")
commit(r)
check("скрипт: правка CLAUDE.md вне карты не освежает", len(arch_drift.drift(r)["events"]) == 1)

r = make("u4")
for p in ("bot/handlers.py", "prototypes/demo/build.py", "_dbg/probe.py"):
    write(os.path.join(r, p), "x")
commit(r)
check("скрипт: новый файл в известной части и черновики молчат", arch_drift.drift(r)["events"] == [])

r = make("u5")
write(os.path.join(r, "deploy", "bot.service"), "[Unit]")
commit(r)
check("скрипт: файл запуска — отставание", [e["kind"] for e in arch_drift.drift(r)["events"]] == ["infra"])

r = make("u6")
write(os.path.join(r, "server", "app.py"), "x")
check("скрипт: незакоммиченная новая часть тоже видна", [e["path"] for e in arch_drift.drift(r)["events"]] == ["server/"])

r = make("u8")
write(os.path.join(r, "общее", "scripts", "сборка.py"), "x")
write(os.path.join(r, "CLAUDE.md"), MAP.replace("- `bot/` — бот", "- `bot/` — бот\n- `общее/scripts/` — сборщики"))
commit(r)
check("скрипт: папка с кириллицей, описанная в карте, — чисто", arch_drift.drift(r)["events"] == [],
      str(arch_drift.drift(r)["events"]))
write(os.path.join(r, "ещё", "новое.py"), "x")
check("скрипт: новая папка с кириллицей — отставание",
      [e["path"] for e in arch_drift.drift(r)["events"]] == ["ещё/"], str(arch_drift.drift(r)["events"]))

r = make("u7")
write(os.path.join(r, "AGENTS.md"), "# для Codex\n")
commit(r)
check("скрипт: AGENTS.md без карты — расхождение", [e["kind"] for e in arch_drift.drift(r)["events"]] == ["agents"])
write(os.path.join(r, "AGENTS.md"), "# для Codex\n" + MAP.split("\n", 1)[1])
commit(r)
check("скрипт: AGENTS.md с той же картой — чисто", arch_drift.drift(r)["events"] == [])

# ── хук конца хода в песочнице ──
home, root = os.path.join(box, "home"), os.path.join(box, "work")
for d in (os.path.join(home, ".claude"), root):
    os.makedirs(d)
env = dict(os.environ, PYTHONIOENCODING="utf-8")
inst = subprocess.run([sys.executable, os.path.join(KIT, "install.py"), "--home", home, "--root", root],
                      capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
check("установка в песочницу", inst.returncode == 0, inst.stdout[-400:] + inst.stderr[-400:])
cfg_path = os.path.join(home, ".claude", "memory-kit.json")
cfg0 = json.load(open(cfg_path, encoding="utf-8"))
HOOK = os.path.join(home, ".claude", "hooks", "session-summary-check.js")
check("скрипт скопирован к агенту", os.path.exists(os.path.join(home, ".claude", "scripts", "arch_drift.py")))
n = [0]


def session(repo):
    """Новая сессия: запись создаётся сейчас, всё дальнейшее — её работа."""
    time.sleep(1.2)  # git --since считает секундами
    n[0] += 1
    t = os.path.join(box, "t%d.jsonl" % n[0])
    write(t, json.dumps({"type": "user", "message": {"role": "user", "content": "сделай"}}) + "\n")
    time.sleep(1.2)
    return t, "sess%d-%s" % (n[0], os.path.basename(box))


def hook(repo, t, sid, **extra):
    r = subprocess.run(["node", HOOK], cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       input=json.dumps(dict({"transcript_path": t, "session_id": sid}, **extra)))
    return r.stdout


r = make("h1")
write(os.path.join(r, "old", "x.py"), "x")
commit(r, "старое отставание до сессии")
t, sid = session(r)
check("хук: старое отставание до сессии не держит", '"decision":"block"' not in hook(r, t, sid))

t, sid = session(r)
write(os.path.join(r, "server", "app.py"), "x")
commit(r, "сессия добавила часть")
out = hook(r, t, sid)
check("хук: часть добавлена в сессии без карты — держит", '"decision":"block"' in out and "server/" in out, out[:300])
check("хук: второй раз за сессию не держит", '"decision":"block"' not in hook(r, t, sid))

t, sid = session(r)
write(os.path.join(r, "api", "main.py"), "x")
check("хук: незакоммиченная новая часть — держит", '"decision":"block"' in hook(r, t, sid))

t, sid = session(r)
write(os.path.join(r, "CLAUDE.md"), MAP.replace("- `bot/` — бот", "- `bot/` — бот\n- `server/`, `api/`, `old/` — сайт"))
check("хук: карта поправлена в сессии (без коммита) — пропускает", '"decision":"block"' not in hook(r, t, sid))
commit(r, "карта")

t, sid = session(r)
write(os.path.join(r, "web", "a.py"), "x")
check("хук: повтор после stop_hook_active не держит", '"decision":"block"' not in hook(r, t, sid, stop_hook_active=True))

r2 = make("h2", claude="# P\n## Структура\n- bot/\n")
t, sid = session(r2)
write(os.path.join(r2, "server", "app.py"), "x")
commit(r2)
check("хук: проект без меток карты не трогает", '"decision":"block"' not in hook(r2, t, sid))

r3 = make("h3")
t, sid = session(r3)
write(os.path.join(r3, "server", "app.py"), "x")
json.dump(dict(cfg0, guard={"arch_map": False}), open(cfg_path, "w", encoding="utf-8"))
check("хук: выключатель guard.arch_map молчит", '"decision":"block"' not in hook(r3, t, sid))
json.dump(dict(cfg0, python="node"), open(cfg_path, "w", encoding="utf-8"))
out = hook(r3, t, "sessX-" + os.path.basename(box))
check("хук: сбой проверки не молчит, а говорит строкой", "Проверка карты устройства не сработала" in out, out[:300])
json.dump(cfg0, open(cfg_path, "w", encoding="utf-8"))
out = hook(r3, t, "sessY-" + os.path.basename(box))
check("хук: тот же случай с рабочим Python — держит", '"decision":"block"' in out, out[:300])

# сессия открыта в общей папке, а правка — в репозитории проекта: хук находит его по правленым файлам
t, sid = session(r3)
write(os.path.join(r3, "worker", "w.py"), "x")
tr = [json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
    {"type": "tool_use", "name": "Write", "input": {"file_path": os.path.join(r3, "worker", "w.py")}}]}})]
io.open(t, "a", encoding="utf-8").write("\n".join(tr) + "\n")
out = subprocess.run(["node", HOOK], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace",
                     input=json.dumps({"transcript_path": t, "session_id": sid})).stdout
check("хук: правка в другом репозитории из общей папки — держит", '"decision":"block"' in out and "worker/" in out, out[:300])

print(f"\nитого: {sum(results)} из {len(results)}")
sys.exit(0 if all(results) else 1)
