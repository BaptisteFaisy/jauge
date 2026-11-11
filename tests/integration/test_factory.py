"""La fabrique assemble les services et l'application (M17).

Vérifie que `creer_services` branche bien les **douze ports** des agents sur les
modules réels, et que `creer_app` renvoie une application FastAPI utilisable
avec ces services. Aucun appel réseau (ENF-IA-01, §12).
"""

from __future__ import annotations

from tests.integration.conftest import Environnement, entetes

from jauge.agents.ports import (
    PortAgregats,
    PortEnregistrement,
    PortEntreesRecommandation,
    PortEstimationCout,
    PortLeviers,
    PortModelesCandidats,
    PortProfilTache,
    PortRapprochement,
    PortReferentiel,
    PortRejeu,
    PortResultats,
    PortValidationHumaine,
    Services,
)
from jauge.api.app import Ports
from jauge.integration import Clients, creer_app, creer_services
from jauge.integration.chat import AdaptateurChat
from jauge.store.memory import MemoryStore

PORTS_ATTENDUS = {
    "referentiel": PortReferentiel,
    "agregats": PortAgregats,
    "leviers": PortLeviers,
    "rapprochement": PortRapprochement,
    "profils": PortProfilTache,
    "candidats": PortModelesCandidats,
    "estimation": PortEstimationCout,
    "rejeu": PortRejeu,
    "resultats": PortResultats,
    "entrees": PortEntreesRecommandation,
    "enregistrement": PortEnregistrement,
    "validation": PortValidationHumaine,
}


def test_creer_services_assemble_les_douze_ports(services: Services) -> None:
    """Chaque port est satisfait par un adaptateur réel."""
    assert isinstance(services, Services)
    for nom, protocole in PORTS_ATTENDUS.items():
        assert isinstance(getattr(services, nom), protocole), f"port {nom} non conforme"


def test_creer_services_sans_plan_ni_trafic() -> None:
    """La fabrique fonctionne sur un store vide (aucune donnée figée requise)."""
    services = creer_services(MemoryStore(), mode_demo=True, clients=Clients())
    assert isinstance(services, Services)


def test_creer_app_expose_les_services(environnement: Environnement) -> None:
    """L'application expose les services et le plan de données dans son état."""
    app = creer_app(
        mode_demo=True,
        store=environnement.store,
        tarifs=environnement.tarifs,
        plan=environnement.plan,
    )
    assert isinstance(app.state.services, Services)
    assert app.state.plan_de_donnees is environnement.plan
    assert isinstance(app.state.port_chat, AdaptateurChat)


def test_creer_app_utilise_les_ports_reels(environnement: Environnement) -> None:
    """Les trois ports métier de l'API sont branchés sur les modules réels."""
    app = creer_app(
        mode_demo=True,
        store=environnement.store,
        tarifs=environnement.tarifs,
        plan=environnement.plan,
    )
    assert not isinstance(app.state.port_chat, type(None))
    assert app.state.port_campagnes.__class__.__name__ == "AdaptateurCampagnes"
    assert app.state.port_rapports.__class__.__name__ == "AdaptateurRapports"


def test_app_demarre_et_sert_identite(client) -> None:
    """L'application répond sur la route d'identité de démonstration."""
    reponse = client.get("/v1/demo/identity", headers=entetes())
    assert reponse.status_code == 200
    assert reponse.json()["utilisateur"]["tenant_id"] == "t-demo-a"


def test_ports_de_lapi_sont_assembles(environnement: Environnement) -> None:
    """`Ports` regroupe bien les trois adaptateurs injectés dans l'application."""
    services = creer_services(
        environnement.store,
        mode_demo=True,
        clients=Clients(),
        tarifs=environnement.tarifs,
        plan=environnement.plan,
    )
    from jauge.integration.api_ports import AdaptateurCampagnes, AdaptateurRapports

    ports = Ports(
        campagnes=AdaptateurCampagnes(environnement.store, services.rejeu),
        rapports=AdaptateurRapports(environnement.store, environnement.tarifs),
        chat=AdaptateurChat(services, environnement.store, mode_demo=True),
    )
    assert ports.campagnes is not None
    assert ports.rapports is not None
    assert ports.chat is not None
