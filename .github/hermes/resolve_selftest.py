#!/usr/bin/env python3
"""Jauge — selftests du noyau de résolution ([HERMES-RESOLVE-TESTS]).

Extraits de `resolve.py` pour que le module de décision reste court et lisible
(< 500 lignes) : ici, uniquement des contrôles. Aucun réseau, aucun secret —
la jambe Fireworks est pilotée par un transport factice.

Usage : `python3 .github/hermes/resolve.py --selftest`
        (ou directement : `python3 .github/hermes/resolve_selftest.py`)
"""

from __future__ import annotations

import resolve


def _verifier(condition, libelle: str, echecs: list[str]) -> None:
    if not condition:
        echecs.append(libelle)


def _selftest_jambe(echecs: list[str]) -> None:
    """La jambe directe doit être Fireworks, et la clé fw doit suffire."""
    _verifier(resolve.JAMBE_DIRECTE == "fireworks", "jambe directe != fireworks", echecs)
    _verifier(
        resolve.FIREWORKS_URL == "https://api.fireworks.ai/inference/v1/chat/completions",
        "URL Fireworks inattendue",
        echecs,
    )
    _verifier(
        resolve.FIREWORKS_MODEL == "accounts/fireworks/models/deepseek-v4p1-flash-1017",
        "modèle Fireworks inattendu",
        echecs,
    )
    _verifier(resolve.FIREWORKS_KEY_ENV == "FIREWORKS_API_KEY", "nom de secret inattendu", echecs)
    _verifier(
        resolve.jambe_llm_disponible({resolve.FIREWORKS_KEY_ENV: "cle-fictive"}) is True,
        "clé Fireworks présente mais jambe jugée indisponible",
        echecs,
    )
    _verifier(resolve.jambe_llm_disponible({}) is False, "absence de clé non détectée", echecs)
    _verifier(
        resolve.jambe_llm_disponible({resolve.FIREWORKS_KEY_ENV: "   "}) is False,
        "clé vide acceptée",
        echecs,
    )


def _selftest_derives(echecs: list[str]) -> None:
    derives = [
        "uv.lock",
        "sub/uv.lock",
        "poetry.lock",
        ".venv/lib/x.py",
        "cdk.out/Jauge.template.json",
        "src/jauge/__pycache__/x.cpython-312.pyc",
        "scripts/legacy.pyc",
    ]
    for chemin in derives:
        _verifier(resolve.est_derive(chemin), "dérivé non reconnu : %s" % chemin, echecs)
    for chemin in ["src/jauge/engine/stats.py", "docs/HERMES.md", "pyproject.toml"]:
        _verifier(
            not resolve.est_derive(chemin), "source prise pour un dérivé : %s" % chemin, echecs
        )
    _verifier(resolve.est_binaire("infra/logo.png"), "binaire non reconnu", echecs)
    _verifier(not resolve.est_binaire("src/jauge/x.py"), "source prise pour un binaire", echecs)
    _verifier(
        resolve.motif_sans_ia("uv.lock", "a", "b", "c") is not None,
        "IA autorisée sur uv.lock",
        echecs,
    )
    _verifier(
        resolve.motif_sans_ia("src/jauge/x.py", "a", "b", "c") is None,
        "IA refusée sur une source",
        echecs,
    )
    _verifier(
        resolve.motif_sans_ia("src/jauge/x.py", "a" * (resolve.LLM_MAX_SIDE + 1), "b", "c")
        is not None,
        "fichier trop gros non écarté",
        echecs,
    )


def _selftest_conflits(echecs: list[str]) -> None:
    style_merge = "avant\n<<<<<<< HEAD\nx\n=======\ny\n>>>>>>> abc\naprès"
    segments, hunks = resolve.decouper_conflits(style_merge)
    _verifier(segments is not None and hunks == 1, "parse style merge", echecs)
    if segments:
        _verifier(segments[0] == ("clair", "avant"), "segment clair de tête", echecs)
        _verifier(segments[1][1] == "x" and segments[1][2] == "y", "côtés du conflit", echecs)
        _verifier(segments[1][3] is None, "base absente en style merge", echecs)
        _verifier(segments[2] == ("clair", "après"), "segment clair de queue", echecs)
    style_diff3 = "<<<<<<< HEAD\nx\n||||||| base\nz\n=======\ny\n>>>>>>> abc"
    segments3, hunks3 = resolve.decouper_conflits(style_diff3)
    _verifier(segments3 is not None and hunks3 == 1, "parse style diff3", echecs)
    if segments3:
        _verifier(segments3[0][3] == "z", "base diff3 mal lue", echecs)
    _verifier(
        resolve.decouper_conflits("a\n>>>>>>> x\nb") == (None, 0),
        "marqueur orphelin accepté",
        echecs,
    )
    _verifier(
        resolve.decouper_conflits("propre\n")[1] == 0, "fichier propre vu comme conflit", echecs
    )


def _selftest_sortie(echecs: list[str]) -> None:
    _verifier(
        resolve.sortie_ia_valide("x.py", "", "a", "b") == "sortie vide",
        "sortie vide acceptée",
        echecs,
    )
    _verifier(
        resolve.sortie_ia_valide("x.py", "<<<<<<< a\nb", "a", "b") is not None,
        "marqueur de conflit accepté",
        echecs,
    )
    _verifier(
        resolve.sortie_ia_valide("x.py", "print(1)\n", "print(1)\n", "print(2)\n") is None,
        "sortie Python valide refusée",
        echecs,
    )
    _verifier(
        resolve.sortie_ia_valide("x.py", "def f(:\n", "print(1)\n", "print(2)\n") is not None,
        "syntaxe Python invalide acceptée",
        echecs,
    )
    _verifier(
        resolve.sortie_ia_valide("x.json", "{invalide", '{"a":1}', '{"b":2}') is not None,
        "JSON invalide accepté",
        echecs,
    )
    _verifier(
        resolve.sortie_ia_valide("x.json", '{"a":1}', '{"a":1}', '{"b":2}') is None,
        "JSON valide refusé",
        echecs,
    )
    _verifier(
        resolve.sortie_ia_valide("x.py", "court", "a" * 100, "b" * 100) is not None,
        "troncature non détectée",
        echecs,
    )


def _selftest_heuristique(echecs: list[str]) -> None:
    _verifier(
        resolve.resoudre_heuristique("uv.lock") == "base", "uv.lock pas résolu côté base", echecs
    )
    _verifier(
        resolve.resoudre_heuristique("assets/logo.png") == "base", "binaire pas côté base", echecs
    )
    _verifier(resolve.resoudre_heuristique("docs/HERMES.md") == "union", "doc pas en union", echecs)
    _verifier(
        resolve.resoudre_heuristique("src/jauge/engine/stats.py") is None,
        "source résolue au hasard",
        echecs,
    )
    _verifier(
        resolve.resoudre_heuristique("pyproject.toml") is None, "pyproject résolu au hasard", echecs
    )


def _selftest_llm(echecs: list[str]) -> None:
    """Jambe Fireworks pilotée par un transport factice : aucun réseau, aucun secret."""
    vus = {}

    def transport_factice(url, corps, cle, timeout):
        vus.update({"url": url, "corps": corps, "cle": cle, "timeout": timeout})
        return {"choices": [{"message": {"content": "```python\nprint(1)\n```"}}]}

    sortie = resolve.appeler_fireworks("sys", "user", "cle-fictive", transport=transport_factice)
    _verifier(sortie == "print(1)", "bloc de code mal dépouillé : %r" % (sortie,), echecs)
    _verifier(vus.get("url") == resolve.FIREWORKS_URL, "transport appelé sur une autre URL", echecs)
    _verifier(
        (vus.get("corps") or {}).get("model") == resolve.FIREWORKS_MODEL, "modèle du corps", echecs
    )
    _verifier((vus.get("corps") or {}).get("temperature") == 0.0, "température non nulle", echecs)

    def transport_marqueurs(url, corps, cle, timeout):
        return {"choices": [{"message": {"content": "<<<<<<< a\nb"}}]}

    _verifier(
        resolve.appeler_fireworks("s", "u", "k", transport=transport_marqueurs) is None,
        "sortie à marqueurs acceptée",
        echecs,
    )

    def transport_erreur(url, corps, cle, timeout):
        return {"_erreur": "HTTP 401"}

    _verifier(
        resolve.appeler_fireworks("s", "u", "k", transport=transport_erreur) is None,
        "erreur HTTP acceptée",
        echecs,
    )
    _verifier(
        resolve.resoudre_fichier("uv.lock", "a", "b", "c", "k", transport=transport_factice)
        is None,
        "IA appliquée à un dérivé",
        echecs,
    )
    _verifier(
        resolve.resoudre_fichier("src/jauge/x.py", "a", "b", "c", "", transport=transport_factice)
        is None,
        "IA appliquée sans clé",
        echecs,
    )


def selftest() -> int:
    """Contrôles hors réseau et hors secret. Rend 0 si tout est vert."""
    echecs: list[str] = []
    for controle in (
        _selftest_jambe,
        _selftest_derives,
        _selftest_conflits,
        _selftest_sortie,
        _selftest_heuristique,
        _selftest_llm,
    ):
        controle(echecs)
    for echec in echecs:
        print("  KO  %s" % echec)
    if echecs:
        print("resolve.py --selftest : %d échec(s)" % len(echecs))
        return 1
    print("resolve.py --selftest : OK (6 groupes, sans réseau ni secret)")
    return 0


if __name__ == "__main__":
    raise SystemExit(selftest())
