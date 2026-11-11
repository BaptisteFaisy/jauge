#!/usr/bin/env python3
"""Jauge — noyau de résolution de conflits Hermes ([HERMES-RESOLVE]).

Porté du sous-agent `duello` (`.github/workflows/hermes-conflict-resolver.yml`,
~1 630 lignes) et réduit au **viable minimal** du runbook
`labkit-hermes-prompt.md` phase 3. Ce module est de la **décision pure** :
aucune écriture disque, un seul appel réseau possible (`appeler_fireworks`),
injectable — donc entièrement testable **sans secret**.

Règles dures :
- jambe LLM directe = **Fireworks** (`deepseek-v4p1-flash-1017`), clé lue dans
  `FIREWORKS_API_KEY`. Pas de clé → 100 % déterministe + escalade humaine,
  jamais d'échec silencieux ;
- fichiers **dérivés/binaires** : jamais soumis à l'IA (voir `docs/HERMES.md`) ;
- une sortie d'IA vide, tronquée ou contenant des marqueurs de conflit est
  refusée, et le repli déterministe prend la main.

Usage : `python3 .github/hermes/resolve.py --selftest`
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

# ---------------------------------------------------------------- jambe LLM
FIREWORKS_URL = "https://api.fireworks.ai/inference/v1/chat/completions"
FIREWORKS_MODEL = "accounts/fireworks/models/deepseek-v4p1-flash-1017"
FIREWORKS_KEY_ENV = "FIREWORKS_API_KEY"
# Jambe directe du chantier Jauge : Fireworks, jamais Agnes (runbook §6.3).
JAMBE_DIRECTE = "fireworks"

# Bornes reprises de `duello` : au-delà, le fichier entier ne tient pas dans un
# prompt sans troncature (une troncature produit du code invalide à coup sûr).
LLM_MAX_SIDE = 12000

# ------------------------------------------------- fichiers dérivés/binaires
# Justification détaillée dans `docs/HERMES.md` § « Fichiers dérivés ».
DERIVES_NOMS = ("uv.lock",)
DERIVES_SUFFIXES = (".lock", ".pyc")
DERIVES_DOSSIERS = (".venv/", "cdk.out/", "__pycache__/")
BINAIRES_SUFFIXES = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".pdf",
    ".zip",
    ".gz",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".so",
    ".whl",
)
# Chemins où une résolution « au hasard » coûterait une régression silencieuse :
# jamais de repli heuristique dessus, on escalade.
CRITIQUES = (".github/", "pyproject.toml", "src/", "tests/", "scripts/", "infra/")


# --------------------------------------------------------------- classement
def est_derive(chemin: str) -> bool:
    """Vrai si le fichier est produit par un outil : il se régénère, pas de réécriture."""
    bas = chemin.lower()
    if os.path.basename(bas) in DERIVES_NOMS:
        return True
    if any(bas.endswith(suffixe) for suffixe in DERIVES_SUFFIXES):
        return True
    return any(dossier in bas for dossier in DERIVES_DOSSIERS)


def est_binaire(chemin: str) -> bool:
    """Vrai si l'extension désigne un binaire : jamais envoyé à l'IA."""
    return chemin.lower().endswith(BINAIRES_SUFFIXES)


def jambe_llm_disponible(env=None) -> bool:
    """Vrai si la clé Fireworks est présente : sans elle, 100 % déterministe."""
    source = os.environ if env is None else env
    return bool((source.get(FIREWORKS_KEY_ENV) or "").strip())


def motif_sans_ia(chemin: str, *cotes: str) -> str | None:
    """Raison d'écarter l'IA pour ce fichier, ou None si l'IA peut intervenir."""
    if est_derive(chemin):
        return "fichier dérivé — régénération, pas réécriture d'IA"
    if est_binaire(chemin):
        return "fichier binaire — jamais soumis à l'IA"
    plus_gros = max([len(cote or "") for cote in cotes] or [0])
    if plus_gros > LLM_MAX_SIDE:
        return "%d car. > %d : mode fichier entier impossible" % (plus_gros, LLM_MAX_SIDE)
    return None


# ------------------------------------------------------- découpage des hunks
def _lire_conflit(lignes: list[str], i: int):
    """Lit UN bloc `<<<<<<< … >>>>>>>`. Rend ((ours, theirs, base), i) ou (None, i)."""
    total = len(lignes)
    i += 1
    ours: list[str] = []
    base = None
    while (
        i < total and not lignes[i].startswith("=======") and not lignes[i].startswith("||||||| ")
    ):
        ours.append(lignes[i])
        i += 1
    if i < total and lignes[i].startswith("||||||| "):
        base_lignes: list[str] = []
        i += 1
        while i < total and not lignes[i].startswith("======="):
            base_lignes.append(lignes[i])
            i += 1
        base = "\n".join(base_lignes)
    if i >= total:
        return None, i
    i += 1  # saute la ligne =======
    theirs: list[str] = []
    while i < total and not lignes[i].startswith(">>>>>>> "):
        theirs.append(lignes[i])
        i += 1
    if i >= total:
        return None, i
    return ("\n".join(ours), "\n".join(theirs), base), i + 1


def decouper_conflits(texte: str):
    """Découpe un fichier en conflit en segments.

    Rend `(segments, n)` où un segment est `("clair", texte)` ou
    `("conflit", ours, theirs, base_ou_None)`, ou `(None, 0)` si le parse
    échoue. Styles merge et diff3 (`|||||||`) supportés.
    """
    lignes = texte.split("\n")
    segments: list[tuple] = []
    tampon: list[str] = []
    i, hunks = 0, 0
    while i < len(lignes):
        ligne = lignes[i]
        if ligne.startswith("<<<<<<< "):
            if tampon:
                segments.append(("clair", "\n".join(tampon)))
                tampon = []
            bloc, i = _lire_conflit(lignes, i)
            if bloc is None:
                return None, 0
            segments.append(("conflit",) + bloc)
            hunks += 1
            continue
        if ligne.startswith(("=======", ">>>>>>> ", "||||||| ")):
            return None, 0
        tampon.append(ligne)
        i += 1
    if tampon:
        segments.append(("clair", "\n".join(tampon)))
    return segments, hunks


# ---------------------------------------------------------- garde-fou sortie
def sortie_ia_valide(chemin: str, contenu: str, *cotes: str) -> str | None:
    """Raison de REFUSER une sortie d'IA, ou None si elle est utilisable."""
    if not contenu or not contenu.strip():
        return "sortie vide"
    if any(marque in contenu for marque in ("<<<<<<<", ">>>>>>>")) or "\n=======" in contenu:
        return "marqueurs de conflit résiduels"
    plancher = int(min([len(cote or "") for cote in cotes] or [0]) * 0.5)
    if plancher and len(contenu) < plancher:
        return "sortie tronquée (%d car., minimum attendu %d)" % (len(contenu), plancher)
    bas = chemin.lower()
    if bas.endswith(".json"):
        try:
            json.loads(contenu)
        except Exception as exc:  # noqa: BLE001 - message seulement
            return "JSON invalide (%s)" % exc
    elif bas.endswith(".py"):
        try:
            compile(contenu, chemin, "exec")
        except SyntaxError as exc:
            return "syntaxe Python invalide (%s)" % exc
    return None


# ------------------------------------------------------ repli déterministe
def resoudre_heuristique(chemin: str) -> str | None:
    """Mode déterministe pour ce fichier : "base", "union", ou None (escalade).

    Rappel : pendant un rebase, `--ours` = la BASE et `--theirs` = la PR. Un
    fichier dérivé se résout toujours côté base (il se régénère).
    """
    if est_derive(chemin) or est_binaire(chemin):
        return "base"
    if any(chemin.startswith(critique) or ("/" + critique) in chemin for critique in CRITIQUES):
        return None
    if chemin.lower().endswith((".md", ".txt", ".csv")):
        return "union"
    return None


# ---------------------------------------------------------------- jambe LLM
def _poste(url: str, corps: dict, cle: str, timeout: int) -> dict:
    """POST JSON authentifié. Ne journalise JAMAIS la clé ni son en-tête."""
    requete = urllib.request.Request(url, data=json.dumps(corps).encode(), method="POST")
    requete.add_header("Authorization", "Bearer " + cle)
    requete.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(requete, timeout=timeout) as reponse:
            return json.loads(reponse.read())
    except urllib.error.HTTPError as exc:
        return {"_erreur": "HTTP %s" % exc.code}
    except Exception as exc:  # noqa: BLE001 - nom de classe seulement
        return {"_erreur": type(exc).__name__}


def extraire_contenu(reponse: dict) -> str | None:
    """Texte utile d'une réponse chat (bloc de code retiré), ou None si inutilisable."""
    if not isinstance(reponse, dict) or reponse.get("_erreur"):
        return None
    try:
        message = reponse["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return None
    texte = (message.get("content") or message.get("reasoning_content") or "").strip()
    if not texte:
        return None
    if texte.startswith("```"):
        morceaux = texte.split("```")
        texte = morceaux[1] if len(morceaux) >= 2 else texte
        texte = texte.split("\n", 1)[1] if "\n" in texte else texte
    texte = texte.strip()
    if not texte or any(marque in texte for marque in ("<<<<<<<", ">>>>>>>")):
        return None
    if "\n=======" in texte:
        return None
    return texte


def appeler_fireworks(sysmsg: str, texte: str, cle: str, transport=None, timeout: int = 180):
    """Un appel à la jambe directe Fireworks. Rend le texte, ou None.

    `transport(url, corps, cle, timeout)` est injectable : les selftests
    vérifient la forme du corps et le refus des sorties invalides **sans
    réseau ni secret**.
    """
    corps = {
        "model": FIREWORKS_MODEL,
        "messages": [
            {"role": "system", "content": sysmsg},
            {"role": "user", "content": texte},
        ],
        "max_tokens": 8000,
        "temperature": 0.0,
    }
    envoi = transport if transport is not None else _poste
    return extraire_contenu(envoi(FIREWORKS_URL, corps, cle, timeout))


SYSTEME_FICHIER = (
    "Tu es le résolveur de conflits Hermes du dépôt Jauge (service FinOps Python). "
    "On te donne un fichier en conflit de rebase : BASE (ancêtre commun), A "
    "(branche de base, à conserver par défaut) et B (la PR). Produis le contenu "
    "final qui fusionne les intentions de A et B sans régression et SANS marqueur "
    "de conflit. Réponds UNIQUEMENT avec le contenu complet du fichier dans un "
    "seul bloc de code, sans aucun commentaire."
)


def resoudre_fichier(
    chemin: str,
    base_t: str,
    ours_t: str,
    theirs_t: str,
    cle: str,
    transport=None,
) -> str | None:
    """Résolution par IA d'un fichier entier, ou None (IA écartée ou sortie refusée)."""
    if not cle or motif_sans_ia(chemin, base_t, ours_t, theirs_t):
        return None
    demande = (
        "FICHIER: %s\n\n===== BASE (ancêtre) =====\n%s\n===== A (base de la PR) =====\n%s"
        "\n===== B (PR) =====\n%s"
    ) % (chemin, base_t[:40000], ours_t[:40000], theirs_t[:40000])
    sortie = appeler_fireworks(SYSTEME_FICHIER, demande, cle, transport=transport)
    if sortie is None:
        return None
    if sortie_ia_valide(chemin, sortie, ours_t, theirs_t):
        return None
    return sortie


# ------------------------------------------------------------------ selftest
def selftest() -> int:
    """Contrôles hors réseau et hors secret (implémentation : `resolve_selftest.py`)."""
    import resolve_selftest

    return resolve_selftest.selftest()


def main(argv=None) -> int:
    import sys

    arguments = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in arguments:
        return selftest()
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
