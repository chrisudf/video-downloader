"""PyInstaller entry point.

Kept outside backend/ so freezing concerns (multiprocessing bootstrap, the
std-stream redirect for windowed builds) never leak into the app modules.
"""

import multiprocessing
import sys

# Frozen Windows apps re-exec themselves for multiprocessing; without this a
# child process would re-run the whole app (open another browser tab, try to
# bind the port again). Must be the first thing that runs.
multiprocessing.freeze_support()


def _redirect_std_streams() -> None:
    """Frozen builds have no useful stdout/stderr unless run from a terminal.
    Windows windowed builds get None streams (writes would crash with
    AttributeError); a Finder-launched macOS .app gets real fds pointing at
    /dev/null (writes silently vanish, and the app.log the install guide
    points users at would never exist). Route both cases to the logfile;
    leave real terminals (VD_CONSOLE debug builds, manual runs) alone."""
    if not getattr(sys, "frozen", False):
        return

    def _dead(stream) -> bool:
        if stream is None:
            return True
        try:
            return not stream.isatty()
        except Exception:  # noqa: BLE001 — a broken stream is a dead stream
            return True

    if not (_dead(sys.stdout) or _dead(sys.stderr)):
        return
    from backend.appdirs import logs_dir

    log_path = logs_dir() / "app.log"
    # Line-buffered append; survive a crashed previous run's partial line.
    log = open(log_path, "a", buffering=1, encoding="utf-8", errors="replace")
    if _dead(sys.stdout):
        sys.stdout = log
    if _dead(sys.stderr):
        sys.stderr = log


_redirect_std_streams()

from backend.main import main  # noqa: E402 — must follow the stream redirect

if __name__ == "__main__":
    main()
