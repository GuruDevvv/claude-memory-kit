# How the agent's memory works

This document has two readers: a person who wants to understand the system, and an agent asked
to install or fix it. Installation is covered in the [README](../README.md); this page explains
how the system is built and why.

Version 2, October 2026. The first version (April 2026) described four layers and a hand-kept
index. What has changed since then is listed at the end.

A note on language: the kit was built for a Russian-speaking user, so some section names, field
values and trigger words it actually matches are Russian. Where this guide mentions one, it keeps
the literal value and gives the English meaning once in parentheses.

## Why any of this

An agent has no memory between sessions. The usual fix is one rules file for everything. With ten
projects it swells: the agent either reads all of it and burns context, or does not read it.
Between sessions of the same project the thread is lost, and every session starts with "where
did we leave off".

The fix has four parts:

1. **Behaviour rules** live in the agent's rules file and are always loaded.
2. **Knowledge** lives as files in memory folders and is loaded through an index.
3. **Automation** on hooks does what someone would otherwise have to remember.
4. **The end-of-session routine** turns the work done into a handoff note, a log and rules.

## Part 1. Behaviour rules

| File | When it loads | What it holds |
|---|---|---|
| `~/.claude/CLAUDE.md` or `~/.codex/AGENTS.md` | every session | how to talk to the person, how to start and end a session, how to write rules |
| `<projects root>/CLAUDE.md` or `AGENTS.md` | sessions inside the root | the HANDOFF note template, what to keep when context is compacted |
| `<project>/CLAUDE.md` or `AGENTS.md` | sessions of that project | project specifics |

Only what must always be followed goes here. This is an order; memory is a reference. If the
same request has to be repeated, the rule is in the wrong place: it belongs in the rules file.

The kit adds one section to these files between the markers `<!-- memory-kit:start -->`
and `<!-- memory-kit:end -->`. Everything outside the markers stays yours.

## Part 2. Knowledge

### Two levels

- **Shared memory**: `~/.agent-memory/central/`. Whatever is useful in two or more projects:
  working rules, references, the logs of all projects, the project list.
- **Project memory**: `~/.claude/projects/<name>/memory/`. Whatever concerns one project:
  deployment specifics, client details, code conventions. In Claude Code this is the built-in
  memory, and it loads its index by itself. Codex uses the same folders.

The project folder name comes from the full path with everything except Latin letters and digits
replaced by a hyphen: `E:\Work\Shop` gives `E--Work-Shop`, `/Users/ann/Work/Shop` gives `-Users-ann-Work-Shop`.

The line is drawn with one question: "will this be useful in two or more projects?" A rule lives
in one place. If a project rule is needed in a second project, it moves to shared memory.

**Why shared memory lives outside `.claude`.** Claude Code treats files inside any `.claude`
folder as sensitive. It allows writing to the memory of *its own* project, but writing to shared
memory from another project's folder asks for confirmation every time, and permission rules do
not override that. Checked with a test run: inside `.claude`, writing the log on "stop" kept
failing; after moving to `~/.agent-memory` it went through without a single prompt.

### What is in shared memory

| Folder or file | What it is |
|---|---|
| `feedback/` | working rules: "do this", "don't do that". One file per rule |
| `ref/` | references: servers, tools, access. Opened by task topic |
| `projects/<Project>.md` | session log of a project, newest entry on top |
| `plans/`, `research/`, `reviews/` | plans, research, post-mortems |
| `MEMORY.md` | the index of rules. Built by a script |
| `MEMORY.head.md` | the index header: who the owner is, section order. Written by hand |
| `INDEX.md` | rules that did not fit into the index |
| `PROJECT_INDEX.md` | the project list. Built by a script |

### A rule file

```
---
name: feedback_time_only_utc
description: Always store time in UTC; convert to the client's time zone only for display
metadata:
  type: feedback
  status: проба
  seen: 1
  index_line: "[Time is UTC only](feedback/feedback_time_only_utc.md) — store in UTC, convert to the client's time zone only for display"
  moment: Before starting
  origin_date: 2026-10-12
  origin_project: Shop
  origin_kind: owner
  origin_quote: "we never store local time again"
---

The rule itself.

**Why:** the reminder went out three hours early.
**How to apply:** every time field in the database is in UTC.
```

Three decisions sit behind this header:

**The index line carries the whole rule.** A count over 383 sessions showed that rule files were
opened 51 times in total. The agent almost never gets as far as the file, so a pointer line with
no content does not work. The file is there for the "why"; the line is what acts.

**Sections are named by moment of work, not by topic.** "Before starting", "When I get a result",
"When I commit". A rule is looked up at the moment of action, not by category.

**Trial and rule.** A new observation is written as `status: проба` (trial), `seen: 1` and applied
gently. If it repeats or the person confirms it, it becomes `status: правило` (rule). A trial
that is not confirmed within two months is deleted. This way one random session does not create a law.

The `origin_*` fields answer "where did this come from": the person said it (`owner`), the agent
proposed it and the person agreed (`owner_ok`), the agent's own conclusion (`mine`), or a
conclusion from a breakage (`incident`). Without them, half a year later you cannot tell the
owner's requirement from the agent's guess.

### The index is built by a script

Claude Code reads only the first 200 lines or 25 thousand characters of `MEMORY.md` and silently
drops the rest. A hand-kept index overflows about once a month, and rules at the tail quietly
stop working.

So the shared-memory index is built by `memory_index.py`:

- it takes the header from `MEMORY.head.md`;
- it takes each rule's line from its `index_line`;
- it puts pinned rules and the «Всегда» ("Always") section first;
- then it takes, in turn, proven rules (`status: правило` with `seen` of 2 or more) and new ones
  (the file was edited in the last two weeks). The alternation keeps new rules from pushing out
  proven ones and vice versa: on a live memory of 440 files, a single shared ranking put
  2 proven rules out of 52 into the index, and a simple bonus for proven rules let in no fresh trials at all;
- it adds lines while they fit into 23.5 thousand characters and 180 lines;
- it puts the rest into `INDEX.md`;
- it checks the count: every rule file must end up in one of the two lists. A lost file is an
  error the agent learns about in the same turn.

If the agent, out of habit, adds a line to `MEMORY.md` by hand, it is not lost: the script moves
it into the `index_line` of the file it links to.

The script only builds folders that contain `MEMORY.head.md`. Project memory is kept by the agent
itself by default. To turn on building there too, put a header of your own into it.


## Searching all of memory

About a third of the rules fit into the index. The rest sit in `INDEX.md`, and the agent hardly
ever looks there. That is why the kit has a search:

```
python scripts/memory_search.py "deploy the site to the server"
```

It returns five short lines with a link to the file: rules, references and individual log entries.
It searches shared memory and the memory of the current folder's project; with `--all`, all projects.

Inside is the SQLite with full-text search that ships with Python. There is no server, no keys and
no storage of its own: the index is built in memory on every run, which takes under a second on
440 files and 4 MB of logs. Russian word endings are handled crudely: the word is cut to its stem
and matched by prefix. It knows no synonyms: "roll out" and "deploy" are different words to it.

A check on 16 working situations in which the agent had already gone wrong at least twice: the
right rule was in the first five search lines in 14 cases. The index held three of those rules.
The situations were chosen among rules outside the index, so the second number is low on purpose:
it shows what the search brings back, not how bad the index is. The result weights were tuned on
these same situations, so on new ones the result will be more modest.

The rules section asks the agent to run the search before a familiar kind of task, before saying
"I found", and before writing a new rule.


## Memory at the right moment

A rule read at the start of a session among 180 index lines is forgotten by the time of the commit.
The `memory-recall-hook.py` hook brings rules in when they are needed. It works in Claude Code and in Codex.

**Before an action.** The agent is about to commit, push or go to a server, and it gets up to five
lines of rules on the topic. Rules come from all of memory, not just the index: by section name
(«Когда коммичу», «Когда деплою»: "When I commit", "When I deploy") and by searching the words of
the topic and of the command itself. A hint on a given topic comes once per session.

The added context reaches the agent together with the command's result, that is, after it.
For a commit that is acceptable. A push or a server command cannot be taken back, so the first
such command in a session is stopped: the rules arrive as the reason for the refusal, the agent
checks them and repeats the command, and the second time it goes through. To turn the stop off:
`"recall_block": false` in `memory-kit.json`. Your own topics are set there with the `recall` key,
following the `GROUPS` list in the hook.

**Along with the request.** Some tasks have no command of their own: fix a slide, redo a screen,
go through a chat. Here the hook looks at the words of the person's request and adds the same up to
five rule lines along with it. Nothing is stopped; a topic comes once per session. Your own topics
are set with the same `recall` key; the request trigger goes in the `prompt` field.

**For helpers.** A helper (subagent) starts without the project note and without memory, so it
presents long-known things as discoveries. At start it gets up to three thousand characters: the
memory search command, the «Всегда» ("Always") section of the index and the beginning of the
`HANDOFF.md` note.

Both paths were checked with a live run of Claude Code 2.1.296: the model sees both the hint and
the refusal with rules, and the helper names the search command word for word.


## Conversation archive

Claude Code keeps session records for 30 days and then deletes them. With them goes whatever did
not make it into the log or a rule: why something was decided, what exactly the person said.

`session_archive.py` takes only the conversation from a session record: what the person typed and
what the agent answered in words. Tool calls, their output, the model's thinking and helpers are
left out. The conversation is under one percent of the record: on a live install, 349 sessions
weighing 2 GB produced an 8 MB archive.

A session goes into the archive once its record has not changed for half an hour. The end-of-turn
hook takes five sessions at a time; for a first pass over all of them: `python scripts/session_archive.py`.
The archive lives in `~/.agent-memory/sessions`, one folder per project, one file per session.

To search: `python scripts/memory_search.py "what we decided about the discount" --sessions`. A hit
is the person's message together with the agent's answer, with project and time.

Keys, tokens and "password: …" are cut out by patterns. A pattern is not a guarantee: keep the
archive on your own machine and do not put it in git. From Codex records, only conversations with
a person go into the archive; `codex exec` runs and helpers are skipped.

## Part 3. Automation

Five hook files; the `memory-recall-hook.py` hints are described in "Memory at the right moment". All of them read settings from `memory-kit.json` next to the `hooks` folder. No settings
file, no output. An error inside a hook never gets in the way of the session.

| Event | File | What it does |
|---|---|---|
| session start | `session-start-context.js` | pulls changes from the remote repository; adds `HANDOFF.md`, three log entries and the memory index |
| end of turn | `session-summary-check.js` | once a day reminds you to save the results if the session was a working one |
| end of turn | `memory-index-hook.py stop` | rebuilds the indexes and the project list; through `memory_upkeep.py` trims logs to 5 entries (older ones go to `projects/archive/`) and checks index links: a broken link stops the end of the turn once |
| after a file write (Claude Code) | `memory-index-hook.py post` | rebuilds the index if the file written is in a memory folder |
| before the conversation is compacted (Claude Code) | `session-start-context.js precompact` | saves a copy of `HANDOFF.md` to `handoff-snapshots/<project>/` in the agent's folder, keeps the last 10; after compaction the session-start hook tells the agent where the copy is and asks it to add decisions from the compacted part |

### Pulling changes at session start

The hook fetches the remote repository's history and checks whether the local branch is behind.

- Nothing new: it stays quiet.
- Something new, no unsaved edits, no merge needed: it updates the files and tells the agent
  what came in.
- Something new, but the folder has unsaved edits or the branch has moved ahead: it leaves the
  files alone and only reports. The agent will offer to update rather than do it silently.

The hook waits for the network no longer than ten seconds and never asks for a password.

### What is added to the context

- `HANDOFF.md` from the current folder, up to 5 thousand characters.
- The last three entries of the `projects/<folder name>.md` log, up to 3.5 thousand characters.
- The shared-memory index.
- For Codex, the project memory index as well: it does not load it on its own.

The budgets are separate: a long note does not push out the log.

### End-of-turn reminder

It fires only when the session record is already over 150 KB, meaning there was work and not
just talk. It checks two signs: nothing was written to the log today; the project note has not
changed for more than two hours. It shows the person one line and does not bother them again
that day. It blocks nothing.

### Project list

`project_index.py` walks the root folder two levels deep, finds folders with `HANDOFF.md` and takes
three lines from each: «Обновлено:» ("Updated:"), «## Статус:» ("## Status:") and the first line of
the «## Следующий шаг» ("## Next step") section. So the form of these lines in the note must not change.

## Part 4. The HANDOFF note and the log

**`HANDOFF.md`** sits in the project root and answers "where are we now". No more than 25 lines,
always the same sections: status, current state, tasks, blockers, what was tried and did not work,
fragile spots, next step, a prompt for the next session. It is overwritten.

**The log** `projects/<Project>.md` answers "what happened". A new entry goes on top and is not
edited: date and topic, «Сделано» ("Done"), «Дальше» ("Next"), «Файлы» ("Files"), «Статус» ("Status").

The note is short and always in context. The log is long; only its last three entries go into
context, and the agent opens the rest when needed.

## Part 5. The end-of-session routine

The person says «стоп» ("stop"). The agent takes six steps:

1. Updates `HANDOFF.md` and writes a prompt for the next session at the end.
2. Commits the session's edits file by file and pushes if the project has a remote repository.
   Unfamiliar files are shown as a list, and the agent asks.
3. Looks for stuck background processes, shows them and asks what to stop.
4. Writes a log entry.
5. Reviews the session: what went wrong, what worked. Checks memory and creates a rule only
   if there is no such rule yet. Zero new rules is a normal outcome.
6. Prints the prompt in the chat.

After that the hook rebuilds the index and the project list.

The routine is written in the rules file rather than hard-coded in a script: its steps need
judgment. Scripts get only what needs no judgment: building the indexes and the project list,
loading context.

### Where new rules come from

- **The end-of-session review**: step 5.
- **A correction along the way.** The person says "not like that" or "yes, exactly", and the agent
  writes a rule right away with their words in `origin_quote`.
- **Repetition.** The situation comes up again, and a "trial" becomes a "rule".

## Codex specifics

- The rules file is called `AGENTS.md`; hooks are registered in `~/.codex/hooks.json`. Codex
  has the same events and hook response format as Claude Code, so the hook files are shared.
- Codex skips new hooks until they are trusted: the `/hooks` command. If you skip this, the agent will tell you itself in the first answer of the session: the start hook puts a marker line into the context, and the rules section tells the agent to warn the person when the marker is missing.
- Codex has no memory loading of its own; both indexes are added by the session-start hook.
- Codex edits files with a different tool, so the index is rebuilt not right after a write
  but at the end of the turn.
- For Codex to be able to write to memory, the memory folder is added to `writable_roots`
  in the `[sandbox_workspace_write]` section of `config.toml`.

## Common mistakes

1. **Adding to the index by hand.** The line will move into the rule file, but it is simpler to
   write `index_line` straight away.
2. **A pointer line with no rule.** "About deploys, see the file" does not work: nobody gets to the file.
3. **One rule in two places.** Within a month the copies drift apart. Pick a level and keep one.
4. **A law from one case.** That is what the "trial" status is for.
5. **A log instead of a note.** `HANDOFF.md` holds only the current state; history goes to the log.
6. **Shared memory inside `.claude`.** Claude Code will ask for permission on every write.

## What changed since the first version

| Before (April 2026) | Now (October 2026) |
|---|---|
| the agent keeps the index by hand | a script builds the index within a budget; the overflow goes to a catalogue |
| an index line is a link with a caption | the line carries the whole rule |
| index sections by topic | sections by moment of work |
| an "eternal rule" mark (`ttl: evergreen`) | confidence as "trial or rule" plus a counter of occurrences |
| no telling where a rule came from | `origin_*` fields: date, project, who said it, quote |
| the start hook adds the note and the log | it also pulls changes from the remote repository and adds the memory index |
| a four-step "stop" | six steps: commit with push and stuck processes were added |
| the project list is updated by hand | it is rebuilt at the end of every turn |
| Claude Code only | Claude Code and Codex on one memory |
| installation as a ten-step text | an installer with a dry run, a check and uninstall |
