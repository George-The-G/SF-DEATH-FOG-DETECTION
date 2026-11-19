#!/usr/bin/env python3
"""On-demand, drift-free code outline and live contract inspector.

Parses Python AST (docstrings, `# @sec:`, preceding `#` comments) and JavaScript/GLSL
(`// @sec:`, `///`, `/** ... */`, top-level `function` / `export function` / `class`)
to output exact current line numbers without storing stale tables in git.

Usage:
    python tools/codemap.py                      # Repo-wide file + @sec summary
    python tools/codemap.py site/v3/app.js       # Detailed function & section index for a file/folder
    python tools/codemap.py microclimate/live    # Detailed index for a directory
    python tools/codemap.py --contracts          # Live feature switches (PROMOTED vs BETA) & .npz schemas
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCAN_DIRS = ("microclimate", "deathfog", "site/v3", "edge", "live_ci", "tools")
SEC_RE = re.compile(r"^\s*(?:#|//|/\*)\s*(?:@sec:\s*|-{3,}\s*)(.+?)\s*(?:-{3,}|\*/)?$")
JS_FUNC_RE = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?(?:function\s+([A-Za-z0-9_$]+)|class\s+([A-Za-z0-9_$]+)|(?:const|let)\s+([A-Za-z0-9_$]+)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>)"
)


def _first_line(text: str | None) -> str:
    if not text:
        return ""
    for line in text.strip().splitlines():
        s = line.strip().lstrip("/*# ").rstrip("*/ ").strip()
        if s:
            return s[:110]
    return ""


def _preceding_comment(lines: list[str], lineno_1based: int) -> str:
    idx = lineno_1based - 2
    collected: list[str] = []
    while idx >= 0:
        raw = lines[idx].strip()
        if not raw:
            break
        if raw.startswith(("#", "//", "/*", "*")):
            if "@sec:" not in raw:
                collected.append(raw)
            idx -= 1
        else:
            break
    collected.reverse()
    return _first_line("\n".join(collected))


def parse_python(path: Path) -> dict:
    src = path.read_text(encoding="utf-8", errors="replace")
    lines = src.splitlines()
    sections = []
    for i, line in enumerate(lines, 1):
        m = SEC_RE.match(line)
        if m:
            sections.append((i, m.group(1).strip()))
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return {"lines": len(lines), "doc": "", "sections": sections, "symbols": []}
    mod_doc = _first_line(ast.get_docstring(tree)) or _preceding_comment(lines, 2)
    symbols = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            kind = "class" if isinstance(node, ast.ClassDef) else "def"
            doc = _first_line(ast.get_docstring(node)) or _preceding_comment(lines, node.lineno)
            end = getattr(node, "end_lineno", node.lineno)
            symbols.append((node.lineno, end, f"{kind} {node.name}", doc))
    return {"lines": len(lines), "doc": mod_doc, "sections": sections, "symbols": symbols}


def parse_js(path: Path) -> dict:
    src = path.read_text(encoding="utf-8", errors="replace")
    lines = src.splitlines()
    sections = []
    symbols = []
    top_doc = ""
    for i, line in enumerate(lines[:15], 1):
        s = line.strip()
        if s.startswith(("//", "/*")) and "@sec:" not in s:
            top_doc = _first_line(s)
            if top_doc:
                break
    for i, line in enumerate(lines, 1):
        m = SEC_RE.match(line)
        if m:
            sections.append((i, m.group(1).strip()))
            continue
        fm = JS_FUNC_RE.match(line)
        if fm:
            name = fm.group(1) or fm.group(2) or fm.group(3)
            kind = "class" if fm.group(2) else "fn"
            doc = _preceding_comment(lines, i)
            symbols.append((i, i, f"{kind} {name}", doc))
    return {"lines": len(lines), "doc": top_doc, "sections": sections, "symbols": symbols}


def iter_source_files(target: Path | None = None) -> list[Path]:
    if target and target.is_file():
        return [target]
    roots = [target] if (target and target.is_dir()) else [ROOT / d for d in SCAN_DIRS if (ROOT / d).exists()]
    out = []
    for r in roots:
        for p in sorted(r.rglob("*")):
            if any(part in ("__pycache__", "node_modules", ".wrangler", "replay", "live", "live_beta", "data") for part in p.parts):
                continue
            if p.suffix in (".py", ".js", ".mjs") and p.is_file():
                out.append(p)
    return out


def print_overview(files: list[Path], detailed: bool = False) -> None:
    for p in files:
        rel = p.relative_to(ROOT).as_posix()
        info = parse_python(p) if p.suffix == ".py" else parse_js(p)
        hdr = f"{rel} ({info['lines']}L)"
        if info["doc"]:
            hdr += f" — {info['doc']}"
        print(hdr)
        for lineno, sec in info["sections"]:
            print(f"  [L{lineno}] @sec: {sec}")
        if detailed:
            for start, end, sym, doc in info["symbols"]:
                rng = f"L{start}-L{end}" if end > start else f"L{start}"
                suffix = f" — {doc}" if doc else ""
                print(f"    [{rng}] {sym}{suffix}")


def print_contracts() -> None:
    from microclimate.live import config as C
    from microclimate.paths import data_dir

    print("=== FEATURE SWITCHES (microclimate.live.config) ===")
    all_keys = sorted(set(C.PROMOTED) | set(C.BETA))
    for k in all_keys:
        p_val = C.PROMOTED.get(k, False)
        b_val = C.BETA.get(k, False)
        print(f"  {k:<18} promoted={str(p_val):<5}  beta={str(b_val):<5}")

    dd = data_dir()
    print(f"\n=== DATA ROOT ({dd}) ===")
    import numpy as np

    for rel_npz in ("live/live_static.npz", "live/live_tracer3d.npz", "live/live_extra.npz", "live/live_east.npz", "grids/solid_20m.npz"):
        np_path = dd / rel_npz
        if np_path.exists():
            try:
                z = np.load(np_path, allow_pickle=False, mmap_mode="r")
                cols = ", ".join(f"{k}:{tuple(z[k].shape)}:{z[k].dtype}" for k in z.files[:12])
                more = f" (+{len(z.files) - 12} more)" if len(z.files) > 12 else ""
                print(f"  {rel_npz}: {cols}{more}")
            except Exception as exc:
                print(f"  {rel_npz}: <{exc}>")

    for st_rel in ("site/v3/live/index.json", "site/v3/replay/index.json"):
        st_p = ROOT / st_rel
        if st_p.exists():
            try:
                obj = json.loads(st_p.read_text(encoding="utf-8"))
                print(f"  {st_rel} keys: {sorted(obj.keys())}")
            except Exception:
                pass


def main(argv: list[str]) -> int:
    if "--contracts" in argv:
        print_contracts()
        return 0
    args = [a for a in argv if not a.startswith("-")]
    if args:
        for a in args:
            t = (ROOT / a).resolve() if not Path(a).is_absolute() else Path(a)
            files = iter_source_files(t)
            print_overview(files, detailed=True)
    else:
        files = iter_source_files(None)
        print_overview(files, detailed=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
