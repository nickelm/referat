/**
 * The tag picker and project CRUD — native QuickPicks, driven from the sidebar.
 *
 * **Nothing here decides anything.** What a project name may be is
 * `projects.name_complaint`; what a slug becomes is `projects.slugify`; whether
 * an id exists is `ProjectsDB`; and what deleting one does to the meetings
 * carrying it — nothing, deliberately, leaving visible orphans — is
 * `referat project rm`. Every path below is a shell-out, and every refusal is
 * shown in the words the CLI used.
 *
 * These are QuickPicks rather than HTML in the sidebar because VS Code already
 * has a multi-select list with a filter box in it, and a webview copy would be a
 * worse one that also had to be maintained.
 */
import * as vscode from "vscode";
import {
  MeetingJson,
  ProjectJson,
  addProject,
  listProjects,
  mutate,
  report,
  tag,
  untag,
} from "./cli";

/**
 * Edit one meeting's tags.
 *
 * Pre-checked with what the meeting already carries, so the picker shows the
 * current state and applying it is a diff: a `tag` for what was added and an
 * `untag` for what was taken off. Both CLI verbs are idempotent, so the two
 * calls are safe in either direction.
 *
 * The last entry is **"Create project '<typed>'"**, driven by `onDidChangeValue`
 * — the common case is tagging a meeting with work that has no project yet, and
 * making somebody leave the picker to create one first is how a meeting stays
 * untagged.
 *
 * **The project list is read fresh, not taken from the sidebar's cache.** It used
 * to come from the listing the sidebar already held, to save a subprocess. That
 * cache goes stale the moment anything creates a project without the sidebar
 * refreshing afterwards — which this function itself did, on every path that
 * returned early — and the symptom is a picker offering one of your two
 * projects. A third of a second at the moment somebody opens a picker is worth
 * less than a picker that cannot be trusted; `projects` stays as the fallback for
 * when the call fails, since a stale list still beats no picker.
 *
 * An **orphan** — an id the meeting carries that no project answers to — is
 * offered as a checked entry too, marked as one, so it can be unchecked. It is
 * otherwise the one tag that cannot be removed here.
 */
export async function editTags(
  meeting: MeetingJson,
  projects: Record<string, string>,
  onChanged: () => void,
): Promise<void> {
  // Two sets, and keeping them apart is the whole of one bug. `carried` is what
  // the meeting has on it now and never changes; `checked` is the picker's tick
  // state, which starts as a copy and **grows when a project is created inside
  // the picker**, so the new project comes back checked. Diffing against the set
  // the picker mutates made a just-created project look like a tag the meeting
  // already had, so it landed in neither `added` nor `removed` and `referat tag`
  // was never called for it - "the project was created but the meeting could not
  // be tagged with it", exactly.
  const carried = new Set(meeting.tags);
  const checked = new Set(carried);
  const known = new Map(Object.entries(await freshProjects(projects)));
  for (const id of carried) {
    if (!known.has(id)) {
      known.set(id, `${id} (orphan — no project answers to this id)`);
    }
  }

  // Whether the picker created a project is a separate question from whether it
  // changed the meeting's tags, and the sidebar has to be told either way: a
  // project created here and then not applied is still a project the next picker
  // and the next set of group headings have to know about. Every return below
  // therefore goes through `done`.
  let created = false;
  const picked = await pickProjects(known, checked, meeting.id, () => {
    created = true;
  });
  const done = () => {
    if (created) {
      onChanged();
    }
  };

  if (picked === undefined) {
    return done();
  }

  const added = [...picked].filter((id) => !carried.has(id));
  const removed = [...carried].filter((id) => !picked.has(id));
  if (!added.length && !removed.length) {
    return done();
  }

  for (const [ids, verb] of [
    [added, tag],
    [removed, untag],
  ] as const) {
    if (!ids.length) {
      continue;
    }
    const complaint = await verb(meeting.id, ids);
    if (complaint) {
      void vscode.window.showErrorMessage(complaint);
      break;
    }
  }
  onChanged();
}

/** The projects as they are right now, falling back to whatever the caller had. */
async function freshProjects(fallback: Record<string, string>): Promise<Record<string, string>> {
  try {
    return (await listProjects()).names;
  } catch (err) {
    report(err);
    return fallback;
  }
}

/**
 * The multi-select itself, plus the create-as-you-type entry.
 *
 * Written out rather than using `showQuickPick`, because that helper cannot add
 * an item in response to what has been typed. Returns the chosen ids, or
 * undefined when the picker was dismissed — which is not the same as choosing
 * nothing, and must not untag the meeting.
 *
 * **Creating a project closes this picker and opens a new one.** The first
 * version created the project and then re-rendered in place, and that is where
 * "it was created but could then not be used to tag the meeting" came from:
 * assigning `picker.items` makes VS Code recompute which rows are checked, so
 * setting `selectedItems` on the next line is a race against that
 * recomputation — one the newly created project loses about as often as it wins,
 * which is exactly the "did not reliably" this was reported as. Nothing here can
 * make an in-place update deterministic. A fresh picker built from the updated
 * `known` and `current` has no selection to preserve and therefore no race, and
 * it costs one repaint.
 *
 * `onCreated` fires whenever a project was actually created, so the caller can
 * refresh even when the tags end up unchanged.
 */
function pickProjects(
  known: Map<string, string>,
  checked: Set<string>,
  meetingId: string,
  onCreated: () => void,
): Promise<Set<string> | undefined> {
  return new Promise((resolve) => {
    const picker = vscode.window.createQuickPick<vscode.QuickPickItem & { id?: string }>();
    picker.title = `Tags for ${meetingId}`;
    picker.placeholder = "Pick the projects this meeting belongs to, or type a new name";
    picker.canSelectMany = true;
    picker.matchOnDescription = true;

    type Item = vscode.QuickPickItem & { id?: string };
    const items = (): Item[] => {
      const base: Item[] = [...known].map(([id, name]) => ({ id, label: name, description: id }));
      const typed = picker.value.trim();
      if (typed && ![...known.values()].some((name) => name.toLowerCase() === typed.toLowerCase())) {
        base.push({ id: undefined, label: `Create project "${typed}"`, description: "" });
      }
      return base;
    };

    const render = () => {
      // Re-assigning items drops the selection, so it is restored from the set
      // this picker is editing rather than from what VS Code happens to hold.
      // Reliable only because `checked` cannot change while this picker is open:
      // the one thing that would change it — creating a project — reopens instead.
      picker.items = items();
      picker.selectedItems = picker.items.filter((item) => item.id && checked.has(item.id));
    };
    render();

    picker.onDidChangeValue(render);

    // Set while handing off to a replacement picker, so the hide on the way out
    // does not resolve this promise with "dismissed".
    let reopening = false;

    picker.onDidAccept(async () => {
      const create = picker.selectedItems.find((item) => !item.id);
      if (create) {
        // Creating is a detour, not an answer: make the project, then come back
        // with it checked, so one accept does not silently mean both "make this"
        // and "and that is the whole tag list".
        reopening = true;
        picker.hide();
        const project = await createProject(picker.value.trim());
        if (project) {
          // The stored name, not the typed string: Python collapses whitespace,
          // and a label here that disagreed with `project list` would be the
          // extension having its own opinion about somebody else's field.
          known.set(project.id, project.name);
          checked.add(project.id);
          onCreated();
        }
        resolve(await pickProjects(known, checked, meetingId, onCreated));
        return;
      }
      const chosen = new Set(
        picker.selectedItems.map((item) => item.id).filter((id): id is string => Boolean(id)),
      );
      picker.hide();
      resolve(chosen);
    });

    picker.onDidHide(() => {
      picker.dispose();
      if (!reopening) {
        resolve(undefined);
      }
    });
    picker.show();
  });
}

/**
 * `referat project add --json`, returning the project the CLI made, or undefined.
 *
 * The id comes back **from the command that made it**. It used to be looked up
 * afterwards in `project list` by display name, which is a guess rather than an
 * answer: two projects are allowed to share a name, and `find` then hands back
 * the older one's id. `projects.slugify` plus a `-2` collision suffix is the only
 * thing that knows which project was just created, and now it says so.
 */
async function createProject(name: string): Promise<ProjectJson | undefined> {
  try {
    return await addProject(name);
  } catch (err) {
    report(err);
    return undefined;
  }
}

/**
 * The *Projects…* command: create, rename or delete.
 *
 * **Attaching and detaching Google Docs is deliberately absent.** `referat
 * project link-doc`, `unlink-doc` and `sync` do not exist in the parser yet —
 * build step 14 left them out on purpose, on the reasoning that a verb which
 * exists and answers "not built yet" reads as a bug — so there is nothing here
 * to shell out to until step 13 builds them. That is the one bullet of step 15
 * that cannot be built now, and it is recorded in `TODO.md` under step 13.
 */
export async function manageProjects(onChanged: () => void): Promise<void> {
  let listing;
  try {
    listing = await listProjects();
  } catch (err) {
    report(err);
    return;
  }

  const orphans = Object.entries(listing.orphans);
  const items: (vscode.QuickPickItem & { action: string; id?: string })[] = [
    { label: "$(add) New project…", action: "add" },
    ...listing.projects.map((project) => ({
      label: project.name,
      description: project.id,
      detail: `${project.meetings} meeting${project.meetings === 1 ? "" : "s"}`,
      action: "edit",
      id: project.id,
    })),
  ];
  if (orphans.length) {
    items.push({
      label: "$(warning) Orphaned tags",
      description: orphans.map(([id, n]) => `${id} (${n})`).join(", "),
      detail: "Ids on meetings that no project answers to. Remove them from a meeting's Tags…",
      action: "none",
    });
  }

  const picked = await vscode.window.showQuickPick(items, {
    title: "Referat projects",
    placeHolder: "A project is a label a meeting carries, not a folder it sits in",
  });
  if (!picked || picked.action === "none") {
    return;
  }

  if (picked.action === "add") {
    const name = await vscode.window.showInputBox({
      title: "New project",
      prompt: "The display name. Its id is slugged from this once and never changes.",
    });
    if (name?.trim()) {
      await createProject(name);
      onChanged();
    }
    return;
  }

  await editProject(picked.id ?? "", picked.label, onChanged);
}

async function editProject(id: string, name: string, onChanged: () => void): Promise<void> {
  const choice = await vscode.window.showQuickPick(
    [
      { label: "$(edit) Rename…", action: "rename" },
      { label: "$(trash) Delete", action: "rm" },
    ],
    { title: `${name} (${id})` },
  );
  if (!choice) {
    return;
  }

  if (choice.action === "rename") {
    const next = await vscode.window.showInputBox({
      title: `Rename ${id}`,
      value: name,
      // Said here because the id not moving is the surprising half, and it is
      // the half every meeting record depends on.
      prompt: "Changes the display name only. The id stays as it is, so no meeting record moves.",
    });
    if (next?.trim()) {
      const complaint = await mutate(["project", "rename", id, next]);
      if (complaint) {
        void vscode.window.showErrorMessage(complaint);
      }
      onChanged();
    }
    return;
  }

  const confirmed = await vscode.window.showWarningMessage(
    `Delete the project ${name}?`,
    {
      modal: true,
      detail:
        `Meetings tagged ${id} keep carrying that id. It will resolve to nothing and be ` +
        "shown as an orphan until it is removed from each meeting — deleting a project " +
        "cascades to no meeting, no note and no document.",
    },
    "Delete",
  );
  if (confirmed !== "Delete") {
    return;
  }
  const complaint = await mutate(["project", "rm", id]);
  if (complaint) {
    void vscode.window.showErrorMessage(complaint);
  }
  onChanged();
}
