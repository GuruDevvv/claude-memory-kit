# -*- coding: utf-8 -*-
"""Проверка сборщика на песочнице. Запуск: test_index.py <путь к memory_index.py>"""
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time

script = os.path.abspath(sys.argv[1])
spec = importlib.util.spec_from_file_location("mi", script)
mi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mi)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("OK   " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))


def rule(mem, name, line, moment="Тест", status="правило", seen=1, age_days=0):
    p = os.path.join(mem, "feedback", name + ".md")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    io.open(p, "w", encoding="utf-8", newline="\n").write(
        f"---\nname: {name}\ndescription: d\nmetadata:\n  type: feedback\n  status: {status}\n"
        f"  seen: {seen}\n  index_line: \"{line}\"\n  moment: {moment}\n---\n\nтело\n")
    if age_days:
        t = time.time() - age_days * 86400
        os.utime(p, (t, t))
    return p


def sandbox():
    mem = tempfile.mkdtemp(prefix="mi_")
    io.open(os.path.join(mem, "MEMORY.head.md"), "w", encoding="utf-8", newline="\n").write("# Память\n")
    return mem


def memory(mem):
    return io.open(os.path.join(mem, "MEMORY.md"), encoding="utf-8").read()


def edit(path, old, new):
    s = io.open(path, encoding="utf-8").read()
    assert old in s
    io.open(path, "w", encoding="utf-8", newline="\n").write(s.replace(old, new))


# 1. исправленная в файле строка не откатывается
mem = sandbox()
p = rule(mem, "feedback_a", "[А](feedback/feedback_a.md) — старая формулировка")
mi.build(mem)
time.sleep(1.1)
edit(p, "старая формулировка", "НОВАЯ формулировка")
mi.build(mem)
mi.build(mem)
check("исправленная строка правила остаётся новой", "НОВАЯ формулировка" in memory(mem)
      and "НОВАЯ" in io.open(p, encoding="utf-8").read(), memory(mem))

# 1б. то же без снимка (первая сборка после обновления скрипта)
mem = sandbox()
p = rule(mem, "feedback_a", "[А](feedback/feedback_a.md) — старая формулировка")
mi.build(mem)
snap = os.path.join(mem, ".index-emitted.json")
if os.path.exists(snap):
    os.remove(snap)
time.sleep(1.1)
edit(p, "старая формулировка", "НОВАЯ формулировка")
mi.build(mem)
check("то же без снимка прошлой сборки", "НОВАЯ формулировка" in memory(mem), memory(mem))

# 2. раздел, исправленный в файле, не откатывается
mem = sandbox()
p = rule(mem, "feedback_a", "[А](feedback/feedback_a.md) — текст", moment="Тест")
mi.build(mem)
time.sleep(1.1)
edit(p, "moment: Тест", "moment: Другой")
mi.build(mem)
mi.build(mem)
check("исправленный раздел правила остаётся новым", "## Другой" in memory(mem) and "## Тест" not in memory(mem), memory(mem))

# 3. самолечение живо: строку поправили руками в оглавлении -> уехала в файл
mem = sandbox()
p = rule(mem, "feedback_a", "[А](feedback/feedback_a.md) — текст")
mi.build(mem)
time.sleep(1.1)
edit(os.path.join(mem, "MEMORY.md"), "— текст", "— текст, дописано руками")
mi.build(mem)
check("ручная правка оглавления переезжает в файл", "дописано руками" in io.open(p, encoding="utf-8").read()
      and "дописано руками" in memory(mem))

# 4. самолечение живо: новая строка руками для файла без index_line
mem = sandbox()
rule(mem, "feedback_a", "[А](feedback/feedback_a.md) — текст")
mi.build(mem)
p2 = os.path.join(mem, "feedback", "feedback_b.md")
io.open(p2, "w", encoding="utf-8", newline="\n").write("---\nname: feedback_b\ndescription: d\nmetadata:\n  type: feedback\n---\n\nтело\n")
io.open(os.path.join(mem, "MEMORY.md"), "a", encoding="utf-8", newline="\n").write(
    "- [Б](feedback/feedback_b.md) — строка, дописанная агентом\n")
mi.build(mem)
check("дописанная руками строка для нового файла переезжает в файл",
      "дописанная агентом" in io.open(p2, encoding="utf-8").read() and "дописанная агентом" in memory(mem))

# 5. баллы: подтверждённое старое правило выше свежей пробы
mem = sandbox()
old = rule(mem, "feedback_old", "x", status="правило", seen=5, age_days=90)
new = rule(mem, "feedback_new", "y", status="проба", seen=1)
now = time.time()
so, sn = mi.score(mi.meta(old), old, now), mi.score(mi.meta(new), new, now)
check(f"подтверждённое правило ({so}) выше свежей пробы ({sn})", so > sn)
unproven = rule(mem, "feedback_unp", "z", status="правило", seen=1, age_days=90)
su = mi.score(mi.meta(unproven), unproven, now)
check(f"свежая проба ({sn}) выше старого неподтверждённого ({su}): новое видно две недели", sn > su)

# 6. две сборки сразу: 8 процессов, ни одного падения, оглавление целое
mem = sandbox()
for i in range(40):
    rule(mem, f"feedback_{i:02d}", f"[П{i}](feedback/feedback_{i:02d}.md) — правило номер {i}")
procs = [subprocess.Popen([sys.executable, script, mem], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
         for _ in range(8)]
outs = [pr.communicate() for pr in procs]
codes = [pr.returncode for pr in procs]
errs = b"".join(o[1] for o in outs).decode("utf-8", "replace")
left = [f for f in os.listdir(mem) if f.endswith(".tmp") or f == ".index-lock"]
check("8 сборок одновременно без падений", all(c == 0 for c in codes) and "Traceback" not in errs
      and "упал" not in b"".join(o[0] for o in outs).decode("utf-8", "replace"), f"{codes} {errs[:300]}")
check("после них оглавление целое, мусора нет", memory(mem).count("правило номер") == 40 and not left, str(left))

# 7. брошенный замок не вешает сборку навсегда
mem = sandbox()
rule(mem, "feedback_a", "[А](feedback/feedback_a.md) — текст")
lock = os.path.join(mem, ".index-lock")
io.open(lock, "w").close()
t = time.time() - 600
os.utime(lock, (t, t))
code, msg = mi.build(mem)
check("старый брошенный замок снимается", code == 0 and os.path.exists(os.path.join(mem, "MEMORY.md")), msg)

print(f"\nитого: {sum(results)} из {len(results)}")
sys.exit(0 if all(results) else 1)
