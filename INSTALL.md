# Installing with an agent

This file is read by the agent: Claude Code or Codex. A person tells it "install memory-kit
following INSTALL.md", the agent does the steps below and runs the check at the end.

Why this way. The installer `install.py` is built for a clean machine. If the person already has
their own hooks, rules and memory folder, the script cannot adapt to them, but an agent can: it
looks at what is there and adds the new parts alongside. The result, though, is checked by a
script, not by the agent: `scripts/kit_check.py` gives the same answer every time it runs.

Clean machine with no settings of your own? It is faster to run `install.py` as described in `README.md`.

## Rules for the agent during the install

1. Delete nothing. Before editing someone else's file, save a copy next to it: `<name>.bak-YYYYMMDD`.
2. Copy the kit's files as they are and do not edit them. Need different behaviour? Add a separate
   hook with its own name. The check will report an edited kit file as a mismatch.
3. Do not grant yourself permissions. Need a line in `permissions` or in `config.toml`? Show it
   to the person and ask them to add it themselves.
4. Do not read or copy login files (`auth.json`, `.credentials.json` and the like).
5. Before writing to the agent's settings, show the person what will change and wait for a "yes".
6. Say "done" only after the check has answered "Всё на месте." ("All in place.").

## Step 1. Survey

Find out and briefly tell the person:

- whether `python` (3.8 or newer), `node` and `git` are installed. Something missing? Say so and stop;
- which agents are present: the folders `~/.claude` and `~/.codex`;
- whether the agent folder has a `memory-kit.json`. If it does, this is an update: the settings are kept, go to step 3;
- which hooks are already configured: `~/.claude/settings.json`, `~/.codex/hooks.json`;
- whether the agent folder's `hooks/` and `scripts/` contain files with the names from step 3;
- where the person already keeps memory and rules, if anywhere.

## Step 2. Four locations

| Key | What it is | Default |
|---|---|---|
| `root` | the folder that holds the projects | ask the person |
| `central` | shared memory for all projects | `~/.agent-memory/central` |
| `projects_dir` | per-project memory | `~/.claude/projects`; without Claude Code: `~/.agent-memory/projects` |
| `sessions_dir` | conversation archive | a `sessions` folder next to `central` |

The person already has a memory folder? Leave it where it is and enter its path. The kit does not need it moved.

One caveat about putting `central` inside `~/.claude`. It works, but Claude Code treats files there
as sensitive and asks for confirmation when writing to them from another project. Tell the person;
it is their call.

## Step 3. Files

Copy from the kit folder into the agent folder. With two agents, repeat for each folder.

| From | To | Files |
|---|---|---|
| `hooks/` | `<agent folder>/hooks/` | `kit.js`, `session-start-context.js`, `session-summary-check.js`, `memory-index-hook.py`, `memory-recall-hook.py` |
| `scripts/` | `<agent folder>/scripts/` | `memory_index.py`, `memory_search.py`, `session_archive.py`, `project_index.py`, `kit_check.py`, `memory_report.py`, `memory_upkeep.py`, `find-zombies.ps1` |

A file with the same name is already there and differs? That is the person's own version. Save a copy,
compare it with the kit's file and list what it has beyond the kit. Show each difference to the person:
either it is no longer needed, or it moves into a separate hook with its own name. Then put the kit's file in place.

## Step 4. Settings

Write `<agent folder>/memory-kit.json`. The hooks look for it next to the `hooks` folder. Without it they stay silent.

```json
{
  "tool": "claude",
  "root": "/home/me/Projects",
  "projects_dir": "/home/me/.claude/projects",
  "central": "/home/me/.agent-memory/central",
  "kit_dir": "/home/me/Projects/claude-memory-kit",
  "inject_index": true,
  "inject_project_index": false,
  "sync_on_start": true,
  "journal_entries": 3
}
```

| Key | What it does |
|---|---|
| `tool` | `claude` or `codex` |
| `kit_dir` | the kit folder. The check uses it to compare installed files with the kit on every run |
| `inject_index` | the session-start hook puts the shared memory index into context. The person already loads it another way? Set `false` |
| `inject_project_index` | the same for project memory. Claude Code loads it on its own, so `false` there, `true` for Codex |
| `sync_on_start` | at session start, pull new commits from the server in the folder the agent is opened in. Files are updated only if there are no unsaved edits and no merge is needed |
| `journal_entries` | how many recent project journal entries to show at start |
| `journal_keep` | how many entries to keep in the journal; the rest move to `projects/archive/`. Default 5, `0` means never trim |
| `start_max_chars` | a cap on the text the hook hands over at start. For Codex set `5800`: it passes no more than that to the model and cuts out the middle. You then get the project note, one journal entry, the "Всегда" ("Always") section and the address of the full index. Claude Code does not need this key |
| `sessions_dir` | a custom location for the conversation archive, if it is not next to `central` |
| `project_index` | `false`: do not build the project list `PROJECT_INDEX.md` at the end of a turn. Useful if another tool already maintains it |
| `recall_block` | `false` turns off the pause before the first push and the first server command |
| `recall` | your own topics and triggers for rule hints; format at the top of `hooks/memory-recall-hook.py` |
| `guard` | blocks: the hook stops `git add -A`, `git add .`, `git commit -a`; ending a session after the stop word without a "Промпт" ("Prompt") section; and ending a session with uncommitted edits or unpushed branches in the current project's repository, or with this session's unpushed commits in other repositories. `false` turns them all off; `{"git_add_all": false}`, `{"end_prompt": false}` or `{"end_git": false}` one at a time |
| `guards` | your own blocks; format at the top of `hooks/memory-recall-hook.py`. A block on another tool also needs a hook line: `PreToolUse` with that tool's name and the same `memory-recall-hook.py pre` command |
| `label` | `true`: once a day, at the end of a turn, a separate process has the Haiku model label the person's new messages (a correction to the agent or not) and updates the `reviews/metrics.md` report. It spends Claude Code quota, about one short call per 40 messages, so it is off by default. Ask the person |
| `stop_words` | words the person ends a session with. Default: стоп, всё, завершай, пока, stop |
| `metrics` | `false` turns off the event log `<central>/.metrics/events.jsonl`. It records which rules were shown and what was blocked; command text is not stored. Weekly summary: `scripts/memory_report.py` |

On an update, do not rewrite the file: add the missing keys and leave the rest.

## Step 5. Memory skeleton

`central` must contain the folders `feedback`, `ref`, `projects`, `plans`, `research`, `reviews`
and the file `MEMORY.head.md`. Create what is missing; take the header from `templates/MEMORY.head.md`.
Put `templates/feedback_example.md` into an empty `feedback` folder as a sample. Do not touch existing memory.

## Step 6. Hooks

Add the hooks next to the ones already there. Do not change or reorder anyone else's.

**Claude Code**, file `~/.claude/settings.json`, section `hooks`:

| Event | For which tools | Command |
|---|---|---|
| `SessionStart` | all | `node <agent folder>/hooks/session-start-context.js` |
| `Stop` | all | `node <agent folder>/hooks/session-summary-check.js` |
| `Stop` | all | `python <agent folder>/hooks/memory-index-hook.py stop`, timeout 60 s |
| `PostToolUse` | `Write\|Edit\|MultiEdit` | `python <agent folder>/hooks/memory-index-hook.py post`, timeout 30 s |
| `PreToolUse` | `Bash\|PowerShell` | `python <agent folder>/hooks/memory-recall-hook.py pre`, timeout 10 s |
| `UserPromptSubmit` | all | `python <agent folder>/hooks/memory-recall-hook.py prompt`, timeout 10 s |
| `SubagentStart` | all | `python <agent folder>/hooks/memory-recall-hook.py subagent`, timeout 10 s |
| `PreCompact` | all | `node <agent folder>/hooks/session-start-context.js precompact`, timeout 10 s |

**Codex**, file `~/.codex/hooks.json`: every row except `PostToolUse` and `PreCompact`; for `PreToolUse` the tools column
is just `Bash` (that is what Codex calls shell commands). Codex skips new hooks until the person
approves them: in the Codex console, type `/hooks` and approve. Tell the person.

Use full paths. On Windows, forward slashes are fine. Replace `python` with whatever command
starts Python on this machine.

The person already has a hook on the same event for the same purpose (say, their own note at start)?
Two hooks will put the text into context twice. Show both and ask which one to keep.

## Step 7. Rules section

The agent has to know where memory lives and how to write to it. Sample text: `templates/instructions.md`
for the main rules file (`~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`) and `templates/root-rules.md`
for the rules file at the projects root. The section goes between the lines `<!-- memory-kit:start -->`
and `<!-- memory-kit:end -->`, so it is easy to find and update later.

The templates are written in Russian. If the person works in another language, translate the section
for them, keeping the file names, paths and the quoted values the hooks look for ("Промпт", "Всегда").

Placeholders in the sample:

| Placeholder | What to put in |
|---|---|
| `{{CENTRAL}}`, `{{PROJECTS_DIR}}`, `{{ROOT}}` | locations from step 2 |
| `{{ROOT_RULES}}` | path to the rules file at the projects root |
| `{{SEARCH}}` | the search command: `python <agent folder>/scripts/memory_search.py` |
| `{{AUTOLOAD}}` | two or three sentences on what enters the context by itself: the session-start hook adds the shared memory index, Claude Code loads project memory on its own |
| `{{HOOKCHECK}}` | a rule: at session start, find the line `memory-kit: хуки работают` ("memory-kit: hooks are working") in the context; if it is missing, tell the person the hooks did not fire |
| `{{ZOMBIES}}` | how to look for abandoned background processes at the end of a session: `scripts/find-zombies.ps1` on Windows, `ps` elsewhere |

The person already has their own memory rules? Do not lay a second section on top. Compare the sample
with their text, add only what is missing and show them the comparison.

Codex caps the size of rules: by default 32,768 bytes across all rules files together,
and it silently skips the rest. Cyrillic takes two bytes per character. Does not fit? Shorten the rules
or raise `project_doc_max_bytes` in `~/.codex/config.toml`. The check counts this.

## Step 8. Permissions

Shared memory lives outside the project folder, so the agent will ask for permission on every write.
Show the person what to add and ask them to do it themselves:

- Claude Code, `~/.claude/settings.json`: the memory folder in `permissions.additionalDirectories`;
- Codex, `~/.codex/config.toml`: the memory folder in `writable_roots` of the `[sandbox_workspace_write]` section.

## Step 9. Check

```bash
python <agent folder>/scripts/kit_check.py --kit <kit folder>
```

The check looks at the settings, whether the files exist and match the kit, the configured hooks, the
session-start hook's reply, the index builder, search and the Codex rules cap. The last line must be
"Всё на месте." ("All in place."). Any items marked "НЕТ" ("NO")? Fix them and run again. Retell the
result to the person in your own words, and do not call the install done until the check passes.

The check prints its report in Russian; translate it for the person if needed.

Then build the index and tell the person what comes next:

```bash
python <agent folder>/scripts/memory_index.py <central>
```

- open a new session in the project folder and ask "what's the state of the project?";
- in Codex, approve the hooks with `/hooks`;
- at the end of the work, say "stop".

## Updating

1. Update the kit folder: `git pull`.
2. Run the check. Lines "совпадает с набором" ("matches the kit") marked "НЕТ" ("NO") show which files
   are behind or were edited in place.
3. Sort out files edited in place first, as in step 3. Then copy the kit's files again.
4. Compare the hook table from step 6 with the settings: a new version may have added hooks.
5. Run the check again.

## Removing

Remove the hooks from step 6 from the agent's settings, delete `memory-kit.json` and the rules section between the markers.
The files in `hooks/` and `scripts/` can stay: without settings they do nothing. Do not touch the memory
folder; it is the person's data.

## Not tested

The kit was built and tested on Windows; the tests also pass on Linux. None of the authors has run it
on macOS. Hit a problem? Open an Issue and describe what broke.
