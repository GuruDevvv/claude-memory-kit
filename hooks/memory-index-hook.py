# -*- coding: utf-8 -*-
"""Автосборка оглавлений памяти — чтобы они не переполнялись и не устаревали.

Два режима:
  post  — после записи файла (PostToolUse: Write|Edit|MultiEdit): записали файл в папку
          памяти -> пересобрать оглавление этой папки. Строка, дописанная в MEMORY.md
          руками, переедет в файл правила.
  stop  — в конце хода (Stop): пересобрать все папки памяти (ловит то, что писали
          не через Write/Edit), обновить список проектов PROJECT_INDEX.md и забрать в архив
          разговоры законченных сессий, пока Claude Code их не стёр.
          Там же memory_upkeep.py: обрезка журналов проектов и проверка ссылок оглавлений.

Молчит, пока всё хорошо. Говорит, только если сборщик нашёл беду: потерю файла,
превышение лимита, собственное падение. Ошибка хука не роняет сессию.
"""
import json
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(BASE, "scripts")


def config():
    try:
        with open(os.path.join(BASE, "memory-kit.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def memory_dir(path, projects, central):
    """Папка памяти (общая или проекта), если путь внутри неё и она переведена на сборку, иначе None."""
    projects = os.path.normcase(os.path.abspath(projects))
    p = os.path.normcase(os.path.abspath(path))
    if central and p.startswith(os.path.normcase(os.path.abspath(central)) + os.sep):
        return central if os.path.exists(os.path.join(central, "MEMORY.head.md")) else None
    if not p.startswith(projects + os.sep):
        return None
    parts = p[len(projects) + 1:].split(os.sep)
    if len(parts) >= 3 and parts[1] == "memory":
        d = os.path.join(projects, parts[0], "memory")
        return d if os.path.exists(os.path.join(d, "MEMORY.head.md")) else None
    return None


def run(script, args):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, os.path.join(SCRIPTS, script)] + args,
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=60, env=env)
    return r.returncode, (r.stdout + r.stderr).strip()


def label_in_background():
    """Раз в сутки размечает новые реплики человека (поправка или нет) и обновляет отчёт reviews/metrics.md.
    Отдельным процессом, чтобы не задерживать конец хода. Тратит лимит Claude, поэтому включается
    только ключом "label": true."""
    import tempfile
    import time
    flag = os.path.join(tempfile.gettempdir(), "memory-kit-labeled-" + time.strftime("%Y%m%d"))
    if os.path.exists(flag):
        return
    try:
        open(flag, "w").close()
        flags = 0x00000008 | 0x00000200 | 0x08000000 if os.name == "nt" else 0     # без окна и без связи с хуком
        subprocess.Popen([sys.executable, os.path.join(SCRIPTS, "memory_report.py"), "--label", "--max", "200", "--write"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=flags, close_fds=True, env=dict(os.environ, PYTHONIOENCODING="utf-8"),
                         **({} if os.name == "nt" else {"start_new_session": True}))
    except Exception:
        pass


def already_said(text):
    """Этот набор битых ссылок уже показывали — второй раз ход не останавливаем."""
    import hashlib
    import tempfile
    mark = os.path.join(tempfile.gettempdir(), "memory-kit-links-" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:12])
    if os.path.exists(mark):
        return True
    open(mark, "w").close()
    return False


def main():
    cfg = config()
    if not cfg:
        return 0
    mode = sys.argv[1] if len(sys.argv) > 1 else "post"
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace") or "{}")
    except Exception:
        data = {}
    problems = []
    if mode == "stop":
        # архив разговоров — по пять законченных сессий за ход: не тормозит конец хода и за день забирает всё
        for script, args in (("memory_index.py", ["--all", "--quiet"]), ("project_index.py", ["--quiet"]),
                             ("session_archive.py", ["--max", "5", "--quiet"])):
            if script == "project_index.py" and cfg.get("project_index") is False:
                continue                     # список проектов собирает своё средство владельца
            code, out = run(script, args)
            if code != 0 and out:
                problems.append(out)
        if cfg.get("label") is True:
            label_in_background()
        # обрезка журналов и проверка ссылок оглавлений. Битые ссылки — единственное, о чём этот хук
        # останавливает конец хода: stderr хука Stop агенту не виден. Один раз на один и тот же набор
        code, out = run("memory_upkeep.py", ["--quiet"])
        if code == 1 and out and not already_said(out):
            print(json.dumps({"decision": "block", "reason": out + "\nПочини ссылки (файл переименован или удалён) "
                              "и закончи ход. Повторно об этих же ссылках не скажу."}, ensure_ascii=True))
        elif code not in (0, 1) and out:
            problems.append(out)
    else:
        d = memory_dir((data.get("tool_input") or {}).get("file_path") or "", cfg["projects_dir"], cfg.get("central"))
        if not d:
            return 0
        code, out = run("memory_index.py", [d, "--quiet"])
        if code != 0 and out:
            problems.append(out)
    if not problems:
        return 0
    msg = "Сборщик оглавления памяти сообщает о проблеме — разберись до конца хода:\n" + "\n".join(problems)
    if mode == "stop":
        sys.stderr.buffer.write((msg + "\n").encode("utf-8"))   # Stop не принимает добавку в контекст
    else:
        out = json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": msg}},
                         ensure_ascii=True)
        print(out)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:                   # хук не имеет права ронять работу
        sys.stderr.write("memory-index-hook: %s\n" % type(e).__name__)
        sys.exit(0)
