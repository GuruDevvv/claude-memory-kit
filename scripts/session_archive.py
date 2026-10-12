# -*- coding: utf-8 -*-
"""Архив разговоров: текст человека и агента из записей сессий Claude Code — до того, как их сотрут.

Зачем. Claude Code хранит записи сессий 30 дней и удаляет. Вместе с ними пропадает то, что
не попало ни в журнал, ни в правило: почему решили так, что именно сказал человек.
Запись сессии — это мегабайты вызовов инструментов; сам разговор занимает в ней меньше процента
(замер 12.10.2026: 361 сессия, 2 ГБ, из них текста около 15 МБ). Архив хранит только разговор.

Что берётся: то, что набрал человек, и то, что ответил агент словами. Что НЕ берётся: вызовы
инструментов и их вывод, размышления модели, служебные вставки, помощники (субагенты).
Секреты вырезаются по маскам (ключи, токены, «пароль: …») — но маска не гарантия:
архив лежит только на этой машине, в git его класть не надо.

Сессия попадает в архив, когда её запись не менялась полчаса (то есть сессия закончена или стоит),
и переписывается, если запись с тех пор выросла. Удалённую запись архив переживает.

Запуск:
  python session_archive.py                 # забрать всё новое
  python session_archive.py --max 5         # не больше пяти сессий за раз (так зовёт хук конца хода)
  python session_archive.py --quiet         # молчать, если всё в порядке
  python session_archive.py --dry           # только посчитать
Искать: python memory_search.py "<слова>" --sessions
"""
import datetime
import glob
import json
import os
import re
import sys
import time

IDLE_SECONDS = 30 * 60
MAX_MESSAGE_CHARS = 6000

MASKS = [
    (r"sk-[A-Za-z0-9_\-]{20,}", "sk-…"),
    (r"\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{20,}", "gh…"),
    (r"\bxox[abprs]-[A-Za-z0-9\-]{10,}", "xox…"),
    (r"\bAKIA[0-9A-Z]{16}\b", "AKIA…"),
    (r"\b\d{8,10}:[A-Za-z0-9_\-]{35}\b", "<токен бота>"),
    (r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}", "<jwt>"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", "<закрытый ключ>"),
    (r"(?i)\b(password|passwd|пароль|token|токен|secret|секрет|api[_\-]?key|apikey|bearer)(\s*[:=]\s*)([^\s\"'`,;]{6,})",
     lambda m: m.group(1) + m.group(2) + "<скрыто>"),
    # через пробел — только то, что похоже на ключ (буквы вперемешку с цифрами), иначе вырежется обычная фраза
    (r"(?i)\b(password|passwd|пароль|token|токен|secret|секрет|bearer)(\s+)((?=\S*\d)(?=\S*[A-Za-z])[A-Za-z0-9_\-+/=.]{8,})",
     lambda m: m.group(1) + m.group(2) + "<скрыто>"),
    (r"\b[0-9a-fA-F]{40,}\b", "<длинный ключ>"),
]
NOISE = re.compile(r"(?s)<system-reminder>.*?</system-reminder>|<local-command-[a-z]+>.*?</local-command-[a-z]+>"
                   r"|<command-(?:name|message|args)>.*?</command-(?:name|message|args)>|<task-notification>.*?</task-notification>")


def _places():
    """(папка записей Claude Code, папка архива). Архив — рядом с общей памятью, если не задано иное."""
    home = os.path.expanduser("~")
    source = os.path.join(home, ".claude", "projects")
    cfg = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "memory-kit.json")
    try:
        with open(cfg, encoding="utf-8") as fh:
            data = json.load(fh)
        archive = data.get("sessions_dir") or os.path.join(os.path.dirname(data["central"]), "sessions")
    except Exception:
        archive = os.path.join(home, ".agent-memory", "sessions")
    return source, archive


SOURCE, ARCHIVE = _places()


def mask(text):
    for pattern, repl in MASKS:
        text = re.sub(pattern, repl, text, flags=re.S)
    return text


def clean(text):
    text = NOISE.sub("", text).strip()
    if not text or text.startswith(("Caveat: The messages below", "[Request interrupted")):
        return ""
    if len(text) > MAX_MESSAGE_CHARS:
        text = text[:MAX_MESSAGE_CHARS] + "\n[…обрезано]"
    return mask(text)


def words_of(entry):
    """Текст сообщения без вызовов инструментов, их вывода и размышлений."""
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return clean(content)
    if isinstance(content, list):
        return clean("\n\n".join(b.get("text", "") for b in content
                                 if isinstance(b, dict) and b.get("type") == "text"))
    return ""


def stamp(iso):
    try:
        return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "????-??-?? ??:??"


def convert(path):
    """Запись сессии -> (текст архива, дата начала, папка проекта). Пустой разговор -> ('', '', '')."""
    blocks, cwd, first = [], "", ""
    with open(path, encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            if '"type":"user"' not in ln and '"type":"assistant"' not in ln:
                continue
            try:
                e = json.loads(ln)
            except ValueError:
                continue
            kind = e.get("type")
            if kind not in ("user", "assistant") or e.get("isSidechain") or e.get("isMeta"):
                continue
            text = words_of(e)
            if not text:
                continue
            cwd = cwd or e.get("cwd") or ""
            when = stamp(e.get("timestamp") or "")
            first = first or when
            who = "человек" if kind == "user" else "агент"
            if blocks and blocks[-1][0] == who == "агент":
                blocks[-1][2].append(text)             # ответ агента идёт кусками между инструментами
            else:
                blocks.append([who, when, [text]])
    if not any(b[0] == "человек" for b in blocks):
        return "", "", ""
    sid = os.path.splitext(os.path.basename(path))[0]
    out = ["# Сессия %s" % sid, "", "Проект: %s" % (cwd or "?"), "Начало: %s" % first, ""]
    for who, when, texts in blocks:
        out += ["## [%s] %s" % (when, who), "", "\n\n".join(texts), ""]
    return "\n".join(out), first, cwd


def target(path, first):
    slug = os.path.basename(os.path.dirname(path))
    sid = os.path.splitext(os.path.basename(path))[0]
    return os.path.join(ARCHIVE, slug, "%s_%s.md" % (first[:10], sid[:8]))


def known(slug, sid):
    hits = glob.glob(os.path.join(ARCHIVE, slug, "*_%s.md" % sid[:8]))
    return hits[0] if hits else None


def sweep(limit=None, dry=False):
    """Забрать законченные сессии, которых в архиве нет или которые с тех пор выросли. -> (взято, пропущено пустых)."""
    done = empty = 0
    now = time.time()
    files = sorted(glob.glob(os.path.join(SOURCE, "*", "*.jsonl")), key=os.path.getmtime)   # старые первыми: их сотрут раньше
    for path in files:
        mtime = os.path.getmtime(path)
        if now - mtime < IDLE_SECONDS:
            continue
        slug = os.path.basename(os.path.dirname(path))
        sid = os.path.splitext(os.path.basename(path))[0]
        old = known(slug, sid)
        marker = os.path.join(ARCHIVE, slug, ".empty-%s" % sid[:8])
        if (old and os.path.getmtime(old) >= mtime) or (os.path.exists(marker) and os.path.getmtime(marker) >= mtime):
            continue
        if limit is not None and done >= limit:
            break
        if dry:
            done += 1
            continue
        text, first, _ = convert(path)
        os.makedirs(os.path.join(ARCHIVE, slug), exist_ok=True)
        if not text:                                    # разговора не было — запомнить, чтобы не разбирать снова
            with open(marker, "w") as fh:
                fh.write("")
            empty += 1
            continue
        dst = target(path, first)
        if old and old != dst:
            os.remove(old)
        tmp = "%s.%d.tmp" % (dst, os.getpid())
        with open(tmp, "wb") as fh:
            fh.write(text.encode("utf-8"))
        os.replace(tmp, dst)
        done += 1
    return done, empty


def main(argv):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    quiet, dry = "--quiet" in argv, "--dry" in argv
    limit = int(argv[argv.index("--max") + 1]) if "--max" in argv else None
    if not os.path.isdir(SOURCE):
        return 0
    done, empty = sweep(limit, dry)
    if not quiet or dry:
        print("архив сессий %s: %s %d, пустых %d" % (ARCHIVE.replace(os.sep, "/"),
                                                     "взял бы" if dry else "взято", done, empty))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
