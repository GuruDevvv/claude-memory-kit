# -*- coding: utf-8 -*-
"""Запреты хука memory-recall-hook.py: что останавливает и что обязан пропускать.

    python tests/test_guard.py
"""
import importlib.util
import os
import sys

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("recall", os.path.join(KIT, "hooks", "memory-recall-hook.py"))
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("OK   " if ok else "FAIL ") + name + ("" if ok else "  [" + str(detail) + "]"))


def bash(command, cfg=None, tool="Bash"):
    hit = hook.guard({"tool_name": tool, "tool_input": {"command": command}}, cfg or {})
    return hit[0] if hit else None


STOP = [
    "git add -A",
    "git add --all",
    "git add .",
    "cd repo && git add -A && git commit -m x",
    "git -C E:/work/shop add -A",
    "git add -A .",
    "git commit -am \"правка\"",
    "git commit -a -m 'правка'",
    "git status\ngit add .\ngit commit -m x",
    "git add . && git push",
]
PASS = [
    "git add README.md",
    "git add ./README.md src/app.py",
    "git add \"docs/план.md\"",
    "git commit -m \"запретили git add -A и git add .\"",
    "git commit -m 'не делать git commit -a'",
    "git commit --amend -m x",
    "git commit -F msg.txt",
    "git add -p file.py",
    "git add .gitignore",
    "git add ..\\other\\file.md",
    "echo git add -A",
    "git log --all --oneline",
    "git diff -a",
    "cat > msg.txt <<'EOF'\nне делать git add -A\nEOF\ngit commit -F msg.txt",
    "git add src/ -- tests/test_a.py",
]
for c in STOP:
    check("останавливает: " + c.replace("\n", " ⏎ "), bash(c) == "git_add_all")
for c in PASS:
    check("пропускает:    " + c.replace("\n", " ⏎ "), bash(c) is None, bash(c))

check("в PowerShell тоже останавливает", bash("git add -A", tool="PowerShell") == "git_add_all")
check("чужой инструмент не трогает", bash("git add -A", tool="Read") is None)
check("выключатель целиком", bash("git add -A", {"guard": False}) is None)
check("выключатель одного запрета", bash("git add -A", {"guard": {"git_add_all": False}}) is None)

# свой запрет из настроек: у инструмента должно быть поле с нужным значением
voice = {"guards": [{"key": "voice_ru", "tool": "voicebox.*speak", "require": {"language": "ru"},
                     "when_text": "[а-яё]", "message": "язык"}]}


def speak(ti):
    hit = hook.guard({"tool_name": "mcp__voicebox__speak", "tool_input": ti}, voice)
    return hit[0] if hit else None


check("русский текст без языка — останавливает", speak({"text": "Привет, готово"}) == "voice_ru")
check("русский текст с language en — останавливает", speak({"text": "Привет", "language": "en"}) == "voice_ru")
check("русский текст с language ru — пропускает", speak({"text": "Привет", "language": "ru"}) is None)
check("английский текст без языка — пропускает", speak({"text": "Hello there"}) is None)
check("запрет без условия не срабатывает", hook.guard({"tool_name": "Bash", "tool_input": {"command": "ls"}},
                                                    {"guards": [{"key": "x", "message": "m"}]}) is None)
check("битое выражение в своём запрете хук не роняет",
      hook.guard({"tool_name": "Bash", "tool_input": {"command": "ls"}},
                 {"guards": [{"key": "x", "pattern": "(", "message": "m"}]}) is None)

print("\nитого: %d из %d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
