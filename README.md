# memory-kit: memory across sessions for Claude Code and Codex

An agent does not remember past sessions. Every time you have to explain again where you left off
and how you like to work. This kit gives the agent a memory that runs itself: it loads at the start
of a session, brings up the right rules at the right moment, gets saved at the end, and does not grow
into a file nobody can read.

The kit grew out of a working setup that has run about thirty projects for more than half a year.
Illustrated overview (in Russian): [claude-memory.forge-hub.ru](https://claude-memory.forge-hub.ru).

## What you get

**At the start of a session**

- The agent already knows where you stopped. A hook hands it the project note (`HANDOFF.md`),
  the latest journal entries and the index of rules.
- Fresh changes from the remote repository are pulled automatically when it is safe to do so.

**While you work**

- Rules arrive when they are needed. Before a commit, a push or a command on a server, the agent gets
  up to five rules on that topic. The first push and the first server command of a session are stopped
  once, so the agent checks the rules before acting rather than after.
- Hints from the words of your request. Ask to fix a slide, rework a screen or go through a chat
  history, and the rules on that topic come along with the request. You can add your own topics in the settings.
- A program hangs or will not open. On words like "frozen", "won't open", "crashes" the agent gets
  the notes about that particular program: known pitfalls and the workaround, instead of debugging from scratch.
- Compacting a long conversation does not lose decisions. Before compaction the kit saves a copy of the
  project note; after compaction the agent learns where it is and adds what was decided in the meantime.
- Dangerous commands are stopped. `git add -A`, `git add .` and `git commit -a` never go through:
  the agent gets a refusal with an explanation and adds files by name.
- Helpers (subagents) do not start with an empty head: they get the memory search command, the main
  rules and the beginning of the project note.
- One command searches all of memory: rules, references, journals and conversations from past sessions.

**At the end of a session**

- One word, «стоп» ("stop"), and the agent updates the note, commits and pushes, checks for stuck
  processes, writes a journal entry, reviews the session and saves new rules.
- If after "stop" the agent has not produced the text for the next session, the turn will not end
  until it writes it.
- If the current project's repository still has uncommitted changes or unpushed branches, the turn
  stops once: the agent commits its own changes by name and pushes, and reports anything foreign or
  disputable. In other repositories the session touched along the way, only its own unpushed commits
  hold the turn; uncommitted changes there are left alone.
  New untracked files are not counted: deciding about them is up to a human.

**All the time**

- Rules accumulate on their own. Correct the agent or confirm a good move, and it becomes a rule file
  with your own words and a confidence mark: first `проба` ("trial"), after it repeats `правило` ("rule").
- The index does not overflow. A script builds it within a hard budget. What does not fit goes into
  a separate catalog; nothing is lost.
- Journals do not bloat. A project journal keeps the five most recent entries; older ones move to an
  archive next to it. Search finds those too.
- Broken links show up at once. If an index line points to a deleted or renamed file, the agent stops
  once at the end of the turn and gets the list.
- Conversations do not disappear. Claude Code deletes session transcripts after 30 days. The kit keeps
  the human and agent text from them, without tool output and with keys cut out.
- One memory for two agents. Claude Code and Codex read and write the same files and get the same hints.
- Log and report. You can see how many times the agent was shown rules, how many commands were stopped,
  and how the share of your corrections changes week by week.

## Requirements

- Claude Code or Codex CLI (or both)
- Python 3.8 or newer
- Node.js 18 or newer
- git
- Windows, macOS or Linux. The script that looks for stuck processes works only on Windows.

## Installation

### Through the agent

Open Claude Code or Codex in the root folder of your projects and paste the text from
[INSTALL-PROMPT.md](INSTALL-PROMPT.md). The agent will download the kit, show what will change,
and install it after you say "yes".

Already have your own hooks, rules or memory folder? The agent will install the kit alongside them
following [INSTALL.md](INSTALL.md): it looks at what is already there and adds only what is missing.
One script checks the result:

```bash
python ~/.claude/scripts/kit_check.py
```

It compares the installed files with the kit and shows what has fallen behind or was edited in place.
For Codex add `--agent-dir ~/.codex`.

### By hand

```bash
git clone https://github.com/GuruDevvv/claude-memory-kit
cd claude-memory-kit

# 1. see what will change (writes nothing)
python install.py --root ~/Projects --dry-run

# 2. install and create the note in the first project
python install.py --root ~/Projects --project ~/Projects/MyApp

# 3. check
python install.py --root ~/Projects --check
```

`--root` is the folder that holds your projects. The installer finds out by itself which agents
you have. You can name them explicitly with `--tool claude`, `--tool codex` or `--tool both`.

After installing, open a **new** session in the project folder and ask "what's the state of the project?".
The agent should answer from the note without asking questions.

### Codex

Start the Codex CLI and approve the new hooks: it asks about them on its own at startup,
and the `/hooks` command opens the same window. Until the hooks are trusted, Codex skips them.
If you forget, the agent will remind you in the first reply of every session.

## How to use it

| Moment | What you do | What happens |
|---|---|---|
| Start of a session | nothing | the agent already knows the project state and your rules |
| The agent got it wrong or right | say "not like that" or "yes, exactly" | a rule file appears with your words |
| The agent is about to commit or go to a server | nothing | it gets the rules on that topic before acting |
| An important decision mid-work | "update HANDOFF" | the note is updated right away |
| You need to recall what was decided a month ago | "search memory for…" | the agent searches rules, journals and conversations |
| End of a session | «стоп» ("stop") | six wrap-up steps, ending with ready text for the next session |
| A new project | `python install.py --root … --project <folder>` | `HANDOFF.md` appears in the project |

## Searching memory

About a third of the rules fit into the index. Search finds the rest:

```bash
python ~/.claude/scripts/memory_search.py "how to deploy to the server"
python ~/.claude/scripts/memory_search.py "what we decided about the discount" --sessions
```

The first command searches rules, references and journals; the second searches conversations from
past sessions. The agent knows these commands from the rules section and runs them itself.

## Log and report

Hints and stops are logged to `.metrics/events.jsonl` inside the shared memory.
The log holds no command text, only the topic and the names of the rules shown. Report for eight weeks:

```bash
python ~/.claude/scripts/memory_report.py --weeks 8
```

The `--write` flag saves the report to `reviews/metrics.md`. The `--label` flag has the Haiku model
tag your messages ("a correction to the agent" or not) and counts the share of corrections per week.
Tagging spends your Claude Code quota, so it is off by default.

## Where things live

```
~/.agent-memory/central/            shared memory, one for all projects
├── MEMORY.md                       index, built by a script
├── MEMORY.head.md                  index header, written by you
├── INDEX.md                        what did not fit into the index
├── PROJECT_INDEX.md                list of projects, built by a script
├── feedback/                       working rules, one file per rule
├── ref/                            references
├── projects/<Project>.md           session journals, newest entry on top, five most recent
├── projects/archive/               older journal entries, search sees them
├── plans/ research/ reviews/       plans, research, reviews
└── .metrics/                       log of hints and stops

~/.agent-memory/sessions/           conversation archive; do not put it in git

~/.claude/projects/<name>/memory/   memory of a single project (Claude Code's built-in memory)

<projects root>/CLAUDE.md, AGENTS.md     HANDOFF note template
<project>/HANDOFF.md                     "where we are now", up to 25 lines

~/.claude/ and ~/.codex/
├── hooks/                          5 hook files
├── scripts/                        builders, search, archive, report, memory upkeep, install check
├── handoff-snapshots/              copies of notes before a conversation is compacted (Claude Code)
├── memory-kit.json                 kit settings
└── CLAUDE.md or AGENTS.md          rules section between the memory-kit markers
```

The shared memory deliberately lives outside the `.claude` folder. Claude Code treats files inside
`.claude` as sensitive and asks for confirmation on every write there from a project folder.

The detailed design and the reasons behind it are in [docs/GUIDE.md](docs/GUIDE.md).

## What the installer changes on your machine

- creates `~/.agent-memory/`;
- copies 5 hooks and 8 scripts into the agent's folder;
- adds its hooks to `settings.json` (Claude Code) or `hooks.json` (Codex), leaving other hooks alone;
- Claude Code: adds the memory folder to the allowed read and write paths;
- Codex: adds the memory folder to `writable_roots` in `config.toml` if that section is not there yet;
- writes the rules section into `CLAUDE.md` and `AGENTS.md` strictly between the markers
  `<!-- memory-kit:start -->` and `<!-- memory-kit:end -->`.

Before editing any existing file, a copy is saved next to it with a `.bak-<date>` suffix.
Running it again duplicates nothing and does not overwrite your settings.

## Updating and removing

Has the kit been updated? The session start hook compares the installed files with the kit folder
and tells the agent about any difference. The agent will offer to update; the procedure is in [INSTALL.md](INSTALL.md).

```bash
python install.py --root ~/Projects --uninstall
```

Uninstalling removes the hooks and the rules section. Memory and project notes stay where they are.

## Settings

The `memory-kit.json` file in the agent's folder. Main keys:

| Key | Default | What it does |
|---|---|---|
| `sync_on_start` | `true` | pull changes from the remote repository at the start of a session |
| `journal_entries` | `3` | how many journal entries to hand to the agent |
| `journal_keep` | `5` | how many entries to keep in a journal; older ones go to `projects/archive/`, search sees them; `0` means no trimming |
| `inject_index` | `true` | hand the shared memory index to the agent |
| `inject_project_index` | Codex: `true`, Claude Code: `false` | hand the project memory index to the agent |
| `start_max_chars` | Codex: `5800` | cap on the start-up text; Codex passes no more than this to the model |
| `recall_block` | `true` | stop the first push and the first server command |
| `recall` | kit topics | your own hint topics: a command pattern or words in the request |
| `guard` | `true` | stops; `false` turns them all off, `{"git_add_all": false}` turns off one |
| `guards` | none | your own stops |
| `stop_words` | стоп, всё, завершай, пока, stop | words you end a session with |
| `metrics` | `true` | keep the log of hints and stops |
| `label` | `false` | once a day tag messages with a model and refresh the report |

Full list with explanations: [INSTALL.md](INSTALL.md), step 4.

## What has been tested and what has not

Tested on Windows 11 and in a Linux container: the kit's tests (hooks, stops, index building,
search, conversation archive), installation, reinstallation, removal.

Claude Code 2.1 went through the full cycle: it answered from the note in a new session, ran all
the steps on "stop", and the next session knew the rule saved in the previous one.

Codex 0.160 was tested with live runs on Windows: rules are read to the end, the hint before a commit
and the hint from request words arrive, `git add -A` is stopped, conversations reach the archive.
Two Codex specifics:

- it passes only about six thousand characters from a hook to the model, so at start-up it gets the
  note, the main rules and the address of the index, and reads the index itself;
- in its sandbox the `.git` folder is read-only, so it asks permission to commit.

The full "stop" cycle has not been run on Codex. The kit has not been run on macOS.

What to expect from memory. We ran 16 situations where the agent used to make mistakes, with memory
and without. In 12 of them a fresh agent managed without memory. Memory helped consistently in one.
A rule sitting in a file does nothing by itself: it has to be delivered at the right moment. That is
why the kit has hints and stops, and the main measure remains the weekly share of your corrections.

If something breaks, open an Issue.

## License

MIT
