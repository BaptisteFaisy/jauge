"""Juges fictifs paramétrables pour la suite de calibration (aucun réseau).

La preuve la plus importante du lot de calibration est que la suite **détecte**
un juge biaisé. Ces doubles simulent des biais précis, de façon déterministe et
hors ligne :

- `juge_parfait` : rend la vérité du jeu — c'est la référence, tout doit passer ;
- `juge_ancre_position` : s'ancre sur la première réponse lue d'une paire, donc
  change d'avis quand l'ordre change ;
- `juge_verbeux` : accepte la réponse candidate dès qu'elle est nettement plus
  longue que la référence, même quand elle est fausse ;
- `juge_hostile_format` : refuse une mise en forme enrichie à contenu identique ;
- `juge_laxiste` : valide tout, y compris les pièges dorés ;
- `juge_severe` : refuse tout.

Chaque double est un `FakeJugeClient` du socle (`jauge.evaluation.judge`) : on
ne réécrit pas un second contrat de juge (zéro duplication). Les doubles
« biaisés » connaissent la vérité du jeu et s'en écartent **uniquement** par le
biais simulé : c'est ce qui isole le biais dans les mesures.
"""

from __future__ import annotations

from collections.abc import Sequence

from jauge.evaluation.jeux_calibration import ItemCalibration
from jauge.evaluation.judge import FakeJugeClient, VerdictJuge

#: Marqueurs d'une mise en forme enrichie (gras Markdown, liste à puces).
MARQUEURS_FORMAT = ("**", "- ")

#: Rapport de longueur à partir duquel le juge verbeux se laisse convaincre.
FACTEUR_LONGUEUR = 1.2


def gold_par_identifiant(jeu: Sequence[ItemCalibration]) -> dict[str, bool]:
    """Vérité du jeu, indexée par identifiant d'item."""
    return {item.identifiant: item.gold_equivalent for item in jeu}


def juge_parfait(jeu: Sequence[ItemCalibration], modele_id: str = "juge-parfait") -> FakeJugeClient:
    """Juge qui rend toujours la vérité du jeu (référence de la suite)."""
    return FakeJugeClient(
        modele_id=modele_id,
        verdicts={
            identifiant: VerdictJuge(gold, "vérité du jeu")
            for identifiant, gold in gold_par_identifiant(jeu).items()
        },
    )


def juge_ancre_position(modele_id: str = "juge-ancre-position") -> FakeJugeClient:
    """Juge ancré sur la position : il retient la première réponse lue d'une paire.

    Biais simulé : pour une paire donnée, la première réponse placée en
    référence devient la référence de fait ; le candidat n'est accepté que s'il
    est cette réponse-là. Deux ordres d'une même paire reçoivent donc deux
    verdicts opposés — la signature du biais de position.
    """
    ancres: dict[tuple[str, str], str] = {}

    def strategie(demande) -> VerdictJuge:
        """Rend le verdict d'un juge ancré sur la première réponse lue."""
        cle = tuple(sorted((demande.reponse_reference, demande.reponse_candidate)))
        if cle not in ancres:
            ancres[cle] = demande.reponse_reference
        return VerdictJuge(
            demande.reponse_candidate == ancres[cle], "ancré sur la première réponse lue"
        )

    return FakeJugeClient(modele_id=modele_id, strategie=strategie)


def juge_verbeux(
    jeu: Sequence[ItemCalibration],
    modele_id: str = "juge-verbeux",
    facteur: float = FACTEUR_LONGUEUR,
) -> FakeJugeClient:
    """Juge qui préfère la réponse longue : il valide dès qu'elle est plus longue.

    Ailleurs, il rend la vérité : seul le biais de verbosité est injecté.
    """
    verite = gold_par_identifiant(jeu)

    def strategie(demande) -> VerdictJuge:
        """Rend le verdict d'un juge convaincu par la longueur."""
        if len(demande.reponse_candidate) > len(demande.reponse_reference) * facteur:
            return VerdictJuge(True, "réponse longue, donc jugée complète")
        return VerdictJuge(verite[demande.requete_id], "vérité du jeu")

    return FakeJugeClient(modele_id=modele_id, strategie=strategie)


def juge_hostile_format(
    jeu: Sequence[ItemCalibration], modele_id: str = "juge-hostile-format"
) -> FakeJugeClient:
    """Juge qui refuse une mise en forme enrichie, à contenu identique.

    Ailleurs, il rend la vérité : seul le biais de format est injecté.
    """
    verite = gold_par_identifiant(jeu)

    def strategie(demande) -> VerdictJuge:
        """Rend le verdict d'un juge qui rejette le formatage enrichi."""
        candidate = demande.reponse_candidate
        if any(marqueur in candidate for marqueur in MARQUEURS_FORMAT):
            return VerdictJuge(False, "mise en forme enrichie refusée")
        return VerdictJuge(verite[demande.requete_id], "vérité du jeu")

    return FakeJugeClient(modele_id=modele_id, strategie=strategie)


def juge_laxiste(modele_id: str = "juge-laxiste") -> FakeJugeClient:
    """Juge qui valide tout : le faux-accept doit crever le plafond de rejet."""
    return FakeJugeClient(
        modele_id=modele_id, strategie=lambda demande: VerdictJuge(True, "toujours équivalent")
    )


def juge_severe(modele_id: str = "juge-severe") -> FakeJugeClient:
    """Juge qui refuse tout : le faux-rejet doit crever le plafond."""
    return FakeJugeClient(
        modele_id=modele_id, strategie=lambda demande: VerdictJuge(False, "jamais équivalent")
    )
