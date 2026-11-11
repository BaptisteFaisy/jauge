#!/usr/bin/env python3
"""Jauge — résolveur Hermes de conflits ([HERMES-RESOLVER]).

Porté du sous-agent `duello` (rebase → résolution IA → repli déterministe →
validation → `push --force-with-lease` → rapport sur la PR), réduit au viable
minimal du runbook `labkit-hermes-prompt.md` §6.

Chaîne exécutée :
1. clone jetable du dépôt + `checkout` de la branche de la PR ;
2. rebase sur `origin/<base>` ; à chaque conflit : jambe **InferHub**
   (`resolve.py`), puis repli **déterministe** (`rebase.py`) ;
3. validation par **la toolchain du dépôt** : `bash scripts/ci-local.sh`
   (ruff + limites + pytest + cfn-lint) — jamais `npm`/`tsc` ici ;
4. `push --force-with-lease` sur la branche de la PR **uniquement** ;
5. rapport : commentaire marqué (compteur de tentatives + horodatage relus par
   le gate) + check-run.

Garde-fous : jamais de push sur la base, abandon si le head a bougé depuis le
dispatch, pas de push si la validation est rouge, aucun secret journalisé,
`--force` nu interdit.

Usage : `python3 .github/hermes/resolver.py --selftest`
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

import gh
import rebase
import resolve

CHECK_NAME = "Hermes résolveur (conflits)"
LABEL_CONFLIT = "hermes/conflit"
LABEL_ESCALADE = "hermes/intervention-humaine"
MARQUEUR = gh.MARQUEUR
VALIDATION_DEFAUT = "bash scripts/ci-local.sh"


# ------------------------------------------------------------------- clone
def cloner(depot: str, work: str, jeton: str, head_ref: str) -> bool:
    """Clone jetable (historique complet requis pour rebaser) + branche de la PR."""
    url = "https://x-access-token:%s@github.com/%s.git" % (jeton, depot)
    commande = [
        "git",
        "clone",
        "--no-tags",
        "-c",
        "user.name=hermes-resolver",
        "-c",
        "user.email=hermes-resolver@jauge.local",
        url,
        work,
    ]
    try:
        resultat = subprocess.run(
            commande,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=600,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except subprocess.TimeoutExpired:
        print("[resolver] clone : timeout", flush=True)
        return False
    if resultat.returncode != 0:
        print("[resolver] clone en échec (code %d)" % resultat.returncode, flush=True)
        return False
    return rebase._git_ok(work, "checkout", "-B", head_ref, "origin/" + head_ref, timeout=300)


def pousser(work: str, head_ref: str, attendu: str, jeton: str) -> bool:
    """`push --force-with-lease` sur la branche de la PR uniquement."""
    lease = (
        "--force-with-lease=refs/heads/%s:%s" % (head_ref, attendu)
        if attendu
        else "--force-with-lease"
    )
    url = "https://x-access-token:%s@github.com/%s.git" % (jeton, gh.depot())
    return rebase._git_ok(work, "push", lease, url, "%s:%s" % (head_ref, head_ref), timeout=600)


# --------------------------------------------------------------- validation
def valider(work: str, commande: str, timeout: int = 200):
    """Lance la toolchain du dépôt. Rend `(ok, extrait de sortie)`."""
    if not commande.strip():
        return True, "Validation désactivée (VALIDATION_CMD vide)."
    chemin = os.environ.get("PATH", "")
    try:
        resultat = subprocess.run(
            ["bash", "-lc", commande],
            cwd=work,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            env={**os.environ, "PATH": chemin + ":/home/baptiste/.local/bin"},
        )
    except subprocess.TimeoutExpired:
        return False, "Validation : timeout après %ss (`%s`)." % (timeout, commande)
    sortie = ((resultat.stdout or "") + (resultat.stderr or "")).strip()
    return resultat.returncode == 0, extraire_sortie(sortie)


def extraire_sortie(sortie: str, caracteres: int = 3000, ligne: int = 200) -> str:
    """Extrait lisible de la fin d'une sortie de validation.

    Double troncature : la fin de la sortie (là où se trouve l'erreur) **et**
    chaque ligne. Sans la troncature par ligne, une seule ligne de JS minifié
    (jsii déverse son runtime quand Node manque) noyait le rapport sur 3 000
    caractères illisibles — constaté sur la PR #28, run 43071083197.
    """
    lignes = [
        element if len(element) <= ligne else element[:ligne] + " …[ligne tronquée]"
        for element in sortie.splitlines()
    ]
    return "\n".join(lignes)[-caracteres:]


# ------------------------------------------------------------------ rapport
def corps_rapport(titre: str, lignes: list[str], essais: int, ok: bool) -> str:
    """Corps du commentaire de PR, avec les marqueurs relus par le gate."""
    entete = (
        "## 🤖 Hermes résolveur — ✅ **conflits résolus, branche repoussée**\n\n"
        if ok
        else "## 🤖 Hermes résolveur — ❌ **résolution impossible**\n\n"
        "_Ce n'est pas un état terminal : le gate re-dispatche au cycle suivant, "
        "avec un backoff croissant plafonné, tant que la PR reste ouverte._\n\n"
    )
    pied = "\n%s\n%s\n%s\n" % (
        "<!-- hermes-resolver:attempts=%d -->" % essais,
        "<!-- hermes-resolver:at=%s -->" % gh.maintenant(),
        MARQUEUR,
    )
    return entete + "**%s**\n\n" % titre + "\n".join(lignes) + pied


def rapporter(numero: int, sha: str, ok: bool, titre: str, lignes: list[str], essais: int) -> None:
    """Commentaire (mis à jour, un seul par PR) + check-run + labels."""
    gh.commenter(numero, corps_rapport(titre, lignes, essais, ok), marqueur=MARQUEUR)
    gh.check_run(sha, CHECK_NAME, "success" if ok else "failure", titre, "\n".join(lignes))
    gh.label(numero, LABEL_CONFLIT, False)
    gh.label(numero, LABEL_ESCALADE, not ok)


def re_armer(head_ref: str, numero: int, pat: bool) -> list[str]:
    """[HERMES-REARM] Un push de bot ne déclenche aucun workflow : on re-arme."""
    if pat:
        print("[rearm] PAT présent : les vrais événements se déclenchent", flush=True)
        return []
    faits = []
    for workflow, ref, entrees in (
        ("ci.yml", head_ref, None),
        ("hermes-lite.yml", "main", {"pr_number": str(numero)}),
    ):
        if gh.dispatcher(workflow, ref, entrees):
            faits.append(workflow)
    return faits


# --------------------------------------------------------------------- flux
def executer(args, transport=None) -> int:
    """Flux complet d'un run de résolution. Rend le code de sortie du job."""
    # Le PAT (contents:write) sert aux opérations git : un push fait avec le
    # GITHUB_TOKEN ne déclenche AUCUN workflow (anti-récursion GitHub).
    jeton = (os.environ.get("HERMES_PUSH_TOKEN") or "").strip() or gh.jeton()
    cle = (os.environ.get(resolve.INFERHUB_KEY_ENV) or "").strip()
    if not jeton:
        print("[resolver] GH_TOKEN absent — abandon", flush=True)
        return 1
    if not cle:
        print("[resolver] INFERHUB_API_KEY absente : 100 % déterministe", flush=True)
    donnees = gh.pr(args.pr)
    if donnees is None:
        print("[resolver] PR #%s illisible ou fermée — abandon transitoire" % args.pr, flush=True)
        return 0
    head = donnees.get("head") or {}
    if (head.get("repo") or {}).get("full_name") not in (None, gh.depot()):
        return _echec(args, donnees, "Fork non supporté", ["La branche vient d'un fork."], 1)
    if head.get("sha") != args.head_sha:
        print("[resolver] head modifié depuis le dispatch — abandon transitoire", flush=True)
        return 0
    work = tempfile.mkdtemp(prefix="hermes-resolver-")
    try:
        return _resoudre(args, donnees, work, jeton, cle, transport)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _publier_succes(args, bilan, head_avant: str, head_apres: str, essais: int) -> None:
    """Rapport de succès : rebase, résolution, validation, ré-armement."""
    lignes = [
        "Branche `%s` rebasée sur `%s` (head `%s` → `%s`)."
        % (args.head_ref, args.base_ref, head_avant[:8], head_apres[:8]),
        bilan.resume(),
        "Validation : ✅ `%s` vert sur l'arbre rebasé." % args.validation,
    ]
    if bilan.regenerer:
        lignes.append(
            "⚠️ **%d fichier(s) dérivé(s) à régénérer** (côté base conservé, jamais une "
            "réécriture d'IA) : %s"
            % (len(bilan.regenerer), ", ".join("`%s`" % f for f in bilan.regenerer[:20]))
        )
    rearmes = re_armer(
        args.head_ref, args.pr, bool((os.environ.get("HERMES_PUSH_TOKEN") or "").strip())
    )
    if rearmes:
        lignes.append("Checks ré-armés : %s." % ", ".join("`%s`" % w for w in rearmes))
    rapporter(
        args.pr,
        head_apres or args.head_sha,
        True,
        "Conflits résolus, branche repoussée",
        lignes,
        essais,
    )


def _resoudre(args, donnees, work: str, jeton: str, cle: str, transport=None) -> int:
    """Clone, rebase, validation, push, rapport. Extrait de `executer` (lisibilité)."""
    essais = gh.essais(args.pr)
    if not cloner(gh.depot(), work, jeton, args.head_ref):
        return _echec(
            args, donnees, "Clone impossible", ["Le clone du dépôt a échoué."], essais + 1
        )
    rebase._git_ok(work, "fetch", "--no-tags", "origin", args.base_ref, args.head_ref, timeout=600)
    avant = rebase.git(work, "rev-parse", "HEAD", timeout=60)
    head_avant = avant.stdout.strip() if avant is not None else ""
    bilan = rebase.rebaser(work, "origin/" + args.base_ref, cle, transport=transport)
    if not bilan.ok:
        rebase.git(work, "rebase", "--abort", timeout=120)
        return _echec(
            args,
            donnees,
            "Rebase non résoluble",
            [bilan.resume(), "Aucun push effectué : la branche distante reste intacte."],
            essais + 1,
        )
    ok, sortie = valider(work, args.validation, args.validation_timeout)
    if not ok:
        rebase.git(work, "rebase", "--abort", timeout=120)
        return _echec(
            args,
            donnees,
            "Validation rouge après rebase",
            [
                "Rebase réussi mais `%s` échoue sur l'arbre rebasé — push bloqué par prudence."
                % args.validation,
                "```",
                sortie,
                "```",
            ],
            essais + 1,
        )
    apres = rebase.git(work, "rev-parse", "HEAD", timeout=60)
    head_apres = apres.stdout.strip() if apres is not None else ""
    if not pousser(work, args.head_ref, args.head_sha, jeton):
        return _echec(
            args,
            donnees,
            "Push refusé",
            ["`--force-with-lease` refusé (head distant modifié entre-temps, ou droits)."],
            essais + 1,
        )
    _publier_succes(args, bilan, head_avant, head_apres, essais)
    print("[resolver] PR #%s : rebase + résolution + push OK" % args.pr, flush=True)
    return 0


def _echec(args, donnees, titre: str, lignes: list[str], essais: int) -> int:
    """Rapporte un échec réel (jamais silencieux) et rend le code de sortie."""
    sha = (donnees.get("head") or {}).get("sha") or args.head_sha
    rapporter(args.pr, sha, False, titre, lignes, essais)
    print("[resolver] PR #%s : %s" % (args.pr, titre), flush=True)
    return 1


# ------------------------------------------------------------------ selftest
def _selftest_validation(echecs: list[str]) -> None:
    racine = tempfile.mkdtemp(prefix="hermes-validation-")
    try:
        ok, _ = valider(racine, "true", timeout=30)
        if not ok:
            echecs.append("validation verte jugée rouge")
        ok2, _ = valider(racine, "false", timeout=30)
        if ok2:
            echecs.append("validation rouge jugée verte")
        ok3, sortie = valider(racine, "sleep 5", timeout=1)
        if ok3 or "timeout" not in sortie:
            echecs.append("timeout de validation non détecté")
        ok4, _ = valider(racine, "", timeout=5)
        if not ok4:
            echecs.append("validation vide non traitée comme neutre")
        _selftest_extrait(echecs)
    finally:
        shutil.rmtree(racine, ignore_errors=True)


def _selftest_extrait(echecs: list[str]) -> None:
    """L'extrait de validation reste lisible : lignes tronquées, fin conservée."""
    longue = "x" * 5000
    extrait = extraire_sortie("\n".join([longue, "ERROR: la vraie cause"]), ligne=200)
    if "ERROR: la vraie cause" not in extrait:
        echecs.append("extrait : la fin de la sortie (l'erreur) a disparu")
    if "x" * 201 in extrait:
        echecs.append("extrait : ligne non tronquée")
    if len(extrait) > 3000:
        echecs.append("extrait : plus long que la limite de 3 000 caractères")
    if extraire_sortie("court") != "court":
        echecs.append("extrait : sortie courte modifiée")


def _selftest_rapport(echecs: list[str]) -> None:
    corps = corps_rapport("Titre", ["ligne"], 2, True)
    for marque in ("<!-- hermes-resolver:attempts=2 -->", "<!-- hermes-resolver:at=", MARQUEUR):
        if marque not in corps:
            echecs.append("marqueur absent du rapport : %s" % marque)
    if "✅" not in corps:
        echecs.append("rapport de succès sans marque de succès")
    echec = corps_rapport("Titre", ["ligne"], 1, False)
    if "❌" not in echec or "pas un état terminal" not in echec:
        echecs.append("rapport d'échec sans mention de re-dispatch")


def _selftest_toolchain(echecs: list[str]) -> None:
    """La validation du dépôt doit être la toolchain Python, jamais npm/tsc/gradle."""
    if "ci-local.sh" not in VALIDATION_DEFAUT:
        echecs.append("validation par défaut != scripts/ci-local.sh")
    for interdit in ("npm", "tsc", "gradle"):
        if interdit in VALIDATION_DEFAUT:
            echecs.append("toolchain interdite dans la validation : %s" % interdit)


def selftest() -> int:
    """Contrôles hors réseau et hors secret (dont un VRAI rebase git)."""
    echecs: list[str] = []
    for controle in (_selftest_validation, _selftest_rapport, _selftest_toolchain):
        controle(echecs)
    if rebase.selftest() != 0:
        echecs.append("rebase.py --selftest en échec")
    if resolve.selftest() != 0:
        echecs.append("resolve.py --selftest en échec")
    for echec in echecs:
        print("  KO  %s" % echec)
    if echecs:
        print("resolver.py --selftest : %d échec(s)" % len(echecs))
        return 1
    print("resolver.py --selftest : OK (validation, rapport, rebase réel ; sans réseau ni secret)")
    return 0


def analyser(argv=None):
    """Arguments de ligne de commande (variables d'environnement en repli)."""
    analyseur = argparse.ArgumentParser(description="Résolveur Hermes de conflits (Jauge)")
    analyseur.add_argument("--selftest", action="store_true", help="contrôles hors réseau")
    analyseur.add_argument("--pr", type=int, default=int(os.environ.get("PR_NUMBER", "0") or 0))
    analyseur.add_argument("--head-ref", default=os.environ.get("HEAD_REF", ""))
    analyseur.add_argument("--base-ref", default=os.environ.get("BASE_REF", "main"))
    analyseur.add_argument("--head-sha", default=os.environ.get("HEAD_SHA", ""))
    analyseur.add_argument(
        "--validation", default=os.environ.get("VALIDATION_CMD", VALIDATION_DEFAUT)
    )
    analyseur.add_argument(
        "--validation-timeout", type=int, default=int(os.environ.get("VALIDATION_TIMEOUT", "480"))
    )
    return analyseur.parse_args(argv)


def main(argv=None) -> int:
    args = analyser(argv)
    if args.selftest:
        return selftest()
    if not (args.pr and args.head_ref):
        print("usage : resolver.py --pr N --head-ref BRANCHE [--base-ref main] | --selftest")
        return 2
    return executer(args)


if __name__ == "__main__":
    sys.exit(main())
