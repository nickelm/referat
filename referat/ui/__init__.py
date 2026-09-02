"""The command center: the desktop window the tray app owns and opens.

The one sub-package in an otherwise flat `referat/`, and a deliberate exception
taken at build step 20 rather than drift: a GUI is a dozen modules and
flattening it would make the package unreadable. Nothing else grows one.

**This layer stays OS-portable.** No Win32 call, no `ctypes`, no Windows-only Qt
API in any module here. That is a code-layer discipline and *not* a shipping
target — WASAPI, the sleep hold and the `keyboard` hooks say Referat runs on
Windows and nowhere else — and its point is that the UI never becomes the reason
a port is impossible.

**It imports; it does not shell out.** This is already a Python process inside
this package, exactly as the tray is, so it calls the same functions
:mod:`referat.cli` calls — `list_document`, `show_document`,
`transcript_document`, `status_document` — and spawning a subprocess of Referat's
own CLI would buy nothing but an interpreter start per refresh. The rule is one
implementation, not one process boundary. **No module here may read `meta.json`,
`voices.json` or `projects.json` itself**, and the day one does is the day this
stops being one implementation.

**Nothing here may cost a recording.** The recorder, the hotkeys, the state
machine and `status.json` are up before :mod:`referat.ui.shell` is imported, and
a window that fails to open is a log line and a tray that still records — the
same degradation diarization runs under. See :func:`referat.tray.main`.
"""

from __future__ import annotations
