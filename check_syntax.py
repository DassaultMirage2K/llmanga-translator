"""Syntax-check all project Python and JavaScript files.

- Python: compile() every .py under app/ plus the root-level scripts
  (main.py, service.py, viewer.py). Catches syntax errors without importing.
- JavaScript: node --check on each static/js/*.js as an ES module — the file
  is copied to a temp *.mjs so import/export syntax is accepted by node.

Usage:
    .venv\\Scripts\\python.exe check_syntax.py     (Windows)
    uv run python check_syntax.py                  (any OS, from repo root)

Exit code: 0 = clean, 1 = at least one error.
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def collect_py() -> list[Path]:
    files: list[Path] = []
    app_dir = ROOT / "app"
    if app_dir.is_dir():
        files += sorted(app_dir.rglob("*.py"))
    for name in ("main.py", "service.py", "viewer.py"):
        p = ROOT / name
        if p.exists():
            files.append(p)
    return [f for f in files if "__pycache__" not in f.parts]


def collect_js() -> list[Path]:
    js_dir = ROOT / "static" / "js"
    return sorted(js_dir.glob("*.js")) if js_dir.is_dir() else []


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def main() -> int:
    errors = 0
    checked = 0

    # ---- Python ------------------------------------------------------------
    for path in collect_py():
        checked += 1
        try:
            src = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as e:
            print(f"FAIL py   {rel(path)}: unreadable ({e})")
            errors += 1
            continue
        try:
            compile(src, str(path), "exec")
            print(f"OK   py    {rel(path)}")
        except SyntaxError as e:
            errors += 1
            print(f"FAIL py    {rel(path)}:{e.lineno}: {e.msg}")

    # ---- JavaScript (ES modules via node --check) --------------------------
    js_files = collect_js()
    node = shutil.which("node")
    if not js_files:
        print("WARN no static/js/*.js found — nothing to check")
    elif node is None:
        print(f"WARN node not on PATH — skipped {len(js_files)} JS file(s)")
    else:
        with tempfile.TemporaryDirectory(prefix="manga-jschk-") as td:
            for path in js_files:
                checked += 1
                tmp = Path(td) / f"{path.stem}.mjs"
                tmp.write_bytes(path.read_bytes())
                proc = subprocess.run(
                    [node, "--check", str(tmp)], capture_output=True, text=True
                )
                if proc.returncode == 0:
                    print(f"OK   js    {rel(path)}")
                else:
                    errors += 1
                    out = (proc.stderr or proc.stdout).strip()
                    lines = [l for l in out.splitlines() if l.strip()]
                    # Point the error at the real file, not the temp copy.
                    fixed = [l.replace(str(tmp), rel(path)) for l in lines[:4]]
                    print(f"FAIL js    {rel(path)}:")
                    for l in fixed:
                        print(f"           {l}")

    print(f"\n{checked} file(s) checked, {errors} error(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
