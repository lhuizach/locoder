"""Desktop notifications (the model's notify_user), with no extra packages:
PowerShell's built-in toast API on Windows, osascript on macOS, notify-send
on Linux. Fire-and-forget: a notification that can't be shown is skipped
quietly, since the message also appears in locoder itself.

The title and message reach the helper through environment variables, never
the command line, so text the model wrote can't turn into a command.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

_WINDOWS_TOAST = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$texts = $template.GetElementsByTagName('text')
$texts.Item(0).AppendChild($template.CreateTextNode($env:LOCODER_NOTIFY_TITLE)) > $null
$texts.Item(1).AppendChild($template.CreateTextNode($env:LOCODER_NOTIFY_MESSAGE)) > $null
$toast = [Windows.UI.Notifications.ToastNotification]::new($template)
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show($toast)
"""

_MAC_SCRIPT = 'display notification (system attribute "LOCODER_NOTIFY_MESSAGE") with title (system attribute "LOCODER_NOTIFY_TITLE")'


def notify(title: str, message: str) -> bool:
    """Show a desktop notification; True if a helper was started."""
    if os.environ.get("LOCODER_NO_DESKTOP_NOTIFY"):
        return False
    env = {**os.environ, "LOCODER_NOTIFY_TITLE": f"locoder: {title}", "LOCODER_NOTIFY_MESSAGE": message}
    kwargs = {"env": env, "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    try:
        if sys.platform == "win32":
            subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                              "-Command", _WINDOWS_TOAST],
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), **kwargs)
        elif sys.platform == "darwin":
            subprocess.Popen(["osascript", "-e", _MAC_SCRIPT], **kwargs)
        elif shutil.which("notify-send") and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            subprocess.Popen(["sh", "-c", 'notify-send "$LOCODER_NOTIFY_TITLE" "$LOCODER_NOTIFY_MESSAGE"'], **kwargs)
        else:
            return False
        return True
    except OSError:
        return False
