/**
 * The Meetings sidebar — one webview view, and the whole browsing UI.
 *
 * **This replaces the TreeView built at step 11.** A tree could list meetings
 * and hang a context menu off them; it could not render step 14's lifecycle
 * field as a strip, show the project tags a meeting carries, fold the speaker
 * labeling into the row it belongs to, or put an off-ramp button on the one
 * meeting that is stuck. Those are the four things worth looking at, so the
 * tree went.
 *
 * **A webview inside an extension is not a web UI.** That rule is about Flask,
 * FastAPI, a localhost server and a browser front end, and there is still none
 * of it: this is an extension view, served from disk, talking to its host over
 * `postMessage` and to nothing else.
 *
 * **Rendering happens in the page, not here.** The host posts the document
 * `referat list --json` produced and `media/sidebar.js` builds the DOM from it.
 * Re-assigning `webview.html` on every refresh would be simpler to write and
 * wrong to use: transcription rewrites `meta.json` several times a meeting, and
 * every one of those would collapse an expanded row and stop a snippet
 * mid-playback. The page therefore updates in place and keeps what the user was
 * doing.
 *
 * Nothing here re-derives a meeting's state, a duration or a project's name.
 * All of that arrives in the document; see `src/cli.ts` for why.
 */
import * as path from "node:path";
import * as vscode from "vscode";
import {
  LabelJson,
  ListJson,
  MeetingJson,
  applyName,
  labelPayload,
  listMeetings,
  output,
  report,
} from "./cli";

/** How long to sit on a burst of file events before re-asking Python. */
const DEBOUNCE_MS = 300;

/** What the page may send us. Anything else is ignored rather than trusted. */
type Inbound =
  | { type: "ready" }
  | { type: "action"; action: string; meeting: string }
  | { type: "speakers"; meeting: string }
  | { type: "apply"; meeting: string; speaker: string; name: string };

/** What one meeting row's actions do. Registered by `extension.ts`, which owns them. */
export interface Actions {
  openTranscript(meeting: MeetingJson): void | Promise<void>;
  openNotes(meeting: MeetingJson): void | Promise<void>;
  generateNotes(meeting: MeetingJson, meetingsDir: string): void | Promise<void>;
  reTranscribe(meeting: MeetingJson): void | Promise<void>;
  editTags(meeting: MeetingJson, projects: Record<string, string>): void | Promise<void>;
  acceptAudioLoss(meeting: MeetingJson): void | Promise<void>;
  deleteMeeting(meeting: MeetingJson): void | Promise<void>;
}

export class Sidebar implements vscode.WebviewViewProvider, vscode.Disposable {
  static readonly viewId = "referat.meetings";

  private view: vscode.WebviewView | undefined;
  private watchers: vscode.FileSystemWatcher[] = [];
  private timer: NodeJS.Timeout | undefined;
  private roots: { meetings: string; staging: string } | undefined;
  /**
   * The last document Python produced.
   *
   * Kept so a page that reloads — which is what re-assigning `localResourceRoots`
   * may cost us, once, the first time the roots become known — can be answered
   * from memory instead of with another interpreter start.
   */
  private listing: ListJson | undefined;

  constructor(
    private readonly extensionUri: vscode.Uri,
    private readonly actions: Actions,
  ) {}

  dispose(): void {
    this.disposeWatchers();
  }

  /** Where the meetings live, as of the last successful listing. */
  meetingsDir(): string | undefined {
    return this.roots?.meetings;
  }

  resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    view.webview.options = { enableScripts: true, localResourceRoots: this.resourceRoots() };
    view.webview.html = this.html(view.webview);
    view.webview.onDidReceiveMessage((message: Inbound) => void this.onMessage(message));
    view.onDidDispose(() => {
      // The watchers outlive a hidden view but not a disposed one; a new
      // `resolveWebviewView` re-creates them from the next listing.
      this.view = undefined;
    });
    void this.refresh();
  }

  // --- Talking to Python --------------------------------------------------

  async refresh(): Promise<void> {
    let listing: ListJson;
    try {
      listing = await listMeetings();
    } catch (err) {
      report(err);
      return;
    }
    this.listing = listing;
    this.watch(listing);
    this.post();
  }

  /** Send the cached document to the page. Newest first; the CLI's order stays. */
  private post(): void {
    const listing = this.listing;
    if (!this.view || !listing) {
      return;
    }
    void this.view.webview.postMessage({
      type: "listing",
      // `referat list` is oldest first by design — a listing is read at a prompt
      // with the last line nearest the cursor. A dashboard is read from the top,
      // so the reversal is a presentation choice made here and never in the CLI.
      meetings: [...listing.meetings].reverse(),
      projects: listing.projects,
      meetingsDir: listing.meetings_dir,
    });
  }

  private async onMessage(message: Inbound): Promise<void> {
    if (!message || typeof message.type !== "string") {
      return;
    }
    if (message.type === "ready") {
      this.post();
      return;
    }
    if (message.type === "speakers") {
      await this.sendSpeakers(message.meeting);
      return;
    }
    if (message.type === "apply") {
      await this.applyName(message.meeting, message.speaker, message.name);
      return;
    }
    if (message.type === "action") {
      await this.runAction(message.action, message.meeting);
    }
  }

  private meeting(id: string): MeetingJson | undefined {
    return this.listing?.meetings.find((m) => m.id === id);
  }

  private async runAction(action: string, id: string): Promise<void> {
    const meeting = this.meeting(id);
    if (!meeting) {
      // The page is showing a listing older than the one we hold; re-post rather
      // than acting on a meeting that may have been promoted out from under it.
      this.post();
      return;
    }
    try {
      switch (action) {
        case "openTranscript":
          return void (await this.actions.openTranscript(meeting));
        case "openNotes":
          return void (await this.actions.openNotes(meeting));
        case "generateNotes": {
          const dir = this.meetingsDir();
          if (!dir) {
            void vscode.window.showWarningMessage("Referat has not read the meetings folder yet.");
            return;
          }
          await this.actions.generateNotes(meeting, dir);
          return;
        }
        case "reTranscribe":
          return void (await this.actions.reTranscribe(meeting));
        case "tags":
          return void (await this.actions.editTags(meeting, this.listing?.projects ?? {}));
        case "accept":
          return void (await this.actions.acceptAudioLoss(meeting));
        case "delete":
          return void (await this.actions.deleteMeeting(meeting));
        default:
          output().appendLine(`sidebar: ignoring unknown action ${action}`);
      }
    } catch (err) {
      report(err);
    }
  }

  /**
   * Fetch one meeting's unnamed speakers, on expansion.
   *
   * Per meeting rather than for the whole listing: this is a second interpreter
   * start, and only the row somebody has opened needs it.
   *
   * The snippet paths are turned into webview URIs **here**, because only the
   * host knows what `localResourceRoots` will let the page load. They are 16 kHz
   * mono 16-bit PCM, which Chromium decodes without help.
   */
  private async sendSpeakers(id: string): Promise<void> {
    let data: LabelJson;
    try {
      data = await labelPayload(id);
    } catch (err) {
      report(err);
      this.postToPage({ type: "speakers", meeting: id, failed: true });
      return;
    }
    const webview = this.view?.webview;
    if (!webview) {
      return;
    }
    this.postToPage({
      type: "speakers",
      meeting: id,
      known: data.known_names,
      // Which channel a voice arrived on is the strongest hint available about
      // who it is, and the panel was showing none of it — somebody naming four
      // speakers after a Teams call was one of them, with nothing saying so.
      // `owner` lets the page lead with that name for a microphone speaker. A
      // hint, never a name applied on its own: the mic hears the whole room.
      owner: data.owner,
      speakers: data.speakers.map((speaker) => ({
        speaker: speaker.speaker,
        channel: speaker.channel,
        hasEmbedding: speaker.has_embedding,
        lines: speaker.lines,
        snippets: speaker.snippets.map((file) => ({
          name: path.basename(file),
          uri: webview.asWebviewUri(vscode.Uri.file(file)).toString(),
        })),
      })),
    });
  }

  /**
   * Name one speaker.
   *
   * The refusal — a reserved name, a speaker that already has one, an empty
   * string — is `voices.name_complaint`'s and `label.apply_name`'s, and it is
   * shown beside the field that caused it rather than as a toast, because it is
   * about the text still sitting in that box.
   */
  private async applyName(id: string, speaker: string, name: string): Promise<void> {
    let complaint: string | null;
    try {
      complaint = await applyName(id, speaker, name);
    } catch (err) {
      report(err);
      this.postToPage({ type: "refused", meeting: id, speaker, message: "See the Referat output." });
      return;
    }
    if (complaint) {
      this.postToPage({ type: "refused", meeting: id, speaker, message: complaint });
      return;
    }
    // Naming somebody changes the meeting's unnamed list and the dashboard's
    // Unnamed column, both of which `referat label` has already rewritten.
    await this.refresh();
    await this.sendSpeakers(id);
  }

  private postToPage(message: unknown): void {
    void this.view?.webview.postMessage(message);
  }

  // --- The watcher --------------------------------------------------------

  /**
   * Watch both roots for the three files that change what the sidebar shows.
   *
   * Unchanged from the tree, deliberately: both roots because a recording lives
   * in staging and would otherwise not appear until it promoted, and debounced
   * because transcription rewrites `meta.json` several times a meeting and every
   * refresh costs an interpreter start.
   */
  private watch(listing: ListJson): void {
    if (this.roots?.meetings === listing.meetings_dir && this.roots.staging === listing.staging_dir) {
      return;
    }
    this.disposeWatchers();
    this.roots = { meetings: listing.meetings_dir, staging: listing.staging_dir };
    // The page cannot load a snippet out of a folder that is not a resource
    // root, and the roots are only known once Python has said where they are.
    if (this.view) {
      this.view.webview.options = {
        enableScripts: true,
        localResourceRoots: this.resourceRoots(),
      };
    }

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

  private resourceRoots(): vscode.Uri[] {
    const roots = [vscode.Uri.joinPath(this.extensionUri, "media")];
    for (const dir of [this.roots?.meetings, this.roots?.staging]) {
      if (dir) {
        roots.push(vscode.Uri.file(dir));
      }
    }
    return roots;
  }

  private debouncedRefresh(): void {
    if (this.timer) {
      clearTimeout(this.timer);
    }
    this.timer = setTimeout(() => {
      this.timer = undefined;
      void this.refresh();
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

  // --- The page -----------------------------------------------------------

  /**
   * The shell. Everything inside `#meetings` is built by the script from the
   * documents posted to it, so this is written once and never re-assigned.
   *
   * The CSP allows the theme stylesheet, one nonced script and media — the
   * snippets — and nothing else. No network, no inline handlers, no eval.
   */
  private html(webview: vscode.Webview): string {
    const nonce = makeNonce();
    const styles = webview.asWebviewUri(
      vscode.Uri.joinPath(this.extensionUri, "media", "sidebar.css"),
    );
    const script = webview.asWebviewUri(
      vscode.Uri.joinPath(this.extensionUri, "media", "sidebar.js"),
    );
    return [
      "<!DOCTYPE html>",
      '<html lang="en">',
      "<head>",
      '<meta charset="UTF-8" />',
      `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; media-src ${webview.cspSource}; style-src ${webview.cspSource}; script-src 'nonce-${nonce}';" />`,
      '<meta name="viewport" content="width=device-width, initial-scale=1.0" />',
      `<link href="${styles}" rel="stylesheet" />`,
      "<title>Meetings</title>",
      "</head>",
      "<body>",
      '<div class="toolbar">',
      '<input type="search" id="search" class="search" placeholder="Filter meetings…" ' +
        'aria-label="Filter meetings by title, date or project" />',
      '<div class="toolbar-row">',
      '<label class="toggle"><input type="checkbox" id="untagged-only" /> Untagged only</label>',
      '<span class="count" id="count"></span>',
      "</div>",
      "</div>",
      '<div id="meetings"><p class="muted">Reading meetings…</p></div>',
      `<script nonce="${nonce}" src="${script}"></script>`,
      "</body>",
      "</html>",
    ].join("\n");
  }
}

export function meetingFile(meeting: MeetingJson, name: string): vscode.Uri {
  return vscode.Uri.file(path.join(meeting.dir, name));
}

function makeNonce(): string {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  let nonce = "";
  for (let i = 0; i < 32; i++) {
    nonce += alphabet.charAt(Math.floor(Math.random() * alphabet.length));
  }
  return nonce;
}
