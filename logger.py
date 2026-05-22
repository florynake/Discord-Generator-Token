"""Pretty colored console logging for the Zrx account generator.

Replaces the noisy colorama `Log` class and the scattered `print()` calls with
clean, structured, color-coded lines. Everything noisy (per-poll status checks,
raw HTTP response dumps, solver chatter) is routed through `dbg()` and only
shows when DEBUG is on.

DEBUG is read from the `DEBUG` env var on import and can be overridden live from
`input/config.json` via `set_debug(config.get("debug"))`.
"""

from __future__ import annotations

import os
import sys
import threading
from datetime import datetime

_print_lock = threading.Lock()


# --- ANSI / color -----------------------------------------------------------

def _setup_console() -> None:
    """Make the console UTF-8 + ANSI-capable so colors and the ● glyph render.

    Without UTF-8 the default Windows code page (cp1252) raises
    UnicodeEncodeError on '●'/'✓' etc.; errors='replace' is a safety net.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            # STD_OUTPUT_HANDLE = -11; ENABLE_PROCESSED_OUTPUT(1) |
            # ENABLE_VIRTUAL_TERMINAL_PROCESSING(4) = 7
            handle = kernel32.GetStdHandle(-11)
            kernel32.SetConsoleMode(handle, 7)
        except Exception:
            pass


_setup_console()

_RESET = "\033[0m"


def _wrap(code: str):
    def f(text) -> str:
        return f"{code}{text}{_RESET}"

    return f


gray = _wrap("\033[90m")
cyan = _wrap("\033[96m")
white = _wrap("\033[97m")
green = _wrap("\033[92m")
red = _wrap("\033[91m")
amber = _wrap("\033[93m")
blue = _wrap("\033[94m")
magenta = _wrap("\033[95m")


# --- DEBUG toggle ------------------------------------------------------------

def _envbool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name, "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")


DEBUG = _envbool("DEBUG", False)


def set_debug(value) -> None:
    """Live-toggle debug verbosity (called after config.json is loaded)."""
    global DEBUG
    if isinstance(value, str):
        value = value.strip().lower() in ("1", "true", "yes", "on")
    DEBUG = bool(value)


def is_debug() -> bool:
    return DEBUG


# --- helpers -----------------------------------------------------------------

def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _short(value, length: int = 80) -> str:
    s = "" if value is None else str(value)
    if length and len(s) > length:
        return s[: length - 1] + "…"
    return s


def _dash(value) -> str:
    s = "" if value is None else str(value)
    return s if s.strip() else "-"


def _emit(line: str) -> None:
    with _print_lock:
        try:
            print(line, flush=True)
        except UnicodeEncodeError:
            enc = getattr(sys.stdout, "encoding", None) or "utf-8"
            safe = line.encode(enc, "replace").decode(enc, "replace")
            print(safe, flush=True)


def _head(level_color, level: str) -> str:
    return gray(_ts()) + " " + level_color(level) + white(" ● ")


# --- generic levels ----------------------------------------------------------

def dbg(*args) -> None:
    """Verbose print — only shows when DEBUG is on."""
    if not DEBUG:
        return
    msg = " ".join(str(a) for a in args)
    _emit(_head(gray, "DEBUG") + gray(msg))


def info(msg) -> None:
    _emit(_head(cyan, "INFO") + white(str(msg)))


def warn(msg) -> None:
    _emit(_head(amber, "WARN") + white(str(msg)))


# --- structured event loggers ------------------------------------------------

def solving(email="") -> None:
    _emit(_head(amber, "SOLVE")
          + gray("Solving captcha ")
          + cyan("email=") + white(_dash(email)))


def captcha_solved(seconds=0.0) -> None:
    try:
        st = f"{float(seconds):.1f}s"
    except (TypeError, ValueError):
        st = str(seconds)
    _emit(_head(green, "SUCCES")
          + gray("Captcha solved ")
          + green("time=") + white(st))


def captcha_failed(reason="") -> None:
    line = _head(red, "FAIL") + gray("Captcha failed")
    if reason:
        line += " " + red("reason=") + white(_short(str(reason), 160))
    _emit(line)


def generated(token="") -> None:
    _emit(_head(green, "SUCCES")
          + gray("Account generated ")
          + green("token=") + white(_short(_dash(token), 40)))


def verified(token="") -> None:
    _emit(_head(green, "SUCCES")
          + gray("Email verified ")
          + green("token=") + white(_short(_dash(token), 40)))


def status(text="") -> None:
    _emit(_head(magenta, "STATUS") + white(_dash(text)))


def waiting(text="") -> None:
    _emit(_head(amber, "WAIT") + white(_dash(text)))


def error(text="") -> None:
    _emit(_head(red, "ERROR") + white(_short(_dash(text), 240)))


def response_dump(text="") -> None:
    """Raw HTTP response body — debug only."""
    dbg("RESPONSE", _short(text, 500))


def parsed_data(proxies=0, build=0, native=0, browser="") -> None:
    """Startup summary line shown once when the generator boots."""
    _emit(
        gray(_ts()) + " " + cyan("INF") + white(" ● ")
        + gray("Parsed data ")
        + gray("| proxies [") + blue(str(proxies))
        + gray("] | build [") + green(str(build))
        + gray("] | native [") + green(str(native))
        + gray("] | browser version [") + cyan(str(browser))
        + gray("]")
    )


def prompt(text: str) -> str:
    """Styled INPUT prompt: prints `HH:MM:SS INPUT ● <text>` then reads a line."""
    line = gray(_ts()) + " " + cyan("INPUT") + white(" ● ") + gray(text)
    with _print_lock:
        try:
            print(line, end="", flush=True)
        except UnicodeEncodeError:
            enc = getattr(sys.stdout, "encoding", None) or "utf-8"
            print(line.encode(enc, "replace").decode(enc, "replace"), end="", flush=True)
    return input()
