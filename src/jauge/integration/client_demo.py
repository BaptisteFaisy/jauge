"""Adaptateurs de démonstration : client de modèle et modèle juge, hors ligne.

Ces deux objets remplacent les dépendances AWS **uniquement en mode
démonstration** : aucun appel réseau, aucune donnée réelle, un comportement
déterministe (même graine ⇒ même réponse).

Le client de modèle reproduit les défaillances attendues d'un modèle plus
petit, sans « écrire » un scénario figé :

- il **tronce** parfois sa réponse (réponse vide) : le contrôle déterministe
  de longueur échoue alors, et le candidat est écarté (EF-EVA-02) ;
- il se trompe d'étiquette sur une part des requêtes de **tâche rédigée** : le
  juge la détecte comme une perte de service (EF-EVA-01).

Le juge compare le **service rendu** (l'étiquette produite), pas la
formulation : c'est la métrique « équivalence fonctionnelle » du §13.3.

Exigences visées : EF-EVA-01, EF-EVA-02, EF-REJ-04, ENF-IA-01.
"""

from __future__ import annotations

import json

from jauge.common.ids import deterministic
from jauge.evaluation.judge import DemandeNotation, FakeJugeClient, VerdictJuge
from jauge.integration.plan_de_donnees import PlanDeDonnees
from jauge.integration.socle import famille_de, type_tache_de
from jauge.replay.client import DemandeRejeu, ResultatInvocation

#: Étiquettes produites par le client de démonstration (réponses de triage).
ETIQUETTES = ("billing", "incident", "renouvellement", "usage", "qualite")

#: Tâches écrites : le service rendu porte plus de substance qu'une étiquette.
TACHES_ECRITES = frozenset({"general", "redaction"})

#: Part des requêtes où le modèle petit tronque sa réponse.
TAUX_TRONCATURE = 0.05

#: Part des requêtes où le modèle perd l'étiquette produite.
TAUX_ERREUR_ETIQUETTE = {"petit": 0.10, "moyen": 0.08}

#: Jetons estimés par caractère (estimation grossière, hors ligne).
JETONS_PAR_CARACTERE = 0.25


def _tirage(*parties: object) -> float:
    """Valeur pseudo-aléatoire déterministe dans [0, 1[."""
    return int(deterministic(*parties), 16) / float(1 << 64)


def _etiquette(requete_id: str, decalee: bool = False) -> str:
    """Étiquette produite pour une requête (déterministe)."""
    index = int(deterministic("etiquette", requete_id), 16) % len(ETIQUETTES)
    if decalee:
        index = (index + 1) % len(ETIQUETTES)
    return ETIQUETTES[index]


class ClientModeleDemo:
    """Client de modèle hors ligne : réponses déterministes, aucun réseau."""

    def __init__(self, plan: PlanDeDonnees | None = None) -> None:
        self._plan = plan
        self.appels: list[DemandeRejeu] = []

    def invoquer(self, demande: DemandeRejeu) -> ResultatInvocation:
        """Rend une réponse déterministe pour le couple (modèle, requête)."""
        self.appels.append(demande)
        famille = famille_de(demande.modele_id)
        type_tache = self._type_tache(demande.requete_id)
        tirage = _tirage(demande.modele_id, demande.requete_id)
        texte = self._texte(demande, famille, type_tache, tirage)
        return ResultatInvocation(
            modele_id=demande.modele_id,
            texte=texte,
            jetons_entree=self._jetons_entree(demande),
            jetons_sortie=max(0, int(len(texte) * JETONS_PAR_CARACTERE)),
            latence_ms=200 + int(_tirage("latence", demande.modele_id, demande.requete_id) * 400),
            niveau=demande.niveau,
        )

    def _type_tache(self, requete_id: str) -> str:
        """Type de tâche de la requête, d'après le trafic observé."""
        if self._plan is None:
            return "general"
        return type_tache_de(self._plan.type_de(requete_id))

    def _texte(self, demande: DemandeRejeu, famille: str, type_tache: str, tirage: float) -> str:
        """Réponse du modèle : troncature, étiquette erronée, ou réponse nominale."""
        if famille == "petit" and tirage < TAUX_TRONCATURE:
            return ""
        taux_erreur = TAUX_ERREUR_ETIQUETTE.get(famille, 0.0)
        etiquette_erronee = bool(taux_erreur) and (
            famille == "petit" or type_tache in TACHES_ECRITES
        )
        if etiquette_erronee and tirage < taux_erreur:
            etiquette = _etiquette(demande.requete_id, decalee=True)
        else:
            etiquette = _etiquette(demande.requete_id)
        confiance = round(0.70 + _tirage("conf", demande.requete_id) * 0.29, 2)
        return json.dumps({"etiquette": etiquette, "confiance": confiance}, ensure_ascii=True)

    def _jetons_entree(self, demande: DemandeRejeu) -> int:
        """Jetons d'entrée estimés sur le texte transmis."""
        return max(1, int(len(demande.texte_requete) * JETONS_PAR_CARACTERE))


def verdict_demonstration(demande: DemandeNotation) -> VerdictJuge:
    """Verdict du juge de démonstration : le service rendu est-il le même ?

    L'équivalence est **fonctionnelle** : deux réponses qui rendent la même
    étiquette sont équivalentes, même si la formulation diffère. Une réponse
    tronquée ne rend aucun service : elle n'est pas équivalente.
    """
    reference = _etiquette_de(demande.reponse_reference)
    candidate = _etiquette_de(demande.reponse_candidate)
    if not candidate:
        return VerdictJuge(equivalent=False, explication="réponse candidate vide")
    if reference and candidate == reference:
        return VerdictJuge(equivalent=True, explication="même étiquette produite")
    return VerdictJuge(equivalent=False, explication="étiquette différente")


def _etiquette_de(texte: str) -> str:
    """Étiquette d'une réponse JSON (ou son texte normalisé)."""
    try:
        charge = json.loads(texte)
    except (json.JSONDecodeError, TypeError):
        return texte.strip().lower()
    if isinstance(charge, dict):
        return str(charge.get("etiquette", "")).strip().lower()
    return texte.strip().lower()


def juge_demonstration(modele_id: str = "juge-demo") -> FakeJugeClient:
    """Modèle juge de démonstration, branché sur la stratégie ci-dessus."""
    return FakeJugeClient(modele_id=modele_id, strategie=verdict_demonstration)
