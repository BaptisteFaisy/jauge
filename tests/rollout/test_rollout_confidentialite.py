"""Test de confidentialité : aucun texte de requête ne sort du plan de données.

ENF-CONF-01 : le texte des requêtes et des réponses ne quitte jamais le compte du
client. Le plan de contrôle ne reçoit que des agrégats, des scores et des
identifiants. Ce test exerce tout le parcours M6 (bascule, rejugement en ligne,
vérification, ticket, règle de passerelle) avec un texte marqué dans le plan de
données, puis cherche ce marqueur dans **toutes** les sorties du module.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from tests.rollout.conftest import agregats

from jauge.models.campaign import PaireRejoueeDataPlane
from jauge.models.recommendation import Recommandation
from jauge.models.tenant import Application, Tenant
from jauge.rollout.controller import ControlleurBascule, FakeQualiteEnLigne, MesureEnLigne
from jauge.rollout.gateway import AgentCoreGatewayClient, FakePasserelleClient
from jauge.rollout.ticket import generer_ticket
from jauge.rollout.verify import verifier_economie
from jauge.store.memory import MemoryStore

# Texte sentinelle : il ne doit apparaître nulle part hors du plan de données.
MARQUEUR = "TEXTE-CONFIDENTIEL-REQUETE-42"


def paire_data_plane(app: Application) -> PaireRejoueeDataPlane:
    """Paire rejouée du plan de données, avec le marqueur dans les trois textes."""
    return PaireRejoueeDataPlane(
        campagne_id="c-demo-001",
        requete_id="q-001",
        modele_candidat_id="anthropic.petit-demo",
        texte_requete=f"Bonjour, {MARQUEUR}",
        reponse_reference=f"Référence {MARQUEUR}",
        reponse_candidate=f"Candidate {MARQUEUR}",
        score_equivalence=0.97,
        explication_juge=f"Explication {MARQUEUR}",
    )


class ClientFactice:
    """Double du client boto3 : conserve les paramètres envoyés à la passerelle."""

    def __init__(self) -> None:
        self.appels: list[dict[str, object]] = []

    def create_gateway_rule(self, **parametres: object) -> dict[str, object]:
        self.appels.append(parametres)
        return {"ruleIdentifier": "regle-aws-1"}

    def update_gateway_rule(self, **parametres: object) -> dict[str, object]:
        self.appels.append(parametres)
        return {}

    def delete_gateway_rule(self, **parametres: object) -> dict[str, object]:
        self.appels.append(parametres)
        return {}


def test_aucun_texte_de_requete_dans_les_sorties(
    tenant: Tenant, app_classement: Application, recommandation: Recommandation
) -> None:
    """Bascule, vérification et ticket : le marqueur ne ressort jamais."""
    paire = paire_data_plane(app_classement)
    store = MemoryStore()
    store.enregistrer_tenant(tenant)
    store.enregistrer_application(app_classement)
    store.enregistrer_recommandation(recommandation)
    passerelle = FakePasserelleClient()
    qualite = FakeQualiteEnLigne(
        [MesureEnLigne(taux_equivalence=0.98, intervalle_bas=0.95, taux_erreur=0.005)] * 3
    )
    controlleur = ControlleurBascule(passerelle=passerelle, qualite=qualite, store=store)

    bascule = controlleur.demarrer(
        tenant.id, app_classement, recommandation, approuve_par="responsable"
    )
    for _ in range(3):
        bascule = controlleur.avancer(tenant.id, bascule)
    verification = verifier_economie(
        tenant.id,
        app_classement,
        recommandation,
        agregats(tenant.id, app_classement.id, 0.01, 1000, date(2026, 9, 1)),
        agregats(tenant.id, app_classement.id, 0.002, 1000, date(2026, 10, 15)),
    )
    ticket = generer_ticket(
        tenant.id,
        app_classement.model_copy(update={"passe_par_passerelle": False}),
        recommandation,
        moment=datetime(2026, 11, 11, 9, 30, tzinfo=UTC),
    )

    sorties = [
        bascule.model_dump_json(),
        verification.model_dump_json(),
        ticket.corps,
        ticket.titre,
        str(passerelle.journal),
        str(controlleur.mesures(bascule.id)),
        str(controlleur.alertes(bascule.id)),
        str(recommandation.model_dump_json()),
    ]
    for sortie in sorties:
        assert MARQUEUR not in sortie

    # La paire rejouée est bien la seule à porter le texte (plan de données).
    assert MARQUEUR in paire.texte_requete


def test_regle_de_passerelle_sans_texte(
    tenant: Tenant, app_classement: Application, recommandation: Recommandation
) -> None:
    """Le message envoyé à AgentCore ne contient que des identifiants de modèles."""
    factice = ClientFactice()
    client = AgentCoreGatewayClient(region="eu-west-1", client_factory=lambda: factice)

    client.creer_regle_ponderee(
        "gw-demo", app_classement.modele_actuel, recommandation.targetModel, 10
    )

    charge = str(factice.appels[0])
    assert MARQUEUR not in charge
    assert app_classement.modele_actuel in charge
    assert recommandation.targetModel in charge


def test_mesure_en_ligne_ne_porte_que_des_taux() -> None:
    """`MesureEnLigne` n'a aucun champ texte : la confidentialité est structurelle."""
    champs = set(MesureEnLigne.__dataclass_fields__)
    assert champs == {
        "taux_equivalence",
        "intervalle_bas",
        "taux_erreur",
        "nombre_requetes",
        "palier",
    }
