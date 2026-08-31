/**
 * The labeling webview — `referat label` with a play button and a text field.
 *
 * It reimplements none of the matching. The panel shows what `referat label
 * <id> --json` reports and posts the answer back to `referat label <id>
 * --speaker <s> --name <n>`; the database write, the `meta.json` update, the
 * transcript relabeling, the snippet deletion and the reserved-name rule all
 * stay in Python, where they already are. The panel's whole job is playing a
 * few seconds of audio and collecting a string.
 *
 * Snippets reach the page as webview resource URIs. They are mono 16-bit PCM
 * WAV at 16 kHz, which every Chromium build decodes, so there is no transcoding
 * and no playback plumbing on the extension host side.
 */
import * as path from "node:path";
import * as vscode from "vscode";
import { LabelJson, SpeakerJson, applyName, labelPayload, report } from "./cli";

export class LabelPanel {
  private static current: LabelPanel | undefined;

  private readonly disposables: vscode.Disposable[] = [];

  private constructor(
    private readonly panel: vscode.WebviewPanel,
    private readonly extensionUri: vscode.Uri,
    readonly meetingId: string,
    private readonly onChanged: () => void,
  ) {
    this.panel.onDidDispose(() => this.dispose(), null, this.disposables);
    this.panel.webview.onDidReceiveMessage(
      (message) => void this.onMessage(message),
      null,
      this.disposables,
    );
  }

  static async show(
    extensionUri: vscode.Uri,
    meetingId: string,
    meetingDir: string,
    onChanged: () => void,
  ): Promise<void> {
    if (LabelPanel.current?.meetingId === meetingId) {
      LabelPanel.current.panel.reveal(vscode.ViewColumn.Active);
      await LabelPanel.current.load();
      return;
    }
    LabelPanel.current?.dispose();

    const panel = vscode.window.createWebviewPanel(
      "referat.label",
      `Speakers · ${meetingId}`,
      vscode.ViewColumn.Active,
      {
        enableScripts: true,
        localResourceRoots: [
          vscode.Uri.joinPath(extensionUri, "media"),
          vscode.Uri.file(path.join(meetingDir, "speakers")),
        ],
      },
    );
    LabelPanel.current = new LabelPanel(panel, extensionUri, meetingId, onChanged);
    await LabelPanel.current.load();
  }

  private async load(): Promise<void> {
    let data: LabelJson;
    try {
      data = await labelPayload(this.meetingId);
    } catch (err) {
      report(err);
      return;
    }
    if (data.speakers.length === 0) {
      // Everybody in this meeting has a name now. An empty panel left open
      // would be a worse answer than closing it and saying so.
      void vscode.window.showInformationMessage(`${this.meetingId}: every speaker has a name.`);
      this.dispose();
      return;
    }
    this.panel.webview.html = this.render(data);
  }

  private async onMessage(message: {
    type?: string;
    speaker?: string;
    name?: string;
  }): Promise<void> {
    if (message.type !== "apply" || !message.speaker || !message.name) {
      return;
    }
    let complaint: string | null;
    try {
      complaint = await applyName(this.meetingId, message.speaker, message.name);
    } catch (err) {
      report(err);
      return;
    }
    if (complaint) {
      // Shown beside the field that caused it rather than as a toast: the name
      // validator's complaints are about the text still sitting in that box.
      void this.panel.webview.postMessage({
        type: "refused",
        speaker: message.speaker,
        message: complaint,
      });
      return;
    }
    this.onChanged();
    await this.load();
  }

  // --- Rendering ----------------------------------------------------------

  private render(data: LabelJson): string {
    const webview = this.panel.webview;
    const nonce = makeNonce();
    const styles = webview.asWebviewUri(
      vscode.Uri.joinPath(this.extensionUri, "media", "label.css"),
    );
    const script = webview.asWebviewUri(vscode.Uri.joinPath(this.extensionUri, "media", "label.js"));
    const cards = data.speakers.map((s) => this.card(s, data.known_names)).join("\n");
    const plural = data.speakers.length === 1 ? "" : "s";

    return [
      "<!DOCTYPE html>",
      '<html lang="en">',
      "<head>",
      '<meta charset="UTF-8" />',
      `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; media-src ${webview.cspSource}; style-src ${webview.cspSource}; script-src 'nonce-${nonce}';" />`,
      '<meta name="viewport" content="width=device-width, initial-scale=1.0" />',
      `<link href="${styles}" rel="stylesheet" />`,
      "<title>Speakers</title>",
      "</head>",
      "<body>",
      "<header>",
      `<h1>${escapeHtml(data.meeting)}</h1>`,
      `<p class="muted">${data.speakers.length} speaker${plural} still waiting for a name.`,
      "Naming somebody here teaches that voice to every meeting after this one, so a wrong",
      "name spreads. Leave it as a number if you are not sure.</p>",
      "</header>",
      cards,
      `<script nonce="${nonce}" src="${script}"></script>`,
      "</body>",
      "</html>",
    ].join("\n");
  }

  private card(speaker: SpeakerJson, known: string[]): string {
    const webview = this.panel.webview;
    const id = escapeHtml(speaker.speaker);

    const evidence = speaker.snippets.length
      ? [
          '<div class="clips">',
          ...speaker.snippets.map((file, n) =>
            [
              '<div class="clip">',
              `<span class="clip-name">${n + 1}. ${escapeHtml(path.basename(file))}</span>`,
              `<audio controls preload="none" src="${webview.asWebviewUri(vscode.Uri.file(file))}"></audio>`,
              "</div>",
            ].join(""),
          ),
          "</div>",
        ].join("\n")
      : [
          '<p class="muted">No audio left for this speaker: they were named once and then',
          "forgotten, and the snippets went when the name did. What they said:</p>",
          '<ul class="lines">',
          ...speaker.lines.map((line) => `<li>${escapeHtml(line)}</li>`),
          "</ul>",
        ].join("\n");

    // A speaker with no stored embedding cannot be filed under any name, so it
    // gets an explanation instead of a field that is guaranteed to be refused.
    const form = speaker.has_embedding
      ? [
          `<form class="name-form" data-speaker="${id}">`,
          '<div class="chips">',
          ...known.map(
            (name) =>
              `<button type="button" class="chip" data-name="${escapeHtml(name)}">${escapeHtml(name)}</button>`,
          ),
          "</div>",
          '<div class="row">',
          '<input type="text" name="name" placeholder="Who was that?" autocomplete="off" />',
          "<button type=\"submit\">Name</button>",
          "</div>",
          '<p class="error" hidden></p>',
          "</form>",
        ].join("\n")
      : [
          '<p class="muted">No embedding was stored for this speaker, so there is nothing to',
          "file under a name. A <code>referat rerun</code> of this meeting is the way back.</p>",
        ].join("\n");

    return [
      `<section class="speaker" id="${id}">`,
      `<h2>${id}</h2>`,
      evidence,
      form,
      "</section>",
    ].join("\n");
  }

  dispose(): void {
    if (LabelPanel.current === this) {
      LabelPanel.current = undefined;
    }
    for (const d of this.disposables) {
      d.dispose();
    }
    this.disposables.length = 0;
    this.panel.dispose();
  }
}

const ESCAPES: Record<string, string> = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (c) => ESCAPES[c] ?? c);
}

function makeNonce(): string {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  let nonce = "";
  for (let i = 0; i < 32; i++) {
    nonce += alphabet.charAt(Math.floor(Math.random() * alphabet.length));
  }
  return nonce;
}
