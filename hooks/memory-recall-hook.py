# -*- coding: utf-8 -*-
"""Память в нужный момент — а не только в начале сессии.

Правило, прочитанное на старте среди 180 строк оглавления, через сотню тысяч токенов уже не помнится,
а две трети правил в оглавление не входят вовсе. Этот хук подкладывает их тогда, когда они нужны.

Два режима:
  pre       — PreToolUse (Bash, PowerShell): агент собрался коммитить, пушить или идти на сервер —
              получает до пяти строк правил по теме. Один раз за сессию на тему, чтобы не шуметь.
              Пуш и команды на сервер в первый раз за сессию останавливаются: правила приходят
              причиной отказа, повтор команды проходит. Отключается ключом "recall_block": false.
  prompt    — UserPromptSubmit: человек просит о деле, у которого нет своей команды (поправить слайд,
              переделать экран, разобрать чат), — те же до пяти строк правил приходят вместе с просьбой.
              Никогда не останавливает. Раз за сессию на тему.
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

# pattern — по тексту команды; prompt — по тексту просьбы человека; moments — основы слов в названии
# раздела правила; query — слова для поиска
_UI = r"(?:экран|страниц|интерфейс|форм[уаы]|панел|кабинет|лендинг|сайт|дашборд|меню)"
_CHAT = r"(?:чат|переписк|диалог|телеграм|telegram)"
_READ = r"(?:разбер|разбор|проанализир|прочитай|прочти|изучи|выжимк|саммари|резюмир|о\s+чём\s+договор)"
GROUPS = [
    # три темы без своей команды: прогон 13.10.2026 показал, что правило есть и поиск его находит,
    # но без подсказки агент его не применяет (слайды кликами, переделка экрана без описи)
    {"key": "slides", "title": "слайды и презентации",
     "prompt": r"слайд|презентац|google\s*slides|\bpptx\b",
     "query": "слайды презентация Google Slides правка текста перенос"},
    {"key": "redesign", "title": "переделка экрана",
     "prompt": r"редизайн|(?:передел|переверст|перевёрст|перерис|перестро)[^.!?\n]{0,60}" + _UI
               + "|" + _UI + r"[^.!?\n]{0,40}(?:передел|переверст|перевёрст|перерис|перестро)",
     "query": "переделка экрана редизайн опись действий инструкции кнопки"},
    {"key": "chat", "title": "разбор переписки",
     "prompt": _READ + r"[^.!?\n]{0,60}" + _CHAT + "|" + _CHAT + r"[^.!?\n]{0,40}" + _READ,
     "query": "разобрать переписку чат Telegram голосовые расшифровать"},
    # программа зависла или не открывается: у инструмента обычно уже записаны грабли и обход, а без
    # подсказки агент чинит с нуля (идея пользователя набора, 14.10.2026). Слова просьбы идут в поиск — по ним
    # находится записка именно про этот инструмент
    {"key": "stuck", "title": "инструмент завис или не работает", "with_text": True,
     "prompt": r"завис|висн|не\s+(?:открыва|запуска|грузит|загружа|отвеча|работает|стартует|подключа)|"
               r"пада[ею]т|упал|вылета|крашит|не\s+видит|ошибк[аиу]\s+при",
     "query": "завис не открывается падает обход грабли"},
    {"key": "commit", "title": "коммит и публикация",
     "pattern": r"\bgit\s+(?:-[Cc]\s+\S+\s+)*(?:commit|push|add|merge|rebase|reset|tag)\b|\bgh\s+(?:pr|release)\s+(?:create|merge)",
     "block": r"\bgit\s+(?:-[Cc]\s+\S+\s+)*push\b|\bgh\s+(?:pr|release)\s+(?:create|merge)",
     "moments": ["коммич", "публик", "git"], "query": "git коммит пуш публикация add"},
    {"key": "server", "title": "сервер и выкатка",
     # docker здесь нет: местный контейнер — не сервер, а на сервере он идёт внутри ssh (ложная остановка 13.10.2026)
     "pattern": r"(?:^|[\s;&|(])(?:ssh|scp|rsync|sftp)\s+\S|\bpm2\s+\w|\bsystemctl\s+(?:restart|stop|start|reload)",
     "block": r".", "moments": ["депло", "сервер", "выкат"], "query": "сервер ssh деплой выкатка pm2"},
]

# Запреты: то, что правило требует всегда и что можно проверить по самой команде. Правило в тексте агент
# забывает, отказ хука — нет. Свои запреты — ключом "guards" в memory-kit.json, выключатель — "guard": false
# целиком или {"<key>": false} по одному.
#   tool — по имени инструмента; pattern — по тексту команды (кавычки вырезаны, чтобы не ловить слова
#   из сообщения коммита); text_pattern — по полному тексту команды вместе с кавычками, вместе с pattern:
#   «запуск X» ловит pattern, «и в задании про Y» — text_pattern; require + when_text — у инструмента должно быть поле с таким значением,
#   если в его тексте встречается when_text.
GUARDS = [
    {"key": "git_add_all", "tool": r"^(?:Bash|PowerShell|shell)$",
     # слова считаем только до конца своей команды: точка или -a из соседней через && — не наши (ложная остановка 14.10.2026)
     "pattern": r"(?:^|[;&|(]\s*|\n\s*)git\s+(?:-[Cc]\s+\S+\s+)*(?:add\s+(?:[^\s;&|]+[^\S\n]+)*(?:-A|--all|\.)(?=\s|$|[;&|)])"
                r"|commit\s+(?:[^\s;&|]+[^\S\n]+)*-[a-z]*a[a-z]*(?=\s|$))",
     "message": "Остановлено: `git add -A`, `git add .` и `git commit -a` берут всё подряд, в том числе чужие "
                "и секретные файлы. Посмотри `git status` и добавь свои файлы поимённо."},
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


def seen_before(session, key, mark=True):
    """Тему в этой сессии уже подсказывали? Отметка — в папке временных файлов, сама устаревает за сутки.
    mark=False только спрашивает: отметку ставим после того, как правила нашлись и ушли агенту."""
    path = os.path.join(tempfile.gettempdir(), "memory-kit-recall-%s.json" % re.sub(r"[^A-Za-z0-9_-]", "", session or "x"))
    try:
        data = json.load(open(path, encoding="utf-8")) if time.time() - os.path.getmtime(path) < 86400 else {}
    except Exception:
        data = {}
    if key in data:
        return True
    if not mark:
        return False
    data[key] = int(time.time())
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    except OSError:
        pass
    return False


def project_name(data, cfg):
    cwd = data.get("cwd") or os.getcwd()
    return os.path.basename(project_dir(cwd, cfg.get("root") or ROOT_FALLBACK) or cwd)


def note(data, cfg, event, **fields):
    """Запись в журнал срабатываний; сам журнал ведёт memory_search.log_event."""
    try:
        load("memory_search").log_event(event, session=(data.get("session_id") or "")[:8],
                                        project=project_name(data, cfg), **fields)
    except Exception:
        pass


def strip_quoted(command):
    """Команда без содержимого кавычек и встроенных текстов: запрет не должен ловить слова из сообщения коммита."""
    command = re.sub(r"<<-?\s*['\"]?(\w+)['\"]?.*?^\s*\1\s*$", " ", command, flags=re.S | re.M)
    return re.sub(r"\"(?:\\.|[^\"\\])*\"|'[^']*'", '""', command)


def guard(data, cfg):
    """Первый сработавший запрет: (key, сообщение) или None."""
    switch = cfg.get("guard", True)
    if switch is False:
        return None
    tool = data.get("tool_name") or ""
    ti = data.get("tool_input") or {}
    command = strip_quoted(ti.get("command") or "")
    for g in GUARDS + list(cfg.get("guards") or []):
        try:
            if isinstance(switch, dict) and switch.get(g["key"]) is False:
                continue
            if g.get("tool") and not re.search(g["tool"], tool, re.I):
                continue
            if g.get("pattern") and not re.search(g["pattern"], command, re.I | re.M):
                continue
            if g.get("text_pattern") and not re.search(g["text_pattern"], ti.get("command") or "", re.I | re.M):
                continue
            if g.get("require"):
                text = " ".join(str(v) for v in ti.values() if isinstance(v, str))
                if g.get("when_text") and not re.search(g["when_text"], text, re.I):
                    continue
                if all(str(ti.get(k, "")).lower() == str(v).lower() for k, v in g["require"].items()):
                    continue
            elif not (g.get("pattern") or g.get("text_pattern")):
                continue                      # запрет без условия не срабатывает никогда
            return g["key"], g.get("message") or "Остановлено правилом «%s»." % g["key"]
        except Exception:
            continue
    return None


def int_of(v):
    return int(re.sub(r"\D", "", str(v or "0")) or 0)


def pick(ms, group, command, dirs):
    """До MAX_LINES правил по теме: раздел или поиск привели к правилу, порядок — проверенность и слова команды."""
    stems = [ms.norm(x) for x in group.get("moments", [])]
    if group.get("with_text"):              # сами слова жалобы («завис», «падает») есть в половине правил — убираем
        command = re.sub(group["prompt"], " ", command, flags=re.I)
    # with_text: ищем по словам самой просьбы (имя программы, текст ошибки), общие слова темы только мешают
    query = command[:300] if group.get("with_text") else group.get("query", "")
    found = {(h["dir"], h["file"]) for kind in ("rule", "ref")
             for h in ms.search(query, dirs, 8, kind)}
    # слова самой команды (push, force, pm2, restart), но не куски путей и адресов: по ним в подсказку
    # лезло постороннее — «claude», «memory», имя папки (живая проба 12.10.2026)
    plain = " ".join(t for t in re.split(r"\s+", command) if not re.search(r"[/\\.:@~$]", t))
    words = [w for w in ms.query_terms(re.sub(r"[\"'`(){}\[\]=;,|&<>]", " ", plain))
             if len(w) >= 3 and not w.isdigit()][:8]
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
            hits = sum(1 for w in words if re.search(r"(?<![0-9a-zа-я_])" + re.escape(w), body))
            if group.get("with_text") and not hits:
                continue                      # правило не про эту программу — не подкладываем
            # у «зависло» решает то, про ту ли это программу, а не проверенность правила
            weight += min(9, 3 * hits) if group.get("with_text") else min(3, 1.5 * hits)
            out.append((-weight, rel, title, mem.replace(os.sep, "/")))
    out.sort()
    return out[:MAX_LINES]


def clean(line):
    line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)        # ссылки оглавления здесь не нужны
    line = re.sub(r"\s+", " ", line).strip(" ·")
    line = re.sub(r"\s*\bфайл$", "", line)                       # хвост от ссылки «[файл](...)»
    return line[:MAX_LINE_CHARS] + ("…" if len(line) > MAX_LINE_CHARS else "")


def pre(data, cfg):
    hit = guard(data, cfg)
    if hit:
        note(data, cfg, "guard", key=hit[0], blocked=True)
        return "deny", hit[1]
    command = (data.get("tool_input") or {}).get("command") or ""
    if not command:
        return None
    group = next((g for g in cfg.get("recall") or GROUPS
                  if g.get("pattern") and re.search(g["pattern"], command, re.I | re.M)), None)
    if not group:
        return None
    # Добавка в контекст приходит агенту вместе с результатом команды, то есть уже после неё
    # (проверено живым прогоном 12.10.2026). Для того, что не вернуть — пуш, сервер, — этого мало:
    # такую команду один раз за сессию останавливаем, правила уходят причиной отказа, повтор проходит.
    stop = bool(group.get("block")) and re.search(group["block"], command, re.I | re.M) \
        and cfg.get("recall_block", True)
    lines = remind(data, cfg, group, command, group["key"] + (":block" if stop else ""), bool(stop))
    if not lines:
        return None
    if stop:
        lines.append("Команда остановлена один раз, чтобы ты сверился до неё, а не после. Правилам не "
                     "противоречит — повтори её без изменений, второй раз она пройдёт. Противоречит — исправь.")
        return "deny", "\n".join(lines)
    return "PreToolUse", "\n".join(lines)


def remind(data, cfg, group, text, mark, blocked=False):
    """Строки подсказки по теме или None: тему в этой сессии уже показывали либо правил не нашлось."""
    if seen_before(data.get("session_id"), mark, mark=False):
        return None                           # в журнал не пишем: повтор темы в той же сессии — не событие
    ms = load("memory_search")
    rows = pick(ms, group, text, ms.memory_dirs(data.get("cwd") or os.getcwd(), False, None))
    if not rows:
        note(data, cfg, "recall_empty", key=group["key"])      # тема была, правил не нашлось: дыра в памяти
        return None
    seen_before(data.get("session_id"), mark)  # отметку ставим, только когда есть что показать
    note(data, cfg, "recall", key=group["key"], blocked=blocked or None, files=[r[1] for r in rows])
    lines = ["Память, тема «%s» — правила, которые ты уже записывал:" % group.get("title", group["key"])]
    for _, rel, title, _ in rows:
        lines.append("- %s (%s)" % (clean(title), rel))
    lines.append("Файлы лежат в " + ", ".join(dict.fromkeys(r[3] for r in rows)))
    return lines


def prompt(data, cfg):
    """Просьба человека о деле без своей команды: правила приходят вместе с просьбой, остановки нет."""
    text = re.sub(r"(?s)<system-reminder>.*?</system-reminder>", " ", data.get("prompt") or "")[:2000]
    if not text.strip() or text.lstrip().startswith("/"):
        return None
    out = []
    for group in cfg.get("recall") or GROUPS:
        if group.get("prompt") and re.search(group["prompt"], text, re.I | re.M):
            out += remind(data, cfg, group, text, group["key"]) or []
    return ("UserPromptSubmit", "\n".join(out)) if out else None


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
    note(data, cfg, "subagent")
    return "SubagentStart", text[:SUBAGENT_MAX_CHARS]


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "pre"
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace") or "{}")
    except Exception:
        data = {}
    res = {"subagent": subagent, "prompt": prompt}.get(mode, pre)(data, config())
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
