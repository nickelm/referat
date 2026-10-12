"""Signing in to Google from the window, by the same paste flow the prompt uses.

Added on 2026-10-12, the day a stored token was rejected seven days after it
was issued and the only way to renew it was a terminal. Until then every
surface here said the command center *cannot authenticate*, and it could not:
:func:`referat.gdocs.consent` reads `input()`, and the tray runs under
`pythonw.exe`, where there is none.

**Nothing about the flow changed, only who stands in the middle of it.**
`gdocs.begin_consent` builds the URL, the browser shows Google's consent, the
redirect lands on a `localhost` nothing is listening on, and the address that
failed to load is pasted back for `gdocs.finish_consent` to exchange. At a
prompt the pasting is into `input()`; here it is into a line edit. No socket is
bound, which is the rule the paste flow exists to honour.

**Two things the old sentence was protecting still hold.** This dialog opens
when somebody presses a button and at no other time -- a sync that finds no
usable token still fails with a sentence, and never opens a browser from a
worker thread. And **the exchange runs on a thread**, because it is a network
round trip and this process owns the recorder; the result crosses back through
a queued signal, which is the rule this project paid three heap corruptions for.

**Not a modal.** Signing in means leaving for a browser for a minute, and a
modal would lock the command center -- including its Stop button -- for that
minute. The same reasoning as :mod:`referat.ui.recap`.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from referat import gdocs
from referat.config import Config
from referat.ui import icons

log = logging.getLogger(__name__)

STEP_ONE = (
    "1. Open Google in your browser and approve what it asks. If Google says it has "
    "not verified this app, choose Advanced and continue -- the app is yours."
)

STEP_TWO = (
    "2. The browser will then fail to load a page at localhost. That is expected: "
    "nothing is listening there. Copy the whole address out of the address bar and "
    "paste it here."
)

SIGNED_IN = (
    "Signed in. The token is saved at {path}. Sync now on a project pushes anything "
    "that did not reach its document while the old token was failing."
)


class SignInDialog(QDialog):
    """Open the consent, take the address back, store the token.

    Holds no rule: what a pasted address must contain and what Google's refusal
    is said as are :mod:`referat.gdocs`'s, and arrive here as the sentence it
    wrote.
    """

    exchanged = Signal(bool, str)
    """`(ok, message)` from the worker thread."""

    signed_in = Signal(str)
    """Emitted on the GUI thread with the message, once a token has been stored."""

    def __init__(self, config: Config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Google sign-in")
        self.setWindowIcon(icons.glyph("gdoc", icons.LINKED))
        self.setMinimumWidth(560)
        self.setModal(False)
        self._config = config
        self._flow: Any = None
        """The flow the open consent belongs to. A code is good for this one only."""
        self._url = ""
        self._busy = False

        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setTextFormat(Qt.TextFormat.PlainText)

        step_one = QLabel(STEP_ONE)
        step_one.setWordWrap(True)
        self.open_button = QPushButton("Open Google in your browser")
        self.open_button.setIcon(icons.glyph("gdoc", icons.LINKED))
        self.open_button.setAutoDefault(False)
        self.open_button.clicked.connect(self._on_open)
        self.copy_button = QPushButton("Copy link")
        self.copy_button.setToolTip("If the browser did not open, paste this link into one yourself.")
        self.copy_button.setAutoDefault(False)
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self._on_copy_link)

        step_two = QLabel(STEP_TWO)
        step_two.setWordWrap(True)
        self.pasted = QLineEdit()
        self.pasted.setPlaceholderText("http://localhost/?code=4/0Ab...&scope=...")
        self.pasted.setClearButtonEnabled(True)
        self.pasted.setEnabled(False)
        self.pasted.textChanged.connect(lambda _text: self._refresh())
        self.pasted.returnPressed.connect(self._on_save)

        self.save_button = QPushButton("Save sign-in")
        self.save_button.setAutoDefault(False)
        self.save_button.clicked.connect(self._on_save)
        self.close_button = QPushButton("Close")
        self.close_button.setAutoDefault(False)
        self.close_button.clicked.connect(self.close)

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)

        open_row = QHBoxLayout()
        open_row.addWidget(self.open_button)
        open_row.addWidget(self.copy_button)
        open_row.addStretch(1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.close_button)

        layout = QVBoxLayout()
        layout.addWidget(self.note)
        layout.addSpacing(8)
        layout.addWidget(step_one)
        layout.addLayout(open_row)
        layout.addSpacing(8)
        layout.addWidget(step_two)
        layout.addWidget(self.pasted)
        layout.addWidget(self.status)
        layout.addStretch(1)
        layout.addLayout(buttons)
        self.setLayout(layout)

        self.exchanged.connect(self._on_exchanged)
        self._refresh()

    def present(self) -> None:
        """Show and raise, starting over unless a consent is already under way."""
        if not self._busy and self._flow is None:
            self.note.setText(gdocs.token_note(self._config))
            self.status.clear()
            self.pasted.clear()
        self._refresh()
        self.show()
        self.raise_()
        self.activateWindow()

    # --- Step one: the browser ----------------------------------------------

    def _on_open(self) -> None:
        """Build the URL and hand it to the desktop. Local: no network on this thread."""
        try:
            self._flow, self._url = gdocs.begin_consent(self._config)
        except gdocs.GoogleError as exc:
            self._flow, self._url = None, ""
            self.status.setText(str(exc))
            self._refresh()
            return
        except Exception:  # noqa: BLE001 - a dialog may not take the process down
            log.exception("could not start a Google consent")
            self._flow, self._url = None, ""
            self.status.setText("Could not start the sign-in - see the log.")
            self._refresh()
            return
        if QDesktopServices.openUrl(QUrl(self._url)):
            self.status.setText("Waiting for the address from your browser.")
        else:
            self.status.setText(
                "The browser did not open. Press Copy link and paste it into one yourself."
            )
        self._refresh()
        self.pasted.setFocus()

    def _on_copy_link(self) -> None:
        if self._url:
            QGuiApplication.clipboard().setText(self._url)
            self.status.setText("The link is on the clipboard.")

    # --- Step two: the address back -----------------------------------------

    def _on_save(self) -> None:
        """Exchange the pasted address. On a thread, and the dialog says it is busy."""
        pasted = self.pasted.text().strip()
        if self._busy or self._flow is None or not pasted:
            return
        self._busy = True
        self.status.setText("Asking Google for the token...")
        self._refresh()
        threading.Thread(
            target=self._worker, args=(self._flow, pasted), name="consent", daemon=True
        ).start()

    def _worker(self, flow: Any, pasted: str) -> None:
        """Touches no widget. The result crosses back as a queued signal."""
        try:
            gdocs.finish_consent(self._config, flow, pasted)
        except gdocs.GoogleError as exc:
            self.exchanged.emit(False, str(exc))
            return
        except Exception:  # noqa: BLE001 - a dialog may not take the process down
            log.exception("could not finish the Google consent")
            self.exchanged.emit(False, "Could not save the sign-in - see the log.")
            return
        self.exchanged.emit(True, SIGNED_IN.format(path=self._config.google_token_file()))

    def _on_exchanged(self, ok: bool, message: str) -> None:
        self._busy = False
        if ok:
            self._flow, self._url = None, ""
            self.pasted.clear()
            self.note.setText(gdocs.token_note(self._config))
            self.status.setText(message)
            self.signed_in.emit(message)
        else:
            # The flow and the text are kept: the commonest refusal is a paste
            # that was not the address, which never reached Google and is fixed
            # by pasting again. A code Google itself refused is spent, and the
            # way past that is the button above, which is still live.
            self.status.setText(
                f"{message} Paste the address again, or open Google again for a fresh one."
            )
        self._refresh()

    def _refresh(self) -> None:
        opened = self._flow is not None
        self.open_button.setEnabled(not self._busy)
        self.copy_button.setEnabled(opened and not self._busy)
        self.pasted.setEnabled(opened and not self._busy)
        self.save_button.setEnabled(
            opened and not self._busy and bool(self.pasted.text().strip())
        )
