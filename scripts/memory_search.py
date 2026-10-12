# -*- coding: utf-8 -*-
"""Поиск по всей памяти: правила, справочники, журналы — включая то, что не влезло в оглавление.

Зачем. В MEMORY.md помещается около трети правил, остальное лежит в INDEX.md и для агента
почти не существует: он туда не заглядывает (замер 12.10.2026 — 301 строка из 441 вне оглавления).
Поиск отдаёт короткие строки со ссылкой на файл; сам файл агент открывает, только если строка по делу.

Как устроено. Встроенный в Python SQLite с полнотекстовым поиском (FTS5): ни сервера, ни ключей,
ни своего хранилища. Индекс каждый раз собирается в памяти из файлов — устареть ему негде.
Русские окончания: слово запроса обрезается до основы и ищется как начало слова («коммитить» -> «коммит*»).
Единица поиска — файл правила или справочника, а в журнале — одна запись («## [дата]»).

Запуск:
  python memory_search.py "деплой на сервер pm2"      # общая память + память проекта текущей папки
  python memory_search.py "..." -n 8                   # сколько строк показать (по умолчанию 5)
  python memory_search.py "..." --all                  # память всех проектов
  python memory_search.py "..." --kind rule|ref|journal|other
  python memory_search.py "..." --dir <папка memory>   # искать в названной папке (можно несколько раз)
  python memory_search.py "..." --json                 # для скриптов и хуков
"""
import argparse
import glob
import json
import os
import re
import sqlite3
import sys

SKIP_FILES = {"MEMORY.md", "MEMORY.head.md", "INDEX.md", "PROJECT_INDEX.md"}
MAX_DOC_CHARS = 20000       # длинный хвост файла на ранг почти не влияет, а сборку замедляет
STOP = set("""и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только ее мне
было вот от меня еще нет о из ему теперь когда даже ну вдруг ли если уже или ни быть был него до вас нибудь
опять уж вам ведь там потом себя ничего ей может они тут где есть надо ней для мы тебя их чем была сам чтоб
без будто чего раз тоже себе под будет ж тогда кто этот того потому этого какой совсем ним здесь этом один
почти мой тем чтобы нее сейчас были куда зачем всех никогда можно при наконец два об другой хоть после над
больше тот через эти нас про всего них какая много разве три эту моя впрочем хорошо свою этой перед иногда
лучше чуть том нельзя такой им более всегда конечно всю между это эта нужно надо давай сделай сделать
the a an of to in on for and or is it this that with as at by be""".split())


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


def norm(text):
    return text.lower().replace("ё", "е")


def stem(word):
    """Грубая основа: срезаем окончание, чтобы «коммитить», «коммичу» и «коммит» нашли друг друга."""
    w = norm(word)
    if re.fullmatch(r"[a-z0-9_.\-]+", w):
        return w                       # команды, имена файлов, английские слова — как есть
    if len(w) <= 3:
        return w
    cut = 3 if len(w) >= 9 else 2 if len(w) >= 6 else 1
    return w[:len(w) - cut]


def query_terms(text):
    words = re.findall(r"[0-9A-Za-zА-Яа-яЁё_][0-9A-Za-zА-Яа-яЁё_.\-]*", text)
    out = []
    for w in words:
        w = w.strip(".-")
        if len(w) < 2 or norm(w) in STOP:
            continue
        s = stem(w)
        if s not in out:
            out.append(s)
    return out


def meta_of(text):
    """Поля шапки файла правила (плоско) и тело без шапки."""
    lines = text.replace("\r\n", "\n").split("\n")
    m = {}
    if lines and lines[0].strip() == "---":
        for i in range(1, min(len(lines), 80)):
            if lines[i].strip() == "---":
                for ln in lines[1:i]:
                    mm = re.match(r"^\s*([A-Za-z_]+):\s*(.*?)\s*$", ln)
                    if mm and mm.group(2):
                        v = mm.group(2)
                        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                            try:
                                v = json.loads(v) if v[0] == '"' else v[1:-1]
                            except ValueError:
                                v = v[1:-1]
                        m.setdefault(mm.group(1), v)
                return m, "\n".join(lines[i + 1:])
    return m, "\n".join(lines)


def read(path):
    with open(path, "rb") as fh:
        return fh.read().decode("utf-8", errors="replace")


def first_line(body):
    for ln in body.split("\n"):
        s = ln.strip().lstrip("#").strip()
        if s and not s.startswith(("---", "<!--", "|")):
            return s[:220]
    return ""


def docs_of(mem):
    """Единицы поиска одной папки памяти: (вид, путь от папки, якорь, заголовок, текст, пометка)."""
    for root, dirs, files in os.walk(mem):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "archive"]
        rel_root = os.path.relpath(root, mem).replace(os.sep, "/")
        top = rel_root.split("/")[0]
        for f in sorted(files):
            if not f.endswith(".md") or f in SKIP_FILES or f.startswith("_"):
                continue
            path = os.path.join(root, f)
            rel = f if rel_root == "." else rel_root + "/" + f
            try:
                text = read(path)
            except OSError:
                continue
            if top == "projects":                       # журнал: одна запись — одна единица
                parts = re.split(r"(?m)^(?=## \[)", text.replace("\r\n", "\n"))
                for part in parts:
                    head = re.match(r"## \[([^\]]+)\]", part)
                    if head:
                        yield ("journal", rel, head.group(1), f[:-3] + ", " + head.group(1),
                               part[:MAX_DOC_CHARS], "")
                continue
            m, body = meta_of(text)
            kind = "rule" if top == "feedback" or (m.get("type") == "feedback") else "ref" if top == "ref" else "other"
            title = m.get("index_line") or m.get("description") or first_line(body) or f
            note = " ".join(x for x in (m.get("status", ""), ("seen " + m["seen"]) if m.get("seen") else "") if x)
            yield (kind, rel, "", title, (m.get("description", "") + "\n" + body)[:MAX_DOC_CHARS], note)


def memory_dirs(cwd, everything, extra):
    if extra:
        return [os.path.abspath(d) for d in extra]
    if everything:
        dirs = sorted(glob.glob(os.path.join(PROJECTS_ROOT, "*", "memory")))
    else:
        want = re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(cwd)).lower()
        dirs = [d for d in glob.glob(os.path.join(PROJECTS_ROOT, "*", "memory"))
                if os.path.basename(os.path.dirname(d)).lower() == want]
    if CENTRAL and os.path.isdir(CENTRAL) and not any(os.path.normcase(d) == os.path.normcase(CENTRAL) for d in dirs):
        dirs.insert(0, CENTRAL)
    return dirs


def search(query, dirs, limit=5, kind=None):
    """Список находок: {kind, dir, file, anchor, title, note, snippet}. Пустой запрос или нет совпадений — []."""
    terms = query_terms(query)
    if not terms:
        return []
    db = sqlite3.connect(":memory:")
    db.execute("create virtual table d using fts5(title, body, kind unindexed, dir unindexed, file unindexed, "
               "anchor unindexed, shown unindexed, note unindexed, tokenize=\"unicode61 remove_diacritics 0\")")
    rows = []
    for mem in dirs:
        for k, rel, anchor, title, text, note in docs_of(mem):
            if kind and k != kind:
                continue
            rows.append((norm(title), norm(text), k, mem, rel, anchor, title, note))
    db.executemany("insert into d values (?,?,?,?,?,?,?,?)", rows)
    # сперва строго (все слова), потом мягко (любое): короткий запрос точнее, длинный не остаётся без ответа
    quoted = ['"%s"*' % t.replace('"', "") for t in terms]
    seen, out = set(), []
    for expr in (" AND ".join(quoted), " OR ".join(quoted)):
        try:
            cur = db.execute(
                "select kind, dir, file, anchor, shown, note, snippet(d, 1, '', '', ' … ', 18) "
                "from d where d match ? order by bm25(d, 6.0, 1.0) * "
                "case kind when 'rule' then 1.6 when 'ref' then 1.3 when 'journal' then 0.6 else 1.0 end "
                "limit ?", (expr, limit * 3))     # правило важнее рассказа о том, как на него наступили
        except sqlite3.OperationalError:
            continue
        for k, mem, rel, anchor, title, note, snip in cur:
            key = (mem, rel, anchor)
            if key in seen:
                continue
            seen.add(key)
            out.append({"kind": k, "dir": mem, "file": rel, "anchor": anchor, "title": title, "note": note,
                        "snippet": re.sub(r"\s+", " ", snip).strip()})
            if len(out) >= limit:
                return out
    return out


KIND_RU = {"rule": "правило", "ref": "справочник", "journal": "журнал", "other": "заметка"}


def render(hits):
    lines = []
    for h in hits:
        where = os.path.join(h["dir"], h["file"]).replace(os.sep, "/")
        tag = KIND_RU[h["kind"]] + (", " + h["note"] if h["note"] else "")
        text = h["title"] if h["kind"] != "journal" else h["title"] + ": " + h["snippet"]
        lines.append("- [%s] %s\n  %s" % (tag, re.sub(r"\s+", " ", text)[:300], where))
    return "\n".join(lines)


def main(argv):
    for stream in (sys.stdout, sys.stderr):     # чужая консоль может быть не в UTF-8
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Поиск по памяти: правила, справочники, журналы")
    ap.add_argument("query", nargs="+")
    ap.add_argument("-n", type=int, default=5)
    ap.add_argument("--all", action="store_true", help="память всех проектов")
    ap.add_argument("--kind", choices=sorted(KIND_RU))
    ap.add_argument("--dir", action="append", help="искать в этой папке памяти")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    dirs = memory_dirs(os.getcwd(), a.all, a.dir)
    hits = search(" ".join(a.query), dirs, a.n, a.kind)
    if a.json:
        print(json.dumps(hits, ensure_ascii=False, indent=1))
    elif hits:
        print(render(hits))
    else:
        print("ничего не найдено: " + " ".join(a.query))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
