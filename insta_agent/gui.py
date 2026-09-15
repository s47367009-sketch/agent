"""رابط گرافیکی ایجنت ریلز اینستاگرام — CustomTkinter، تم تیره."""

from __future__ import annotations

import hashlib
import os
import queue
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from .bot import ReelsBot
from .downloader import DownloadManager
from .human import PROFILES, estimate_seconds_per_item

APP_DIR = Path.home() / ".insta_reels_agent"
SESSIONS_DIR = APP_DIR / "sessions"
DEFAULT_DOWNLOADS = Path.home() / "Downloads" / "InstaReels"

IG_COLORS = ["#FEDA75", "#FA7E1E", "#D62976", "#962FBF", "#4F5BD5"]
BG = "#0E1116"
PANEL = "#151B24"
CARD = "#1A2230"
TEXT_DIM = "#8B95A7"
ACCENT = "#D62976"
GREEN = "#22C55E"
RED = "#EF4444"
AMBER = "#F59E0B"

STATE_STYLE = {
    "idle":          ("آماده", TEXT_DIM),
    "launching":     ("باز کردن مرورگر…", "#4F5BD5"),
    "loading":       ("بارگذاری…", "#4F5BD5"),
    "logging_in":    ("در حال ورود…", AMBER),
    "awaiting_user": ("منتظر اقدام شما در مرورگر", AMBER),
    "logged_in":     ("وارد شد", GREEN),
    "scrolling":     ("در حال اسکرول ریلز 🎬", ACCENT),
    "done":          ("تمام شد ✅", GREEN),
    "stopped":       ("متوقف شد ⏹️", AMBER),
    "error":         ("خطا ❌", RED),
}

PACE_LABELS = {"safe": "امن 🐢", "balanced": "متعادل ⚖️", "fast": "سریع ⚡"}
PACE_KEYS = list(PACE_LABELS.keys())


# ---------------------------------------------------------------- ابزار ظاهری
class GradientBar(ctk.CTkCanvas):
    """نوار گرادیان اینستاگرامی بالای پنجره."""

    def __init__(self, master, height: int = 6, **kwargs):
        super().__init__(master, height=height, highlightthickness=0, bd=0, bg=BG, **kwargs)
        self._height = height
        self.bind("<Configure>", self._redraw)

    def _redraw(self, event=None) -> None:
        self.delete("all")
        w = self.winfo_width() or 600
        stops = [self._hex2rgb(c) for c in IG_COLORS]
        n = len(stops) - 1
        for x in range(w):
            t = x / max(1, w - 1) * n
            i = min(int(t), n - 1)
            f = t - i
            c0, c1 = stops[i], stops[i + 1]
            r = int(c0[0] + (c1[0] - c0[0]) * f)
            g = int(c0[1] + (c1[1] - c0[1]) * f)
            b = int(c0[2] + (c1[2] - c0[2]) * f)
            self.create_line(x, 0, x, self._height, fill=f"#{r:02x}{g:02x}{b:02x}")

    @staticmethod
    def _hex2rgb(h: str):
        h = h.lstrip("#")
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


# ---------------------------------------------------------------- اپ اصلی
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        self.title("ایجنت ریلز اینستاگرام 👁️")
        self.geometry("1120x740")
        self.minsize(980, 640)
        self.configure(fg_color=BG)

        self.events: queue.Queue = queue.Queue()
        self.bot: ReelsBot | None = None
        self.downloader: DownloadManager | None = None
        self.manifest = None
        self.stop_event: threading.Event | None = None
        self.link_queue: queue.Queue | None = None
        self.producer_done: threading.Event | None = None
        self._job_running = False
        self._start_ts: float | None = None

        self.target_var = ctk.IntVar(value=500)
        self.count_viewed = 0
        self.count_downloaded = 0
        self.count_failed = 0

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(120, self._poll_events)
        self.after(1000, self._tick_clock)

    # ------------------------------------------------------------ ساخت UI
    def _build_ui(self) -> None:
        GradientBar(self).pack(fill="x", side="top")

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=18, pady=(12, 4))
        ctk.CTkLabel(
            header, text="👁️  ایجنت ریلز اینستاگرام", font=ctk.CTkFont(size=22, weight="bold")
        ).pack(side="right", padx=(0, 6))
        self.state_var = ctk.StringVar(value=STATE_STYLE["idle"][0])
        self.state_pill = ctk.CTkLabel(
            header, textvariable=self.state_var, font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#232B38", corner_radius=14, padx=14, pady=4,
        )
        self.state_pill.pack(side="left", padx=6)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=14, pady=(4, 8))
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=0)
        body.grid_rowconfigure(0, weight=1)

        self._build_main(body).grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self._build_sidebar(body).grid(row=0, column=1, sticky="nsew")

        ctk.CTkLabel(
            self,
            text="⚠️ فقط برای استفاده‌ی شخصی — مسئولیت رعایت قوانین اینستاگرام و حق‌نشر با کاربر است.",
            text_color=TEXT_DIM, font=ctk.CTkFont(size=11),
        ).pack(side="bottom", pady=(0, 8))

    # ---- پنل کنترل (سمت راست) ----
    def _build_sidebar(self, parent) -> ctk.CTkFrame:
        sb = ctk.CTkFrame(parent, width=300, corner_radius=14, fg_color=PANEL)
        sb.grid_propagate(False)
        pad = {"padx": 14, "pady": (10, 0)}

        ctk.CTkLabel(sb, text="حساب اینستاگرام", font=ctk.CTkFont(size=15, weight="bold")).pack(anchor="e", **pad)
        self.username_entry = ctk.CTkEntry(sb, placeholder_text="username", justify="left")
        self.username_entry.pack(fill="x", padx=14, pady=(8, 6))

        pw_row = ctk.CTkFrame(sb, fg_color="transparent")
        pw_row.pack(fill="x", padx=14)
        self.password_entry = ctk.CTkEntry(pw_row, placeholder_text="password", show="•", justify="left")
        self.password_entry.pack(side="left", fill="x", expand=True)
        self.show_pw_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            pw_row, text="نمایش", variable=self.show_pw_var, width=60,
            command=lambda: self.password_entry.configure(show="" if self.show_pw_var.get() else "•"),
        ).pack(side="left", padx=(8, 0))

        self.clear_session_btn = ctk.CTkButton(
            sb, text="🗑️ حذف نشست ذخیره‌شده", fg_color="#26303F", hover_color="#32405A",
            height=28, command=self._clear_session,
        )
        self.clear_session_btn.pack(fill="x", padx=14, pady=(8, 0))

        ctk.CTkLabel(sb, text="تعداد ریل‌ها", font=ctk.CTkFont(size=15, weight="bold")).pack(anchor="e", **pad)
        self.target_label = ctk.CTkLabel(sb, text="500 ریل", text_color=ACCENT, font=ctk.CTkFont(size=13, weight="bold"))
        self.target_label.pack(anchor="e", padx=14)
        ctk.CTkSlider(
            sb, from_=10, to=1000, number_of_steps=99, variable=self.target_var,
            command=lambda v: self.target_label.configure(text=f"{int(v)} ریل"),
        ).pack(fill="x", padx=14, pady=(2, 0))

        ctk.CTkLabel(sb, text="ریتم اسکرول", font=ctk.CTkFont(size=15, weight="bold")).pack(anchor="e", **pad)
        self.pace_var = ctk.StringVar(value=PACE_LABELS["balanced"])
        ctk.CTkSegmentedButton(sb, values=[PACE_LABELS[k] for k in PACE_KEYS], variable=self.pace_var).pack(fill="x", padx=14, pady=(8, 0))

        ctk.CTkLabel(sb, text="مرورگر", font=ctk.CTkFont(size=15, weight="bold")).pack(anchor="e", **pad)
        self.browser_var = ctk.StringVar(value="Chromium (پیش‌فرض)")
        ctk.CTkSegmentedButton(sb, values=["Chromium (پیش‌فرض)", "Google Chrome"], variable=self.browser_var).pack(fill="x", padx=14, pady=(8, 0))
        self.headless_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(sb, text="حالت مخفی (بدون نمایش پنجره)", variable=self.headless_var).pack(anchor="e", padx=14, pady=(6, 0))

        ctk.CTkLabel(sb, text="پوشه‌ی دانلود", font=ctk.CTkFont(size=15, weight="bold")).pack(anchor="e", **pad)
        today = datetime.now().strftime("%Y-%m-%d")
        self.folder_var = ctk.StringVar(value=str(DEFAULT_DOWNLOADS / today))
        ctk.CTkLabel(sb, textvariable=self.folder_var, text_color=TEXT_DIM, font=ctk.CTkFont(size=10), wraplength=260).pack(padx=14, pady=(4, 0))
        folder_row = ctk.CTkFrame(sb, fg_color="transparent")
        folder_row.pack(fill="x", padx=14, pady=(6, 0))
        ctk.CTkButton(folder_row, text="📁 انتخاب پوشه…", fg_color="#26303F", hover_color="#32405A", height=28,
                      command=self._pick_folder).pack(side="left", fill="x", expand=True, padx=(0, 3))
        ctk.CTkButton(folder_row, text="📂 بازکردن", fg_color="#26303F", hover_color="#32405A", height=28, width=90,
                      command=self._open_download_folder).pack(side="left", padx=(3, 0))

        # دکمه‌های اصلی
        self.start_btn = ctk.CTkButton(
            sb, text="▶️  شروع ایجنت", height=44, font=ctk.CTkFont(size=15, weight="bold"),
            fg_color=ACCENT, hover_color="#B01762", command=self._start,
        )
        self.start_btn.pack(fill="x", padx=14, pady=(16, 6))
        self.stop_btn = ctk.CTkButton(
            sb, text="⏹️  توقف", height=36, fg_color=RED, hover_color="#B91C1C",
            state="disabled", command=self._stop,
        )
        self.stop_btn.pack(fill="x", padx=14, pady=(0, 14))
        return sb

    # ---- بخش اصلی ----
    def _build_main(self, parent) -> ctk.CTkFrame:
        main = ctk.CTkFrame(parent, corner_radius=14, fg_color=PANEL)

        stats = ctk.CTkFrame(main, fg_color="transparent")
        stats.pack(fill="x", padx=14, pady=(14, 6))
        for i in range(4):
            stats.grid_columnconfigure(i, weight=1)
        self.stat_viewed = self._stat_card(stats, "👁️ بازدید شد", 0, "#4F5BD5")
        self.stat_downloaded = self._stat_card(stats, "⬇️ دانلود شد", 1, GREEN)
        self.stat_failed = self._stat_card(stats, "❌ ناموفق", 2, RED)
        self.stat_time = self._stat_card(stats, "⏱️ زمان / تخمین پایان", 3, AMBER)

        prog_row = ctk.CTkFrame(main, fg_color="transparent")
        prog_row.pack(fill="x", padx=14, pady=(4, 2))
        self.progress = ctk.CTkProgressBar(prog_row, progress_color=ACCENT)
        self.progress.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.progress.set(0)
        self.progress_label = ctk.CTkLabel(prog_row, text="0 / 500", width=90)
        self.progress_label.pack(side="left")

        tabs = ctk.CTkTabview(main, fg_color=CARD, segmented_button_fg_color=PANEL,
                              segmented_button_selected_color=ACCENT, anchor="e")
        tabs.pack(fill="both", expand=True, padx=14, pady=(6, 14))
        log_tab = tabs.add("📜 رویدادها")
        reels_tab = tabs.add("🎬 ریل‌های اخیر")

        self.log_box = ctk.CTkTextbox(log_tab, font=("Consolas", 12), wrap="word", state="disabled")
        self.log_box.pack(fill="both", expand=True)

        top = ctk.CTkFrame(reels_tab, fg_color="transparent")
        top.pack(fill="x", pady=(0, 4))
        self.copy_btn = ctk.CTkButton(top, text="📋 کپی همه‌ی لینک‌ها", width=160, height=26,
                                      fg_color="#26303F", hover_color="#32405A", state="disabled",
                                      command=self._copy_links)
        self.copy_btn.pack(side="left")
        self.reels_list = ctk.CTkScrollableFrame(reels_tab, fg_color="#131926")
        self.reels_list.pack(fill="both", expand=True)
        self._reel_rows: dict[str, tuple[ctk.CTkFrame, ctk.CTkLabel, str]] = {}
        self._reel_order: list[str] = []

        self._log("سلام! 👋 یوزرنیم/پسورد را وارد کن، پوشه را انتخاب کن و «شروع ایجنت» را بزن.")
        self._log("🔒 رمز فقط در حافظه می‌ماند و هیچ‌جا ذخیره نمی‌شود؛ فقط نشست (کوکی) ذخیره می‌شود.")
        self._log("👁️ حالت فقط-تماشا: ایجنت لایک/کامنت/فالو انجام نمی‌دهد — فقط اسکرول.", "ok")
        return main

    def _stat_card(self, parent, title: str, col: int, color: str):
        card = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=12)
        card.grid(row=0, column=col, sticky="nsew", padx=4)
        ctk.CTkLabel(card, text=title, text_color=TEXT_DIM, font=ctk.CTkFont(size=12)).pack(pady=(10, 0))
        var = ctk.StringVar(value="0" if col != 3 else "—")
        ctk.CTkLabel(card, textvariable=var, text_color=color, font=ctk.CTkFont(size=20, weight="bold")).pack(pady=(0, 10))
        return var

    # ------------------------------------------------------------ کنترل اجرا
    def _start(self) -> None:
        if self._job_running:
            return
        username = self.username_entry.get().strip()
        password = self.password_entry.get()
        target = max(1, int(self.target_var.get()))

        session_path = self._session_path(username) if username else None
        has_session = bool(session_path and session_path.exists())
        if not username and not has_session:
            messagebox.showerror("خطا", "یوزرنیم اینستاگرام را وارد کنید.")
            return
        if not password and not has_session:
            if not messagebox.askyesno("بدون رمز؟", "رمز خالی است و نشست ذخیره‌شده‌ای هم نیست.\nممکن است ورود ناموفق شود. ادامه می‌دهید؟"):
                return
        if target >= 400:
            self._log("⚠️ تعداد بالا: چندصد ریل پشت‌سرهم ممکن است اینستاگرام را حساس کند؛ ریتم «امن» پیشنهاد می‌شود.", "warn")

        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        session_path = session_path or self._session_path("default")
        cookie_path = SESSIONS_DIR / f"cookies_{session_path.stem}.txt"
        out_dir = Path(self.folder_var.get()).expanduser()
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("خطا", f"ساخت پوشه ممکن نشد:\n{exc}")
            return

        # ریست شمارنده‌ها
        self.count_viewed = self.count_downloaded = self.count_failed = 0
        self.stat_viewed.set("0")
        self.stat_downloaded.set("0")
        self.stat_failed.set("0")
        self.progress.set(0)
        self.progress_label.configure(text=f"0 / {target}")
        self._start_ts = time.time()

        self.stop_event = threading.Event()
        self.link_queue = queue.Queue()
        self.producer_done = threading.Event()

        from .manifest import Manifest
        self.manifest = Manifest(out_dir)

        self.downloader = DownloadManager(
            out_dir=out_dir, events=self.events, stop_event=self.stop_event,
            link_queue=self.link_queue, producer_done=self.producer_done,
            manifest=self.manifest, workers=2,
        )
        pace_key = next((k for k in PACE_KEYS if PACE_LABELS[k] == self.pace_var.get()), "balanced")
        channel = "chrome" if "Chrome" in self.browser_var.get() else None
        self.bot = ReelsBot(
            username=username, password=password, target=target,
            events=self.events, stop_event=self.stop_event,
            link_queue=self.link_queue, producer_done=self.producer_done,
            session_path=session_path, cookie_path=cookie_path,
            headless=self.headless_var.get(), channel=channel,
            pace=pace_key, manifest=self.manifest,
        )

        if self.headless_var.get() and (password or username):
            self._log("ℹ️ حالت مخفی فعال است — اگر کد دو مرحله‌ای/چالش لازم شود، حالت مخفی را خاموش کنید.", "warn")

        self._job_running = True
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self._set_state("launching")
        self.downloader.start()
        self.bot.start()
        self._log(f"🎬 شروع — هدف: {target} ریل | ریتم: {PACE_LABELS[pace_key]} | پوشه: {out_dir}")

    def _stop(self) -> None:
        if not self._job_running:
            return
        self._log("⏹️ درخواست توقف — ایجنت به‌آرامی متوقف می‌شود…", "warn")
        self.stop_btn.configure(state="disabled")
        if self.stop_event:
            self.stop_event.set()
        if self.producer_done:
            self.producer_done.set()

    def _finish(self) -> None:
        self._job_running = False
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        if self.manifest:
            self.manifest.close()
        ok, bad = (self.manifest.stats if self.manifest else (0, 0))
        self._log(f"🏁 پایان کار — بازدید: {self.count_viewed} | دانلود: {ok} | ناموفق: {bad}")

    def _clear_session(self) -> None:
        username = self.username_entry.get().strip()
        p = self._session_path(username) if username else None
        targets = list(SESSIONS_DIR.glob("*")) if not p else [p, SESSIONS_DIR / f"cookies_{p.stem}.txt"]
        removed = 0
        for t in targets:
            try:
                if t.exists():
                    t.unlink()
                    removed += 1
            except OSError:
                pass
        self._log(f"🗑️ {removed} فایل نشست حذف شد.")

    def _session_path(self, username: str) -> Path:
        h = hashlib.sha1(username.strip().lower().encode("utf-8")).hexdigest()[:12]
        return SESSIONS_DIR / f"ig_{h}.json"

    def _pick_folder(self) -> None:
        d = filedialog.askdirectory(initialdir=str(Path.home()))
        if d:
            self.folder_var.set(d)

    def _open_download_folder(self) -> None:
        p = Path(self.folder_var.get()).expanduser()
        try:
            p.mkdir(parents=True, exist_ok=True)
            if sys.platform.startswith("win"):
                os.startfile(str(p))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(p)])
            else:
                subprocess.Popen(["xdg-open", str(p)])
        except Exception as exc:  # noqa: BLE001
            self._log(f"⚠️ بازکردن پوشه ناموفق بود: {exc}", "warn")

    def _copy_links(self) -> None:
        if not self.manifest or not self.manifest.links_path.exists():
            return
        text = self.manifest.links_path.read_text(encoding="utf-8", errors="ignore").strip()
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)
            self._log(f"📋 {len(text.splitlines())} لینک در کلیپ‌بورد کپی شد.")

    # ------------------------------------------------------------ رویدادها
    def _poll_events(self) -> None:
        try:
            for _ in range(60):
                ev = self.events.get_nowait()
                self._handle_event(ev)
        except queue.Empty:
            pass

        if self._job_running:
            bot_alive = self.bot.is_alive() if self.bot else False
            dl_alive = self.downloader.alive() if self.downloader else False
            if not bot_alive and not dl_alive:
                self._finish()

        self.after(120, self._poll_events)

    def _handle_event(self, ev: tuple) -> None:
        kind = ev[0]
        if kind == "log":
            self._log(ev[1], ev[2] if len(ev) > 2 else "info")
        elif kind == "viewed":
            self.count_viewed = ev[1]
            self.stat_viewed.set(str(self.count_viewed))
            self._update_progress()
            self._add_reel_row(ev[2], REEL_CODE(ev[2]), "seen")
            self.copy_btn.configure(state="normal")
        elif kind == "downloaded":
            self.count_downloaded += 1
            self.stat_downloaded.set(str(self.count_downloaded))
            self._add_reel_row_by_code(ev[1], "ok")
        elif kind == "failed":
            self.count_failed += 1
            self.stat_failed.set(str(self.count_failed))
            self._add_reel_row_by_code(ev[1], "fail")
            self._log(f"❌ دانلود ناموفق ({ev[1]}): {ev[2]}", "warn")
        elif kind == "cookies_ready":
            if ev[1] and self.downloader:
                self.downloader.set_cookiefile(ev[1])
        elif kind == "state":
            self._set_state(ev[1])
        elif kind == "fatal":
            self._set_state("error")
            self._log(f"💥 خطای مرگبار: {ev[1]}", "error")
        elif kind == "bot_done":
            pass

    def _set_state(self, name: str) -> None:
        label, color = STATE_STYLE.get(name, (name, TEXT_DIM))
        self.state_var.set(label)
        self.state_pill.configure(text_color=color)

    def _update_progress(self) -> None:
        target = max(1, int(self.target_var.get()))
        self.progress.set(min(1.0, self.count_viewed / target))
        self.progress_label.configure(text=f"{self.count_viewed} / {target}")

    def _add_reel_row_by_code(self, code: str, status: str) -> None:
        row = self._reel_rows.get(code)
        if row:
            self._paint_row(row, code, status)

    def _add_reel_row(self, url: str, code: str, status: str) -> None:
        if code in self._reel_rows:
            return
        row = ctk.CTkFrame(self.reels_list, fg_color="#1B2331", corner_radius=8)
        dot = ctk.CTkLabel(row, text="●", width=18)
        dot.pack(side="left", padx=(8, 2))
        self._paint_row((row, dot, url), code, status, skip_pack=True)
        ctk.CTkLabel(row, text=code, font=("Consolas", 12)).pack(side="left", padx=6)
        ctk.CTkButton(
            row, text="کپی لینک", width=70, height=22, fg_color="#26303F", hover_color="#32405A",
            command=lambda u=url: (self.clipboard_clear(), self.clipboard_append(u)),
        ).pack(side="right", padx=6, pady=4)

        row.pack(fill="x", pady=2, padx=2)
        self._reel_rows[code] = (row, dot, url)
        self._reel_order.append(code)
        while len(self._reel_order) > 80:
            old = self._reel_order.pop(0)
            w = self._reel_rows.pop(old, None)
            if w:
                w[0].destroy()

    @staticmethod
    def _paint_row(row_tuple, code: str, status: str, skip_pack: bool = False) -> None:
        _row, dot, _url = row_tuple
        colors = {"seen": TEXT_DIM, "ok": GREEN, "fail": RED}
        dot.configure(text_color=colors.get(status, TEXT_DIM))

    # ------------------------------------------------------------ لاگ و ساعت
    def _log(self, msg: str, level: str = "info") -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{stamp}] {msg}\n")
        lines = int(self.log_box.index("end-1c").split(".")[0])
        if lines > 1200:
            self.log_box.delete("1.0", f"{lines - 1100}.0")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _tick_clock(self) -> None:
        if self._job_running and self._start_ts:
            elapsed = int(time.time() - self._start_ts)
            target = max(1, int(self.target_var.get()))
            pace_key = next((k for k in PACE_KEYS if PACE_LABELS[k] == self.pace_var.get()), "balanced")
            remaining = max(0, target - self.count_viewed)
            eta = int(remaining * estimate_seconds_per_item(pace_key))
            self.stat_time.set(f"{fmt_duration(elapsed)} / ~{fmt_duration(eta)}")
        self.after(1000, self._tick_clock)

    # ------------------------------------------------------------ خروج
    def _on_close(self) -> None:
        if self._job_running:
            if not messagebox.askyesno("خروج", "ایجنت در حال اجراست. توقف و خروج؟"):
                return
            self._stop()
        if self.manifest:
            self.manifest.close()
        self.destroy()


def REEL_CODE(url: str) -> str:
    import re
    m = re.search(r"/reel/([A-Za-z0-9_-]+)", url or "")
    return m.group(1) if m else (url or "?")[-12:]


def fmt_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
