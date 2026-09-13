"""Spotify Web API: official playback control (search/play/pause/skip/volume), not
screen-scraping the desktop app or faking media-key presses via pc_control. Needs one-time OAuth
consent (see app/main.py's GET /jarvis/spotify-login + /jarvis/spotify-callback) and Spotify
Premium for the playback-control endpoints (free accounts can search but not start/pause
playback via the API). Also needs an *active device* -- Spotify open and signed in somewhere
(desktop app, phone, web player); without one, calls return a clear error the model can react to,
e.g. by using run_on_pc to launch the desktop app first.
"""
import logging
import time

import requests
from dotenv import set_key

from .. import config

log = logging.getLogger("jarvis.spotify")

API_BASE = "https://api.spotify.com/v1"
TOKEN_URL = "https://accounts.spotify.com/api/token"

_refresh_token = config.SPOTIFY_REFRESH_TOKEN
_access_token = ""
_access_token_expires = 0.0


def set_refresh_token(token: str) -> None:
    """Called once by the /jarvis/spotify-callback route after the user grants consent. Updates
    the in-memory token immediately (works without a restart) and persists it to .env so it
    survives one."""
    global _refresh_token
    _refresh_token = token
    try:
        set_key(str(config.BASE_DIR / ".env"), "SPOTIFY_REFRESH_TOKEN", token)
    except Exception:
        log.exception("Konnte SPOTIFY_REFRESH_TOKEN nicht in .env schreiben")


def _get_access_token() -> str:
    global _access_token, _access_token_expires
    if _access_token and time.time() < _access_token_expires:
        return _access_token
    if not _refresh_token:
        raise RuntimeError("Spotify nicht verbunden -- einmal /jarvis/spotify-login im Browser oeffnen.")
    resp = requests.post(TOKEN_URL, data={
        "grant_type": "refresh_token",
        "refresh_token": _refresh_token,
        "client_id": config.SPOTIFY_CLIENT_ID,
        "client_secret": config.SPOTIFY_CLIENT_SECRET,
    }, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    _access_token = data["access_token"]
    _access_token_expires = time.time() + data.get("expires_in", 3600) - 30
    return _access_token


def _api(method: str, path: str, **kwargs) -> requests.Response:
    token = _get_access_token()
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {token}"
    return requests.request(method, f"{API_BASE}{path}", headers=headers, timeout=10, **kwargs)


def _friendly_error(resp: requests.Response) -> str:
    try:
        reason = resp.json().get("error", {}).get("reason", "")
    except Exception:
        reason = ""
    if reason == "NO_ACTIVE_DEVICE" or resp.status_code == 404:
        return ("Kein aktives Spotify-Geraet. Spotify muss irgendwo offen/eingeloggt sein, "
                "z.B. die Desktop-App auf dem PC.")
    if resp.status_code == 403:
        return "Wiedergabesteuerung braucht Spotify Premium."
    return f"Spotify-Fehler ({resp.status_code}): {resp.text[:300]}"


def _simple_action(method: str, path: str, ok_message: str) -> str:
    try:
        resp = _api(method, path)
        if resp.status_code in (200, 204):
            return ok_message
        return _friendly_error(resp)
    except RuntimeError as e:
        return str(e)
    except requests.RequestException as e:
        return f"Spotify nicht erreichbar: {e}"


def spotify_search_and_play(query: str) -> str:
    """Searches Spotify for `query` (song/artist/album name) and starts playing the first
    matching track on the user's active device."""
    try:
        resp = _api("GET", "/search", params={"q": query, "type": "track", "limit": 1})
        resp.raise_for_status()
        items = resp.json().get("tracks", {}).get("items", [])
        if not items:
            return f"Nichts gefunden fuer '{query}'."
        track = items[0]
        name = track["name"]
        artists = ", ".join(a["name"] for a in track.get("artists", []))
        play_resp = _api("PUT", "/me/player/play", json={"uris": [track["uri"]]})
        if play_resp.status_code not in (200, 204):
            return _friendly_error(play_resp)
        return f"Spielt jetzt: {name} - {artists}"
    except RuntimeError as e:
        return str(e)
    except requests.RequestException as e:
        return f"Spotify nicht erreichbar: {e}"


def spotify_pause() -> str:
    """Pauses Spotify playback."""
    return _simple_action("PUT", "/me/player/pause", "Pausiert.")


def spotify_resume() -> str:
    """Resumes/continues Spotify playback."""
    return _simple_action("PUT", "/me/player/play", "Wiedergabe fortgesetzt.")


def spotify_next() -> str:
    """Skips to the next track."""
    return _simple_action("POST", "/me/player/next", "Naechster Titel.")


def spotify_previous() -> str:
    """Goes back to the previous track."""
    return _simple_action("POST", "/me/player/previous", "Vorheriger Titel.")


def spotify_set_volume(percent: int) -> str:
    """Sets Spotify playback volume, 0-100."""
    percent = max(0, min(100, int(percent)))
    return _simple_action("PUT", f"/me/player/volume?volume_percent={percent}", f"Lautstaerke {percent}%.")


def spotify_current_track() -> str:
    """Returns what's currently playing on Spotify, if anything."""
    try:
        resp = _api("GET", "/me/player/currently-playing")
        if resp.status_code == 204:
            return "Aktuell laeuft nichts."
        resp.raise_for_status()
        data = resp.json()
        item = data.get("item")
        if not item:
            return "Aktuell laeuft nichts."
        name = item.get("name", "?")
        artists = ", ".join(a["name"] for a in item.get("artists", []))
        state = "spielt" if data.get("is_playing") else "pausiert"
        return f"{name} - {artists} ({state})"
    except RuntimeError as e:
        return str(e)
    except requests.RequestException as e:
        return f"Spotify nicht erreichbar: {e}"
