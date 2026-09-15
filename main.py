#!/usr/bin/env python3
"""نقطه‌ی ورود ایجنت ریلز اینستاگرام — اجرا: python main.py"""

from __future__ import annotations

import sys


def _preflight() -> None:
    if sys.version_info < (3, 9):
        print("❌ به پایتون 3.9 یا بالاتر نیاز است.")
        sys.exit(1)
    try:
        import tkinter  # noqa: F401
    except ImportError:
        print("❌ tkinter نصب نیست.")
        print("   لینوکس:  sudo apt install python3-tk")
        print("   ویندوز/مک: معمولاً همراه پایتون نصب است.")
        sys.exit(1)
    missing = []
    for pkg, mod in (("customtkinter", "customtkinter"), ("playwright", "playwright"), ("yt-dlp", "yt_dlp")):
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)
    if missing:
        print("❌ این کتابخانه‌ها نصب نیستند: " + ", ".join(missing))
        print("   pip install -r requirements.txt")
        print("   python -m playwright install chromium")
        sys.exit(1)


if __name__ == "__main__":
    _preflight()
    from insta_agent.gui import main
    main()
