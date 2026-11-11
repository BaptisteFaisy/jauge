"""Règles de passerelle AgentCore : faire servir un autre modèle que celui demandé.

Répond aux exigences :
- EF-BAS-01 : créer une règle de passerelle qui fait servir le modèle cible à la
  place du modèle demandé (forme de la commande : Annexe C).
- EF-BAS-02 : répartir le trafic par poids pour les paliers intermédiaires
  (10 %, 50 %).
- EF-BAS-03 : retirer la règle d'un seul appel, ce qui remet tout le trafic sur
  le modèle actuel (§11.5 : le retour arrière se fait en quelques secondes).
- ENF-CONF-01 : une règle ne transporte que des identifiants de modèles et un
  nom de cible, jamais un texte de requête.

Trois pièces :
- `ReglePasserelle` : état d'une règle tel que le plan de contrôle le manipule.
- `PasserelleClient` : le protocole attendu par `controller.py`.
- `AgentCoreGatewayClient` : l'adaptateur réel (boto3, service
  `bedrock-agentcore-control`), écrit mais **jamais appelé par les tests**.
- `FakePasserelleClient` : état en mémoire + journal des appels, pour les tests
  et la démo locale (aucun réseau).

Hypothèse ouverte (§23 Q4) : la syntaxe exacte de la répartition pondérée n'est
pas encore confirmée par la documentation AgentCore. Elle est isolée dans
`construire_action_ponderee` / `construire_action_mise_a_jour_poids` pour qu'un
seul endroit change le jour où la référence tranche.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from jauge.common.errors import IntrouvableError, ServiceIndisponibleError, ValidationError

# Priorité des règles créées par Jauge : une règle plus prioritaire (nombre plus
# petit) posée par le client garde la main. Valeur de l'Annexe C.
PRIORITE_PAR_DEFAUT = 100

# Nom de la cible Bedrock de la passerelle. La démo n'en a qu'une ; un client qui
# en a plusieurs la fournit explicitement.
NOM_CIBLE_PAR_DEFAUT = "bedrock-target"

# Libellé du service AWS qui porte les règles (adaptateur réel uniquement).
SERVICE_AWS = "bedrock-agentcore-control"


@dataclass(frozen=True)
class ReglePasserelle:
    """Règle de passerelle : conditions sur le modèle demandé + action de route."""

    id: str
    passerelle_id: str
    modele_demande: str
    pourcentage_cible: int = 100
    modele_cible: str | None = None
    nom_cible: str = NOM_CIBLE_PAR_DEFAUT
    priorite: int = PRIORITE_PAR_DEFAUT
    description: str = ""
    active: bool = True

    @property
    def ponderee(self) -> bool:
        """Vrai si la règle partage le trafic entre deux modèles."""
        return self.pourcentage_cible < 100


@dataclass(frozen=True)
class AppelPasserelle:
    """Entrée du journal d'appels d'une passerelle (audit EF-ADM-02)."""

    operation: str
    regle_id: str | None
    details: dict[str, object] = field(default_factory=dict)


def verifier_pourcentage(pourcentage: int) -> int:
    """Contrôle qu'un palier est un pourcentage entier entre 1 et 100."""
    if not isinstance(pourcentage, int) or not 1 <= pourcentage <= 100:
        raise ValidationError(
            "Le pourcentage du palier doit être un entier entre 1 et 100.",
            pourcentage=pourcentage,
        )
    return pourcentage


# --- Forme des messages envoyés au service AgentCore (Annexe C) -------------


def construire_conditions(modele_demande: str) -> list[dict[str, object]]:
    """Conditions de la règle : les requêtes qui demandent ce modèle."""
    if not modele_demande:
        raise ValidationError("Le modèle demandé est obligatoire dans une règle de passerelle.")
    return [{"matchModels": {"anyOf": [{"value": modele_demande}]}}]


def construire_action_statique(nom_cible: str, modele_cible: str) -> list[dict[str, object]]:
    """Action d'une règle à 100 % : route fixe vers le modèle cible (Annexe C)."""
    if not modele_cible:
        raise ValidationError("Le modèle cible est obligatoire dans une règle statique.")
    return [
        {
            "routeToTarget": {
                "staticRoute": {"targetName": nom_cible, "model": modele_cible},
            }
        }
    ]


def construire_action_ponderee(
    nom_cible: str, modele_demande: str, modele_cible: str, pourcentage_cible: int
) -> list[dict[str, object]]:
    """Action d'une règle pondérée : `pourcentage_cible` du trafic vers la cible.

    HYPOTHÈSE (§23 Q4) : une route `weightedRoute` portant deux entrées
    `{targetName, model, weight}` sur la même cible, les poids sommant à 100.
    Si la référence impose une cible par modèle, seul ce constructeur change.
    """
    verifier_pourcentage(pourcentage_cible)
    if pourcentage_cible >= 100:
        return construire_action_statique(nom_cible, modele_cible)
    cibles = [
        {"targetName": nom_cible, "model": modele_cible, "weight": pourcentage_cible},
        {"targetName": nom_cible, "model": modele_demande, "weight": 100 - pourcentage_cible},
    ]
    return [{"routeToTarget": {"weightedRoute": {"targets": cibles}}}]


def construire_action_mise_a_jour_poids(
    nom_cible: str, modele_demande: str, modele_cible: str, pourcentage_cible: int
) -> list[dict[str, object]]:
    """Action d'un changement de palier : mêmes poids, règle déjà existante."""
    return construire_action_ponderee(nom_cible, modele_demande, modele_cible, pourcentage_cible)


# --- Protocole attendu par le contrôleur -----------------------------------


class PasserelleClient(Protocol):
    """Contrat minimal d'une passerelle de modèles (implémenté par AgentCore)."""

    def creer_regle_statique(
        self, passerelle_id: str, modele_demande: str, modele_cible: str, description: str = ""
    ) -> ReglePasserelle: ...

    def creer_regle_ponderee(
        self,
        passerelle_id: str,
        modele_demande: str,
        modele_cible: str,
        pourcentage_cible: int,
        description: str = "",
    ) -> ReglePasserelle: ...

    def mettre_a_jour_poids(self, regle_id: str, pourcentage_cible: int) -> ReglePasserelle: ...

    def supprimer_regle(self, regle_id: str) -> None: ...

    def lire_regle(self, regle_id: str) -> ReglePasserelle | None: ...

    def lister_regles(self, passerelle_id: str) -> list[ReglePasserelle]: ...


# --- Implémentation en mémoire (tests, démo) -------------------------------


class FakePasserelleClient:
    """Passerelle fictive : état en mémoire et journal des appels.

    Elle reproduit le comportement observable de la passerelle (règle créée,
    pondérée, mise à jour, supprimée) sans aucun appel réseau.
    """

    def __init__(self, nom_cible: str = NOM_CIBLE_PAR_DEFAUT) -> None:
        self.nom_cible = nom_cible
        self.regles: dict[str, ReglePasserelle] = {}
        self.journal: list[AppelPasserelle] = []
        self._compteur = 0

    def _nouvel_id(self) -> str:
        self._compteur += 1
        return f"regle-{self._compteur:03d}"

    def creer_regle_statique(
        self, passerelle_id: str, modele_demande: str, modele_cible: str, description: str = ""
    ) -> ReglePasserelle:
        regle = ReglePasserelle(
            id=self._nouvel_id(),
            passerelle_id=passerelle_id,
            modele_demande=modele_demande,
            pourcentage_cible=100,
            modele_cible=modele_cible,
            nom_cible=self.nom_cible,
            description=description,
        )
        self.regles[regle.id] = regle
        self.journal.append(AppelPasserelle("creer_regle_statique", regle.id, {"pourcentage": 100}))
        return regle

    def creer_regle_ponderee(
        self,
        passerelle_id: str,
        modele_demande: str,
        modele_cible: str,
        pourcentage_cible: int,
        description: str = "",
    ) -> ReglePasserelle:
        verifier_pourcentage(pourcentage_cible)
        regle = ReglePasserelle(
            id=self._nouvel_id(),
            passerelle_id=passerelle_id,
            modele_demande=modele_demande,
            pourcentage_cible=pourcentage_cible,
            modele_cible=modele_cible,
            nom_cible=self.nom_cible,
            description=description,
        )
        self.regles[regle.id] = regle
        self.journal.append(
            AppelPasserelle("creer_regle_ponderee", regle.id, {"pourcentage": pourcentage_cible})
        )
        return regle

    def mettre_a_jour_poids(self, regle_id: str, pourcentage_cible: int) -> ReglePasserelle:
        verifier_pourcentage(pourcentage_cible)
        regle = self.lire_regle(regle_id)
        if regle is None:
            raise IntrouvableError("Règle de passerelle inconnue.", regle_id=regle_id)
        maj = ReglePasserelle(
            id=regle.id,
            passerelle_id=regle.passerelle_id,
            modele_demande=regle.modele_demande,
            pourcentage_cible=pourcentage_cible,
            modele_cible=regle.modele_cible,
            nom_cible=regle.nom_cible,
            priorite=regle.priorite,
            description=regle.description,
            active=regle.active,
        )
        self.regles[regle_id] = maj
        self.journal.append(
            AppelPasserelle("mettre_a_jour_poids", regle_id, {"pourcentage": pourcentage_cible})
        )
        return maj

    def supprimer_regle(self, regle_id: str) -> None:
        if regle_id not in self.regles:
            raise IntrouvableError("Règle de passerelle inconnue.", regle_id=regle_id)
        del self.regles[regle_id]
        self.journal.append(AppelPasserelle("supprimer_regle", regle_id))

    def lire_regle(self, regle_id: str) -> ReglePasserelle | None:
        return self.regles.get(regle_id)

    def lister_regles(self, passerelle_id: str) -> list[ReglePasserelle]:
        return [r for r in self.regles.values() if r.passerelle_id == passerelle_id]

    def operations(self) -> list[str]:
        """Suite des opérations, dans l'ordre : sert aux assertions des tests."""
        return [appel.operation for appel in self.journal]


# --- Implémentation réelle (jamais appelée par les tests) ------------------


class AgentCoreGatewayClient:
    """Adaptateur réel des règles de passerelle AgentCore (boto3).

    Le client boto3 est créé paresseusement et injectable (`client_factory`),
    ce qui permet de vérifier la forme des appels sans réseau. En production,
    aucune fabrique n'est fournie : le client standard est utilisé.

    La mise à jour d'un palier suppose que le plan de contrôle connaît la règle
    créée : `AgentCoreGatewayClient` tient donc un cache local des règles qu'il
    a créées (le store de Jauge reste la source de vérité persistée).
    """

    def __init__(
        self,
        region: str,
        client_factory: Callable[[], Any] | None = None,
        nom_cible: str = NOM_CIBLE_PAR_DEFAUT,
    ) -> None:
        self.region = region
        self.nom_cible = nom_cible
        self._factory = client_factory or self._fabrique_boto3
        self._client: Any | None = None
        self._cache: dict[str, ReglePasserelle] = {}

    def _fabrique_boto3(self) -> Any:
        """Fabrique par défaut : client boto3 du service de contrôle AgentCore."""
        import boto3  # import tardif : les tests n'ont jamais besoin du SDK

        return boto3.client(SERVICE_AWS, region_name=self.region)

    @property
    def client(self) -> Any:
        """Client boto3, créé au premier usage réel."""
        if self._client is None:
            self._client = self._factory()
        return self._client

    def _appeler(self, methode: str, parametres: dict[str, object]) -> dict[str, Any]:
        """Appelle une opération du service et traduit les pannes réseau."""
        try:
            reponse = getattr(self.client, methode)(**parametres)
        except Exception as erreur:  # boto3 lève des exceptions très variées
            raise ServiceIndisponibleError(
                f"Appel {methode} refusé par la passerelle AgentCore.",
                service=SERVICE_AWS,
                operation=methode,
                cause=type(erreur).__name__,
            ) from erreur
        return dict(reponse or {})

    def _creer(
        self,
        passerelle_id: str,
        modele_demande: str,
        modele_cible: str,
        pourcentage: int,
        description: str,
    ) -> ReglePasserelle:
        """Crée la règle (statique à 100 %, pondérée sinon) puis la met en cache."""
        actions = construire_action_ponderee(
            self.nom_cible, modele_demande, modele_cible, pourcentage
        )
        parametres: dict[str, object] = {
            "gatewayIdentifier": passerelle_id,
            "priority": PRIORITE_PAR_DEFAUT,
            "description": description,
            "conditions": construire_conditions(modele_demande),
            "actions": actions,
        }
        reponse = self._appeler("create_gateway_rule", parametres)
        identifiant = str(
            reponse.get("ruleIdentifier") or reponse.get("gatewayRuleIdentifier") or ""
        )
        if not identifiant:
            raise ServiceIndisponibleError(
                "La passerelle n'a pas renvoyé d'identifiant de règle.",
                service=SERVICE_AWS,
                operation="create_gateway_rule",
            )
        regle = ReglePasserelle(
            id=identifiant,
            passerelle_id=passerelle_id,
            modele_demande=modele_demande,
            pourcentage_cible=pourcentage,
            modele_cible=modele_cible,
            nom_cible=self.nom_cible,
            description=description,
        )
        self._cache[identifiant] = regle
        return regle

    def creer_regle_statique(
        self, passerelle_id: str, modele_demande: str, modele_cible: str, description: str = ""
    ) -> ReglePasserelle:
        return self._creer(passerelle_id, modele_demande, modele_cible, 100, description)

    def creer_regle_ponderee(
        self,
        passerelle_id: str,
        modele_demande: str,
        modele_cible: str,
        pourcentage_cible: int,
        description: str = "",
    ) -> ReglePasserelle:
        verifier_pourcentage(pourcentage_cible)
        return self._creer(
            passerelle_id, modele_demande, modele_cible, pourcentage_cible, description
        )

    def mettre_a_jour_poids(self, regle_id: str, pourcentage_cible: int) -> ReglePasserelle:
        """Change le poids d'une règle existante (palier suivant, Annexe C)."""
        verifier_pourcentage(pourcentage_cible)
        regle = self.lire_regle(regle_id)
        if regle is None:
            raise IntrouvableError(
                "Règle inconnue du client de passerelle : la relire avant mise à jour.",
                regle_id=regle_id,
            )
        parametres: dict[str, object] = {
            "gatewayIdentifier": regle.passerelle_id,
            "ruleIdentifier": regle.id,
            "actions": construire_action_mise_a_jour_poids(
                self.nom_cible,
                regle.modele_demande,
                regle.modele_cible or "",
                pourcentage_cible,
            ),
        }
        self._appeler("update_gateway_rule", parametres)
        maj = ReglePasserelle(
            id=regle.id,
            passerelle_id=regle.passerelle_id,
            modele_demande=regle.modele_demande,
            pourcentage_cible=pourcentage_cible,
            modele_cible=regle.modele_cible,
            nom_cible=regle.nom_cible,
            priorite=regle.priorite,
            description=regle.description,
            active=regle.active,
        )
        self._cache[regle_id] = maj
        return maj

    def supprimer_regle(self, regle_id: str) -> None:
        """Retire la règle : tout le trafic revient au modèle demandé (EF-BAS-03)."""
        regle = self._cache.get(regle_id)
        if regle is None:
            raise IntrouvableError("Règle inconnue du client de passerelle.", regle_id=regle_id)
        self._appeler(
            "delete_gateway_rule",
            {"gatewayIdentifier": regle.passerelle_id, "ruleIdentifier": regle.id},
        )
        del self._cache[regle_id]

    def lire_regle(self, regle_id: str) -> ReglePasserelle | None:
        return self._cache.get(regle_id)

    def lister_regles(self, passerelle_id: str) -> list[ReglePasserelle]:
        return [r for r in self._cache.values() if r.passerelle_id == passerelle_id]
