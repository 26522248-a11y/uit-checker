from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def main() -> int:
    raw_path = os.getenv("STATE_DB_PATH", "./data/state.db")
    db_path = Path(raw_path)
    if not db_path.is_absolute():
        db_path = BASE_DIR / db_path
    heartbeat = db_path.parent / "heartbeat"
    if not heartbeat.exists():
        print(f"heartbeat not found: {heartbeat}")
        return 1
    try:
        age = datetime.now().astimezone() - datetime.fromisoformat(
            heartbeat.read_text(encoding="utf-8").strip()
        )
    except Exception as exc:
        print(f"cannot read heartbeat: {exc}")
        return 1
    if age > timedelta(minutes=5):
        print(f"heartbeat too old: {age}")
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
