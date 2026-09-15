"""مدیریت دانلود ریل‌ها با yt-dlp — چند ورکر هم‌زمان، بدون تعامل با اینستاگرام."""

from __future__ import annotations

import random
import threading
from pathlib import Path
from queue import Empty, Queue


class DownloadManager:
    def __init__(
        self,
        out_dir: Path | str,
        events: Queue,
        stop_event: threading.Event,
        link_queue: Queue,
        producer_done: threading.Event,
        manifest=None,
        workers: int = 2,
        cookies_timeout: int = 240,
    ):
        self.out_dir = Path(out_dir)
        self.events = events
        self.stop = stop_event
        self.link_queue = link_queue
        self.producer_done = producer_done
        self.manifest = manifest
        self.workers = max(1, workers)
        self.cookies_timeout = cookies_timeout

        self._cookies_ready = threading.Event()
        self._cookiefile: str | None = None
        self._threads: list[threading.Thread] = []

    # ---------------- کنترل از بیرون ----------------
    def set_cookiefile(self, path: str) -> None:
        self._cookiefile = path
        self._cookies_ready.set()

    def start(self) -> None:
        for i in range(self.workers):
            t = threading.Thread(target=self._worker, args=(i,), daemon=True, name=f"dl-{i}")
            t.start()
            self._threads.append(t)

    def join(self, timeout: float | None = None) -> None:
        for t in self._threads:
            t.join(timeout=timeout)

    def alive(self) -> bool:
        return any(t.is_alive() for t in self._threads)

    def _log(self, msg: str, level: str = "info") -> None:
        self.events.put(("log", msg, level))

    # ---------------- بدنه‌ی ورکر ----------------
    def _worker(self, idx: int) -> None:
        try:
            import yt_dlp
        except ImportError:
            self.events.put(("fatal", "yt-dlp نصب نیست: pip install yt-dlp"))
            return

        if not self._cookies_ready.wait(self.cookies_timeout):
            self._log("⚠️ کوکی نشست در مهمل تعیین‌شده نیامد؛ دانلود بدون کوکی ادامه می‌یابد (ممکن است خطا بدهد).", "warn")

        while not self.stop.is_set():
            if self.producer_done.is_set() and self.link_queue.empty():
                break
            try:
                url, code = self.link_queue.get(timeout=0.5)
            except Empty:
                continue
            try:
                self._download_one(yt_dlp, url, code)
            finally:
                self.link_queue.task_done()

        self._log(f"🧵 ورکر دانلود {idx + 1} بسته شد.")

    def _download_one(self, yt_dlp, url: str, code: str) -> None:
        opts = {
            "outtmpl": str(self.out_dir / "%(uploader)s - %(id)s.%(ext)s"),
            "format": "mp4/best",
            "merge_output_format": "mp4",
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "retries": 3,
            "fragment_retries": 3,
            "socket_timeout": 30,
            "sleep_interval_requests": random.uniform(1.0, 2.5),
            "continuedl": True,
            "overwrites": False,
        }
        if self._cookiefile:
            opts["cookiefile"] = self._cookiefile

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                title = (info or {}).get("title") or code
                filepath = ""
                try:
                    filepath = (info.get("requested_downloads") or [{}])[0].get("filepath") or ydl.prepare_filename(info)
                except Exception:
                    pass
            if self.manifest:
                self.manifest.mark_downloaded(url, code, filepath, title)
            self.events.put(("downloaded", code, title, filepath))
        except Exception as exc:  # noqa: BLE001 — خطای دانلود نباید کل ایجنت را متوقف کند
            err = str(exc)
            if self.manifest:
                self.manifest.mark_failed(url, code, err)
            self.events.put(("failed", code, err.splitlines()[-1][:220] if err else "unknown"))
