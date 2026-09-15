"""دفتر ثبت ریل‌ها: manifest.jsonl + links.txt با قابلیت ادامه‌ی اجرای قبلی."""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path

REEL_RE = re.compile(r"/reel/([A-Za-z0-9_-]+)")


class Manifest:
    def __init__(self, out_dir: Path | str):
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.jsonl_path = self.out_dir / "manifest.jsonl"
        self.links_path = self.out_dir / "links.txt"
        self._lock = threading.Lock()
        self.seen_shortcodes: set[str] = set()
        self.successful_codes: set[str] = set()
        self._download_fail_count = 0
        self._download_ok_count = 0

        import json  # local import to keep top light
        self._json = json

        # ادامه‌ی اجرای قبلی: لینک‌هایی که قبلاً دیده شده‌اند دوباره شمرده نمی‌شوند
        if self.links_path.exists():
            for line in self.links_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                m = REEL_RE.search(line.strip())
                if m:
                    self.seen_shortcodes.add(m.group(1))

        # آخرین وضعیت هر ریل در اجراهای قبلی — برای تلاش مجددِ دانلودهای ناتمام
        if self.jsonl_path.exists():
            last_event: dict[str, str] = {}
            for line in self.jsonl_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                code = rec.get("shortcode")
                if code and rec.get("event") in ("downloaded", "failed"):
                    last_event[code] = rec["event"]
            self.successful_codes = {c for c, e in last_event.items() if e == "downloaded"}
        self._links_fh = self.links_path.open("a", encoding="utf-8", buffering=1)
        self._json_fh = self.jsonl_path.open("a", encoding="utf-8", buffering=1)

    def _record(self, **fields) -> None:
        fields["ts"] = round(time.time(), 3)
        with self._lock:
            self._json_fh.write(self._json.dumps(fields, ensure_ascii=False) + "\n")

    def mark_seen(self, url: str, code: str) -> None:
        with self._lock:
            self._links_fh.write(url + "\n")
        self._record(event="seen", url=url, shortcode=code)

    def mark_downloaded(self, url: str, code: str, filepath: str, title: str = "") -> None:
        self._download_ok_count += 1
        self.successful_codes.add(code)
        self._record(event="downloaded", url=url, shortcode=code, file=filepath, title=title)

    def mark_failed(self, url: str, code: str, error: str) -> None:
        self._download_fail_count += 1
        self._record(event="failed", url=url, shortcode=code, error=error[:500])

    def retry_candidates(self) -> set[str]:
        """ریل‌هایی که دیده شده‌اند ولی هنوز با موفقیت دانلود نشده‌اند."""
        return self.seen_shortcodes - self.successful_codes

    @property
    def stats(self) -> tuple[int, int]:
        return self._download_ok_count, self._download_fail_count

    def close(self) -> None:
        with self._lock:
            for fh in (self._links_fh, self._json_fh):
                try:
                    fh.close()
                except Exception:
                    pass
