import argparse
import os
import re
import secrets
import shutil
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PS_EXE = "powershell"

HERE = Path(__file__).resolve().parent
# A secret kept next to this file. Every trigger needs it, so only someone
# with the link (not just anyone on the Wi-Fi or the tunnel) can fire a prank.
SECRET_FILE = HERE / ".troll_secret"


def load_secret():
    try:
        existing = SECRET_FILE.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass
    token = secrets.token_urlsafe(18)
    SECRET_FILE.write_text(token, encoding="utf-8")
    return token

MAXIMIZE = ""

def type_line_fn():
    return r"""
function Type-Line($text, $delay=15) {
    foreach ($c in $text.ToCharArray()) { Write-Host -NoNewline $c; Start-Sleep -Milliseconds $delay }
    Write-Host ""
}
"""

EFFECTS = {}

EFFECTS["fake_hack"] = MAXIMIZE + type_line_fn() + r"""
$Host.UI.RawUI.BackgroundColor = "Black"; $Host.UI.RawUI.ForegroundColor = "Green"; Clear-Host
$Host.UI.RawUI.WindowTitle = "SYSTEM32"
Type-Line "INITIALIZING BREACH PROTOCOL..."
Start-Sleep -Milliseconds 500
Type-Line "CONNECTING TO REMOTE HOST... [OK]"
Start-Sleep -Milliseconds 300
Type-Line "BYPASSING FIREWALL..."
Start-Sleep -Milliseconds 800
Type-Line "ACCESS GRANTED."
Write-Host ""
$chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%^&*"
for ($i = 0; $i -lt 300; $i++) {
    $line = -join ((1..(Get-Random -Minimum 40 -Maximum 90)) | ForEach-Object { $chars[(Get-Random -Maximum $chars.Length)] })
    Write-Host $line
    Start-Sleep -Milliseconds 15
}
Write-Host ""
Type-Line "DOWNLOADING FILES: [====================] 100%"
Type-Line "TARGET SYSTEM COMPROMISED."
Write-Host ""
Type-Line "just kidding :) - nothing happened to your pc" 25
Write-Host ""
Write-Host "Press Enter to close..."
Read-Host
"""

EFFECTS["fake_virus"] = MAXIMIZE + type_line_fn() + r"""
$Host.UI.RawUI.BackgroundColor = "Black"; $Host.UI.RawUI.ForegroundColor = "Red"; Clear-Host
$Host.UI.RawUI.WindowTitle = "Windows Defender - THREAT DETECTED"
Type-Line "!!! CRITICAL THREAT DETECTED !!!"
Start-Sleep -Milliseconds 500
$files = "C:\Users\Documents\passwords.txt","C:\Windows\System32\kernel.dll","C:\Users\Pictures\vacation.jpg","C:\Program Files\Steam\steam.exe"
foreach ($f in $files) {
    Write-Host "SCANNING: $f ... " -NoNewline
    Start-Sleep -Milliseconds 400
    Write-Host "INFECTED" -ForegroundColor Red
}
Write-Host ""
Type-Line "Trojan.Win32.Generic found in 4 files."
Start-Sleep -Milliseconds 800
Type-Line "Attempting quarantine..."
for ($i=0; $i -le 100; $i+=10) { Write-Host "`rQuarantine progress: $i%" -NoNewline; Start-Sleep -Milliseconds 200 }
Write-Host ""
Write-Host ""
Type-Line "relax, this is fake. no virus, no scan, all a joke." 25
Write-Host ""
Write-Host "Press Enter to close..."
Read-Host
"""

EFFECTS["fake_bsod"] = MAXIMIZE + r"""
$Host.UI.RawUI.BackgroundColor = "DarkBlue"; $Host.UI.RawUI.ForegroundColor = "White"; Clear-Host
Write-Host ""
Write-Host "  :("
Write-Host ""
Write-Host "  Your PC ran into a problem and needs to restart. We're"
Write-Host "  just collecting some error info, and then we'll restart for you."
Write-Host ""
Write-Host "  0% complete"
Start-Sleep -Seconds 2
Write-Host ""
Write-Host "  STOP CODE: TOTALLY_A_PRANK"
Write-Host ""
Start-Sleep -Seconds 3
Clear-Host
$Host.UI.RawUI.BackgroundColor = "Black"; $Host.UI.RawUI.ForegroundColor = "Green"
Write-Host "just kidding, this was fake. press enter to close"
Read-Host
"""

EFFECTS["fake_ransom"] = MAXIMIZE + type_line_fn() + r"""
$Host.UI.RawUI.BackgroundColor = "Black"; $Host.UI.RawUI.ForegroundColor = "Red"; Clear-Host
$Host.UI.RawUI.WindowTitle = "YOUR FILES HAVE BEEN ENCRYPTED"
Write-Host "================================================"
Write-Host "         !!! ALL YOUR FILES ARE ENCRYPTED !!!"
Write-Host "================================================"
Write-Host ""
Type-Line "Send 0.5 BTC to unlock your files within 24 hours"
Write-Host ""
Type-Line "wallet: 1FaKeW4ll3tAddr3ssTr0ll0000000"
Start-Sleep -Seconds 3
Write-Host ""
$Host.UI.RawUI.ForegroundColor = "Green"
Type-Line "lol jk, this is fake, your files are fine" 25
Write-Host ""
Write-Host "Press Enter to close..."
Read-Host
"""

EFFECTS["fake_update"] = MAXIMIZE + r"""
$Host.UI.RawUI.BackgroundColor = "DarkBlue"; $Host.UI.RawUI.ForegroundColor = "White"; Clear-Host
Write-Host ""
Write-Host "    Working on updates 0%"
Write-Host "    Don't turn off your computer"
for ($i=0; $i -le 100; $i+=1) {
    Write-Host "`r    Working on updates $i%   " -NoNewline
    Start-Sleep -Milliseconds (Get-Random -Minimum 50 -Maximum 250)
}
Write-Host ""
Start-Sleep -Seconds 2
$Host.UI.RawUI.BackgroundColor = "Black"; $Host.UI.RawUI.ForegroundColor = "Green"; Clear-Host
Write-Host "just kidding, no updates happened. press enter to close"
Read-Host
"""

EFFECTS["fake_format"] = MAXIMIZE + type_line_fn() + r"""
$Host.UI.RawUI.BackgroundColor = "Black"; $Host.UI.RawUI.ForegroundColor = "Gray"; Clear-Host
$Host.UI.RawUI.WindowTitle = "Formatting C:\"
Type-Line "Formatting C:\ ..."
Write-Host ""
for ($i=0; $i -le 100; $i+=2) {
    Write-Host "`rProgress: $i% complete" -NoNewline
    Start-Sleep -Milliseconds 80
}
Write-Host ""
Start-Sleep -Seconds 1
$Host.UI.RawUI.ForegroundColor = "Green"
Write-Host ""
Type-Line "relax, nothing was formatted, this is fake" 25
Write-Host ""
Write-Host "Press Enter to close..."
Read-Host
"""

EFFECTS["matrix_rain"] = MAXIMIZE + r"""
$Host.UI.RawUI.BackgroundColor = "Black"; $Host.UI.RawUI.ForegroundColor = "Green"; Clear-Host
$Host.UI.RawUI.WindowTitle = "The Matrix"
$chars = "ｦｱｳｴｵｶｷｹｺｻｼｽｾｿﾀﾂﾃﾅﾆﾇﾈﾊﾋﾎﾏﾐﾑﾒﾓﾔﾕﾗﾘﾜ0123456789"
for ($i = 0; $i -lt 500; $i++) {
    $line = -join ((1..(Get-Random -Minimum 60 -Maximum 120)) | ForEach-Object { $chars[(Get-Random -Maximum $chars.Length)] })
    Write-Host $line
    Start-Sleep -Milliseconds 10
}
Write-Host ""
Write-Host "just a fake matrix effect, press enter to close"
Read-Host
"""

EFFECTS["fake_webcam"] = MAXIMIZE + type_line_fn() + r"""
$Host.UI.RawUI.BackgroundColor = "Black"; $Host.UI.RawUI.ForegroundColor = "Red"; Clear-Host
$Host.UI.RawUI.WindowTitle = "Remote Access - Webcam"
Type-Line "Establishing remote connection to webcam..."
Start-Sleep -Seconds 1
Type-Line "Access granted. Recording started [REC]"
for ($i=0; $i -lt 8; $i++) {
    Write-Host "[REC] ● capturing frame $i..."
    Start-Sleep -Milliseconds 400
}
Write-Host ""
$Host.UI.RawUI.ForegroundColor = "Green"
Type-Line "relax, your webcam was never accessed, totally fake" 25
Write-Host ""
Write-Host "Press Enter to close..."
Read-Host
"""

EFFECTS["fake_tracking"] = MAXIMIZE + type_line_fn() + r"""
$Host.UI.RawUI.BackgroundColor = "Black"; $Host.UI.RawUI.ForegroundColor = "Cyan"; Clear-Host
$Host.UI.RawUI.WindowTitle = "IP Tracer"
Type-Line "Tracing IP address..."
$hops = "192.168.1.1","10.0.0.1","172.16.254.1","203.0.113.7","198.51.100.23","YOUR LOCATION FOUND"
foreach ($h in $hops) {
    Write-Host "Hop: $h"
    Start-Sleep -Milliseconds 600
}
Write-Host ""
$Host.UI.RawUI.ForegroundColor = "Green"
Type-Line "relax, this is fake, nobody was traced" 25
Write-Host ""
Write-Host "Press Enter to close..."
Read-Host
"""

EFFECTS["self_destruct"] = MAXIMIZE + type_line_fn() + r"""
$Host.UI.RawUI.BackgroundColor = "Black"; $Host.UI.RawUI.ForegroundColor = "Red"; Clear-Host
$Host.UI.RawUI.WindowTitle = "SELF DESTRUCT SEQUENCE"
Type-Line "WARNING: SELF DESTRUCT SEQUENCE INITIATED"
Write-Host ""
for ($i=10; $i -ge 1; $i--) {
    Write-Host "  $i..."
    Start-Sleep -Seconds 1
}
Write-Host ""
$Host.UI.RawUI.ForegroundColor = "Green"
Type-Line "just kidding, nothing destructed, this is fake" 25
Write-Host ""
Write-Host "Press Enter to close..."
Read-Host
"""

EFFECTS["fake_miner"] = MAXIMIZE + type_line_fn() + r"""
$Host.UI.RawUI.BackgroundColor = "Black"; $Host.UI.RawUI.ForegroundColor = "Yellow"; Clear-Host
$Host.UI.RawUI.WindowTitle = "cryptominer.exe"
Type-Line "Starting background crypto miner..."
Start-Sleep -Milliseconds 500
for ($i=0; $i -lt 15; $i++) {
    $hash = -join ((1..16) | ForEach-Object { "0123456789abcdef"[(Get-Random -Maximum 16)] })
    Write-Host "Hash: $hash  |  CPU: $((Get-Random -Minimum 80 -Maximum 100))%"
    Start-Sleep -Milliseconds 300
}
Write-Host ""
$Host.UI.RawUI.ForegroundColor = "Green"
Type-Line "relax, no mining happened, totally fake" 25
Write-Host ""
Write-Host "Press Enter to close..."
Read-Host
"""

EFFECTS["fake_fbi"] = MAXIMIZE + type_line_fn() + r"""
$Host.UI.RawUI.BackgroundColor = "DarkBlue"; $Host.UI.RawUI.ForegroundColor = "White"; Clear-Host
$Host.UI.RawUI.WindowTitle = "FEDERAL BUREAU OF INVESTIGATION"
Write-Host "================================================"
Write-Host "   FBI - THIS COMPUTER HAS BEEN LOCKED"
Write-Host "================================================"
Write-Host ""
Type-Line "Illegal activity has been detected on this device."
Start-Sleep -Milliseconds 800
Type-Line "This incident has been reported."
Start-Sleep -Seconds 2
Write-Host ""
$Host.UI.RawUI.ForegroundColor = "Green"
Type-Line "relax, this is fake, nothing was reported anywhere" 25
Write-Host ""
Write-Host "Press Enter to close..."
Read-Host
"""

EFFECTS["invert_colors"] = type_line_fn() + r"""
$Host.UI.RawUI.BackgroundColor = "Black"; $Host.UI.RawUI.ForegroundColor = "Magenta"; Clear-Host
$Host.UI.RawUI.WindowTitle = "Display Calibration"
Type-Line "Adjusting color profile..."
try {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing

    $bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
    $shot = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
    $sg = [System.Drawing.Graphics]::FromImage($shot)
    $sg.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
    $sg.Dispose()

    $cm = New-Object System.Drawing.Imaging.ColorMatrix
    $cm.Matrix00 = -1; $cm.Matrix11 = -1; $cm.Matrix22 = -1
    $cm.Matrix33 = 1
    $cm.Matrix40 = 1; $cm.Matrix41 = 1; $cm.Matrix42 = 1
    $ia = New-Object System.Drawing.Imaging.ImageAttributes
    $ia.SetColorMatrix($cm)

    $inverted = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
    $ig = [System.Drawing.Graphics]::FromImage($inverted)
    $rect = New-Object System.Drawing.Rectangle(0, 0, $bounds.Width, $bounds.Height)
    $ig.DrawImage($shot, $rect, 0, 0, $bounds.Width, $bounds.Height, [System.Drawing.GraphicsUnit]::Pixel, $ia)
    $ig.Dispose()
    $shot.Dispose()

    $overlay = New-Object System.Windows.Forms.Form
    $overlay.FormBorderStyle = 'None'
    $overlay.StartPosition = 'Manual'
    $overlay.Location = $bounds.Location
    $overlay.Size = $bounds.Size
    $overlay.TopMost = $true
    $overlay.ShowInTaskbar = $false
    $overlay.BackgroundImage = $inverted
    $overlay.BackgroundImageLayout = 'Stretch'
    $overlay.Show()
    [System.Windows.Forms.Application]::DoEvents()
    Start-Sleep -Seconds 6
    $overlay.Close()
} catch {}
Write-Host ""
Type-Line "relax, just inverted your colors for a few seconds, back to normal now" 25
Write-Host ""
Write-Host "Press Enter to close..."
Read-Host
"""

EFFECTS["cursor_drift"] = type_line_fn() + r"""
$Host.UI.RawUI.BackgroundColor = "Black"; $Host.UI.RawUI.ForegroundColor = "Cyan"; Clear-Host
$Host.UI.RawUI.WindowTitle = "Mouse Calibration"
Type-Line "Calibrating mouse sensitivity..."
try {
    Add-Type -AssemblyName System.Windows.Forms
    $rand = New-Object Random
    $endTime = (Get-Date).AddSeconds(8)
    while ((Get-Date) -lt $endTime) {
        $pos = [System.Windows.Forms.Cursor]::Position
        $dx = $rand.Next(-15,16)
        $dy = $rand.Next(-15,16)
        [System.Windows.Forms.Cursor]::Position = New-Object System.Drawing.Point(($pos.X+$dx),($pos.Y+$dy))
        Start-Sleep -Milliseconds 120
    }
} catch {}
Write-Host ""
Type-Line "relax, just messing with your cursor, it's back to normal now" 25
Write-Host ""
Write-Host "Press Enter to close..."
Read-Host
"""

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()

BUTTON_LABELS = {
    "fake_hack": "Fake Hack Screen",
    "fake_virus": "Fake Virus Scan",
    "fake_bsod": "Fake Blue Screen",
    "fake_ransom": "Fake Ransomware",
    "fake_update": "Fake Windows Update",
    "fake_format": "Fake Format C:",
    "matrix_rain": "Matrix Rain",
    "fake_webcam": "Fake Webcam Hack",
    "fake_tracking": "Fake IP Tracker",
    "self_destruct": "Self Destruct Countdown",
    "fake_miner": "Fake Crypto Miner",
    "fake_fbi": "Fake FBI Lock Screen",
    "invert_colors": "Invert Screen Colors",
    "cursor_drift": "Cursor Drift",
}

buttons_html = "\n".join(
    f'<button onclick="fire(\'{key}\')">{label}</button>'
    for key, label in BUTTON_LABELS.items()
)

PAGE = f"""<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Troll Dashboard</title>
<style>
body {{ font-family: sans-serif; background: #111; color: #eee; text-align: center; padding: 20px; }}
button {{ display:block; width:100%; max-width:320px; margin:12px auto; padding:18px; font-size:18px;
  background:#222; color:#0f0; border:2px solid #0f0; border-radius:8px; }}
button:active {{ background:#0f0; color:#000; }}
h1 {{ color:#0f0; }}
#status {{ min-height:1.2em; color:#0f0; }}
#status.err {{ color:#f55; }}
</style>
</head>
<body>
<h1>Troll Dashboard</h1>
{buttons_html}
<p id="status"></p>
<script>
// The secret rides in the link's #fragment, which browsers never send over
// the network, so the tunnel and its provider never see it.
const KEY = new URLSearchParams(location.hash.slice(1)).get('k') || '';
const status = document.getElementById('status');
if (!KEY) {{ status.className = 'err'; status.innerText = 'no key in the link - open the full link the PC printed'; }}
function fire(effect) {{
  status.className = '';
  status.innerText = 'Firing ' + effect + '...';
  fetch('/trigger?effect=' + encodeURIComponent(effect) + '&k=' + encodeURIComponent(KEY)).then(r => {{
    if (r.ok) status.innerText = effect + ' launched!';
    else {{ status.className = 'err'; status.innerText = r.status === 403 ? 'wrong key' : 'unknown effect'; }}
  }}).catch(() => {{ status.className = 'err'; status.innerText = "can't reach the PC"; }});
}}
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if url.path in ("/", "/index.html"):
            # The page itself is harmless (no secret in it); it reads the key
            # from its own link and sends it back only when you press a button.
            self._send(200, PAGE.encode(), "text/html")
        elif url.path == "/trigger":
            if not secrets.compare_digest(query.get("k", [""])[0], self.server.secret):
                return self._send(403, b"forbidden")
            effect = query.get("effect", [""])[0]
            if effect not in EFFECTS:
                return self._send(404, b"unknown effect")
            subprocess.Popen([PS_EXE, "-NoExit", "-Command", EFFECTS[effect]],
                             creationflags=subprocess.CREATE_NEW_CONSOLE)
            self._send(200, b"ok")
        else:
            self._send(404, b"not found")

    def _send(self, status, body, ctype="text/plain"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def find_cloudflared():
    """cloudflared next to this file, in locoder's bin, or on PATH."""
    for candidate in (HERE / "cloudflared.exe", Path.home() / ".locoder" / "bin" / "cloudflared.exe"):
        if candidate.is_file():
            return str(candidate)
    return shutil.which("cloudflared")


def start_tunnel(port):
    """A Cloudflare quick tunnel to this server; returns its https address."""
    binary = find_cloudflared()
    if not binary:
        print("no tunnel: cloudflared.exe not found - home Wi-Fi only")
        return None
    proc = subprocess.Popen([binary, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    url = []

    def read():
        for line in proc.stdout:
            match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
            if match and not url:
                url.append(match.group(0))

    threading.Thread(target=read, daemon=True).start()
    for _ in range(300):
        if url:
            return url[0]
        threading.Event().wait(0.1)
    print("no tunnel: cloudflared didn't come up - home Wi-Fi only")
    return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="A prank remote for your own PC, from your phone.")
    parser.add_argument("--port", type=int, default=5000, help="port to listen on (default 5000)")
    parser.add_argument("--tunnel", action="store_true", help="also reachable from anywhere through Cloudflare")
    parser.add_argument("--new-key", action="store_true", help="make a fresh secret (old links stop working)")
    args = parser.parse_args()

    if args.new_key:
        try:
            os.remove(SECRET_FILE)
        except OSError:
            pass
    secret = load_secret()

    server = HTTPServer(("0.0.0.0", args.port), Handler)
    server.secret = secret
    ip = get_local_ip()

    print("Troll Dashboard is running. Only someone with the link below can fire a prank.\n")
    print(f"On your phone (same Wi-Fi), open:\n  http://{ip}:{args.port}/#k={secret}\n")
    if args.tunnel:
        public = start_tunnel(args.port)
        if public:
            print(f"From anywhere, open:\n  {public}/#k={secret}\n")
    print("Keep this link private. Ctrl+C stops the server.\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
