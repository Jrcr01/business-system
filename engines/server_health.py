"""What went wrong on the server yesterday (owner, 2026-10-15, architecture review: "errors can fail silently"). The pages are written never to break
on a figure that cannot be worked out, so nobody is told; this reads the server's own log for the day before and counts the real errors, for one
line in the System admin's morning email when there are any. Only on the server (journalctl); anywhere else it answers None."""
from __future__ import annotations

import re
import subprocess
from datetime import date, timedelta

IGNORE = ("Control server error",)          # gunicorn's own harmless note on this server (its control socket has no home directory to use)


def errors_on(day: date) -> tuple[int, list[str]] | None:
    """(how many error lines, the few most common, shortened) for that day, or None where there is no server log to read."""
    try:
        out = subprocess.run(["journalctl", "-u", "slicks-erp", "--since", f"{day} 00:00", "--until", f"{day + timedelta(days=1)} 00:00", "--no-pager", "-q"],
                             capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return summarise(out)


def summarise(log: str) -> tuple[int, list[str]]:
    counts: dict[str, int] = {}
    for line in log.splitlines():
        if not ("ERROR" in line or "Traceback" in line) or any(k in line for k in IGNORE):
            continue
        text = re.sub(r"^.*?gunicorn\[\d+\]: ", "", line)
        text = re.sub(r"^\[[^\]]*\] \[\d+\] \[ERROR\] ", "", text).strip()
        counts[text[:90]] = counts.get(text[:90], 0) + 1
    top = [f"{t} (×{n})" if n > 1 else t for t, n in sorted(counts.items(), key=lambda kv: -kv[1])[:3]]
    return sum(counts.values()), top
