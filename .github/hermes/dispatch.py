#!/usr/bin/env python3
"""Jauge — dispatch du résolveur de conflits depuis le gate ([HERMES-DISPATCH]).

Le gate (`gate.py`, workflow `hermes-lite.yml`) détecte un **vrai conflit** et
appelle `dispatch_resolver()` : label `hermes/conflit` déjà posé, un run de
`hermes-resolver.yml` est lancé pour cette PR.

Trois freins, tous hérités des pannes du chantier `duello` :
- **anti-doublon** : un check-run `Hermes résolveur (conflits)` encore ouvert
  sur le head signifie qu'un run travaille déjà — on ne double pas ;
- **backoff** : `min(5 * 2^n, 30)` minutes depuis le dernier rapport du
  résolveur (n = tentatives consommées, lues dans le commentaire marqué) ;
- **escalade informative** : au-delà de 3 tentatives, label
  `hermes/intervention-humaine` + commentaire. La boucle ne s'arrête jamais :
  une PR ne se ferme jamais, et un échec n'est pas un état terminal.

Toutes les fonctions acceptent un `api_fn` injectable : les selftests tournent
sans réseau ni secret.

Usage : `python3 .github/hermes/dispatch.py --selftest`
"""

from __future__ import annotations

import datetime
import os
import sys

import gh

WORKFLOW = "hermes-resolver.yml"
CHECK_NAME = "Hermes résolveur (conflits)"
LABEL_CONFLIT = "hermes/conflit"
LABEL_ESCALADE = "hermes/intervention-humaine"
BACKOFF_BASE_MIN = 5
BACKOFF_MAX_MIN = 30
MAX_ESSAIS = 3
# Marqueur du commentaire d'escalade (un seul par PR, mis à jour).
MARQUEUR_ESCALADE = "<!-- hermes:escalade-resolveur:v1 -->"


def backoff_minutes(essais: int) -> int:
    """Délai avant le prochain dispatch : `min(5 * 2^n, 30)` minutes."""
    return min(BACKOFF_BASE_MIN * (2 ** max(int(essais or 0), 0)), BACKOFF_MAX_MIN)


def dernier_rapport(numero: int, api_fn=None) -> float | None:
    """Horodatage (epoch) du dernier rapport du résolveur, ou None."""
    lire = api_fn or gh.api
    code, donnees = lire("GET", "/repos/%s/issues/%d/comments?per_page=100" % (gh.depot(), numero))
    if not (200 <= code < 300) or not isinstance(donnees, list):
        return None
    meilleur = None
    for commentaire in donnees:
        trouve = gh.RE_AT.search(commentaire.get("body") or "")
        if not trouve:
            continue
        try:
            horodatage = datetime.datetime.strptime(trouve.group(1), gh.RE_HEURE).replace(
                tzinfo=datetime.UTC
            )
        except ValueError:
            continue
        stamp = horodatage.timestamp()
        if meilleur is None or stamp > meilleur:
            meilleur = stamp
    return meilleur


def deja_en_cours(sha: str, api_fn=None) -> bool:
    """Vrai si un check-run du résolveur est encore ouvert sur ce head."""
    lire = api_fn or gh.api
    code, donnees = lire("GET", "/repos/%s/commits/%s/check-runs?per_page=100" % (gh.depot(), sha))
    if not (200 <= code < 300) or not isinstance(donnees, dict):
        return False
    for run in donnees.get("check_runs") or []:
        if run.get("name") == CHECK_NAME and run.get("status") != "completed":
            return True
    return False


def _escalader(pr: dict, essais: int) -> None:
    """Information seule : au-delà du seuil, on prévient un humain. Rien ne s'arrête."""
    numero = pr["number"]
    base = (pr.get("base") or {}).get("ref", "main")
    corps = (
        "## 🆘 Hermes — escalade informative : la boucle continue\n\n"
        "Le résolveur de conflits a échoué **%d fois** sur cette PR. Les tentatives "
        "automatiques **ne s'arrêtent pas** (backoff croissant plafonné) tant que la PR "
        "reste ouverte et en conflit.\n\n"
        "Ce qui aiderait — rebaser à la main :\n\n"
        "```bash\ngit fetch origin\ngit rebase origin/%s\ngit push --force-with-lease\n```\n\n"
        "Le label `%s` est **purement informatif** : il est retiré au prochain passage "
        "vert et n'empêche aucun re-dispatch.\n%s\n"
        % (essais, base, LABEL_ESCALADE, MARQUEUR_ESCALADE)
    )
    gh.label(numero, LABEL_ESCALADE, True)
    gh.commenter(numero, corps, marqueur=MARQUEUR_ESCALADE)


def _freiner(pr: dict, lire, instant: float):
    """Freins avant dispatch : head lisible, anti-doublon, backoff.

    Rend `(feu_vert, essais)`. Aucun effet de bord : la décision seule, donc
    testable sans réseau.
    """
    numero = pr["number"]
    head = pr.get("head") or {}
    if not (head.get("sha") and head.get("ref")):
        print("[dispatch] #%d : head illisible — pas de dispatch" % numero, flush=True)
        return False, 0
    essais = gh.essais(numero)
    if deja_en_cours(head["sha"], lire):
        print("[dispatch] #%d : résolveur déjà en cours — pas de doublon" % numero, flush=True)
        return False, essais
    attente = backoff_minutes(essais)
    dernier = dernier_rapport(numero, lire)
    if dernier is not None and (instant - dernier) < attente * 60:
        print(
            "[dispatch] #%d : backoff %d min non écoulé — re-dispatch au cycle suivant"
            % (numero, attente),
            flush=True,
        )
        return False, essais
    return True, essais


def dispatch_resolver(pr: dict, dry: bool = False, api_fn=None, maintenant=None) -> bool:
    """Lance `hermes-resolver.yml` pour cette PR. Rend True si un run est parti."""
    lire = api_fn or gh.api
    numero = pr["number"]
    instant = (
        maintenant if maintenant is not None else datetime.datetime.now(datetime.UTC).timestamp()
    )
    feu, essais = _freiner(pr, lire, instant)
    if not feu:
        return False
    if essais >= MAX_ESSAIS:
        print(
            "[dispatch] #%d : %d tentatives — escalade informative" % (numero, essais), flush=True
        )
        if not dry:
            _escalader(pr, essais)
    if dry:
        print("[dispatch] #%d : dispatch possible (dry-run)" % numero, flush=True)
        return False
    head = pr.get("head") or {}
    corps = {
        "ref": "main",
        "inputs": {
            "pr_number": str(numero),
            "head_ref": head.get("ref") or "",
            "base_ref": (pr.get("base") or {}).get("ref") or "main",
            "head_sha": head.get("sha") or "",
        },
    }
    code, _ = lire(
        "POST", "/repos/%s/actions/workflows/%s/dispatches" % (gh.depot(), WORKFLOW), corps
    )
    if not (200 <= code < 300):
        print("[dispatch] #%d : dispatch refusé (HTTP %s)" % (numero, code), flush=True)
        return False
    print("[dispatch] #%d : résolveur lancé sur %s" % (numero, head.get("ref")), flush=True)
    return True


# ------------------------------------------------------------------ selftest
def _verifier(condition, libelle: str, echecs: list[str]) -> None:
    if not condition:
        echecs.append(libelle)


def _selftest_backoff(echecs: list[str]) -> None:
    attendus = {0: 5, 1: 10, 2: 20, 3: 30, 4: 30, 9: 30}
    for essais, attendu in attendus.items():
        _verifier(
            backoff_minutes(essais) == attendu,
            "backoff(%d) = %d, attendu %d" % (essais, backoff_minutes(essais), attendu),
            echecs,
        )
    _verifier(backoff_minutes(-3) == 5, "backoff d'un compteur négatif", echecs)


def _selftest_rapport(echecs: list[str]) -> None:
    corps = "blabla\n<!-- hermes-resolver:at=2026-11-11T10:00:00Z -->\n"
    faux = lambda *a, **k: (200, [{"body": corps}])  # noqa: E731
    stamp = dernier_rapport(7, faux)
    _verifier(stamp is not None, "horodatage du rapport non lu", echecs)
    if stamp is not None:
        attendu = datetime.datetime(2026, 11, 11, 10, 0, tzinfo=datetime.UTC).timestamp()
        _verifier(abs(stamp - attendu) < 1, "horodatage mal converti", echecs)
    vide = lambda *a, **k: (200, [{"body": "sans marqueur"}])  # noqa: E731
    _verifier(dernier_rapport(7, vide) is None, "rapport sans marqueur accepté", echecs)


def _selftest_doublon(echecs: list[str]) -> None:
    ouvert = lambda *a, **k: (  # noqa: E731
        200,
        {"check_runs": [{"name": CHECK_NAME, "status": "in_progress"}]},
    )
    _verifier(deja_en_cours("abc", ouvert), "check ouvert non détecté", echecs)
    clos = lambda *a, **k: (  # noqa: E731
        200,
        {"check_runs": [{"name": CHECK_NAME, "status": "completed"}]},
    )
    _verifier(not deja_en_cours("abc", clos), "check terminé pris pour un run vivant", echecs)
    panne = lambda *a, **k: (0, {})  # noqa: E731
    _verifier(not deja_en_cours("abc", panne), "panne réseau lue comme run vivant", echecs)


def _selftest_dispatch(echecs: list[str]) -> None:
    """Dispatch réel intercepté : vérifie les entrées envoyées et les refus."""
    appels: list[tuple] = []

    def faux(methode, chemin, corps=None, *a, **k):
        appels.append((methode, chemin, corps))
        return 200, {}

    pr = {
        "number": 12,
        "head": {"sha": "deadbeef", "ref": "feat/x", "repo": {"full_name": gh.depot() or "o/r"}},
        "base": {"ref": "main"},
    }
    os.environ.setdefault("GITHUB_REPOSITORY", "BaptisteFaisy/jauge")
    os.environ.setdefault("GH_TOKEN", "faux")
    parti = dispatch_resolver(pr, dry=False, api_fn=faux, maintenant=10**9)
    _verifier(parti, "dispatch non parti sur un conflit frais", echecs)
    envois = [c for (m, c, corps) in appels if c.endswith("/dispatches")]
    _verifier(bool(envois), "aucun POST de dispatch", echecs)
    if envois:
        _verifier(WORKFLOW in envois[0], "mauvais workflow dispatché : %s" % envois[0], echecs)
    corps = [b for (m, c, b) in appels if c.endswith("/dispatches")]
    if corps:
        entrees = corps[0].get("inputs") or {}
        for cle in ("pr_number", "head_ref", "base_ref", "head_sha"):
            _verifier(cle in entrees, "entrée manquante : %s" % cle, echecs)
        _verifier(entrees.get("head_sha") == "deadbeef", "head_sha mal transmis", echecs)
    _verifier(
        not dispatch_resolver(pr, dry=True, api_fn=faux, maintenant=10**9),
        "dry-run a dispatché",
        echecs,
    )
    _verifier(
        not dispatch_resolver({"number": 13, "head": {}}, api_fn=faux),
        "dispatch sans head accepté",
        echecs,
    )


def selftest() -> int:
    """Contrôles hors réseau et hors secret. Rend 0 si tout est vert."""
    echecs: list[str] = []
    for controle in (_selftest_backoff, _selftest_rapport, _selftest_doublon, _selftest_dispatch):
        controle(echecs)
    for echec in echecs:
        print("  KO  %s" % echec)
    if echecs:
        print("dispatch.py --selftest : %d échec(s)" % len(echecs))
        return 1
    print("dispatch.py --selftest : OK (backoff, anti-doublon, dispatch intercepté)")
    return 0


def main(argv=None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in arguments:
        return selftest()
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
