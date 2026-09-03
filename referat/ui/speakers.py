"""The labeling dialog: who each voice nobody has named belongs to.

Phase 3 of build step 20, and the second thing the command center writes. It
writes names and only names, through :func:`referat.label.name_speaker` — never
:func:`referat.label.apply_name` directly, because the reserved-name rule, the
meeting lookup, the refusal to rename and the dashboard regeneration around it
are what make naming *correct*, and putting them in a dialog would be the second
implementation the one-implementation rule exists to prevent. That is the same
sentence :mod:`referat.ui.tags` says about `cli.apply_tags` and
`projects.add_tags`. This module opens no file of its own.

**Naming is the most consequential thing either surface does**, and the dialog is
shaped around that rather than around convenience. A name here does not just fix
this transcript: it files a voiceprint that every meeting after this one is
matched against, so a wrong name spreads by itself and the way back — `referat
label --forget` — reverts that person's labels *everywhere*. So a chip fills the
field rather than applying itself, `SPEAKER_NN` is always a legitimate outcome,
and the panel says which channel a voice arrived on because that is the strongest
honest hint there is about who somebody is.

**The gallery is Python's and the order is this module's.** `label_document`
hands over `gallery.scoped` and `gallery.rest`, already decided by
:func:`referat.people.gallery`; what happens here is that the owner leads the
chips for a microphone speaker and the rest sit behind *Show all*. Which people
belong to a project is a join over three files and is not re-derived in a dialog;
which chip is first is presentation.

**Playback is refused while the recorder is running**, and that is not
tidiness. This is the tray's process, so the snippets play out of the same
speakers WASAPI is looping back into `system.wav`: a person's earlier speech
would be captured into the meeting being recorded, transcribed, diarized and
rendered as if the far end had said it. The transcript is evidence of what was
said, and this is the one way a UI could quietly write something into one.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from referat import cli
from referat import label as labelling
from referat.config import Config
from referat.state import State

log = logging.getLogger(__name__)

SPEAKER_ROLE = Qt.ItemDataRole.UserRole
"""The `SPEAKER_NN` a row stands for, so nothing parses it back out of a label."""

CHANNELS = {"mic": "your microphone", "system": "the call"}
"""How a channel is said to a person. Anything else is not said at all.

The same two phrases `referat label`'s prompt prints and the sidebar's card
shows, so three surfaces cannot describe one speaker differently.
"""

WARNING = (
    "Naming somebody here teaches that voice to every meeting after this one, so a "
    "wrong name spreads. Leave a speaker as a number if you are not sure."
)

NO_AUDIO = (
    "No audio left for this speaker: they were named once and then forgotten, and "
    "the snippets went when the name did. What they said:"
)

NO_EMBEDDING = (
    "No embedding was stored for this speaker, so there is nothing to file under a "
    "name. Re-transcribing this meeting is the way back."
)

RECORDING = "Not while a meeting is recording: the snippets would be captured into it."

UNTAGGED = (
    "This meeting carries no project, so every known name is offered. Tagging it "
    "first narrows the list to the people that project has met."
)
"""The nudge, and it is a nudge: labeling an untagged meeting is never refused.

Shown on `gallery.tags` being empty, which the document *says*, rather than on
`gallery.scoped` being short, which would be inferring a meeting's state from the
shape of an answer about something else.
"""


class SpeakerDialog(QDialog):
    """One meeting's unnamed speakers, one panel each, named one at a time."""

    def __init__(
        self, parent: QWidget | None, config: Config, document: dict[str, Any], app: Any
    ) -> None:
        super().__init__(parent)
        self.config = config
        self.app = app
        """The tray app, for one question only: is a meeting being recorded right now."""
        self.document = document
        self.meeting_id = str(document["meeting"])
        self.named = False
        """Whether anything was actually filed, which is what the window refreshes on."""
        self._who = _summaries(document.get("people") or [])
        """Name to a one-line *who is this already*, for the chip tooltips."""

        self.setWindowTitle(f"Speakers - {self.meeting_id}")
        self.resize(720, 520)

        self.speakers = QListWidget()
        self.speakers.setMaximumWidth(200)
        self.speakers.currentItemChanged.connect(self._on_row_changed)

        self.heading = QLabel()
        self.heading.setTextFormat(Qt.TextFormat.PlainText)
        self.play_button = QPushButton("Play snippets")
        self.play_button.clicked.connect(self._on_play)
        self.lines = QLabel()
        self.lines.setWordWrap(True)
        self.lines.setTextFormat(Qt.TextFormat.PlainText)
        self.lines.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.chips = QWidget()
        self.chip_row = QHBoxLayout(self.chips)
        self.chip_row.setContentsMargins(0, 0, 0, 0)
        self.show_all = QPushButton()
        self.show_all.setFlat(True)
        self.show_all.clicked.connect(self._on_show_all)
        self._showing_all = False

        self.field = QLineEdit()
        self.field.setPlaceholderText("Who was that?")
        self.field.returnPressed.connect(self._on_apply)
        self.apply_button = QPushButton("Name this speaker")
        self.apply_button.clicked.connect(self._on_apply)

        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.hide()

        warning = QLabel(WARNING)
        warning.setWordWrap(True)

        self.nudge = QLabel(UNTAGGED)
        self.nudge.setWordWrap(True)

        name_row = QHBoxLayout()
        name_row.addWidget(self.field, 1)
        name_row.addWidget(self.apply_button)

        detail = QVBoxLayout()
        detail.addWidget(self.heading)
        detail.addWidget(self.play_button)
        detail.addWidget(self.lines, 1)
        detail.addWidget(self.chips)
        detail.addWidget(self.show_all)
        detail.addLayout(name_row)
        detail.addWidget(self.message)
        self.detail = QWidget()
        self.detail.setLayout(detail)

        panes = QHBoxLayout()
        panes.addWidget(self.speakers)
        panes.addWidget(self.detail, 1)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.buttons.rejected.connect(self.reject)
        # Close must not be the default, or Enter in the name field would close
        # the dialog instead of naming somebody.
        for button in self.buttons.buttons():
            button.setAutoDefault(False)
        self.apply_button.setDefault(True)

        layout = QVBoxLayout()
        layout.addWidget(warning)
        layout.addWidget(self.nudge)
        layout.addLayout(panes, 1)
        layout.addWidget(self.buttons)
        self.setLayout(layout)

        self._fill()

    # --- The list of speakers ------------------------------------------------

    def _fill(self) -> None:
        """Rebuild the speaker list from :attr:`document`, selecting the first row.

        Called once at construction and again after every name is filed, because
        naming somebody removes them from `unknown_speakers` and the document has
        to be re-read to know that. Cheap: one `meta.json`, one voices database
        and one scan for the gallery.
        """
        self.speakers.clear()
        self.detail.setEnabled(True)
        self.nudge.setVisible(not self.document["gallery"]["tags"])
        for speaker in self.document["speakers"]:
            where = CHANNELS.get(speaker["channel"], "")
            label = speaker["speaker"]
            item = QListWidgetItem(f"{label}\n{where}" if where else label)
            item.setData(SPEAKER_ROLE, label)
            self.speakers.addItem(item)
        if self.speakers.count():
            self.speakers.setCurrentRow(0)
        else:
            self._show_nothing_left()

    def _show_nothing_left(self) -> None:
        """Every speaker in this meeting has a name. Say so and leave nothing to click."""
        self.heading.setText(f"Every speaker in {self.meeting_id} has a name.")
        self.lines.clear()
        self.detail.setEnabled(False)

    def _selected(self) -> dict[str, Any] | None:
        item = self.speakers.currentItem()
        if item is None:
            return None
        wanted = str(item.data(SPEAKER_ROLE))
        return next((s for s in self.document["speakers"] if s["speaker"] == wanted), None)

    def _on_row_changed(self, *_args: object) -> None:
        """Show the selected speaker, stopping whatever the previous one was playing."""
        labelling.stop_playback()
        self._clear_message()
        self._showing_all = False
        speaker = self._selected()
        if speaker is None:
            return

        where = CHANNELS.get(speaker["channel"], "")
        self.heading.setText(
            f"{speaker['speaker']} - from {where}" if where else speaker["speaker"]
        )
        clips = [Path(p) for p in speaker["snippets"]]
        self.play_button.setEnabled(bool(clips) and not self._recording())
        self.play_button.setText(
            f"Play {len(clips)} snippet(s)" if clips else "No snippets for this speaker"
        )
        self.play_button.setToolTip(RECORDING if self._recording() else "")
        if clips:
            self.lines.clear()
        else:
            # Named once and then forgotten: the snippets went when the name
            # arrived and reverting the label cannot bring them back. Three of
            # their lines is thin evidence and beats being unable to name them at
            # all — the embedding `meta.json` kept is still perfectly good.
            said = "\n".join(f"  {line}" for line in speaker["lines"])
            self.lines.setText(f"{NO_AUDIO}\n\n{said}" if said else NO_AUDIO)

        self.field.clear()
        self.field.setEnabled(speaker["has_embedding"])
        self.apply_button.setEnabled(speaker["has_embedding"])
        if not speaker["has_embedding"]:
            # A field here would be a field guaranteed to be refused.
            self._complain(NO_EMBEDDING)
        self._fill_chips(speaker)
        if speaker["has_embedding"]:
            self.field.setFocus()

    # --- The gallery ---------------------------------------------------------

    def _ordered(self, speaker: dict[str, Any]) -> list[str]:
        """The names to offer, in the order to offer them.

        The scoping is :func:`referat.people.gallery`'s and arrives decided; the
        only thing decided here is that the owner leads for a **microphone**
        speaker, because that is the one guess this dialog can make honestly — a
        voice on the mic that nothing recognised is often the owner's own second
        cluster, which is what `2026-09-01_2102` was. A hint and never a name
        applied on its own: the microphone hears the whole room in a meeting held
        in person, so it narrows the guess rather than settling it.
        """
        gallery = self.document["gallery"]
        names = list(gallery["scoped"])
        if self._showing_all or not names:
            # An untagged meeting has nothing to scope on, and the full gallery is
            # what it gets. A missing tag must never cost a name.
            names = [*names, *(n for n in gallery["rest"] if n not in names)]
        owner = self.document["owner"]
        if owner and speaker["channel"] == "mic" and owner in names:
            names.remove(owner)
            names.insert(0, owner)
        return names

    def _fill_chips(self, speaker: dict[str, Any]) -> None:
        """One button per offered name, and the *Show all* that reveals the rest.

        A chip **fills the field rather than applying itself.** Two keystrokes
        instead of one, deliberately: a name teaches every meeting after this one
        a voice, and the click that starts it should not also be the click that
        commits it.
        """
        while (item := self.chip_row.takeAt(0)) is not None:
            if (widget := item.widget()) is not None:
                widget.deleteLater()

        owner = self.document["owner"]
        for name in self._ordered(speaker):
            chip = QPushButton(name)
            chip.setAutoDefault(False)
            if name == owner and speaker["channel"] == "mic":
                chip.setToolTip("You. This voice came out of your own microphone.")
            elif summary := self._who.get(name):
                # A tooltip and deliberately **not** a link to the people page.
                # A chip's click is committed to filling the field, and this
                # dialog is modal over that page anyway — the question somebody
                # actually has while naming is *who is this already*, which a
                # tooltip answers without moving them somewhere else.
                chip.setToolTip(summary)
            chip.clicked.connect(lambda _checked=False, n=name: self._pick(n))
            chip.setEnabled(speaker["has_embedding"])
            self.chip_row.addWidget(chip)
        self.chip_row.addStretch(1)

        hidden = len(self.document["gallery"]["rest"])
        scoped = bool(self.document["gallery"]["scoped"])
        # The toggle exists only when there is a scoped list to be narrower
        # *than*: with no tags every name is already on screen, and a button
        # offering to show what is shown would be a lie about the meeting.
        self.show_all.setVisible(scoped and bool(hidden) and not self._showing_all)
        self.show_all.setText(f"Show all {hidden} other name(s)")

    def _on_show_all(self) -> None:
        self._showing_all = True
        if (speaker := self._selected()) is not None:
            self._fill_chips(speaker)

    def _pick(self, name: str) -> None:
        self.field.setText(name)
        self.field.setFocus()

    # --- Playback ------------------------------------------------------------

    def _recording(self) -> bool:
        """Whether the recorder is capturing right now, `paused` included.

        A paused meeting is still being recorded — that is the whole reason the
        recorder's state machine has a `paused` and `meta.json`'s lifecycle does
        not — and the loopback stream is still open, so this refuses both.
        """
        return self.app.machine.state in (State.RECORDING, State.PAUSED)

    def _on_play(self) -> None:
        """Play the selected speaker's snippets, unless a meeting is being recorded."""
        speaker = self._selected()
        if speaker is None:
            return
        if self._recording():
            # Belt and braces: the button is already disabled, and a recording can
            # start while this dialog is open.
            self._complain(RECORDING)
            return
        complaint = labelling.play_async([Path(p) for p in speaker["snippets"]])
        if complaint:
            self._complain(complaint)

    # --- Naming --------------------------------------------------------------

    def _on_apply(self) -> None:
        """File the typed name, then re-read the document and move on.

        Every rule is `label.name_speaker`'s and its refusal is shown unedited,
        which is the in-process form of what `cli.ts`'s `mutate` gives the
        extension. The dialog stays open on one: the ticks of a tag picker have
        their counterpart here in a typed name, and closing over the top of a
        complaint would lose both the name and the reason.
        """
        speaker = self._selected()
        if speaker is None:
            return
        name = self.field.text().strip()
        if not name:
            return
        # Disabled across the call so a double-click cannot file the same
        # voiceprint twice.
        self.apply_button.setEnabled(False)
        try:
            named, message = labelling.name_speaker(
                self.config, self.meeting_id, speaker["speaker"], name
            )
        finally:
            self.apply_button.setEnabled(True)
        if not named:
            self._complain(message)
            return

        log.info("%s", message)
        self.named = True
        labelling.stop_playback()
        self._reload()
        # After the reload, or `_fill` selecting the next speaker would clear it.
        # Naming somebody has no other visible trace in here: the row simply goes.
        self._report(message)

    def _reload(self) -> None:
        """Re-read the document after a name and redraw. Keeps the dialog open.

        Naming one speaker changes three things a still-open dialog would
        otherwise be wrong about: that speaker is gone from `unknown_speakers`,
        the new name is in the gallery, and the snippets it just deleted are no
        longer on disk. Re-reading is one `meta.json` and one database.
        """
        from referat.meeting import resolve_meeting

        meeting, why = resolve_meeting(self.config, self.meeting_id)
        if meeting is None:
            self._complain(why)
            return
        try:
            self.document = labelling.label_document(self.config, meeting)
        except Exception:
            log.exception("could not re-read the speakers of %s", self.meeting_id)
            self._complain(f"Could not re-read {self.meeting_id}.")
            return
        self._fill()

    # --- Messages and closing -------------------------------------------------

    def _complain(self, message: str) -> None:
        """Show a refusal in the words of whoever owns the rule, unedited."""
        self.message.setText(message)
        self.message.show()

    def _report(self, message: str) -> None:
        """Say what just happened, in the same place and the same words.

        One line for both outcomes, because both are `name_speaker`'s own
        sentence and there is nothing this dialog could add to either. It is the
        window's status bar that carries the recounted totals, exactly as it is
        after a tag.
        """
        self._complain(message)

    def _clear_message(self) -> None:
        self.message.clear()
        self.message.hide()

    def done(self, result: int) -> None:
        """Stop any playback on the way out, whichever way the dialog is closed.

        Snippets play on PortAudio's own callback thread, so a dialog that simply
        vanished would leave somebody's voice coming out of the speakers with
        nothing on screen to stop it.
        """
        labelling.stop_playback()
        super().done(result)


def _summaries(entries: list[dict[str, Any]]) -> dict[str, str]:
    """A one-line *who is this already* per name, for the chip tooltips.

    Phase 5's answer to "clicking a name in a speaker chip opens that page": it
    does not, and this is what it does instead. See :meth:`_fill_chips`.
    """
    return {
        person["name"]: (
            f"{person['prints']} voiceprint(s), {len(person['appears_in'])} meeting(s), "
            f"{len(person['tags'])} project(s)"
        )
        for person in entries
    }


def open_for(parent: QWidget, config: Config, meeting_id: str, app: Any) -> bool:
    """Run the dialog over one meeting. True when a name was filed.

    Resolving the meeting and building the document happen here rather than in
    the dialog, so the two ways this can be empty — no such meeting, and nothing
    left to name — are answered before a window is put on screen.

    The people document is read separately and folded in, rather than widening
    `label_document`: what a chip's tooltip says is presentation, and
    `referat label <id> --json` has no use for it.
    """
    from referat.meeting import resolve_meeting

    meeting, why = resolve_meeting(config, meeting_id)
    if meeting is None:
        log.warning("%s", why)
        return False
    document = labelling.label_document(config, meeting)
    try:
        document["people"] = cli.people_document(config)["people"]
    except Exception:
        # A tooltip is worth nothing beside a name being filed, so this never
        # costs the dialog — the same shape as identification never costing a
        # transcript.
        log.warning("could not read the people for the chip tooltips", exc_info=True)
    dialog = SpeakerDialog(parent, config, document, app)
    dialog.exec()
    return dialog.named
