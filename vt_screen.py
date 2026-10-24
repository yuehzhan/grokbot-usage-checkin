#!/usr/bin/env python3
"""A tiny virtual terminal screen (stdlib only) for turning a pseudo-console byte stream into text.

Windows ConPTY (used through pywinpty) does not send a plain text stream: it repaints the screen
with cursor moves (``ESC[row;colH``), "cursor forward" instead of spaces (``ESC[5C``), erase
commands and colour codes. Stripping the escape codes would glue words together
("Weekly" + ESC[1C + "limit" -> "Weeklylimit"), so the reader feeds the stream into this screen
and parses what would be visible, just like ``tmux capture-pane`` does on Linux/macOS.

Supported: printable text (East Asian wide characters take two cells), CR, LF, BS, TAB,
CSI A B C D E F G H f d J K X @ P (cursor moves, erase, insert/delete), CSI s/u and ESC 7/8
(save/restore cursor), alternate screen on/off (``?1049h/l``, ``?47``, ``?1047``), and it skips
OSC/DCS strings and every other control sequence (colours, modes, titles). It also collects the
replies a terminal gives to a cursor-position query (``ESC[6n``) and a primary device-attributes
query (``ESC[c``), so a TUI that asks for them does not stall; the reader writes them back.
"""
import unicodedata

__all__ = ["Screen"]


def _width(ch):
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


class Screen:
    def __init__(self, cols=200, rows=50):
        self.cols, self.rows = int(cols), int(rows)
        self.reset()

    def reset(self):
        self.grid = [[" "] * self.cols for _ in range(self.rows)]
        self.x = self.y = 0
        self.saved = (0, 0)
        self.responses = []    # answers a real terminal would send back (cursor position, device attributes)
        self._state = "text"   # text | esc | csi | osc | osc_esc | str (DCS/APC/PM) | str_esc | charset
        self._buf = ""
        self._main = None      # saved main screen while the alternate screen is active

    # ---------------------------------------------------------------- helpers
    def _clamp(self):
        self.x = max(0, min(self.cols - 1, self.x))
        self.y = max(0, min(self.rows - 1, self.y))

    def _scroll(self):
        self.grid.pop(0)
        self.grid.append([" "] * self.cols)

    def _lf(self):
        if self.y >= self.rows - 1:
            self._scroll()
        else:
            self.y += 1

    def _put(self, ch):
        w = _width(ch)
        if w == 0:
            return
        if self.x + w > self.cols:  # auto-wrap
            self.x = 0
            self._lf()
        self.grid[self.y][self.x] = ch
        if w == 2 and self.x + 1 < self.cols:
            self.grid[self.y][self.x + 1] = ""  # placeholder cell behind a wide character
        self.x += w

    def _erase_line(self, mode):
        row = self.grid[self.y]
        if mode == 1:
            rng = range(0, self.x + 1)
        elif mode == 2:
            rng = range(0, self.cols)
        else:
            rng = range(self.x, self.cols)
        for i in rng:
            if 0 <= i < self.cols:
                row[i] = " "

    def _erase_display(self, mode):
        if mode in (2, 3):
            self.grid = [[" "] * self.cols for _ in range(self.rows)]
            return
        if mode == 1:
            for r in range(0, self.y):
                self.grid[r] = [" "] * self.cols
            self._erase_line(1)
        else:
            self._erase_line(0)
            for r in range(self.y + 1, self.rows):
                self.grid[r] = [" "] * self.cols

    def _csi(self, seq):
        final, body = seq[-1], seq[:-1]
        private = body[:1] in ("?", ">", "<", "=")
        if private:
            body = body[1:]
        parts = [p for p in body.replace(":", ";").split(";")]

        def n(i=0, default=1):
            try:
                v = int(parts[i]) if i < len(parts) and parts[i] != "" else default
            except ValueError:
                v = default
            return v if v != 0 or default == 0 else default

        if final == "n" and not private and body.strip() == "6":  # "where is the cursor?" (DSR)
            self.responses.append(f"\x1b[{self.y + 1};{self.x + 1}R")
            return
        if final == "c" and not private and body.strip() in ("", "0"):  # primary device attributes
            self.responses.append("\x1b[?1;2c")  # "VT100 with advanced video"
            return
        if private:
            if final in ("h", "l") and any(p in ("1049", "47", "1047") for p in parts):
                if final == "h" and self._main is None:   # enter: keep the main screen, start blank
                    self._main = (self.grid, self.x, self.y)
                    self.grid = [[" "] * self.cols for _ in range(self.rows)]
                    self.x = self.y = 0
                elif final == "l" and self._main is not None:  # leave: back to the main screen
                    self.grid, self.x, self.y = self._main
                    self._main = None
            return
        if final == "A":
            self.y -= n()
        elif final == "B" or final == "e":
            self.y += n()
        elif final == "C" or final == "a":
            self.x += n()
        elif final == "D":
            self.x -= n()
        elif final == "E":
            self.y += n(); self.x = 0
        elif final == "F":
            self.y -= n(); self.x = 0
        elif final in ("G", "`"):
            self.x = n() - 1
        elif final == "d":
            self.y = n() - 1
        elif final in ("H", "f"):
            self.y, self.x = n(0) - 1, n(1) - 1
        elif final == "J":
            self._erase_display(n(0, 0))
        elif final == "K":
            self._erase_line(n(0, 0))
        elif final == "X":
            for i in range(self.x, min(self.cols, self.x + n())):
                self.grid[self.y][i] = " "
        elif final == "@":
            row = self.grid[self.y]
            k = n()
            self.grid[self.y] = (row[:self.x] + [" "] * k + row[self.x:])[:self.cols]
        elif final == "P":
            row = self.grid[self.y]
            k = n()
            self.grid[self.y] = row[:self.x] + row[self.x + k:] + [" "] * k
        elif final == "s":
            self.saved = (self.x, self.y)
        elif final == "u":
            self.x, self.y = self.saved
        # m (colours), r (scroll region), t, n, c, ... carry no text: ignored
        self._clamp()

    # ---------------------------------------------------------------- public
    def feed(self, data):
        """Feed a chunk (str, or bytes decoded as UTF-8). Sequences may be split across chunks."""
        if isinstance(data, (bytes, bytearray)):
            data = bytes(data).decode("utf-8", "replace")
        for ch in data:
            st = self._state
            if st == "text":
                if ch == "\x1b":
                    self._state = "esc"
                elif ch == "\r":
                    self.x = 0
                elif ch in "\n\x0b\x0c":
                    self._lf()
                elif ch == "\b":
                    self.x = max(0, self.x - 1)
                elif ch == "\t":
                    self.x = min(self.cols - 1, (self.x // 8 + 1) * 8)
                elif ch == "\x9b":
                    self._state, self._buf = "csi", ""
                elif ord(ch) < 32 or ch == "\x7f":
                    pass
                else:
                    self._put(ch)
            elif st == "esc":
                if ch == "[":
                    self._state, self._buf = "csi", ""
                elif ch == "]":
                    self._state = "osc"
                elif ch in "PX^_":
                    self._state = "str"
                elif ch in "()*+":
                    self._state = "charset"
                elif ch == "7":
                    self.saved = (self.x, self.y); self._state = "text"
                elif ch == "8":
                    self.x, self.y = self.saved; self._state = "text"
                elif ch == "c":
                    self.reset()
                elif ch == "D":
                    self._lf(); self._state = "text"
                elif ch == "E":
                    self.x = 0; self._lf(); self._state = "text"
                elif ch == "M":
                    self.y = max(0, self.y - 1); self._state = "text"
                else:
                    self._state = "text"
            elif st == "charset":
                self._state = "text"
            elif st == "csi":
                if "\x40" <= ch <= "\x7e":
                    self._state = "text"
                    self._csi(self._buf + ch)
                elif len(self._buf) > 64:  # garbage: drop it
                    self._state = "text"
                else:
                    self._buf += ch
            elif st in ("osc", "str"):
                if ch == "\x07":
                    self._state = "text"
                elif ch == "\x1b":
                    self._state = st + "_esc"
            elif st in ("osc_esc", "str_esc"):
                self._state = "text" if ch == "\\" else st[:-4]

    def take_responses(self):
        """Return and clear the replies the program is waiting for (write them to its input)."""
        out, self.responses = self.responses, []
        return out

    def text(self):
        """The visible screen as text: one line per row, trailing spaces removed."""
        return "\n".join("".join(row).rstrip() for row in self.grid)
