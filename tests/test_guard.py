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
    "git add -- a.py && git commit -m x && grep -r слово . | wc -l",
    "git add a.py; ls .",
    "git commit -m x && python run.py --agent-dir here -a",
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

# свой запрет с text_pattern: запуск ловит pattern (без кавычек), тему задания — text_pattern (с кавычками)
img = {"guards": [{"key": "codex_image",
                   "pattern": r"(?:^|[;&|(]\s*|\n\s*)(?:\S*[\\/])?codex(?:\.exe|\.cmd)?\s[^;&|\n]*?\bexec\b",
                   "text_pattern": r"image_gen|imagegen|изображени|картинк|нарисуй|\.(?:png|jpe?g|webp)\b",
                   "message": "m"}]}
for c in ['codex exec --skip-git-repo-check "Сгенерируй изображение кота. Сохрани как out.png"',
          "cd mock && codex exec -m gpt-6.1-sol 'нарисуй картинку'",
          'C:/bin/codex.cmd --search exec "use image_gen"']:
    check("картинка через codex — останавливает: " + c, bash(c, img) == "codex_image", bash(c, img))
for c in ['python codex_img.py "нарисуй кота" out.png',
          'codex exec -s read-only "проверь дифф на ошибки"',
          "echo 'codex exec нарисуй картинку'",
          'git commit -m "запрет: codex exec на картинку"',
          'codex review "картинка"']:
    check("пропускает: " + c, bash(c, img) is None, bash(c, img))

print("\nитого: %d из %d" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
