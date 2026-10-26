# Prompt for the agent

Open Claude Code or Codex in the root folder of your projects and paste the text below.

```
Install memory-kit for me: memory that carries over between sessions.

1. Clone https://github.com/GuruDevvv/claude-memory-kit into a _memory-kit subfolder
   of the current folder and read README.md, INSTALL.md and docs/GUIDE.md there.
2. Ask me which folder is the root of my projects (suggest the current one)
   and in which project to start the first HANDOFF.md note.
3. Check that python, node and git are installed. If something is missing, tell me and stop.
4. Check whether I already have my own hooks, my own memory rules or my own memory folder.
   If I do, install step by step following INSTALL.md and replace nothing of mine without asking.
   If I don't, run install.py with --dry-run and tell me in plain words which files
   will appear and which will change. Wait for my "yes", then run it without --dry-run, with --project.
5. Either way, run scripts/kit_check.py from the agent folder with --kit and show me the result.
   Until the last line says "Всё на месте." ("All in place."), the install is not done.
6. Open the shared memory header MEMORY.head.md, ask me for 2–3 lines about myself
   (who I am, how to talk to me) and write them into the owner section ("Про владельца").
7. Tell me what to do next: open a new session in the project folder; in Codex type /hooks
   and approve the hooks; at the end of the work say "stop". The _memory-kit folder can be
   deleted after the install; ask me.

Delete nothing. Do not rewrite my existing CLAUDE.md, AGENTS.md or settings:
only add the kit's section and keep backups. Do not grant yourself permissions; show me
what to add.
```
