# Referat for VS Code

The primary UI for [Referat](https://github.com/nickelm/referat): a sidebar of
meetings, everything you do to one, and the projects they are tagged with.

<!-- That link is absolute on purpose. This file is the .vsix's own front page,
     where a relative `../README.md` points outside the package at nothing;
     `vsce` rewrites relative links against the repository URL and turns that
     one into a URL with a literal `..` still in it. -->


This and the tray icon are the only graphical surfaces Referat has, and the only
ones it will ever have. **There is no web UI in this project** — that rule is
about Flask, FastAPI, a localhost server and a browser front end. The sidebar is
a VS Code webview, which is part of this extension; there is no server anywhere.

## What it does

- **Meetings sidebar** — one row per meeting, newest first: title, duration,
  when it started, the project tags it carries as chips, and a strip showing
  `meta.json`'s `status` field. Nothing here works a meeting's state out from
  which files exist; the pipeline writes that field and this prints it.
- **Untagged only** — a toggle that leaves the meetings with no tags. That view
  *is* the queue for tagging, the way the speakers section is the queue for
  naming.
- **Transcript** / **Notes** — opens them rendered.
- **Generate notes** — runs `/cleanup <meeting-id>` through the `claude` binary
  in the meetings folder, streams what it says into the progress toast, then
  records `referat state <id> notes-written` and opens the `notes.md` it wrote.
- **Re-transcribe** — `referat rerun <meeting-id>` in a terminal, because it
  takes minutes and the log is worth watching.
- **Tags…** — a multi-select over the projects, pre-checked with what the
  meeting carries, with *Create project "…"* as you type. An id no project
  answers to is shown as an orphan chip and can be unchecked like any other.
- **Speakers (n)** — expands in place: a player per snippet, the known names as
  clickable chips, and a field. It drives `referat label --speaker --name`.
- **Accept & delete audio** — the off-ramp for a meeting stuck in staging
  because the quality gate refused its transcript. It deletes `mic.wav` and
  `system.wav` for good and then promotes the meeting, behind a modal that says
  exactly that. It cannot promote *with* the audio: no WAV may ever reach the
  meetings folder, because deleting a file inside a synced folder does not
  delete it.
- **Projects** (view title) — create, rename and delete projects. Deleting one
  leaves its ids on the meetings carrying them, visibly, as orphans.
- **A status bar item** — what the tray is doing, from `referat status --json`.
  It does not tick: the elapsed time is `format_duration`'s output, refreshed
  when the tray rewrites its status file and every 30 seconds otherwise, because
  a tray that was killed rewrites nothing. It appears once the sidebar has been
  opened in a window — no window that never opens Referat starts an interpreter
  to discover that nothing is recording.

Attaching and detaching a project's Google Docs is **not here yet**: `referat
project link-doc`, `unlink-doc` and `sync` arrive with the digests at build
step 13, and this extension has nothing to shell out to until they exist.

## What it deliberately does not do

**It reimplements nothing.** Every meeting it shows comes from
`referat list --json`, and every change it makes is a `referat` verb: `label
--speaker --name`, `tag`, `untag`, `project add|rename|rm`, `state` and
`promote --release-audio`. All of them run through the venv's own interpreter
(`.venv\Scripts\python.exe -m referat.cli`, working directory at the
repository). Not `uv run` — Smart App Control blocks `uv.exe` on this machine —
and not `referat.exe`, which Dropbox has deleted twice. The two meeting roots, the
duration format, the title rule, the audio state, the lifecycle vocabulary, the
project ids, the reserved-name rule, the voiceprint database and the transcript
relabeling all stay in Python, where they already exist exactly once. A copy here
would be one more thing that can disagree with the recorder — and every refusal
you see in this sidebar is the sentence the CLI would have printed.

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

## Installing it

```powershell
cd referat-vscode
npm install
npm run package                                    # referat-vscode-0.2.0.vsix
code --install-extension referat-vscode-0.2.0.vsix
```

**Then set `referat.repoRoot`.** With the setting empty the extension looks
through the open workspace folders for the repository and, failing that, at the
checkout its own bundle sits inside — which, installed from a `.vsix`, is
`~\.vscode\extensions\...` and holds no `pyproject.toml`. That fallback is what
makes F5 work in a host window opened on nothing; it cannot work here. So any
window that does not have the repository open — the window opened on the
*meetings* folder, which is the normal one — needs the setting.

`code --uninstall-extension niklas-elmqvist.referat-vscode` removes it again.

## Developing it

Press **F5** in the repository, which starts an Extension Development Host with
esbuild watching and source maps on. `npm run typecheck` and `npm run compile`
do those separately.

The packaged build has no source map, deliberately: a map carries
`sourcesContent`, so shipping one would put the whole TypeScript source inside a
`.vsix` whose entire payload is otherwise `dist/extension.js`, `media/`,
`package.json`, `README.md` and `LICENSE`. `.vscodeignore` is written as an
allowlist so that stays true when a file is added.
