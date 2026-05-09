"""One-shot migration: move teacher photos from <repo>/school-bot/assets/teachers_uploaded
to <repo>/assets/teachers_uploaded so they match the path stored in the DB
(`assets/teachers_uploaded/<file>`) and the BOT_DIR resolution used by school-bot.

Idempotent: skips files that already exist at the destination, leaves the source
file in place if a destination collision is detected.
"""

from __future__ import annotations

import shutil
from pathlib import Path


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    src = repo_root / "school-bot" / "assets" / "teachers_uploaded"
    dst = repo_root / "assets" / "teachers_uploaded"

    if not src.exists():
        print(f"Nothing to migrate: {src} does not exist.")
        return 0

    dst.mkdir(parents=True, exist_ok=True)

    moved = 0
    skipped = 0
    collisions = 0

    for entry in src.iterdir():
        if not entry.is_file():
            continue
        target = dst / entry.name
        if target.exists():
            if entry.stat().st_size == target.stat().st_size:
                skipped += 1
                continue
            collisions += 1
            print(f"COLLISION (different size, skipped): {entry} vs {target}")
            continue
        shutil.move(str(entry), str(target))
        print(f"moved: {entry.name}")
        moved += 1

    print(f"Done. moved={moved} already_present={skipped} collisions={collisions}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
