/**
 * The one path from the extension to Python.
 *
 * Everything the tree and the labeling panel know comes through `referat
 * ... --json`. The extension deliberately does not read `meta.json` itself:
 * the two meeting roots, the duration format, the title rule, the audio state
 * and the set of speakers still waiting for a name all already exist exactly
 * once in Python, shared by the CLI, the dashboard and the tray so that they
 * cannot disagree. A TypeScript copy would be one more reader of `meta.json`
 * with its own opinions about all five.
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

export interface MeetingJson {
  id: string;
  dir: string;
  started_at: string;
  duration_seconds: number;
  duration: string;
  status: string;
  audio: string;
  staged: boolean;
  title: string;
  transcript: boolean;
  notes: boolean;
  unnamed: string[];
}

export interface ListJson {
  meetings_dir: string;
  staging_dir: string;
  meetings: MeetingJson[];
}

export interface SpeakerJson {
  speaker: string;
  snippets: string[];
  lines: string[];
  has_embedding: boolean;
}

export interface LabelJson {
  meeting: string;
  dir: string;
  known_names: string[];
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

/** Name one speaker. Returns the CLI's complaint on refusal, or null on success. */
export async function applyName(
  meetingId: string,
  speaker: string,
  name: string,
): Promise<string | null> {
  const result = await run(["label", meetingId, "--speaker", speaker, "--name", name]);
  return result.code === 0 ? null : firstLine(result.stderr) || "referat label failed.";
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
