"""
Minimal zero-dependency test runner.

The tests are written to be pytest-compatible (plain `test_*` functions with
`assert`), so `pytest` works if installed. This runner lets them also run in a
bare environment with no third-party packages:

    python -m tests.run_tests
    python tests/run_tests.py
"""
from __future__ import annotations

import importlib
import os
import sys
import traceback

# Make src/ importable when run directly.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))


def discover_test_modules() -> list[str]:
    here = os.path.dirname(os.path.abspath(__file__))
    mods = []
    for fn in sorted(os.listdir(here)):
        if fn.startswith("test_") and fn.endswith(".py"):
            mods.append("tests." + fn[:-3])
    return mods


def run() -> int:
    sys.path.insert(0, ROOT)
    passed = failed = 0
    failures: list[str] = []
    for modname in discover_test_modules():
        mod = importlib.import_module(modname)
        for name in sorted(dir(mod)):
            if not name.startswith("test_"):
                continue
            fn = getattr(mod, name)
            if not callable(fn):
                continue
            try:
                fn()
                passed += 1
                print(f"  PASS  {modname}.{name}")
            except Exception:  # noqa: BLE001
                failed += 1
                failures.append(f"{modname}.{name}\n{traceback.format_exc()}")
                print(f"  FAIL  {modname}.{name}")
    print(f"\n{passed} passed, {failed} failed")
    for f in failures:
        print("\n" + "=" * 60 + "\n" + f)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(run())
