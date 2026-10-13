#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Установщик memory-kit — памяти между сессиями для Claude Code и Codex.

Что делает (и только это):
  * создаёт папку общей памяти и её подпапки, шапку оглавления;
  * копирует хуки и скрипты в папку агента (~/.claude и/или ~/.codex);
  * прописывает хуки в настройки агента, сохранив прежний файл рядом (.bak-дата);
  * дописывает раздел правил в общий файл правил агента и шаблон HANDOFF в файл правил
    корневой папки проектов — строго между метками memory-kit, остальное не трогает.

Запуск:
  python install.py --root <корневая папка проектов> --dry-run     # показать, что изменится
  python install.py --root <корневая папка проектов>               # установить
  python install.py --root <...> --project <папка проекта>          # и завести в проекте HANDOFF.md
  python install.py --root <...> --check                            # проверить установку
  python install.py --root <...> --uninstall                        # снять хуки и раздел правил

  --tool claude|codex|both   по умолчанию — те агенты, чьи папки уже есть в домашней папке
  --memory <папка>           где держать память; по умолчанию ~/.agent-memory
  --home <папка>             другая домашняя папка (для проверки в песочнице)

Память при --uninstall не удаляется никогда.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

KIT = os.path.dirname(os.path.abspath(__file__))
START, END = "<!-- memory-kit:start -->", "<!-- memory-kit:end -->"
HOOK_FILES = ["kit.js", "session-start-context.js", "session-summary-check.js", "memory-index-hook.py",
              "memory-recall-hook.py"]
SCRIPT_FILES = ["memory_index.py", "memory_search.py", "session_archive.py", "project_index.py", "kit_check.py",
                "memory_report.py",
                "find-zombies.ps1"]
MEMORY_DIRS = ["feedback", "ref", "projects", "plans", "research", "reviews"]
STAMP = time.strftime("%Y%m%d-%H%M%S")
HOOKS_ALIVE = "memory-kit: хуки работают"      # та же строка — в hooks/kit.js
CODEX_RULES_MAX = 32 * 1024                    # project_doc_max_bytes по умолчанию: дальше Codex файл правил не читает

TOOLS = {
    "claude": {"dir": ".claude", "hooks_file": "settings.json", "rules": "CLAUDE.md", "inject_project_index": False},
    # start_max_chars: Codex пропускает к модели около 6,5 тыс. знаков вывода хука, остальное вырезает
    "codex": {"dir": ".codex", "hooks_file": "hooks.json", "rules": "AGENTS.md", "inject_project_index": True,
              "start_max_chars": 5800},
}

AUTOLOAD = {
    "claude": (
        "Оглавление общей памяти (`MEMORY.md`) подкладывает в контекст хук начала сессии. Оглавление\n"
        "памяти проекта Claude Code подгружает сам. Файлы правил открывай по ссылкам из оглавления,\n"
        "когда задача их касается. Встроенная память Claude Code — это и есть память проекта: то, что\n"
        "касается одного проекта, сохраняй туда как обычно, а общее для всех проектов клади в общую\n"
        "память по правилам ниже. В общей памяти строку в `MEMORY.md` дописывать не нужно: сборщик\n"
        "сделает это сам."
    ),
    "codex": (
        "Оглавления памяти (`MEMORY.md` общей памяти и памяти проекта) подкладывает в контекст хук\n"
        "начала сессии. Файлы правил открывай по ссылкам из оглавления, когда задача их касается.\n"
        "Всё, что нужно запомнить, пиши файлами в папки выше по правилам ниже — так память\n"
        "остаётся общей для всех агентов на этой машине."
    ),
}

# Строка-метка должна совпадать с HOOKS_ALIVE в hooks/kit.js
HOOKCHECK_HEAD = (
    "Это правило выполняется раньше любого другого и раньше ответа на вопрос человека.\n"
    "Найди в контексте дословную строку `memory-kit: хуки работают` (её кладёт хук начала\n"
    "сессии). Строка есть — ничего не говори об этом и работай. Строки НЕТ — свой первый ответ\n"
    "в сессии, каким бы коротким ни был вопрос, начни с этого предупреждения дословно\n"
    "и только потом отвечай по делу:\n\n"
)
HOOKCHECK = {
    "claude": HOOKCHECK_HEAD + (
        "> ⚠️ Память memory-kit не подключена: хуки не сработали. Разреши мне проверить настройки\n"
        "> хуков в `settings.json`, потом открой новую сессию.\n\n"
        "Получив согласие, проверь, что хуки memory-kit прописаны в `settings.json` и что `node`\n"
        "запускается. Одного раза за сессию достаточно; в следующей сессии проверь заново."
    ),
    "codex": HOOKCHECK_HEAD + (
        "> ⚠️ Память memory-kit не подключена: Codex не запускает хуки, пока им не доверили.\n"
        "> Набери `/hooks`, подтверди хуки memory-kit и открой новую сессию.\n\n"
        "Сам подтвердить хуки ты не можешь, это делает только человек. Одного раза за сессию\n"
        "достаточно; в следующей сессии проверь заново."
    ),
}


def say(text=""):
    print(text)


def fwd(path):
    return path.replace("\\", "/")


def slug(path):
    """Имя папки памяти для пути — правило Claude Code: всё, кроме латинских букв и цифр, в дефис."""
    return re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(path))


def slug_dir(projects_dir, path):
    """Существующая папка с таким именем без учёта регистра (буква диска на Windows гуляет) или новая."""
    want = slug(path)
    if os.path.isdir(projects_dir):
        for name in os.listdir(projects_dir):
            if name.lower() == want.lower():
                return os.path.join(projects_dir, name)
    return os.path.join(projects_dir, want)


def read(path):
    with open(path, "rb") as fh:
        return fh.read().decode("utf-8-sig", errors="replace")


class Doer:
    """Все записи идут через него: в режиме --dry-run только печатает."""

    def __init__(self, dry):
        self.dry = dry
        self.changed = 0

    def mkdir(self, path):
        if not os.path.isdir(path):
            say("  + папка   " + fwd(path))
            self.changed += 1
            if not self.dry:
                os.makedirs(path, exist_ok=True)

    def write(self, path, text, backup=True):
        old = read(path) if os.path.exists(path) else None
        if old is not None and old.replace("\r\n", "\n") == text.replace("\r\n", "\n"):
            return False
        say("  %s файл    %s" % ("~" if old is not None else "+", fwd(path)))
        self.changed += 1
        if self.dry:
            return True
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if old is not None and backup:
            shutil.copy2(path, path + ".bak-" + STAMP)
            say("    прежний сохранён: " + fwd(path) + ".bak-" + STAMP)
        with open(path, "wb") as fh:
            fh.write(text.encode("utf-8"))
        return True

    def copy(self, src, dst):
        with open(src, "rb") as fh:
            data = fh.read()
        if os.path.exists(dst):
            with open(dst, "rb") as fh:
                if fh.read() == data:
                    return
        say("  %s файл    %s" % ("~" if os.path.exists(dst) else "+", fwd(dst)))
        self.changed += 1
        if self.dry:
            return
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.exists(dst):
            shutil.copy2(dst, dst + ".bak-" + STAMP)
        with open(dst, "wb") as fh:
            fh.write(data)

    def remove(self, path):
        if os.path.exists(path):
            say("  - файл    " + fwd(path))
            self.changed += 1
            if not self.dry:
                os.remove(path)


# ---------- хуки в настройках агента ----------

def quote(path, tool):
    p = fwd(path)
    if " " not in p:
        return p
    return ("'%s'" if tool == "codex" else '"%s"') % p


def python_cmd():
    exe = sys.executable or ""
    if exe and " " not in exe:
        return fwd(exe)
    return "python" if os.name == "nt" else "python3"


def wanted_hooks(tool, tool_dir):
    hooks = os.path.join(tool_dir, "hooks")
    node = lambda name: "node " + quote(os.path.join(hooks, name), tool)
    py = lambda name, arg: "%s %s %s" % (python_cmd(), quote(os.path.join(hooks, name), tool), arg)
    out = [
        ("SessionStart", "", {"type": "command", "command": node("session-start-context.js")}),
        ("Stop", "", {"type": "command", "command": node("session-summary-check.js")}),
        ("Stop", "", {"type": "command", "command": py("memory-index-hook.py", "stop"), "timeout": 60}),
    ]
    if tool == "claude":      # у Codex правка файлов идёт другим инструментом; там хватает сборки в конце хода
        out.append(("PostToolUse", "Write|Edit|MultiEdit",
                    {"type": "command", "command": py("memory-index-hook.py", "post"), "timeout": 30}))
    # память в нужный момент: правила перед коммитом и сервером, по словам просьбы, короткий контекст помощнику.
    # Codex зовёт команды оболочки именем Bash и принимает на этих событиях и отказ, и добавку в контекст
    # (его документация по хукам, сверено 13.10.2026)
    out.append(("PreToolUse", "Bash|PowerShell" if tool == "claude" else "Bash",
                {"type": "command", "command": py("memory-recall-hook.py", "pre"), "timeout": 10}))
    out.append(("UserPromptSubmit", "",
                {"type": "command", "command": py("memory-recall-hook.py", "prompt"), "timeout": 10}))
    out.append(("SubagentStart", "",
                {"type": "command", "command": py("memory-recall-hook.py", "subagent"), "timeout": 10}))
    return out


def hook_key(command):
    """По чему узнаём свой хук в чужих настройках: имя скрипта + режим."""
    for name in HOOK_FILES:
        if name in command:
            mode = next((m for m in ("stop", "post", "pre", "subagent") if command.rstrip().endswith(" " + m)), "")
            return name + ":" + mode
    return None


def merge_hooks(settings, tool, tool_dir):
    hooks = settings.setdefault("hooks", {})
    for event, matcher, entry in wanted_hooks(tool, tool_dir):
        key = hook_key(entry["command"])
        groups = hooks.setdefault(event, [])
        found = False
        for g in groups:
            for i, h in enumerate(g.get("hooks", [])):
                if hook_key(h.get("command", "")) == key:
                    g["hooks"][i] = entry
                    found = True
        if found:
            continue
        target = next((g for g in groups if g.get("matcher", "") == matcher), None)
        if target is None:
            target = {"matcher": matcher, "hooks": []}
            groups.append(target)
        target.setdefault("hooks", []).append(entry)
    return settings


def allow_memory_dir(settings, memory_home, home):
    """Claude Code: общая память лежит вне папки проекта — без этого каждая запись в неё требует разрешения."""
    perm = settings.setdefault("permissions", {})
    dirs = perm.setdefault("additionalDirectories", [])
    if not any(os.path.normcase(os.path.abspath(d)) == os.path.normcase(memory_home) for d in dirs):
        dirs.append(memory_home)
    rel = os.path.relpath(memory_home, home)
    if not rel.startswith(".."):                     # правило можно записать от домашней папки
        allow = perm.setdefault("allow", [])
        for rule in ("Edit(~/%s/**)" % fwd(rel), "Read(~/%s/**)" % fwd(rel)):
            if rule not in allow:
                allow.append(rule)
    return settings


def codex_writable_note(doer, tool_dir, roots):
    """Codex: то же право выдаётся в config.toml. Чужой файл настроек правим, только если раздела ещё нет."""
    path = os.path.join(tool_dir, "config.toml")
    text = read(path).replace("\r\n", "\n") if os.path.exists(path) else ""
    flat = fwd(text).replace("//", "/")
    roots = [fwd(r) for r in roots if fwd(r) not in flat]
    if not roots:
        return
    if re.search(r"^\[sandbox_workspace_write\]", text, re.M):
        say("  ! в config.toml уже есть раздел [sandbox_workspace_write] — допиши в его writable_roots:")
        for r in roots:
            say("    " + r)
        return
    block = ("\n# memory-kit: память лежит вне папки проекта, агенту нужно право писать в неё\n"
             "[sandbox_workspace_write]\nwritable_roots = [%s]\n" % ", ".join('"%s"' % r for r in roots))
    doer.write(path, text.rstrip("\n") + "\n" + block if text else block.lstrip("\n"))


def strip_hooks(settings):
    hooks = settings.get("hooks", {})
    for event in list(hooks):
        for g in hooks[event]:
            g["hooks"] = [h for h in g.get("hooks", []) if not hook_key(h.get("command", ""))]
        hooks[event] = [g for g in hooks[event] if g.get("hooks")]
        if not hooks[event]:
            del hooks[event]
    return settings


def load_json(path):
    if not os.path.exists(path):
        return {}
    try:
        return json.loads(read(path) or "{}")
    except ValueError as e:
        raise SystemExit("Не могу разобрать %s (%s). Ничего не тронул — поправь файл и запусти снова." % (fwd(path), e))


def dump_json(data):
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


# ---------- раздел правил между метками ----------

def put_block(doer, path, block, title):
    block = START + "\n" + block.strip("\n") + "\n" + END
    if os.path.exists(path):
        text = read(path).replace("\r\n", "\n")
        if START in text and END in text:
            new = text[:text.index(START)] + block + text[text.index(END) + len(END):]
        else:
            new = text.rstrip("\n") + "\n\n" + block + "\n"
    else:
        new = "# " + title + "\n\n" + block + "\n"
    doer.write(path, new)


def cut_block(doer, path):
    if not os.path.exists(path):
        return
    text = read(path).replace("\r\n", "\n")
    if START in text and END in text:
        new = (text[:text.index(START)].rstrip("\n") + "\n" + text[text.index(END) + len(END):].lstrip("\n")).rstrip("\n") + "\n"
        doer.write(path, new)


def render(name, values):
    text = read(os.path.join(KIT, "templates", name)).replace("\r\n", "\n")
    for k, v in values.items():
        text = text.replace("{{" + k + "}}", v)
    return text


def zombies_text(tool_dir):
    if os.name == "nt":
        return ("Запусти `powershell -NoProfile -File %s`.\n"
                "   Он ищет серверы разработки и фоновые команды, которые остались от прошлых сессий без\n"
                "   живого агента-родителя. Найденное покажи списком и спроси, что остановить (флаг `-Kill`).\n"
                "   Сам ничего не убивай." % fwd(os.path.join(tool_dir, "scripts", "find-zombies.ps1")))
    return ("Проверь через `ps`, не остались ли запущенные в этой сессии серверы разработки\n"
            "   и фоновые команды. Найденное покажи списком и спроси, что остановить. Сам ничего не убивай.")


# ---------- действия ----------

def layout(args):
    home = os.path.abspath(os.path.expanduser(args.home or "~"))
    root = os.path.abspath(os.path.expanduser(args.root))
    if args.tool == "both":
        tools = ["claude", "codex"]
    elif args.tool:
        tools = [args.tool]
    else:
        tools = [t for t in TOOLS if os.path.isdir(os.path.join(home, TOOLS[t]["dir"]))]
        if not tools:
            raise SystemExit("В %s нет ни .claude, ни .codex. Запусти агента хотя бы раз или укажи --tool." % fwd(home))
    memory_home = os.path.abspath(os.path.expanduser(args.memory)) if args.memory else os.path.join(home, ".agent-memory")
    if os.sep + ".claude" + os.sep in memory_home + os.sep:
        raise SystemExit("Общую память нельзя класть внутрь папки .claude: Claude Code считает файлы там\n"
                         "чувствительными и на каждую запись из проекта требует подтверждения. Выбери другую папку.")
    # Общая память — вне папок агентов. Память проекта у Claude Code своя, встроенная; ею же пользуется Codex.
    central = os.path.join(memory_home, "central")
    if "claude" in tools:
        projects_dir = os.path.join(home, ".claude", "projects")
    else:
        projects_dir = os.path.join(memory_home, "projects")
    return home, root, tools, projects_dir, central, memory_home


def install(args):
    home, root, tools, projects_dir, central, memory_home = layout(args)
    if not os.path.isdir(root):
        raise SystemExit("Корневой папки проектов нет: " + fwd(root))
    doer = Doer(args.dry_run)
    say("memory-kit: %s" % ("пробный прогон, ничего не пишу" if args.dry_run else "установка"))
    say("  агенты:        " + ", ".join(tools))
    say("  корень:        " + fwd(root))
    say("  общая память:  " + fwd(central))
    say("  память проектов: " + fwd(projects_dir) + "/<имя>/memory")
    say()
    for need in ("node", "git"):
        if not shutil.which(need):
            say("  ! не найден %s — хук начала сессии без него работать не будет" % need)

    say("Память:")
    for d in MEMORY_DIRS:
        doer.mkdir(os.path.join(central, d))
    head = os.path.join(central, "MEMORY.head.md")
    fresh = not os.path.exists(head)
    if fresh:
        doer.write(head, render("MEMORY.head.md", {}), backup=False)
        feedback = os.path.join(central, "feedback")
        if not os.path.isdir(feedback) or not os.listdir(feedback):
            doer.copy(os.path.join(KIT, "templates", "feedback_example.md"),
                      os.path.join(feedback, "feedback_example.md"))

    for tool in tools:
        spec = TOOLS[tool]
        tool_dir = os.path.join(home, spec["dir"])
        say()
        say("%s (%s):" % (tool, fwd(tool_dir)))
        for name in HOOK_FILES:
            doer.copy(os.path.join(KIT, "hooks", name), os.path.join(tool_dir, "hooks", name))
        for name in SCRIPT_FILES:
            doer.copy(os.path.join(KIT, "scripts", name), os.path.join(tool_dir, "scripts", name))
        cfg = {"tool": tool, "root": root, "projects_dir": projects_dir, "central": central,
               "inject_index": True, "inject_project_index": spec["inject_project_index"],
               "sync_on_start": True, "journal_entries": 3}
        if spec.get("start_max_chars"):
            cfg["start_max_chars"] = spec["start_max_chars"]
        cfg_path = os.path.join(tool_dir, "memory-kit.json")
        if os.path.exists(cfg_path):                 # ручные правки настроек переживают переустановку
            try:
                kept = json.loads(read(cfg_path))
                for k, v in kept.items():            # всё, кроме адресов: их задаёт этот запуск
                    if k not in ("tool", "root", "projects_dir", "central"):
                        cfg[k] = v
            except ValueError:
                pass
        doer.write(cfg_path, dump_json(cfg), backup=False)
        hooks_path = os.path.join(tool_dir, spec["hooks_file"])
        settings = merge_hooks(load_json(hooks_path), tool, tool_dir)
        if tool == "claude":
            settings = allow_memory_dir(settings, memory_home, home)
        doer.write(hooks_path, dump_json(settings))
        if tool == "codex":
            codex_writable_note(doer, tool_dir, [memory_home] + ([projects_dir] if "claude" in tools else []))
        root_rules = os.path.join(root, spec["rules"])
        values = {"CENTRAL": fwd(central), "PROJECTS_DIR": fwd(projects_dir), "ROOT": fwd(root),
                  "ROOT_RULES": fwd(root_rules), "AUTOLOAD": AUTOLOAD[tool], "HOOKCHECK": HOOKCHECK[tool], "ZOMBIES": zombies_text(tool_dir),
                  "SEARCH": "%s %s" % (python_cmd(), fwd(os.path.join(tool_dir, "scripts", "memory_search.py")))}
        put_block(doer, os.path.join(tool_dir, spec["rules"]), render("instructions.md", values), "Общие правила")
        put_block(doer, root_rules, render("root-rules.md", values), os.path.basename(root) + " — правила")
        if tool == "codex" and not args.dry_run:
            for path in (os.path.join(tool_dir, spec["rules"]), root_rules):
                size = os.path.getsize(path)
                if size > CODEX_RULES_MAX:
                    say("  ! %s весит %d байт, а Codex читает только первые %d: конец файла он не увидит."
                        % (fwd(path), size, CODEX_RULES_MAX))
                    say("    Сократи файл или подними project_doc_max_bytes в config.toml.")
                elif size > CODEX_RULES_MAX * 0.9:
                    say("  ! %s: %d из %d байт, до потолка Codex осталось %d. Дальше он молча обрежет конец."
                        % (fwd(path), size, CODEX_RULES_MAX, CODEX_RULES_MAX - size))

    for project in args.project or []:
        project = os.path.abspath(os.path.expanduser(project))
        handoff = os.path.join(project, "HANDOFF.md")
        say()
        say("Проект %s:" % fwd(project))
        if not os.path.isdir(project):
            say("  ! папки нет, пропуск")
        elif os.path.exists(handoff):
            say("  HANDOFF.md уже есть, не трогаю")
        else:
            doer.write(handoff, render("HANDOFF.md", {"PROJECT": os.path.basename(project),
                                                     "NOW": time.strftime("%Y-%m-%d %H:%M")}))

    say()
    if args.dry_run:
        say("Изменений было бы: %d. Запусти без --dry-run, чтобы установить." % doer.changed)
        return 0
    first = os.path.join(home, TOOLS[tools[0]]["dir"], "scripts")
    for script, extra in (("memory_index.py", [central]), ("project_index.py", [])):
        r = subprocess.run([sys.executable, os.path.join(first, script)] + extra,
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        say("  " + (r.stdout + r.stderr).strip().replace("\n", "\n  "))
    say()
    say("Готово, изменений: %d. Дальше:" % doer.changed)
    say("  1. Проверь установку:  python install.py --root %s --check" % quote(root, "claude"))
    say("  2. Открой НОВУЮ сессию агента в папке проекта, где есть HANDOFF.md, и спроси «что у нас с проектом?»")
    if "codex" in tools:
        say("  3. В Codex набери /hooks и подтверди новые хуки: пока им не доверили, Codex их пропускает.")
        say("     Забудешь — агент сам напомнит об этом в начале сессии")
    return 0


def uninstall(args):
    home, root, tools, projects_dir, central, memory_home = layout(args)
    doer = Doer(args.dry_run)
    say("memory-kit: снимаю хуки и раздел правил. Память не трогаю: " + fwd(central))
    for tool in tools:
        spec = TOOLS[tool]
        tool_dir = os.path.join(home, spec["dir"])
        hooks_path = os.path.join(tool_dir, spec["hooks_file"])
        if os.path.exists(hooks_path):
            doer.write(hooks_path, dump_json(strip_hooks(load_json(hooks_path))))
        cut_block(doer, os.path.join(tool_dir, spec["rules"]))
        cut_block(doer, os.path.join(root, spec["rules"]))
        doer.remove(os.path.join(tool_dir, "memory-kit.json"))
    say("Изменений: %d. Файлы хуков и скриптов оставлены на месте — без настроек они ничего не делают." % doer.changed)
    return 0


def check(args):
    home, root, tools, projects_dir, central, memory_home = layout(args)
    bad = 0

    def line(ok, text):
        nonlocal bad
        bad += 0 if ok else 1
        say("  [%s] %s" % ("ок" if ok else "НЕТ", text))

    line(os.path.exists(os.path.join(central, "MEMORY.head.md")), "шапка оглавления общей памяти")
    for tool in tools:
        spec = TOOLS[tool]
        tool_dir = os.path.join(home, spec["dir"])
        say("%s:" % tool)
        line(os.path.exists(os.path.join(tool_dir, "memory-kit.json")), "настройки memory-kit.json")
        for name in HOOK_FILES:
            line(os.path.exists(os.path.join(tool_dir, "hooks", name)), "хук " + name)
        registered = json.dumps(load_json(os.path.join(tool_dir, spec["hooks_file"])).get("hooks", {}))
        for event, _, entry in wanted_hooks(tool, tool_dir):
            line(hook_key(entry["command"]).split(":")[0] in registered, "хук прописан: " + event)
        rules = os.path.join(tool_dir, spec["rules"])
        line(os.path.exists(rules) and START in read(rules), "раздел правил в " + spec["rules"])
        if shutil.which("node"):
            r = subprocess.run(["node", os.path.join(tool_dir, "hooks", "session-start-context.js")],
                               cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace")
            try:                                     # пустой ответ — не успех: хук обязан отдать метку и оглавление
                ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
            except (ValueError, KeyError, TypeError):
                ctx = ""
            line(r.returncode == 0 and HOOKS_ALIVE in ctx, "хук начала сессии отдаёт метку «%s»" % HOOKS_ALIVE)
            index = os.path.join(central, "MEMORY.md")
            if os.path.exists(index) and read(index).strip():
                # в тесном режиме (Codex) вместо оглавления приходит его адрес
                line(read(index).replace("\r\n", "\n").strip()[:200] in ctx.replace("\r\n", "\n")
                     or fwd(index) in ctx,
                     "общее оглавление или его адрес доходит до агента (контекст %d знаков)" % len(ctx))
        if tool == "codex":
            for path in (rules, os.path.join(root, spec["rules"])):
                if os.path.exists(path):
                    line(os.path.getsize(path) <= CODEX_RULES_MAX, "%s влезает в потолок Codex: %d из %d байт"
                         % (fwd(path), os.path.getsize(path), CODEX_RULES_MAX))
        r = subprocess.run([sys.executable, os.path.join(tool_dir, "scripts", "memory_index.py"), central, "--dry"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        line(r.returncode == 0, "сборщик оглавления: " + (r.stdout + r.stderr).strip()[:200])
    say()
    say("Всё на месте." if not bad else "Не хватает пунктов: %d. Запусти установку ещё раз." % bad)
    return 1 if bad else 0


def main():
    for stream in (sys.stdout, sys.stderr):          # чужая консоль может быть не в UTF-8
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Установщик memory-kit: память между сессиями для Claude Code и Codex")
    ap.add_argument("--root", required=True, help="корневая папка, в которой лежат проекты")
    ap.add_argument("--tool", choices=["claude", "codex", "both"], help="для какого агента ставить")
    ap.add_argument("--project", action="append", help="папка проекта, где завести HANDOFF.md (можно несколько раз)")
    ap.add_argument("--memory", help="где держать память (по умолчанию ~/.agent-memory)")
    ap.add_argument("--home", help="другая домашняя папка (для проверки в песочнице)")
    ap.add_argument("--dry-run", action="store_true", help="только показать, что изменится")
    ap.add_argument("--check", action="store_true", help="проверить установку")
    ap.add_argument("--uninstall", action="store_true", help="снять хуки и раздел правил (память остаётся)")
    args = ap.parse_args()
    if args.check:
        return check(args)
    if args.uninstall:
        return uninstall(args)
    return install(args)


if __name__ == "__main__":
    sys.exit(main())
