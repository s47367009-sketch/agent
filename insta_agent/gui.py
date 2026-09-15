"""رابط گرافیکی ایجنت ریلز اینستاگرام — CustomTkinter، دارک‌تم مدرن."""

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

# ------------------------------------------------------------------ پالت رنگ
IG_COLORS = ["#FEDA75", "#FA7E1E", "#D62976", "#962FBF", "#4F5BD5"]
BG = "#0A0D12"          # زمینه‌ی اصلی — خیلی تیره
PANEL = "#11151D"       # پنل‌ها
CARD = "#171D29"        # کارت‌ها
CARD_2 = "#1C2432"      # کارت روشن‌تر/ردیف‌ها
BORDER = "#232C3D"      # حاشیه‌ی ظریف
TEXT = "#E8ECF4"
TEXT_DIM = "#8B95A7"
ACCENT = "#E1306C"
ACCENT_HOVER = "#B01762"
GREEN = "#2BD576"
RED = "#F2545B"
AMBER = "#F5A623"
BLUE = "#4F7DF9"

FONT = "Segoe UI"

STATE_STYLE = {
    "idle":          ("آماده", TEXT_DIM),
    "launching":     ("باز کردن مرورگر…", BLUE),
    "loading":       ("بارگذاری…", BLUE),
    "logging_in":    ("در حال ورود…", AMBER),
    "awaiting_user": ("منتظر اقدام شما در مرورگر", AMBER),
    "logged_in":     ("وارد شد", GREEN),
    "scrolling":     ("در حال اسکرول ریلز", ACCENT),
    "done":          ("تمام شد", GREEN),
    "stopped":       ("متوقف شد", AMBER),
    "error":         ("خطا", RED),
}

PACE_LABELS = {"safe": "امن 🐢", "balanced": "متعادل ⚖️", "fast": "سریع ⚡"}
PACE_KEYS = ["safe", "balanced", "fast"]
DEFAULT_PACE = "fast"

LOG_COLORS = {"info": TEXT, "ok": GREEN, "warn": AMBER, "error": RED}


# ---------------------------------------------------------------- ویجت‌های سفارشی
class GradientBar(ctk.CTkCanvas):
    """نوار گرادیان اینستاگرامی بالای پنجره."""

    def __init__(self, master, height: int = 7, **kwargs):
        super().__init__(master, height=height, highlightthickness=0, bd=0, bg=BG, **kwargs)
        self._h = height
        self.bind("<Configure>", self._redraw)

    def _redraw(self, _event=None) -> None:
        self.delete("all")
        w = self.winfo_width() or 900
        stops = [_hex2rgb(c) for c in IG_COLORS]
        n = len(stops) - 1
        for x in range(w):
            t = x / max(1, w - 1) * n
            i = min(int(t), n - 1)
            f = t - i
            c0, c1 = stops[i], stops[i + 1]
            r = int(c0[0] + (c1[0] - c0[0]) * f)
            g = int(c0[1] + (c1[1] - c0[1]) * f)
            b = int(c0[2] + (c1[2] - c0[2]) * f)
            self.create_line(x, 0, x, self._h, fill=f"#{r:02x}{g:02x}{b:02x}")


class ProgressRing(ctk.CTkCanvas):
    """حلقه‌ی پیشرفت با گرادیان اینستاگرامی و درصد در مرکز."""

    def __init__(self, master, size: int = 176, thickness: int = 13, **kwargs):
        super().__init__(master, width=size, height=size, highlightthickness=0, bd=0, bg=PANEL, **kwargs)
        self.size, self.th = size, thickness
        self._frac = 0.0
        self._center = "0٪"
        self._sub = "0 / 0"
        self.after(60, self._draw)

    def set(self, frac: float, center: str, sub: str) -> None:
        frac = max(0.0, min(1.0, frac))
        if abs(frac - self._frac) < 1e-9 and center == self._center and sub == self._sub:
            return
        self._frac, self._center, self._sub = frac, center, sub
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        s, th = self.size, self.th
        pad = th + 6
        box = (pad, pad, s - pad, s - pad)
        # حلقه‌ی پس‌زمینه
        self.create_oval(*box, outline=BORDER, width=th)
        # قوس‌های گرادیانی (هر رنگ ۷۲ درجه)
        per = 360 / len(IG_COLORS)
        filled = self._frac * 360
        for i, color in enumerate(IG_COLORS):
            span = filled - i * per
            if span <= 0:
                break
            extent = -min(span, per)
            start = 90 - i * per
            # گوشه‌های گرد با دو دایره‌ی کوچک در ابتدا/انتهای هر قوس
            self.create_arc(*box, start=start, extent=extent, style="arc",
                            outline=color, width=th)
        cx = cy = s / 2
        self.create_text(cx, cy - 12, text=self._center, fill=TEXT, font=(FONT, 27, "bold"))
        self.create_text(cx, cy + 18, text=self._sub, fill=TEXT_DIM, font=(FONT, 11))


class StatCard(ctk.CTkFrame):
    def __init__(self, master, icon: str, title: str, color: str):
        super().__init__(master, fg_color=CARD, corner_radius=12,
                         border_width=1, border_color=BORDER)
        ctk.CTkLabel(self, text=f"{icon}  {title}", text_color=TEXT_DIM,
                     font=ctk.CTkFont(family=FONT, size=12)).pack(anchor="e", padx=12, pady=(10, 0))
        self.var = ctk.StringVar(value="0")
        ctk.CTkLabel(self, textvariable=self.var, text_color=color,
                     font=ctk.CTkFont(family=FONT, size=22, weight="bold")).pack(anchor="e", padx=12, pady=(0, 10))


class SideCard(ctk.CTkFrame):
    """کارت گروه‌بندی تنظیمات در سایدبار."""

    def __init__(self, master, title: str):
        super().__init__(master, fg_color=CARD, corner_radius=14,
                         border_width=1, border_color=BORDER)
        ctk.CTkLabel(self, text=title, text_color=TEXT,
                     font=ctk.CTkFont(family=FONT, size=14, weight="bold")).pack(anchor="e", padx=14, pady=(12, 4))


def _hex2rgb(h: str):
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


# ---------------------------------------------------------------- اپ اصلی
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        self.title("ایجنت ریلز اینستاگرام")
        self.geometry("1160x780")
        self.minsize(1020, 700)
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
        header.pack(fill="x", padx=22, pady=(14, 6))
        title_col = ctk.CTkFrame(header, fg_color="transparent")
        title_col.pack(side="right")
        ctk.CTkLabel(title_col, text="ایجنت ریلز اینستاگرام 👁️‍🗨️",
                     font=ctk.CTkFont(family=FONT, size=22, weight="bold"),
                     text_color=TEXT).pack(anchor="e")
        ctk.CTkLabel(title_col, text="تماشا و دانلود خودکار ریلز — بدون لایک، بدون تعامل",
                     font=ctk.CTkFont(family=FONT, size=12), text_color=TEXT_DIM).pack(anchor="e")

        self.state_var = ctk.StringVar(value=STATE_STYLE["idle"][0])
        pill = ctk.CTkFrame(header, fg_color=CARD, corner_radius=16,
                            border_width=1, border_color=BORDER)
        pill.pack(side="left", padx=4)
        self.state_dot = ctk.CTkLabel(pill, text="●", text_color=TEXT_DIM,
                                      font=ctk.CTkFont(family=FONT, size=15))
        self.state_dot.pack(side="left", padx=(12, 0), pady=7)
        ctk.CTkLabel(pill, textvariable=self.state_var, text_color=TEXT,
                     font=ctk.CTkFont(family=FONT, size=13, weight="bold")).pack(side="left", padx=(4, 14), pady=7)

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=16, pady=(2, 6))
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=0)
        body.grid_rowconfigure(0, weight=1)

        self._build_main(body).grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self._build_sidebar(body).grid(row=0, column=1, sticky="nsew")

        ctk.CTkLabel(
            self,
            text="⚠️ فقط برای استفاده‌ی شخصی — مسئولیت رعایت قوانین اینستاگرام و حق‌نشر با کاربر است.",
            text_color=TEXT_DIM, font=ctk.CTkFont(family=FONT, size=11),
        ).pack(side="bottom", pady=(0, 8))

    # ---- بخش اصلی (چپ) ----
    def _build_main(self, parent) -> ctk.CTkFrame:
        main = ctk.CTkFrame(parent, fg_color=PANEL, corner_radius=16,
                            border_width=1, border_color=BORDER)

        top = ctk.CTkFrame(main, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(16, 8))

        self.ring = ProgressRing(top, size=176)
        self.ring.pack(side="right", padx=(6, 10))

        info = ctk.CTkFrame(top, fg_color="transparent")
        info.pack(side="right", padx=18, pady=24)
        self.time_var = ctk.StringVar(value="—")
        self.eta_var = ctk.StringVar(value="—")
        ctk.CTkLabel(info, text="⏱ زمان سپری‌شده", text_color=TEXT_DIM,
                     font=ctk.CTkFont(family=FONT, size=11)).pack(anchor="e")
        ctk.CTkLabel(info, textvariable=self.time_var, text_color=TEXT,
                     font=ctk.CTkFont(family=FONT, size=15, weight="bold")).pack(anchor="e", pady=(0, 10))
        ctk.CTkLabel(info, text="🏁 تخمین پایان", text_color=TEXT_DIM,
                     font=ctk.CTkFont(family=FONT, size=11)).pack(anchor="e")
        ctk.CTkLabel(info, textvariable=self.eta_var, text_color=AMBER,
                     font=ctk.CTkFont(family=FONT, size=15, weight="bold")).pack(anchor="e")

        grid = ctk.CTkFrame(top, fg_color="transparent")
        grid.pack(side="left", fill="both", expand=True, padx=(0, 10))
        for i in range(2):
            grid.grid_columnconfigure(i, weight=1)
            grid.grid_rowconfigure(i, weight=1)
        self.stat_viewed = StatCard(grid, "👁️", "بازدید شد", BLUE)
        self.stat_viewed.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        self.stat_downloaded = StatCard(grid, "⬇️", "دانلود شد", GREEN)
        self.stat_downloaded.grid(row=0, column=1, sticky="nsew", padx=4, pady=4)
        self.stat_failed = StatCard(grid, "❌", "ناموفق", RED)
        self.stat_failed.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)
        self.stat_speed = StatCard(grid, "⚡", "ریل بر دقیقه", AMBER)
        self.stat_speed.grid(row=1, column=1, sticky="nsew", padx=4, pady=4)
        self.stat_speed.var.set("—")

        tabs = ctk.CTkTabview(
            main, fg_color=CARD, corner_radius=14, anchor="e",
            segmented_button_fg_color=CARD_2,
            segmented_button_selected_color=ACCENT,
            segmented_button_selected_hover_color=ACCENT_HOVER,
            segmented_button_unselected_color=CARD_2,
            segmented_button_unselected_hover_color=BG,
            border_width=1, border_color=BORDER,
        )
        tabs.pack(fill="both", expand=True, padx=16, pady=(4, 16))
        log_tab = tabs.add("📜 رویدادها")
        reels_tab = tabs.add("🎬 ریل‌های اخیر")

        self.log_box = ctk.CTkTextbox(log_tab, font=("Consolas", 12), wrap="word",
                                      state="disabled", fg_color="#0D1219",
                                      corner_radius=10, text_color=TEXT)
        self.log_box.pack(fill="both", expand=True)
        for level, color in LOG_COLORS.items():
            self.log_box.tag_config(f"lv_{level}", foreground=color)

        top2 = ctk.CTkFrame(reels_tab, fg_color="transparent")
        top2.pack(fill="x", pady=(0, 4))
        self.copy_btn = ctk.CTkButton(top2, text="📋 کپی همه‌ی لینک‌ها", width=160, height=26,
                                      fg_color=CARD_2, hover_color=BORDER, text_color=TEXT,
                                      state="disabled", command=self._copy_links)
        self.copy_btn.pack(side="left")
        self.reels_list = ctk.CTkScrollableFrame(reels_tab, fg_color="#0D1219", corner_radius=10)
        self.reels_list.pack(fill="both", expand=True)
        self._reel_rows: dict[str, tuple[ctk.CTkFrame, ctk.CTkLabel, str]] = {}
        self._reel_order: list[str] = []

        self._log("سلام! 👋 یوزرنیم/پسورد را وارد کن، پوشه را انتخاب کن و «شروع ایجنت» را بزن.")
        self._log("🔒 رمز فقط در حافظه می‌ماند و هیچ‌جا ذخیره نمی‌شود؛ فقط نشست (کوکی) ذخیره می‌شود.")
        self._log("👁️ حالت فقط-تماشا: ایجنت لایک/کامنت/فالو انجام نمی‌دهد — فقط اسکرول.", "ok")
        return main

    # ---- پنل کنترل (راست) ----
    def _build_sidebar(self, parent) -> ctk.CTkFrame:
        sb = ctk.CTkFrame(parent, width=314, fg_color=PANEL, corner_radius=16,
                          border_width=1, border_color=BORDER)
        sb.grid_propagate(False)
        inner = ctk.CTkScrollableFrame(sb, fg_color="transparent", scrollbar_button_color=CARD_2)
        inner.pack(fill="both", expand=True, padx=10, pady=10)

        # --- حساب ---
        c1 = SideCard(inner, "👤 حساب اینستاگرام")
        c1.pack(fill="x", pady=(0, 10))
        self.username_entry = ctk.CTkEntry(c1, placeholder_text="username", justify="left",
                                           height=36, fg_color=CARD_2, border_color=BORDER, text_color=TEXT)
        self.username_entry.pack(fill="x", padx=14, pady=(4, 6))
        pw_row = ctk.CTkFrame(c1, fg_color="transparent")
        pw_row.pack(fill="x", padx=14, pady=(0, 6))
        self.password_entry = ctk.CTkEntry(pw_row, placeholder_text="password", show="•",
                                           justify="left", height=36, fg_color=CARD_2,
                                           border_color=BORDER, text_color=TEXT)
        self.password_entry.pack(side="left", fill="x", expand=True)
        self.show_pw_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(pw_row, text="نمایش", variable=self.show_pw_var, width=58,
                        fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color=TEXT_DIM,
                        command=lambda: self.password_entry.configure(show="" if self.show_pw_var.get() else "•"),
                        ).pack(side="left", padx=(8, 0))
        ctk.CTkButton(c1, text="🗑️ حذف نشست ذخیره‌شده", fg_color="transparent",
                      hover_color=CARD_2, text_color=TEXT_DIM, height=26,
                      border_width=1, border_color=BORDER,
                      command=self._clear_session).pack(fill="x", padx=14, pady=(0, 12))

        # --- تنظیمات اجرا ---
        c2 = SideCard(inner, "⚙️ تنظیمات اجرا")
        c2.pack(fill="x", pady=(0, 10))

        trow = ctk.CTkFrame(c2, fg_color="transparent")
        trow.pack(fill="x", padx=14, pady=(6, 0))
        ctk.CTkLabel(trow, text="تعداد ریل‌ها", text_color=TEXT_DIM,
                     font=ctk.CTkFont(family=FONT, size=12)).pack(side="right")
        self.target_label = ctk.CTkLabel(trow, text="500", text_color=ACCENT,
                                         font=ctk.CTkFont(family=FONT, size=15, weight="bold"))
        self.target_label.pack(side="left")
        ctk.CTkSlider(c2, from_=10, to=1000, number_of_steps=99, variable=self.target_var,
                      progress_color=ACCENT, button_color=ACCENT, button_hover_color=ACCENT_HOVER,
                      command=lambda v: self.target_label.configure(text=str(int(v))),
                      ).pack(fill="x", padx=14, pady=(0, 6))

        ctk.CTkLabel(c2, text="ریتم اسکرول", text_color=TEXT_DIM,
                     font=ctk.CTkFont(family=FONT, size=12)).pack(anchor="e", padx=14)
        self.pace_var = ctk.StringVar(value=PACE_LABELS[DEFAULT_PACE])
        ctk.CTkSegmentedButton(c2, values=[PACE_LABELS[k] for k in PACE_KEYS], variable=self.pace_var,
                               fg_color=CARD_2, selected_color=ACCENT, selected_hover_color=ACCENT_HOVER,
                               unselected_color=CARD_2, unselected_hover_color=BG,
                               text_color=TEXT).pack(fill="x", padx=14, pady=(4, 2))
        ctk.CTkLabel(c2, text="⚡ سریع = ریسک محدودشدن موقت بیشتر", text_color=TEXT_DIM,
                     font=ctk.CTkFont(family=FONT, size=10)).pack(anchor="e", padx=14, pady=(0, 8))

        ctk.CTkLabel(c2, text="مرورگر", text_color=TEXT_DIM,
                     font=ctk.CTkFont(family=FONT, size=12)).pack(anchor="e", padx=14)
        self.browser_var = ctk.StringVar(value="Chromium")
        ctk.CTkSegmentedButton(c2, values=["Chromium", "Google Chrome"], variable=self.browser_var,
                               fg_color=CARD_2, selected_color=ACCENT, selected_hover_color=ACCENT_HOVER,
                               unselected_color=CARD_2, unselected_hover_color=BG,
                               text_color=TEXT).pack(fill="x", padx=14, pady=(4, 8))
        self.headless_var = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(c2, text="حالت مخفی (بدون نمایش پنجره)", variable=self.headless_var,
                      progress_color=ACCENT, button_color=TEXT, text_color=TEXT_DIM,
                      font=ctk.CTkFont(family=FONT, size=12)).pack(anchor="e", padx=14, pady=(0, 12))

        # --- پوشه ---
        c3 = SideCard(inner, "📂 پوشه‌ی دانلود")
        c3.pack(fill="x", pady=(0, 10))
        today = datetime.now().strftime("%Y-%m-%d")
        self.folder_var = ctk.StringVar(value=str(DEFAULT_DOWNLOADS / today))
        ctk.CTkLabel(c3, textvariable=self.folder_var, text_color=TEXT_DIM,
                     font=("Consolas", 10), wraplength=260, justify="left").pack(padx=14, pady=(0, 6))
        frow = ctk.CTkFrame(c3, fg_color="transparent")
        frow.pack(fill="x", padx=14, pady=(0, 12))
        ctk.CTkButton(frow, text="📁 انتخاب…", fg_color=CARD_2, hover_color=BORDER,
                      text_color=TEXT, height=28,
                      command=self._pick_folder).pack(side="left", fill="x", expand=True, padx=(0, 4))
        ctk.CTkButton(frow, text="📂 بازکردن", fg_color=CARD_2, hover_color=BORDER,
                      text_color=TEXT, height=28, width=90,
                      command=self._open_download_folder).pack(side="left")

        # --- اکشن‌ها ---
        self.start_btn = ctk.CTkButton(
            inner, text="▶️  شروع ایجنت", height=52, corner_radius=14,
            font=ctk.CTkFont(family=FONT, size=17, weight="bold"),
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="white",
            command=self._start,
        )
        self.start_btn.pack(fill="x", pady=(4, 8))
        self.stop_btn = ctk.CTkButton(
            inner, text="⏹️  توقف", height=38, corner_radius=12,
            font=ctk.CTkFont(family=FONT, size=13, weight="bold"),
            fg_color="transparent", hover_color="#2A1620", text_color=RED,
            border_width=2, border_color=RED, state="disabled",
            command=self._stop,
        )
        self.stop_btn.pack(fill="x", pady=(0, 6))
        return sb

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
            self._log("⚠️ تعداد بالا: چندصد ریل پشت‌سرهم ممکن است اینستاگرام را حساس کند.", "warn")

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
        self.stat_viewed.var.set("0")
        self.stat_downloaded.var.set("0")
        self.stat_failed.var.set("0")
        self.stat_speed.var.set("—")
        self._update_progress()
        self._start_ts = time.time()

        self.stop_event = threading.Event()
        self.link_queue = queue.Queue()
        self.producer_done = threading.Event()

        from .manifest import Manifest
        self.manifest = Manifest(out_dir)

        self.downloader = DownloadManager(
            out_dir=out_dir, events=self.events, stop_event=self.stop_event,
            link_queue=self.link_queue, producer_done=self.producer_done,
            manifest=self.manifest, workers=3,
        )
        pace_key = next((k for k in PACE_KEYS if PACE_LABELS[k] == self.pace_var.get()), DEFAULT_PACE)
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
        self.start_btn.configure(state="disabled", fg_color=CARD_2, text_color=TEXT_DIM)
        self.stop_btn.configure(state="normal")
        self._set_state("launching")
        self.downloader.start()
        self.bot.start()
        self._log(f"🎬 شروع — هدف: {target} ریل | ریتم: {PACE_LABELS[pace_key]} | پوشه: {out_dir}", "ok")

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
        self.start_btn.configure(state="normal", fg_color=ACCENT, text_color="white")
        self.stop_btn.configure(state="disabled")
        ok, bad = (self.manifest.stats if self.manifest else (0, 0))
        if self.manifest:
            self.manifest.close()
        self._log(f"🏁 پایان کار — بازدید: {self.count_viewed} | دانلود: {ok} | ناموفق: {bad}", "ok")

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
            self.stat_viewed.var.set(str(self.count_viewed))
            self._update_progress()
            self._add_reel_row(ev[2], REEL_CODE(ev[2]), "seen")
            self.copy_btn.configure(state="normal")
        elif kind == "downloaded":
            self.count_downloaded += 1
            self.stat_downloaded.var.set(str(self.count_downloaded))
            self._add_reel_row_by_code(ev[1], "ok")
        elif kind == "failed":
            self.count_failed += 1
            self.stat_failed.var.set(str(self.count_failed))
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
        self.state_dot.configure(text_color=color)

    def _update_progress(self) -> None:
        target = max(1, int(self.target_var.get()))
        frac = min(1.0, self.count_viewed / target)
        pct = int(round(frac * 100))
        self.ring.set(frac, f"{pct}٪", f"{self.count_viewed} / {target} ریل")

    def _add_reel_row_by_code(self, code: str, status: str) -> None:
        row = self._reel_rows.get(code)
        if row:
            self._paint_row(row, status)

    def _add_reel_row(self, url: str, code: str, status: str) -> None:
        if code in self._reel_rows:
            return
        row = ctk.CTkFrame(self.reels_list, fg_color=CARD_2, corner_radius=8,
                           border_width=1, border_color=BORDER)
        dot = ctk.CTkLabel(row, text="●", width=20, font=ctk.CTkFont(size=13))
        dot.pack(side="left", padx=(8, 0), pady=5)
        ctk.CTkLabel(row, text=code, font=("Consolas", 12), text_color=TEXT).pack(side="left", padx=6)
        ctk.CTkButton(
            row, text="کپی لینک", width=70, height=22, fg_color=CARD, hover_color=BORDER,
            text_color=TEXT_DIM,
            command=lambda u=url: (self.clipboard_clear(), self.clipboard_append(u)),
        ).pack(side="right", padx=6, pady=4)

        row.pack(fill="x", pady=2, padx=2)
        entry = (row, dot, url)
        self._reel_rows[code] = entry
        self._paint_row(entry, status)
        self._reel_order.append(code)
        while len(self._reel_order) > 80:
            old = self._reel_order.pop(0)
            w = self._reel_rows.pop(old, None)
            if w:
                w[0].destroy()

    @staticmethod
    def _paint_row(row_tuple, status: str) -> None:
        _row, dot, _url = row_tuple
        colors = {"seen": TEXT_DIM, "ok": GREEN, "fail": RED}
        dot.configure(text_color=colors.get(status, TEXT_DIM))

    # ------------------------------------------------------------ لاگ و ساعت
    def _log(self, msg: str, level: str = "info") -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        tag = f"lv_{level}" if level in LOG_COLORS else "lv_info"
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{stamp}] {msg}\n", tag)
        lines = int(self.log_box.index("end-1c").split(".")[0])
        if lines > 1200:
            self.log_box.delete("1.0", f"{lines - 1100}.0")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _tick_clock(self) -> None:
        if self._job_running and self._start_ts:
            elapsed = int(time.time() - self._start_ts)
            target = max(1, int(self.target_var.get()))
            pace_key = next((k for k in PACE_KEYS if PACE_LABELS[k] == self.pace_var.get()), DEFAULT_PACE)
            remaining = max(0, target - self.count_viewed)
            eta = int(remaining * estimate_seconds_per_item(pace_key))
            self.time_var.set(fmt_duration(elapsed))
            self.eta_var.set(f"~{fmt_duration(eta)}")
            minutes = max(elapsed / 60, 1 / 60)
            self.stat_speed.var.set(f"{self.count_viewed / minutes:.1f}")
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
