"""Client minimal de l'API REST Microsoft Fabric (lecture seule).

Utilisé par le pré-vol, la résolution des références et la vérification
post-déploiement. Le déploiement lui-même est fait par fabric-cicd.

* pagination : suit ``continuationUri`` / ``continuationToken`` ;
* 429 et 5xx : nouvelles tentatives avec ``Retry-After`` ;
* opérations longues (getDefinition) : suit ``Location`` jusqu'au résultat ;
* aucune valeur de jeton n'est journalisée.
"""

from __future__ import annotations

import base64
import time
from typing import Any, Optional

from urllib.parse import quote

import requests

FABRIC_API = "https://api.fabric.microsoft.com/v1"
FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"


class FabricApiError(Exception):
    def __init__(self, status: int, message: str, url: str):
        super().__init__(f"HTTP {status} sur {url} : {message}")
        self.status = status
        self.url = url


class FabricClient:
    def __init__(self, credential, base_url: str = FABRIC_API, session: Optional[requests.Session] = None,
                 max_retries: int = 5, timeout: int = 60):
        self._credential = credential
        self._base = base_url.rstrip("/")
        self._session = session or requests.Session()
        self._max_retries = max_retries
        self._timeout = timeout
        self._token: Optional[str] = None
        self._token_expiry = 0.0

    # ---- authentification ---------------------------------------------
    def _bearer(self) -> str:
        if not self._token or time.time() > self._token_expiry - 300:
            token = self._credential.get_token(FABRIC_SCOPE)
            self._token = token.token
            self._token_expiry = float(token.expires_on)
        return self._token

    # ---- transport ------------------------------------------------------
    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        if not url.startswith("http"):
            url = f"{self._base}/{url.lstrip('/')}"
        for attempt in range(self._max_retries + 1):
            headers = {"Authorization": f"Bearer {self._bearer()}", "Content-Type": "application/json"}
            response = self._session.request(method, url, headers=headers, timeout=self._timeout, **kwargs)
            if response.status_code in (429, 500, 502, 503, 504) and attempt < self._max_retries:
                try:
                    delay = float(response.headers.get("Retry-After", 2 ** attempt))
                except ValueError:  # Retry-After au format date HTTP
                    delay = float(2 ** attempt)
                time.sleep(min(delay, 60))
                continue
            if response.status_code >= 400:
                raise FabricApiError(response.status_code, response.text[:400], url)
            return response
        raise FabricApiError(429, "nombre maximal de tentatives atteint", url)

    def get(self, path: str) -> dict:
        return self._request("GET", path).json()

    def get_all(self, path: str) -> list[dict]:
        """GET paginé : concatène ``value`` de toutes les pages."""
        values: list[dict] = []
        url: Optional[str] = path
        while url:
            body = self._request("GET", url).json()
            values.extend(body.get("value", []))
            next_uri = body.get("continuationUri")
            token = body.get("continuationToken")
            if next_uri:
                url = next_uri
            elif token:
                separator = "&" if "?" in path else "?"
                url = f"{path}{separator}continuationToken={quote(token, safe='')}"
            else:
                url = None
        return values

    # ---- ressources -----------------------------------------------------
    def list_workspaces(self) -> list[dict]:
        return self.get_all("workspaces")

    def get_workspace(self, workspace_id: str) -> dict:
        return self.get(f"workspaces/{workspace_id}")

    def list_items(self, workspace_id: str) -> list[dict]:
        return self.get_all(f"workspaces/{workspace_id}/items")

    def list_connections(self) -> list[dict]:
        return self.get_all("connections")

    def get_variable_library(self, workspace_id: str, item_id: str) -> dict:
        return self.get(f"workspaces/{workspace_id}/variableLibraries/{item_id}")

    def get_item_definition(self, workspace_id: str, item_id: str, poll_seconds: int = 3, max_wait: int = 300) -> dict[str, str]:
        """Définition d'un item : {chemin de partie: contenu texte décodé}."""
        response = self._request("POST", f"workspaces/{workspace_id}/items/{item_id}/getDefinition")
        if response.status_code == 202:
            location = response.headers.get("Location")
            if not location:
                raise FabricApiError(202, "réponse LRO sans en-tête Location", response.url)
            waited = 0
            while True:
                state = self._request("GET", location).json()
                status = str(state.get("status", "")).lower()
                if status == "succeeded":
                    body = self._request("GET", f"{location.rstrip('/')}/result").json()
                    break
                if status in ("failed", "cancelled"):
                    raise FabricApiError(500, f"getDefinition {status}", location)
                if waited >= max_wait:
                    raise FabricApiError(504, "getDefinition : délai dépassé", location)
                time.sleep(poll_seconds)
                waited += poll_seconds
        else:
            body = response.json()
        parts = (body.get("definition") or {}).get("parts") or []
        decoded: dict[str, str] = {}
        for part in parts:
            payload = part.get("payload", "")
            if part.get("payloadType", "InlineBase64") == "InlineBase64":
                decoded[part["path"]] = base64.b64decode(payload).decode("utf-8", errors="replace")
            else:
                decoded[part["path"]] = str(payload)
        return decoded


def run_item_job(client: "FabricClient", workspace_id: str, item_id: str, job_type: str,
                 timeout_seconds: int = 1800, poll_seconds: int = 15) -> str:
    """Lance un job d'item (ex. jobType=Pipeline) et attend son statut final.

    API Fabric « Run On Demand Item Job » : POST .../jobs/instances?jobType=… -> 202 + Location,
    puis GET Location jusqu'à un statut final (Completed, Failed, Cancelled, Deduped).
    """
    response = client._request("POST", f"workspaces/{workspace_id}/items/{item_id}/jobs/instances?jobType={job_type}")
    location = response.headers.get("Location")
    if not location:
        raise FabricApiError(response.status_code, "réponse sans en-tête Location", response.url)
    waited = 0
    while waited <= timeout_seconds:
        state = client._request("GET", location).json()
        status = str(state.get("status", ""))
        if status in ("Completed", "Failed", "Cancelled", "Deduped"):
            return status
        time.sleep(poll_seconds)
        waited += poll_seconds
    return "Timeout"


def find_unique(entries: list[dict], *, label: str, **criteria: Any) -> tuple[Optional[dict], Optional[str]]:
    """Retourne (entrée, None) si exactement une entrée correspond, sinon (None, motif)."""
    matches = [e for e in entries if all(e.get(k) == v for k, v in criteria.items())]
    if len(matches) == 1:
        return matches[0], None
    if not matches:
        return None, f"{label} introuvable ({criteria}) — absent ou identité sans accès"
    return None, f"{label} ambigu : {len(matches)} correspondances pour {criteria}"
