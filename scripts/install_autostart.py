r"""Create the shortcuts that launch the Referat tray -- at sign-in, and by hand.

    .venv\Scripts\python.exe scripts\install_autostart.py              # autostart
    .venv\Scripts\python.exe scripts\install_autostart.py --start-menu # Start menu
    .venv\Scripts\python.exe scripts\install_autostart.py --status
    .venv\Scripts\python.exe scripts\install_autostart.py --uninstall

Both shortcuts target this checkout's `.venv\Scripts\pythonw.exe` with
`-m referat.tray`, so the tray comes up with no console window.

**Two locations, one shortcut.** Writing a `.lnk` into the Startup folder and
writing the same `.lnk` into `Start Menu\Programs` differ only in which known
folder they name: the target, the verification read-back and the refusal to
overwrite somebody else's shortcut are the same operation both times. So
`--start-menu` selects a `Location` rather than reaching a second script, and
the two cannot drift apart in what they install. The Start menu entry is the
answer to "the tray is not running and I do not want to open a terminal" --
searchable by name, and pinnable to the taskbar from there.

Installing both is the expected state and they do not conflict: `tray.py` holds
a single-instance mutex, so launching from the Start menu while an autostarted
tray is already running hands the hotkeys to nobody new.

Three choices worth writing down, because each of them has an obvious
alternative that is wrong:

**`pythonw.exe -m referat.tray`, not `referat-tray.exe`.** The gui-script is a
generated launcher that `CreateProcess`es the interpreter and stays resident as
the parent, and it is an artifact a reinstall regenerates -- Dropbox has deleted
it twice here. `pythonw.exe` is the stable name. `tray.py` names its logger
explicitly so the two paths log identically, and the `.lnk` properties dialog
then says what it runs.

This choice used to be argued on the grounds that `.venv\Scripts\pythonw.exe`
"is the venv itself" and so costs no stub process. **That is no longer true and
was only ever true of uv's venv.** Since the venv was rebuilt with the stdlib
`venv` module -- see SETUP.md section 2 on why the interpreter had to change --
`.venv\Scripts\pythonw.exe` is a *copy of CPython's own venv redirector*
(`Lib\venv\scripts\nt\pythonw.exe`, 263 kB against the real interpreter's
104 kB), which spawns the base `pythonw.exe` and waits on it so exit codes and
Ctrl+C propagate. So the tray is two processes: a ~6 MB stub and the ~46 MB
interpreter doing the work. `--copies` and `--symlinks` do not change this, and
pointing the shortcut at the base interpreter instead would miss the venv's
`site-packages` entirely. The stub is accepted rather than avoided. Nothing
depends on the process count -- `tray.py` writes its *own* pid into
`status.json`, so `referat status` reports the interpreter, not the stub.

**PowerShell driving `WScript.Shell.CreateShortcut`, not COM through ctypes.**
ctypes is right for a single flat function with scalar arguments -- which is
exactly what `SHGetKnownFolderPath` below is, alongside `SetThreadExecutionState`
in `power.py`, `CreateMutexW` in `tray.py` and `OpenProcess` in `status.py` --
and wrong for navigating a COM interface. `IShellLinkW` plus `IPersistFile`
would be a hundred lines of hand-declared GUID structs and vtable-index
arithmetic in which a wrong index is memory corruption rather than an exception.
Adding pywin32 was the third option and would put ten megabytes of Win32
bindings into the *base* install -- the one deliberately kept to tray and audio
-- to write one two-kilobyte file, once.

**`SHGetKnownFolderPath(FOLDERID_Startup)`, not the `%APPDATA%` join.** The
literal path is an assumption: folder redirection moves the Startup folder, and
`%APPDATA%` can be redirected on its own. The join stays as a fallback for a bad
HRESULT and nothing else.

One thing this cannot do: a shortcut cannot set an environment variable. So
`REFERAT_CONFIG` reaches the autostarted tray only when it is persisted in the
user environment, and this script says which config file that tray will actually
load rather than leaving it to be discovered after a meeting.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import winreg
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

# Put the repository root on the path before importing `referat`. Running a
# script puts the *script's* directory on `sys.path`, not the working directory,
# so `scripts/install_autostart.py` cannot see the package beside it -- and the
# editable install is not something to rely on either: this venv lives inside a
# Dropbox folder, and its `referat-0.1.0.dist-info` has already been found
# emptied once. The script knows where it is; that is the more reliable answer.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from referat import paths
except ImportError as exc:  # pragma: no cover - the one mistake worth catching
    raise SystemExit(
        f"install_autostart: could not import referat ({exc}).\n"
        "Run it inside the project environment:\n"
        "    .venv\\Scripts\\python.exe scripts\\install_autostart.py"
    )

SHORTCUT_NAME = "Referat.lnk"
DESCRIPTION = "Referat - meeting recorder tray"
MODULE_ARGS = "-m referat.tray"
FOLDERID_STARTUP = "{B97D20BB-F46A-4C97-BA10-5E3608430854}"
FOLDERID_PROGRAMS = "{A77F5D77-2E2B-44C3-A6A2-ABA601054A51}"


class InstallError(Exception):
    """The shell would not write or read the shortcut."""


@dataclass(frozen=True)
class Shortcut:
    """The four `.lnk` fields Referat sets -- and reads back to verify the save."""

    target: Path
    arguments: str
    working_dir: Path
    description: str = DESCRIPTION

    def matches(self, other: Shortcut) -> bool:
        """True when `other` is the same shortcut, comparing paths case-insensitively.

        Windows paths are case-insensitive and the shell normalises `TargetPath`
        on save, so `normcase` is the only honest comparison. Arguments are
        compared exactly: `-m referat.tray` is a command line, not a path.
        """
        return (
            os.path.normcase(str(self.target)) == os.path.normcase(str(other.target))
            and self.arguments == other.arguments
            and os.path.normcase(str(self.working_dir)) == os.path.normcase(str(other.working_dir))
        )

    def lines(self) -> list[str]:
        """The indented block this script prints to describe a shortcut."""
        return [
            f"  target:  {self.target}",
            f"  args:    {self.arguments}",
            f"  workdir: {self.working_dir}",
        ]


# --- Windows ----------------------------------------------------------------


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _guid(text: str) -> _GUID:
    """Parse a `{XXXXXXXX-XXXX-...}` string into a GUID structure."""
    guid = _GUID()
    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    ole32.CLSIDFromString.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(_GUID)]
    if ole32.CLSIDFromString(text, ctypes.byref(guid)) != 0:
        raise InstallError(f"could not parse GUID {text}")
    return guid


def _appdata_programs_dir() -> Path:
    r"""`Start Menu\Programs` as it is on an unredirected machine. Fallback only."""
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def _appdata_startup_dir() -> Path:
    """The Startup folder as it is on an unredirected machine. Fallback only."""
    return _appdata_programs_dir() / "Startup"


def _known_folder(folder_id: str, fallback: Callable[[], Path]) -> Path:
    r"""A known folder from `SHGetKnownFolderPath`, or `fallback()`.

    Falls back to an `%APPDATA%` join only when the call fails, because that
    path is an assumption: Group Policy folder redirection moves these folders,
    and `%APPDATA%` can be redirected on its own.
    """
    try:
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        ole32 = ctypes.WinDLL("ole32", use_last_error=True)
        shell32.SHGetKnownFolderPath.argtypes = [
            ctypes.POINTER(_GUID),
            wintypes.DWORD,
            wintypes.HANDLE,
            ctypes.POINTER(ctypes.c_wchar_p),
        ]
        buffer = ctypes.c_wchar_p()
        result = shell32.SHGetKnownFolderPath(
            ctypes.byref(_guid(folder_id)), 0, None, ctypes.byref(buffer)
        )
        if result != 0 or not buffer.value:
            return fallback()
        try:
            return Path(buffer.value)
        finally:
            ole32.CoTaskMemFree(buffer)
    except (OSError, InstallError):
        return fallback()


@dataclass(frozen=True)
class Location:
    """Where a shortcut goes, and what installing it there means.

    The two locations differ in the known folder they name and in the sentence
    printed afterwards; everything else -- the target, the read-back
    verification, the refusal to overwrite a shortcut Referat did not write --
    is the same operation, and is written once.
    """

    flag: str
    label: str
    folder_id: str
    fallback: Callable[[], Path]
    installed_note: tuple[str, ...]
    removed_note: str

    def dir(self) -> Path:
        return _known_folder(self.folder_id, self.fallback)

    def path(self) -> Path:
        return self.dir() / SHORTCUT_NAME


AUTOSTART = Location(
    flag="",
    label="Startup folder",
    folder_id=FOLDERID_STARTUP,
    fallback=_appdata_startup_dir,
    installed_note=("The tray starts at your next sign-in.",),
    removed_note="The tray no longer starts at sign-in.",
)

START_MENU = Location(
    flag="--start-menu",
    label="Start menu",
    folder_id=FOLDERID_PROGRAMS,
    fallback=_appdata_programs_dir,
    installed_note=(
        'Press Start and type "Referat" to launch the tray. Right-click the',
        "result to pin it to the taskbar or to Start.",
    ),
    removed_note="Referat is no longer in the Start menu.",
)


def startup_dir() -> Path:
    """The user's Startup folder. Kept as a name because it reads better."""
    return AUTOSTART.dir()


def shortcut_path() -> Path:
    """Where the autostart shortcut lives."""
    return AUTOSTART.path()


# --- Talking to the shell ----------------------------------------------------


def _powershell_exe() -> Path:
    r"""`%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe`, absolutely.

    Not off PATH. This repository has been bitten by PATH twice already, and the
    installer is the one thing that has to work on a machine nobody has set up
    yet.
    """
    root = os.environ.get("SystemRoot") or r"C:\Windows"
    return Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"


def _powershell(script: str) -> str:
    """Run `script` through PowerShell and return its stdout.

    `-NoProfile` because a user profile may print banners into stdout, and
    `CREATE_NO_WINDOW` because this may be run from a context with no console of
    its own. `-ExecutionPolicy` is deliberately absent: policy governs script
    files, not `-Command`.
    """
    exe = _powershell_exe()
    if not exe.exists():
        raise InstallError(f"PowerShell not found at {exe}")
    try:
        completed = subprocess.run(
            [str(exe), "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except OSError as exc:
        raise InstallError(f"could not run PowerShell: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise InstallError(f"PowerShell failed: {detail or completed.returncode}")
    return completed.stdout


def _ps_literal(value: str) -> str:
    """`value` as a PowerShell single-quoted literal, with `'` doubled.

    Backslashes and the space in this repository's own path are literal inside
    one, which is the whole reason nothing here is double-quoted.
    """
    return "'" + value.replace("'", "''") + "'"


def write_shortcut(path: Path, link: Shortcut) -> None:
    """Create or overwrite the `.lnk` at `path`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _powershell(
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut("
        f"{_ps_literal(str(path))})\n"
        f"$s.TargetPath = {_ps_literal(str(link.target))}\n"
        f"$s.Arguments = {_ps_literal(link.arguments)}\n"
        f"$s.WorkingDirectory = {_ps_literal(str(link.working_dir))}\n"
        f"$s.Description = {_ps_literal(link.description)}\n"
        "$s.Save()"
    )


def read_shortcut(path: Path) -> Shortcut | None:
    """The shortcut at `path`, or None when there is none.

    The existence check is not an optimisation. `CreateShortcut` on a path that
    does not exist returns a *blank* object rather than failing, so without it a
    missing shortcut would read as an installed-but-wrong one.
    """
    if not path.exists():
        return None
    raw = _powershell(
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n"
        f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({_ps_literal(str(path))})\n"
        "[pscustomobject]@{ target = $s.TargetPath; arguments = $s.Arguments; "
        "workdir = $s.WorkingDirectory; description = $s.Description } | "
        "ConvertTo-Json -Compress"
    )
    try:
        fields = json.loads(raw)
    except ValueError as exc:
        raise InstallError(f"could not read {path}: {raw.strip() or exc}") from exc
    return Shortcut(
        target=Path(fields.get("target") or ""),
        arguments=fields.get("arguments") or "",
        working_dir=Path(fields.get("workdir") or ""),
        description=fields.get("description") or "",
    )


# --- What we would install ---------------------------------------------------


def venv_scripts_dir() -> Path:
    r"""The `Scripts` directory of the environment this script is running in.

    `sys.executable` already answers this; deriving `.venv` from the repo root
    would be a second answer to the same question, and the two would disagree
    the first time somebody used a differently-named environment. The repo is
    cross-checked below instead.
    """
    return Path(sys.executable).resolve().parent


def intended() -> Shortcut:
    """The shortcut this checkout would install."""
    pythonw = venv_scripts_dir() / "pythonw.exe"
    if not pythonw.exists():
        raise InstallError(
            f"no pythonw.exe beside {sys.executable}.\n"
            "Run it inside the project environment:\n"
            "    .venv\\Scripts\\python.exe scripts\\install_autostart.py"
        )
    here = Path(__file__).resolve().parent.parent
    if os.path.normcase(str(here)) != os.path.normcase(str(paths.REPO_ROOT)):
        raise InstallError(
            "this script and the imported referat package are in different "
            f"checkouts:\n  script:  {here}\n  package: {paths.REPO_ROOT}"
        )
    return Shortcut(target=pythonw, arguments=MODULE_ARGS, working_dir=paths.REPO_ROOT)


def persisted_config_override() -> str | None:
    r"""`REFERAT_CONFIG` from `HKCU\Environment`, or None.

    A value set only in the installing shell is not inherited by anything
    Explorer starts at sign-in, and reading the registry is the only way to tell
    the two cases apart.
    """
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, paths.CONFIG_ENV_VAR)
    except OSError:
        return None
    text = str(value).strip()
    return text or None


def effective_config() -> Path:
    """The config file the autostarted tray will load.

    `paths.config_path()` reads this process's environment, which is not the one
    the tray will get, so the persisted value is what decides it.
    """
    persisted = persisted_config_override()
    if persisted:
        return Path(persisted).expanduser().resolve()
    return paths.REPO_ROOT / "config.toml"


def _config_warning() -> list[str]:
    """The warning for a `REFERAT_CONFIG` that is set in this shell only."""
    shell = os.environ.get(paths.CONFIG_ENV_VAR)
    if not shell or persisted_config_override():
        return []
    return [
        "",
        f"warning: {paths.CONFIG_ENV_VAR} is set in this shell but not in your user",
        "         environment, so the autostarted tray will load",
        f"           {effective_config()}",
        "         A shortcut cannot set an environment variable. Persist it if you",
        f"         meant otherwise:  setx {paths.CONFIG_ENV_VAR} \"{shell}\"",
    ]


# --- Commands ----------------------------------------------------------------


def _describe_mismatch(found: Shortcut, wanted: Shortcut) -> list[str]:
    return [
        f"  found:    {found.target} {found.arguments}".rstrip(),
        f"  expected: {wanted.target} {wanted.arguments}".rstrip(),
    ]


def install(location: Location, *, force: bool) -> int:
    """Write the shortcut, unless one is already there pointing somewhere else."""
    wanted = intended()
    path = location.path()
    existing = read_shortcut(path)

    if existing is not None and existing.matches(wanted):
        print(f"Already installed {path}")
        for line in wanted.lines():
            print(line)
        print(f"  config:  {effective_config()}")
        for line in _config_warning():
            print(line)
        return 0

    if existing is not None and not force:
        print(f"A different shortcut is already at {path}", file=sys.stderr)
        for line in _describe_mismatch(existing, wanted):
            print(line, file=sys.stderr)
        print("Re-run with --force to replace it.", file=sys.stderr)
        return 1

    write_shortcut(path, wanted)

    # Read it back rather than trusting Save(): it reports nothing useful, and
    # the shell normalises TargetPath on the way in.
    written = read_shortcut(path)
    if written is None or not written.matches(wanted):
        print(f"Wrote {path}, but it did not come back as expected:", file=sys.stderr)
        if written is not None:
            for line in _describe_mismatch(written, wanted):
                print(line, file=sys.stderr)
        return 1

    print(f"Installed {path}")
    for line in wanted.lines():
        print(line)
    print(f"  config:  {effective_config()}")
    for line in _config_warning():
        print(line)
    print("")
    for line in location.installed_note:
        print(line)
    print("To start it now, without waiting:")
    print(f"    {wanted.target} {wanted.arguments}")
    print("A tray that is already running keeps the hotkeys: the single-instance")
    print("mutex means an autostarted tray and a manual launch cannot fight over them.")
    return 0


def uninstall(location: Location, *, force: bool) -> int:
    """Remove the shortcut, unless it is one Referat did not write."""
    path = location.path()
    existing = read_shortcut(path)
    if existing is None:
        print(f"Nothing to remove at {path}")
        return 0

    if not force:
        try:
            wanted = intended()
        except InstallError:
            wanted = None
        if wanted is not None and not existing.matches(wanted):
            print(f"The shortcut at {path} is not the one this checkout installs:", file=sys.stderr)
            for line in _describe_mismatch(existing, wanted):
                print(line, file=sys.stderr)
            print("Re-run with --force to remove it anyway.", file=sys.stderr)
            return 1

    try:
        path.unlink()
    except OSError as exc:
        print(f"Could not remove {path}: {exc}", file=sys.stderr)
        return 1
    print(f"Removed {path}")
    print(location.removed_note)
    return 0


def _status_of(location: Location, wanted: Shortcut) -> int:
    """Report one location. Exit 0 when it is current, 1 when it is not."""
    path = location.path()
    existing = read_shortcut(path)
    if existing is None:
        print(f"{location.label}: not installed.")
        print(f"  folder: {location.dir()}")
        print(
            "  install with: .venv\\Scripts\\python.exe scripts\\install_autostart.py"
            + (f" {location.flag}" if location.flag else "")
        )
        return 1

    if not existing.matches(wanted):
        print(f"{location.label}: installed, but not current.")
        for line in _describe_mismatch(existing, wanted):
            print(line)
        print("  re-run with --force to replace it.")
        return 1

    print(f"{location.label}: installed {path}")
    for line in wanted.lines():
        print(line)
    return 0


def show_status() -> int:
    """Report both locations. Exit 0 only when both are current.

    Both, rather than the one a flag selected, because "is the tray set up"
    is one question and answering half of it is how somebody concludes the
    Start menu entry is missing when it is the autostart one that is.
    """
    wanted = intended()
    codes = [_status_of(location, wanted) for location in (AUTOSTART, START_MENU)]
    print(f"  config:  {effective_config()}")
    for line in _config_warning():
        print(line)
    return 0 if not any(codes) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="install_autostart.py",
        description=(
            "Create the shortcuts that launch the Referat tray. With no "
            "options, installs the Startup-folder one, so the tray comes up at "
            "sign-in; with --start-menu, the Start menu one, so it can be "
            "launched by name. Installing again when a shortcut is already "
            "current changes nothing."
        ),
    )
    what = parser.add_mutually_exclusive_group()
    what.add_argument(
        "--status",
        action="store_true",
        help="report both locations; exit 0 when both are current, 1 when either is not",
    )
    what.add_argument(
        "--uninstall",
        action="store_true",
        help="remove the shortcut from the selected location",
    )
    parser.add_argument(
        "--start-menu",
        dest="start_menu",
        action="store_true",
        help="act on the Start menu entry rather than the autostart one",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace, or remove, a shortcut that points somewhere else",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    location = START_MENU if args.start_menu else AUTOSTART
    try:
        if args.status:
            return show_status()
        if args.uninstall:
            return uninstall(location, force=args.force)
        return install(location, force=args.force)
    except InstallError as exc:
        print(f"install_autostart: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
