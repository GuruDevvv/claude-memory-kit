# -*- coding: utf-8 -*-
"""Проверка хуков и установщика на песочнице: своя домашняя папка, свой корень проектов.

Запуск из папки набора: python tests/test_hooks.py
"""
import io
import json
import os
import re
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

# память в нужный момент
def recall(mode, payload):
    r = subprocess.run([sys.executable, os.path.join(home, ".claude", "hooks", "memory-recall-hook.py"), mode],
                       input=json.dumps(payload), capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env)
    try:
        out = json.loads(r.stdout)["hookSpecificOutput"]
        return out.get("additionalContext") or "ОТКАЗ: " + out["permissionDecisionReason"]
    except Exception:
        return ""


write(os.path.join(central, "feedback", "feedback_commit.md"), "---\nname: c\nmetadata:\n  type: feedback\n"
      "  status: правило\n  seen: 3\n  index_line: \"Коммитить поимённо, без add -A\"\n  moment: Когда коммичу\n---\n\nтело\n")
write(os.path.join(central, "feedback", "feedback_ssh.md"), "---\nname: s\nmetadata:\n  type: feedback\n"
      "  index_line: \"На сервере pm2 руками не запускать\"\n---\n\nssh и деплой: только скриптом\n")
sid = "test-%d" % time.time()
ctx = recall("pre", {"session_id": sid, "cwd": proj, "tool_input": {"command": "git -C . commit -m x"}})
check("перед коммитом приходит правило про коммит", "без add -A" in ctx and "pm2" not in ctx, ctx[:300])
check("коммит не останавливается, подсказка идёт добавкой", not ctx.startswith("ОТКАЗ"))
check("второй коммит за сессию — без повтора",
      recall("pre", {"session_id": sid, "cwd": proj, "tool_input": {"command": "git commit -m y"}}) == "")
ctx = recall("pre", {"session_id": sid, "cwd": proj, "tool_input": {"command": "git push origin main"}})
check("первый пуш за сессию остановлен, правила — причиной отказа", ctx.startswith("ОТКАЗ") and "без add -A" in ctx, ctx[:200])
check("повтор пуша проходит",
      recall("pre", {"session_id": sid, "cwd": proj, "tool_input": {"command": "git push origin main"}}) == "")
ctx = recall("pre", {"session_id": sid, "cwd": proj, "tool_input": {"command": "ssh host pm2 restart app"}})
check("первая команда на сервер остановлена; правило без раздела нашёл поиск",
      ctx.startswith("ОТКАЗ") and "pm2 руками" in ctx, ctx[:300])
check("чтение без подсказки", recall("pre", {"session_id": sid + "b", "cwd": proj,
                                              "tool_input": {"command": "git status && git log -3 && ls"}}) == "")
ctx = recall("pre", {"session_id": sid, "cwd": proj, "tool_name": "Bash", "tool_input": {"command": "git add -A"}})
check("запрет: git add -A остановлен, и каждый раз", ctx.startswith("ОТКАЗ") and "поимённо" in ctx
      and recall("pre", {"session_id": sid, "cwd": proj, "tool_name": "Bash",
                         "tool_input": {"command": "git add ."}}).startswith("ОТКАЗ"), ctx[:200])
check("docker на своей машине — не сервер", recall("pre", {"session_id": sid + "d", "cwd": proj, "tool_name": "Bash",
      "tool_input": {"command": "docker run --rm python:3.11-slim python -V"}}) == "")

# журнал срабатываний
events = [json.loads(x) for x in io.open(os.path.join(central, ".metrics", "events.jsonl"), encoding="utf-8")]
kinds = [(e["event"], e.get("key")) for e in events]
check("журнал: показ правил, остановка и запрет записаны", ("recall", "commit") in kinds and ("recall", "server") in kinds
      and ("guard", "git_add_all") in kinds, str(kinds))
check("журнал: в записи есть проект и файлы правил, текста команды нет",
      all(e.get("project") == "Shop" for e in events) and any("feedback/feedback_commit.md" in (e.get("files") or []) for e in events)
      and "push origin" not in json.dumps(events), str(events[:2]))
check("журнал: повтор темы в той же сессии не пишется", kinds.count(("recall", "commit")) == 2, str(kinds))

# журнал не растёт без конца: старые части удаляются
import importlib.util
spec = importlib.util.spec_from_file_location("ms_inst", os.path.join(home, ".claude", "scripts", "memory_search.py"))
ms_inst = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ms_inst)
mdir = os.path.join(central, ".metrics")
for stamp in ("20260101-000000", "20260102-000000", "20260103-000000", "20260104-000000"):
    write(os.path.join(mdir, "events.%s.jsonl" % stamp), "{}\n")
ms_inst.METRICS_MAX_BYTES, ms_inst.METRICS_KEEP_FILES = 10, 2
ms_inst.log_event("search", project="Shop")
parts = sorted(f for f in os.listdir(mdir) if f.startswith("events."))
check("журнал: при переполнении остаются текущий файл и две последние части",
      len(parts) == 3 and "events.jsonl" in parts and "events.20260101-000000.jsonl" not in parts, str(parts))
kept = [f for f in parts if f != "events.jsonl"][-1]
os.replace(os.path.join(mdir, kept), os.path.join(mdir, "events.jsonl"))      # вернуть записи для проверок ниже
for f in os.listdir(mdir):
    if re.match(r"events\.\d", f):
        os.remove(os.path.join(mdir, f))

# тема есть, правил нет: отметка «показали» не ставится, пустой показ виден в журнале
empty_cfg = os.path.join(home, ".claude", "memory-kit.json")
keep_cfg = io.open(empty_cfg, encoding="utf-8").read()
cfg_x = json.loads(keep_cfg)
cfg_x["recall"] = [{"key": "deploy_x", "pattern": "kubectl", "query": "кубернетес которого нет", "moments": ["нет такого раздела"]}]
write(empty_cfg, json.dumps(cfg_x, ensure_ascii=False))
recall("pre", {"session_id": sid, "cwd": proj, "tool_name": "Bash", "tool_input": {"command": "kubectl apply"}})
mark = os.path.join(tempfile.gettempdir(), "memory-kit-recall-%s.json" % re.sub(r"[^A-Za-z0-9_-]", "", sid))
events = [json.loads(x) for x in io.open(os.path.join(central, ".metrics", "events.jsonl"), encoding="utf-8")]
check("правил по теме нет: в журнале пустой показ, отметки «показали» нет",
      events[-1]["event"] == "recall_empty" and "deploy_x" not in io.open(mark, encoding="utf-8").read(), str(events[-1]))
write(empty_cfg, keep_cfg)

# конец сессии без «Промпта»
U = lambda text: {"type": "user", "message": {"role": "user", "content": text}}
A = lambda text: {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}
TOOL = {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]}}


def ritual(lines, **extra):
    t = os.path.join(box, "transcript.jsonl")
    write(t, "\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n")
    r = subprocess.run(["node", os.path.join(home, ".claude", "hooks", "session-summary-check.js")], cwd=proj,
                       input=json.dumps(dict({"transcript_path": t, "session_id": "s1"}, **extra)),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return '"decision":"block"' in r.stdout


check("«стоп» без раздела «Промпт»: конец хода остановлен", ritual([U("привет"), A("да"), U("стоп"), A("сохраняю"), TOOL, A("Готово.")]))
check("«Стоп.» с точкой и большой буквы — тоже", ritual([U("Стоп."), A("Готово.")]))
check("«стоп» и раздел «Промпт» в ответе: пропускает", not ritual([U("стоп"), A("Итог\n\n## Промпт\n> продолжи")]))
check("слово внутри фразы концом сессии не считается", not ritual([U("пока не трогай это"), A("хорошо")]))
check("обычный ход не трогает", not ritual([U("сделай кнопку"), A("сделал")]))
check("второй раз подряд не держит", not ritual([U("стоп"), A("Готово.")], stop_hook_active=True))
check("служебная вставка в сообщении человека не мешает",
      ritual([U("стоп\n<system-reminder>что-то служебное</system-reminder>"), A("Готово.")]))

ctx = recall("subagent", {"session_id": sid, "cwd": os.path.join(proj, "src")})
check("помощник получает записку проекта и команду поиска", "записка магазина" in ctx and "memory_search.py" in ctx
      and len(ctx) <= 3000, ctx[:300])
settings = json.load(open(os.path.join(home, ".claude", "settings.json"), encoding="utf-8"))["hooks"]
check("хуки момента прописаны у Claude и не прописаны у Codex", "SubagentStart" in settings
      and any("memory-recall-hook" in h["command"] for g in settings["PreToolUse"] for h in g["hooks"])
      and "memory-recall" not in io.open(os.path.join(home, ".codex", "hooks.json"), encoding="utf-8").read())

code, out = installer("--tool", "both", "--check")
check("проверка установки проходит", code == 0, out[-600:])
check("проверка смотрит метку и оглавление", "отдаёт метку" in out and "оглавление доходит" in out, out[-600:])
check("проверка смотрит потолок Codex", "потолок Codex" in out, out[-600:])

# отдельная проверка установки: та же, что после установки агентом по INSTALL.md
def kit_check(tool, *args):
    r = subprocess.run([sys.executable, os.path.join(home, tool, "scripts", "kit_check.py")] + list(args),
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    return r.returncode, r.stdout + r.stderr


code, out = kit_check(".claude", "--kit", KIT)
check("kit_check: чистая установка Claude проходит и сверена с набором", code == 0 and "совпадает с набором" in out, out[-900:])
code, out = kit_check(".codex", "--kit", KIT)
check("kit_check: чистая установка Codex проходит", code == 0, out[-900:])

src = io.open(os.path.join(KIT, "install.py"), encoding="utf-8").read()
chk = io.open(os.path.join(KIT, "scripts", "kit_check.py"), encoding="utf-8").read()
names = lambda text, var: re.findall(r'"([^"]+)"', re.search(var + r" = \[(.*?)\]", text, re.S).group(1))
check("kit_check и установщик знают один список файлов",
      names(src, "HOOK_FILES") == names(chk, "HOOK_FILES") and names(src, "SCRIPT_FILES") == names(chk, "SCRIPT_FILES"))

drift = os.path.join(home, ".claude", "scripts", "memory_search.py")
keep_drift = io.open(drift, encoding="utf-8").read()
write(drift, keep_drift + "\n# правка мимо набора\n")
code, out = kit_check(".claude", "--kit", KIT)
check("kit_check: файл поправили мимо набора — ловит", code != 0 and "[НЕТ] scripts/memory_search.py совпадает" in out, out[-900:])
write(drift, keep_drift)

sfile = os.path.join(home, ".claude", "settings.json")
keep_settings = io.open(sfile, encoding="utf-8").read()
write(sfile, keep_settings.replace("memory-recall-hook.py", "other-hook.py"))
code, out = kit_check(".claude")
check("kit_check: хук не прописан — ловит", code != 0 and "[НЕТ] PreToolUse" in out, out[-900:])
write(sfile, keep_settings)

# свои ключи настроек переживают переустановку
cfile = os.path.join(home, ".claude", "memory-kit.json")
cfg_now = json.load(io.open(cfile, encoding="utf-8"))
cfg_now.update({"recall_block": False, "sessions_dir": "D:/archive", "kit_dir": KIT})
write(cfile, json.dumps(cfg_now, ensure_ascii=False, indent=2))
installer("--tool", "claude")
cfg_after = json.load(io.open(cfile, encoding="utf-8"))
check("переустановка не теряет свои ключи настроек", cfg_after.get("recall_block") is False
      and cfg_after.get("sessions_dir") == "D:/archive" and cfg_after.get("kit_dir") == KIT, str(cfg_after))
code, out = kit_check(".claude")
check("kit_check берёт папку набора из настроек", code == 0 and "сверка с набором" in out, out[-900:])

# установка разошлась с набором: хук начала сессии говорит об этом сам
ctx = start_hook(proj)
check("файлы совпадают с набором: хук старта про расхождение молчит", "разошлась с набором" not in ctx, ctx[-300:])
write(drift, keep_drift + "\n# правка мимо набора\n")
ctx = start_hook(proj)
check("файл поправили на месте: хук старта называет его", "разошлась с набором" in ctx
      and "поправлены на месте" in ctx and "scripts/memory_search.py" in ctx, ctx[-400:])
write(drift, keep_drift)
old = time.time() - 86400
os.utime(drift, (old, old))
stale = os.path.join(home, ".claude", "hooks", "memory-recall-hook.py")
write(stale, "# старая версия\n")
os.utime(stale, (old, old))
ctx = start_hook(proj)
check("набор ушёл вперёд: хук старта называет отставший файл", "набор обновился" in ctx
      and "hooks/memory-recall-hook.py" in ctx, ctx[-400:])
installer("--tool", "claude")
check("после обновления хук старта снова молчит", "разошлась с набором" not in start_hook(proj))

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
