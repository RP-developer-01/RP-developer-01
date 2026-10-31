"""Рендер орбітального графа проєктів і картки статистики в SVG (світла/темна тема)."""
from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from html import escape
from pathlib import Path



@dataclass
class Repo:
    name: str                       # для приватних НЕ потрапляє в SVG
    private: bool
    domain: str | None              # None → «без напрямку», потрапляє в окрему групу
    about: str
    langs: dict[str, int]           # байти по мовах
    stack: list[str]                # фреймворки/сервіси
    commits: int
    pushed_days: int
    weekly: list[int] = field(default_factory=lambda: [0] * 26)


@dataclass
class Data:
    center: str
    domains: dict[str, str]         # назва напрямку → ключ кольору
    repos: list[Repo]
    ghost: str = ""
    updated: date = field(default_factory=date.today)
    min_lang_share: float = 0.05
    max_tech: int = 18
    max_slots: int = 12
    max_projects: int = 16
    unsorted_label: str = "{lang} · other"
    max_groups: int = 3


W, H = 960, 600
CX, CY = W / 2, H / 2
RINGS = {"hub": (122, 74), "project": (232, 146), "tech": (392, 236)}
FONT = "-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif"
EASE = "0.42 0 0.58 1"

THEMES: dict[str, dict[str, str]] = {
    "dark": dict(bg="#17171a", bg2="#1c1c20", guide="#2b2b31", link="#33333a", node="#8a8a94", tech="#5d5d67", text="#d6d6de",
                 dim="#82828c", faint="#56565f", acc="#a78bfa", acc2="#ddd3ff",
                 amber="#d9a35b", sky="#6aa8d8", green="#6cbf95", rose="#d98395", teal="#5bbcb8", orange="#e08a5a", lime="#a3c45a", violet="#a78bfa"),
    "light": dict(bg="#ffffff", bg2="#fbfbfc", guide="#ececf0", link="#dddde3", node="#6b6b75", tech="#a9a9b3", text="#26262c",
                  dim="#6e6e78", faint="#b4b4bc", acc="#7c3aed", acc2="#4c1d95",
                  amber="#b7791f", sky="#2b6cb0", green="#2f855a", rose="#b83256", teal="#23827e", orange="#c05621", lime="#5f7f1c", violet="#6d28d9"),
}
LANG_COLORS: dict[str, dict[str, str]] = {
    "dark": {"Python": "#5b9bd5", "JavaScript": "#e8d44d", "TypeScript": "#4f8fd6", "HTML": "#e8704a", "CSS": "#9f7ae0", "Rust": "#d9a07a",
             "Shell": "#89c95a", "Go": "#4fc3d9", "Java": "#c8935a", "C#": "#5fae5f", "C++": "#e0608a", "PHP": "#8892c4", "Kotlin": "#a97bff",
             "Dart": "#4fc3c0", "Vue": "#55c08a", "Svelte": "#ff6a3d", "Lua": "#6f6fe0", "Other": "#5d5d67"},
    "light": {"Python": "#3572A5", "JavaScript": "#c9b200", "TypeScript": "#3178c6", "HTML": "#e34c26", "CSS": "#663399", "Rust": "#b5703a",
              "Shell": "#4e8a1d", "Go": "#00a0c4", "Java": "#b07219", "C#": "#178600", "C++": "#c2185b", "PHP": "#4F5D95", "Kotlin": "#7F52FF",
              "Dart": "#00838f", "Vue": "#2c8a5c", "Svelte": "#e04a1a", "Lua": "#000080", "Other": "#a9a9b3"},
}


def _ago(d: int) -> str:
    return "today" if d <= 0 else f"{d}d ago" if d < 30 else f"{d // 30}mo ago" if d < 365 else f"{d // 365}y ago"


class Graph:
    """Підготовка графа і геометрії — спільна для обох тем."""

    def __init__(self, data: Data):
        self.d = data
        self.repos = sorted(data.repos, key=lambda r: -r.commits)[: data.max_projects]
        self.domains = [k for k in data.domains if any(r.domain == k for r in self.repos)]
        # репо без напрямку → вільні групи за основною мовою (не з'єднані з центром)
        self.group_of: dict[int, str] = {}
        loose = [r for r in self.repos if r.domain not in data.domains]
        prim = {id(r): (max(r.langs, key=lambda l: r.langs[l]) if r.langs else "misc") for r in loose}
        counts: dict[str, int] = {}
        for r in loose:
            counts[prim[id(r)]] = counts.get(prim[id(r)], 0) + 1
        top = sorted(counts, key=lambda l: (-counts[l], l))[: data.max_groups]
        for r in loose:
            self.group_of[id(r)] = prim[id(r)] if prim[id(r)] in top else "misc"
        self.groups = sorted(set(self.group_of.values()), key=lambda gname: (gname == "misc", -counts.get(gname, 0), gname))
        self.lang_bytes: dict[str, int] = {}
        self.tech_repos: dict[str, list[Repo]] = {}
        self.is_lang: dict[str, bool] = {}
        for r in self.repos:
            tot = sum(r.langs.values()) or 1
            for lang, b in r.langs.items():
                self.lang_bytes[lang] = self.lang_bytes.get(lang, 0) + b
                if b / tot >= data.min_lang_share:
                    self.tech_repos.setdefault(lang, []).append(r); self.is_lang[lang] = True
            for s in r.stack:
                if r not in self.tech_repos.setdefault(s, []):
                    self.tech_repos[s].append(r)
                self.is_lang.setdefault(s, False)
        # обмеження кількості технологій: лишаємо найуживаніші
        ranked = sorted(self.tech_repos, key=lambda t: (-len(self.tech_repos[t]), -self.lang_bytes.get(t, 0), t))
        self.techs = ranked[: data.max_tech]
        self.all_bytes = sum(self.lang_bytes.values()) or 1

        self.nodes: list[str] = []
        self.kind: dict[str, str] = {}
        self.edges: list[tuple[str, str]] = []
        self.repo_of: dict[str, Repo] = {}

        def add(key: str, kind: str) -> None:
            self.nodes.append(key); self.kind[key] = kind
        add("me", "me")
        for dm in self.domains:
            add(f"d:{dm}", "hub"); self.edges.append(("me", f"d:{dm}"))
        for gname in self.groups:
            add(f"g:{gname}", "group")
        for i, r in enumerate(self.repos):
            key = f"p:{i}"; add(key, "project"); self.repo_of[key] = r
            self.edges.append((self.parent(r), key))
        for t in self.techs:
            add(f"t:{t}", "tech")
            for r in self.tech_repos[t]:
                self.edges.append((f"p:{self.repos.index(r)}", f"t:{t}"))
        self.nb: dict[str, list[str]] = {n: [] for n in self.nodes}
        for a, b in self.edges:
            self.nb[a].append(b); self.nb[b].append(a)
        self._layout()

    def parent(self, r: Repo) -> str:
        return f"g:{self.group_of[id(r)]}" if id(r) in self.group_of else f"d:{r.domain}"

    def loose(self, k: str) -> bool:
        """Вузол належить вільній групі (проєкт без напрямку або сама група)."""
        return self.kind[k] == "group" or (self.kind[k] == "project" and id(self.repo_of[k]) in self.group_of)

    # ---------- геометрія ----------
    def _layout(self) -> None:
        ang: dict[str, float] = {"me": 0.0}
        parents = [f"d:{dm}" for dm in self.domains] + [f"g:{gn}" for gn in self.groups]
        kids = {p: [k for k in self.nodes if self.kind[k] == "project" and self.parent(self.repo_of[k]) == p] for p in parents}
        weight = {p: len(kids[p]) + 1.0 for p in parents}   # ширина сектора ∝ кількості проєктів
        total = sum(weight.values()) or 1; cur = -180.0
        for p in parents:
            sector = 360 * weight[p] / total; a = cur + sector / 2; cur += sector
            ang[p] = a; ps = kids[p]
            step = min(30.0, sector * .8 / max(len(ps), 1))
            for j, k in enumerate(ps):
                ang[k] = a + (j - (len(ps) - 1) / 2) * step
        projects = [k for k in self.nodes if self.kind[k] == "project"]
        self._relax(ang, projects, min(15.0, 360 / max(len(projects), 1) * .9))
        if self.d.ghost and projects:
            srt = sorted(projects, key=lambda k: ang[k] % 360)
            gaps = [((ang[srt[(i + 1) % len(srt)]] - ang[k]) % 360 or 360, k) for i, k in enumerate(srt)]
            # серед найбільших проміжків — той, що ближче до лівого/правого краю (там більше місця)
            g, k = max(gaps, key=lambda gk: (round(gk[0], 1), abs(math.cos(math.radians(ang[gk[1]] + gk[0] / 2)))))
            ang["ghost"] = ang[k] + g / 2
        techs = [k for k in self.nodes if self.kind[k] == "tech"]
        for t in techs:
            ps = [ang[p] for p in self.nb[t]]
            ang[t] = math.degrees(math.atan2(sum(math.sin(math.radians(a)) for a in ps), sum(math.cos(math.radians(a)) for a in ps)))
        self._relax(ang, techs, min(19.5, 360 / max(len(techs), 1) * .92))
        self.ang = ang
        self.pos: dict[str, tuple[float, float]] = {}
        for k in list(self.nodes) + (["ghost"] if "ghost" in ang else []):
            if k == "me":
                self.pos[k] = (CX, CY); continue
            ring = "project" if k == "ghost" else "hub" if self.kind[k] == "group" else self.kind[k]
            rx, ry = RINGS[ring]; a = math.radians(ang[k])
            self.pos[k] = (CX + rx * math.cos(a), CY + ry * math.sin(a))

    @staticmethod
    def _relax(ang: dict[str, float], keys: list[str], gap: float) -> None:
        if len(keys) < 2:
            return
        for _ in range(3000):
            order = sorted(keys, key=lambda k: ang[k] % 360); moved = False
            for i in range(len(order)):
                a, b = order[i], order[(i + 1) % len(order)]
                diff = (ang[b] - ang[a]) % 360
                if diff < gap - 1e-6:
                    push = min((gap - diff) / 2, 1.5); ang[a] -= push; ang[b] += push; moved = True
            if not moved:
                return

    def radius(self, k: str) -> float:
        kind = self.kind[k]
        if kind == "me": return 9
        if kind == "hub": return 6
        if kind == "group": return 5
        if kind == "project":
            c = self.repo_of[k].commits; return 6.5 if c >= 200 else 5.2 if c >= 100 else 4.2
        m = len(self.nb[k]); return 4.6 if m >= 3 else 3.6 if m == 2 else 2.8

    def label(self, k: str) -> str:
        kind = self.kind[k]
        if kind == "me": return self.d.center
        if kind == "hub": return k[2:]
        if kind == "group": return self.d.unsorted_label.format(lang=k[2:])
        if kind == "tech": return k[2:]
        name = self.repo_of[k].name
        return name if len(name) <= 18 else name[:17] + "…"

    def label_spec(self, k: str) -> tuple[float, float, str, int]:
        x, y = self.pos[k]; r = 7 if k == "ghost" else self.radius(k)
        if k == "me": return x, y + r + 18, "middle", 1
        a = math.radians(self.ang[k]); c, s = math.cos(a), math.sin(a)
        if c > .42: return x + r + 7, y + 4, "start", 1
        if c < -.42: return x - r - 7, y + 4, "end", 1
        return (x, y - r - 8, "middle", -1) if s < 0 else (x, y + r + 15, "middle", 1)

    def slots(self) -> list[str]:
        ps = [k for k in self.nodes if self.kind[k] == "project"]
        ts = sorted((k for k in self.nodes if self.kind[k] == "tech"), key=lambda t: (-len(self.nb[t]), -self.lang_bytes.get(t[2:], 0)))
        out: list[str] = []
        for i in range(max(len(ps), len(ts))):
            if i < len(ps): out.append(ps[i])
            if i < len(ts): out.append(ts[i])
        return out[: self.d.max_slots]


class Drift:
    def __init__(self, rnd: random.Random, amp: float, n: int = 36):
        self.T = rnd.uniform(22, 34); p1, p2 = rnd.uniform(0, math.tau), rnd.uniform(0, math.tau)
        self.pts = [(amp * math.sin(math.tau * i / n + p1), amp * .7 * math.sin(2 * math.tau * i / n + p2)) for i in range(n + 1)]

    def g(self) -> str:
        return (f'<animateTransform attributeName="transform" type="translate" dur="{self.T:.1f}s" repeatCount="indefinite" '
                f'values="{";".join(f"{x:.1f} {y:.1f}" for x, y in self.pts)}"/>')

    def a(self, attr: str, base: float, ax: int) -> str:
        return f'<animate attributeName="{attr}" dur="{self.T:.1f}s" repeatCount="indefinite" values="{";".join(f"{base + p[ax]:.1f}" for p in self.pts)}"/>'


def _mask_words(name: str) -> list[int]:
    """Довжини «плашок» для приватної назви: стабільні, але НЕ залежать від довжини назви."""
    rnd = random.Random(hashlib.sha256(name.encode()).hexdigest())
    return [rnd.randint(4, 8) for _ in range(rnd.choice((1, 2, 2)))]


def graph_svg(g: Graph, theme: str) -> str:
    c = THEMES[theme]; d = g.d
    rnd = random.Random(5)
    amp = {"me": 1.2, "hub": 2.2, "group": 2.6, "project": 2.8, "tech": 3.2}
    keys = list(g.pos)
    DR = {k: Drift(rnd, amp["project" if k == "ghost" else g.kind[k]]) for k in keys}
    IDX = {k: i for i, k in enumerate(keys)}
    EID = {frozenset((a, b)): (f"e{IDX[a]}_{IDX[b]}", a) for a, b in g.edges}
    SEQ = g.slots()
    FADE, HOLD, IDLE, START = .7, 2.9, .9, 3.0
    SLOT = 2 * FADE + HOLD + IDLE; CYCLE = SLOT * max(len(SEQ), 1)

    def anim(kt: list[float], vs: list[str]) -> str:
        pk: list[float] = []; pv: list[str] = []
        for k, v in zip(kt, vs):
            if pk and abs(k - pk[-1]) < 1e-6: pv[-1] = v; continue
            pk.append(k); pv.append(v)
        return (f'dur="{CYCLE:.2f}s" begin="{START}s" repeatCount="indefinite" calcMode="spline" keyTimes="{";".join(f"{k / CYCLE:.5f}" for k in pk)}" '
                f'values="{";".join(pv)}" keySplines="{";".join([EASE] * (len(pk) - 1))}"')

    def pulse(i: int) -> str:
        a = i * SLOT; return anim([0, a, a + FADE, a + FADE + HOLD, a + 2 * FADE + HOLD, CYCLE], ["0", "0", "1", "1", "0", "0"])

    def dim(lo: str) -> str:
        kt, vs = [0.0], ["1"]
        for i in range(len(SEQ)):
            a = i * SLOT; kt += [a, a + FADE, a + FADE + HOLD, a + 2 * FADE + HOLD]; vs += ["1", lo, lo, "1"]
        return anim(kt + [CYCLE], vs + ["1"])

    def dom_col(k: str) -> str:
        if g.loose(k):
            return c["node"]
        dm = k[2:] if g.kind[k] == "hub" else g.repo_of[k].domain
        return c.get(d.domains.get(dm or "", ""), c["node"])

    def ncol(k: str) -> str:
        kind = g.kind[k]
        return c["acc"] if kind == "me" else c["tech"] if kind == "tech" else dom_col(k)

    def focus_col(k: str) -> str:
        return dom_col(k) if g.kind[k] == "project" and not g.loose(k) else c["acc"]

    halo = f'stroke="{c["bg2"]}" stroke-width="4" stroke-linejoin="round" paint-order="stroke"'
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" font-family="{FONT}">']
    defs = [f'''<radialGradient id="bg" cx="50%" cy="50%" r="65%"><stop offset="0" stop-color="{c["bg2"]}"/><stop offset="1" stop-color="{c["bg"]}"/></radialGradient>
<radialGradient id="mehalo"><stop offset="0" stop-color="{c["acc"]}" stop-opacity=".34"/><stop offset="1" stop-color="{c["acc"]}" stop-opacity="0"/></radialGradient>
<linearGradient id="shim" x1="0" x2="1"><stop offset="0" stop-color="{c["text"]}" stop-opacity="0"/><stop offset=".5" stop-color="{c["text"]}" stop-opacity=".55"/><stop offset="1" stop-color="{c["text"]}" stop-opacity="0"/>
<animateTransform attributeName="gradientTransform" type="translate" values="-1 0;1.6 0" dur="2.8s" repeatCount="indefinite" calcMode="spline" keyTimes="0;1" keySplines="0.45 0 0.55 1"/></linearGradient>''']
    P = g.pos
    for a, b in g.edges:
        defs.append(f'<line id="e{IDX[a]}_{IDX[b]}" pathLength="100" x1="{P[a][0]:.1f}" y1="{P[a][1]:.1f}" x2="{P[b][0]:.1f}" y2="{P[b][1]:.1f}">'
                    + DR[a].a("x1", P[a][0], 0) + DR[a].a("y1", P[a][1], 1) + DR[b].a("x2", P[b][0], 0) + DR[b].a("y2", P[b][1], 1) + '</line>')
    for k in g.nodes:
        i, kind, (x, y) = IDX[k], g.kind[k], P[k]; r = g.radius(k)
        if kind == "hub":
            shape = f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="none" stroke-width="1.6"/><circle cx="{x:.1f}" cy="{y:.1f}" r="2"/>'
        elif kind == "group":  # пунктирне кільце: група «без напрямку», з центром не з'єднана
            shape = f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="none" stroke-width="1.3" stroke-dasharray="2.2 2.2"/>'
        else:
            shape = f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}"/>'
        defs.append(f'<g id="c{i}">{DR[k].g()}{shape}</g>')
        lx, ly, anc, _ = g.label_spec(k)
        if kind == "project" and g.repo_of[k].private:
            ww = [w * 5.6 for w in _mask_words(g.repo_of[k].name)]; tot = sum(ww) + 5 * (len(ww) - 1) + 14
            x0 = lx if anc == "start" else lx - tot if anc == "end" else lx - tot / 2
            bars = ""; bx = x0 + 14
            for w in ww:
                bars += (f'<rect x="{bx:.1f}" y="{ly - 8:.1f}" width="{w:.1f}" height="8.5" rx="4.25" opacity=".42"/>'
                         f'<rect x="{bx:.1f}" y="{ly - 8:.1f}" width="{w:.1f}" height="8.5" rx="4.25" fill="url(#shim)" opacity=".5"/>')
                bx += w + 5
            lock = (f'<rect x="{x0:.1f}" y="{ly - 6.5:.1f}" width="8" height="6.5" rx="1.4"/>'
                    f'<path d="M{x0 + 1.8:.1f} {ly - 6.3:.1f}v-1.6a2.2 2.2 0 0 1 4.4 0v1.6" fill="none" stroke="currentColor" stroke-width="1.3"/>')
            defs.append(f'<g id="t{i}">{DR[k].g()}{lock}{bars}</g>')
        else:
            if kind in ("hub", "group"):
                op = ' opacity=".75"' if kind == "group" else ""
                txt = (f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="{anc}" font-size="9.5" font-weight="600" letter-spacing="1.4"{op}>'
                       f'{escape(g.label(k).upper())}</text>')
            else:
                fs = {"me": 13, "project": 12, "tech": 11}[kind]; fw = 600 if kind == "me" else 500 if kind == "project" else 400
                txt = f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="{anc}" font-size="{fs}" font-weight="{fw}">{escape(g.label(k))}</text>'
            defs.append(f'<g id="t{i}">{DR[k].g()}{txt}</g>')
    o.append("<defs>" + "\n".join(defs) + "</defs>")
    o.append(f'<rect width="{W}" height="{H}" rx="14" fill="url(#bg)"/>')
    for ring, (rx, ry) in RINGS.items():
        o.append(f'<ellipse cx="{CX}" cy="{CY}" rx="{rx}" ry="{ry}" fill="none" stroke="{c["guide"]}" stroke-width="1" stroke-dasharray="0.1 7" stroke-linecap="round">'
                 f'<animate attributeName="stroke-dashoffset" from="0" to="-{70 if ring == "tech" else 49}" dur="{40 if ring == "tech" else 30}s" repeatCount="indefinite"/></ellipse>')

    # базовий шар
    o.append(f'<g><animate attributeName="opacity" {dim(".16")}/>' if SEQ else '<g>')
    o.append('<g opacity="0"><animate attributeName="opacity" begin="1.1s" dur="1.1s" fill="freeze" values="0;1" calcMode="spline" keyTimes="0;1" keySplines="0.2 0 0.2 1"/>')
    for a, b in g.edges:
        dash = ' stroke-dasharray="2 3"' if g.kind[a] == "group" or g.kind[b] == "group" else ""
        o.append(f'<use href="#e{IDX[a]}_{IDX[b]}" stroke="{c["link"]}" stroke-width="1"{dash}/>')
    o.append('</g>')
    if "ghost" in P:
        gx, gy = P["ghost"]
        a = math.cos(math.radians(g.ang["ghost"]))
        lx, anc = (gx + 13, "start") if a >= 0 else (gx - 13, "end")
        o.append(f'<g opacity="0"><animate attributeName="opacity" begin="2s" dur="1s" fill="freeze" values="0;1"/>{DR["ghost"].g()}'
                 f'<circle cx="{gx:.1f}" cy="{gy:.1f}" r="6" fill="none" stroke="{c["faint"]}" stroke-width="1.2" stroke-dasharray="2 3">'
                 f'<animateTransform attributeName="transform" type="rotate" from="0 {gx:.1f} {gy:.1f}" to="360 {gx:.1f} {gy:.1f}" dur="24s" repeatCount="indefinite"/></circle>'
                 f'<text x="{lx:.1f}" y="{gy + 4:.1f}" text-anchor="{anc}" font-size="11" fill="{c["faint"]}" {halo}>{escape(d.ghost)}</text></g>')
    for k in g.nodes:
        i, kind, (x, y) = IDX[k], g.kind[k], P[k]
        ring = {"me": 0, "hub": 1, "group": 1, "project": 2, "tech": 3}[kind]; delay = .15 + ring * .28 + (g.ang[k] % 360) / 360 * .25
        ox, oy = (CX - x) * .35, (CY - y) * .35
        enter = (f'<animateTransform attributeName="transform" type="translate" dur="{1.2 + delay:.2f}s" fill="freeze" calcMode="spline" '
                 f'keyTimes="0;{delay / (1.2 + delay):.3f};1" keySplines="0 0 1 1;0.16 1 0.3 1" values="{ox:.1f} {oy:.1f};{ox:.1f} {oy:.1f};0 0"/>'
                 f'<animate attributeName="opacity" dur="{.9 + delay:.2f}s" fill="freeze" calcMode="spline" keyTimes="0;{delay / (.9 + delay):.3f};1" '
                 f'keySplines="0 0 1 1;0.2 0 0.2 1" values="0;0;1"/>')
        extra = ""
        if kind == "me":
            extra = (f'<circle cx="{x}" cy="{y}" r="30" fill="url(#mehalo)"><animate attributeName="r" dur="7s" repeatCount="indefinite" '
                     f'calcMode="spline" keySplines="{EASE};{EASE}" values="24;34;24"/></circle>')
        tcol = c["text"] if kind in ("me", "project") else c["faint"] if kind == "group" else c["dim"]
        col = ncol(k)
        o.append(f'<g>{enter}{extra}<use href="#c{i}" fill="{col}" stroke="{col}"/><use href="#t{i}" fill="{tcol}" color="{tcol}" {halo}/></g>')
    o.append('</g>')
    n_priv = sum(r.private for r in g.repos)
    o.append(f'<text x="24" y="{H - 20}" font-size="10" fill="{c["faint"]}" letter-spacing=".4">'
             f'{len(g.repos)} REPOS  ·  {n_priv} PRIVATE  ·  UPDATED {d.updated.strftime("%d %b %Y").upper()}</text>')

    # шари підсвітки
    for si, f in enumerate(SEQ):
        fc = focus_col(f); fk = g.kind[f]
        L = [f'<g opacity="0"><animate attributeName="opacity" {pulse(si)}/>']
        for n in g.nb[f]:
            eid, a = EID[frozenset((f, n))]; fwd = a == f
            L.append(f'<use href="#{eid}" stroke="{fc}" stroke-width="1.4" opacity=".75"/>')
            L.append(f'<use href="#{eid}" stroke="{fc if fk == "project" else c["acc2"]}" stroke-width="2.4" stroke-linecap="round" stroke-dasharray="7 100">'
                     f'<animate attributeName="stroke-dashoffset" values="{"107;0" if fwd else "0;107"}" dur="1.5s" repeatCount="indefinite" '
                     f'calcMode="spline" keyTimes="0;1" keySplines="0.4 0 0.6 1"/></use>')
        for n in [f, *g.nb[f]]:
            i, kind, (x, y) = IDX[n], g.kind[n], P[n]; r = g.radius(n)
            col = fc if n == f else (c["text"] if kind == "tech" else ncol(n))
            if n == f:
                L.append(f'<g>{DR[n].g()}<circle cx="{x:.1f}" cy="{y:.1f}" r="{r + 5:.1f}" fill="none" stroke="{fc}" stroke-width="1.3"/>'
                         f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r + 5:.1f}" fill="none" stroke="{fc}" stroke-width="1">'
                         f'<animate attributeName="r" values="{r + 5:.1f};{r + 20:.1f}" dur="2.2s" repeatCount="indefinite" calcMode="spline" keyTimes="0;1" keySplines="0.2 0 0.2 1"/>'
                         f'<animate attributeName="opacity" values=".7;0" dur="2.2s" repeatCount="indefinite"/></circle></g>')
            L.append(f'<use href="#c{i}" fill="{col}" stroke="{col}"/><use href="#t{i}" fill="{c["text"]}" color="{c["text"]}" {halo}/>')
            lines: list[str] = []
            if n == f and fk == "project":
                rp = g.repo_of[f]
                first = ("private" + (f" · {rp.about}" if rp.about else "")) if rp.private else rp.about
                lines = [s for s in (first, f"{rp.commits} commits · {_ago(rp.pushed_days)}") if s]
            elif n == f and fk == "tech":
                t = f[2:]; cnt = len(g.nb[f])
                lines = [f"{cnt} repo{'s' if cnt != 1 else ''}" + (f" · {g.lang_bytes[t] / g.all_bytes:.0%} of code" if g.is_lang.get(t) else "")]
            elif fk == "tech" and g.is_lang.get(f[2:]) and kind == "project":
                rp = g.repo_of[n]; lines = [f"{f[2:]} {rp.langs.get(f[2:], 0) / (sum(rp.langs.values()) or 1):.0%}"]
            if lines:
                lx, ly, anc, dr = g.label_spec(n)
                seq = lines if dr > 0 else list(reversed(lines))
                txt = "".join(f'<text x="{lx:.1f}" y="{ly + dr * 14 * (j + 1):.1f}" text-anchor="{anc}" font-size="10.5" fill="{c["dim"]}" {halo} '
                              f'style="font-variant-numeric:tabular-nums">{escape(s)}</text>' for j, s in enumerate(seq))
                L.append(f'<g>{DR[n].g()}{txt}</g>')
        L.append('</g>'); o.append("".join(L))
    o.append('</svg>')
    return "\n".join(o)


def card_svg(g: Graph, theme: str) -> str:
    c = THEMES[theme]; lc = LANG_COLORS[theme]; d = g.d; CW, CH = 960, 206
    weeks = [sum(r.weekly[i] if i < len(r.weekly) else 0 for r in g.repos) for i in range(26)]
    head = f'font-size="9.5" font-weight="600" letter-spacing="1.4" fill="{c["dim"]}"'
    cols = [30, 345, 660]; CWID = 270
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {CW} {CH}" width="{CW}" height="{CH}" font-family="{FONT}">',
         f'<defs><radialGradient id="bg" cx="50%" cy="40%" r="80%"><stop offset="0" stop-color="{c["bg2"]}"/><stop offset="1" stop-color="{c["bg"]}"/></radialGradient>'
         f'<clipPath id="bar"><rect x="{cols[1]}" y="58" width="{CWID}" height="8" rx="4"/></clipPath></defs>',
         f'<rect width="{CW}" height="{CH}" rx="14" fill="url(#bg)"/>']
    for x in (cols[1] - 22, cols[2] - 22):
        o.append(f'<line x1="{x}" y1="30" x2="{x}" y2="160" stroke="{c["guide"]}" stroke-width="1"/>')
    for x, t in zip(cols, ("OVERVIEW", "LANGUAGES", "COMMITS PER WEEK")):
        o.append(f'<text x="{x}" y="38" {head}>{t}</text>')
    o.append(f'<text x="{cols[2] + CWID}" y="38" font-size="10" fill="{c["faint"]}" text-anchor="end">26 weeks</text>')

    def fade(delay: float) -> str:
        return (f'<animate attributeName="opacity" begin="{delay:.2f}s" dur=".7s" fill="freeze" values="0;1" calcMode="spline" keyTimes="0;1" keySplines="0.2 0 0.2 1"/>'
                f'<animateTransform attributeName="transform" type="translate" begin="{delay:.2f}s" dur=".7s" fill="freeze" values="0 5;0 0" calcMode="spline" keyTimes="0;1" keySplines="0.16 1 0.3 1"/>')
    total = sum(r.commits for r in g.repos); n_priv = sum(r.private for r in g.repos)
    langs_used = [l for l, b in g.lang_bytes.items() if b / g.all_bytes >= .01]
    rows = [(str(len(g.repos)), "projects", f"{n_priv} private"), (f"{total:,}".replace(",", " "), "commits", "all time"),
            (str(len(langs_used)), "languages", f"{len(g.domains)} domains")]
    for i, (num, w, sub) in enumerate(rows):
        y = 78 + i * 38
        o.append(f'<g opacity="0">{fade(.2 + i * .12)}<text x="{cols[0] + 74}" y="{y}" text-anchor="end" font-size="24" font-weight="600" fill="{c["text"]}" '
                 f'letter-spacing="-.4" style="font-variant-numeric:tabular-nums">{num}</text>'
                 f'<text x="{cols[0] + 86}" y="{y - 9}" font-size="12" font-weight="500" fill="{c["text"]}">{w}</text>'
                 f'<text x="{cols[0] + 86}" y="{y + 4}" font-size="10" fill="{c["dim"]}">{sub}</text></g>')
    langs = sorted(g.lang_bytes.items(), key=lambda kv: -kv[1])
    items = langs[:5] + ([("Other", sum(b for _, b in langs[5:]))] if len(langs) > 5 else [])
    o.append(f'<rect x="{cols[1]}" y="58" width="{CWID}" height="8" rx="4" fill="{c["guide"]}"/><g clip-path="url(#bar)">')
    x = float(cols[1])
    for i, (lang, b) in enumerate(items):
        w = max(b / g.all_bytes * CWID - 1.5, 0)
        o.append(f'<rect x="{x:.1f}" y="58" width="{w:.1f}" height="8" fill="{lc.get(lang, lc["Other"])}">'
                 f'<animate attributeName="width" begin="{.3 + i * .1:.2f}s" dur=".9s" fill="freeze" values="0;{w:.1f}" calcMode="spline" keyTimes="0;1" keySplines="0.16 1 0.3 1"/></rect>')
        x += b / g.all_bytes * CWID
    o.append('</g>')
    for i, (lang, b) in enumerate(items):
        cx_ = cols[1] + (i % 2) * 145; cy_ = 96 + (i // 2) * 24
        o.append(f'<g opacity="0">{fade(.5 + i * .06)}<circle cx="{cx_ + 4}" cy="{cy_ - 4}" r="3.5" fill="{lc.get(lang, lc["Other"])}"/>'
                 f'<text x="{cx_ + 14}" y="{cy_}" font-size="12" fill="{c["text"]}">{escape(lang)}</text>'
                 f'<text x="{cx_ + 125}" y="{cy_}" font-size="11" fill="{c["dim"]}" text-anchor="end" style="font-variant-numeric:tabular-nums">{b / g.all_bytes:.0%}</text></g>')
    bw, n = 7, 26; gap = (CWID - n * bw) / (n - 1); base = 146; hmax = 80; mx = max(weeks) or 1
    o.append(f'<line x1="{cols[2]}" y1="{base + .5}" x2="{cols[2] + CWID}" y2="{base + .5}" stroke="{c["guide"]}"/>')
    for i, v in enumerate(weeks):
        h = max(v / mx * hmax, 1.5 if v else 0); bx = cols[2] + i * (bw + gap); op = 1 if i == n - 1 else .22 + .38 * (i / n)
        o.append(f'<rect x="{bx:.1f}" y="{base - h:.1f}" width="{bw}" height="{h:.1f}" rx="2" fill="{c["acc"]}" opacity="{op:.2f}">'
                 f'<animate attributeName="height" begin="{.3 + i * .03:.2f}s" dur=".8s" fill="freeze" values="0;{h:.1f}" calcMode="spline" keyTimes="0;1" keySplines="0.16 1 0.3 1"/>'
                 f'<animate attributeName="y" begin="{.3 + i * .03:.2f}s" dur=".8s" fill="freeze" values="{base};{base - h:.1f}" calcMode="spline" keyTimes="0;1" keySplines="0.16 1 0.3 1"/></rect>')
    lx = cols[2] + (n - 1) * (bw + gap) + bw
    o.append(f'<g opacity="0">{fade(1.3)}<text x="{lx}" y="{base - max(weeks[-5:]) / mx * hmax - 8:.1f}" text-anchor="end" font-size="11" font-weight="500" '
             f'fill="{c["text"]}">{weeks[-1]} this week</text></g>')
    start = (d.updated - timedelta(weeks=25)).strftime("%b")
    o.append(f'<text x="{cols[2]}" y="{base + 15}" font-size="9.5" fill="{c["faint"]}">{start}</text>'
             f'<text x="{cols[2] + CWID}" y="{base + 15}" font-size="9.5" fill="{c["faint"]}" text-anchor="end">now</text>')
    dc: dict[str, int] = {}
    homed = [r for r in g.repos if r.domain in g.domains]
    for r in homed:
        dc[r.domain] = dc.get(r.domain, 0) + sum(r.weekly)
    if not any(dc.values()):
        for r in homed:
            dc[r.domain] += r.commits
    top = max(dc, key=lambda k: dc[k]) if dc else ""
    last = min((r.pushed_days for r in g.repos), default=0)
    o.append(f'<line x1="30" y1="174.5" x2="{CW - 30}" y2="174.5" stroke="{c["guide"]}"/>')
    o.append(f'<text x="30" y="193" font-size="10" fill="{c["faint"]}" letter-spacing=".4">MOST ACTIVE <tspan fill="{c["dim"]}" font-weight="600">{escape(top.upper())}</tspan>'
             f'  ·  LAST PUSH {_ago(last).upper()}  ·  UPDATED {d.updated.strftime("%d %b %Y").upper()}</text>')
    o.append('</svg>')
    return "\n".join(o)


def render_all(data: Data, out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    g = Graph(data); files: list[Path] = []
    for theme in THEMES:
        for name, svg in ((f"graph-{theme}.svg", graph_svg(g, theme)), (f"stats-{theme}.svg", card_svg(g, theme))):
            p = out / name; p.write_text(svg, encoding="utf-8"); files.append(p)
    return files
