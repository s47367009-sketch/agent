"""رفتار انسانی: مکث‌های تصادفی و استراحت‌های دوره‌ای برای شبیه‌سازی کاربر واقعی."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from threading import Event


@dataclass(frozen=True)
class Pace:
    scroll_min: float      # حداقل مکث بین دو ریل (ثانیه)
    scroll_max: float      # حداکثر مکث بین دو ریل
    break_every_min: int   # هر چند ریل یک استراحت (حداقل)
    break_every_max: int   # هر چند ریل یک استراحت (حداکثر)
    break_min: float       # حداقل طول استراحت
    break_max: float       # حداکثر طول استراحت


PROFILES: dict[str, Pace] = {
    # امن‌ترین حالت — برای اکانت‌های مهم توصیه می‌شود
    "safe":     Pace(2.5, 6.0, 20, 35, 20.0, 50.0),
    # تعادل بین سرعت و امنیت
    "balanced": Pace(1.2, 3.0, 30, 50, 10.0, 25.0),
    # سریع — ریسک محدودیت موقت بالاتر
    "fast":     Pace(0.35, 0.9, 50, 80, 5.0, 12.0),
}


def interruptible_sleep(seconds: float, stop: Event, step: float = 0.15) -> bool:
    """می‌خوابد ولی با set شدن stop بلافاصله بیدار می‌شود. False یعنی متوقف شده."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if stop.is_set():
            return False
        time.sleep(min(step, max(0.0, end - time.monotonic())))
    return not stop.is_set()


class Pacer:
    """ریتم اسکرول را مدیریت می‌کند."""

    def __init__(self, profile: str = "balanced"):
        self.pace = PROFILES.get(profile, PROFILES["balanced"])
        self._since_break = 0
        self._next_break = random.randint(self.pace.break_every_min, self.pace.break_every_max)

    def rest(self, stop: Event, on_break=None) -> bool:
        """یک واحد مکث انجام می‌دهد؛ هر چند واحد یک‌بار استراحت بلندتر."""
        if not interruptible_sleep(random.uniform(self.pace.scroll_min, self.pace.scroll_max), stop):
            return False
        self._since_break += 1
        if self._since_break >= self._next_break:
            self._since_break = 0
            self._next_break = random.randint(self.pace.break_every_min, self.pace.break_every_max)
            length = random.uniform(self.pace.break_min, self.pace.break_max)
            if on_break:
                on_break(length)
            return interruptible_sleep(length, stop)
        return True


def estimate_seconds_per_item(profile: str = "balanced") -> float:
    """تخمین زمان هر ریل — برای نمایش ETA در رابط کاربری."""
    p = PROFILES.get(profile, PROFILES["balanced"])
    mean_scroll = (p.scroll_min + p.scroll_max) / 2
    mean_every = (p.break_every_min + p.break_every_max) / 2
    mean_break = (p.break_min + p.break_max) / 2
    return mean_scroll + mean_break / mean_every + 1.2  # ۱.۲ ثانیه سربار رندر/بارگذاری
