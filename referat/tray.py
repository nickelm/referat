r"""The tray app: the recorder core, and the process entry point.

This is the only module that knows about all the others. It owns the
:class:`referat.state.Machine`, translates the two hotkeys into transitions,
drives the :class:`referat.recorder.Recorder`, holds sleep off through
:class:`referat.power.SleepBlocker`, writes `status.json`, and hands each stopped
meeting to :mod:`referat.transcribe` on a background thread.

**Nothing in here is Qt, and that is the point.** Until build step 20 this
module also drew a `pystray` icon and built its menu; the icon is now a
`QSystemTrayIcon` in :mod:`referat.ui.shell`, and what is left here is the part
that must come up whether or not any of that works. :func:`main` starts this
core first — hotkeys registered, `status.json` written — and only then imports
the Qt shell, so the ordering rule step 20 wrote down is a fact about this file
rather than an intention: *the recorder, the hotkeys, the state machine and
`status.json` come up first and independently, and the window is opened after.*

The one seam between the two is :attr:`App.notify`, which the shell replaces
with a real balloon. Its default logs, so every message this class raises still
lands somewhere when there is no UI at all.

Runs under `pythonw.exe` in production (see the `referat-tray` gui-script), so
there is no console: the rotating log in `%LOCALAPPDATA%\Referat` is the only
record of what happened. For development, `python -m referat.tray` keeps a
console attached.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
import time
from collections.abc import Callable
from ctypes import wintypes

from referat import gpu, paths, progress, rerun, sac, status, voices
from referat.config import Config, ConfigError, load_config
from referat.hotkeys import Hotkeys
from referat.logging_setup import setup_logging
from referat.meeting import Meeting, MeetingStatus, sac_block
from referat.power import PowerEvents, SleepBlocker, SuspendWatcher
from referat.recorder import Recorder, RecorderError
from referat.state import Machine, State, Transition
from referat.transcribe import progress_key, transcribe_meeting

# Named explicitly so the log reads the same whether this ran as referat-tray
# or as `python -m referat.tray`, where __name__ would be "__main__".
log = logging.getLogger("referat.tray")

MUTEX_NAME = r"Local\ReferatTraySingleInstance"
ERROR_ALREADY_EXISTS = 183

RECOVERY_PROBE_SECONDS = 15 * 60.0
RECOVERY_GIVE_UP_SECONDS = 12 * 3600.0
RECOVERY_MAX_RERUNS = 4
"""How :meth:`App._recover_blocked` waits out a Smart App Control window.

A meeting whose diarization was blocked keeps its audio — see
:func:`referat.transcribe.audio_is_clean` — and is re-transcribed by this
process once :func:`referat.sac.probe` comes back clean. Every quarter hour,
because the window is measured in hours and each probe costs a child process
importing torch; for at most twelve, because a block that outlasts a working
day is not a window, and a person should hear about it rather than a thread
keep quietly trying; and at most four reruns per meeting, because the window
closes one file at a time — 2026-09-01 went `_odepack`, `_stats_pythran`,
`_sobol` — so a clean probe can be followed by a run that hits the next file.
Nothing here is a config knob: there is no meeting for which a different
answer is right.
"""

SETTLE_SECONDS = 90.0
RESUME_GRACE_SECONDS = 15.0
"""How a meeting stopped by the lid or by sleep waits before it is transcribed.

A meeting the hotkey stops goes straight onto the GPU. One the lid stopped is
about to lose the machine: the suspend follows the lid by a few seconds when
the lid action is Sleep, and a large-v3 load that a suspend interrupts is the
wedged job `reconcile_interrupted` exists to clean up after. So the job waits.
If the machine sleeps, it waits for the resume and then
:data:`RESUME_GRACE_SECONDS` more for the GPU and the audio devices to come
back; if nothing happens for :data:`SETTLE_SECONDS` — a docked laptop whose lid
action is *do nothing* — it goes ahead, since a machine that stayed awake for a
minute and a half is staying awake. Neither is a config knob: there is no
meeting for which a different answer is right, and the cost of the long one is
a transcript ninety seconds later than it could have been.
"""


# --- Single instance --------------------------------------------------------


def acquire_single_instance() -> wintypes.HANDLE | None:
    """Take the named mutex, or return None when another tray already holds it.

    Autostart plus a manual launch would otherwise leave two processes fighting
    over the same hotkeys and both writing `status.json`. The handle is returned
    so the caller can hold it for as long as those two things are true of this
    process, and hand it to :func:`release_single_instance` when they stop being.
    """
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    handle = kernel32.CreateMutexW(None, wintypes.BOOL(True), MUTEX_NAME)
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        # Windows hands back a handle to the *other* tray's mutex on refusal,
        # and an open handle keeps that mutex alive after its owner has let
        # go. The refused process exits at once, so it never mattered there;
        # it matters to anything that asks twice in one process.
        kernel32.CloseHandle(handle)
        return None
    return handle


def release_single_instance(handle: wintypes.HANDLE) -> None:
    """Let go of the mutex now, rather than when the process finally dies.

    Windows releases a mutex when its last handle closes, which for a handle
    nobody closes is process exit -- and a tray's exit is not the moment
    `main` returns. Interpreter finalization follows, tearing down whatever
    the process loaded, and after a transcription that is torch, a CUDA
    context, CTranslate2 and Qt. On 2026-09-11 a tray logged `tray exited` at
    08:31:01 and was still holding this mutex at 08:31:25, so three restarts
    in a row were refused as *another Referat tray is already running* while
    no tray was running -- and under `pythonw.exe` the refusal is a line in
    the log and nothing on the screen. Which finalizer took the time is not
    established; this does not depend on the answer.

    Called after :meth:`App.shutdown`, which is the point at which the two
    things the mutex protects -- the hotkeys and `status.json` -- have been
    given up, so a tray admitted from here on takes nothing from this one.
    Never raises: a release that fails leaves the process exit to do the same
    job, as it always did.
    """
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.ReleaseMutex(handle)
        kernel32.CloseHandle(handle)
    except Exception:
        log.debug("could not release the single-instance mutex", exc_info=True)


# --- The app ----------------------------------------------------------------


class App:
    """The recorder core: the state machine, the hotkeys and the meetings.

    Owns no widget and imports no toolkit. :mod:`referat.ui.shell` attaches to an
    instance of this — registering a listener on :attr:`machine` and replacing
    :attr:`notify` — and everything it drives goes through the public methods
    below, which are the same ones the hotkeys call.
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        self.machine = Machine()
        self.recorder: Recorder | None = None
        self.power = SleepBlocker()
        self.notify: Callable[[str], None] = self._log_notification
        self._stopped = False
        self._closing = threading.Event()
        # Meetings whose audio was kept because Smart App Control blocked their
        # diarization, waiting for the window to close; and how many times each
        # has been re-run for it, kept for the life of the process so the cap
        # in `RECOVERY_MAX_RERUNS` survives the meeting leaving and re-entering
        # the pending set.
        self._recovery_lock = threading.Lock()
        self._recovery_pending: set[str] = set()
        self._recovery_reruns: dict[str, int] = {}
        self._recovery_thread: threading.Thread | None = None
        self.hotkeys = Hotkeys(
            config.hotkeys,
            on_toggle_record=self.on_toggle_record,
            on_toggle_pause=self.on_toggle_pause,
        )
        # Power first, and as its own listener: Machine._notify calls listeners
        # in order and isolates their exceptions, so nothing registered later --
        # the status write, and the whole of the Qt shell -- can cost the machine
        # its sleep hold.
        self.machine.add_listener(self._on_transition_power)
        self.machine.add_listener(self._on_transition_status)
        # What the power events below say about the machine, for a job waiting
        # to load a model: asleep or not, and when it last woke. Under one
        # condition because the waiter and the callbacks are on different
        # threads, and a resume that arrives between a check and a wait must
        # wake the waiter rather than be missed.
        self._power_cond = threading.Condition()
        self._asleep = False
        self._resumed_at = 0.0
        # Started last and owning nothing: it only reads the progress registry
        # and writes to the log, so it cannot cost the recorder anything. Its
        # gap callback is the fallback stop for a sleep nothing announced.
        self.watcher = SuspendWatcher(self._describe_work, on_gap=self._on_lost_time)
        # The lid and sleep. Registering can fail and the recorder comes up
        # regardless; see `PowerEvents` for what each signal is and is not.
        self.events = PowerEvents(
            on_lid_closed=self._on_lid_closed,
            on_suspend=self._on_suspend,
            on_resume=self._on_resume,
        )

    def _describe_work(self) -> str:
        """What is in flight, for the log's heartbeat and for a resume line.

        Read out of :mod:`referat.progress` rather than tracked here, because
        that registry is already what the transcription pipeline and the notes
        queue report into, and a second tally of the same jobs would be the first
        thing to disagree with it.
        """
        return ", ".join(
            f"{job.title} ({job.phase}, {job.elapsed / 60.0:.0f} min)"
            for job in progress.active()
        )

    @property
    def missing_channels(self) -> list[str]:
        """What this run failed to capture, for whatever is drawing the state."""
        return list(self.recorder.missing_channels) if self.recorder else []

    def _on_transition_power(self, transition: Transition) -> None:
        """Hold sleep off while this process is doing anything at all.

        Every state but IDLE, which is wider than the *recording or paused* this
        used to be and is meant to be: TRANSCRIBING is a CUDA job that a suspend
        can wedge, and STOPPED is the moment between the two where dropping the
        hold would open a window for exactly that. The cost is a laptop staying
        awake for the few minutes a transcription takes; see
        :mod:`referat.power` for the measurement that reversed the old decision.

        A job outliving its meeting is still covered, because the machine is
        RECORDING while the next one runs and returns to TRANSCRIBING after it,
        and `notes` is a second reason held elsewhere.
        """
        self.power.want("machine", transition.to is not State.IDLE)

    def _on_transition_status(self, _transition: Transition) -> None:
        """Record the new state on disk, for `referat status` to read back."""
        status.write_status(self.machine)

    # --- Recording ----------------------------------------------------------

    def on_toggle_record(self) -> None:
        """Start a meeting when there is none, otherwise stop the running one.

        The record hotkey is one button, so it has to mean both. The two halves
        are :meth:`start_meeting` and :meth:`stop_meeting`, which the command
        center's buttons call directly — the buttons know which one they mean,
        and there is still exactly one path into the state machine.
        """
        state = self.machine.state
        if state in (State.IDLE, State.TRANSCRIBING):
            self.start_meeting()
        elif state in (State.RECORDING, State.PAUSED):
            self.stop_meeting()
        else:
            log.info("record hotkey ignored in state %s", state)

    def start_meeting(self) -> None:
        """Open the streams first, then transition with the id of the real folder.

        The order matters: the transition carries the meeting id, and only
        :meth:`Recorder.start` knows it, because a folder collision can turn
        `2026-08-27_1400` into `2026-08-27_1400_2`.
        """
        if self.machine.state not in (State.IDLE, State.TRANSCRIBING):
            log.info("start ignored in state %s", self.machine.state)
            return
        recorder = Recorder(self.config)
        try:
            meeting = recorder.start()
        except RecorderError:
            # No audio device at all: stay idle rather than pretend to record.
            log.exception("could not start recording")
            self.notify("Could not start recording - no audio device would open.")
            return
        self.recorder = recorder
        if not self.machine.try_to(State.RECORDING, meeting_id=meeting.id):
            log.warning("recording refused by the state machine; closing %s", meeting.id)
            self._stop_recorder()
            return
        # After the transition, so the toast lands on a tray that is already
        # showing the hollow icon. A meeting held in a room *is* the microphone
        # channel, so this is the difference between recording and only looking
        # like it -- and on 2026-09-02 that difference was an INFO line nobody
        # read until the meeting was over and gone.
        if "mic" in recorder.missing_channels:
            self.notify(
                f"NO MICROPHONE - {meeting.id} is recording system audio only. "
                f"Stop, fix the mic, and start again."
            )
        elif recorder.missing_channels:
            self.notify(
                f"No system audio - {meeting.id} is recording the microphone only."
            )
        # A block found now is known an hour before it would cost anything, and
        # said so; the pipeline at the end of this meeting keeps the audio
        # either way, so this is warning and never a decision.
        self._probe_async(f"the start of {meeting.id}")

    def stop_meeting(self, *, because: str = "") -> None:
        """End the running meeting and hand it to a background transcription job.

        `because` is set when the machine stopped it rather than a person — the
        lid closed, sleep began, or a sleep was found in the clock afterwards —
        and does two things: the notification says so, and the transcription
        *waits* for the machine to be awake before it loads a model, since the
        thing that stopped the meeting is about to interrupt anything started
        now. See :data:`SETTLE_SECONDS`. Everything else is the same stop the
        hotkey makes, through the same lines.
        """
        if self.machine.state not in (State.RECORDING, State.PAUSED):
            log.info("stop ignored in state %s", self.machine.state)
            return
        meeting_id = self.machine.meeting_id
        if because:
            log.warning("stopping %s: %s", meeting_id, because)
        if self.machine.try_to(State.STOPPED):
            self._finish_meeting(self._stop_recorder(), settle=because)
            if because and meeting_id:
                self.notify(
                    f"Stopped {meeting_id} because {because}. It is transcribed "
                    f"once the machine is awake again."
                )

    # --- The lid, and sleep ---------------------------------------------------

    def _on_lid_closed(self) -> None:
        """A closed laptop is not recording a meeting. On Windows' own thread."""
        if self.machine.recording:
            self.stop_meeting(because="the lid closed")

    def _on_suspend(self) -> None:
        """The machine is going to sleep: stop what is recording, and remember it.

        Windows gives about two seconds. Closing two WAVs whose headers were
        synced within the last two anyway, and writing one `meta.json`, is well
        inside that; if the machine still goes before the stop completes, the
        thread is frozen rather than killed and finishes the stop on resume.
        A job already on the GPU cannot be helped from here — the clock
        watcher records the gap and `reconcile_interrupted` picks up the piece.
        """
        with self._power_cond:
            self._asleep = True
        if self.machine.recording:
            self.stop_meeting(because="the machine went to sleep")
        elif self.machine.jobs:
            log.warning("sleeping with a transcription in flight: %s", self._describe_work())

    def _on_resume(self) -> None:
        """The machine is back. Wakes any job waiting in :meth:`_settle`."""
        with self._power_cond:
            self._asleep = False
            self._resumed_at = time.monotonic()
            self._power_cond.notify_all()

    def _on_lost_time(self, lost: float) -> None:
        """The clock says the machine slept, whether or not anything announced it.

        The fallback behind :class:`referat.power.PowerEvents`: a suspend that
        delivered no notification still leaves a meeting that was recorded
        through a sleep, and by the lid rule that meeting was over when the
        sleep began. The stop is late by the length of the sleep, which the WAVs
        carry as padded silence, and `referat trim` is how that stretch is cut
        off afterwards. Never a second stop: a meeting the lid already stopped
        is not recording by the time this runs.
        """
        if self.machine.recording:
            self.stop_meeting(
                because=f"the machine slept for {lost / 60.0:.0f} minutes without saying so"
            )

    def _settle(self, meeting: Meeting, why: str) -> None:
        """Wait, on the job thread, for the machine to be awake. See :data:`SETTLE_SECONDS`.

        Reported through :mod:`referat.progress` under the job's own key, which
        the pipeline's `begin` then replaces, so the Activity tab shows a job
        that is waiting rather than one that has not started. A shutdown ends
        the wait; the thread is a daemon and the meeting is `recorded` with its
        audio in staging, which is what the next tray's reconciliation reads.
        """
        progress.begin(
            progress_key(meeting),
            progress.TRANSCRIBE,
            meeting.id,
            f"waiting for the machine to settle ({why})",
        )
        began = time.monotonic()
        deadline = began + SETTLE_SECONDS
        with self._power_cond:
            while not self._closing.is_set():
                now = time.monotonic()
                if self._asleep:
                    progress.step(progress_key(meeting), "waiting for the machine to wake")
                    # A suspend freezes this thread; the timeout is only so a
                    # resume that delivered no notification still gets a look.
                    self._power_cond.wait(60.0)
                    continue
                if self._resumed_at > began:
                    ready = self._resumed_at + RESUME_GRACE_SECONDS
                    if now >= ready:
                        log.info("%s: the machine is awake; transcribing", meeting.id)
                        return
                    self._power_cond.wait(ready - now)
                    continue
                if now >= deadline:
                    log.info(
                        "%s: the machine stayed awake for %.0fs; transcribing",
                        meeting.id,
                        SETTLE_SECONDS,
                    )
                    return
                self._power_cond.wait(deadline - now)

    def on_toggle_pause(self) -> None:
        """Pause a running meeting, or resume a paused one."""
        state = self.machine.state
        if state is State.RECORDING:
            if self.machine.try_to(State.PAUSED) and self.recorder is not None:
                self.recorder.pause()
        elif state is State.PAUSED:
            if self.machine.try_to(State.RECORDING) and self.recorder is not None:
                self.recorder.resume()
        else:
            log.info("pause hotkey ignored in state %s", state)

    def _stop_recorder(self) -> Meeting | None:
        """Close the streams and finalize `meta.json`. Safe when nothing is recording.

        Returns the meeting so the caller can transcribe it; None when there was
        nothing recording, or when stopping failed badly enough that there is no
        meeting to hand on.
        """
        recorder, self.recorder = self.recorder, None
        if recorder is None:
            return None
        try:
            return recorder.stop()
        except Exception:
            # Whatever went wrong, the WAVs on disk are already playable.
            log.exception("could not stop the recorder cleanly")
            return recorder.meeting

    def _finish_meeting(self, meeting: Meeting | None, *, settle: str = "") -> None:
        """Hand the stopped meeting to a background transcription job."""
        if meeting is None:
            # Nothing captured worth transcribing; STOPPED -> IDLE exists for this.
            log.warning("no meeting to transcribe")
            self.machine.try_to(State.IDLE)
            return
        self._queue_transcription(meeting, settle=settle)

    def _queue_transcription(self, meeting: Meeting, *, settle: str = "") -> None:
        """Count the job, claim the state, start the thread. The one path onto the GPU.

        All three callers want exactly this — a meeting just stopped, a meeting
        the last process did not finish, and since build step 23 a meeting
        somebody asked to re-transcribe from the command center — and they used
        to be three copies of it.

        `try_to` rather than `to`, which is what makes the three interchangeable:
        a hotkey may have started a recording between the caller's decision and
        here, and `RECORDING -> TRANSCRIBING` is not a legal edge. The job count
        is still right, the thread still runs, and the machine keeps saying the
        truer of the two things — which is the same reasoning the resumed-job
        edge `IDLE -> TRANSCRIBING` was added under.

        Queueing several is safe: `transcribe._RUN_LOCK` serializes them inside
        this process, so two large-v3 models never coexist.
        """
        self.machine.begin_job()
        self.machine.try_to(State.TRANSCRIBING)
        threading.Thread(
            target=self._transcribe, args=(meeting, settle), name="transcribe", daemon=True
        ).start()

    def rerun_meeting(self, meeting_id: str) -> tuple[bool, str]:
        """Transcribe a finished meeting again, on the thread a fresh one uses.

        The command center's *Re-transcribe...*, and the reason it is a method
        here rather than a `cli` function: what a rerun costs is the state
        machine, the job count and a thread, all three of which are this class's
        and none of which a window may touch. `referat rerun` in a terminal is
        the same work in a process that owns none of them, which is why it runs
        the pipeline inline and this queues it.

        The rules are :func:`referat.rerun.check`'s — including the one that is
        deliberately *not* asked here, `busy_tray`: it guards a second process
        against this one, and this is that one.

        Returns the pair rather than a `cli.Outcome` so the recorder core keeps
        importing no part of the CLI. The message is unprefixed either way.
        """
        meeting, why = rerun.check(self.config, meeting_id)
        if meeting is None:
            return False, why
        # Before the pipeline runs, because it writes `speakers/SPEAKER_NN_*.wav`
        # as it goes and a run finding fewer speakers than the last would leave
        # the extras behind claiming to be somebody this run never produced.
        rerun.clear_snippets(meeting)
        self._queue_transcription(meeting)
        channels = ", ".join(p.name for p in rerun.audio_present(meeting))
        return True, f"re-transcribing {meeting.id} from {channels}"

    def _transcribe(self, meeting: Meeting, settle: str = "") -> None:
        """The background job. Deliberately survives a new recording starting.

        `settle` names what stopped the meeting when the machine did — see
        :meth:`stop_meeting` — and makes the job wait for the machine to be
        awake before it touches the GPU.
        """
        try:
            if settle:
                self._settle(meeting, settle)
                if self._closing.is_set():
                    return
            done = transcribe_meeting(meeting, self.config)
            if not self._recover_blocked(done):
                self._notify_transcribed(done)
        except Exception:
            # Already recorded as `failed` in meta.json; the tray carries on and
            # `referat rerun` can try again.
            log.exception("transcription job for %s failed", meeting.id)
            self.notify(f"Transcription of {meeting.id} failed")
        finally:
            self.machine.end_job()
            # Ending a job while a new meeting records changes no state, but the
            # job count in status.json is still worth keeping honest.
            status.write_status(self.machine)
            # The last word on the GPU, after the pipeline's own release and
            # deliberately after the `except` above rather than inside it: a
            # release that runs while an exception is still propagating is
            # partial, because the traceback holds the frames that hold the
            # tensors. By here the exception is handled and the cycle collector
            # can reach them. This is the process the whole thing is about — an
            # idle tray sitting on 8.4 GB of a 12 GB card is what made a
            # `referat rerun` in a second process stall.
            gpu.release(f"the {meeting.id} job")

    # --- Notifications ------------------------------------------------------

    def _notify_transcribed(self, meeting: Meeting) -> None:
        """Say the meeting is done, and how many voices nobody has named yet.

        The count is the whole point of the message: a speaker only becomes a
        name if somebody names it, and nothing else in Referat ever asks. With
        everybody recognised it says nothing extra.
        """
        unknown = voices.unknown_speakers(meeting)
        message = f"Transcribed {meeting.id}"
        if unknown:
            message += (
                f" ({len(unknown)} unknown voice{'s' if len(unknown) > 1 else ''} — "
                f"run: referat label {meeting.id})"
            )
        self.notify(message)

    @staticmethod
    def _log_notification(message: str) -> None:
        """What :attr:`notify` does before a UI has replaced it, and if none ever does."""
        log.info("notification: %s", message)

    # --- Smart App Control ----------------------------------------------------

    def _probe_async(self, when: str) -> None:
        """Ask, on a thread, whether Smart App Control is blocking the stack, and say so.

        A child process importing torch and pyannote — see
        :func:`referat.sac.probe` — so nothing unsigned is loaded into the
        process that owns the recorder, and on a daemon thread so the caller
        never waits on it. A block is a warning here and a decision nowhere:
        what happens at the end of the meeting is the gate's, which keeps the
        audio whether or not this ever ran.
        """

        def run() -> None:
            try:
                result = sac.probe()
            except Exception:
                log.exception("the Smart App Control probe at %s crashed", when)
                return
            if result.status == sac.BLOCKED:
                log.warning("at %s: %s", when, result.describe())
                self.notify(
                    f"Smart App Control is blocking {result.blocked}. Speaker names "
                    f"will fail until it clears; a meeting transcribed meanwhile keeps "
                    f"its audio and is re-transcribed when it does."
                )
            else:
                log.info("at %s: %s", when, result.describe())

        threading.Thread(target=run, name="sac-probe", daemon=True).start()

    def _recover_blocked(self, meeting: Meeting) -> bool:
        """Queue a meeting whose diarization Smart App Control blocked for a later rerun.

        True when this took the meeting — it is `gate_failed`, its audio is in
        staging, and `meta.json` names the file that was refused — so the
        caller's *Transcribed ...* notification is replaced by this one, which
        says what was lost and that it is coming back. False for every other
        meeting, including one the gate refused on the transcript's merits,
        which no amount of waiting improves.

        Bounded by :data:`RECOVERY_MAX_RERUNS`: a meeting that has been re-run
        that many times and is still blocked is handed to the person, with the
        command that finishes the job, rather than run a fifth time.
        """
        blocked = sac_block(meeting)
        if meeting.status is not MeetingStatus.GATE_FAILED or not blocked:
            return False
        with self._recovery_lock:
            reruns = self._recovery_reruns.get(meeting.id, 0)
            if reruns >= RECOVERY_MAX_RERUNS:
                self.notify(
                    f"{meeting.id}: Smart App Control still blocking {blocked} after "
                    f"{reruns} re-transcriptions. Audio kept - run: referat rerun "
                    f"{meeting.id} once `referat probe` is clear."
                )
                return True
            self._recovery_pending.add(meeting.id)
            self._start_recovery_thread()
        self.notify(
            f"{meeting.id}: speaker names lost - Smart App Control blocked {blocked}. "
            f"Audio kept; it will be re-transcribed when the block clears."
        )
        return True

    def _start_recovery_thread(self) -> None:
        """Start the waiting thread if none is running. Called under the lock."""
        if self._recovery_thread is not None and self._recovery_thread.is_alive():
            return
        self._recovery_thread = threading.Thread(
            target=self._recovery_loop, name="sac-recovery", daemon=True
        )
        self._recovery_thread.start()

    def _recovery_loop(self) -> None:
        """Probe every quarter hour; re-run every pending meeting once the probe is clean.

        Only while the recorder is idle — a rerun is a GPU job, and starting one
        during a live meeting is a load the recording never asked for — and
        only on a probe that says :data:`referat.sac.OK`: an :data:`ERROR`
        probe says nothing either way and is not permission. The thread ends
        after dispatching; a rerun that hits the next blocked file comes back
        through :meth:`_recover_blocked`, which starts it again.
        """
        started = time.monotonic()
        while not self._closing.wait(RECOVERY_PROBE_SECONDS):
            with self._recovery_lock:
                pending = sorted(self._recovery_pending)
            if not pending:
                return
            if time.monotonic() - started > RECOVERY_GIVE_UP_SECONDS:
                with self._recovery_lock:
                    self._recovery_pending.clear()
                self.notify(
                    f"Smart App Control has been blocking for {RECOVERY_GIVE_UP_SECONDS / 3600:.0f} "
                    f"hours. Audio kept for {', '.join(pending)} - run: referat rerun "
                    f"<id> once `referat probe` is clear."
                )
                return
            if self.machine.state is not State.IDLE:
                log.info("not probing Smart App Control while %s", self.machine.state)
                continue
            try:
                result = sac.probe()
            except Exception:
                log.exception("the Smart App Control probe crashed; will try again")
                continue
            log.info("waiting on %s: %s", ", ".join(pending), result.describe())
            if not result.ok:
                continue
            for meeting_id in pending:
                with self._recovery_lock:
                    self._recovery_pending.discard(meeting_id)
                    self._recovery_reruns[meeting_id] = self._recovery_reruns.get(meeting_id, 0) + 1
                ok, why = self.rerun_meeting(meeting_id)
                if ok:
                    self.notify(f"Smart App Control cleared - {why} for its speaker names")
                else:
                    log.warning("could not re-transcribe %s: %s", meeting_id, why)
            return

    def _recover_blocked_on_disk(self) -> None:
        """Pick up meetings a previous tray left waiting on a block.

        The pending set lives in this process, so a tray restarted mid-window
        would otherwise forget them. Staging only: a meeting with audio is
        never anywhere else. Never fatal, on the rule every optional thing in
        this file runs under.
        """
        try:
            for folder in paths.list_meeting_dirs(self.config.staging_dir()):
                meeting = Meeting.load(folder)
                if meeting is None or meeting.status is not MeetingStatus.GATE_FAILED:
                    continue
                if sac_block(meeting) and (
                    meeting.mic_path.exists() or meeting.system_path.exists()
                ):
                    log.info("%s is waiting on Smart App Control from a previous tray", meeting.id)
                    self._recover_blocked(meeting)
        except Exception:
            log.exception("could not look for meetings waiting on Smart App Control")

    # --- Lifecycle ----------------------------------------------------------

    def start(self) -> None:
        """Register the hotkeys and publish the initial state. The recorder is live after this.

        The reconciliation runs **before** `write_status`, and the order is the
        whole of its correctness: it asks whether a live tray is transcribing,
        and writing our own idle status first would make this process the one
        answering. Hotkeys go first regardless — the recorder comes up before
        anything that merely tidies, the same rule that puts the window after it.
        """
        self.hotkeys.start()
        self._reconcile_meetings()
        status.write_status(self.machine)
        log.info(
            "recorder running; %s to record, %s to pause",
            self.config.hotkeys.toggle_record,
            self.config.hotkeys.toggle_pause,
        )
        # After the recorder is up, and both on their own threads: neither may
        # delay a hotkey, and a probe that fails is a log line.
        self._recover_blocked_on_disk()
        self._probe_async("startup")

    def _reconcile_meetings(self) -> None:
        """Correct any meeting left `transcribing` by a process that is gone.

        Guarded on :func:`referat.rerun.busy_tray`, which is the existing answer
        to *is another process working on this* and is reused rather than
        restated — a second copy of that rule is how two trays would come to
        disagree about which of them is allowed to write. `rerun` is free of
        torch at module scope, so importing it here costs nothing.

        Never fatal. A tray that cannot tidy up is still a tray that records,
        which is the rule every optional thing in this file is held to.
        """
        from referat import rerun
        from referat.meeting import reconcile_interrupted

        try:
            if (busy := rerun.busy_tray()) is not None:
                log.warning(
                    "another tray is transcribing %s; not reconciling", busy or "a meeting"
                )
                return
            if repaired := reconcile_interrupted(self.config):
                self._resume(repaired)
        except Exception:
            log.exception("could not reconcile the meetings on disk")

    def _resume(self, meetings: list[Meeting]) -> None:
        """Re-queue meetings whose transcription did not survive the last process.

        This is what makes closing the lid a delay rather than a chore: the
        meeting comes back as `recorded` with both WAVs still in staging, and
        finishing it is the same job that was interrupted. Doing it automatically
        is consistent with transcription being automatic on stop — nobody asked
        for the first attempt either.

        **Only meetings that still have audio**, since the transcript is rebuilt
        from the WAVs and a meeting whose audio was released has nothing to run.
        That is also what keeps this from looping: a run that fails writes
        `failed` and a run that succeeds writes `transcribed`, and neither is
        `transcribing`, so nothing is picked up twice for the same reason.

        Jobs are serialized by `transcribe._RUN_LOCK`, so queueing several is
        safe — they run one at a time, and two large-v3 models never coexist.
        This is `IDLE -> TRANSCRIBING`, the edge added for exactly this.
        """
        pending = [m for m in meetings if m.mic_path.exists() or m.system_path.exists()]
        if not pending:
            return
        names = ", ".join(m.id for m in pending)
        log.info("resuming %d interrupted transcription(s): %s", len(pending), names)
        self.notify(
            f"Resuming {len(pending)} interrupted transcription"
            f"{'s' if len(pending) > 1 else ''}: {names}"
        )
        for meeting in pending:
            self._queue_transcription(meeting)

    def shutdown(self) -> None:
        """Stop the hotkeys, close out any recording, and release the sleep hold.

        Idempotent: it is called by the tray's Quit item and again from
        :func:`main`'s `finally`, and a second pass must not try to stop a
        recorder that is already stopped.
        """
        if self._stopped:
            return
        self._stopped = True
        self._closing.set()
        log.info("shutting down")
        self.hotkeys.stop()
        if self.machine.recording:
            # Never exit mid-meeting without closing it out first.
            log.warning("quit while %s; stopping the meeting", self.machine.state)
            self.machine.try_to(State.STOPPED)
            self._stop_recorder()
            self.machine.try_to(State.IDLE)
        with self._power_cond:
            # Any job waiting in `_settle` returns on this; the meeting stays
            # `recorded` in staging for the next tray to reconcile.
            self._power_cond.notify_all()
        self.events.close()
        self.watcher.close()
        self.power.close()
        status.clear_status()


def main() -> int:
    """Entry point for `referat-tray` and for `python -m referat.tray`.

    The order is the rule: config, logging, the single-instance mutex, then the
    **recorder core**, and only then the Qt shell. Everything a meeting needs is
    running before anything graphical is imported, so a Qt that will not load is
    an exception with a stack trace in the log rather than a silent failure to
    start — and the escape hatch step 20 wrote down, putting the icon back on
    `pystray`, is a change to these last four lines and to nothing else.

    A Qt that will not load still ends the process, because a tray with no icon
    is one nobody can see, quit, or tell apart from a crash. What *is* survivable
    is the command center failing to open, and that is guarded one layer down in
    :meth:`referat.ui.shell.Shell.open_window`.
    """
    try:
        config = load_config()
    except ConfigError as exc:
        # Under pythonw.exe the print below goes nowhere, and this used to
        # return before any logging existed -- so an autostarted tray with an
        # unparseable config simply never appeared, with no record of why. The
        # log file's path does not depend on the config, so logging is set up
        # at the default level for the one line that says what stopped it.
        setup_logging("INFO")
        log.error("referat-tray cannot start: %s", exc)
        print(f"referat-tray: {exc}", file=sys.stderr)
        return 1

    setup_logging(config.app.log_level)

    # Held until `shutdown` has given up the hotkeys and `status.json`, and
    # released explicitly then -- not left to process exit, which after a
    # transcription can trail `tray exited` by half a minute of finalization
    # and refuse every restart attempted in between.
    mutex = acquire_single_instance()
    if mutex is None:
        log.warning("another Referat tray is already running; exiting")
        print("referat-tray: already running", file=sys.stderr)
        return 1
    log.debug("single-instance mutex held (handle %s)", mutex)

    app = App(config)
    try:
        app.start()
    except Exception:
        log.exception("the recorder core would not start")
        release_single_instance(mutex)
        return 1

    try:
        from referat.ui import shell

        return shell.run(app)
    except Exception:
        log.exception("the Qt shell would not start; there is no tray icon")
        print("referat-tray: the Qt shell would not start; see the log", file=sys.stderr)
        return 1
    finally:
        app.shutdown()
        release_single_instance(mutex)
        log.info("tray exited")


if __name__ == "__main__":
    raise SystemExit(main())
