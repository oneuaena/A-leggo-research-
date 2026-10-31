"""LAN pairing helpers for the A-leggo Next mobile app.

The Windows runtime publishes a short-lived pairing token together with its
LAN address; the phone reads both from the QR code shown in the workbench and
echoes the token back to ``/api/aleggo/pair/verify`` before trusting the
server.  Nothing here touches the tutor / knowledge-base / agent layers.
"""

from __future__ import annotations

import io
import json
import secrets
import socket
import time

_state = {"token": secrets.token_urlsafe(18), "ts": time.time()}


def _lan_ipv4() -> str:
    """Best-effort outbound-socket trick; loopback when offline."""

    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        return str(probe.getsockname()[0])
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


def _frontend_port() -> int:
    try:
        from aleggo.runtime.home import get_runtime_home
        from aleggo.services.config import load_launch_settings

        return int(load_launch_settings(get_runtime_home()).frontend_port)
    except Exception:
        return 3782


def pairing_info() -> dict:
    return {
        "v": 1,
        "name": socket.gethostname(),
        "host": _lan_ipv4(),
        "port": _frontend_port(),
        "token": _state["token"],
        "ts": _state["ts"],
    }


def qr_payload() -> str:
    return json.dumps(pairing_info(), separators=(",", ":"), ensure_ascii=False)


def rotate() -> dict:
    _state["token"] = secrets.token_urlsafe(18)
    _state["ts"] = time.time()
    return pairing_info()


def verify(token: str) -> bool:
    return bool(token) and secrets.compare_digest(token, _state["token"])


def qr_svg(text: str) -> str:
    import qrcode
    import qrcode.image.svg

    factory = qrcode.image.svg.SvgPathImage
    try:
        qr = qrcode.QRCode(box_size=8, border=2, image_factory=factory)
    except TypeError:  # qrcode < 8 spells the keyword ``factory``
        qr = qrcode.QRCode(box_size=8, border=2, factory=factory)
    qr.add_data(text)
    qr.make(fit=True)
    img = qr.make_image()
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode("utf-8")
