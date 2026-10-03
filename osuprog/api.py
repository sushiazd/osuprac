"""Client pour l'API officielle osu! v2 (identifiants client, portée publique).

Utilisable depuis plusieurs threads : une session HTTP par thread et un
limiteur de débit commun pour rester sous la limite d'osu! (1200 requêtes/min).
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from .config import credentials

BASE = "https://osu.ppy.sh/api/v2"
TOKEN_URL = "https://osu.ppy.sh/oauth/token"
# Format de score « lazer » (statistiques great/ok/meh/miss, mods avec réglages).
API_VERSION = "20240529"
MAX_REQUESTS_PER_SECOND = 10  # 600/min, la moitié de la limite officielle
WORKERS = 6


class MissingCredentials(RuntimeError):
    pass


class AuthError(RuntimeError):
    pass


class OsuApi:
    def __init__(self, client_id: str | None = None, client_secret: str | None = None):
        if client_id is None or client_secret is None:
            client_id, client_secret = credentials()
        if not client_id or not client_secret:
            raise MissingCredentials(
                "OSU_CLIENT_ID et OSU_CLIENT_SECRET manquent dans le fichier .env "
                "(voir le README pour créer une application OAuth)."
            )
        self.client_id = client_id
        self.client_secret = client_secret
        self._local = threading.local()
        self._token_lock = threading.Lock()
        self._rate_lock = threading.Lock()
        self._next_slot = 0.0
        self._token = None
        self._token_expiry = 0.0

    @property
    def http(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers["User-Agent"] = "osu-progression (usage perso)"
            self._local.session = session
        return session

    def _wait_turn(self) -> None:
        with self._rate_lock:
            now = time.monotonic()
            slot = max(now, self._next_slot)
            self._next_slot = slot + 1 / MAX_REQUESTS_PER_SECOND
        time.sleep(max(0.0, slot - now))

    def _auth_header(self) -> dict:
        with self._token_lock:
            if not self._token or time.time() > self._token_expiry - 60:
                r = self.http.post(
                    TOKEN_URL,
                    data={
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "grant_type": "client_credentials",
                        "scope": "public",
                    },
                    timeout=30,
                )
                if r.status_code == 401:
                    raise AuthError(
                        "osu! refuse ta clé API : l'ID client et le secret ne correspondent pas. "
                        "Dans les paramètres osu! (section OAuth), clique sur « Éditer » sur ton application, "
                        "recopie l'ID client et le secret client dans .env, puis relance le dashboard."
                    )
                if r.status_code != 200:
                    raise AuthError(f"Authentification osu! impossible (HTTP {r.status_code}), réessaie plus tard.")
                data = r.json()
                self._token = data["access_token"]
                self._token_expiry = time.time() + data.get("expires_in", 3600)
            return {"Authorization": f"Bearer {self._token}", "x-api-version": API_VERSION}

    def get(self, path: str, params: dict | None = None):
        for attempt in range(4):
            self._wait_turn()
            r = self.http.get(BASE + path, params=params, headers=self._auth_header(), timeout=30)
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"Trop de requêtes vers l'API osu! ({path})")

    def map_parallel(self, fn, items, progress=None) -> list:
        """Applique fn à chaque élément sur plusieurs threads (le limiteur reste commun)."""
        results = [None] * len(items)
        with ThreadPoolExecutor(WORKERS) as pool:
            futures = {pool.submit(fn, item): i for i, item in enumerate(items)}
            for done, future in enumerate(as_completed(futures), 1):
                results[futures[future]] = future.result()
                if progress:
                    progress(done, len(items))
        return results

    def user(self, username: str) -> dict | None:
        return self.get(f"/users/@{username}/osu")

    def user_scores(self, user_id: int, kind: str, include_fails: bool = False) -> list[dict]:
        """kind = 'best' ou 'recent'. Parcourt toutes les pages disponibles."""
        out, offset = [], 0
        while True:
            page = self.get(
                f"/users/{user_id}/scores/{kind}",
                {"mode": "osu", "limit": 100, "offset": offset,
                 "include_fails": int(include_fails), "legacy_only": 0},
            ) or []
            out.extend(page)
            if len(page) < 100 or offset >= 900:
                return out
            offset += 100

    def most_played(self, user_id: int) -> list[dict]:
        """Toutes les maps que tu as jouées, avec le nombre de parties sur chacune."""
        out, offset = [], 0
        while True:
            page = self.get(f"/users/{user_id}/beatmapsets/most_played", {"limit": 100, "offset": offset}) or []
            out.extend(page)
            if len(page) < 100:
                return out
            offset += 100

    def user_beatmap_scores(self, beatmap_id: int, user_id: int) -> list[dict]:
        """Tous les scores qu'osu! a gardés pour toi sur une map."""
        data = self.get(f"/beatmaps/{beatmap_id}/scores/users/{user_id}/all", {"ruleset": "osu"})
        return (data or {}).get("scores", [])

    def search_beatmapsets(self, query: str, cursor_string: str | None = None) -> dict:
        params = {"q": query, "m": 0, "s": "ranked", "sort": "plays_desc"}
        if cursor_string:
            params["cursor_string"] = cursor_string
        return self.get("/beatmapsets/search", params) or {"beatmapsets": []}
