"""Ports locaux du module de bascule : client de modèle, juge, dépôt de paires.

Règle du dépôt : un module code contre le socle (`models`, `engine`, `common`,
`store`), pas contre l'implémentation d'un autre module. Les protocoles
déclarés ici sont donc **structurellement compatibles** avec
`jauge.replay.client.ModeleClient` et `jauge.evaluation.judge.JugeClient` : leurs
doubles hors ligne (`FakeModeleClient`, `FakeJugeClient`) les satisfont sans
aucun adaptateur — ce que vérifient les tests. Le câblage des adaptateurs réels
(Bedrock) et du plan de données se fait dans la PR d'intégration.

ENF-CONF-01 : `DemandeShadow` et `PaireShadow` portent des textes ; ils ne
sortent du plan de données que vers le plan de données lui-même. Aucun de ces
objets n'est rendu par l'API du plan de contrôle ni par un outil d'agent.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from jauge.models.campaign import PaireRejoueeDataPlane, ParametresRejeu
from jauge.models.pricing import ServiceTier


@dataclass(frozen=True)
class DemandeShadow:
    """Invocation du candidat sur une requête du trafic réel (plan de données).

    Même forme que `jauge.replay.client.DemandeRejeu` : `FakeModeleClient` et
    `BedrockModeleClient` la consomment telle quelle (typage structurel), ce que
    vérifie `tests/rollout/test_shadow.py`.
    """

    requete_id: str
    modele_id: str
    texte_requete: str
    parametres: ParametresRejeu
    niveau: ServiceTier
    consigne_systeme: str = ""


class ReponseModele(Protocol):
    """Réponse d'un modèle : texte, jetons facturés et latence (EF-REJ-04)."""

    modele_id: str
    texte: str
    jetons_entree: int
    jetons_sortie: int
    jetons_cache_ecriture: int
    jetons_cache_lecture: int
    latence_ms: int


class ClientModele(Protocol):
    """Contrat local d'invocation d'un modèle.

    `jauge.replay.client.ModeleClient` (et son double `FakeModeleClient`) le
    satisfont structurellement : aucun adaptateur n'est nécessaire.
    """

    def invoquer(self, demande: DemandeShadow) -> ReponseModele: ...


@dataclass(frozen=True)
class PaireShadow:
    """Paire soumise au juge : réponse réelle contre réponse candidate.

    Même forme que `jauge.evaluation.judge.DemandeNotation` : `FakeJugeClient`
    et `BedrockJugeClient` la consomment telle quelle (typage structurel).
    """

    requete_id: str
    requete: str
    reponse_reference: str
    reponse_candidate: str
    type_tache: str = "general"


class VerdictEquivalence(Protocol):
    """Verdict du juge : un booléen et une explication, aucun nombre (ENF-IA-01)."""

    equivalent: bool
    explication: str


class JugeEquivalence(Protocol):
    """Contrat local du juge d'équivalence.

    `jauge.evaluation.judge.JugeClient` (et son double `FakeJugeClient`) le
    satisfont structurellement.
    """

    modele_id: str

    def noter_lot(self, demandes: Sequence[PaireShadow]) -> list[VerdictEquivalence]: ...


class DepotPaires(Protocol):
    """Dépôt des paires du shadow, côté **plan de données** (ENF-CONF-01).

    L'adaptateur d'intégration y enregistre les paires du shadow ; les textes
    ne remontent jamais vers le plan de contrôle.
    """

    def enregistrer_paires(self, run_id: str, paires: Sequence[PaireRejoueeDataPlane]) -> None: ...

    def paires(self, run_id: str) -> list[PaireRejoueeDataPlane]: ...


class DepotPairesMemoire:
    """Dépôt de paires en mémoire : hors ligne, pour les tests et la démo."""

    def __init__(self) -> None:
        self._paires: dict[str, list[PaireRejoueeDataPlane]] = {}

    def enregistrer_paires(self, run_id: str, paires: Sequence[PaireRejoueeDataPlane]) -> None:
        """Mémorise les paires d'un run de shadow."""
        self._paires.setdefault(run_id, []).extend(paires)

    def paires(self, run_id: str) -> list[PaireRejoueeDataPlane]:
        """Paires d'un run de shadow."""
        return list(self._paires.get(run_id, []))
