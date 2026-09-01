/**
 * The one path from the extension to Python.
 *
 * Everything the sidebar knows comes through `referat ... --json`, and every
 * change it makes goes back out through `referat <verb>`. The extension
 * deliberately does not read `meta.json` itself: the two meeting roots, the
 * duration format, the title rule, the audio state, the lifecycle vocabulary,
 * the project ids and the set of speakers still waiting for a name all already
 * exist exactly once in Python, shared by the CLI, the dashboard and the tray so
 * that they cannot disagree. A TypeScript copy would be one more reader of
 * `meta.json` with its own opinions about all seven.
 *
 * Invoked through the venv's own interpreter, as `python.exe -m referat.cli`.
 * Not through `uv run`: **Smart App Control blocks `uv.exe` on this machine** —
 * it is unsigned, and the cloud reputation SAC was admitting it on was withdrawn
 * on 2026-08-31 — and SAC has no exclusion list to add it to. And not through
 * `.venv\Scripts\referat.exe` either, which Dropbox has deleted twice.
 * The interpreter is the one part of the chain that both survives.
 *
 * `-m referat.cli` resolves the package because `cwd` is the repository root,
 * which puts it on `sys.path` — so `cwd` here is load-bearing rather than a
 * nicety, exactly as the autostart shortcut's working directory is.
 */
import { spawn } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";
import * as vscode from "vscode";

/**
 * The meeting's lifecycle, exactly as `meta.json` records it. Widened at build
 * step 14 rather than joined by a second field, so there is one authority on what
 * a meeting is; nothing here may infer a state from which files happen to exist.
 */
export type MeetingStatus =
  | "recording"
  | "recorded"
  | "transcribing"
  | "gate_failed"
  | "transcribed"
  | "notes_written"
  | "synced"
  | "failed";

export interface MeetingJson {
  id: string;
  dir: string;
  started_at: string;
  duration_seconds: number;
  duration: string;
  status: MeetingStatus;
  audio: string;
  staged: boolean;
  title: string;
  transcript: boolean;
  notes: boolean;
  unnamed: string[];
  /** Project ids, zero or more. Empty is untagged; resolve each against `ListJson.projects`. */
  tags: string[];
}

export interface ListJson {
  meetings_dir: string;
  staging_dir: string;
  /**
   * Project id to display name, for the tag chips. It comes down with the
   * meetings so one subprocess feeds the whole sidebar; an id in a meeting's
   * `tags` that is missing here is an *orphan*, left behind by a deleted
   * project, and is rendered as one rather than hidden.
   */
  projects: Record<string, string>;
  meetings: MeetingJson[];
}

/**
 * `referat status --json` — what the tray is doing, for the status bar.
 *
 * `elapsed` is `format_duration`'s output rather than a number of seconds, on
 * purpose: the extension must not grow a second duration formatter beside the
 * one `referat list`, the meetings dashboard and this all share. It follows that
 * the status bar does not tick — it says what Python said when it was last
 * asked, which is what the poll interval is for.
 *
 * `running` false with `stale` true is a tray that died: the file it wrote is
 * still there and its pid is not. That is worth telling apart from no tray
 * having run at all, which is `stale` false.
 */
export interface StatusJson {
  running: boolean;
  stale: boolean;
  state?: string;
  meeting_id?: string | null;
  elapsed?: string | null;
  jobs?: number;
  pid?: number;
  updated_at?: string;
}

export interface ProjectJson {
  id: string;
  name: string;
  docs: unknown[];
  glossary: string[];
  description: string;
  created_at: string;
  meetings: number;
}

export interface ProjectListJson {
  projects_file: string;
  names: Record<string, string>;
  projects: ProjectJson[];
  orphans: Record<string, number>;
}

export interface SpeakerJson {
  speaker: string;
  /** `mic`, `system`, or `""` - which channel this voice arrived on. */
  channel: string;
  snippets: string[];
  lines: string[];
  has_embedding: boolean;
}

export interface LabelJson {
  meeting: string;
  dir: string;
  known_names: string[];
  /**
   * `[speakers].owner_name`, or `""` when it is unset. A string rather than a
   * pre-sorted `known_names`, because what the owner is called is Python's to
   * know and the order chips appear in is the page's to decide.
   */
  owner: string;
  speakers: SpeakerJson[];
}

export interface Result {
  code: number;
  stdout: string;
  stderr: string;
}

/** Raised for the failures worth an explanation rather than a stack trace. */
export class ReferatError extends Error {
  constructor(message: string, readonly detail?: string) {
    super(message);
  }
}

let channel: vscode.OutputChannel | undefined;

export function output(): vscode.OutputChannel {
  channel ??= vscode.window.createOutputChannel("Referat");
  return channel;
}

/**
 * The repository root: the setting, the open workspace folder that looks like
 * it, or — running from source — the checkout this file was bundled inside.
 * "Looks like it" is `pyproject.toml` beside `referat/cli.py`; a folder with
 * only one of the two is somebody else's project.
 */
export function repoRoot(): string {
  const configured = vscode.workspace.getConfiguration("referat").get<string>("repoRoot", "").trim();
  if (configured) {
    if (!looksLikeRepo(configured)) {
      throw new ReferatError(
        `referat.repoRoot is set to ${configured}, which does not hold pyproject.toml and referat/cli.py.`,
      );
    }
    return configured;
  }
  for (const folder of vscode.workspace.workspaceFolders ?? []) {
    if (folder.uri.scheme === "file" && looksLikeRepo(folder.uri.fsPath)) {
      return folder.uri.fsPath;
    }
  }
  const source = sourceCheckout();
  if (source) {
    return source;
  }
  throw new ReferatError(
    "Cannot find the Referat repository. Open it as a workspace folder, or set referat.repoRoot.",
  );
}

/**
 * The checkout this bundle sits inside, when there is one.
 *
 * Run from source, `dist/extension.js` is at `<repo>/referat-vscode/dist/`, so
 * the repository is two levels up. That is worth checking because F5 does not
 * imply an open folder: `--extensionDevelopmentPath` says which *extension* to
 * load and nothing about which workspace to open, so the development host comes
 * up on whatever it had last — frequently nothing — and every command then
 * failed with "Cannot find the Referat repository" in a window where the
 * repository was, in fact, three directories away.
 *
 * Installed from a `.vsix` the same walk lands in `~/.vscode/extensions`, which
 * holds no `pyproject.toml`, so this returns undefined and the real error
 * stands. `path.resolve` rather than string joining, because `__dirname` is
 * absolute and Windows-shaped already.
 */
function sourceCheckout(): string | undefined {
  const candidate = path.resolve(__dirname, "..", "..");
  return looksLikeRepo(candidate) ? candidate : undefined;
}

function looksLikeRepo(dir: string): boolean {
  return (
    fs.existsSync(path.join(dir, "pyproject.toml")) &&
    fs.existsSync(path.join(dir, "referat", "cli.py"))
  );
}

/** The venv interpreter, or a `ReferatError` naming the repair. */
export function interpreter(): string {
  const root = repoRoot();
  const python = path.join(root, ".venv", "Scripts", "python.exe");
  if (!fs.existsSync(python)) {
    throw new ReferatError(
      `No interpreter at ${python}. Create the virtual environment first (SETUP.md section 2).`,
    );
  }
  return python;
}

/** Run `referat <args>` and hand back everything it said. Never throws on a non-zero exit. */
export function run(args: string[]): Promise<Result> {
  const root = repoRoot();
  const python = interpreter();
  const argv = ["-m", "referat.cli", ...args];
  output().appendLine(`$ "${python}" ${argv.join(" ")}   (cwd: ${root})`);

  return new Promise((resolve, reject) => {
    const child = spawn(python, argv, { cwd: root, windowsHide: true });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => (stdout += chunk));
    child.stderr.on("data", (chunk) => (stderr += chunk));
    child.on("error", (err: NodeJS.ErrnoException) => {
      // EACCES here is most likely Smart App Control refusing the interpreter
      // the way it already refuses uv.exe. There is no exclusion list to add it
      // to, so say what happened rather than offering a fix that does not exist.
      reject(
        new ReferatError(
          err.code === "EACCES"
            ? `Windows refused to run ${python}. If this is Smart App Control, see SETUP.md section 2.`
            : `Could not run ${python}: ${err.message}`,
          String(err),
        ),
      );
    });
    child.on("close", (code) => {
      if (stderr.trim()) {
        output().appendLine(stderr.trimEnd());
      }
      resolve({ code: code ?? 1, stdout, stderr });
    });
  });
}

/** Run `referat <args> --json` and parse it. Throws a `ReferatError` worth showing a person. */
export async function runJson<T>(args: string[]): Promise<T> {
  const result = await run(args);
  if (result.code !== 0) {
    throw new ReferatError(firstLine(result.stderr) || `referat ${args[0]} failed.`, result.stderr);
  }
  try {
    return JSON.parse(result.stdout) as T;
  } catch {
    // Almost always a `referat` that predates these flags, or a config error
    // printed where the document should have been. The raw output is in the
    // channel, which is the only place that distinction is visible.
    output().appendLine(result.stdout);
    throw new ReferatError(
      `\`referat ${args.join(" ")}\` did not return JSON.`,
      result.stdout,
    );
  }
}

export function listMeetings(): Promise<ListJson> {
  return runJson<ListJson>(["list", "--json"]);
}

export function labelPayload(meetingId: string): Promise<LabelJson> {
  return runJson<LabelJson>(["label", meetingId, "--json"]);
}

export function listProjects(): Promise<ProjectListJson> {
  return runJson<ProjectListJson>(["project", "list", "--json"]);
}

/**
 * `referat project add <name> --json`, returning the project the CLI created.
 *
 * The `--json` matters. An id is `projects.slugify` of the name plus a `-2`
 * suffix on a collision, so a caller cannot work it out; this used to create the
 * project and then look it back out of `project list` **by display name**, which
 * is wrong the moment two projects share one — the caller would silently get the
 * older project's id, or, if the two normalizations of the name ever disagreed,
 * nothing at all. Both showed up as "the project was created but the meeting
 * could not be tagged with it".
 */
export function addProject(name: string): Promise<ProjectJson> {
  return runJson<ProjectJson>(["project", "add", name, "--json"]);
}

/**
 * `referat status --json`.
 *
 * Exit 1 is not a failure here — it is the answer "no tray is running", and the
 * document explaining that comes out on stdout regardless. So this parses first
 * and only treats unparseable output as an error.
 */
export async function readStatus(): Promise<StatusJson> {
  const result = await run(["status", "--json"]);
  try {
    return JSON.parse(result.stdout) as StatusJson;
  } catch {
    throw new ReferatError("`referat status --json` did not return JSON.", result.stdout);
  }
}

/** Name one speaker. Returns the CLI's complaint on refusal, or null on success. */
export function applyName(meetingId: string, speaker: string, name: string): Promise<string | null> {
  return mutate(["label", meetingId, "--speaker", speaker, "--name", name]);
}

/**
 * Run a mutating command, handing back the CLI's own complaint on refusal.
 *
 * Every mutation in this extension goes through here, and none of them decides
 * anything: whether a name is allowed, whether a project id exists, whether a
 * lifecycle transition is legal and whether a meeting may be promoted are all
 * questions Python answers — see `voices.name_complaint`, `projects`, `run_state`
 * and `run_promote`. What comes back is the sentence the CLI would have printed
 * at a terminal, so a refusal reaches the user in the words that own the rule.
 */
export async function mutate(args: string[]): Promise<string | null> {
  const result = await run(args);
  if (result.code === 0) {
    return null;
  }
  return firstLine(result.stderr) || firstLine(result.stdout) || `referat ${args[0]} failed.`;
}

export function tag(meetingId: string, projectIds: string[]): Promise<string | null> {
  return mutate(["tag", meetingId, ...projectIds]);
}

export function untag(meetingId: string, projectIds: string[]): Promise<string | null> {
  return mutate(["untag", meetingId, ...projectIds]);
}

/**
 * The transition no pipeline can make: `/cleanup` is forbidden from touching
 * `meta.json`, so whoever spawned it records that the notes exist.
 */
export function setNotesWritten(meetingId: string): Promise<string | null> {
  return mutate(["state", meetingId, "notes-written"]);
}

/**
 * The gate-failed off-ramp: delete both WAVs, then move the meeting out of
 * staging. **Irreversible**, and the caller must have said so in a modal first.
 */
export function promoteReleasingAudio(meetingId: string): Promise<string | null> {
  return mutate(["promote", meetingId, "--release-audio"]);
}

/**
 * Delete a meeting folder and everything in it. **Irreversible**, and the caller
 * must have said so in a modal first.
 *
 * `--yes` is not a shortcut past the confirmation: `referat delete`'s prompt
 * reads `input()` and answers *no* on EOF, so a spawned CLI could never confirm.
 * The modal is the confirmation, and it says what the CLI says.
 */
export function deleteMeeting(meetingId: string): Promise<string | null> {
  return mutate(["delete", meetingId, "--yes"]);
}

export function firstLine(text: string): string {
  return text.trim().split(/\r?\n/, 1)[0] ?? "";
}

/** Show `err` as a notification, with the whole invocation a click away. */
export function report(err: unknown): void {
  const message = err instanceof Error ? err.message : String(err);
  if (err instanceof ReferatError && err.detail) {
    output().appendLine(err.detail);
  }
  void vscode.window.showErrorMessage(message, "Show output").then((choice) => {
    if (choice === "Show output") {
      output().show(true);
    }
  });
}
