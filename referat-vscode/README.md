# Referat for VS Code

The browsing layer for [Referat](../README.md): a tree of meetings, the four
things you do to one, and a panel for putting names on the speakers
identification could not place.

This and the tray icon are the only graphical surfaces Referat has, and the only
ones it will ever have. There is no web UI in this project.

## What it does

- **Meetings tree** — one node per meeting, newest first, with its title,
  duration and status. A meeting still in staging says so; a meeting with
  speakers waiting for a name has an **Unknown speakers** child.
- **Open transcript** / **Open notes** — opens them rendered.
- **Generate notes** — runs `/cleanup <meeting-id>` through the `claude` binary
  in the meetings folder, then opens the `notes.md` it wrote.
- **Re-transcribe** — `referat rerun <meeting-id>` in a terminal, because it
  takes minutes and the log is worth watching.
- **Name speakers** — plays a speaker's snippets and takes a name.

## What it deliberately does not do

**It reimplements nothing.** Every meeting it shows comes from
`referat list --json`, and every name it applies goes through
`referat label <id> --speaker <s> --name <n>`, both run through the venv's own
interpreter (`.venv\Scripts\python.exe -m referat.cli`, working directory at the
repository). Not `uv run` — Smart App Control blocks `uv.exe` on this machine —
and not `referat.exe`, which Dropbox has deleted twice. The two meeting roots, the
duration format, the title rule, the audio state, the reserved-name rule, the
voiceprint database and the transcript relabeling all stay in Python, where they
already exist exactly once. A copy here would be one more thing that can
disagree with the recorder.

**It never handles credentials.** *Generate notes* spawns the official `claude`
binary as a child process and that binary owns all authentication, under your
own Claude Code subscription login. There is no API key in this extension, in
`config.toml`, or anywhere else in Referat.

**It never gives the cleanup pass a shell.** `/cleanup` is spawned with
`--allowedTools "Read,Write,Glob"`, matching the slash command's own frontmatter.
The meetings folder's `.claude/settings.json` keeps that pass out of the
voiceprints with `Read` and `Edit` deny rules, and those bind the file tools
only — a shell would walk straight past them. **`Bash` must never be added to
that list.**

## Settings

| Setting | What it is |
| --- | --- |
| `referat.repoRoot` | The Referat repository, the folder holding `pyproject.toml`. Empty means "look through the open workspace folders for one". |
| `referat.claudeBinary` | The `claude` binary. Empty means "ask VS Code for the installed Claude Code extension and use the binary inside it", then `PATH` — which is the order that works on a machine where `where claude` finds nothing. Resolved on every run and never cached, because that path changes with every update of that extension. |

There is deliberately **no meetings-folder setting**. The meetings folder is
`[paths].meetings_dir` in the repository's `config.toml`, which is what the tray
records into; a second place to say where meetings live is a second thing that
can be wrong.

## Running it

Not packaged yet — that is build step 12. Until then:

```powershell
cd referat-vscode
npm install
```

then press **F5** in the repository, which starts an Extension Development Host
with esbuild watching. `npm run typecheck` and `npm run compile` do those
separately.
