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
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIN_REPLICAS = 30
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
        first = True
        for block in re.split(r"(?m)^(?=## \[)", text)[1:]:
            head = re.match(r"## \[(\d{4}-\d{2}-\d{2})[^\]]*\] (\S+)\n", block)
            if not head or head.group(2) != "человек":
                continue
            if first:
                first = False
                continue
            body = block[head.end():].strip()
            if body:
                yield datetime.date.fromisoformat(head.group(1)), re.split(r"[\\/]", project.rstrip("\\/"))[-1], body


def is_correction(text):
    return len(text) <= LONG_REPLICA and bool(MARKER_RE.search(text))


def corrections_table(rows, weeks):
    total, bad = collections.Counter(), collections.Counter()
    for day, _, text in rows:
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
    ap.add_argument("--archive", help="папка архива разговоров")
    ap.add_argument("--central", help="папка общей памяти")
    a = ap.parse_args()
    cfg = config()
    central = a.central or cfg.get("central")
    archive = a.archive or cfg.get("sessions_dir") or (os.path.join(os.path.dirname(central), "sessions") if central else None)
    if not central or not archive:
        print("memory_report: нет настроек memory-kit.json, укажи --central и --archive")
        return 1

    rows = list(dict.fromkeys(replicas(archive, cfg.get("root"))))     # продолженная сессия даёт те же реплики дважды
    parts = ["# Память: отчёт на %s" % datetime.date.today().isoformat(), "",
             "## Поправки человека, на 100 его реплик", "",
             "Реплик всего: %d. Первая реплика сессии и реплики длиннее %d знаков поправкой не считаются." % (len(rows), LONG_REPLICA),
             "", table(["Неделя с", "Реплик", "Поправок", "На 100 реплик"], corrections_table(rows, a.weeks))]
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
        found = [(d, p, t) for d, p, t in rows if is_correction(t)]
        found.sort(reverse=True)
        print("## Примеры реплик, засчитанных как поправка (%d из %d)\n" % (min(a.examples, len(found)), len(found)))
        for d, p, t in found[:a.examples]:
            print("- %s %s: %s" % (d.isoformat(), p, re.sub(r"\s+", " ", t)[:220]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
