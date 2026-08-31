/**
 * The Referat extension: activation, the commands, and the wiring between them.
 *
 * This and the tray icon are the only graphical surfaces Referat has, and the
 * only ones it will ever have — there is no web UI in this project. Everything
 * a person needs to look at or click is a node in this tree, an editor it
 * opened, or the labeling panel.
 *
 * Every command here either opens a file or shells out to `referat` / `claude`.
 * None of them reimplements anything Python already does.
 */
import * as vscode from "vscode";
import { generateNotes } from "./claude";
import { firstLine, interpreter, output, repoRoot, report } from "./cli";
import { LabelPanel } from "./labelPanel";
import { MeetingNode, MeetingsProvider, Node, meetingFile } from "./tree";

export function activate(context: vscode.ExtensionContext): void {
  const provider = new MeetingsProvider();
  const tree = vscode.window.createTreeView("referat.meetings", { treeDataProvider: provider });
  context.subscriptions.push(provider, tree, output());

  const register = (name: string, handler: (...args: never[]) => unknown) =>
    context.subscriptions.push(vscode.commands.registerCommand(name, handler));

  register("referat.refresh", () => provider.refresh());
  register("referat.showOutput", () => output().show(true));

  register("referat.openMeetingsFolder", async () => {
    const dir = provider.meetingsDir();
    if (!dir) {
      void vscode.window.showWarningMessage("Referat has not read the meetings folder yet.");
      return;
    }
    await vscode.env.openExternal(vscode.Uri.file(dir));
  });

  register("referat.openTranscript", (node: MeetingNode) =>
    openMarkdown(meetingFile(node.meeting, "transcript.md")),
  );
  register("referat.openNotes", (node: MeetingNode) =>
    openMarkdown(meetingFile(node.meeting, "notes.md")),
  );

  register("referat.generateNotes", async (node: MeetingNode) => {
    const meetingsDir = provider.meetingsDir();
    if (!meetingsDir) {
      void vscode.window.showWarningMessage("Referat has not read the meetings folder yet.");
      return;
    }
    if (node.meeting.staged) {
      // A staged meeting is not in the meetings folder at all, so `/cleanup`
      // running there would not find it. Say why rather than letting the pass
      // fail with a confusing "no such meeting".
      void vscode.window.showWarningMessage(
        `${node.meeting.id} is still in staging because its audio was kept. Re-transcribe it first.`,
      );
      return;
    }
    try {
      const { code, stderr } = await generateNotes(node.meeting.id, meetingsDir);
      provider.refresh();
      if (code !== 0) {
        report(new Error(firstLine(stderr) || `claude exited ${code} writing notes.`));
        return;
      }
      await openMarkdown(meetingFile(node.meeting, "notes.md"));
    } catch (err) {
      report(err);
    }
  });

  register("referat.reTranscribe", async (node: MeetingNode) => {
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
    const terminal = vscode.window.createTerminal({
      name: `referat rerun ${node.meeting.id}`,
      cwd: root,
    });
    terminal.show();
    // Same interpreter the rest of the extension uses, and the same reason:
    // Smart App Control blocks uv.exe on this machine.
    terminal.sendText(`& "${python}" -m referat.cli rerun ${node.meeting.id}`);
    const listener = vscode.window.onDidCloseTerminal((closed) => {
      if (closed === terminal) {
        provider.refresh();
        listener.dispose();
      }
    });
    context.subscriptions.push(listener);
  });

  register("referat.labelSpeakers", async (node: Node) => {
    const meeting = node.meeting;
    await LabelPanel.show(context.extensionUri, meeting.id, meeting.dir, () => provider.refresh());
  });

  // The roots come out of the repository's own config.toml, so pointing the
  // extension at a different repository changes where it is looking.
  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (event.affectsConfiguration("referat.repoRoot")) {
        provider.refresh();
      }
    }),
  );
}

export function deactivate(): void {
  // Everything is in context.subscriptions.
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
