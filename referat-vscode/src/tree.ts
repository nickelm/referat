/**
 * The Meetings tree, and the watcher that keeps it live.
 *
 * Newest first, unlike `referat list` and like the meetings folder's generated
 * `INDEX.md`: a listing is read at a prompt with the last line nearest the
 * cursor, a dashboard is read from the top. This is the dashboard.
 */
import * as path from "node:path";
import * as vscode from "vscode";
import { ListJson, MeetingJson, listMeetings, report } from "./cli";

export type Node = MeetingNode | GroupNode | SpeakerNode;

export interface MeetingNode {
  kind: "meeting";
  meeting: MeetingJson;
}

export interface GroupNode {
  kind: "group";
  meeting: MeetingJson;
}

export interface SpeakerNode {
  kind: "speaker";
  meeting: MeetingJson;
  speaker: string;
}

/** How long to sit on a burst of file events before re-asking Python. */
const DEBOUNCE_MS = 300;

export class MeetingsProvider implements vscode.TreeDataProvider<Node>, vscode.Disposable {
  private readonly changed = new vscode.EventEmitter<Node | undefined>();
  readonly onDidChangeTreeData = this.changed.event;

  private watchers: vscode.FileSystemWatcher[] = [];
  private timer: NodeJS.Timeout | undefined;
  private roots: { meetings: string; staging: string } | undefined;

  dispose(): void {
    this.disposeWatchers();
    this.changed.dispose();
  }

  refresh(): void {
    this.changed.fire(undefined);
  }

  /** Where the meetings live, as of the last successful listing. */
  meetingsDir(): string | undefined {
    return this.roots?.meetings;
  }

  async getChildren(node?: Node): Promise<Node[]> {
    if (!node) {
      return this.rootChildren();
    }
    if (node.kind === "meeting") {
      return node.meeting.unnamed.length ? [{ kind: "group", meeting: node.meeting }] : [];
    }
    if (node.kind === "group") {
      return node.meeting.unnamed.map((speaker) => ({
        kind: "speaker",
        meeting: node.meeting,
        speaker,
      }));
    }
    return [];
  }

  private async rootChildren(): Promise<Node[]> {
    let listing: ListJson;
    try {
      listing = await listMeetings();
    } catch (err) {
      report(err);
      return [];
    }
    this.watch(listing);
    // `referat list` is oldest first by design; a dashboard is read from the top.
    return [...listing.meetings]
      .reverse()
      .map((meeting): MeetingNode => ({ kind: "meeting", meeting }));
  }

  getTreeItem(node: Node): vscode.TreeItem {
    if (node.kind === "meeting") {
      return meetingItem(node);
    }
    if (node.kind === "group") {
      const item = new vscode.TreeItem(
        `Unknown speakers (${node.meeting.unnamed.length})`,
        vscode.TreeItemCollapsibleState.Collapsed,
      );
      item.iconPath = new vscode.ThemeIcon("question");
      item.contextValue = "unknownGroup";
      return item;
    }
    const item = new vscode.TreeItem(node.speaker, vscode.TreeItemCollapsibleState.None);
    item.iconPath = new vscode.ThemeIcon("person");
    item.contextValue = "speaker";
    item.tooltip = `Name ${node.speaker} in ${node.meeting.id}`;
    item.command = {
      command: "referat.labelSpeakers",
      title: "Name Speakers",
      arguments: [node],
    };
    return item;
  }

  // --- The watcher --------------------------------------------------------

  /**
   * Watch both roots for the three files that change what the tree shows.
   * Re-created only when the roots actually move, so an ordinary refresh does
   * not churn them.
   */
  private watch(listing: ListJson): void {
    if (this.roots?.meetings === listing.meetings_dir && this.roots.staging === listing.staging_dir) {
      return;
    }
    this.disposeWatchers();
    this.roots = { meetings: listing.meetings_dir, staging: listing.staging_dir };

    for (const root of new Set([listing.meetings_dir, listing.staging_dir])) {
      const pattern = new vscode.RelativePattern(
        vscode.Uri.file(root),
        "**/{meta.json,notes.md,transcript.md}",
      );
      const watcher = vscode.workspace.createFileSystemWatcher(pattern);
      watcher.onDidCreate(() => this.debouncedRefresh());
      watcher.onDidChange(() => this.debouncedRefresh());
      watcher.onDidDelete(() => this.debouncedRefresh());
      this.watchers.push(watcher);
    }
  }

  /**
   * Transcription rewrites `meta.json` several times a meeting, and every
   * refresh is a `uv run`. Coalesce the burst.
   */
  private debouncedRefresh(): void {
    if (this.timer) {
      clearTimeout(this.timer);
    }
    this.timer = setTimeout(() => {
      this.timer = undefined;
      this.refresh();
    }, DEBOUNCE_MS);
  }

  private disposeWatchers(): void {
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = undefined;
    }
    for (const watcher of this.watchers) {
      watcher.dispose();
    }
    this.watchers = [];
  }
}

function meetingItem(node: MeetingNode): vscode.TreeItem {
  const { meeting } = node;
  const time = meeting.started_at.slice(11, 16);
  const label = meeting.title === meeting.id ? meeting.id : `${meeting.id}  ${meeting.title}`;
  const item = new vscode.TreeItem(
    label,
    meeting.unnamed.length
      ? vscode.TreeItemCollapsibleState.Collapsed
      : vscode.TreeItemCollapsibleState.None,
  );
  const parts = [meeting.duration, meeting.status];
  if (meeting.staged) {
    parts.push("staged");
  }
  item.description = parts.join(" \u00b7 ");
  item.iconPath = new vscode.ThemeIcon(iconFor(meeting));
  item.tooltip = new vscode.MarkdownString(
    [
      `**${meeting.title}**`,
      "",
      `${meeting.started_at.slice(0, 10)} ${time} \u00b7 ${meeting.duration} \u00b7 ${meeting.status}`,
      `audio: ${meeting.audio}${meeting.staged ? " \u00b7 still in staging" : ""}`,
      meeting.unnamed.length ? `unnamed: ${meeting.unnamed.join(", ")}` : "",
      "",
      `\`${meeting.dir}\``,
    ]
      .filter(Boolean)
      .join("\n\n"),
  );
  // Composed rather than switched on, so a menu `when` clause can ask about one
  // capability without enumerating every combination of the others.
  item.contextValue = [
    "meeting",
    meeting.transcript ? "hasTranscript" : "",
    meeting.notes ? "hasNotes" : "",
    meeting.audio === "kept" ? "hasAudio" : "",
    meeting.unnamed.length ? "hasUnnamed" : "",
  ]
    .filter(Boolean)
    .join(" ");
  return item;
}

function iconFor(meeting: MeetingJson): string {
  switch (meeting.status) {
    case "recording":
      return "record";
    case "transcribing":
      return "sync~spin";
    case "failed":
      return "error";
  }
  if (meeting.notes) {
    return "notebook";
  }
  return meeting.transcript ? "file-text" : "circle-outline";
}

export function meetingFile(meeting: MeetingJson, name: string): vscode.Uri {
  return vscode.Uri.file(path.join(meeting.dir, name));
}
