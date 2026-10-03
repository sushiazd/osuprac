"""Notifications Windows (toasts), sans dépendance : PowerShell et l'API WinRT intégrée à Windows 10/11."""
import subprocess
import sys

# Identifiant d'application déjà enregistré par Windows : les toasts s'affichent sous le nom « Windows PowerShell ».
APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"

SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($env:OSU_TOAST_XML)
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:OSU_TOAST_APP).Show($toast)
"""


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def toast_xml(title: str, lines: list[str]) -> str:
    body = "".join(f"<text>{_escape(line)}</text>" for line in lines[:2])
    return (f'<toast><visual><binding template="ToastGeneric"><text>{_escape(title)}</text>{body}'
            "</binding></visual></toast>")


def toast(title: str, lines: list[str]) -> bool:
    """Affiche une notification. Renvoie False si ce n'est pas possible (autre système, PowerShell bloqué…)."""
    if sys.platform != "win32":
        return False
    import os
    env = {**os.environ, "OSU_TOAST_XML": toast_xml(title, lines), "OSU_TOAST_APP": APP_ID}
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", SCRIPT], env=env, capture_output=True,
            timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def announce(events: list[dict]) -> bool:
    """Une seule notification qui résume les nouveautés d'une synchronisation."""
    if not events:
        return False
    if len(events) == 1:
        return toast(f"osu! progression · {events[0]['title']}", [events[0]["text"]])
    return toast(f"osu! progression · {len(events)} nouveautés",
                 [" · ".join(e["text"] for e in events[:3]), "Ouvre le tableau de bord pour les détails."])
