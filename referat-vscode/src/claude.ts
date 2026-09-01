/**
 * Finding the `claude` binary, and running `/cleanup` with it.
 *
 * **The extension never handles credentials.** It spawns the official binary as
 * a child process and that binary owns all authentication, under the user's own
 * Claude Code subscription login. There is no API key anywhere in this project.
 *
 * Finding it is the awkward part: on this machine `claude` is not on `PATH` at
 * all. It ships inside the installed Claude Code VS Code extension, whose
 * directory name carries a version that changes on every update — so the
 * fallback globs, drops whatever the marketplace has flagged in `.obsolete`,
 * and takes the newest of what is left. Nothing here is hardcoded to a version.
 */
import { spawn } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";
import * as vscode from "vscode";
import { ReferatError, firstLine, output } from "./cli";

/** The published id of the Claude Code extension, which ships the binary. */
const CLAUDE_CODE_EXTENSION = "Anthropic.claude-code";

/**
 * The tools `/cleanup` is allowed while it runs.
 *
 * These match the slash command's own frontmatter in
 * `templates/meetings/.claude/commands/cleanup.md`, which is the authority on
 * what the prompt needs — `Glob` is what its wrong-meeting-id fallback uses to
 * list the ids that do exist.
 *
 * **`Bash` must never be added to this list.** The meetings folder's
 * `.claude/settings.json` keeps the pass out of the voiceprints with `Read` and
 * `Edit` deny rules on `.voices/`, and those bind the file tools only: a shell
 * would walk straight past them and `cat` the database. Having no shell is what
 * makes a file-tool deny rule sufficient.
 */
const ALLOWED_TOOLS = "Read,Write,Glob";

/**
 * The `claude` binary, resolved **at spawn time and never persisted**.
 *
 * A stored absolute path is the thing to avoid here: the binary lives in a
 * directory whose name carries a version, so a path cached today is a path that
 * fails a week from now — silently, at the moment somebody clicks *Generate
 * notes*. That is exactly what rotted between the two step 10 sessions.
 *
 * Asking VS Code for the extension is what makes that a non-problem: it knows
 * where it installed it and follows it across updates, so nothing here parses a
 * version or reads the `.obsolete` file. It is tried before `PATH` because on
 * this machine there is nothing on `PATH` to find.
 */
export async function resolveClaude(): Promise<string> {
  const configured = vscode.workspace
    .getConfiguration("referat")
    .get<string>("claudeBinary", "")
    .trim();
  if (configured) {
    if (!fs.existsSync(configured)) {
      throw new ReferatError(`referat.claudeBinary points at ${configured}, which does not exist.`);
    }
    return configured;
  }

  const bundled = bundledClaude();
  if (bundled) {
    return bundled;
  }

  const onPath = await firstOnPath();
  if (onPath) {
    return onPath;
  }

  throw new ReferatError(
    "Cannot find the claude binary: the Claude Code extension is not installed and there is nothing named claude on PATH. Set referat.claudeBinary.",
  );
}

/**
 * The binary inside the installed Claude Code extension. Present even when that
 * extension has not been activated, which is why this needs no activation event
 * of its own.
 */
function bundledClaude(): string | undefined {
  const extension = vscode.extensions.getExtension(CLAUDE_CODE_EXTENSION);
  if (!extension) {
    return undefined;
  }
  const binary = path.join(
    extension.extensionPath,
    "resources",
    "native-binary",
    process.platform === "win32" ? "claude.exe" : "claude",
  );
  if (!fs.existsSync(binary)) {
    output().appendLine(`the Claude Code extension is installed but has no binary at ${binary}`);
    return undefined;
  }
  return binary;
}

function firstOnPath(): Promise<string | undefined> {
  return new Promise((resolve) => {
    const child = spawn("where", ["claude"], { windowsHide: true });
    let stdout = "";
    child.stdout.on("data", (chunk) => (stdout += chunk));
    child.on("error", () => resolve(undefined));
    child.on("close", (code) => {
      const found = firstLine(stdout);
      resolve(code === 0 && found && fs.existsSync(found) ? found : undefined);
    });
  });
}

/**
 * Run `/cleanup <meeting-id>` in the meetings folder.
 *
 * `cwd` is the meetings folder and never the repository: the slash command, the
 * folder's `CLAUDE.md` and the `.voices/` deny rule all live in that folder's
 * `.claude/`, and a pass run anywhere else would have none of them.
 */
export async function generateNotes(
  meetingId: string,
  meetingsDir: string,
): Promise<{ code: number; stderr: string }> {
  const binary = await resolveClaude();
  const args = [
    "-p",
    `/cleanup ${meetingId}`,
    "--allowedTools",
    ALLOWED_TOOLS,
    "--permission-mode",
    "acceptEdits",
  ];
  output().appendLine(`$ "${binary}" ${args.join(" ")}   (cwd: ${meetingsDir})`);

  return vscode.window.withProgress(
    {
      location: vscode.ProgressLocation.Notification,
      title: `Writing notes for ${meetingId}\u2026`,
      cancellable: true,
    },
    (progress, token) =>
      new Promise((resolve, reject) => {
        const child = spawn(binary, args, { cwd: meetingsDir, windowsHide: true });
        let stderr = "";
        child.stdout.on("data", (chunk) => {
          const text = String(chunk);
          output().append(text);
          // The last non-empty line into the toast, the whole stream into the
          // channel. A cleanup pass takes a minute or two and says what it is
          // doing while it works, so a progress toast that only says "writing
          // notes" is throwing away the one signal there is.
          const line = lastNonEmptyLine(text);
          if (line) {
            progress.report({ message: truncate(line) });
          }
        });
        child.stderr.on("data", (chunk) => {
          stderr += chunk;
          output().append(String(chunk));
        });
        token.onCancellationRequested(() => child.kill());
        child.on("error", (err) => reject(new ReferatError(`Could not run claude: ${err.message}`)));
        child.on("close", (code) => resolve({ code: code ?? 1, stderr }));
      }),
  );
}

function lastNonEmptyLine(text: string): string {
  const lines = text.split(/\r?\n/).filter((line) => line.trim());
  return lines.length ? lines[lines.length - 1]!.trim() : "";
}

/** A progress toast is one line wide; a paragraph in it just pushes itself off. */
function truncate(line: string): string {
  return line.length > 90 ? `${line.slice(0, 89)}\u2026` : line;
}
