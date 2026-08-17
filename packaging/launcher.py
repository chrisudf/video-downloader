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
    """Windowed (no-console) builds have no usable stdout/stderr. Anything
    that writes to them — our prints, uvicorn's log handlers — would crash
    with AttributeError on None. Route both to a logfile instead, which is
    also where users can look when 'nothing happened'."""
    if not getattr(sys, "frozen", False):
        return
    if sys.stdout is not None and sys.stderr is not None:
        return
    from backend.appdirs import logs_dir

    log_path = logs_dir() / "app.log"
    # Line-buffered append; survive a crashed previous run's partial line.
    stream = open(log_path, "a", buffering=1, encoding="utf-8", errors="replace")
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream


_redirect_std_streams()

from backend.main import main  # noqa: E402 — must follow the stream redirect

if __name__ == "__main__":
    main()
