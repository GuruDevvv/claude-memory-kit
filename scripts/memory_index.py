# -*- coding: utf-8 -*-
"""Собирает MEMORY.md (оглавление памяти) из файлов правил — по жёсткому бюджету.

Зачем. Claude Code в начале сессии читает из MEMORY.md только первые 200 строк / 25 КБ,
остальное молча отбрасывает. Оглавление, которое пишут руками, растёт на ~0,5 тыс. знаков
в день (замер 21.08–26.09.2026) и через месяц после каждой чистки снова переполнено.
Поэтому оглавление больше не пишется руками: его собирает этот скрипт.

Как устроено:
  * MEMORY.head.md — неизменная шапка (правила пользования, указатели). Пишется руками, мала.
  * Строка оглавления живёт в САМОМ файле правила: metadata.index_line (текст строки)
    и metadata.moment (раздел оглавления). Файл — источник правды, оглавление — выжимка.
  * Кандидаты набираются, пока влезают в бюджет: сперва закреплённое и раздел «Всегда»,
    затем по очереди проверенное (правило с seen >= 2) и новое (правили за последние 2 недели),
    затем остальное. Очередь нужна, чтобы ни новое не вытесняло проверенное, ни наоборот:
    замер 12.10.2026 — при одной общей шкале в оглавлении было 2 проверенных правила из 52.
    Не влезшее уходит в INDEX.md — ничего не теряется.
  * Сверка счёта: каждый файл правила обязан оказаться либо в MEMORY.md, либо в INDEX.md.
    Иначе — код возврата 2 и список потерь (однажды ручная перестройка потеряла 16 записей).
  * Самолечение: если сессия дописала строку в MEMORY.md руками (так велит сам Claude Code),
    строка не теряется — её текст переносится в index_line файла, на который она ссылается,
    а строка без ссылки на файл — в раздел «Входящие» шапки.
    Ручной считается только строка, которой не было в прошлой сборке (снимок .index-emitted.json):
    иначе исправленная в файле index_line откатывалась бы к старой строке оглавления.
  * Две сборки одной папки сразу не идут: вторая ждёт замок .index-lock, не дождалась — пропускает.

Запуск:
  python memory_index.py <папка memory>      # одна папка
  python memory_index.py --all               # все папки памяти из настроек memory-kit.json
  --dry     не писать файлы, только отчёт
  --quiet   печатать только проблемы
"""
import glob
import itertools
import json
import os
import re
import sys
import time

BUDGET_CHARS = 23500        # запас под лимит 25 тыс. ЗНАКОВ (замер 26.09: обрезка шла по знакам, не по байтам)
BUDGET_LINES = 180          # запас под лимит 200 строк
RECENT_DAYS = 14            # свежие правила получают надбавку, чтобы новое было видно хотя бы две недели
SKIP_DIRS = {"projects", "research", "plans", "reviews", "audits", "archive", "knowledge", "user"}
SKIP_FILES = {"MEMORY.md", "MEMORY.head.md", "INDEX.md", "PROJECT_INDEX.md"}


def _places():
    """Где лежит память: из настроек memory-kit.json рядом с папкой scripts, иначе место Claude Code."""
    cfg = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "memory-kit.json")
    try:
        with open(cfg, encoding="utf-8") as fh:
            data = json.load(fh)
        return data["projects_dir"], data.get("central")
    except Exception:
        return os.path.join(os.path.expanduser("~"), ".claude", "projects"), None


PROJECTS_ROOT, CENTRAL = _places()
MARK = "<!-- СОБРАНО memory_index.py — руками не править. Новое правило = отдельный файл; строка для оглавления — в его metadata.index_line и metadata.moment. -->"
LINK_RE = re.compile(r"\]\(([^)\s]+\.md)\)")
HEAD_END = "<!-- КОНЕЦ ШАПКИ: ниже собранные строки -->"
HEAD_STATE = ".head-emitted"            # снимок шапки на момент прошлой сборки
LINES_STATE = ".index-emitted.json"     # снимок собранных строк: {файл: {line, moment}}
LOCK = ".index-lock"
LOCK_WAIT = 10                          # сколько секунд ждать чужую сборку
LOCK_STALE = 120                        # замок старше — остался от упавшей сборки


# ---------- файлы правил и их шапки ----------

def rule_files(mem):
    out = []
    for root, dirs, files in os.walk(mem):
        rel_root = os.path.relpath(root, mem)
        top = rel_root.split(os.sep)[0]
        if rel_root != "." and top in SKIP_DIRS:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            if f.endswith(".md") and f not in SKIP_FILES and not f.startswith("_"):
                out.append(os.path.relpath(os.path.join(root, f), mem).replace(os.sep, "/"))
    return sorted(out)


def read(path):
    with open(path, "rb") as fh:
        raw = fh.read()
    return raw.decode("utf-8", errors="replace")


def split_fm(txt):
    """(строки шапки без ---, индекс строки закрывающего ---) или (None, None)."""
    lines = txt.split("\n")
    if not lines or lines[0].strip() != "---":
        return None, None
    for i in range(1, min(len(lines), 80)):
        if lines[i].strip() == "---":
            return lines[1:i], i
    return None, None


def unq(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        try:
            return json.loads(v) if v[0] == '"' else v[1:-1]
        except Exception:
            return v[1:-1]
    return v


def meta(path):
    txt = read(path)
    fm, _ = split_fm(txt)
    m = {}
    if fm:
        for ln in fm:
            mm = re.match(r"^\s*([A-Za-z_]+):\s*(.*?)\s*$", ln.rstrip("\r"))
            if mm and mm.group(2) != "":
                m.setdefault(mm.group(1), unq(mm.group(2)))
    if "description" not in m:            # запасной вариант — первая содержательная строка тела
        body = txt.split("\n")
        for ln in body[(split_fm(txt)[1] or -1) + 1:]:
            s = ln.strip().lstrip("#").strip()
            if s and not s.startswith("---"):
                m["description"] = s[:200]
                break
    return m


def set_meta(path, updates):
    """Вставить/заменить ключи в metadata, НЕ переписывая файл целиком (сохраняем CRLF и порядок)."""
    txt = read(path)
    nl = "\r\n" if "\r\n" in txt else "\n"
    t = txt.replace("\r\n", "\n")
    fm, end = split_fm(t)
    lines = t.split("\n")
    vals = {k: json.dumps(v, ensure_ascii=False) if isinstance(v, str) else str(v)
            for k, v in updates.items()}
    if fm is None:
        name = os.path.splitext(os.path.basename(path))[0]
        head = ["---", f"name: {name}", "metadata:"] + [f"  {k}: {v}" for k, v in vals.items()] + ["---"]
        lines = head + lines
    else:
        block = lines[1:end]
        for k, v in vals.items():
            idx = next((i for i, ln in enumerate(block) if re.match(rf"^\s+{k}:", ln)), None)
            if idx is not None:
                indent = re.match(r"^(\s+)", block[idx]).group(1)
                block[idx] = f"{indent}{k}: {v}"
                continue
            mi = next((i for i, ln in enumerate(block) if re.match(r"^metadata:\s*$", ln)), None)
            if mi is None:
                block.append("metadata:")
                mi = len(block) - 1
            j = mi + 1
            while j < len(block) and re.match(r"^\s+\S", block[j]):
                j += 1
            block.insert(j, f"  {k}: {v}")
        lines = lines[:1] + block + lines[end:]
    new = nl.join(lines)
    if new != txt:
        st = os.stat(path)
        write_atomic(path, new)
        os.utime(path, (st.st_atime, st.st_mtime))   # служебная правка — не «свежее правило»


def write_atomic(path, text):
    tmp = f"{path}.{os.getpid()}.tmp"     # своё имя у каждого процесса: две сборки не делят временный файл
    with open(tmp, "wb") as fh:
        fh.write(text.encode("utf-8"))
    os.replace(tmp, path)


def take_lock(mem):
    """Замок на папку памяти. True — взяли; False — другая сборка так и не закончилась."""
    path = os.path.join(mem, LOCK)
    deadline = time.time() + LOCK_WAIT
    while True:
        try:
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            return True
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(path) > LOCK_STALE:
                    os.remove(path)
                    continue
            except OSError:
                continue
            if time.time() > deadline:
                return False
            time.sleep(0.2)


def drop_lock(mem):
    try:
        os.remove(os.path.join(mem, LOCK))
    except OSError:
        pass


def load_emitted(mem):
    """Строки прошлой сборки или None, если снимка ещё нет (первая сборка после обновления)."""
    try:
        with open(os.path.join(mem, LINES_STATE), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


# ---------- разбор текущего MEMORY.md (самолечение) ----------

def parse_index(mem, rules, head_lines):
    """Строки текущего MEMORY.md -> (записи с файлом, строки без файла, порядок разделов)."""
    path = os.path.join(mem, "MEMORY.md")
    if not os.path.exists(path):
        return [], [], [], []
    entries, stray, order, dead = [], [], [], []
    moment = "Разное"
    in_table = False
    head_set = {h.strip() for h in head_lines if h.strip()}
    # строки, которые прошлая сборка сама положила из шапки: если их в шапке уже нет — их там
    # исправили или убрали, это не «потерянная ручная строка», обратно в шапку не тащим
    prev = os.path.join(mem, HEAD_STATE)
    if os.path.exists(prev):
        head_set |= {h.strip() for h in read(prev).replace("\r\n", "\n").split("\n") if h.strip()}
    text = read(path).replace("\r\n", "\n")
    if HEAD_END in text:                # собранный файл: шапку не разбираем — её правят в MEMORY.head.md
        text = text.split(HEAD_END, 1)[1]
    for ln in text.split("\n"):
        s = ln.strip()
        if "PROJECTS:START" in s:          # таблица между метками PROJECTS:START/END — чужая, не разбираем
            in_table = True
        if in_table:
            if "PROJECTS:END" in s:
                in_table = False
            continue
        bold = re.fullmatch(r"\*\*([^*]+)\*\*", s)
        if s.startswith("## ") or bold:     # подзаголовок «**Группа**» внутри раздела — тоже раздел
            moment = bold.group(1).strip() if bold else s[3:].strip()
            if moment not in order:
                order.append(moment)
            continue
        if not s or s.startswith("#") or s.startswith("<!--") or s == MARK or s in head_set:
            continue
        targets = []
        for href in LINK_RE.findall(s):
            rel = os.path.normpath(href).replace(os.sep, "/")
            if rel in rules:
                targets.append(rel)
        if targets:
            text = s[2:].strip() if s.startswith(("- ", "* ")) else s
            entries.append({"moment": moment, "text": text, "files": targets})
        elif any(not os.path.exists(os.path.normpath(os.path.join(mem, h))) for h in LINK_RE.findall(s)):
            dead.append(s)                  # файл удалён/переименован — строке некуда вести, в шапку не тащим
        else:
            stray.append((moment, s))
    return entries, stray, order, dead


# ---------- шапка ----------

def load_head(mem):
    p = os.path.join(mem, "MEMORY.head.md")
    if not os.path.exists(p):
        return None
    return read(p).replace("\r\n", "\n")


def head_sections_order(head):
    m = re.search(r"<!--\s*разделы:\s*(.*?)\s*-->", head or "", re.S)
    return [x.strip() for x in m.group(1).split("|") if x.strip()] if m else []


def split_head(head):
    """Шапка -> (вступление до первого «## », {раздел: [строки]} в порядке появления)."""
    pre, secs, cur = [], {}, None
    for ln in head.rstrip("\n").split("\n"):
        if ln.startswith("## "):
            cur = ln[3:].strip()
            secs.setdefault(cur, [])
            continue
        (secs[cur] if cur else pre).append(ln)
    return pre, secs


def join_head(pre, secs):
    out = list(pre)
    for name, lines in secs.items():
        body = list(lines)
        while body and not body[0].strip():
            body.pop(0)
        while body and not body[-1].strip():
            body.pop()
        out += ["", f"## {name}", ""] + body
    return "\n".join(out).rstrip("\n") + "\n"


def add_to_head(mem, head, items):
    """Строки без своего файла (ссылка на общую память, факт, подпись) — в шапку, в тот же раздел."""
    if not items:
        return head
    pre, secs = split_head(head)
    for moment, s in items:
        tgt = secs.setdefault(moment, []) if moment != "Разное" else pre
        if s not in (l.strip() for l in tgt):
            tgt.append(s)
    head = join_head(pre, secs)
    write_atomic(os.path.join(mem, "MEMORY.head.md"), head)
    return head


# ---------- сборка ----------

def score(m, path, now):
    st = (m.get("status") or "").lower()
    pin = str(m.get("pin", "")).lower() in ("true", "1", "yes", "да")
    try:
        seen = int(re.sub(r"\D", "", str(m.get("seen", "0"))) or 0)
    except ValueError:
        seen = 0
    fresh = (now - os.path.getmtime(path)) < RECENT_DAYS * 86400
    always = (m.get("moment") or "") == "Всегда"      # раздел «Всегда» виден всегда, по замыслу
    rule = st != "проба"
    proven = rule and seen >= 2                       # подтверждённое повтором свежая проба не вытесняет
    return ((1000 if pin or always else 0) + (100 if rule else 0) + (200 if proven else 0)
            + min(seen, 5) * 4 + (150 if fresh else 0))   # новое видно 2 недели


def ranked(cands, pos):
    """Порядок набора в оглавление: закреплённое, потом проверенное и новое по очереди, потом остальное."""
    def key(c):
        return (-c["score"], pos.get(c["file"], 10 ** 6), c["file"])
    top = sorted((c for c in cands if c["score"] >= 1000), key=key)
    low = [c for c in cands if c["score"] < 1000]
    proven = sorted((c for c in low if c["proven"]), key=key)
    fresh = sorted((c for c in low if c["fresh"] and not c["proven"]), key=lambda c: (-c["mtime"], c["file"]))
    rest = sorted((c for c in low if not c["proven"] and not c["fresh"]), key=key)
    mixed = [c for pair in itertools.zip_longest(proven, fresh) for c in pair if c]
    return top + mixed + rest


def build(mem, dry=False, quiet=False):
    mem = os.path.abspath(mem)
    if dry:
        return _build(mem, dry, quiet)
    if not take_lock(mem):
        return 0, ("" if quiet else f"{mem}: идёт другая сборка, пропуск")
    try:
        return _build(mem, dry, quiet)
    finally:
        drop_lock(mem)


def _build(mem, dry, quiet):
    rules = set(rule_files(mem))
    head = load_head(mem)
    if head is None:
        return 0, f"{mem}: нет MEMORY.head.md — папка не переведена на сборку, пропуск"
    head_lines = head.split("\n")
    entries, stray, order_seen, dead = parse_index(mem, rules, head_lines)
    notes = []
    if dead:
        notes.append(f"строк на удалённые файлы выброшено: {len(dead)}")
    in_head = set()                     # файлы, на которые уже ссылается шапка — их не дублируем
    for href in LINK_RE.findall(head):
        rel = os.path.normpath(href).replace(os.sep, "/")
        if rel in rules:
            in_head.add(rel)
    entries = [e for e in entries if e["files"][0] not in in_head]

    # 1) самолечение: строки из MEMORY.md -> index_line файлов
    metas = {r: meta(os.path.join(mem, r)) for r in rules}
    texts = {}                          # две строки про один файл — склеить, а не затереть первую
    for e in entries:
        t = texts.setdefault(e["files"][0], [])
        if e["text"] not in t:
            t.append(e["text"])
    emitted = load_emitted(mem)
    index_path = os.path.join(mem, "MEMORY.md")
    index_mtime = os.path.getmtime(index_path) if os.path.exists(index_path) else 0

    def newer(f):                       # снимка нет: файл правили позже оглавления — прав файл
        return os.path.getmtime(os.path.join(mem, f)) > index_mtime

    def ours(f, text):                  # строку положила прошлая сборка, а не человек
        if emitted is not None:
            return (emitted.get(f) or {}).get("line") == text
        return bool(metas[f].get("index_line")) and newer(f)

    for e in entries:
        primary, others = e["files"][0], e["files"][1:]
        m = metas[primary]
        upd = {}
        hand = [t for t in texts[primary] if not ours(primary, t)]
        if hand:                        # источник правды — файл; из оглавления берём только дописанное руками
            kept = [m["index_line"]] if len(hand) < len(texts[primary]) and m.get("index_line") else []
            text = " · ".join(kept + [t for t in hand if t not in kept])
            if m.get("index_line") != text:
                upd["index_line"] = text
        if m.get("index_via"):          # был соседом в чужой строке, теперь у него своя
            upd["index_via"] = ""
        if emitted is not None and primary in emitted:
            moved = emitted[primary].get("moment") != e["moment"]      # строку перенесли в другой раздел руками
        else:
            moved = not newer(primary)
        if (hand or moved or not m.get("moment")) and m.get("moment") != e["moment"] \
                and (not m.get("moment") or e["moment"] != "Разное"):
            upd["moment"] = e["moment"]
        if upd:
            if not dry:
                set_meta(os.path.join(mem, primary), upd)
            metas[primary].update(upd)
        for o in others:
            if o != primary and not metas[o].get("index_line") and metas[o].get("index_via") != primary:
                if not dry:
                    set_meta(os.path.join(mem, o), {"index_via": primary})
                metas[o]["index_via"] = primary
    if stray and not dry:
        head = add_to_head(mem, head, stray)
        notes.append(f"строк без своего файла перенесено в шапку: {len(stray)}")

    # 2) кандидаты
    now = time.time()
    cands = []
    for r in sorted(rules):
        m = metas[r]
        if m.get("index_via") in rules or r in in_head:
            continue
        line = m.get("index_line") or f"[{m.get('name') or os.path.basename(r)}]({r}) — {m.get('description', '')}".rstrip(" —")
        if f"]({r})" not in line and r not in line:
            line = f"{line} [файл]({r})"
        sc = score(m, os.path.join(mem, r), now)
        mtime = os.path.getmtime(os.path.join(mem, r))
        cands.append({"file": r, "line": "- " + line, "moment": m.get("moment") or "Новое", "score": sc,
                      "proven": sc % 1000 >= 300, "fresh": now - mtime < RECENT_DAYS * 86400, "mtime": mtime})

    # 3) набор по бюджету
    head_text = head.rstrip("\n")
    used_b = len(MARK + "\n" + head_text + "\n\n" + HEAD_END + "\n")     # в ЗНАКАХ — так считает Claude Code
    used_l = head_text.count("\n") + 5
    order = head_sections_order(head)
    for o in order_seen:
        if o not in order:
            order.append(o)
    pos = {}                            # место в прежнем оглавлении: выше стояло — важнее при равенстве
    for i, e in enumerate(entries):
        pos.setdefault(e["files"][0], i)
    chosen, left = [], []
    for c in ranked(cands, pos):
        add_b = len(c["line"] + "\n") + (0 if any(x["moment"] == c["moment"] for x in chosen)
                                          else len(f"\n## {c['moment']}\n\n"))
        add_l = 1 if any(x["moment"] == c["moment"] for x in chosen) else 4
        if used_b + add_b <= BUDGET_CHARS and used_l + add_l <= BUDGET_LINES:
            chosen.append(c)
            used_b += add_b
            used_l += add_l
        else:
            left.append(c)
    for c in chosen + left:
        if c["moment"] not in order:
            order.append(c["moment"])

    # 4) порядок внутри раздела — как было в оглавлении, новые в конце
    pre, hsecs = split_head(head)
    final = [h for h in hsecs if h not in order] + order
    out = [MARK] + pre + ["", HEAD_END]
    for sec in final:                   # раздел шапки и собранные строки того же раздела — под одним заголовком
        items = [c for c in chosen if c["moment"] == sec]
        items.sort(key=lambda c: (pos.get(c["file"], 10 ** 6), c["file"]))
        body = [l for l in hsecs.get(sec, []) if l.strip()] + [c["line"] for c in items]
        if body:
            out += ["", f"## {sec}", ""] + body
    memory_md = "\n".join(out) + "\n"

    idx = ["# Всё, что не вошло в оглавление", "",
           "Собрано memory_index.py. Полный список правил этой папки, которым не хватило места в MEMORY.md.",
           "Искать по слову: grep в папке памяти. Ничего не удалено — каждое правило лежит своим файлом.", ""]
    for sec in order:
        items = [c for c in left if c["moment"] == sec]
        if items:
            idx += [f"## {sec}", ""] + [c["line"] for c in sorted(items, key=lambda c: c["file"])] + [""]
    index_md = "\n".join(idx) + "\n"

    # 5) сверка счёта
    shown = set(c["file"] for c in chosen)
    listed = set(c["file"] for c in left)
    # сосед засчитан, только если строка его «хозяина» реально есть в оглавлении или каталоге
    via = set(r for r in rules if metas[r].get("index_via") in (shown | listed)) | in_head
    lost = sorted(rules - shown - listed - via)
    if lost:
        return 2, f"{mem}: ПОТЕРЯ — файлы не попали ни в оглавление, ни в каталог: {lost}"

    if not dry:
        for name, body in (("MEMORY.md", memory_md), ("INDEX.md", index_md)):
            p = os.path.join(mem, name)
            old = read(p) if os.path.exists(p) else None
            if old != body:
                write_atomic(p, body)
        write_atomic(os.path.join(mem, HEAD_STATE), head.replace("\r\n", "\n"))
        write_atomic(os.path.join(mem, LINES_STATE), json.dumps(
            {c["file"]: {"line": c["line"][2:].strip(), "moment": c["moment"]} for c in chosen},
            ensure_ascii=False, indent=0, sort_keys=True))
    size = len(memory_md)
    msg = (f"{os.path.basename(os.path.dirname(mem))}/{os.path.basename(mem)}: оглавление {size} знаков / {memory_md.count(chr(10))} строк, "
           f"показано {len(chosen)}, в каталоге {len(left)}, через соседа {len(via)}, всего файлов {len(rules)}")
    if notes:
        msg += "; " + "; ".join(notes)
    if size > 25000 or memory_md.count("\n") > 200:
        return 3, msg + " — ПРЕВЫШЕН ЛИМИТ (шапка слишком большая?)"
    return 0, ("" if quiet and not notes else msg)


def main(argv):
    for stream in (sys.stdout, sys.stderr):     # чужая консоль может быть не в UTF-8
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    dry = "--dry" in argv
    quiet = "--quiet" in argv
    args = [a for a in argv if not a.startswith("--")]
    dirs = args
    if "--all" in argv:
        dirs = sorted(glob.glob(os.path.join(PROJECTS_ROOT, "*", "memory")))
        if CENTRAL and CENTRAL not in dirs:
            dirs.insert(0, CENTRAL)
    worst = 0
    for d in dirs:
        if not os.path.exists(os.path.join(d, "MEMORY.head.md")):
            continue
        try:
            code, msg = build(d, dry=dry, quiet=quiet)
        except Exception as e:           # сборщик не имеет права ронять сессию — но обязан сказать
            code, msg = 4, f"{d}: сборщик упал: {type(e).__name__}: {e}"
        worst = max(worst, code)
        if msg:
            print(msg)
    return worst


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
