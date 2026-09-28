"""Wire protocols for the console broker: DES auth, WebSocket, and RFB/VNC.

Pure protocol machinery: no Proxmox policy, eligibility, or command handling.
"""

import base64
import hashlib
import io
import os
import socket
import ssl
import struct
import time
from typing import Any

from PIL import Image

from .keys import (
    VNC_BUTTON_LEFT, VNC_BUTTON_MIDDLE, VNC_BUTTON_RIGHT,
    VNC_BUTTON_SCROLL_DOWN, VNC_BUTTON_SCROLL_UP,
)

def _generate_websocket_key() -> str:
    return base64.b64encode(os.urandom(16)).decode("ascii")


def _websocket_accept_key(key: str) -> str:
    magic = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
    return base64.b64encode(hashlib.sha1((key + magic).encode("ascii")).digest()).decode("ascii")


class _WebSocketFrame:
    __slots__ = ("opcode", "payload")

    def __init__(self, opcode: int, payload: bytes):
        self.opcode = opcode
        self.payload = payload


class _WebSocketClient:
    def __init__(self, host: str, port: int, use_ssl: bool, ssl_context: ssl.SSLContext, timeout: int):
        self.host = host
        self.port = port
        self.use_ssl = use_ssl
        self.ssl_context = ssl_context
        self.timeout = timeout
        self.sock: socket.socket | None = None

    def connect(self, path: str, headers: dict[str, str] | None = None) -> bool:
        try:
            sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
            if self.use_ssl:
                sock = self.ssl_context.wrap_socket(sock, server_hostname=self.host)
            self.sock = sock

            key = _generate_websocket_key()
            all_headers = {
                "Host": f"{self.host}:{self.port}",
                "Upgrade": "websocket",
                "Connection": "Upgrade",
                "Sec-WebSocket-Key": key,
                "Sec-WebSocket-Version": "13",
            }
            if headers:
                all_headers.update(headers)

            request_lines = [f"GET {path} HTTP/1.1"]
            for k, v in all_headers.items():
                request_lines.append(f"{k}: {v}")
            request_lines.append("\r\n")
            sock.sendall("\r\n".join(request_lines).encode("ascii"))

            response = b""
            while b"\r\n\r\n" not in response:
                chunk = sock.recv(4096)
                if not chunk:
                    return False
                response += chunk
            header, _ = response.split(b"\r\n\r\n", 1)
            status_line = header.split(b"\r\n", 1)[0].decode("ascii", errors="replace")
            if not status_line.startswith("HTTP/1.1 101") and not status_line.startswith("HTTP/1.0 101"):
                return False
            return True
        except Exception:
            return False

    def send_binary(self, data: bytes) -> bool:
        if self.sock is None:
            return False
        try:
            self.sock.sendall(self._encode_frame(0x2, data))
            return True
        except Exception:
            return False

    def recv(self) -> _WebSocketFrame | None:
        if self.sock is None:
            return None
        try:
            self.sock.settimeout(self.timeout)
            header = self._recv_bytes(2)
            if not header:
                return None
            b1, b2 = header
            opcode = b1 & 0x0F
            masked = (b2 >> 7) & 1
            length = b2 & 0x7F
            if length == 126:
                length_bytes = self._recv_bytes(2)
                if not length_bytes:
                    return None
                length = struct.unpack("!H", length_bytes)[0]
            elif length == 127:
                length_bytes = self._recv_bytes(8)
                if not length_bytes:
                    return None
                length = struct.unpack("!Q", length_bytes)[0]

            if masked:
                mask = self._recv_bytes(4)
                if not mask:
                    return None
                payload = self._recv_bytes(length)
                if payload is None:
                    return None
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            else:
                payload = self._recv_bytes(length)
                if payload is None:
                    return None
            return _WebSocketFrame(opcode, payload)
        except Exception:
            return None

    def _recv_bytes(self, n: int) -> bytes | None:
        if self.sock is None:
            return None
        data = b""
        while len(data) < n:
            chunk = self.sock.recv(n - len(data))
            if not chunk:
                return None
            data += chunk
        return data

    def _encode_frame(self, opcode: int, data: bytes) -> bytes:
        header = bytes([0x80 | opcode])
        length = len(data)
        if length < 126:
            header += bytes([0x80 | length])
        elif length < 65536:
            header += bytes([0x80 | 126]) + struct.pack("!H", length)
        else:
            header += bytes([0x80 | 127]) + struct.pack("!Q", length)
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        return header + mask + masked

    def close(self) -> None:
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None


# ---------------------------------------------------------------------------
# DES implementation for VNC authentication
# ---------------------------------------------------------------------------
# Pure-Python DES-ECB used only for VNC "VNC authentication" (security type 2).
# The password is truncated/padded to 8 bytes and each byte is bit-reversed
# before being used as the DES key, per the VNC spec.

_DES_IP = [
    58, 50, 42, 34, 26, 18, 10, 2,
    60, 52, 44, 36, 28, 20, 12, 4,
    62, 54, 46, 38, 30, 22, 14, 6,
    64, 56, 48, 40, 32, 24, 16, 8,
    57, 49, 41, 33, 25, 17, 9, 1,
    59, 51, 43, 35, 27, 19, 11, 3,
    61, 53, 45, 37, 29, 21, 13, 5,
    63, 55, 47, 39, 31, 23, 15, 7,
]

_DES_FP = [
    40, 8, 48, 16, 56, 24, 64, 32,
    39, 7, 47, 15, 55, 23, 63, 31,
    38, 6, 46, 14, 54, 22, 62, 30,
    37, 5, 45, 13, 53, 21, 61, 29,
    36, 4, 44, 12, 52, 20, 60, 28,
    35, 3, 43, 11, 51, 19, 59, 27,
    34, 2, 42, 10, 50, 18, 58, 26,
    33, 1, 41, 9, 49, 17, 57, 25,
]

_DES_E = [
    32, 1, 2, 3, 4, 5,
    4, 5, 6, 7, 8, 9,
    8, 9, 10, 11, 12, 13,
    12, 13, 14, 15, 16, 17,
    16, 17, 18, 19, 20, 21,
    20, 21, 22, 23, 24, 25,
    24, 25, 26, 27, 28, 29,
    28, 29, 30, 31, 32, 1,
]

_DES_P = [
    16, 7, 20, 21,
    29, 12, 28, 17,
    1, 15, 23, 26,
    5, 18, 31, 10,
    2, 8, 24, 14,
    32, 27, 3, 9,
    19, 13, 30, 6,
    22, 11, 4, 25,
]

_DES_PC1 = [
    57, 49, 41, 33, 25, 17, 9,
    1, 58, 50, 42, 34, 26, 18,
    10, 2, 59, 51, 43, 35, 27,
    19, 11, 3, 60, 52, 44, 36,
    63, 55, 47, 39, 31, 23, 15,
    7, 62, 54, 46, 38, 30, 22,
    14, 6, 61, 53, 45, 37, 29,
    21, 13, 5, 28, 20, 12, 4,
]

_DES_PC2 = [
    14, 17, 11, 24, 1, 5,
    3, 28, 15, 6, 21, 10,
    23, 19, 12, 4, 26, 8,
    16, 7, 27, 20, 13, 2,
    41, 52, 31, 37, 47, 55,
    30, 40, 51, 45, 33, 48,
    44, 49, 39, 56, 34, 53,
    46, 42, 50, 36, 29, 32,
]

_DES_SHIFT = [1, 1, 2, 2, 2, 2, 2, 2, 1, 2, 2, 2, 2, 2, 2, 1]

_DES_SBOX = [
    [14, 4, 13, 1, 2, 15, 11, 8, 3, 10, 6, 12, 5, 9, 0, 7,
     0, 15, 7, 4, 14, 2, 13, 1, 10, 6, 12, 11, 9, 5, 3, 8,
     4, 1, 14, 8, 13, 6, 2, 11, 15, 12, 9, 7, 3, 10, 5, 0,
     15, 12, 8, 2, 4, 9, 1, 7, 5, 11, 3, 14, 10, 0, 6, 13],
    [15, 1, 8, 14, 6, 11, 3, 4, 9, 7, 2, 13, 12, 0, 5, 10,
     3, 13, 4, 7, 15, 2, 8, 14, 12, 0, 1, 10, 6, 9, 11, 5,
     0, 14, 7, 11, 10, 4, 13, 1, 5, 8, 12, 6, 9, 3, 2, 15,
     13, 8, 10, 1, 3, 15, 4, 2, 11, 6, 7, 12, 0, 5, 14, 9],
    [10, 0, 9, 14, 6, 3, 15, 5, 1, 13, 12, 7, 11, 4, 2, 8,
     13, 7, 0, 9, 3, 4, 6, 10, 2, 8, 5, 14, 12, 11, 15, 1,
     13, 6, 4, 9, 8, 15, 3, 0, 11, 1, 2, 12, 5, 10, 14, 7,
     1, 10, 13, 0, 6, 9, 8, 7, 4, 15, 14, 3, 11, 5, 2, 12],
    [7, 13, 14, 3, 0, 6, 9, 10, 1, 2, 8, 5, 11, 12, 4, 15,
     13, 8, 11, 5, 6, 15, 0, 3, 4, 7, 2, 12, 1, 10, 14, 9,
     10, 6, 9, 0, 12, 11, 7, 13, 15, 1, 3, 14, 5, 2, 8, 4,
     3, 15, 0, 6, 10, 1, 13, 8, 9, 4, 5, 11, 12, 7, 2, 14],
    [2, 12, 4, 1, 7, 10, 11, 6, 8, 5, 3, 15, 13, 0, 14, 9,
     14, 11, 2, 12, 4, 7, 13, 1, 5, 0, 15, 10, 3, 9, 8, 6,
     4, 2, 1, 11, 10, 13, 7, 8, 15, 9, 12, 5, 6, 3, 0, 14,
     11, 8, 12, 7, 1, 14, 2, 13, 6, 15, 0, 9, 10, 4, 5, 3],
    [12, 1, 10, 15, 9, 2, 6, 8, 0, 13, 3, 4, 14, 7, 5, 11,
     10, 15, 4, 2, 7, 12, 9, 5, 6, 1, 13, 14, 0, 11, 3, 8,
     9, 14, 15, 5, 2, 8, 12, 3, 7, 0, 4, 10, 1, 13, 11, 6,
     4, 3, 2, 12, 9, 5, 15, 10, 11, 14, 1, 7, 6, 0, 8, 13],
    [4, 11, 2, 14, 15, 0, 8, 13, 3, 12, 9, 7, 5, 10, 6, 1,
     13, 0, 11, 7, 4, 9, 1, 10, 14, 3, 5, 12, 2, 15, 8, 6,
     1, 4, 11, 13, 12, 3, 7, 14, 10, 15, 6, 8, 0, 5, 9, 2,
     6, 11, 13, 8, 1, 4, 10, 7, 9, 5, 0, 15, 14, 2, 3, 12],
    [13, 2, 8, 4, 6, 15, 11, 1, 10, 9, 3, 14, 5, 0, 12, 7,
     1, 15, 13, 8, 10, 3, 7, 4, 12, 5, 6, 11, 0, 14, 9, 2,
     7, 11, 4, 1, 9, 12, 14, 2, 0, 6, 10, 13, 15, 3, 5, 8,
     2, 1, 14, 7, 4, 10, 8, 13, 15, 12, 9, 0, 3, 5, 6, 11],
]


def _bytes_to_bits(data: bytes) -> str:
    return "".join(format(b, "08b") for b in data)


def _bits_to_bytes(bits: str) -> bytes:
    return bytes(int(bits[i : i + 8], 2) for i in range(0, len(bits), 8))


def _permute_bits(bits: str, table: list[int]) -> str:
    return "".join(bits[i - 1] for i in table)


def _shift_left_bits(bits: str, n: int) -> str:
    return bits[n:] + bits[:n]


def _xor_bits(a: str, b: str) -> str:
    return "".join("1" if x != y else "0" for x, y in zip(a, b))


def _sbox_lookup(bits: str, sbox: list[int]) -> str:
    row = int(bits[0] + bits[5], 2)
    col = int(bits[1:5], 2)
    return format(sbox[row * 16 + col], "04b")


def _f_function(right: str, subkey: str) -> str:
    expanded = _permute_bits(right, _DES_E)
    xored = _xor_bits(expanded, subkey)
    sbox_out = "".join(_sbox_lookup(xored[i * 6 : (i + 1) * 6], _DES_SBOX[i]) for i in range(8))
    return _permute_bits(sbox_out, _DES_P)


def _des_key_schedule(key_bits: str) -> list[str]:
    key56 = _permute_bits(key_bits, _DES_PC1)
    left = key56[:28]
    right = key56[28:]
    subkeys = []
    for shift in _DES_SHIFT:
        left = _shift_left_bits(left, shift)
        right = _shift_left_bits(right, shift)
        subkeys.append(_permute_bits(left + right, _DES_PC2))
    return subkeys


def _des_encrypt_block(block: bytes, key_bits: str) -> bytes:
    bits = _permute_bits(_bytes_to_bits(block), _DES_IP)
    left = bits[:32]
    right = bits[32:]
    subkeys = _des_key_schedule(key_bits)
    for i in range(16):
        left, right = right, _xor_bits(left, _f_function(right, subkeys[i]))
    bits = right + left
    return _bits_to_bytes(_permute_bits(bits, _DES_FP))


def _reverse_byte_bits(b: int) -> int:
    result = 0
    for i in range(8):
        if b & (1 << i):
            result |= 1 << (7 - i)
    return result


def _vnc_des_key(password: str) -> str:
    key_bytes = password.encode("utf-8")[:8].ljust(8, b"\x00")
    reversed_bytes = bytes(_reverse_byte_bits(b) for b in key_bytes)
    return _bytes_to_bits(reversed_bytes)


def _vnc_auth_response(password: str, challenge: bytes) -> bytes:
    key_bits = _vnc_des_key(password)
    response = b""
    for i in range(0, len(challenge), 8):
        response += _des_encrypt_block(challenge[i : i + 8], key_bits)
    return response


class _VNCClient:
    def __init__(self, ws_client: _WebSocketClient):
        self.ws = ws_client
        self.width = 0
        self.height = 0
        self.pixel_format: dict[str, Any] = {}
        self._buffer = b""

    def _recv(self, n: int) -> bytes | None:
        while len(self._buffer) < n:
            frame = self.ws.recv()
            if frame is None:
                return None
            if frame.opcode == 0x8:
                return None
            self._buffer += frame.payload
        data = self._buffer[:n]
        self._buffer = self._buffer[n:]
        return data

    def _recv_line(self) -> bytes | None:
        while b"\n" not in self._buffer:
            frame = self.ws.recv()
            if frame is None:
                return None
            if frame.opcode == 0x8:
                return None
            self._buffer += frame.payload
        line, self._buffer = self._buffer.split(b"\n", 1)
        return line + b"\n"

    def _send(self, data: bytes) -> bool:
        return self.ws.send_binary(data)

    def connect(self, password: str | None = None) -> bool:
        version = self._recv_line()
        if not version or not version.startswith(b"RFB "):
            return False
        if not self._send(b"RFB 003.008\n"):
            return False

        sec_count = self._recv(1)
        if sec_count is None:
            return False
        count = sec_count[0]
        if count == 0:
            return False
        sec_types = self._recv(count)
        if sec_types is None:
            return False

        if 1 in sec_types:
            if not self._send(bytes([1])):
                return False
        elif 2 in sec_types and password:
            if not self._send(bytes([2])):
                return False
            challenge = self._recv(16)
            if challenge is None or len(challenge) != 16:
                return False
            response = _vnc_auth_response(password, challenge)
            if not self._send(response):
                return False
        else:
            return False

        result = self._recv(4)
        if result is None or struct.unpack("!I", result)[0] != 0:
            return False

        if not self._send(bytes([0])):
            return False

        server_init = self._recv(24)
        if server_init is None or len(server_init) < 24:
            return False
        self.width = struct.unpack("!H", server_init[0:2])[0]
        self.height = struct.unpack("!H", server_init[2:4])[0]
        fmt = server_init[4:20]
        self.pixel_format = {
            "bpp": fmt[0],
            "depth": fmt[1],
            "big_endian": fmt[2] != 0,
            "true_color": fmt[3] != 0,
            "red_max": struct.unpack("!H", fmt[4:6])[0],
            "green_max": struct.unpack("!H", fmt[6:8])[0],
            "blue_max": struct.unpack("!H", fmt[8:10])[0],
            "red_shift": fmt[10],
            "green_shift": fmt[11],
            "blue_shift": fmt[12],
        }
        name_len = struct.unpack("!I", server_init[20:24])[0]
        if name_len > 0:
            name = self._recv(name_len)
            if name is None:
                return False

        # Configure the session with the server's pixel format and request
        # standard encodings. Some servers (including Proxmox/QEMU) expect
        # these setup messages before pointer events are honored.
        pixel_format = bytes([
            fmt[0], fmt[1], fmt[2], fmt[3],
            fmt[4], fmt[5], fmt[6], fmt[7],
            fmt[8], fmt[9], fmt[10], fmt[11],
            fmt[12],
        ]) + b"\x00\x00\x00"
        set_pixel_format = struct.pack("!B", 0) + b"\x00\x00\x00" + pixel_format
        if not self._send(set_pixel_format):
            return False

        # Request raw encoding only (encoding 0). Some QEMU/Proxmox VNC
        # servers will not process pointer events until encodings are set.
        set_encodings = struct.pack("!BBH", 2, 0, 1) + struct.pack("!i", 0)
        if not self._send(set_encodings):
            return False

        return True

    def _read_framebuffer_update(self) -> tuple[bytes, int, int, int] | None:
        """Read one raw framebuffer update; return (raw, w, h, bpp) or None."""
        header = self._recv(4)
        if header is None or len(header) < 4 or header[0] != 0:
            return None
        num_rects = struct.unpack("!H", header[2:4])[0]
        if num_rects != 1:
            return None

        rect_header = self._recv(12)
        if rect_header is None or len(rect_header) < 12:
            return None
        w = struct.unpack("!H", rect_header[4:6])[0]
        h = struct.unpack("!H", rect_header[6:8])[0]
        encoding = struct.unpack("!i", rect_header[8:12])[0]
        if encoding != 0:
            return None

        bpp = self.pixel_format.get("bpp", 32)
        if bpp == 32:
            pixel_bytes = 4
        elif bpp == 16:
            pixel_bytes = 2
        else:
            return None
        total = w * h * pixel_bytes
        raw = self._recv(total)
        if raw is None or len(raw) < total:
            return None
        return raw, w, h, bpp

    def request_screenshot(self, stable: bool = True) -> bytes | None:
        if not self.width or not self.height:
            return None
        req = struct.pack("!BBHHHH", 3, 0, 0, 0, self.width, self.height)
        if not self._send(req):
            return None

        result = self._read_framebuffer_update()
        if result is None:
            return None
        raw, w, h, bpp = result
        if not stable:
            return self._to_png(raw, w, h, bpp)

        # For slow or transitional console states, wait until the framebuffer
        # stops changing before returning a screenshot. This avoids capturing
        # partial animation or mid-transition frames.
        stable_cycles = 0
        for _ in range(10):
            time.sleep(0.5)
            if not self._send(req):
                return None
            next_result = self._read_framebuffer_update()
            if next_result is None:
                return None
            raw2, w2, h2, bpp2 = next_result
            if raw2 == raw and w2 == w and h2 == h and bpp2 == bpp:
                stable_cycles += 1
                if stable_cycles >= 2:
                    break
            else:
                raw, w, h, bpp = raw2, w2, h2, bpp2
                stable_cycles = 0
        return self._to_png(raw, w, h, bpp)

    def _send_pointer_event(self, button_mask: int, x: int, y: int) -> bool:
        """Send a VNC PointerEvent message (message type 5)."""
        if not self.width or not self.height:
            return False
        x, y = self.clamp_pointer_coordinates(x, y)
        msg = struct.pack("!BBHH", 5, button_mask, x, y)
        return self._send(msg)

    def clamp_pointer_coordinates(self, x: int, y: int) -> tuple[int, int]:
        """Return pointer coordinates clamped to the framebuffer bounds."""
        if not self.width or not self.height:
            return x, y
        return max(0, min(x, self.width - 1)), max(0, min(y, self.height - 1))

    def mouse_move(self, x: int, y: int) -> bool:
        """Move the mouse pointer to the given coordinates."""
        return self._send_pointer_event(0, x, y)

    def mouse_click(self, x: int, y: int, button: str = "left", clicks: int = 1, hold_seconds: float = 0.05) -> bool:
        """Send a mouse click at the given coordinates."""
        button = button.lower()
        if button == "left":
            mask = VNC_BUTTON_LEFT
        elif button == "middle":
            mask = VNC_BUTTON_MIDDLE
        elif button == "right":
            mask = VNC_BUTTON_RIGHT
        else:
            return False
        if not self.mouse_move(x, y):
            return False
        time.sleep(hold_seconds)
        for _ in range(clicks):
            if not self._send_pointer_event(mask, x, y):
                return False
            time.sleep(hold_seconds)
            if not self._send_pointer_event(0, x, y):
                return False
            time.sleep(hold_seconds)
        return True

    def mouse_scroll(self, x: int, y: int, direction: str = "down", amount: int = 3) -> bool:
        """Send scroll wheel events at the given coordinates."""
        direction = direction.lower()
        if direction == "up":
            mask = VNC_BUTTON_SCROLL_UP
        elif direction == "down":
            mask = VNC_BUTTON_SCROLL_DOWN
        else:
            return False
        if not self.mouse_move(x, y):
            return False
        for _ in range(amount):
            if not self._send_pointer_event(mask, x, y):
                return False
            if not self._send_pointer_event(0, x, y):
                return False
        return True

    def mouse_drag(self, x1: int, y1: int, x2: int, y2: int, button: str = "left") -> bool:
        """Drag the mouse from (x1, y1) to (x2, y2)."""
        button = button.lower()
        if button == "left":
            mask = VNC_BUTTON_LEFT
        elif button == "middle":
            mask = VNC_BUTTON_MIDDLE
        elif button == "right":
            mask = VNC_BUTTON_RIGHT
        else:
            return False
        if not self.mouse_move(x1, y1):
            return False
        if not self._send_pointer_event(mask, x1, y1):
            return False
        if not self._send_pointer_event(mask, x2, y2):
            return False
        if not self._send_pointer_event(0, x2, y2):
            return False
        return True

    def _to_png(self, raw: bytes, w: int, h: int, bpp: int) -> bytes | None:
        if bpp not in (16, 32):
            return None
        red_shift = self.pixel_format.get("red_shift", 16)
        green_shift = self.pixel_format.get("green_shift", 8)
        blue_shift = self.pixel_format.get("blue_shift", 0)
        red_max = self.pixel_format.get("red_max", 255)
        green_max = self.pixel_format.get("green_max", 255)
        blue_max = self.pixel_format.get("blue_max", 255)
        big_endian = self.pixel_format.get("big_endian", False)
        fmt = ">" if big_endian else "<"
        if bpp == 32:
            unpack_fmt = f"{fmt}I"
            stride = 4
        elif bpp == 16:
            unpack_fmt = f"{fmt}H"
            stride = 2
        else:
            return None

        pixels = bytearray()
        for row in range(h):
            row_start = row * w * stride
            for col in range(w):
                idx = row_start + col * stride
                pixel = struct.unpack(unpack_fmt, raw[idx:idx + stride])[0]
                r = (pixel >> red_shift) & red_max
                g = (pixel >> green_shift) & green_max
                b = (pixel >> blue_shift) & blue_max
                if red_max != 255:
                    r = (r * 255) // red_max
                if green_max != 255:
                    g = (g * 255) // green_max
                if blue_max != 255:
                    b = (b * 255) // blue_max
                pixels.extend([r, g, b])
        img = Image.frombytes("RGB", (w, h), bytes(pixels))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()


class _SerialClient:
    def __init__(self, ws_client: _WebSocketClient):
        self.ws = ws_client

    def read_available(self, timeout: float = 2.0) -> bytes:
        text = b""
        deadline = time.time() + timeout
        while time.time() < deadline:
            frame = self.ws.recv()
            if frame is None:
                break
            if frame.opcode == 0x8:
                break
            text += frame.payload
        return text
