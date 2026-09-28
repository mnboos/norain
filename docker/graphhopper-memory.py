#!/usr/bin/env python3
"""Reject GraphHopper JVM heaps that cannot safely fit in the container."""

import re
import sys
from pathlib import Path


GIB = 1024**3
MINIMUM_HEADROOM = 2 * GIB
CGROUP_LIMIT_PATHS = (
    Path("/sys/fs/cgroup/memory.max"),
    Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"),
)


def parse_java_size(value):
    """Parse the integral byte/KiB/MiB/GiB/TiB sizes accepted by -Xmx."""
    match = re.fullmatch(r"([1-9][0-9]*)([kmgt]?)", value.strip(), re.IGNORECASE)
    if not match:
        raise ValueError(f"Invalid JVM heap size: {value!r}")
    amount, suffix = match.groups()
    power = {"": 0, "k": 1, "m": 2, "g": 3, "t": 4}[suffix.lower()]
    return int(amount) * 1024**power


def cgroup_memory_limit(paths=CGROUP_LIMIT_PATHS):
    """Return the effective cgroup v2/v1 limit, or None when unlimited."""
    for path in paths:
        try:
            value = path.read_text().strip()
        except FileNotFoundError:
            continue
        if value == "max":
            return None
        try:
            limit = int(value)
        except ValueError as error:
            raise ValueError(f"Invalid cgroup memory limit in {path}: {value!r}") from error
        # cgroup v1 represents an unlimited value as a number close to INT64_MAX.
        return None if limit >= 2**60 else limit
    return None


def required_limit(heap):
    return heap + max(MINIMUM_HEADROOM, (heap + 9) // 10)


def format_gib(value):
    return f"{value / GIB:.1f}".rstrip("0").rstrip(".") + " GiB"


def check_memory(heap_text, limit=None):
    heap = parse_java_size(heap_text)
    if limit is None:
        limit = cgroup_memory_limit()
    if limit is None:
        return

    required = required_limit(heap)
    if limit < required:
        minimum_gib = (required + GIB - 1) // GIB
        raise ValueError(
            f"JVM heap {format_gib(heap)} needs a container memory limit of at least "
            f"{format_gib(required)} (heap plus max(2 GiB, 10%) native-memory "
            f"headroom), but the effective cgroup limit is {format_gib(limit)}. "
            f"Set GRAPHHOPPER_MEM_LIMIT to at least {minimum_gib}g."
        )


def main():
    if len(sys.argv) != 2:
        raise SystemExit(f"Usage: {Path(sys.argv[0]).name} JVM_HEAP")
    try:
        check_memory(sys.argv[1])
    except ValueError as error:
        raise SystemExit(f"GraphHopper memory check failed: {error}") from error


if __name__ == "__main__":
    main()
