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

# снимок перед сжатием: копия записки, напоминание после сжатия — один раз
subprocess.run(["node", os.path.join(home, ".claude", "hooks", "session-start-context.js"), "precompact"],
               cwd=proj, capture_output=True, stdin=subprocess.DEVNULL)
snaps = os.path.join(home, ".claude", "handoff-snapshots", "Shop")
copies = [f for f in os.listdir(snaps) if f.startswith("HANDOFF-")] if os.path.isdir(snaps) else []
check("перед сжатием: копия записки лежит", len(copies) == 1
      and "записка магазина" in io.open(os.path.join(snaps, copies[0]), encoding="utf-8").read(), str(copies))
ctx = start_hook(proj)
check("после сжатия: напоминание и путь к снимку", "только что сжат" in ctx and copies and copies[0] in ctx, ctx[:400])
check("напоминание о сжатии — один раз", "только что сжат" not in start_hook(proj))
check("в другом проекте о чужом сжатии молчат", "только что сжат" not in start_hook(bare))

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

# подсказка по словам просьбы
write(os.path.join(central, "feedback", "feedback_slides.md"), "---\nname: sl\nmetadata:\n  type: feedback\n"
      "  index_line: \"Слайды кликами не править, собирать конвейером\"\n---\n\nпрезентация Google Slides: правка текста\n")
ctx = recall("prompt", {"session_id": sid, "cwd": proj, "prompt": "Поправь заголовок на третьем слайде"})
check("просьба про слайд приносит правило про слайды", "кликами не править" in ctx and not ctx.startswith("ОТКАЗ"), ctx[:300])
check("вторая просьба про слайды за сессию — без повтора",
      recall("prompt", {"session_id": sid, "cwd": proj, "prompt": "и ещё один слайд поправь"}) == "")
check("обычная просьба и команда со слэшем — без подсказки",
      recall("prompt", {"session_id": sid + "p", "cwd": proj, "prompt": "ок, делай"}) == ""
      and recall("prompt", {"session_id": sid + "p", "cwd": proj, "prompt": "/slides слайд"}) == "")
write(os.path.join(central, "ref", "word-hangs.md"), "---\nname: word\nmetadata:\n  type: reference\n"
      "  index_line: \"Word висит на docx из сети: открывать копию с диска\"\n---\n\nWord docx обход: скопировать локально\n")
ctx = recall("prompt", {"session_id": sid + "s", "cwd": proj, "prompt": "Word завис при открытии docx"})
check("«завис» приносит записку именно про эту программу", "Word висит" in ctx and "кликами" not in ctx, ctx[:300])
check("«завис» без знакомой программы — тишина", recall("prompt", {"session_id": sid + "s2", "cwd": proj,
      "prompt": "Фотошоп завис"}) == "")
check("слово «слайд» в команде оболочки подсказку не вызывает", recall("pre", {"session_id": sid + "p", "cwd": proj,
      "tool_name": "Bash", "tool_input": {"command": "echo слайд"}}) == "")

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


def ritual(lines, cwd=None, **extra):
    t = os.path.join(box, "transcript.jsonl")
    write(t, "\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n")
    r = subprocess.run(["node", os.path.join(home, ".claude", "hooks", "session-summary-check.js")], cwd=cwd or proj,
                       input=json.dumps(dict({"transcript_path": t, "session_id": "s1"}, **extra)),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return '"decision":"block"' in r.stdout


check("«стоп» без раздела «Промпт»: конец хода остановлен", ritual([U("привет"), A("да"), U("стоп"), A("сохраняю"), TOOL, A("Готово.")]))
check("«Стоп.» с точкой и большой буквы — тоже", ritual([U("Стоп."), A("Готово.")]))
check("«стоп» и раздел «Промпт» в ответе: пропускает", not ritual([U("стоп"), A("Итог\n\n## Промпт\n> продолжи")]))
check("ответ ещё не в записи, но пришёл во входе хука: пропускает",
      not ritual([U("стоп"), A("сохраняю"), TOOL], last_assistant_message="Итог\n\n## Промпт\n> продолжи"))
check("во входе хука ответ без «Промпта»: держит", ritual([U("стоп"), A("Готово.")], last_assistant_message="Готово."))
check("слово внутри фразы концом сессии не считается", not ritual([U("пока не трогай это"), A("хорошо")]))
check("обычный ход не трогает", not ritual([U("сделай кнопку"), A("сделал")]))
check("второй раз подряд не держит", not ritual([U("стоп"), A("Готово.")], stop_hook_active=True))
check("служебная вставка в сообщении человека не мешает",
      ritual([U("стоп\n<system-reminder>что-то служебное</system-reminder>"), A("Готово.")]))

# после «стоп» в репозитории текущего проекта не должно остаться незакоммиченного и незапушенного
def g(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=master"]
                          + list(args), cwd=cwd, capture_output=True, text=True)


server, repo = os.path.join(box, "server.git"), os.path.join(box, "repo")
g(box, "init", "--bare", server)
g(box, "clone", server, repo)
write(os.path.join(repo, "a.txt"), "1\n")
write(os.path.join(repo, "HANDOFF.md"), "# HANDOFF — Repo\n")
write(os.path.join(repo, "src", "keep.txt"), "k\n")
g(repo, "add", "a.txt", "HANDOFF.md", "src/keep.txt")
g(repo, "commit", "-m", "первый")
g(repo, "push", "-u", "origin", "master")
EDIT = {"type": "assistant", "message": {"role": "assistant", "content": [
    {"type": "tool_use", "name": "Edit", "input": {"file_path": os.path.join(repo, "a.txt")}}]}}
DONE = A("Итог\n\n## Промпт\n> продолжи")
check("git: всё запушено — «стоп» с «Промптом» проходит", not ritual([EDIT, U("стоп"), DONE], cwd=repo))
write(os.path.join(repo, "a.txt"), "2\n")
check("git: правленный файл без коммита — держит", ritual([EDIT, U("стоп"), DONE], cwd=repo))
write(os.path.join(repo, "junk.tmp"), "x\n")
g(repo, "commit", "-am", "второй")
check("git: коммит не запушен — держит", ritual([EDIT, U("стоп"), DONE], cwd=repo))
g(repo, "push")
check("git: новый неотслеживаемый файл не держит", not ritual([EDIT, U("стоп"), DONE], cwd=repo))
g(repo, "checkout", "-b", "feature")
write(os.path.join(repo, "b.txt"), "b\n")
g(repo, "add", "b.txt")
g(repo, "commit", "-m", "ветка")
g(repo, "checkout", "master")
check("git: ветка без копии на сервере — держит", ritual([EDIT, U("стоп"), DONE], cwd=repo))
check("git: без «стоп» репозитории не сверяет", not ritual([EDIT, U("сделай кнопку"), A("сделал")], cwd=repo))
check("git: второй раз подряд не держит", not ritual([EDIT, U("стоп"), DONE], cwd=repo, stop_hook_active=True))
check("git: правки в чужом репозитории при «стоп» в другом проекте не держат", not ritual([EDIT, U("стоп"), DONE]))
check("git: запуск из подпапки проекта сверяет его репозиторий",
      ritual([U("стоп"), DONE], cwd=os.path.join(repo, "src")))
g(repo, "push", "-u", "origin", "feature")
check("git: всё на сервере — снова проходит", not ritual([EDIT, U("стоп"), DONE], cwd=repo))

# сессия открыта в другом проекте, но сама коммитила в этот репозиторий
write(os.path.join(repo, "a.txt"), "3\n")
check("чужой репо: только незакоммиченное не держит", not ritual([EDIT, U("стоп"), DONE]))
g(repo, "commit", "-am", "из другой сессии")
check("чужой репо: свой незапушенный коммит держит", ritual([EDIT, U("стоп"), DONE]))
CMT = {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "name": "Bash", "input": {
    "command": 'cd "%s" && git add a.txt && git commit -m x' % repo.replace("\\", "/")}}]}}
check("чужой репо: найден по команде коммита", ritual([CMT, U("стоп"), DONE]))
check("чужой репо: без следов сессии в нём не держит", not ritual([U("стоп"), DONE]))
g(repo, "push")
check("чужой репо: после пуша проходит", not ritual([EDIT, U("стоп"), DONE]))

ctx = recall("subagent", {"session_id": sid, "cwd": os.path.join(proj, "src")})
check("помощник получает записку проекта и команду поиска", "записка магазина" in ctx and "memory_search.py" in ctx
      and len(ctx) <= 3000, ctx[:300])
settings = json.load(open(os.path.join(home, ".claude", "settings.json"), encoding="utf-8"))["hooks"]
# Codex пропускает к модели немного текста хука: на старте приходит главное и адрес полного оглавления
write(os.path.join(central, "MEMORY.md"), "# Оглавление\n\n## Всегда\n\n- говорить просто\n\n## Прочее\n\n"
      + "- длинная строка правила номер такой-то\n" * 400)
write(os.path.join(proj, "HANDOFF.md"), "# HANDOFF — Shop\nзаписка магазина\n" + "строка записки\n" * 600)
ctx = start_hook(proj, ".codex")
check("Codex: старт укладывается в потолок и несёт записку, «Всегда» и адрес оглавления",
      len(ctx) <= 5900 and "записка магазина" in ctx and "говорить просто" in ctx
      and "Прочитай его до начала работы" in ctx and "MEMORY.md" in ctx and "номер такой-то" not in ctx,
      "%d %s" % (len(ctx), ctx[-300:]))
ctx = start_hook(proj)
check("Claude: старт без потолка, оглавление целиком", "номер такой-то" in ctx and len(ctx) > 10000, len(ctx))

codex_hooks = json.load(open(os.path.join(home, ".codex", "hooks.json"), encoding="utf-8"))["hooks"]
check("хуки момента прописаны у Claude и у Codex", "SubagentStart" in settings
      and any("memory-recall-hook.py prompt" in h["command"] for g in settings["UserPromptSubmit"] for h in g["hooks"])
      and any("memory-recall-hook" in h["command"] for g in settings["PreToolUse"] for h in g["hooks"])
      and any("memory-recall-hook" in h["command"] and g["matcher"] == "Bash"
              for g in codex_hooks["PreToolUse"] for h in g["hooks"])
      and "UserPromptSubmit" in codex_hooks and "PostToolUse" not in codex_hooks)

code, out = installer("--tool", "both", "--check")
check("проверка установки проходит", code == 0, out[-600:])
check("проверка смотрит метку и оглавление", "отдаёт метку" in out and "доходит до агента" in out, out[-600:])
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

# конец хода: журнал обрезается до 5 записей, старое — в архив; битая ссылка оглавления останавливает ход один раз
def index_stop():
    r = subprocess.run([sys.executable, os.path.join(home, ".claude", "hooks", "memory-index-hook.py"), "stop"],
                       input="{}", capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    return r.stdout


cafe = os.path.join(central, "projects", "Cafe.md")
write(cafe, "# Журнал Cafe\n\n" + "".join("## [2026-10-%02d 10:00]\nзапись %d\n\n" % (20 - i, i) for i in range(8)))
old = time.time() - 5 * 86400
os.utime(cafe, (old, old))
index_stop()
kept = io.open(cafe, encoding="utf-8").read()
arch = os.path.join(central, "projects", "archive", "Cafe.md")
archived = io.open(arch, encoding="utf-8").read() if os.path.exists(arch) else ""
check("журнал: остались 5 свежих записей и вступление", kept.count("## [") == 5 and "запись 0" in kept
      and "запись 4" in kept and kept.startswith("# Журнал Cafe"), kept[:300])
check("журнал: 3 старые записи в архиве, ничего не потеряно", archived.count("## [") == 3 and "запись 7" in archived
      and "запись 5" in archived, archived[:300])
check("журнал: время правки после обрезки прежнее", abs(os.path.getmtime(cafe) - old) < 2)
head_file = os.path.join(central, "MEMORY.head.md")
write(head_file, io.open(head_file, encoding="utf-8").read() + "\n- [пропавшее](feedback/no_such_rule.md)\n")
for f in os.listdir(tempfile.gettempdir()):             # отметка «уже говорил» от прошлого прогона тестов
    if f.startswith("memory-kit-links-"):
        os.remove(os.path.join(tempfile.gettempdir(), f))
out = index_stop()
check("битая ссылка в оглавлении останавливает конец хода", '"block"' in out and "no_such_rule.md" in out, out[:300])
check("та же битая ссылка второй раз ход не останавливает", '"block"' not in index_stop())

# свой адрес журнала, журнал вниз, длинная записка, предупреждения проверки
cfg_file = os.path.join(home, ".claude", "memory-kit.json")
cfg_saved = io.open(cfg_file, encoding="utf-8").read()
cfg_new = json.loads(cfg_saved)
cfg_new["journal_path"] = "docs/{project}-log.md"
write(cfg_file, json.dumps(cfg_new, ensure_ascii=False))
log = os.path.join(proj, "docs", "Shop-log.md")
write(log, "# Мой журнал\n\n" + "".join("## [2026-10-%02d 10:00]\nмоя запись %d\n\n" % (d, d) for d in range(10, 16)))
ctx = start_hook(proj)
check("свой журнал: свежие записи при записи вниз", "моя запись 15" in ctx and "моя запись 13" in ctx and "моя запись 10" not in ctx, ctx[:400])
check("свой журнал: адрес назван, стандартный не читается", "Shop-log.md" in ctx and "запись журнала магазина" not in ctx, ctx[:400])
write(log, "просто заметки без заголовков\nпоследняя строка заметок\n")
ctx = start_hook(proj)
check("свой журнал без заголовков: конец файла", "последняя строка заметок" in ctx, ctx[:400])
past = time.time() - 3 * 86400
os.utime(log, (past, past))
check("свой журнал не тронут сегодня: напоминание называет его", "Shop-log.md" in stop_hook(proj))
os.utime(log, None)
check("свой журнал тронут сегодня: про журнал молчит", "в журнал сегодня" not in stop_hook(proj))
write(log, "# Мой журнал\n\n## [2026-10-20 10:00]\nзапись по словарю\n")
cfg_new["journal_path"] = {"Shop": "docs/{project}-log.md"}
write(cfg_file, json.dumps(cfg_new, ensure_ascii=False))
ctx = start_hook(proj)
check("свой журнал словарём: названный проект читает свой адрес", "запись по словарю" in ctx and "запись журнала магазина" not in ctx, ctx[:400])
r = subprocess.run([sys.executable, os.path.join(home, ".claude", "scripts", "kit_check.py")], cwd=proj,
                   capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
check("свой журнал словарём: проверка его видит", "свой журнал найден" in r.stdout, r.stdout[-600:])
cfg_new["journal_path"] = {"Другой": "docs/{project}-log.md"}
write(cfg_file, json.dumps(cfg_new, ensure_ascii=False))
ctx = start_hook(proj)
check("свой журнал словарём: остальные проекты на стандартном адресе", "запись журнала магазина" in ctx and "запись по словарю" not in ctx, ctx[:400])
write(cfg_file, cfg_saved)
ctx = start_hook(proj)
check("без ключа журнал на старом месте", "запись журнала магазина" in ctx and "Shop-log" not in ctx, ctx[:400])

note_path = os.path.join(proj, "HANDOFF.md")
note_saved = io.open(note_path, encoding="utf-8").read()
write(note_path, "# HANDOFF — Shop\nкоротко\n")
code, out = kit_check(".claude")
check("проверка: без причин предупреждений нет", "[!!]" not in out, out[-600:])
write(note_path, "# HANDOFF\n" + "длинно " * 1000)
ctx = start_hook(proj)
check("длинная записка: агенту сказано дочитать", "Прочитай HANDOFF.md целиком" in ctx, ctx[:300])
code, out = kit_check(".claude")
check("проверка: длинная записка — предупреждение, не поломка", code == 0 and "[!!] записки HANDOFF" in out and "Shop" in out, out[-600:])
write(note_path, note_saved)
rules_file = os.path.join(home, ".claude", "CLAUDE.md")
rules_saved = io.open(rules_file, encoding="utf-8").read()
write(rules_file, rules_saved.replace("memory-kit: хуки работают", "x").replace("memory_search", "x"))
code, out = kit_check(".claude")
check("проверка: нет метки и поиска в правилах — предупреждения", code == 0 and out.count("[!!] в правилах") == 2, out[-600:])
write(rules_file, rules_saved)

asc = os.path.join(central, "projects", "Asc.md")
asc_text = "# Журнал\n\n" + "".join("## [2026-10-%02d 10:00]\nзапись %d\n\n" % (d, d) for d in range(10, 18))
write(asc, asc_text)
subprocess.run([sys.executable, os.path.join(home, ".claude", "scripts", "memory_upkeep.py"), "--quiet"],
               capture_output=True, env=env)
check("чистка не архивирует свежее в журнале, который пишут вниз", io.open(asc, encoding="utf-8").read() == asc_text)
os.remove(asc)

# второй агент ставится к уже установленному: память общая, а не новая пустая
box2 = tempfile.mkdtemp(prefix="mkh2_")
home2, root2 = os.path.join(box2, "home"), os.path.join(box2, "work")
for d in (os.path.join(home2, ".claude"), os.path.join(home2, ".codex"), root2):
    os.makedirs(d)
mine = os.path.join(box2, "mymem")


def install2(*args):
    return subprocess.run([sys.executable, os.path.join(KIT, "install.py"), "--home", home2, "--root", root2] + list(args),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)


def cfg2(tool):
    return json.load(open(os.path.join(home2, tool, "memory-kit.json"), encoding="utf-8"))


def check2():
    return subprocess.run([sys.executable, os.path.join(home2, ".codex", "scripts", "kit_check.py")], cwd=root2,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)


install2("--tool", "claude", "--memory", mine)
write(os.path.join(home2, ".codex", "AGENTS.md"), "мои правила\n")
r = install2("--tool", "codex", "--dry-run")
check("второй агент: пробный прогон показывает ту же память", ".agent-memory" not in r.stdout and "mymem" in r.stdout, r.stdout[:400])
check("второй агент: пробный прогон ничего не пишет", not os.path.exists(os.path.join(home2, ".codex", "memory-kit.json")))
install2("--tool", "codex")
same = cfg2(".claude"), cfg2(".codex")
check("второй агент: память и папка проектов общие", same[0]["central"] == same[1]["central"]
      and same[0]["projects_dir"] == same[1]["projects_dir"], str(same[1]))
check("второй агент: вторая память не заведена", not os.path.exists(os.path.join(home2, ".agent-memory")))
check("второй агент: свои правила целы", "мои правила" in io.open(os.path.join(home2, ".codex", "AGENTS.md"), encoding="utf-8").read())
toml = io.open(os.path.join(home2, ".codex", "config.toml"), encoding="utf-8").read()
check("второй агент: Codex может писать в общую память", "mymem" in toml and ".claude/projects" in toml.replace("\\\\", "/"), toml)
r = check2()
check("проверка: память общая — проходит", r.returncode == 0 and "общая память та же" in r.stdout, r.stdout[-500:])
c = cfg2(".codex")
c["central"] = os.path.join(box2, "other")
write(os.path.join(home2, ".codex", "memory-kit.json"), json.dumps(c, ensure_ascii=False))
os.makedirs(c["central"])
r = check2()
check("проверка: память разная — ловит", r.returncode != 0 and "[НЕТ] общая память та же" in r.stdout, r.stdout[-500:])

# первая сборка над чужой памятью: план о ней говорит, копия есть, тексты целы
box3 = tempfile.mkdtemp(prefix="mkh3_")
home3, root3 = os.path.join(box3, "home"), os.path.join(box3, "work")
os.makedirs(root3)
mem3 = os.path.join(home3, ".claude", "projects", "own", "memory")
hand = "# Память\n\n- [Правило](rule.md) — моё правило\n"
write(os.path.join(mem3, "MEMORY.md"), hand)
write(os.path.join(mem3, "rule.md"), "---\nname: rule\n---\n\nтело правила\n")
write(os.path.join(home3, ".claude", "memory-kit.json"), json.dumps({"central": mem3, "projects_dir": os.path.join(home3, ".claude", "projects")}))


def install3(*args):
    return subprocess.run([sys.executable, os.path.join(KIT, "install.py"), "--home", home3, "--root", root3, "--tool", "codex"] + list(args),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)


r = install3("--dry-run")
check("первая сборка: план о ней говорит", "первая сборка оглавления" in r.stdout and "копия всей папки памяти" in r.stdout, r.stdout[:600])
check("первая сборка: пробный прогон копий не делает", os.listdir(os.path.dirname(mem3)) == ["memory"])
r = install3()
copies = [d for d in os.listdir(os.path.dirname(mem3)) if d.startswith("memory.bak-")]
check("первая сборка: копия памяти сделана", len(copies) == 1 and io.open(os.path.join(os.path.dirname(mem3), copies[0], "MEMORY.md"), encoding="utf-8").read() == hand, str(copies))
check("первая сборка: текст правила цел", "тело правила" in io.open(os.path.join(mem3, "rule.md"), encoding="utf-8").read())
r = install3()
check("повторная установка: новой копии нет", len([d for d in os.listdir(os.path.dirname(mem3)) if d.startswith("memory.bak-")]) == 1)

print(f"\nитого: {sum(results)} из {len(results)}")
sys.exit(0 if all(results) else 1)
