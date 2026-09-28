"""Rolling reasoning/thought spinner for the terminal."""

import re
import sys
import threading
import time
from typing import List, Optional

from .markdown import DIM, GRAY, ITALIC, MAGENTA, RESET

class RollingThoughtStreamer:
    """Visualizes model reasoning / <think> tokens in the terminal."""

    def __init__(self):
        self._buffer: List[str] = []
        self._is_active = False
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._spinner_frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        self._spinner_idx = 0
        self._start_time = 0.0

    def start(self) -> None:
        """Start the reasoning ticker animation."""
        with self._lock:
            self._buffer = []
            self._is_active = True
            self._start_time = time.time()
            self._spinner_idx = 0

        self._thread = threading.Thread(target=self._animate, daemon=True)
        self._thread.start()

    def push(self, chunk: str) -> None:
        """Push a new thought token chunk."""
        with self._lock:
            if chunk:
                self._buffer.append(chunk)

    def _animate(self) -> None:
        while True:
            with self._lock:
                if not self._is_active:
                    break
                elapsed = time.time() - self._start_time
                full_text = "".join(self._buffer).replace("\n", " ")
                full_text = re.sub(r"\s+", " ", full_text).strip()
                tail = full_text[-60:] if len(full_text) > 60 else full_text
                spinner = self._spinner_frames[self._spinner_idx % len(self._spinner_frames)]
                self._spinner_idx += 1

            status_line = f"\r\033[K{MAGENTA}{spinner}{RESET} {DIM}{ITALIC}Thinking ({elapsed:.1f}s): {tail}{RESET}"
            sys.stdout.write(status_line)
            sys.stdout.flush()
            time.sleep(0.08)

    def finish(self) -> None:
        """Stop animation and cleanly clear the status line."""
        with self._lock:
            if not self._is_active:
                return
            self._is_active = False
            elapsed = time.time() - self._start_time
            total_chars = len("".join(self._buffer))

        if self._thread:
            self._thread.join(timeout=0.5)

        if total_chars > 0:
            sys.stdout.write(f"\r\033[K{GRAY}thought {elapsed:.1f}s{RESET}\n")
        else:
            sys.stdout.write("\r\033[K")
        sys.stdout.flush()
