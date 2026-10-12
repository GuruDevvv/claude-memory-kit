# -*- coding: utf-8 -*-
"""Память в нужный момент — а не только в начале сессии.

Правило, прочитанное на старте среди 180 строк оглавления, через сотню тысяч токенов уже не помнится,
а две трети правил в оглавление не входят вовсе. Этот хук подкладывает их тогда, когда они нужны.

Два режима:
  pre       — PreToolUse (Bash, PowerShell): агент собрался коммитить, пушить или идти на сервер —
              получает до пяти строк правил по теме. Один раз за сессию на тему, чтобы не шуметь.
              Пуш и команды на сервер в первый раз за сессию останавливаются: правила приходят
              причиной отказа, повтор команды проходит. Отключается ключом "recall_block": false.
  subagent  — SubagentStart: помощник стартует без записки проекта и без памяти и потому выдаёт
              известное за новое. Получает записку, раздел «Всегда» и команду поиска, до 3 тыс. знаков.

Молчит во всех остальных случаях. Ошибка хука работу не останавливает.
Темы и их признаки — в GROUPS; свои можно задать ключом "recall" в memory-kit.json.
"""
import importlib.util
import json
import os
import re
import sys
import tempfile
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT_FALLBACK = None            # корень проектов, если настроек memory-kit.json нет
MAX_LINES = 5
MAX_LINE_CHARS = 260
SUBAGENT_MAX_CHARS = 3000
HANDOFF_CHARS = 1200
ALWAYS_CHARS = 1300
ALWAYS_LINE_CHARS = 170

# pattern — по тексту команды; moments — основы слов в названии раздела правила; query — слова для поиска
GROUPS = [
    {"key": "commit", "title": "коммит и публикация",
     "pattern": r"\bgit\s+(?:-[Cc]\s+\S+\s+)*(?:commit|push|add|merge|rebase|reset|tag)\b|\bgh\s+(?:pr|release)\s+(?:create|merge)",
     "block": r"\bgit\s+(?:-[Cc]\s+\S+\s+)*push\b|\bgh\s+(?:pr|release)\s+(?:create|merge)",
     "moments": ["коммич", "публик", "git"], "query": "git коммит пуш публикация add"},
    {"key": "server", "title": "сервер и выкатка",
     "pattern": r"(?:^|[\s;&|(])(?:ssh|scp|rsync|sftp)\s+\S|\bpm2\s+\w|\bsystemctl\s+(?:restart|stop|start|reload)|\bdocker\s+(?:compose|exec|run)\b",
     "block": r".", "moments": ["депло", "сервер", "выкат"], "query": "сервер ssh деплой выкатка pm2"},
]


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(BASE, "scripts", name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def config():
    try:
        with open(os.path.join(BASE, "memory-kit.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def seen_before(session, key):
    """Тему в этой сессии уже подсказывали? Отметка — в папке временных файлов, сама устаревает за сутки."""
    path = os.path.join(tempfile.gettempdir(), "memory-kit-recall-%s.json" % re.sub(r"[^A-Za-z0-9_-]", "", session or "x"))
    try:
        data = json.load(open(path, encoding="utf-8")) if time.time() - os.path.getmtime(path) < 86400 else {}
    except Exception:
        data = {}
    if key in data:
        return True
    data[key] = int(time.time())
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    except OSError:
        pass
    return False


def int_of(v):
    return int(re.sub(r"\D", "", str(v or "0")) or 0)


def pick(ms, group, command, dirs):
    """До MAX_LINES правил по теме: раздел или поиск привели к правилу, порядок — проверенность и слова команды."""
    stems = [ms.norm(x) for x in group.get("moments", [])]
    found = {(h["dir"], h["file"]) for kind in ("rule", "ref")
             for h in ms.search(group.get("query", ""), dirs, 8, kind)}
    words = [w for w in ms.query_terms(re.sub(r"[\"'`$(){}\[\]=/\\:;,|&<>]", " ", command))
             if len(w) >= 3 and not w.isdigit()][:12]
    out = []
    for mem in dirs:
        for kind, rel, _, title, text, _, m in ms.docs_of(mem):
            if kind not in ("rule", "ref"):
                continue
            by_moment = any(s in ms.norm(m.get("moment", "")) for s in stems)
            if not by_moment and (mem, rel) not in found:
                continue
            status, seen = (m.get("status") or "").lower(), int_of(m.get("seen"))
            body = ms.norm(title + "\n" + text)
            weight = (2 if by_moment else 0) + (1 if status != "проба" else 0) + (2 if status != "проба" and seen >= 2 else 0)
            weight += {"owner": 1, "owner_ok": 1}.get((m.get("origin_kind") or "").lower(), 0)
            weight += 1.5 * sum(1 for w in words if re.search(r"(?<![0-9a-zа-я_])" + re.escape(w), body))
            out.append((-weight, rel, title, mem.replace(os.sep, "/")))
    out.sort()
    return out[:MAX_LINES]


def clean(line):
    line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)        # ссылки оглавления здесь не нужны
    line = re.sub(r"\s+", " ", line).strip(" ·")
    line = re.sub(r"\s*\bфайл$", "", line)                       # хвост от ссылки «[файл](...)»
    return line[:MAX_LINE_CHARS] + ("…" if len(line) > MAX_LINE_CHARS else "")


def pre(data, cfg):
    command = (data.get("tool_input") or {}).get("command") or ""
    if not command:
        return None
    group = next((g for g in cfg.get("recall") or GROUPS if re.search(g["pattern"], command, re.I | re.M)), None)
    if not group:
        return None
    # Добавка в контекст приходит агенту вместе с результатом команды, то есть уже после неё
    # (проверено живым прогоном 12.10.2026). Для того, что не вернуть — пуш, сервер, — этого мало:
    # такую команду один раз за сессию останавливаем, правила уходят причиной отказа, повтор проходит.
    stop = bool(group.get("block")) and re.search(group["block"], command, re.I | re.M) \
        and cfg.get("recall_block", True)
    if seen_before(data.get("session_id"), group["key"] + (":block" if stop else "")):
        return None
    ms = load("memory_search")
    rows = pick(ms, group, command, ms.memory_dirs(data.get("cwd") or os.getcwd(), False, None))
    if not rows:
        return None
    lines = ["Память, тема «%s» — правила, которые ты уже записывал:" % group.get("title", group["key"])]
    for _, rel, title, _ in rows:
        lines.append("- %s (%s)" % (clean(title), rel))
    lines.append("Файлы лежат в " + ", ".join(dict.fromkeys(r[3] for r in rows)))
    if stop:
        lines.append("Команда остановлена один раз, чтобы ты сверился до неё, а не после. Правилам не "
                     "противоречит — повтори её без изменений, второй раз она пройдёт. Противоречит — исправь.")
        return "deny", "\n".join(lines)
    return "PreToolUse", "\n".join(lines)


def project_dir(cwd, root):
    """Ближайшая вверх папка с HANDOFF.md; выше корня проектов не идём, сам корень — только если стоим в нём."""
    same = lambda a, b: os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
    start = d = os.path.abspath(cwd)
    for _ in range(12):
        if root and same(d, root) and not same(d, start):
            break
        if os.path.exists(os.path.join(d, "HANDOFF.md")):
            return d
        up = os.path.dirname(d)
        if up == d:
            break
        d = up
    return None


def subagent(data, cfg):
    ms = load("memory_search")
    cwd = data.get("cwd") or os.getcwd()
    search = "%s %s" % (os.path.basename(sys.executable) or "python",
                        os.path.join(BASE, "scripts", "memory_search.py").replace(os.sep, "/"))
    # главное — первым: при обрезке по длине пропадает хвост, а не команда поиска
    parts = ["У проекта есть память: правила, справочники, журналы прошлых сессий. Прежде чем назвать "
             "что-то находкой или новым, проверь: `%s \"<о чём речь, своими словами>\"`. "
             "Нашлось — так и скажи: «это уже записано», со ссылкой на файл." % search]
    central = cfg.get("central") or ms.CENTRAL
    index = os.path.join(central, "MEMORY.md") if central else ""
    if index and os.path.exists(index):
        m = re.search(r"(?ms)^## Всегда\s*\n(.*?)(?=^## |\Z)", ms.read(index).replace("\r\n", "\n"))
        if m and m.group(1).strip():
            always = "\n".join("- " + clean(ln[2:] if ln.startswith("- ") else ln)[:ALWAYS_LINE_CHARS]
                               for ln in m.group(1).strip().split("\n") if ln.strip())
            parts.append("Правила владельца, которые действуют всегда:\n" + always[:ALWAYS_CHARS])
    proj = project_dir(cwd, cfg.get("root") or ROOT_FALLBACK)
    if proj:
        text = ms.read(os.path.join(proj, "HANDOFF.md")).strip()
        parts.append("Записка проекта %s (%s):\n%s" % (os.path.basename(proj), os.path.join(proj, "HANDOFF.md"),
                     text[:HANDOFF_CHARS] + ("\n[…обрезано, открой файл]" if len(text) > HANDOFF_CHARS else "")))
    text = "memory-kit: память для помощника\n\n" + "\n\n".join(parts)
    return "SubagentStart", text[:SUBAGENT_MAX_CHARS]


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "pre"
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace") or "{}")
    except Exception:
        data = {}
    res = (subagent if mode == "subagent" else pre)(data, config())
    if res and res[0] == "deny":
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                 "permissionDecisionReason": res[1]}}, ensure_ascii=True))
    elif res:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": res[0], "additionalContext": res[1]}},
                         ensure_ascii=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:                   # хук не имеет права ронять работу
        sys.stderr.write("memory-recall-hook: %s\n" % type(e).__name__)
        sys.exit(0)
