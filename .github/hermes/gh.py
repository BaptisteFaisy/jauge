#!/usr/bin/env python3
"""Jauge — accès minimal à l'API GitHub pour les workflows Hermes ([HERMES-API]).

Bibliothèque **stdlib seule** (aucun `pip` dans un workflow), partagée par
`dispatch.py`, `resolver.py` et la branche de dispatch de `gate.py` : une seule
implémentation des appels (labels, commentaires, check-runs, dispatches),
donc zéro duplication.

Aucune valeur de secret n'est jamais journalisée : les erreurs ne rendent que
le code HTTP et le nom de classe d'exception.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import urllib.error
import urllib.request

API = "https://api.github.com"
# Marqueurs relus par le gate pour son compteur de tentatives et son backoff.
MARQUEUR = "<!-- hermes-resolver:v1 -->"
RE_ESSAIS = re.compile(r"<!-- hermes-resolver:attempts=(\d+) -->")
RE_AT = re.compile(
    r"<!-- hermes-resolver:at=([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z) -->"
)
RE_HEURE = "%Y-%m-%dT%H:%M:%SZ"


def jeton() -> str:
    """Jeton d'API (PAT `HERMES_PUSH_TOKEN` si présent, sinon `GH_TOKEN`)."""
    return (os.environ.get("GH_TOKEN") or "").strip()


def depot() -> str:
    """Dépôt `owner/name` du run courant."""
    return (os.environ.get("GITHUB_REPOSITORY") or "").strip()


def maintenant() -> str:
    """Horodatage UTC au format GitHub."""
    return datetime.datetime.now(datetime.UTC).strftime(RE_HEURE)


def api(methode: str, chemin: str, corps=None, jeton_api: str | None = None, timeout: int = 60):
    """Appel API. Rend `(code, données)` ; `code` vaut 0 sur panne réseau."""
    jeton_api = jeton() if jeton_api is None else jeton_api
    donnees = json.dumps(corps).encode() if corps is not None else None
    requete = urllib.request.Request(API + chemin, data=donnees, method=methode)
    requete.add_header("Authorization", "Bearer " + jeton_api)
    requete.add_header("Accept", "application/vnd.github+json")
    requete.add_header("X-GitHub-Api-Version", "2022-11-28")
    if donnees is not None:
        requete.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(requete, timeout=timeout) as reponse:
            brut = reponse.read()
            return reponse.status, (json.loads(brut) if brut else {})
    except urllib.error.HTTPError as exc:
        try:
            charge = json.loads(exc.read() or b"{}")
        except Exception:  # noqa: BLE001 - corps illisible : le code suffit
            charge = {}
        return exc.code, charge
    except Exception as exc:  # noqa: BLE001 - nom de classe seulement
        print("[api] réseau KO (%s) %s %s" % (type(exc).__name__, methode, chemin), flush=True)
        return 0, {}


def ok(code: int) -> bool:
    """Vrai pour un code 2xx."""
    return 200 <= code < 300


# ------------------------------------------------------------------- labels
def label(numero: int, nom: str, ajouter: bool, jeton_api: str | None = None) -> bool:
    """Pose ou retire un label sur une PR."""
    chemin = "/repos/%s/issues/%d/labels" % (depot(), numero)
    if ajouter:
        code, _ = api("POST", chemin, {"labels": [nom]}, jeton_api)
    else:
        code, _ = api("DELETE", "%s/%s" % (chemin, nom.replace("/", "%2F")), None, jeton_api)
    return ok(code)


# -------------------------------------------------------------- commentaires
def commentaires(numero: int, jeton_api: str | None = None) -> list:
    """Commentaires d'une PR (liste vide en cas d'erreur)."""
    code, donnees = api(
        "GET", "/repos/%s/issues/%d/comments?per_page=100" % (depot(), numero), None, jeton_api
    )
    return donnees if ok(code) and isinstance(donnees, list) else []


def commenter(numero: int, corps: str, marqueur: str | None = None, jeton_api=None) -> bool:
    """Écrit un commentaire, ou met à jour celui qui porte `marqueur` (un seul par PR)."""
    if marqueur:
        for existant in commentaires(numero, jeton_api):
            if marqueur in (existant.get("body") or ""):
                code, _ = api(
                    "PATCH",
                    "/repos/%s/issues/comments/%s" % (depot(), existant.get("id")),
                    {"body": corps},
                    jeton_api,
                )
                return ok(code)
    code, _ = api(
        "POST", "/repos/%s/issues/%d/comments" % (depot(), numero), {"body": corps}, jeton_api
    )
    return ok(code)


# ---------------------------------------------------------------- check-runs
def check_runs(sha: str, jeton_api: str | None = None) -> list:
    """Check-runs attachés à un SHA (liste vide en cas d'erreur)."""
    code, donnees = api(
        "GET", "/repos/%s/commits/%s/check-runs?per_page=100" % (depot(), sha), None, jeton_api
    )
    if not ok(code) or not isinstance(donnees, dict):
        return []
    return donnees.get("check_runs") or []


def check_run(sha: str, nom: str, conclusion: str, titre: str, resume: str, jeton_api=None) -> bool:
    """Publie un check-run terminé (`conclusion` = success|failure|neutral)."""
    code, _ = api(
        "POST",
        "/repos/%s/check-runs" % depot(),
        {
            "name": nom,
            "head_sha": sha,
            "status": "completed",
            "conclusion": conclusion,
            "completed_at": maintenant(),
            "output": {"title": titre[:140], "summary": (resume or "")[:60000]},
        },
        jeton_api,
    )
    return ok(code)


def check_ouvert(sha: str, nom: str, jeton_api: str | None = None):
    """Check-run NON terminé portant `nom` sur ce SHA (verrou anti-doublon), ou None."""
    for run in check_runs(sha, jeton_api):
        if run.get("name") == nom and run.get("status") != "completed":
            return run
    return None


# ------------------------------------------------------------- compteur/backoff
def essais(numero: int, jeton_api: str | None = None) -> int:
    """Tentatives réelles déjà consommées (compteur rangé dans le rapport)."""
    meilleur = 0
    for commentaire in commentaires(numero, jeton_api):
        trouve = RE_ESSAIS.search(commentaire.get("body") or "")
        if trouve:
            meilleur = max(meilleur, int(trouve.group(1)))
    return meilleur


def _heure(texte: str):
    try:
        return datetime.datetime.strptime(texte or "", RE_HEURE).replace(tzinfo=datetime.UTC)
    except (TypeError, ValueError):
        return None


def derniere_activite(numero: int, sha: str, nom_check: str, jeton_api=None):
    """Horodatage du dernier passage du résolveur (rapport, puis check terminé)."""
    meilleure = None
    for commentaire in commentaires(numero, jeton_api):
        trouve = RE_AT.search(commentaire.get("body") or "")
        if trouve:
            heure = _heure(trouve.group(1))
            if heure and (meilleure is None or heure > meilleure):
                meilleure = heure
    if meilleure is not None:
        return meilleure
    for run in check_runs(sha, jeton_api):
        if run.get("name") != nom_check or run.get("status") != "completed":
            continue
        heure = _heure(run.get("completed_at") or "")
        if heure and (meilleure is None or heure > meilleure):
            meilleure = heure
    return meilleure


# ------------------------------------------------------------------ dispatch
def dispatcher(workflow: str, ref: str, entrees=None, jeton_api=None) -> bool:
    """`workflow_dispatch` d'un workflow du dépôt."""
    corps = {"ref": ref}
    if entrees is not None:
        corps["inputs"] = entrees
    code, _ = api(
        "POST", "/repos/%s/actions/workflows/%s/dispatches" % (depot(), workflow), corps, jeton_api
    )
    return code == 204 or ok(code)


def runs_actifs(workflow: str, jeton_api: str | None = None) -> int:
    """Nombre de runs en attente ou en cours d'un workflow (frein de capacité)."""
    total = 0
    for statut in ("queued", "in_progress"):
        code, donnees = api(
            "GET",
            "/repos/%s/actions/workflows/%s/runs?status=%s&per_page=100"
            % (depot(), workflow, statut),
            None,
            jeton_api,
        )
        if ok(code) and isinstance(donnees, dict):
            total += len(donnees.get("workflow_runs") or [])
    return total


# ----------------------------------------------------------------------- PR
def pr(numero: int, jeton_api: str | None = None) -> dict | None:
    """PR complète, ou None si illisible."""
    code, donnees = api("GET", "/repos/%s/pulls/%d" % (depot(), numero), None, jeton_api)
    return donnees if ok(code) and isinstance(donnees, dict) and donnees.get("head") else None


def prs_ouvertes(jeton_api: str | None = None) -> list:
    """PR ouvertes (pagination complète)."""
    sortie, page = [], 1
    while page <= 10:
        code, donnees = api(
            "GET",
            "/repos/%s/pulls?state=open&per_page=100&page=%d" % (depot(), page),
            None,
            jeton_api,
        )
        if not ok(code) or not isinstance(donnees, list):
            break
        sortie += donnees
        if len(donnees) < 100:
            break
        page += 1
    return sortie


def conflit(pr_data: dict) -> bool:
    """Vrai si la PR est en VRAI conflit (et pas seulement en retard)."""
    return pr_data.get("mergeable") is False and (pr_data.get("mergeable_state") or "") == "dirty"
