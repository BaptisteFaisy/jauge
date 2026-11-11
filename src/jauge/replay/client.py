"""Adaptateurs d'invocation de modèles pour le rejeu.

Exigences visées :
- EF-REJ-02 : rejouer une requête avec **exactement les mêmes paramètres** que
  l'appel d'origine (consigne système, température, `top_p`, limite de jetons,
  séquences d'arrêt) — `ParametresRejeu` du socle ;
- EF-REJ-03 : exécuter le rejeu dans le compte du client, au **niveau Flex** ou
  par lots — le niveau de service voyage dans la demande ;
- EF-REJ-04 : rendre les jetons consommés et la latence, sans quoi le coût et
  les percentiles ne sont pas calculables.

Trois objets :
- `ModeleClient` : le protocole (une seule méthode, `invoquer`) ;
- `BedrockModeleClient` : l'adaptateur réel (boto3, `bedrock-runtime`, opération
  `Converse`). **Écrit mais jamais appelé par les tests** (aucun réseau) ;
- `FakeModeleClient` : l'adaptateur fictif des tests, paramétrable en jetons
  générés, latence et erreurs.

ENF-CONF-01 : le texte de la requête et celui de la réponse ne sortent d'ici que
vers l'appelant du plan de données (`executor`), jamais vers le plan de contrôle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from jauge.common.errors import ServiceIndisponibleError
from jauge.common.ids import deterministic
from jauge.models.campaign import ParametresRejeu
from jauge.models.pricing import ServiceTier

# Estimation de jetons d'un texte, utilisée par l'adaptateur fictif seulement.
JETONS_PAR_CARACTERE = 0.25


@dataclass(frozen=True)
class DemandeRejeu:
    """Une invocation à rejouer, paramètres compris (§13.2)."""

    requete_id: str
    modele_id: str
    texte_requete: str
    parametres: ParametresRejeu
    niveau: ServiceTier = ServiceTier.FLEX
    consigne_systeme: str = ""


@dataclass(frozen=True)
class ResultatInvocation:
    """Réponse d'un modèle et ce qu'elle a coûté à produire (EF-REJ-04)."""

    modele_id: str
    texte: str
    jetons_entree: int = 0
    jetons_sortie: int = 0
    jetons_cache_ecriture: int = 0
    jetons_cache_lecture: int = 0
    latence_ms: int = 0
    niveau: ServiceTier = ServiceTier.FLEX


class ModeleClient(Protocol):
    """Contrat minimal d'un client de modèle : une invocation, un résultat."""

    def invoquer(self, demande: DemandeRejeu) -> ResultatInvocation: ...


class BedrockModeleClient:
    """Adaptateur réel : boto3 `bedrock-runtime`, opération `Converse`.

    Le client boto3 n'est créé qu'au premier appel (`_obtenir_client`), ce qui
    permet d'instancier la classe sans identifiants AWS et de l'injecter en test
    d'intégration. Aucun test de la suite ne l'appelle : pas de réseau.

    Hypothèse d'API (à confirmer à l'intégration, cf. corps de PR) : le niveau
    de service voyage dans le champ `serviceTier` de `Converse` ; il est omis
    pour le niveau Standard, qui est le comportement par défaut.
    """

    def __init__(self, region: str, *, client: Any | None = None) -> None:
        self.region = region
        self._client = client

    def _obtenir_client(self) -> Any:
        """Crée (une fois) le client boto3 `bedrock-runtime`."""
        if self._client is None:
            import boto3

            self._client = boto3.client("bedrock-runtime", region_name=self.region)
        return self._client

    def _requete_converse(self, demande: DemandeRejeu) -> dict[str, Any]:
        """Construit les arguments de `Converse` à partir des paramètres d'origine."""
        parametres = demande.parametres
        inference: dict[str, Any] = {
            "temperature": parametres.temperature,
            "maxTokens": parametres.max_tokens,
            "topP": parametres.top_p,
        }
        if parametres.stop_sequences:
            inference["stopSequences"] = list(parametres.stop_sequences)
        corps: dict[str, Any] = {
            "modelId": demande.modele_id,
            "messages": [{"role": "user", "content": [{"text": demande.texte_requete}]}],
            "inferenceConfig": inference,
        }
        if demande.consigne_systeme:
            corps["system"] = [{"text": demande.consigne_systeme}]
        if demande.niveau != ServiceTier.STANDARD:
            corps["serviceTier"] = {"type": demande.niveau.value}
        return corps

    def invoquer(self, demande: DemandeRejeu) -> ResultatInvocation:
        """Rejoue une demande sur Bedrock et rend texte, usage et latence."""
        import time

        debut = time.perf_counter()
        try:
            reponse = self._obtenir_client().converse(**self._requete_converse(demande))
        except Exception as erreur:  # toute panne boto3 devient une erreur métier
            raise ServiceIndisponibleError(
                f"Appel Bedrock en échec pour {demande.modele_id} : {erreur}",
                modele=demande.modele_id,
                region=self.region,
            ) from erreur
        latence_ms = int(round((time.perf_counter() - debut) * 1000))
        return self._vers_resultat(demande, reponse, latence_ms)

    def _vers_resultat(
        self, demande: DemandeRejeu, reponse: dict[str, Any], latence_ms: int
    ) -> ResultatInvocation:
        """Traduit une réponse `Converse` en `ResultatInvocation`."""
        contenu = reponse.get("output", {}).get("message", {}).get("content", [])
        texte = "".join(bloc.get("text", "") for bloc in contenu if isinstance(bloc, dict))
        usage = reponse.get("usage", {})
        return ResultatInvocation(
            modele_id=demande.modele_id,
            texte=texte,
            jetons_entree=int(usage.get("inputTokens", 0)),
            jetons_sortie=int(usage.get("outputTokens", 0)),
            jetons_cache_ecriture=int(usage.get("cacheWriteInputTokens", 0)),
            jetons_cache_lecture=int(usage.get("cacheReadInputTokens", 0)),
            latence_ms=latence_ms,
            niveau=demande.niveau,
        )


class FakeModeleClient:
    """Adaptateur fictif : aucun réseau, comportement entièrement paramétrable.

    - `jetons_sortie` / `jetons_entree_fixe` : ce que le modèle « génère » ;
    - `latence_ms` : latence simulée, constante (percentiles déterministes) ;
    - `modeles_en_erreur` : modèles qui échouent (`ServiceIndisponibleError`) ;
    - `variabilite` : à partir de la deuxième invocation d'un même couple
      (modèle, texte), la réponse diffère — c'est la variabilité naturelle que
      mesure l'auto-accord du modèle actuel (EF-EVA-04).

    La réponse est déterministe pour un couple (modèle, texte) donné : deux
    exécutions du même modèle rendent le même texte, donc un auto-accord de
    100 % par défaut.
    """

    def __init__(
        self,
        *,
        jetons_sortie: int = 40,
        jetons_entree_fixe: int | None = None,
        latence_ms: int = 250,
        modeles_en_erreur: set[str] | None = None,
        variabilite: bool = False,
        textes: dict[tuple[str, str], str] | None = None,
    ) -> None:
        self.jetons_sortie = jetons_sortie
        self.jetons_entree_fixe = jetons_entree_fixe
        self.latence_ms = latence_ms
        self.modeles_en_erreur = set(modeles_en_erreur or ())
        self.variabilite = variabilite
        self.textes = dict(textes or {})
        # Journal des appels : sert à vérifier les paramètres rejoués (EF-REJ-02).
        self.appels: list[DemandeRejeu] = []
        self._occurrences: dict[tuple[str, str], int] = {}

    def invoquer(self, demande: DemandeRejeu) -> ResultatInvocation:
        """Rend une réponse fictive déterministe, ou échoue si configuré."""
        self.appels.append(demande)
        if demande.modele_id in self.modeles_en_erreur:
            raise ServiceIndisponibleError(
                f"Modèle {demande.modele_id} en erreur (adaptateur fictif).",
                modele=demande.modele_id,
            )
        occurrence = self._occurrences.get((demande.modele_id, demande.texte_requete), 0) + 1
        self._occurrences[(demande.modele_id, demande.texte_requete)] = occurrence
        return ResultatInvocation(
            modele_id=demande.modele_id,
            texte=self._texte(demande, occurrence),
            jetons_entree=self._jetons_entree(demande),
            jetons_sortie=self.jetons_sortie,
            latence_ms=self.latence_ms,
            niveau=demande.niveau,
        )

    def _jetons_entree(self, demande: DemandeRejeu) -> int:
        if self.jetons_entree_fixe is not None:
            return self.jetons_entree_fixe
        return max(1, int(len(demande.texte_requete) * JETONS_PAR_CARACTERE))

    def _texte(self, demande: DemandeRejeu, occurrence: int) -> str:
        """Texte de réponse : imposé, sinon dérivé du couple (modèle, texte)."""
        impose = self.textes.get((demande.modele_id, demande.texte_requete))
        if impose is not None:
            return impose
        empreinte = deterministic(demande.modele_id, demande.texte_requete)
        base = f"reponse-{demande.modele_id}-{empreinte}"
        if self.variabilite and occurrence > 1:
            return f"{base}-variante{occurrence}"
        return base

    def demandes_pour(self, requete_id: str) -> list[DemandeRejeu]:
        """Appels reçus pour une requête, dans l'ordre (utile aux tests)."""
        return [appel for appel in self.appels if appel.requete_id == requete_id]
