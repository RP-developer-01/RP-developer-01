"""Збір даних з GitHub API. Назви приватних репо нікуди не логуються (логи Actions публічні)."""
from __future__ import annotations

import json
import re
import sys
import time
import tomllib
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from render import Repo

API = "https://api.github.com"

# пакет / файл → назва вузла. Невідомі пакети ігноруються, щоб граф лишався чистим.
FRAMEWORKS: dict[str, str] = {
    # python
    "aiogram": "aiogram", "python-telegram-bot": "PTB", "pytelegrambotapi": "telebot", "telethon": "Telethon", "pyrogram": "Pyrogram",
    "fastapi": "FastAPI", "flask": "Flask", "django": "Django", "aiohttp": "aiohttp", "sqlalchemy": "SQLAlchemy",
    "pandas": "pandas", "numpy": "NumPy", "ccxt": "ccxt", "ib-insync": "IBKR API", "ib-async": "IBKR API", "ibapi": "IBKR API",
    "discord-py": "Discord", "discord-py-self": "Discord", "gspread": "Google Sheets", "firebase-admin": "Firebase",
    "playwright": "Playwright", "selenium": "Selenium", "openai": "OpenAI", "anthropic": "Claude API",
    # js
    "firebase": "Firebase", "react": "React", "vue": "Vue", "svelte": "Svelte", "next": "Next.js",
    "@tauri-apps/api": "Tauri", "@tauri-apps/cli": "Tauri", "electron": "Electron", "tailwindcss": "Tailwind",
    "express": "Express", "telegraf": "Telegraf", "grammy": "grammY", "chart.js": "Chart.js", "three": "three.js",
    # rust
    "tauri": "Tauri", "tokio": "Tokio",
}
MARKER_FILES: dict[str, str] = {
    "Dockerfile": "Docker", "docker-compose.yml": "Docker", "docker-compose.yaml": "Docker", "compose.yaml": "Docker",
    "firebase.json": "Firebase", ".firebaserc": "Firebase", "appsscript.json": "Apps Script", "src-tauri": "Tauri",
}
IGNORE_LANGS = {"Dockerfile", "Procfile", "Makefile", "Batchfile", "Jupyter Notebook"}
DEP_FILES = ("requirements.txt", "requirements-dev.txt", "pyproject.toml", "package.json", "Cargo.toml")


class GitHub:
    def __init__(self, token: str):
        self.token = token
        self.calls = 0

    def req(self, path: str, raw: bool = False) -> tuple[int, Any, dict[str, str]]:
        url = path if path.startswith("http") else API + path
        r = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "profile-graph"})
        self.calls += 1
        try:
            with urllib.request.urlopen(r, timeout=30) as resp:
                body = resp.read().decode()
                data = body if raw else (json.loads(body) if body else None)
                return resp.status, data, dict(resp.headers)
        except urllib.error.HTTPError as e:
            return e.code, None, dict(e.headers or {})

    def paged(self, path: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []; page = 1
        while True:
            sep = "&" if "?" in path else "?"
            st, data, _ = self.req(f"{path}{sep}per_page=100&page={page}")
            if st != 200 or not data:
                return out if out or st == 200 else []
            out += data; page += 1
            if len(data) < 100:
                return out


def _norm(pkg: str) -> str:
    return re.split(r"[<>=!~\[;\s@]", pkg.strip(), maxsplit=1)[0].lower().replace("_", "-").replace(".", "-") if not pkg.startswith("@") else pkg.lower()


def parse_deps(fname: str, text: str) -> set[str]:
    names: set[str] = set()
    try:
        if fname.startswith("requirements"):
            for line in text.splitlines():
                line = line.split("#")[0].strip()
                if line and not line.startswith("-"):
                    names.add(_norm(line))
        elif fname == "pyproject.toml":
            t = tomllib.loads(text)
            names |= {_norm(d) for d in t.get("project", {}).get("dependencies", [])}
            names |= {_norm(k) for k in t.get("tool", {}).get("poetry", {}).get("dependencies", {})}
        elif fname == "package.json":
            j = json.loads(text)
            names |= {k.lower() for k in {**j.get("dependencies", {}), **j.get("devDependencies", {})}}
        elif fname == "Cargo.toml":
            names |= {k.lower() for k in tomllib.loads(text).get("dependencies", {})}
    except (ValueError, tomllib.TOMLDecodeError):
        pass
    return names


def detect_stack(gh: GitHub, full: str, extra_map: dict[str, str]) -> list[str]:
    st, root, _ = gh.req(f"/repos/{full}/contents/")
    if st != 200 or not isinstance(root, list):
        return []
    names = {x["name"] for x in root}
    stack: set[str] = {v for k, v in MARKER_FILES.items() if k in names}
    fmap = {**FRAMEWORKS, **{k.lower(): v for k, v in extra_map.items()}}
    for f in DEP_FILES:
        if f in names:
            st, text, _ = gh.req(f"/repos/{full}/contents/{f}", raw=True)
            if st == 200 and text:
                stack |= {fmap[p] for p in parse_deps(f, text) if p in fmap}
    return sorted(stack)


def commit_count(gh: GitHub, full: str) -> int:
    st, data, hdr = gh.req(f"/repos/{full}/commits?per_page=1")
    if st != 200:
        return 0
    m = re.search(r'[?&]page=(\d+)>; rel="last"', hdr.get("Link", "") or hdr.get("link", ""))
    return int(m.group(1)) if m else len(data or [])


def weekly(gh: GitHub, full: str) -> list[int]:
    for _ in range(5):  # GitHub рахує статистику асинхронно: перший запит часто 202
        st, data, _ = gh.req(f"/repos/{full}/stats/commit_activity")
        if st == 200 and isinstance(data, list):
            return [w["total"] for w in data][-26:] if data else [0] * 26
        if st != 202:
            break
        time.sleep(3)
    return [0] * 26


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def classify(cfg: dict[str, Any], topics: list[str], stack: list[str], langs: dict[str, int]) -> str | None:
    """Напрямок: topic domain-xxx → правила за стеком → default_domain."""
    domains = list(cfg["domains"])
    by_slug = {slug(d): d for d in domains}
    for t in topics:
        if t.startswith("domain-") and t[7:] in by_slug:
            return by_slug[t[7:]]
    tot = sum(langs.values()) or 1
    have = {s.lower() for s in stack} | {l.lower() for l, b in langs.items() if b / tot >= .2} | {t.lower() for t in topics}
    best, score = None, 0
    for dom in domains:  # більше збігів — перемагає; при нічиїй — той, що вище в списку
        sc = sum(1 for k in (cfg.get("domain_rules") or {}).get(dom, []) if str(k).lower() in have)
        if sc > score:
            best, score = dom, sc
    return best or cfg.get("default_domain")


def fetch(cfg: dict[str, Any], token: str, user: str, private_cfg: dict[str, Any] | None = None) -> list[Repo]:
    gh = GitHub(token)
    repos = gh.paged("/user/repos?affiliation=owner&visibility=all&sort=pushed") if cfg.get("include_private", True) else []
    if not repos:  # без PAT (тільки GITHUB_TOKEN) — лише публічні
        print("private access unavailable -> public repos only (check GRAPH_TOKEN)")
        repos = gh.paged(f"/users/{user}/repos?type=owner&sort=pushed")
    public_ov: dict[str, dict[str, Any]] = cfg.get("repos") or {}
    private_ov: dict[str, dict[str, Any]] = private_cfg or {}
    exclude = {e.lower() for e in cfg.get("exclude", [])} | {user.lower()}
    now = datetime.now(timezone.utc)
    out: list[Repo] = []; unclassified_public: list[str] = []; unclassified_private = 0
    for r in repos:
        name = r["name"]; priv = bool(r.get("private"))
        ov = {**public_ov.get(name, {}), **private_ov.get(name, {})}
        if name.lower() in exclude or ov.get("hide") or (r.get("fork") and not cfg.get("include_forks")) or r.get("archived") and not cfg.get("include_archived"):
            continue
        if priv and not cfg.get("include_private", True):
            continue
        full = r["full_name"]
        _, langs, _ = gh.req(f"/repos/{full}/languages")
        langs = {k: v for k, v in (langs or {}).items() if k not in IGNORE_LANGS}
        stack = sorted(set(detect_stack(gh, full, cfg.get("frameworks") or {})) | set(ov.get("stack", [])))
        dom = ov.get("domain") or classify(cfg, r.get("topics", []), stack, langs)
        if dom not in cfg["domains"]:
            if priv: unclassified_private += 1
            else: unclassified_public.append(name)
            continue
        if ov.get("about"):
            about = ov["about"]
        elif priv:  # опис приватного репо з GitHub — лише якщо явно дозволено
            about = (r.get("description") or "") if cfg.get("private_about_from_github") else ""
        else:
            about = r.get("description") or ""
        if len(about) > 34:
            about = about[:33].rsplit(" ", 1)[0] + "…"
        pushed = datetime.fromisoformat(r["pushed_at"].replace("Z", "+00:00")) if r.get("pushed_at") else now
        out.append(Repo(name=ov.get("title", name), private=priv, domain=dom, about=about, langs=langs, stack=stack,
                        commits=commit_count(gh, full), pushed_days=(now - pushed).days, weekly=weekly(gh, full)))
    # у лог — тільки кількості й ПУБЛІЧНІ назви (логи Actions публічного репо бачать усі)
    print(f"in graph: {len(out)} repos ({sum(x.private for x in out)} private); API calls: {gh.calls}")
    for d in cfg["domains"]:
        print(f"  {d}: {sum(x.domain == d for x in out)}")
    if unclassified_public:
        print(f"no domain (public): {', '.join(unclassified_public)}  -> add domain_rules or topic domain-...")
    if unclassified_private:
        print(f"no domain (private): {unclassified_private}  -> add domain_rules or GRAPH_PRIVATE")
    return out
