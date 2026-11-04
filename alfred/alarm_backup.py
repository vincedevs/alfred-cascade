"""A backup for each alarm, kept by the operating system, so an alarm still rings if
Alfred has crashed or was stopped.

When an alarm is set, the system is asked to play the alarm sound BACKUP_DELAY after it
is due: launchd on macOS, systemd on Linux (the Raspberry Pi). When Alfred rings the
alarm himself, he cancels the backup first, so it never rings twice. Only if Alfred
isn't there does it ring, a minute late. When Alfred starts, backups of alarms that are
no longer waiting (rung, missed or cancelled meanwhile) are removed.

Times are given to the system in the computer's own clock (which may differ from
ALFRED_TIMEZONE, e.g. a Pi left on UTC); a Unix time is the same moment either way.
"""

import os
import plistlib
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from loguru import logger

BACKUP_DELAY = 60  # seconds after the alarm is due; Alfred cancels it before then
RING_SECONDS = 120
LABEL = "com.alfred.alarm"  # each backup is "<LABEL>.<alarm id>"
LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"


def output(command: list[str]) -> str:
    """A system command's output ("" if it failed); tests replace this."""
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def run(command: list[str]) -> bool:
    """Run a system command quietly; tests replace this."""
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    if result.returncode != 0:
        logger.debug(f"{' '.join(command[:3])}… failed: {result.stderr.strip()[:200]}")
    return result.returncode == 0


class AlarmBackup:
    def __init__(self, sound: Path, platform: str = sys.platform):
        self._sound = sound
        self._mac = platform == "darwin"
        self.available = self._mac or bool(shutil.which("systemd-run"))
        if not self.available:
            logger.warning("No launchd or systemd here: alarms have no backup")

    def schedule(self, alarm_id: int, due_at: float) -> None:
        if not self.available:
            return
        self.cancel(alarm_id)
        when = datetime.fromtimestamp(due_at + BACKUP_DELAY)  # the computer's own clock
        schedule = self._schedule_mac if self._mac else self._schedule_linux
        done = schedule(alarm_id, when)
        if done:
            logger.debug(f"Alarm {alarm_id}: system backup set for {when:%Y-%m-%d %H:%M}")
        else:
            logger.warning(f"Alarm {alarm_id}: couldn't set the system backup")

    def cancel(self, alarm_id: int) -> None:
        if not self.available:
            return
        if self._mac:
            plist = LAUNCH_AGENTS / f"{LABEL}.{alarm_id}.plist"
            if plist.exists():
                run(["launchctl", "bootout", f"gui/{_uid()}", str(plist)])
                plist.unlink(missing_ok=True)
        else:
            run(["systemctl", "--user", "stop", f"alfred-alarm-{alarm_id}.timer"])

    def clear_old(self, keep: set[int]) -> None:
        """At startup: remove backups of alarms that are no longer waiting."""
        if not self.available:
            return
        if self._mac:
            names = [
                plist.stem.rsplit(".", 1)[-1] for plist in LAUNCH_AGENTS.glob(f"{LABEL}.*.plist")
            ]
        else:  # timers still waiting, e.g. for an alarm that came due while Alfred was off
            listed = output(
                [
                    "systemctl",
                    "--user",
                    "list-timers",
                    "--all",
                    "--plain",
                    "--no-legend",
                    "alfred-alarm-*",
                ]
            )
            names = re.findall(r"alfred-alarm-(\d+)\.timer", listed)
        for alarm_id in names:
            if alarm_id.isdigit() and int(alarm_id) not in keep:
                self.cancel(int(alarm_id))

    def _ring_command(self, alarm_id: int) -> str:
        player = "afplay" if self._mac else "aplay -q"
        rounds = RING_SECONDS // 2  # the sound is about 2 seconds
        ring = f"for i in $(seq {rounds}); do {player} '{self._sound}'; done"
        if not self._mac:
            return ring
        # launchd would ring it again next year: the backup removes itself once it rang
        plist = LAUNCH_AGENTS / f"{LABEL}.{alarm_id}.plist"
        return f"rm -f '{plist}'; {ring}; launchctl bootout gui/{_uid()}/{LABEL}.{alarm_id}"

    def _schedule_mac(self, alarm_id: int, when: datetime) -> bool:
        LAUNCH_AGENTS.mkdir(parents=True, exist_ok=True)
        plist = LAUNCH_AGENTS / f"{LABEL}.{alarm_id}.plist"
        job = {
            "Label": f"{LABEL}.{alarm_id}",
            "ProgramArguments": ["/bin/sh", "-c", self._ring_command(alarm_id)],
            "StartCalendarInterval": {
                "Month": when.month,
                "Day": when.day,
                "Hour": when.hour,
                "Minute": when.minute,
            },
        }
        plist.write_bytes(plistlib.dumps(job))
        return run(["launchctl", "bootstrap", f"gui/{_uid()}", str(plist)])

    def _schedule_linux(self, alarm_id: int, when: datetime) -> bool:
        return run(
            [
                "systemd-run",
                "--user",
                f"--unit=alfred-alarm-{alarm_id}",
                f"--on-calendar={when:%Y-%m-%d %H:%M:%S}",
                "/bin/sh",
                "-c",
                self._ring_command(alarm_id),
            ]
        )


def _uid() -> int:
    return os.getuid()
