#!/usr/bin/env python3
"""Проверка установки memory-kit. Ничего не меняет, только смотрит и отвечает «да» или «нет».

Ставил ли набор install.py или агент по INSTALL.md — итог проверяется одинаково, этим скриптом.
Настройки берутся из memory-kit.json рядом с папкой scripts, то есть из папки агента.

  python kit_check.py                     # проверить установку, в которой лежит этот файл
  python kit_check.py --kit <папка набора>   # заодно сверить файлы с набором байт в байт
  python kit_check.py --agent-dir ~/.codex   # проверить другую папку агента

Папку набора можно один раз записать в настройки ключом "kit_dir": тогда сверка идёт всегда.
Код возврата 0 — всё на месте, 1 — есть пункты «НЕТ».
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

# Те же списки, что в install.py; совпадение сторожит tests/test_hooks.py
HOOK_FILES = ["kit.js", "session-start-context.js", "session-summary-check.js", "memory-index-hook.py",
              "memory-recall-hook.py"]
SCRIPT_FILES = ["memory_index.py", "memory_search.py", "session_archive.py", "project_index.py", "kit_check.py",
                "memory_report.py", "memory_upkeep.py",
                "find-zombies.ps1"]
HOOKS_ALIVE = "memory-kit: хуки работают"
CODEX_RULES_MAX = 32 * 1024
HANDOFF_MAX_CHARS = 5000                       # те же бюджеты, что в hooks/session-start-context.js
HANDOFF_TIGHT_SHARE = 0.38

TOOLS = {
    "claude": {"hooks_file": "settings.json", "rules": "CLAUDE.md"},
    "codex": {"hooks_file": "hooks.json", "rules": "AGENTS.md"},
}
# событие, файл хука, режим (последнее слово команды)
WANTED = {
    "claude": [("SessionStart", "session-start-context.js", ""), ("Stop", "session-summary-check.js", ""),
               ("Stop", "memory-index-hook.py", "stop"), ("PostToolUse", "memory-index-hook.py", "post"),
               ("PreToolUse", "memory-recall-hook.py", "pre"), ("SubagentStart", "memory-recall-hook.py", "subagent"),
               ("UserPromptSubmit", "memory-recall-hook.py", "prompt"),
               ("PreCompact", "session-start-context.js", "precompact")],
    "codex": [("SessionStart", "session-start-context.js", ""), ("Stop", "session-summary-check.js", ""),
              ("Stop", "memory-index-hook.py", "stop"),
              ("PreToolUse", "memory-recall-hook.py", "pre"), ("SubagentStart", "memory-recall-hook.py", "subagent"),
              ("UserPromptSubmit", "memory-recall-hook.py", "prompt")],
}


def say(text=""):
    print(text)


def read(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def same_bytes(a, b):
    """Файлы совпадают; перевод строк не в счёт (git на Windows меняет его сам)."""
    with open(a, "rb") as fa, open(b, "rb") as fb:
        return fa.read().replace(b"\r\n", b"\n") == fb.read().replace(b"\r\n", b"\n")


def commands(settings, event):
    out = []
    for group in (settings.get("hooks") or {}).get(event) or []:
        for h in group.get("hooks") or []:
            out.append(h.get("command") or "")
    return out


def norm(path):
    return os.path.normcase(os.path.abspath(path)).replace("\\", "/")


def run(cmd, cwd=None, stdin=None):
    return subprocess.run(cmd, cwd=cwd, input=stdin, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=dict(os.environ, PYTHONIOENCODING="utf-8"))


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Проверка установки memory-kit")
    ap.add_argument("--agent-dir", help="папка агента (~/.claude или ~/.codex); по умолчанию та, где лежит этот файл")
    ap.add_argument("--kit", help="папка набора: сверить установленные файлы с ней")
    args = ap.parse_args()

    agent_dir = os.path.abspath(os.path.expanduser(args.agent_dir)) if args.agent_dir else \
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bad = 0
    warned = 0

    def line(ok, text):
        nonlocal bad
        bad += 0 if ok else 1
        say("  [%s] %s" % ("ок" if ok else "НЕТ", text))

    def warn(ok, text):
        """Установка цела, но агент получит не всё: в итог «НЕТ» не идёт."""
        nonlocal warned
        warned += 0 if ok else 1
        say("  [%s] %s" % ("ок" if ok else "!!", text))

    say("memory-kit: проверка %s" % agent_dir.replace("\\", "/"))
    cfg_path = os.path.join(agent_dir, "memory-kit.json")
    try:
        cfg = json.loads(read(cfg_path))
    except (OSError, ValueError) as e:
        line(False, "настройки memory-kit.json читаются (%s)" % type(e).__name__)
        say()
        say("Без настроек хуки молча ничего не делают. Дальше проверять нечего.")
        return 1
    line(True, "настройки memory-kit.json читаются")

    tool = cfg.get("tool")
    line(tool in TOOLS, "в настройках назван агент: %s" % tool)
    if tool not in TOOLS:
        return 1
    for key in ("root", "central"):
        value = cfg.get(key)
        line(bool(value) and os.path.isdir(os.path.expanduser(value)), "папка %s на месте: %s" % (key, value))
    line(bool(cfg.get("projects_dir")), "в настройках есть projects_dir: %s" % cfg.get("projects_dir"))   # самой папки до первой сессии может не быть
    central = os.path.expanduser(cfg.get("central") or "")
    root = os.path.expanduser(cfg.get("root") or "")
    line(os.path.exists(os.path.join(central, "MEMORY.head.md")), "шапка оглавления общей памяти MEMORY.head.md")

    for other in TOOLS:
        other_cfg = os.path.join(os.path.dirname(agent_dir), "." + other, "memory-kit.json")
        if other != tool and os.path.exists(other_cfg):
            try:
                theirs = json.loads(read(other_cfg)).get("central") or ""
            except ValueError:
                theirs = ""
            if theirs:
                line(norm(os.path.expanduser(theirs)) == norm(central),
                     "общая память та же, что у %s: %s" % (other, theirs))

    kit = args.kit or cfg.get("kit_dir")
    kit = os.path.abspath(os.path.expanduser(kit)) if kit else None
    if kit and not os.path.isdir(os.path.join(kit, "hooks")):
        line(False, "папка набора на месте: %s" % kit)
        kit = None
    say("Файлы%s:" % (" (сверка с набором %s)" % kit.replace("\\", "/") if kit else ""))
    for sub, names in (("hooks", HOOK_FILES), ("scripts", SCRIPT_FILES)):
        for name in names:
            mine = os.path.join(agent_dir, sub, name)
            if not os.path.exists(mine):
                line(False, "%s/%s есть" % (sub, name))
            elif kit and os.path.exists(os.path.join(kit, sub, name)):
                line(same_bytes(mine, os.path.join(kit, sub, name)), "%s/%s совпадает с набором" % (sub, name))
            else:
                line(True, "%s/%s есть" % (sub, name))

    say("Хуки прописаны (%s):" % TOOLS[tool]["hooks_file"])
    try:
        settings = json.loads(read(os.path.join(agent_dir, TOOLS[tool]["hooks_file"])))
    except (OSError, ValueError):
        settings = {}
    for event, name, mode in WANTED[tool]:
        found = [c for c in commands(settings, event)
                 if name in c and (not mode or c.rstrip().endswith(" " + mode))]
        here = norm(os.path.join(agent_dir, "hooks", name))
        ours = [c for c in found
                if here.lower() in c.replace("\\", "/").replace("'", "").replace('"', "").lower()]
        label = "%s → %s%s" % (event, name, " " + mode if mode else "")
        if found and not ours:
            line(False, label + " (прописан, но ведёт в другую папку: %s)" % found[0][:120])
        else:
            line(bool(found), label)

    say("Хуки отвечают:")
    if shutil.which("node"):
        r = run(["node", os.path.join(agent_dir, "hooks", "session-start-context.js")], cwd=root or None, stdin="{}")
        try:
            ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        except (ValueError, KeyError, TypeError):
            ctx = ""
        line(r.returncode == 0 and HOOKS_ALIVE in ctx, "хук начала сессии отдаёт метку «%s»" % HOOKS_ALIVE)
    else:
        line(False, "node установлен (без него хук начала сессии не работает)")
    r = run([sys.executable, os.path.join(agent_dir, "scripts", "memory_index.py"), central, "--dry"])
    line(r.returncode == 0, "сборщик оглавления: " + (r.stdout + r.stderr).strip()[:160])
    r = run([sys.executable, os.path.join(agent_dir, "scripts", "memory_search.py"), "проверка"])
    line(r.returncode == 0, "поиск по памяти запускается")
    probe = json.dumps({"session_id": "kit-check", "cwd": root, "tool_name": "Bash", "tool_input": {"command": "echo 1"}})
    r = run([sys.executable, os.path.join(agent_dir, "hooks", "memory-recall-hook.py"), "pre"], stdin=probe)
    line(r.returncode == 0 and not r.stdout.strip(), "хук подсказки молчит на обычной команде")

    if tool == "codex":
        say("Правила Codex:")
        limit = CODEX_RULES_MAX
        try:
            m = re.search(r"^\s*project_doc_max_bytes\s*=\s*(\d+)", read(os.path.join(agent_dir, "config.toml")), re.M)
            if m:
                limit = int(m.group(1))
        except OSError:
            pass
        total = 0
        for path in (os.path.join(agent_dir, "AGENTS.md"), os.path.join(root, "AGENTS.md")):
            if os.path.exists(path):
                total += os.path.getsize(path)
        line(total <= limit, "файлы правил влезают в потолок Codex: %d из %d байт" % (total, limit))

    say("Что агент получит на старте:")
    rules_text = ""
    for path in (os.path.join(agent_dir, TOOLS[tool]["rules"]), os.path.join(root, TOOLS[tool]["rules"])):
        if os.path.exists(path):
            rules_text += read(path)
    warn(HOOKS_ALIVE in rules_text, "в правилах агента есть проверка метки «%s»: без неё он не скажет, что хуки молчат"
         % HOOKS_ALIVE)
    warn("memory_search" in rules_text, "в правилах агента есть команда поиска по памяти")

    tight = int(cfg.get("start_max_chars") or 0)
    limit = round(tight * HANDOFF_TIGHT_SHARE) if tight else HANDOFF_MAX_CHARS
    projects = [root] + [os.path.join(root, n) for n in sorted(os.listdir(root))] if os.path.isdir(root) else []
    long_notes = []
    for p in projects:
        note = os.path.join(p, "HANDOFF.md")
        if os.path.isfile(note):
            size = len(read(note).strip())
            if size > limit:
                long_notes.append("%s: %d" % (os.path.basename(p), size))
    warn(not long_notes, "записки HANDOFF влезают в старт (до %d знаков)%s" % (
        limit, "; длиннее: " + ", ".join(long_notes[:5]) + (" и ещё %d" % (len(long_notes) - 5) if len(long_notes) > 5 else "")
        + ". Конец такой записки агент видит, только если откроет файл" if long_notes else ""))

    here = os.getcwd()
    name = os.path.basename(here)
    custom = cfg.get("journal_path") or ""
    if isinstance(custom, dict):
        custom = custom.get(name) or ""
    if custom:
        journal = os.path.join(here, os.path.expanduser(str(custom).replace("{project}", name)))
        warn(os.path.isfile(journal), "свой журнал найден для папки %s: %s" % (name, journal.replace("\\", "/")))

    say()
    if not bad and warned:
        say("Предупреждений: %d. Установка цела, но разберись с ними: агент получит не всё." % warned)
    say("Всё на месте." if not bad else "Пунктов «НЕТ»: %d. Исправь их и запусти проверку ещё раз." % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
