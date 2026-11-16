"""Synchronize human-editable default policy into distribution data."""

import shutil
from pathlib import Path

root = Path(__file__).resolve().parent.parent
source = root / "packs" / "general-agent"
target = root / "src" / "data" / "general-agent"
if target.exists():
    shutil.rmtree(target)
shutil.copytree(source, target, ignore=shutil.ignore_patterns(".DS_Store", "__pycache__"))
print("Synchronized general-agent package data")
