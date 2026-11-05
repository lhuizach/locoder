"""``locoder serve``: use locoder from your phone.

Runs the same agent as the CLI behind a small HTTP API on your local
network, plus the phone web app. Pair a phone with the QR code or 6-digit
code it prints. With --tunnel the app can also reach it from anywhere,
end-to-end encrypted, through Cloudflare. See mobile/PLAN.md for the design.

The same server also runs ``lochat serve`` (locoder/lochat): a ServeApp says
what differs - the name, the port, the page, which phones are paired, and
the Hub (lochat's own chats, with no file or command tools).
"""

from __future__ import annotations

import argparse
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from rich.console import Console
from rich.table import Table
from rich.text import Text

from locoder.display import FAINT, FG, LOGO, MUTED, SECONDARY, pixels_to_text, wordmark_bitmap
from locoder.safety import broad_root_warning
from locoder.serve.pairing import CODE_TTL, Pairing
from locoder.serve.qr import pair_url, qr_text

ACCENT = "#4a9eff"


@dataclass(frozen=True)
class ServeApp:
    """What ``serve_main`` serves: locoder (the default) or lochat."""

    name: str = "locoder"
    description: str = "Use locoder from your phone."
    port: int = 8765
    accent: str = ACCENT
    devices_file: Optional[Path] = None  # None: pairing.DEVICES_FILE
    web_dir: Optional[Path] = None  # None: server.WEB_DIR
    make_hub: Optional[Callable[[Path, dict[str, Any]], Any]] = None  # None: Hub(cwd, overrides)
    serves_folder: bool = True  # works on the current folder (so warn when it's a broad one)
    discovery: bool = True  # answers the app's "locoder?" broadcasts


LOCODER = ServeApp()


def build_parser(app: ServeApp = LOCODER) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=f"{app.name} serve", description=app.description)
    parser.add_argument("--port", type=int, default=app.port, help=f"port to listen on (default {app.port})")
    parser.add_argument("--host", default="0.0.0.0",
                        help="address to listen on (default: every network this PC is on)")
    parser.add_argument("--address",
                        help=f"the address phones should use, if the one {app.name} picks (first listed) is wrong")
    parser.add_argument("--tunnel", action="store_true",
                        help="also reachable from anywhere through Cloudflare (needs cloudflared), end-to-end encrypted")
    parser.add_argument("--tunnel-url",
                        help="the public address of a Cloudflare tunnel you run yourself, pointing at this server")
    parser.add_argument("--model", help="override the model name from config")
    parser.add_argument("--provider", help="override provider kind")
    parser.add_argument("--base-url", help="override the backend base URL")
    parser.add_argument("--num-ctx", type=int, help="override the context window size (tokens)")
    if app.serves_folder:
        parser.add_argument("--yolo", action="store_true", help="start with confirmations off")
    parser.add_argument("--add-phone", action="store_true",
                        help="pair one more phone (pairing is closed while a phone is already paired)")
    parser.add_argument("--unpair-all", action="store_true", help="forget every paired phone and exit")
    return parser


def serve_main(argv: Optional[list[str]] = None, app: ServeApp = LOCODER) -> int:
    args = build_parser(app).parse_args(argv)
    console = Console(highlight=False)
    store = {"store": app.devices_file} if app.devices_file else {}

    if args.unpair_all:
        count = Pairing(on_new_code=lambda code: None, **store).unpair_all()
        console.print(f"unpaired {count} device{'s' if count != 1 else ''}")
        return 0

    from locoder.serve.hub import Hub
    from locoder.serve.server import LocoderServer, lan_addresses, start_discovery

    overrides = {
        "model_override": args.model,
        "base_url_override": args.base_url,
        "provider_override": args.provider,
        "num_ctx_override": args.num_ctx,
        "yolo_override": getattr(args, "yolo", False),
    }
    try:
        hub = (app.make_hub or Hub)(Path.cwd(), overrides)
    except ValueError as exc:
        console.print(Text(f"{app.name}: {exc}", style="#e5534b"))
        return 1

    broad = broad_root_warning(Path.cwd()) if app.serves_folder else None
    if broad:
        console.print(Text(broad, style="#d7a84a"))
        console.print()

    ready = threading.Event()
    addresses: list[str] = []

    def show_code(code: str) -> None:
        if not ready.is_set():  # the first code (made while starting) is in the banner
            return
        if pairing.closed:
            console.print()
            console.print(Text("pairing is now closed: no other phone can pair. "
                               f"To add another, run {app.name} serve --add-phone", style=MUTED))
            console.print()
            return
        console.print()
        console.print(Text("new pairing code (the last one was used or expired)", style=MUTED))
        _print_pairing(console, addresses[0], args.port, code, secret=pairing.pair_secret, remote=hub.remote,
                       accent=app.accent)
        console.print()

    def log(msg: str) -> None:
        console.print(Text.assemble(("· ", FAINT), (msg, SECONDARY)))

    # Once a phone is paired, nobody else can pair - even with a code - until
    # you ask for it on this PC. Each --add-phone run lets exactly one more in.
    pairing = Pairing(on_new_code=show_code, close_after_pair=True, **store)
    pairing.closed = bool(pairing.devices) and not args.add_phone
    try:
        server = LocoderServer((args.host, args.port), hub, pairing, log=log, app_name=app.name,
                               **({"web_dir": app.web_dir} if app.web_dir else {}))
    except OSError as exc:
        console.print(Text(f"{app.name}: can't listen on port {args.port} ({exc}) - try --port {args.port + 2}",
                           style="#e5534b"))
        return 1
    stop = threading.Event()
    if app.discovery:
        start_discovery(args.port, hub.computer, stop)

    addresses[:] = lan_addresses() if args.host in ("0.0.0.0", "") else [args.host]
    if args.address:
        addresses[:] = [args.address, *[a for a in addresses if a != args.address]]

    tunnel = None
    if args.tunnel_url:
        hub.remote = args.tunnel_url.rstrip("/")
    elif args.tunnel:
        tunnel = _start_tunnel(console, args.port, log, app.name)
        hub.remote = tunnel.url if tunnel else None

    _banner(console, hub, addresses, args.port, pairing.current_code(), len(pairing.devices),
            secret=pairing.pair_secret, closed=pairing.closed, app=app)
    ready.set()

    # Expired codes are replaced (and the new one shown) without waiting for a phone to ask.
    def rotate() -> None:
        while not stop.wait(5):
            pairing.current_code()

    threading.Thread(target=rotate, daemon=True).start()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        if tunnel:
            tunnel.stop()
        server.server_close()
        console.print(Text("stopped", style=MUTED))
    return 0


def _start_tunnel(console: Console, port: int, log, name: str = "locoder"):
    from locoder.serve.tunnel import DOWNLOAD_PAGE, BIN_DIR, QuickTunnel, find_cloudflared

    binary = find_cloudflared()
    if binary is None:
        console.print(Text.assemble(
            ("no tunnel: cloudflared isn't installed. ", "#d7a84a"),
            (f"Get cloudflared-windows-amd64.exe from {DOWNLOAD_PAGE}, save it as "
             f"{BIN_DIR / 'cloudflared.exe'}, and run {name} serve --tunnel again.", MUTED)))
        return None
    console.print(Text("starting the encrypted tunnel...", style=MUTED))
    tunnel = QuickTunnel(port, log=log)
    if tunnel.start(binary) is None:
        tunnel.stop()
        console.print(Text("no tunnel: cloudflared didn't come up (no internet?) - home Wi-Fi only for now", style="#d7a84a"))
        return None
    return tunnel


def _fancy(console: Console) -> bool:
    """Block characters (logo, QR code) need a console that can encode them."""
    return (console.encoding or "").lower().replace("-", "").startswith("utf")


def _can_show_qr(console: Console) -> bool:
    """The QR code is drawn with colours; without them it's just a block."""
    return _fancy(console) and console.color_system is not None


def _banner(console: Console, hub, addresses: list[str], port: int, code: str, paired: int,
            *, secret: bytes = b"", closed: bool = False, app: ServeApp = LOCODER) -> None:
    console.print()
    if app is not LOCODER:
        console.print(Text(app.name, style=f"bold {app.accent}"))
    elif _fancy(console):
        lockup = Table.grid(padding=(0, 3))
        lockup.add_column(vertical="middle")
        lockup.add_column(vertical="middle")
        lockup.add_row(pixels_to_text(LOGO, ACCENT), pixels_to_text(wordmark_bitmap("locoder"), FG))
        console.print(lockup)
    else:
        console.print(Text("locoder", style=f"bold {ACCENT}"))
    console.print()
    console.print(Text.assemble(("serving ", MUTED), (hub.project_info()["label"], FG),
                                ("  ·  ", FAINT), (hub.provider.model, SECONDARY)))
    console.print()
    console.print(Text(f"on your phone (same Wi-Fi), open the {app.name} app or go to", style=MUTED))
    console.print(Text(f"  http://{addresses[0]}:{port}", style=f"bold {FG}"))
    for addr in addresses[1:]:
        # VPN / VM / WSL adapters: usually not the one the phone can see.
        console.print(Text.assemble((f"  http://{addr}:{port}", SECONDARY), ("  other network", FAINT)))
    if hub.remote:
        console.print()
        console.print(Text("anywhere else, the app connects through", style=MUTED))
        console.print(Text.assemble((f"  {hub.remote}", f"bold {FG}"),
                                    ("  end-to-end encrypted, Cloudflare can't read it", FAINT)))
    console.print()
    phones = f"{paired} phone{'s are' if paired != 1 else ' is'} paired"
    if closed:
        console.print(Text.assemble((f"{phones} - pairing is closed, so no other phone can connect", FG)))
        console.print(Text(f"to add another phone: {app.name} serve --add-phone   "
                           f"to remove them all: {app.name} serve --unpair-all", style=MUTED))
    else:
        _print_pairing(console, addresses[0], port, code, secret=secret, remote=hub.remote, accent=app.accent)
        if paired:
            console.print(Text(f"{phones} already; pairing closes again once this one pairs", style=MUTED))
        else:
            console.print(Text("once a phone pairs, pairing closes: no other phone can connect", style=MUTED))
    console.print()
    console.print(Text("first time? if Windows asks, allow Python on private networks.  ctrl+c stops", style=FAINT))
    console.print()


def _print_pairing(console: Console, address: str, port: int, code: str, *, secret: bytes = b"",
                   remote: Optional[str] = None, accent: str = ACCENT) -> None:
    fancy = _can_show_qr(console)
    console.print(_pairing_block(address, port, code, secret=secret, remote=remote, fancy=fancy, accent=accent))
    if not fancy:
        # No QR here: the link it would hold, to paste into the app instead.
        # soft_wrap keeps it on one line however narrow the console, so it copies whole.
        link = pair_url(address, port, code, secret=secret, remote=remote)
        console.print(Text.assemble(("pairing link  ", MUTED), (link, FG)), soft_wrap=True)


def _pairing_block(address: str, port: int, code: str, *, secret: bytes = b"", remote: Optional[str] = None,
                   fancy: bool = True, accent: str = ACCENT):
    """The QR code, with the code to type next to it."""
    if not fancy:
        # No QR possible here: the link it would hold, to paste into the app instead.
        return Text.assemble(("pairing code  ", MUTED), (f"{code[:3]} {code[3:]}", f"bold {accent}"),
                             (f"   (works once, for {CODE_TTL // 60} minutes)", FAINT))
    info = Text.assemble(
        ("scan with your phone's camera\n", FG),
        ("or with scan QR code in the app\n\n", MUTED),
        ("or type the code  ", MUTED), (f"{code[:3]} {code[3:]}", f"bold {accent}"), ("\n", ""),
        (f"works once, for {CODE_TTL // 60} minutes", FAINT),
    )
    block = Table.grid(padding=(0, 3))
    block.add_column(no_wrap=True)
    block.add_column(vertical="middle")
    block.add_row(qr_text(pair_url(address, port, code, secret=secret, remote=remote)), info)
    return block
