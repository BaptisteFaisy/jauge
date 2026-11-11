#!/usr/bin/env python3
"""Jauge — chien de garde Hermes ([HERMES-WATCHDOG]).

Problème réglé : le gate (`hermes-lite.yml`) ne se réveille que sur un événement
de PR ou la fin d'un check. Or une PR devient **en conflit** sans qu'aucun
événement ne la concerne : il suffit qu'une autre PR soit fusionnée dans `main`.
Sans réveil, cette PR reste en conflit indéfiniment et aucun résolveur n'est
lancé.

Le chien de garde réveille donc le balayage du gate :
- déclenchement **événementiel** (`workflow_run` à la fin de la CI sur `main`,
  donc juste après une fusion) et manuel — **aucun cron** : sur un dépôt privé,
  un cron coûte et le planificateur GitHub livre mal (leçon [WD-EVENT]) ;
- rate-limit : au moins 10 min entre deux réveils (1 min en mode forcé) ;
- jamais de réveil si un balayage est déjà en attente ou en cours : un dispatch
  de trop annulerait le run en attente du même groupe de concurrence.

Usage : `python3 .github/hermes/watchdog.py --selftest`
"""

from __future__ import annotations

import os
import sys
import time

import gh

GATE = "hermes-lite.yml"
RATE_MIN = 10
FORCE_MIN = 1
STAMP_NOM = "hermes-watchdog-last-dispatch.txt"


def stamp(chemin: str | None = None) -> str:
    """Chemin du fichier d'horodatage (par runner : jamais partagé)."""
    if chemin:
        return chemin
    return os.path.join(os.environ.get("RUNNER_TEMP", "/tmp"), STAMP_NOM)


def balayage_actif(api_fn=None) -> bool:
    """Vrai si un run de `hermes-lite.yml` est en attente ou en cours."""
    lire = api_fn or gh.api
    for statut in ("queued", "in_progress", "waiting", "pending"):
        code, donnees = lire(
            "GET",
            "/repos/%s/actions/workflows/%s/runs?status=%s&per_page=20"
            % (gh.depot(), GATE, statut),
        )
        if 200 <= code < 300 and isinstance(donnees, dict) and donnees.get("workflow_runs"):
            return True
    return False


def trop_recent(chemin: str, fenetre_min: int, maintenant: float | None = None) -> bool:
    """Vrai si un réveil a eu lieu il y a moins de `fenetre_min` minutes."""
    instant = time.time() if maintenant is None else maintenant
    try:
        with open(chemin, encoding="utf-8") as fichier:
            dernier = float(fichier.read().strip())
    except (OSError, ValueError):
        return False
    return (instant - dernier) < fenetre_min * 60


def reveiller(forcer: bool = False, api_fn=None, chemin: str | None = None, maintenant=None) -> int:
    """Réveille le gate si nécessaire. Rend 0 (rien à faire ou dispatché), 1 si échec."""
    lire = api_fn or gh.api
    fichier = stamp(chemin)
    if balayage_actif(lire):
        print("[watchdog] un balayage est déjà actif/en attente — rien à faire", flush=True)
        return 0
    fenetre = FORCE_MIN if forcer else RATE_MIN
    if trop_recent(fichier, fenetre, maintenant):
        print("[watchdog] réveil récent (< %d min) — rien à faire" % fenetre, flush=True)
        return 0
    code, _ = lire(
        "POST", "/repos/%s/actions/workflows/%s/dispatches" % (gh.depot(), GATE), {"ref": "main"}
    )
    if not (200 <= code < 300):
        print("[watchdog] dispatch refusé (HTTP %s)" % code, flush=True)
        return 1
    try:
        with open(fichier, "w", encoding="utf-8") as sortie:
            sortie.write(str(time.time() if maintenant is None else maintenant))
    except OSError as exc:
        print("[watchdog] horodatage non écrit (%s)" % exc, flush=True)
    print("[watchdog] balayage du gate déclenché", flush=True)
    return 0


# ------------------------------------------------------------------ selftest
def _verifier(condition, libelle: str, echecs: list[str]) -> None:
    if not condition:
        echecs.append(libelle)


def _selftest_rate(echecs: list[str]) -> None:
    import tempfile

    racine = tempfile.mkdtemp(prefix="hermes-watchdog-")
    chemin = os.path.join(racine, STAMP_NOM)
    _verifier(not trop_recent(chemin, 10, 1000.0), "absence d'horodatage vue comme récente", echecs)
    with open(chemin, "w", encoding="utf-8") as fichier:
        fichier.write("1000")
    _verifier(trop_recent(chemin, 10, 1000.0 + 60), "réveil d'il y a 1 min accepté", echecs)
    _verifier(not trop_recent(chemin, 10, 1000.0 + 3600), "réveil d'il y a 1 h refusé", echecs)
    with open(chemin, "w", encoding="utf-8") as fichier:
        fichier.write("pas un nombre")
    _verifier(not trop_recent(chemin, 10, 2000.0), "horodatage corrompu bloquant", echecs)


def _selftest_reveil_libre(echecs: list[str]) -> None:
    """Gate libre : dispatch émis une fois, puis rate-limit respecté."""
    import tempfile

    chemin = os.path.join(tempfile.mkdtemp(prefix="hermes-watchdog2-"), STAMP_NOM)
    appels: list[tuple] = []

    def libre(methode, chemin_api, corps=None, *a, **k):
        appels.append((methode, chemin_api))
        return (200, {"workflow_runs": []}) if methode == "GET" else (204, {})

    os.environ.setdefault("GITHUB_REPOSITORY", "BaptisteFaisy/jauge")
    _verifier(
        reveiller(api_fn=libre, chemin=chemin, maintenant=5000.0) == 0,
        "réveil refusé sur un gate libre",
        echecs,
    )
    _verifier(
        any(m == "POST" and c.endswith("/dispatches") for m, c in appels),
        "aucun dispatch émis",
        echecs,
    )
    appels.clear()
    _verifier(
        reveiller(api_fn=libre, chemin=chemin, maintenant=5100.0) == 0,
        "rate-limit non appliqué",
        echecs,
    )
    _verifier(not any(m == "POST" for m, _ in appels), "dispatch malgré le rate-limit", echecs)


def _selftest_reveil_occupe(echecs: list[str]) -> None:
    """Balayage déjà en vol, et dispatch refusé : jamais de doublon, jamais muet."""
    import tempfile

    chemin = os.path.join(tempfile.mkdtemp(prefix="hermes-watchdog3-"), STAMP_NOM)
    appels: list[tuple] = []

    def occupe(methode, chemin_api, corps=None, *a, **k):
        appels.append((methode, chemin_api))
        return (200, {"workflow_runs": [{"id": 1}]}) if methode == "GET" else (204, {})

    _verifier(
        reveiller(forcer=True, api_fn=occupe, chemin=chemin, maintenant=9999.0) == 0,
        "réveil refusé alors qu'un balayage tourne",
        echecs,
    )
    _verifier(not any(m == "POST" for m, _ in appels), "dispatch malgré un balayage actif", echecs)

    def casse(methode, chemin_api, corps=None, *a, **k):
        return (200, {"workflow_runs": []}) if methode == "GET" else (403, {})

    _verifier(
        reveiller(api_fn=casse, chemin=chemin, maintenant=9999.0) == 1,
        "dispatch refusé non signalé",
        echecs,
    )


def selftest() -> int:
    """Contrôles hors réseau et hors secret. Rend 0 si tout est vert."""
    echecs: list[str] = []
    for controle in (_selftest_rate, _selftest_reveil_libre, _selftest_reveil_occupe):
        controle(echecs)
    for echec in echecs:
        print("  KO  %s" % echec)
    if echecs:
        print("watchdog.py --selftest : %d échec(s)" % len(echecs))
        return 1
    print("watchdog.py --selftest : OK (rate-limit, anti-doublon, dispatch intercepté)")
    return 0


def main(argv=None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in arguments:
        return selftest()
    forcer = (os.environ.get("FORCER") or "").lower() in ("true", "1", "yes")
    return reveiller(forcer=forcer)


if __name__ == "__main__":
    raise SystemExit(main())
