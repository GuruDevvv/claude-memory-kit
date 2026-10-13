#!/usr/bin/env python3
"""Отчёт: помогает ли память. Две части, обе по неделям.

1. Поправки человека. По архиву разговоров считается, как часто человек поправляет агента:
   реплики со словами повтора и недовольства («опять», «я же говорил», «не так») на 100 его реплик.
   Архив хранит прошлые сессии, поэтому цифра «до» получается сразу, без ожидания.
   Счёт грубый. Проверка глазами 13.10.2026 на 2235 репликах: слова ловят около 1 реплики из 100,
   и примерно половина пойманного — не поправка («почему не прошли?», «вводится снова»). Недельные числа
   в единицах штук — шум, а не замер. Годится как сигнал «стало резко хуже», не как доказательство пользы.
2. Срабатывания. По журналу <общая память>/.metrics/events.jsonl: сколько раз правила показаны,
   сколько раз искали по памяти, что остановили запреты, какие правила показывают чаще всего.

  python memory_report.py                 # таблица на экран
  python memory_report.py --examples 20   # плюс примеры реплик, засчитанных как поправка: проверить счёт глазами
  python memory_report.py --write         # записать в <общая память>/reviews/metrics.md
  python memory_report.py --weeks 8       # сколько последних недель показать

Меньше 30 реплик за неделю — вместо доли пишется «мало данных».
"""
import argparse
import collections
import datetime
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIN_REPLICAS = 30
CONTEXT_CHARS = 250         # сколько знаков конца ответа агента показывать модели перед репликой
BATCH = 40                  # реплик в одном обращении к модели
KINDS = ("нет", "поправка", "повтор")
LONG_REPLICA = 800          # длиннее — это постановка задачи или вставленный текст, а не поправка

# Признаки поправки: основы и обороты. Проверять глазами через --examples после любой правки списка.
MARKERS = [
    r"\bопять\b", r"\bснова\b(?!\s+(?:запус|откр|собер|провер|прогон|сдела))", r"\bя\s+же\s+(?:говорил|просил|писал|сказал)",
    r"\bсколько\s+(?:можно|раз)\b", r"\bпочему\s+(?:ты\s+)?(?:не|опять|снова)\b", r"\bзачем\s+ты\b",
    r"\bне\s+так\b", r"\bне\s+то\b(?!\s*,?\s*что)", r"\bты\s+не\s+понял", r"\bповторяю\b", r"\bя\s+не\s+(?:это|об\s+этом|про\s+это|просил)",
    r"\bне\s+просил\b", r"\bне\s+надо\s+было\b", r"\bкто\s+тебя\s+просил\b", r"\bя\s+устал\b", r"\bнадоел", r"\bне\s+работает\s+опять",
    r"\bты\s+(?:опять|снова|забыл|проигнор)", r"\bгде\s+(?:ты\s+)?это\s+взял\b", r"\bне\s+понимаю\s+(?:тебя|что\s+ты)",
]
MARKER_RE = re.compile("|".join(MARKERS), re.I)


def config():
    try:
        with open(os.path.join(BASE, "memory-kit.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def week_of(day):
    """Понедельник недели, в которую попадает день."""
    return day - datetime.timedelta(days=day.weekday())


def replicas(archive, root):
    """Реплики человека из архива: (день, проект, текст). Первая реплика сессии не берётся: это постановка
    задачи. Сессии вне корня проектов (пробные, временные папки) пропускаются."""
    root = os.path.normcase(os.path.abspath(root)) if root else None
    for path in glob.glob(os.path.join(archive, "*", "*.md")):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read().replace("\r\n", "\n")
        except OSError:
            continue
        m = re.search(r"(?m)^Проект: (.*)$", text)
        project = m.group(1).strip() if m else ""
        if root and not os.path.normcase(project.replace("/", os.sep)).startswith(root):
            continue
        first, before = True, ""
        for block in re.split(r"(?m)^(?=## \[)", text)[1:]:
            head = re.match(r"## \[(\d{4}-\d{2}-\d{2})[^\]]*\] (\S+)\n", block)
            if not head:
                continue
            body = block[head.end():].strip()
            if head.group(2) != "человек":
                before = body or before          # на что человек отвечает: конец последнего ответа агента
                continue
            if first:
                first = False
                continue
            if body:
                yield (datetime.date.fromisoformat(head.group(1)), re.split(r"[\\/]", project.rstrip("\\/"))[-1],
                       body, re.sub(r"\s+", " ", before)[-CONTEXT_CHARS:])


def is_correction(text):
    return len(text) <= LONG_REPLICA and bool(MARKER_RE.search(text))


def rid(text):
    """Имя реплики в файле разметки: по тексту, чтобы та же реплика не размечалась дважды."""
    return hashlib.sha1(re.sub(r"\s+", " ", text).strip().encode("utf-8")).hexdigest()[:16]


def load_labels(central):
    out = {}
    try:
        with open(os.path.join(central, ".metrics", "labels.jsonl"), encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                    out[row["id"]] = row["k"]
                except (ValueError, KeyError):
                    pass
    except OSError:
        pass
    return out


LABEL_PROMPT = """Ниже реплики человека, который работает с ИИ-агентом. Перед каждой — конец ответа агента, на который человек отвечает.
Для каждой реплики реши, поправляет ли человек агента.

- "повтор" — человек поправляет и при этом говорит или даёт понять, что уже просил, говорил или объяснял это раньше.
- "поправка" — человек недоволен тем, что агент сделал или написал: сделал не то, понял не так, ошибся в факте, написал непонятно или слишком длинно, нарушил договорённость.
- "нет" — всё остальное: новая задача, вопрос, уточнение, согласие, ответ на вопрос агента, вопрос «почему» про мир, а не упрёк агенту.

Сомневаешься — "нет". Ответ: только массив JSON, по одному объекту на реплику, в том же порядке, без пояснений.
Образец ответа на три реплики:
[{"n": 1, "k": "нет"}, {"n": 2, "k": "поправка"}, {"n": 3, "k": "повтор"}]

Реплики:
"""


def ask_model(batch, command):
    """Разметка пачки одной короткой сессией Claude Code без хуков, правил и инструментов.
    Возвращает список меток той же длины или None."""
    lines = []
    for n, (_, _, text, before) in enumerate(batch, 1):
        lines.append("%d.\nагент до этого: …%s\nчеловек: %s\n" % (n, before or "(нет)", re.sub(r"\s+", " ", text)))
    try:
        r = subprocess.run(command + ["-p", "--model", "haiku", "--no-session-persistence", "--setting-sources", "",
                                      "--strict-mcp-config", "--disable-slash-commands", "--tools", "",
                                      "--system-prompt", "Ты размечаешь реплики. Отвечаешь только массивом JSON."],
                           input=LABEL_PROMPT + "\n".join(lines), capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=300, cwd=tempfile.gettempdir())
        m = re.search(r"\[.*\]", r.stdout, re.S)
        rows = json.loads(m.group(0)) if m else None
    except Exception:
        return None
    if not isinstance(rows, list) or len(rows) != len(batch):
        return None
    out = [str(x.get("k", "")).strip().lower() if isinstance(x, dict) else "" for x in rows]
    return out if all(k in KINDS for k in out) else None


def label(rows, central, limit, command):
    """Разметить ещё не размеченные реплики. Каждая размечается один раз, итог дописывается в labels.jsonl."""
    done = load_labels(central)
    todo = [r for r in rows if len(r[2]) <= LONG_REPLICA and rid(r[2]) not in done]
    todo = list({rid(r[2]): r for r in todo}.values())[:limit]
    if not todo:
        print("разметка: новых реплик нет")
        return 0
    folder = os.path.join(central, ".metrics")
    os.makedirs(folder, exist_ok=True)
    ok = failed = 0
    with open(os.path.join(folder, "labels.jsonl"), "a", encoding="utf-8", newline="\n") as fh:
        for i in range(0, len(todo), BATCH):
            batch = todo[i:i + BATCH]
            kinds = ask_model(batch, command)
            if kinds is None:
                failed += len(batch)
                continue
            for (day, _, text, _), k in zip(batch, kinds):
                fh.write(json.dumps({"id": rid(text), "k": k, "day": day.isoformat()}, ensure_ascii=False) + "\n")
            fh.flush()
            ok += len(batch)
            print("разметка: %d из %d" % (ok, len(todo)), flush=True)
    print("разметка: готово %d, не удалось %d (эти пойдут в следующий запуск)" % (ok, failed))
    return 0 if ok or not failed else 1


def labeled_table(rows, labels, weeks):
    total, fix, again, unknown = (collections.Counter() for _ in range(4))
    for day, _, text, _ in rows:
        w = week_of(day)
        k = labels.get(rid(text)) if len(text) <= LONG_REPLICA else "нет"
        if k is None:
            unknown[w] += 1
            continue
        total[w] += 1
        fix[w] += k in ("поправка", "повтор")
        again[w] += k == "повтор"
    out = []
    for w in sorted(set(total) | set(unknown))[-weeks:]:
        enough = total[w] >= MIN_REPLICAS
        out.append((w.isoformat(), total[w], fix[w], "%.1f" % (100.0 * fix[w] / total[w]) if enough else "мало данных",
                    again[w], "%.1f" % (100.0 * again[w] / total[w]) if enough else "мало данных", unknown[w]))
    return out


def corrections_table(rows, weeks):
    total, bad = collections.Counter(), collections.Counter()
    for day, _, text, _ in rows:
        w = week_of(day)
        total[w] += 1
        bad[w] += 1 if is_correction(text) else 0
    out = []
    for w in sorted(total)[-weeks:]:
        share = "%.1f" % (100.0 * bad[w] / total[w]) if total[w] >= MIN_REPLICAS else "мало данных"
        out.append((w.isoformat(), total[w], bad[w], share))
    return out


def events_of(central):
    rows = []
    for path in sorted(glob.glob(os.path.join(central, ".metrics", "events*.jsonl"))):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        pass
        except OSError:
            pass
    return rows


def events_table(events, weeks):
    by = collections.defaultdict(collections.Counter)
    for e in events:
        try:
            w = week_of(datetime.date.fromisoformat(e["ts"][:10]))
        except Exception:
            continue
        kind = e.get("event")
        by[w][kind] += 1
        if kind in ("recall", "guard") and e.get("blocked"):
            by[w]["blocked"] += 1
    return [(w.isoformat(), c["recall"], c["recall_empty"], c["blocked"], c["guard"], c["search"], c["subagent"])
            for w, c in sorted(by.items())[-weeks:]]


def table(head, rows):
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(str(x) for x in r) + " |" for r in rows]
    return "\n".join(out)


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Отчёт: поправки человека и срабатывания памяти по неделям")
    ap.add_argument("--weeks", type=int, default=8)
    ap.add_argument("--examples", type=int, default=0, help="показать столько реплик, засчитанных как поправка")
    ap.add_argument("--write", action="store_true", help="записать в <общая память>/reviews/metrics.md")
    ap.add_argument("--label", action="store_true", help="разметить новые реплики моделью (нужен Claude Code)")
    ap.add_argument("--max", type=int, default=400, help="сколько реплик разметить за запуск")
    ap.add_argument("--archive", help="папка архива разговоров")
    ap.add_argument("--central", help="папка общей памяти")
    a = ap.parse_args()
    cfg = config()
    central = a.central or cfg.get("central")
    archive = a.archive or cfg.get("sessions_dir") or (os.path.join(os.path.dirname(central), "sessions") if central else None)
    if not central or not archive:
        print("memory_report: нет настроек memory-kit.json, укажи --central и --archive")
        return 1

    seen, rows = set(), []
    for r in replicas(archive, cfg.get("root")):         # продолженная сессия даёт те же реплики дважды
        if r[:3] not in seen:
            seen.add(r[:3])
            rows.append(r)
    if a.label:
        exe = shutil.which("claude")
        if not exe:
            print("разметка: не найдена команда claude. Разметку делает Claude Code, без него остаётся счёт по словам.")
            return 1
        code = label(rows, central, a.max, [exe])
        if not a.write:
            return code                       # с --write после разметки сразу обновляется файл отчёта
    labels = load_labels(central)
    parts = ["# Память: отчёт на %s" % datetime.date.today().isoformat(), "",
             "## Поправки человека, на 100 его реплик", "",
             "Реплик всего: %d. Первая реплика сессии и реплики длиннее %d знаков поправкой не считаются." % (len(rows), LONG_REPLICA), ""]
    if labels:
        parts += ["Размечено моделью, каждая реплика один раз (`--label`). «Повтор» — человек поправляет и говорит, "
                  "что уже просил об этом раньше: это прямой счёт того, что память не удержала.", "",
                  table(["Неделя с", "Реплик", "Поправок", "На 100", "Из них повторов", "Повторов на 100", "Не размечено"],
                        labeled_table(rows, labels, a.weeks))]
    else:
        parts += ["Разметки моделью нет (`--label`), ниже грубый счёт по словам: годится только как сигнал «стало резко хуже».", "",
                  table(["Неделя с", "Реплик", "Поправок", "На 100 реплик"], corrections_table(rows, a.weeks))]
    events = events_of(central)
    parts += ["", "## Срабатывания памяти", ""]
    if events:
        parts.append(table(["Неделя с", "Показов правил", "Тема без правил", "Остановок", "Запретов", "Поисков", "Помощников"],
                           events_table(events, a.weeks)))
        shown = collections.Counter(f for e in events if e.get("event") == "recall" for f in e.get("files") or [])
        if shown:
            parts += ["", "Чаще всего показаны:", ""] + ["- %s — %d" % (f, n) for f, n in shown.most_common(10)]
    else:
        parts.append("Журнал срабатываний пока пуст.")
    text = "\n".join(parts) + "\n"
    if a.write:
        path = os.path.join(central, "reviews", "metrics.md")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print("записано: " + path.replace("\\", "/"))
    else:
        print(text)
    if a.examples:
        found = [(d, p, t) for d, p, t, _ in rows if (labels.get(rid(t)) in ("поправка", "повтор") if labels else is_correction(t))]
        found.sort(reverse=True)
        print("## Примеры реплик, засчитанных как поправка (%d из %d)\n" % (min(a.examples, len(found)), len(found)))
        for d, p, t in found[:a.examples]:
            print("- %s %s [%s]: %s" % (d.isoformat(), p, labels.get(rid(t), "слова"), re.sub(r"\s+", " ", t)[:220]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
