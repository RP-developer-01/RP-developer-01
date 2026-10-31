"""Точка входу: python graph/build.py  (env: GH_TOKEN, GH_USER)."""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from fetch import fetch  # noqa: E402
from render import Data, render_all  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    cfg = yaml.safe_load((ROOT / "graph" / "graph.yml").read_text(encoding="utf-8"))
    token, user = os.environ["GH_TOKEN"], os.environ.get("GH_USER") or cfg["user"]
    raw_private = os.environ.get("GRAPH_PRIVATE", "").strip()
    try:
        private_cfg = yaml.safe_load(raw_private) if raw_private else {}
    except yaml.YAMLError:
        sys.exit("GRAPH_PRIVATE: invalid YAML")  # вміст секрету не друкуємо
    repos = fetch(cfg, token, user, private_cfg or {})
    if not repos:
        sys.exit("no repos to show - check graph.yml (domains, domain_rules, unsorted.show)")
    lim = cfg.get("limits", {}); uns = cfg.get("unsorted") or {}
    data = Data(center=cfg.get("center", user), domains=cfg["domains"], repos=repos, ghost=cfg.get("ghost", ""),
                updated=date.today(), min_lang_share=lim.get("min_lang_share", .15), max_tech=lim.get("max_tech", 18),
                max_slots=lim.get("highlight_slots", 12), max_projects=lim.get("max_projects", 24),
                unsorted_label=uns.get("label", "{lang} · other"), max_groups=uns.get("max_groups", 3),
                private_labels=cfg.get("private_labels", "none"), highlight_dim=float(cfg.get("highlight_dim", .68)))
    for p in render_all(data, ROOT / cfg.get("output", "assets")):
        print("wrote", p.relative_to(ROOT), f"{p.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
