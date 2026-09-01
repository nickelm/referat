/**
 * The Referat extension: activation, the commands, and the wiring between them.
 *
 * This and the tray icon are the only graphical surfaces Referat has, and the
 * only ones it will ever have — there is no web UI in this project, and the
 * sidebar being a webview does not make one: that rule is about Flask, FastAPI,
 * localhost and a browser front end. Everything a person needs to look at or
 * click is a row in the sidebar, an editor it opened, or the status bar item.
 *
 * Every command here either opens a file or shells out to `referat` / `claude`.
 * None of them reimplements anything Python already does.
 */
import * as vscode from "vscode";
import { generateNotes } from "./claude";
import {
  MeetingJson,
  StatusJson,
  deleteMeeting as deleteMeetingFolder,
  firstLine,
  interpreter,
  output,
  promoteReleasingAudio,
  readStatus,
  repoRoot,
  report,
  setNotesWritten,
} from "./cli";
import { editTags, manageProjects } from "./projects";
import { Actions, Sidebar, meetingFile } from "./sidebar";

/**
 * How often to re-ask what the tray is doing, on top of the watcher.
 *
 * The watcher catches every transition, because the tray writes `status.json` on
 * each one. It cannot catch a tray that was killed: a dead process writes no
 * file, so without this poll the status bar would sit there claiming a recording
 * is still running. Thirty seconds is the staleness this is willing to show.
 */
const STATUS_POLL_MS = 30_000;

export function activate(context: vscode.ExtensionContext): void {
  const sidebar = new Sidebar(context.extensionUri, actions(() => void sidebar.refresh()));
  context.subscriptions.push(
    sidebar,
    output(),
    vscode.window.registerWebviewViewProvider(Sidebar.viewId, sidebar, {
      // An expanded speakers section with a half-played snippet in it has to
      // survive the sidebar being hidden; rebuilding it would cost a subprocess
      // and lose the playback position.
      webviewOptions: { retainContextWhenHidden: true },
    }),
  );

  const register = (name: string, handler: (...args: never[]) => unknown) =>
    context.subscriptions.push(vscode.commands.registerCommand(name, handler));

  register("referat.refresh", () => void sidebar.refresh());
  register("referat.showOutput", () => output().show(true));
  register("referat.projects", () => void manageProjects(() => void sidebar.refresh()));

  register("referat.openMeetingsFolder", async () => {
    const dir = sidebar.meetingsDir();
    if (!dir) {
      void vscode.window.showWarningMessage("Referat has not read the meetings folder yet.");
      return;
    }
    await vscode.env.openExternal(vscode.Uri.file(dir));
  });

  context.subscriptions.push(statusBar(context));

  // The roots come out of the repository's own config.toml, so pointing the
  // extension at a different repository changes where it is looking.
  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (event.affectsConfiguration("referat.repoRoot")) {
        void sidebar.refresh();
      }
    }),
  );
}

export function deactivate(): void {
  // Everything is in context.subscriptions.
}

// --- What a meeting row's buttons do ----------------------------------------

function actions(refresh: () => void): Actions {
  return {
    openTranscript: (meeting) => openMarkdown(meetingFile(meeting, "transcript.md")),
    openNotes: (meeting) => openMarkdown(meetingFile(meeting, "notes.md")),
    editTags: (meeting, projects) => editTags(meeting, projects, refresh),
    generateNotes: (meeting, meetingsDir) => runCleanup(meeting, meetingsDir, refresh),
    reTranscribe: (meeting) => reTranscribe(meeting, refresh),
    acceptAudioLoss: (meeting) => acceptAudioLoss(meeting, refresh),
    deleteMeeting: (meeting) => deleteMeeting(meeting, refresh),
  };
}

/**
 * *Generate notes*: `/cleanup` through the official Claude Code binary, and then
 * the one lifecycle transition no pipeline can make.
 *
 * `/cleanup` is forbidden from touching `meta.json`, so it cannot record that it
 * wrote `notes.md`; whoever spawned it says so afterwards, which is this.
 */
async function runCleanup(
  meeting: MeetingJson,
  meetingsDir: string,
  refresh: () => void,
): Promise<void> {
  if (meeting.staged) {
    // A staged meeting is not in the meetings folder at all, so `/cleanup`
    // running there would not find it. Say why rather than letting the pass
    // fail with a confusing "no such meeting".
    void vscode.window.showWarningMessage(
      `${meeting.id} is still in staging because its audio was kept. Re-transcribe it, or accept the transcript and let the audio go.`,
    );
    return;
  }
  try {
    const { code, stderr } = await generateNotes(meeting.id, meetingsDir);
    if (code !== 0) {
      refresh();
      report(new Error(firstLine(stderr) || `claude exited ${code} writing notes.`));
      return;
    }
    const complaint = await setNotesWritten(meeting.id);
    if (complaint) {
      // Worth showing rather than swallowing: the notes exist either way, but a
      // meeting stuck at `transcribed` will be offered *Generate notes* again.
      output().appendLine(complaint);
      void vscode.window.showWarningMessage(complaint);
    }
    refresh();
    await openMarkdown(meetingFile(meeting, "notes.md"));
  } catch (err) {
    report(err);
  }
}

function reTranscribe(meeting: MeetingJson, refresh: () => void): void {
  // A terminal rather than a spawn with a progress toast: `referat rerun`
  // takes minutes, loads a model and logs continuously, and that output is
  // the thing worth watching while it does.
  let root: string;
  let python: string;
  try {
    root = repoRoot();
    python = interpreter();
  } catch (err) {
    report(err);
    return;
  }
  const terminal = vscode.window.createTerminal({ name: `referat rerun ${meeting.id}`, cwd: root });
  terminal.show();
  // Same interpreter the rest of the extension uses, and the same reason:
  // Smart App Control blocks uv.exe on this machine.
  terminal.sendText(`& "${python}" -m referat.cli rerun ${meeting.id}`);
  const listener = vscode.window.onDidCloseTerminal((closed) => {
    if (closed === terminal) {
      refresh();
      listener.dispose();
    }
  });
}

/**
 * The off-ramp for a meeting stuck in staging because the quality gate refused
 * its transcript.
 *
 * `promote_meeting` will not move a folder that still holds WAVs, and that
 * refusal is the invariant the staging split exists for: no WAV may ever reach
 * the meetings folder, because deleting a file inside a synced folder does not
 * delete it. So accepting cannot mean "promote with the audio" — it means
 * **delete the recordings and then promote**, and the modal says that in those
 * words rather than talking about accepting something.
 */
async function acceptAudioLoss(meeting: MeetingJson, refresh: () => void): Promise<void> {
  const confirmed = await vscode.window.showWarningMessage(
    `Delete ${meeting.id}'s recordings?`,
    {
      modal: true,
      detail:
        "mic.wav and system.wav will be permanently deleted, and the meeting will move into " +
        "the meetings folder. transcript.md is all that will be left of it — the transcript " +
        "the quality gate was not confident in, and there will be no audio left to " +
        "re-transcribe from. This cannot be undone.",
    },
    "Delete the audio and promote",
  );
  if (confirmed !== "Delete the audio and promote") {
    return;
  }
  const complaint = await promoteReleasingAudio(meeting.id);
  if (complaint) {
    report(new Error(complaint));
  }
  refresh();
}

/**
 * *Delete*: remove the meeting folder and everything in it.
 *
 * The modal carries the two things `referat delete` prints and a person would
 * otherwise assume the other way. A meeting in the meetings folder is not really
 * gone if that folder is synced — a sync client keeps deleted files and prior
 * versions on its own servers for weeks — while a staged one is; and the
 * voiceprints this meeting contributed stay in the known-voices database,
 * because deleting a meeting is not deleting a person.
 *
 * Both sentences are composed here rather than read back from the CLI, which is
 * the one place this extension says something Python also says. It is a *modal*,
 * shown before the command runs and therefore before there is any output to
 * quote; what a refusal says still comes back through `mutate` unedited.
 */
async function deleteMeeting(meeting: MeetingJson, refresh: () => void): Promise<void> {
  const where = meeting.staged
    ? "It is still in staging, outside any synced folder, so this deletion is real."
    : "If your meetings folder is synced, deleting a file does not delete it: the sync " +
      "client keeps deleted files and prior versions on its servers for weeks.";
  const audio = meeting.audio === "kept" ? " Its recordings will go with it." : "";
  const confirmed = await vscode.window.showWarningMessage(
    `Delete ${meeting.id}?`,
    {
      modal: true,
      detail:
        `The whole folder goes: transcript.md, notes.md, meta.json and any speaker ` +
        `snippets.${audio} This cannot be undone.\n\n${where}\n\n` +
        "Voiceprints this meeting contributed stay in the known-voices database — " +
        "`referat label --forget <name>` is how a person is removed.",
    },
    "Delete the meeting",
  );
  if (confirmed !== "Delete the meeting") {
    return;
  }
  const complaint = await deleteMeetingFolder(meeting.id);
  if (complaint) {
    report(new Error(complaint));
  }
  refresh();
}

// --- The status bar ---------------------------------------------------------

/**
 * Ambient tray state: what `referat status --json` says, and nothing more.
 *
 * **It does not tick.** The elapsed time is `format_duration`'s output, read
 * when the tray last wrote its status file or when the poll last fired; a local
 * clock here would be a second duration formatter in TypeScript, which is the
 * duplication this whole extension is arranged to avoid.
 *
 * The item exists once the sidebar has been opened in a window, because that is
 * what activates the extension — and no window that never opens Referat should
 * start a Python interpreter to find out that nothing is recording.
 */
function statusBar(context: vscode.ExtensionContext): vscode.Disposable {
  const item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
  item.command = "referat.showOutput";
  const timer = setInterval(() => void update(), STATUS_POLL_MS);
  const watcher = statusFileWatcher(() => void update());

  async function update(): Promise<void> {
    let status: StatusJson;
    try {
      status = await readStatus();
    } catch {
      // A missing venv or an unreadable repository is already reported by
      // whatever the user actually asked for. The status bar stays quiet.
      item.hide();
      return;
    }
    item.text = statusText(status);
    item.tooltip = statusTooltip(status);
    if (status.running || status.stale) {
      item.show();
    } else {
      item.hide();
    }
  }

  void update();
  context.subscriptions.push(item);
  return {
    dispose() {
      clearInterval(timer);
      watcher?.dispose();
      item.dispose();
    },
  };
}

function statusText(status: StatusJson): string {
  if (status.stale) {
    return "$(warning) Referat stopped";
  }
  if (!status.running) {
    return "";
  }
  const parts = [status.state ?? "idle"];
  if (status.meeting_id && status.elapsed) {
    parts.push(status.elapsed);
  } else if (status.meeting_id) {
    parts.push(status.meeting_id);
  }
  if (status.jobs) {
    parts.push(`(${status.jobs} transcribing)`);
  }
  return `${icon(status.state)} ${parts.join(" ")}`;
}

function icon(state: string | undefined): string {
  switch (state) {
    case "recording":
      return "$(record)";
    case "paused":
      return "$(debug-pause)";
    case "transcribing":
      return "$(sync~spin)";
    default:
      return "$(circle-large-outline)";
  }
}

function statusTooltip(status: StatusJson): string {
  if (status.stale) {
    return `The tray left a status file from ${status.updated_at} saying ${status.state}, and its process (pid ${status.pid}) is gone.`;
  }
  if (!status.running) {
    return "The Referat tray is not running.";
  }
  return [
    `Referat is ${status.state}`,
    status.meeting_id ? `meeting ${status.meeting_id}` : "",
    `tray pid ${status.pid}`,
    `updated ${status.updated_at}`,
  ]
    .filter(Boolean)
    .join("\n");
}

/**
 * Watch the tray's `status.json`.
 *
 * It lives in `%LOCALAPPDATA%\Referat`, which is outside every workspace — the
 * same situation as the meeting roots, and `RelativePattern` handles it the same
 * way. Undefined when `LOCALAPPDATA` is not set, in which case the poll above is
 * the only refresh, which is the degradation this can afford.
 */
function statusFileWatcher(onChange: () => void): vscode.FileSystemWatcher | undefined {
  const local = process.env.LOCALAPPDATA;
  if (!local) {
    return undefined;
  }
  const pattern = new vscode.RelativePattern(
    vscode.Uri.joinPath(vscode.Uri.file(local), "Referat"),
    "status.json",
  );
  const watcher = vscode.workspace.createFileSystemWatcher(pattern);
  watcher.onDidCreate(onChange);
  watcher.onDidChange(onChange);
  watcher.onDidDelete(onChange);
  return watcher;
}

/**
 * Open Markdown rendered. The meetings folder's own `.vscode/settings.json`
 * associates `*.md` with the preview editor, but that only applies when that
 * folder is the workspace — the extension is usually run from the repository
 * instead, so it asks for the preview explicitly.
 */
async function openMarkdown(uri: vscode.Uri): Promise<void> {
  try {
    await vscode.workspace.fs.stat(uri);
  } catch {
    void vscode.window.showWarningMessage(`${uri.fsPath} does not exist.`);
    return;
  }
  await vscode.commands.executeCommand("markdown.showPreview", uri);
}
