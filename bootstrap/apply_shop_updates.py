"""Apply additive ShoppingMall updates needed after pulling recent changes.

This script is intentionally small and safe to run repeatedly. `Web_Starter.exe`
calls the Node automation before starting servers, and the automation calls this
script after DB verification so teammates can pull changes and start the project
without remembering extra ShoppingMall maintenance commands.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from _venv_utils import _venv_python

ROOT = Path(__file__).resolve().parents[1]
SHOP_BACKEND = ROOT / "shopping_mall" / "backend"


def _run_shop_code(label: str, code: str) -> None:
    python_exe = _venv_python(SHOP_BACKEND)
    print(f"[apply_shop_updates] start: {label} (python={python_exe})", flush=True)
    env = os.environ.copy()
    pythonpath_parts = [str(ROOT)]
    if existing := env.get("PYTHONPATH"):
        pythonpath_parts.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    result = subprocess.run([python_exe, "-c", code], cwd=str(SHOP_BACKEND), env=env)
    if result.returncode != 0:
        print(
            f"[apply_shop_updates] failed: {label} (exit={result.returncode})",
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(result.returncode)
    print(f"[apply_shop_updates] done: {label}", flush=True)


SHOP_PRODUCT_IMAGE_CODE = """
from scripts.update_product_images import update_product_images

updated = update_product_images()
print(f"[apply_shop_updates] product image URLs updated: {updated}")
"""


def main() -> int:
    print("[apply_shop_updates] ShoppingMall post-update hooks start", flush=True)
    _run_shop_code("product image URL refresh", SHOP_PRODUCT_IMAGE_CODE)
    print("[apply_shop_updates] ShoppingMall post-update hooks done", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
