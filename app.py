"""mania-skills - merge osu! official and mamesosu skillset ratings into one view."""
from __future__ import annotations

import json
import math
import queue
import threading
import tkinter as tk
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from core.aggregate import (DISPLAY_NAME, RADAR_SKILLS, build_pools, contributions,
                            counts_toward_rating, rate_skillsets)
from core import osu_install, settings
from core.beatmap import LocalSongs, cache_dir
from core.bundle import bundled
from core.dan import DAN_SKILL_LABELS, DAN_SKILLS, estimate_dan
from core.i18n import LANGUAGES, language, language_name, set_language, t
from core.msd import SKILLSETS
from core.predict import fit_timing_model
from core.local_scores import players_seen, read_local_scores
from core.ratings import MsdCache, fetch_dans, rate_plays
from core.recommend import PATTERN_OF, recommend, weaknesses
from core.sources import SourceError, _session, fetch_mamesosu, fetch_official
from core.version import LABEL as VERSION
from core.wife import DEFAULT_JUDGE, JUDGE_SCALES, REFERENCE_OD

BG = "#12141c"
PANEL = "#1a1d29"
GRID = "#2c3142"
TEXT = "#e6e9f2"
MUTED = "#8b93a8"

# How many player names the picker shows before it starts scrolling, and how tall a row
# is. Twelve is about the point where a taller dialog stops fitting comfortably.
VISIBLE_PLAYERS = 12
PLAYER_ROW_HEIGHT = 22

SERIES = [
    ("official", "osu! official", "#4da3ff"),
    ("mamesosu", "mamesosu", "#ff9f43"),
    ("combined", "combined", "#3ddc97"),
]


def _saved_ids() -> tuple[str, str]:
    """Whoever used this copy last. Blank on a fresh install rather than a stranger's id."""
    saved = settings.load()
    return str(saved.get("osu_id") or ""), str(saved.get("mame_id") or "")


def _saved_players() -> set[str] | None:
    """The chosen owner names, or None for 'every name in the database'."""
    picked = settings.load().get("players")
    return {str(n) for n in picked} if isinstance(picked, list) and picked else None


def _players_asked() -> bool:
    """Whether the owner has answered the question at all.

    Distinct from the answer: an absent key means never asked, while a stored None means
    asked and answered "all of them". Without the distinction a first run on a shared PC
    would quietly analyse a stranger's clears alongside the owner's.
    """
    return "players" in settings.load()


class RadarChart(tk.Canvas):
    """Radar plot of the 7 non-overall skillsets, one polygon per source."""

    def __init__(self, master, **kw):
        super().__init__(master, bg=PANEL, highlightthickness=0, **kw)
        self.ratings: dict[str, dict[str, float]] = {}
        self.visible: set[str] = {k for k, _, _ in SERIES}
        self.selected: str | None = None
        self.bind("<Configure>", lambda e: self.redraw())

    def set_data(self, ratings: dict[str, dict[str, float]]) -> None:
        self.ratings = ratings
        self.redraw()

    def toggle(self, key: str, on: bool) -> None:
        self.visible = (self.visible | {key}) if on else (self.visible - {key})
        self.redraw()

    def select_skill(self, skill: str | None) -> None:
        self.selected = skill
        self.redraw()

    def _max_value(self) -> float:
        vals = [v for r in self.ratings.values() for k, v in r.items() if k in RADAR_SKILLS]
        return max(5.0, math.ceil((max(vals) if vals else 10.0) / 5.0) * 5.0)

    def redraw(self) -> None:
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 50 or h < 50:
            return
        cx, cy = w / 2, h / 2 + 6
        radius = min(w, h) / 2 - 62
        if radius < 30:
            return

        n = len(RADAR_SKILLS)
        vmax = self._max_value()
        rings = 4

        for i in range(1, rings + 1):
            pts = []
            for j in range(n):
                a = -math.pi / 2 + 2 * math.pi * j / n
                r = radius * i / rings
                pts += [cx + r * math.cos(a), cy + r * math.sin(a)]
            self.create_polygon(pts, outline=GRID, fill="", width=1)
            self.create_text(cx + 4, cy - radius * i / rings, text=f"{vmax * i / rings:.0f}",
                             fill=MUTED, font=("Segoe UI", 7), anchor="w")

        for j, skill in enumerate(RADAR_SKILLS):
            a = -math.pi / 2 + 2 * math.pi * j / n
            ex, ey = cx + radius * math.cos(a), cy + radius * math.sin(a)
            hot = self.selected == skill
            self.create_line(cx, cy, ex, ey, fill="#3f4660" if hot else GRID,
                             width=2 if hot else 1)
            lx, ly = cx + (radius + 30) * math.cos(a), cy + (radius + 22) * math.sin(a)
            self.create_text(lx, ly, text=DISPLAY_NAME[skill], fill=TEXT if hot else MUTED,
                             font=("Segoe UI Semibold" if hot else "Segoe UI", 9))
            best = max((self.ratings.get(k, {}).get(skill, 0.0) for k in self.visible),
                       default=0.0)
            if best:
                self.create_text(lx, ly + 14, text=f"{best:.2f}", fill=MUTED,
                                 font=("Segoe UI", 8))

        for key, _, colour in SERIES:
            if key not in self.visible or key not in self.ratings:
                continue
            r = self.ratings[key]
            pts = []
            for j, skill in enumerate(RADAR_SKILLS):
                a = -math.pi / 2 + 2 * math.pi * j / n
                rr = radius * min(1.0, r.get(skill, 0.0) / vmax)
                pts += [cx + rr * math.cos(a), cy + rr * math.sin(a)]
            self.create_polygon(pts, outline=colour, fill="", width=2)
            for j in range(0, len(pts), 2):
                self.create_oval(pts[j] - 3, pts[j + 1] - 3, pts[j] + 3, pts[j + 1] + 3,
                                 fill=colour, outline="")


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.geometry("1360x820")
        self.minsize(1160, 700)
        self.configure(bg=BG)
        self._set_icon()

        self.plays: list[dict] = []
        self.pools: dict[str, list[dict]] = {}
        self.ratings: dict[str, dict[str, float]] = {}
        self.ratings_wife: dict[str, float] = {}
        self.dans: dict[str, dict] = {}
        self.selected_skill = "overall"
        self.recommended: list[dict] = []
        self.timing_model = None
        self.source_failures: list[str] = []
        saved = settings.load()
        # One restated column, read on whichever system is selected. Both levels are kept
        # so switching systems and back does not forget the other one. A display choice
        # only: the ranking beside it stays the osu!-accuracy one it explains.
        self.ref_system = saved.get("ref_system") or "osu"
        self.ref_od = float(saved.get("ref_od", REFERENCE_OD))
        self.ref_judge = int(saved.get("judge", DEFAULT_JUDGE))
        self.events: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.stop_dans = threading.Event()
        self.rec_worker: threading.Thread | None = None

        self._build_style()
        self._build_ui()
        self._apply_language()
        self._load_cached()
        self.after(80, self._drain_events)

    def _set_icon(self) -> None:
        """The app icon, drawn by scripts/make_icon.py.

        `default=` so the player picker inherits it too. Wrapped because an icon is
        decoration: a missing or unreadable file must not be what stops the app opening.
        """
        try:
            self.iconbitmap(default=str(bundled("assets", "skull.ico")))
        except tk.TclError:
            pass

    # ---------- layout ----------

    def _build_style(self) -> None:
        st = ttk.Style(self)
        st.theme_use("clam")
        st.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=TEXT,
                     rowheight=23, borderwidth=0, font=("Segoe UI", 9))
        st.configure("Treeview.Heading", background=GRID, foreground=MUTED,
                     borderwidth=0, font=("Segoe UI Semibold", 9))
        st.map("Treeview", background=[("selected", "#32405c")], foreground=[("selected", TEXT)])
        st.configure("TNotebook", background=BG, borderwidth=0)
        st.configure("TNotebook.Tab", background=PANEL, foreground=MUTED, borderwidth=0,
                     padding=(14, 5), font=("Segoe UI Semibold", 9))
        st.map("TNotebook.Tab", background=[("selected", GRID)],
               foreground=[("selected", TEXT)])
        st.configure("TCombobox", fieldbackground=PANEL, background=GRID, foreground=TEXT,
                     arrowcolor=MUTED, borderwidth=0, padding=3)
        st.map("TCombobox", fieldbackground=[("readonly", PANEL)],
               selectbackground=[("readonly", PANEL)], selectforeground=[("readonly", TEXT)])
        # The dropdown is a plain Tk listbox, so it is themed through the option database.
        self.option_add("*TCombobox*Listbox.background", PANEL)
        self.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", "#32405c")
        self.option_add("*TCombobox*Listbox.selectForeground", TEXT)

    def _build_ui(self) -> None:
        top = tk.Frame(self, bg=BG)
        top.pack(fill="x", padx=14, pady=(12, 6))

        self.osu_label = tk.Label(top, bg=BG, fg=MUTED, font=("Segoe UI", 9))
        self.osu_label.pack(side="left")
        self.osu_id = tk.Entry(top, width=17, bg=PANEL, fg=TEXT, insertbackground=TEXT,
                               relief="flat", font=("Segoe UI", 10))
        self.osu_id.insert(0, _saved_ids()[0])
        self.osu_id.pack(side="left", padx=(6, 16), ipady=4)

        self.mame_label = tk.Label(top, bg=BG, fg=MUTED, font=("Segoe UI", 9))
        self.mame_label.pack(side="left")
        self.mame_id = tk.Entry(top, width=17, bg=PANEL, fg=TEXT, insertbackground=TEXT,
                                relief="flat", font=("Segoe UI", 10))
        self.mame_id.insert(0, _saved_ids()[1])
        self.mame_id.pack(side="left", padx=(6, 16), ipady=4)

        self.refresh_btn = tk.Button(top, command=self.start_refresh,
                                     bg="#3d5afe", fg="white", relief="flat",
                                     font=("Segoe UI Semibold", 9), padx=18, pady=5,
                                     activebackground="#5872ff", cursor="hand2")
        self.refresh_btn.pack(side="left")

        self.lang_box = ttk.Combobox(top, state="readonly", width=9, font=("Segoe UI", 9),
                                     values=[name for _, name in LANGUAGES])
        self.lang_box.set(language_name(language()))
        self.lang_box.pack(side="right")
        self.lang_box.bind("<<ComboboxSelected>>", self._on_language_change)
        self.lang_label = tk.Label(top, bg=BG, fg=MUTED, font=("Segoe UI", 9))
        self.lang_label.pack(side="right", padx=(0, 6))

        self.status = tk.Label(top, text="", bg=BG, fg=MUTED, font=("Segoe UI", 9))
        self.status.pack(side="left", padx=14)

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=14, pady=(4, 12))

        left = tk.Frame(body, bg=PANEL)
        self.radar = RadarChart(left)
        self.radar.pack(fill="both", expand=True, padx=6, pady=6)

        legend = tk.Frame(left, bg=PANEL)
        legend.pack(fill="x", padx=12, pady=(0, 10))
        self.series_vars: dict[str, tk.BooleanVar] = {}
        for key, label, colour in SERIES:
            var = tk.BooleanVar(value=True)
            self.series_vars[key] = var
            cb = tk.Checkbutton(legend, text=f"  {label}", variable=var, bg=PANEL,
                                fg=colour, selectcolor=PANEL, activebackground=PANEL,
                                activeforeground=colour, relief="flat", bd=0,
                                font=("Segoe UI Semibold", 9), cursor="hand2",
                                command=lambda k=key: self.radar.toggle(k, self.series_vars[k].get()))
            cb.pack(side="left", padx=(0, 14))

        self.dan_head = tk.Label(left, bg=PANEL, fg=MUTED, font=("Segoe UI Semibold", 8))
        self.dan_head.pack(anchor="w", padx=12)
        dcols = ("row", "official", "mamesosu", "combined")
        self.dan_tree = ttk.Treeview(left, columns=dcols, show="headings", height=5)
        for c, wpx in (("row", 86), ("official", 96), ("mamesosu", 96), ("combined", 104)):
            self.dan_tree.column(c, width=wpx, stretch=False,
                                 anchor="e" if c != "row" else "w")
        # Not fill="x": five short rows stretched across the whole panel read as a large
        # empty box, and the width is better spent on the play list.
        self.dan_tree.pack(anchor="w", padx=12, pady=(4, 4))
        self.dan_tree.tag_configure("overall", font=("Segoe UI Semibold", 9))

        self.dan_note = tk.Label(left, bg=PANEL, fg=MUTED, font=("Segoe UI", 8),
                                 justify="left", wraplength=460, anchor="w")
        self.dan_note.pack(anchor="w", padx=12, pady=(0, 2))

        # Coverage, not accuracy: says when the number rests on too little evidence.
        self.dan_cov = tk.Label(left, bg=PANEL, fg="#e8a33d", font=("Segoe UI", 8),
                                justify="left", wraplength=460, anchor="w")
        self.dan_cov.pack(anchor="w", padx=12, pady=(0, 6))
        left.bind("<Configure>", lambda e: [w.config(wraplength=max(240, e.width - 30))
                                            for w in (self.dan_note, self.dan_cov)])

        btns = tk.Frame(left, bg=PANEL)
        btns.pack(anchor="w", padx=12, pady=(0, 10))

        # Shown only when the score database cannot be found. A portable copy registers
        # nothing to auto-detect, and an environment variable is not an answer for
        # somebody who just downloaded the exe.
        self.pick_btn = tk.Button(btns, command=self._pick_osu_folder, bg=GRID, fg=TEXT,
                                  relief="flat", font=("Segoe UI", 8), padx=10, pady=3,
                                  activebackground="#3a4160", cursor="hand2")
        # Shown only when scores.db holds more than one player name.
        self.acct_btn = tk.Button(btns, command=self._pick_players, bg=GRID, fg=TEXT,
                                  relief="flat", font=("Segoe UI", 8), padx=10, pady=3,
                                  activebackground="#3a4160", cursor="hand2")

        right = tk.Frame(body, bg=BG, width=800)
        right.pack(side="right", fill="both")
        right.pack_propagate(False)
        # Packed after `right` on purpose: pack hands out parcels in packing order, and
        # this one is the elastic half, so it has to take what is left rather than first.
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))

        self.skills_head = tk.Label(right, bg=BG, fg=MUTED, font=("Segoe UI Semibold", 8))
        self.skills_head.pack(anchor="w", pady=(0, 4))
        cols = ("skill", "official", "mamesosu", "combined", "wife", "delta")
        self.skill_tree = ttk.Treeview(right, columns=cols, show="headings", height=9)
        for c, wpx in (("skill", 150), ("official", 100), ("mamesosu", 100),
                       ("combined", 108), ("wife", 108), ("delta", 92)):
            self.skill_tree.column(c, width=wpx, stretch=(c == "skill"),
                                   anchor="e" if c != "skill" else "w")
        self.skill_tree.pack(fill="x")
        self.skill_tree.bind("<<TreeviewSelect>>", self._on_skill_select)

        self.detail_label = tk.Label(right, bg=BG, fg=MUTED,
                                     font=("Segoe UI Semibold", 8))
        self.detail_label.pack(anchor="w", pady=(14, 4))

        self.tabs = ttk.Notebook(right)
        self.tabs.pack(fill="both", expand=True)

        plays_tab = tk.Frame(self.tabs, bg=BG)
        self.tabs.add(plays_tab)

        pbar = tk.Frame(plays_tab, bg=BG)
        pbar.pack(fill="x", pady=(4, 6))
        self.system_label = tk.Label(pbar, bg=BG, fg=MUTED, font=("Segoe UI", 8))
        self.system_label.pack(side="left")
        self.system_box = ttk.Combobox(pbar, state="readonly", width=15,
                                       font=("Segoe UI", 8))
        self.system_box.pack(side="left", padx=(5, 16))
        self.system_box.bind("<<ComboboxSelected>>", self._on_reference_change)
        self.level_label = tk.Label(pbar, bg=BG, fg=MUTED, font=("Segoe UI", 8))
        self.level_label.pack(side="left")
        self.level_box = ttk.Combobox(pbar, state="readonly", width=6,
                                      font=("Segoe UI", 8))
        self.level_box.pack(side="left", padx=(5, 0))
        self.level_box.bind("<<ComboboxSelected>>", self._on_reference_change)

        pcols = ("src", "eff", "acc", "ref", "ms", "mods", "map")
        self.play_tree = ttk.Treeview(plays_tab, columns=pcols, show="headings")
        for c, wpx, anc in (("src", 52, "w"), ("eff", 56, "e"), ("acc", 62, "e"),
                            ("ref", 78, "e"), ("ms", 58, "e"), ("mods", 50, "w"),
                            ("map", 430, "w")):
            self.play_tree.column(c, width=wpx, stretch=(c == "map"), anchor=anc)
        self.play_tree.pack(fill="both", expand=True)
        self.play_tree.tag_configure("official", foreground="#4da3ff")
        self.play_tree.tag_configure("mamesosu", foreground="#ff9f43")
        self.play_tree.tag_configure("local", foreground="#9d8cff")

        rec_tab = tk.Frame(self.tabs, bg=BG)
        self.tabs.add(rec_tab)
        bar = tk.Frame(rec_tab, bg=BG)
        bar.pack(fill="x", pady=(4, 6))
        self.rec_btn = tk.Button(bar, command=self.start_recommend,
                                 bg="#3d5afe", fg="white", relief="flat",
                                 font=("Segoe UI Semibold", 9), padx=14, pady=3,
                                 activebackground="#5872ff", cursor="hand2")
        self.rec_btn.pack(side="left")
        self.rec_hint = tk.Label(bar, text="", bg=BG, fg=MUTED, font=("Segoe UI", 8))
        self.rec_hint.pack(side="left", padx=10)

        rcols = ("dan", "msd", "focus", "bpm", "pred", "band", "tag", "status", "map")
        self.rec_tree = ttk.Treeview(rec_tab, columns=rcols, show="headings")
        for c, wpx, anc in (("dan", 52, "e"), ("msd", 48, "e"), ("focus", 44, "e"),
                            ("bpm", 42, "e"), ("pred", 58, "e"), ("band", 82, "w"),
                            ("tag", 104, "w"), ("status", 56, "w"), ("map", 290, "w")):
            self.rec_tree.column(c, width=wpx, stretch=(c == "map"), anchor=anc)
        self.rec_tree.pack(fill="both", expand=True)
        self.rec_tree.tag_configure("ranked", foreground="#3ddc97")
        self.rec_tree.tag_configure("loved", foreground="#ff7ab6")
        self.rec_tree.bind("<Double-1>", self._open_recommended)

    # ---------- language ----------

    def _on_language_change(self, _event) -> None:
        chosen = self.lang_box.get()
        for code, name in LANGUAGES:
            if name == chosen:
                set_language(code)
                break
        self._apply_language()
        self.focus_set()  # drop the combobox highlight

    def _apply_language(self) -> None:
        """(Re)label every static widget. Trees are refilled because rows carry text too."""
        self.title(f'{t("app.title")}   v{VERSION}')
        self.osu_label.config(text=t("field.osu_id"))
        self.mame_label.config(text=t("field.mame_id"))
        self.pick_btn.config(text=t("btn.pick_osu"))
        self.acct_btn.config(text=t("btn.pick_players"))
        self.lang_label.config(text=t("lang.label"))
        self.dan_head.config(text=t("head.dan"))
        self.dan_note.config(text=t("note.dan"))
        self.skills_head.config(text=t("head.skills"))

        idle = self.worker is None or not self.worker.is_alive()
        self.refresh_btn.config(text=t("btn.refresh") if idle else t("btn.working"))
        rec_idle = self.rec_worker is None or not self.rec_worker.is_alive()
        self.rec_btn.config(text=t("btn.find_maps") if rec_idle else t("btn.searching"))

        for i, key in enumerate(("tab.plays", "tab.recommend")):
            self.tabs.tab(i, text=t(key))

        for c, key in (("row", ""), ("official", "osu!"), ("mamesosu", "mame"),
                       ("combined", t("col.combined"))):
            self.dan_tree.heading(c, text=key)
        for c, txt in (("skill", t("col.skillset")), ("official", "osu!"),
                       ("mamesosu", "mame"), ("combined", t("col.combined")),
                       ("wife", t("col.wife")), ("delta", t("col.delta"))):
            self.skill_tree.heading(c, text=txt)
        for c, key in (("src", "col.src"), ("eff", "col.ssr"), ("acc", "col.acc"),
                       ("ms", "col.spread"), ("mods", "col.mods"), ("map", "col.map")):
            self.play_tree.heading(c, text=t(key))
        self.system_label.config(text=t("field.system"))
        self.level_label.config(text=t("field.level"))
        self.system_box.config(values=[t("sys.osu"), t("sys.wife")])
        self._sync_reference_boxes()
        for c, key in (("dan", "col.dan"), ("msd", "col.msd"), ("focus", "col.focus"),
                       ("bpm", "col.bpm"), ("pred", "col.pred"), ("band", "col.band"),
                       ("tag", "col.tag"), ("status", "col.status"), ("map", "col.map")):
            self.rec_tree.heading(c, text=t(key))

        if self.plays:
            self.recompute()
        else:
            self._fill_coverage()
        self._fill_rec_tree()

    # ---------- data ----------

    def _load_cached(self) -> None:
        path = cache_dir() / "plays.json"
        if not path.exists():
            self.status.config(text=t("status.no_cache"))
            return
        try:
            self.plays = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.status.config(text=t("status.cache_bad"))
            return
        self.recompute()
        self.status.config(text=t("status.loaded", count=len(self.plays)))

    def recompute(self) -> None:
        self.pools = build_pools(self.plays)
        self.ratings = {k: rate_skillsets(v) for k, v in self.pools.items()}
        # The same pool judged by Etterna's own accuracy rather than osu!'s, shown beside
        # it rather than instead of it: one matches mania-tracker, the other matches what
        # MSD_GOAL actually means.
        self.ratings_wife = rate_skillsets(self.pools.get("combined", []), basis="wife")
        # Refitted whenever the pool changes, so a recommendation is predicted against how
        # the player hits now rather than whenever the app was last opened.
        self.timing_model = fit_timing_model(self.pools.get("combined", []))
        self.dans = {k: estimate_dan(v) for k, v in self.pools.items()}
        self.radar.set_data(self.ratings)
        self._fill_skill_tree()
        self._fill_dan_tree()
        self._fill_play_tree()
        self._update_rec_hint()

    def _fill_dan_tree(self) -> None:
        self.dan_tree.delete(*self.dan_tree.get_children())

        def cell(d: dict | None) -> str:
            if not d or d.get("raw") is None:
                return "-"
            return f"{d['label']}  ({d['raw']:.2f})"

        self.dan_tree.insert("", "end", tags=("overall",), values=(
            t("col.overall"), *(cell(self.dans.get(k)) for k, _, _ in SERIES)))
        for s in DAN_SKILLS:
            self.dan_tree.insert("", "end", values=(
                DAN_SKILL_LABELS[s],
                *(cell((self.dans.get(k) or {}).get("skills", {}).get(s)) for k, _, _ in SERIES)))
        self._fill_coverage()

    def _fill_coverage(self) -> None:
        """Warn about the two things that make a dan number untrustworthy.

        The window averages the best 20 clears, so a bucket holding 5 is averaging noise;
        and without the local score database the pool is pp top plays only, which exist
        on ranked maps while dan courses are almost all graveyard.
        """
        skills = (self.dans.get("combined") or {}).get("skills", {})
        thin = [f"{DAN_SKILL_LABELS[s]} {skills[s]['clears']}/{skills[s]['need']}"
                for s in DAN_SKILLS
                if s in skills and skills[s]["clears"] < skills[s]["need"]]

        # A server that dropped out leads, because every number below is missing its
        # plays and nothing else on screen would say so.
        lines = list(self.source_failures)
        if osu_install.scores_db() is None:
            # Lazer keeps its scores in a realm database this cannot read, so say that
            # rather than let a lazer-only player think their clears simply did not count.
            lines.append(t("note.lazer") if osu_install.lazer_dir() else t("note.ranked_only"))
            self.pick_btn.pack(side="left", padx=(0, 6))
        else:
            local = sum(1 for p in self.plays if p.get("source") == "local")
            if local:
                lines.append(t("note.local_on", count=local))
            self.pick_btn.pack_forget()

        # A guest who sits down at someone else's PC plays one hard chart to show off, so
        # foreign scores are few but land at the very top of the dan distribution - which
        # is the only part of it the model reads. Measured here: 8 guest clears out of 588
        # moved the estimate 0.35 dan.
        names = players_seen()
        if len(names) > 1:
            self.acct_btn.pack(side="left", padx=(0, 6))
            if not _players_asked():
                lines.append(t("note.guests", count=len(names)))
        else:
            self.acct_btn.pack_forget()

        if thin:
            lines.append(t("note.thin", buckets=", ".join(thin)))
        self.dan_cov.config(text="\n".join(lines))

    def _pick_osu_folder(self) -> None:
        """Point at the osu! stable folder by hand when nothing could be auto-detected."""
        chosen = filedialog.askdirectory(title=t("dlg.pick_osu"))
        if not chosen:
            return
        path = Path(chosen)
        if not osu_install.is_root(path):
            messagebox.showwarning(t("app.title"), t("msg.not_osu_folder"))
            return
        osu_install.set_root(path)
        self._fill_coverage()
        messagebox.showinfo(t("app.title"), t("msg.osu_folder_set"))

    def _pick_players(self) -> None:
        """Choose whose scores count. Never guessed: the osu! cfg's `Username` is only the
        name last logged in, and a player who has renamed has clears under every old name.
        """
        names = players_seen()
        if not names:
            return
        chosen = _saved_players()

        win = tk.Toplevel(self)
        win.title(t("dlg.pick_players"))
        win.configure(bg=PANEL)
        win.transient(self)
        win.resizable(False, True)
        tk.Label(win, text=t("dlg.pick_players_hint"), bg=PANEL, fg=MUTED, justify="left",
                 font=("Segoe UI", 8), wraplength=320).pack(anchor="w", padx=14, pady=(12, 8))

        # A shared PC accumulates names without limit - this machine has twelve, and a
        # club or a PC-bang has dozens. Packed straight into the window they made it taller
        # than the screen, which pushes Save off the bottom where it cannot be clicked at
        # all. So the names scroll inside a fixed-height viewport and the buttons stay put.
        listbox = tk.Frame(win, bg=PANEL)
        listbox.pack(fill="both", expand=True, padx=14)
        canvas = tk.Canvas(listbox, bg=PANEL, highlightthickness=0,
                           height=min(len(names), VISIBLE_PLAYERS) * PLAYER_ROW_HEIGHT)
        bar = ttk.Scrollbar(listbox, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        rows = tk.Frame(canvas, bg=PANEL)
        holder = canvas.create_window((0, 0), window=rows, anchor="nw")
        rows.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(holder, width=e.width))
        canvas.pack(side="left", fill="both", expand=True)
        if len(names) > VISIBLE_PLAYERS:
            bar.pack(side="right", fill="y")

        def on_wheel(event) -> None:
            canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

        for widget in (win, canvas, rows):
            widget.bind("<MouseWheel>", on_wheel)

        vars_: dict[str, tk.BooleanVar] = {}
        for name, count in sorted(names.items(), key=lambda kv: -kv[1]):
            var = tk.BooleanVar(value=chosen is None or name in chosen)
            vars_[name] = var
            label = name or t("dlg.players_offline")
            cb = tk.Checkbutton(rows, text=f"  {label}  ({count})", variable=var, bg=PANEL,
                                fg=TEXT, selectcolor=GRID, activebackground=PANEL,
                                activeforeground=TEXT, relief="flat", bd=0, anchor="w",
                                font=("Segoe UI", 9), cursor="hand2")
            cb.pack(fill="x")
            cb.bind("<MouseWheel>", on_wheel)

        def set_all(value: bool) -> None:
            for var in vars_.values():
                var.set(value)

        def save() -> None:
            picked = [n for n, v in vars_.items() if v.get()]
            if not picked:
                messagebox.showwarning(t("app.title"), t("msg.players_none"), parent=win)
                return
            # All of them selected is not a filter; store None so a later rename or a new
            # guest is not silently excluded by a stale list.
            settings.update(players=None if len(picked) == len(names) else picked)
            win.destroy()
            self._fill_coverage()
            messagebox.showinfo(t("app.title"), t("msg.players_set"))

        row = tk.Frame(win, bg=PANEL)
        row.pack(fill="x", padx=14, pady=12)
        tk.Button(row, text=t("btn.save"), command=save, bg=GRID, fg=TEXT, relief="flat",
                  font=("Segoe UI", 8), padx=12, pady=3, activebackground="#3a4160",
                  cursor="hand2").pack(side="right")
        # With one owner among forty guests, unticking thirty-nine by hand is the same
        # problem in a different shape.
        for key, value in (("btn.none", False), ("btn.all", True)):
            tk.Button(row, text=t(key), command=lambda v=value: set_all(v), bg=PANEL,
                      fg=MUTED, relief="flat", font=("Segoe UI", 8), padx=8, pady=3,
                      activebackground=GRID, activeforeground=TEXT,
                      cursor="hand2").pack(side="left", padx=(0, 6))

        # Never taller than the screen it has to fit on, whatever the name count.
        win.update_idletasks()
        win.minsize(max(340, win.winfo_reqwidth()), 0)
        win.geometry(f"{max(340, win.winfo_reqwidth())}x"
                     f"{min(win.winfo_reqheight(), int(win.winfo_screenheight() * 0.8))}")

        # Modal, because start_refresh asks this before deciding what to analyse.
        win.grab_set()
        self.wait_window(win)

    def _fill_skill_tree(self) -> None:
        self.skill_tree.delete(*self.skill_tree.get_children())
        for s in SKILLSETS:
            o = self.ratings.get("official", {}).get(s, 0.0)
            m = self.ratings.get("mamesosu", {}).get(s, 0.0)
            c = self.ratings.get("combined", {}).get(s, 0.0)
            w = self.ratings_wife.get(s, 0.0)
            self.skill_tree.insert("", "end", iid=s, values=(
                DISPLAY_NAME[s], f"{o:.2f}", f"{m:.2f}", f"{c:.2f}",
                f"{w:.2f}" if w else "-", f"{c - o:+.2f}"))
        if self.skill_tree.exists(self.selected_skill):
            self.skill_tree.selection_set(self.selected_skill)

        # Counted after the accuracy bar, so the header agrees with the list under it.
        counts = {k: sum(1 for p in v if counts_toward_rating(p))
                  for k, v in self.pools.items()}
        self.detail_label.config(text=t(
            "head.plays", official=counts.get("official", 0),
            mame=counts.get("mamesosu", 0), combined=counts.get("combined", 0)))

    def _on_skill_select(self, _event) -> None:
        sel = self.skill_tree.selection()
        if sel:
            self.selected_skill = sel[0]
            self.radar.select_skill(self.selected_skill if self.selected_skill != "overall" else None)
            self._fill_play_tree()
            self._update_rec_hint()

    def _rec_target(self) -> str | None:
        """The skillset to recommend for: the selected one, else the weakest."""
        if self.selected_skill in PATTERN_OF:
            return self.selected_skill
        weak = weaknesses(self.ratings.get("combined", {}), limit=1)
        return weak[0][0] if weak else None

    def _update_rec_hint(self) -> None:
        skill = self._rec_target()
        dan = (self.dans.get("combined") or {}).get("raw")
        if skill is None or dan is None:
            self.rec_hint.config(text=t("hint.refresh_first"))
            return
        auto = "" if self.selected_skill in PATTERN_OF else t("hint.weakest")
        self.rec_hint.config(text=t("hint.target", skill=DISPLAY_NAME[skill],
                                    auto=auto, dan=f"{dan:.1f}"))

    def _sync_reference_boxes(self) -> None:
        """Point both pickers and the column heading at the current selection.

        The level list belongs to the system: osu! grades charts by OD and Etterna by
        judge, and they are not two names for the same dial.
        """
        osu = self.ref_system == "osu"
        self.system_box.set(t("sys.osu") if osu else t("sys.wife"))
        if osu:
            self.level_box.config(values=[f"OD {i}" for i in range(11)])
            self.level_box.set(f"OD {self.ref_od:.0f}")
            self.play_tree.heading("ref", text=t("col.acc_od", od=f"{self.ref_od:.0f}"))
        else:
            self.level_box.config(values=[f"J{j}" for j in sorted(JUDGE_SCALES)])
            self.level_box.set(f"J{self.ref_judge}")
            self.play_tree.heading("ref", text=t("col.wife_j", judge=self.ref_judge))

    def _on_reference_change(self, event=None) -> None:
        chosen = "wife" if self.system_box.get() == t("sys.wife") else "osu"
        switched = chosen != self.ref_system
        self.ref_system = chosen
        # On a switch the level box still holds the old system's value, so it is left
        # alone and the remembered one for the new system is put back instead.
        if not switched:
            level = self.level_box.get().strip()
            if chosen == "osu":
                self.ref_od = float(level.replace("OD", "").strip() or REFERENCE_OD)
            else:
                self.ref_judge = int(level.lstrip("Jj") or DEFAULT_JUDGE)
        settings.update(ref_system=self.ref_system, ref_od=self.ref_od,
                        judge=self.ref_judge)
        self._sync_reference_boxes()
        self._fill_play_tree()
        self.focus_set()   # drop the combobox highlight

    def _fill_play_tree(self) -> None:
        self.play_tree.delete(*self.play_tree.get_children())
        pool = self.pools.get("combined", [])
        for p in contributions(pool, self.selected_skill,
                               ref_od=self.ref_od, judge=self.ref_judge):
            src = {"official": "osu!", "mamesosu": "mame"}.get(p["source"], "local")
            spread = p.get("spread")
            ref = p.get("acc_at") if self.ref_system == "osu" else p.get("wife")
            pct = lambda v: f"{max(0.0, v) * 100:.2f}%" if v is not None else "-"
            self.play_tree.insert("", "end", tags=(p["source"],), values=(
                src, f"{p['effective']:.2f}", f"{p['accuracy'] * 100:.2f}%",
                pct(ref), f"{spread:.1f}" if spread is not None else "-",
                p["mods"], p["label"]))

    # ---------- recommendations ----------

    def start_recommend(self) -> None:
        if self.rec_worker and self.rec_worker.is_alive():
            return
        skill = self._rec_target()
        dan = (self.dans.get("combined") or {}).get("raw")
        if skill is None or dan is None:
            messagebox.showinfo("mania-skills", t("msg.refresh_first"))
            return
        played = {p["beatmap_id"] for p in self.plays}
        self.rec_btn.config(state="disabled", text=t("btn.searching"))
        self.rec_worker = threading.Thread(
            target=self._recommend,
            args=(skill, dan, played, self.timing_model), daemon=True)
        self.rec_worker.start()

    def _recommend(self, skill: str, dan: float, played: set[int], model) -> None:
        try:
            maps = recommend(skill, dan, played, _session(), model=model)
            self.events.put(("recommend", maps))
        except Exception as exc:  # surfaced in the UI rather than killing the thread
            self.events.put(("rec-error", str(exc)))

    def _fill_rec_tree(self) -> None:
        self.rec_tree.delete(*self.rec_tree.get_children())
        for m in self.recommended:
            pred = m.get("predicted")
            self.rec_tree.insert("", "end", tags=(m["status"],), values=(
                m["dan_label"], f"{m['skill_msd']:.1f}", f"{m['focus'] * 100:.0f}%",
                f"{m['bpm']:.0f}", f"{pred * 100:.1f}%" if pred else "-",
                t(f"band.{m['band']}") if m.get("band") else "-",
                ", ".join(m.get("tags") or []),
                t(f"status.{m['status']}"), m["label"]))

    def _open_recommended(self, _event) -> None:
        sel = self.rec_tree.selection()
        if not sel:
            return
        idx = self.rec_tree.index(sel[0])
        if idx < len(self.recommended):
            webbrowser.open(self.recommended[idx]["url"])

    # ---------- refresh worker ----------

    def start_refresh(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        # Both servers resolve a username as readily as an id, and mamesosu is an account
        # most players do not have at all - so only the osu! side is required, and neither
        # is required to be a number.
        osu_id = self.osu_id.get().strip()
        mame_id = self.mame_id.get().strip()
        if not osu_id:
            messagebox.showerror("mania-skills", t("msg.need_osu_id"))
            return
        # Asked before the first scan, not after: the whole point of the question is that
        # the answer decides which clears get analysed.
        if not _players_asked() and len(players_seen()) > 1:
            self._pick_players()
            if not _players_asked():
                return  # dismissed without answering - nothing to analyse against yet

        self.refresh_btn.config(state="disabled", text=t("btn.working"))
        self.stop_dans.set()          # let any previous dan pass finish its request
        if self.worker:
            self.worker.join(timeout=5)
        self.stop_dans.clear()
        settings.update(osu_id=osu_id, mame_id=mame_id)
        self.worker = threading.Thread(target=self._refresh, args=(osu_id, mame_id), daemon=True)
        self.worker.start()

    def _refresh(self, osu_id: str, mame_id: str) -> None:
        try:
            self.events.put(("status", t("status.collecting")))

            # All three sources at once: two servers on the network and the local
            # database on disk have nothing to wait on each other for, and the slow one
            # is whichever the machine happens to have most of.
            #
            # Each is also allowed to fail on its own. A wrong name or a server that is
            # down costs only that source - and losing the local database, the only one
            # that sees dan courses at all, to somebody else's outage was the worst of
            # the old behaviour.
            def _local():
                songs = LocalSongs().load()
                return songs, read_local_scores(songs, players=_saved_players())

            # A Session is not safe to share, so each source opens its own.
            wanted = [("osu!", lambda: fetch_official(osu_id, _session()))]
            if mame_id:
                wanted.append(("mamesosu", lambda: fetch_mamesosu(mame_id, 100, _session())))

            plays: list = []
            failed: list[tuple[str, str]] = []
            with ThreadPoolExecutor(max_workers=len(wanted) + 1) as pool:
                local_job = pool.submit(_local)
                jobs = [(label, pool.submit(fetch)) for label, fetch in wanted]
                for label, job in jobs:
                    try:
                        plays += job.result()
                    except SourceError as exc:
                        failed.append((label, t(f"err.{exc.reason}")))
                songs, local_plays = local_job.result()
                plays += local_plays
            self.events.put(("sources", [t("err.source", source=s, reason=r)
                                         for s, r in failed]))

            if not plays:
                # An empty radar looks like a working app that rates you at zero, so say
                # what happened instead of drawing one. The reasons are repeated bare
                # here: "the rest still counted" is untrue when there is no rest.
                self.events.put(("error", "\n".join(
                    [f"{s}: {r}" for s, r in failed] + ["", t("msg.no_plays")])))
                return

            cache = MsdCache()

            def progress(done: int, total: int) -> None:
                self.events.put(("status", t("status.rating", done=done, total=total)))
                if done % 25 == 0:
                    cache.save()

            # Dans are left to _fetch_dans: they are one slow request each, and the
            # ratings are worth showing long before the last one lands.
            rate_plays(plays, cache, songs=songs, progress=progress, dans=False)
            cache.save()
            self.events.put(("done", self._publish(plays)))

            self._fetch_dans(plays, cache)
        except Exception as exc:  # surfaced in the UI rather than killing the thread
            self.events.put(("error", str(exc)))

    def _publish(self, plays: list) -> list[dict]:
        rated = [asdict(p) for p in plays if p.msd]
        (cache_dir() / "plays.json").write_text(json.dumps(rated, ensure_ascii=False),
                                                encoding="utf-8")
        return rated

    def _fetch_dans(self, plays: list, cache: MsdCache) -> None:
        """Fill in chart dans behind the already-visible ratings.

        Ordered so the charts that can reach a dan window come first, which is why the
        number stops moving well before the queue empties. Everything is cached, so an
        interrupted run just picks up where it left off.
        """
        def progress(done: int, total: int) -> None:
            self.events.put(("status", t("status.dans", done=done, total=total)))
            if done % 20 == 0:
                self.events.put(("dans", self._publish(plays)))

        fetch_dans(plays, cache, progress=progress,
                   should_stop=lambda: self.stop_dans.is_set())
        self.events.put(("dans", self._publish(plays)))
        self.events.put(("status", t("status.rated", count=len(plays))))

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "status":
                    self.status.config(text=payload)
                elif kind == "done":
                    self.plays = payload
                    self.recompute()
                    self.status.config(text=t("status.rated", count=len(payload)))
                    self.refresh_btn.config(state="normal", text=t("btn.refresh"))
                elif kind == "dans":
                    self.plays = payload
                    self.recompute()
                elif kind == "sources":
                    self.source_failures = payload
                    self._fill_coverage()
                elif kind == "recommend":
                    self.recommended = payload
                    self._fill_rec_tree()
                    self.rec_btn.config(state="normal", text=t("btn.find_maps"))
                elif kind == "rec-error":
                    self.rec_btn.config(state="normal", text=t("btn.find_maps"))
                    messagebox.showerror("mania-skills", payload)
                elif kind == "error":
                    self.status.config(text=t("status.failed"))
                    self.refresh_btn.config(state="normal", text=t("btn.refresh"))
                    messagebox.showerror("mania-skills", payload)
        except queue.Empty:
            pass
        self.after(80, self._drain_events)


if __name__ == "__main__":
    App().mainloop()
