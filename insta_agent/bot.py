"""
بات مرورگر: ورود، رفتن به ریلز و اسکرول فقط-تماشایی.

⚠️ سیاست سخت‌گیرانه‌ی VIEW-ONLY:
این ماژول عمداً هیچ تعاملی با محتوا ندارد — بدون لایک، بدون کامنت، بدون فالو،
بدون اشتراک‌گذاری و بدون سیو. تنها کنش‌های مجاز: کلیک روی دکمه‌ی «Next»،
فشردن ArrowDown و اسکرول چرخ موس برای رفتن به ریل بعدی.
"""

from __future__ import annotations

import random
import re
import threading
import time
from pathlib import Path
from queue import Queue

from .cookies import export_netscape_cookies
from .human import Pacer

REEL_RE = re.compile(r"/reel/([A-Za-z0-9_-]+)")
REELS_HOME = "https://www.instagram.com/reels/"
HOME = "https://www.instagram.com/"

# نشانه‌های «لاگین بودن» در رابط اینستاگرام
LOGGED_IN_MARKERS = (
    'svg[aria-label="Home"]',
    'a[href*="/direct/"]',
    'svg[aria-label="New post"]',
    'svg[aria-label="Reels"]',
)


class StoppedByUser(Exception):
    """توقف تمیز توسط کاربر — خطا محسوب نمی‌شود."""


class ReelsBot(threading.Thread):
    def __init__(
        self,
        *,
        username: str,
        password: str,
        target: int,
        events: Queue,
        stop_event: threading.Event,
        link_queue: Queue,
        producer_done: threading.Event,
        session_path: Path,
        cookie_path: Path,
        headless: bool = False,
        channel: str | None = None,   # None = Chromium داخلی، "chrome" = گوگل‌کروم نصب‌شده
        pace: str = "balanced",
        manifest=None,
    ):
        super().__init__(daemon=True, name="reels-bot")
        self.username = (username or "").strip()
        self.password = password or ""
        self.target = max(1, int(target))
        self.events = events
        self.stop = stop_event
        self.link_queue = link_queue
        self.producer_done = producer_done
        self.session_path = Path(session_path)
        self.cookie_path = Path(cookie_path)
        self.headless = headless
        self.channel = channel
        self.manifest = manifest
        self.pacer = Pacer(pace)

    # ---------------- ابزار گزارش ----------------
    def log(self, msg: str, level: str = "info") -> None:
        self.events.put(("log", msg, level))

    def _state(self, name: str) -> None:
        self.events.put(("state", name))

    # ---------------- نقطه‌ی ورود ----------------
    def run(self) -> None:
        try:
            self._run()
        except StoppedByUser:
            self._state("stopped")
            self.log("⏹️ ایجنت توسط کاربر متوقف شد.")
        except Exception as exc:  # noqa: BLE001
            msg = f"{type(exc).__name__}: {exc}"
            if "Executable doesn't exist" in msg:
                msg = (
                    "مرورگر Playwright نصب نیست. در ترمینال اجرا کنید:\n"
                    "    python -m playwright install chromium"
                )
            self.events.put(("fatal", msg))
        finally:
            self.producer_done.set()
            self.events.put(("bot_done", None))

    def _run(self) -> None:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            launch_kwargs = {
                "headless": self.headless,
                "args": [
                    "--disable-blink-features=AutomationControlled",
                    "--no-first-run",
                    "--disable-dev-shm-usage",
                ],
            }
            if self.channel:
                launch_kwargs["channel"] = self.channel

            self._state("launching")
            self.log("🚀 در حال باز کردن مرورگر…")
            browser = p.chromium.launch(**launch_kwargs)

            ctx_kwargs = {
                "viewport": {"width": 430, "height": 900},  # نمای موبایل-مانند مناسب ریلز
                "locale": "en-US",
            }
            if self.session_path.exists():
                ctx_kwargs["storage_state"] = str(self.session_path)

            context = browser.new_context(**ctx_kwargs)
            context.set_default_timeout(8000)
            page = context.new_page()
            try:
                self._open_and_login(page, context)
                self._export_session(page, context)
                self._scroll_loop(page, context)
            finally:
                try:
                    context.close()
                    browser.close()
                except Exception:
                    pass

        if self.stop.is_set():
            self._state("stopped")
            self.log("⏹️ ایجنت توسط کاربر متوقف شد.")
        else:
            self._state("done")

    # ---------------- ورود ----------------
    def _open_and_login(self, page, context) -> None:
        self._state("loading")
        page.goto(HOME, wait_until="domcontentloaded", timeout=60000)
        self._wait_loaded(page)
        self._dismiss_popups(page)

        if self._is_logged_in(page):
            self.log("✅ نشست ذخیره‌شده معتبر است — بدون نیاز به رمز ادامه می‌دهیم.")
            return

        if not self.username or not self.password:
            raise RuntimeError("نشست معتبری نیست؛ یوزرنیم و پسورد را وارد کنید.")

        self._state("logging_in")
        self.log("🔐 در حال ورود به حساب…")
        page.goto("https://www.instagram.com/accounts/login/", wait_until="domcontentloaded", timeout=60000)
        self._dismiss_popups(page)

        user_input = page.locator('input[name="username"]')
        user_input.wait_for(state="visible", timeout=30000)
        user_input.click()
        user_input.press_sequentially(self.username, delay=random.randint(55, 130))
        time.sleep(random.uniform(0.3, 0.8))
        pass_input = page.locator('input[name="password"]')
        pass_input.click()
        pass_input.press_sequentially(self.password, delay=random.randint(55, 130))
        time.sleep(random.uniform(0.4, 0.9))
        page.locator('button[type="submit"]').first.click()
        self.log("📨 اطلاعات ورود ارسال شد؛ منتظر تأیید اینستاگرام…")

        notified_2fa = notified_challenge = notified_wrong = False
        deadline = time.monotonic() + 300  # ۵ دقیقه فرصت برای تایپ کد دو مرحله‌ای دستی
        while time.monotonic() < deadline:
            if self.stop.is_set():
                raise StoppedByUser()
            url = page.url or ""

            if ("two_factor" in url or page.locator('input[name="verificationCode"]').count()) and not notified_2fa:
                notified_2fa = True
                self._state("awaiting_user")
                self.log("🔢 تأیید دو مرحله‌ای فعال است — کد را در همان پنجره‌ی مرورگر وارد کنید.", "warn")
            if "challenge" in url and not notified_challenge:
                notified_challenge = True
                self._state("awaiting_user")
                self.log("🧩 چالش امنیتی اینستاگرام — لطفاً دستی در مرورگر حلش کنید.", "warn")
            if not notified_wrong and page.locator("text=/incorrect|اشتباه|wrong/i").count():
                notified_wrong = True
                self.log("❌ به نظر می‌رسد یوزرنیم/پسورد اشتباه است.", "error")

            if self._is_logged_in(page):
                self._dismiss_popups(page)
                self.log("🎉 ورود موفق!")
                self._state("logged_in")
                return
            time.sleep(1.4)

        raise RuntimeError("مهلت ورود تمام شد — رمز، کد دو مرحله‌ای یا چالش را بررسی کنید.")

    def _is_logged_in(self, page) -> bool:
        try:
            if page.locator('input[name="username"]').count() > 0:
                return False
            for sel in LOGGED_IN_MARKERS:
                try:
                    if page.locator(sel).first.is_visible(timeout=1200):
                        return True
                except Exception:
                    continue
        except Exception:
            pass
        return False

    def _wait_loaded(self, page) -> None:
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass

    def _dismiss_popups(self, page) -> None:
        """دکمه‌های Not now / کوکی / نوتیفیکیشن — هیچ‌کدام تعامل با محتوا نیستند."""
        patterns = ["Not now", "Not Now", "Decline optional cookies", "Allow essential and optional cookies", "Dismiss"]
        for name in patterns:
            try:
                btn = page.get_by_role("button", name=re.compile(re.escape(name), re.I))
                if btn.count() and btn.first.is_visible(timeout=700):
                    btn.first.click()
                    page.wait_for_timeout(600)
            except Exception:
                continue

    def _export_session(self, page, context) -> None:
        try:
            self.session_path.parent.mkdir(parents=True, exist_ok=True)
            context.storage_state(path=str(self.session_path))
            try:
                self.session_path.chmod(0o600)
            except OSError:
                pass
        except Exception as exc:  # noqa: BLE001
            self.log(f"⚠️ ذخیره‌ی نشست ناموفق بود: {exc}", "warn")

        try:
            cookies = context.cookies([HOME, REELS_HOME])
            export_netscape_cookies(cookies, self.cookie_path)
            self.events.put(("cookies_ready", str(self.cookie_path)))
            self.log("🍪 کوکی نشست برای دانلودکننده آماده شد.")
        except Exception as exc:  # noqa: BLE001
            self.log(f"⚠️ استخراج کوکی ناموفق بود: {exc}", "warn")
            self.events.put(("cookies_ready", ""))

    # ---------------- حلقه‌ی اسکرول ----------------
    def _scroll_loop(self, page, context) -> None:
        self._state("scrolling")
        self.log(f"📺 رفتن به ریلز — هدف: {self.target} ریل جدید. حالت فقط-تماشا فعال است 👁️")
        page.goto(REELS_HOME, wait_until="domcontentloaded", timeout=60000)
        self._dismiss_popups(page)
        page.wait_for_timeout(2500)

        seen: set[str] = set(self.manifest.seen_shortcodes) if self.manifest else set()
        if seen:
            self.log(f"♻️ ادامه‌ی اجرای قبلی: {len(seen)} ریل قبلاً دیده شده و رد می‌شود.")

        # دانلودهای ناتمام اجرای قبلی را اول صف بگذار
        if self.manifest:
            retries = sorted(self.manifest.retry_candidates())
            if retries:
                self.log(f"🔁 {len(retries)} ریل از قبل دانلود نشده‌اند — صف تلاش مجدد…", "warn")
                for code in retries:
                    self.link_queue.put((f"https://www.instagram.com/reel/{code}/", code))

        harvested = 0
        stuck = 0

        while harvested < self.target and not self.stop.is_set():
            m = REEL_RE.search(page.url or "")
            if m:
                code = m.group(1)
                if code not in seen:
                    seen.add(code)
                    harvested += 1
                    canon = f"https://www.instagram.com/reel/{code}/"
                    self.link_queue.put((canon, code))
                    if self.manifest:
                        self.manifest.mark_seen(canon, code)
                    self.events.put(("viewed", harvested, canon))
                    stuck = 0
                    if harvested % 100 == 0:
                        self._refresh_cookies(context)
                        self.log(f"📈 پیشرفت: {harvested}/{self.target}")
                else:
                    stuck += 1
            else:
                stuck += 1

            if stuck >= 10:
                self.log("🔁 فید گیر کرد — بازخوانی صفحه‌ی ریلز…", "warn")
                try:
                    page.goto(REELS_HOME, wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_timeout(2500)
                except Exception:
                    pass
                stuck = 0

            self._advance(page)
            if not self.pacer.rest(self.stop, on_break=self._on_break):
                break

        if harvested >= self.target:
            self.log(f"🏁 هدف کامل شد: {harvested} ریل برداشت شد.")
        else:
            self.log(f"⏹️ توقف در {harvested}/{self.target} ریل.")

    def _advance(self, page) -> None:
        """فقط رفتن به ریل بعدی — هیچ تعامل دیگری (لایک/کامنت/…) انجام نمی‌شود."""
        advanced = False
        try:
            for sel in ('button[aria-label="Next"]', '[role="button"][aria-label="Next"]'):
                btn = page.locator(sel)
                if btn.count() and btn.first.is_visible(timeout=400):
                    btn.first.click()
                    advanced = True
                    break
        except Exception:
            pass
        if not advanced:
            try:
                page.keyboard.press("ArrowDown")
                advanced = True
            except Exception:
                pass
        if not advanced:
            try:
                page.mouse.move(random.randint(180, 250), random.randint(380, 520))
                page.mouse.wheel(0, random.randint(700, 1100))
            except Exception:
                pass
        try:
            page.wait_for_timeout(random.randint(350, 800))
        except Exception:
            pass

    def _on_break(self, seconds: float) -> None:
        self.log(f"☕ استراحت انسانی: {int(seconds)} ثانیه مکث…")

    def _refresh_cookies(self, context) -> None:
        try:
            cookies = context.cookies([HOME, REELS_HOME])
            export_netscape_cookies(cookies, self.cookie_path)
        except Exception:
            pass
