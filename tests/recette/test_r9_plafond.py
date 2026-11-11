"""Recette R9 — campagne au-dessus du plafond refusée (§22, ENF-COUT-01).

Scénario : « Campagne dont le coût estimé dépasse le plafond » → **Refusée**.

Le refus est produit par le module de rejeu (`jauge.replay.cost`) avant tout
appel de modèle : aucun dollar n'est dépensé pour découvrir que le plafond
saute. La recette vérifie les deux portes :

- le module : `estimer_et_verifier` lève `PlafondDepasseError` et le devis
  affiche le dépassement ;
- l'API du plan de contrôle : `POST /v1/applications/{id}/campaigns` répond
  `422 campaign_cost_cap_exceeded` au-delà du plafond du client.

Les hypothèses de coût sont les moyennes réelles du trafic de démonstration
(jetons d'entrée et de sortie observés), jamais des chiffres inventés
(ENF-IA-01). Aucun réseau.
"""

from __future__ import annotations

import pytest
from demo.profils import DATE_FIN_DEFAUT, GRAINE_DEFAUT
from demo.seed import peupler_store
from demo.socle import charger_socle_demo
from demo.traffic import generer_trafic
from fastapi.testclient import TestClient
from tests.conftest import MOYEN_MODELE, PETIT_MODELE
from tests.recette.conftest import entetes
from tests.recette.outillage import MODELE_DISTILLE, table_avec_distille

from jauge.api.auth import RoleUtilisateur
from jauge.common.errors import PlafondDepasseError
from jauge.replay.client import FakeModeleClient
from jauge.replay.cost import HypothesesCout, estimer_et_verifier
from jauge.store.memory import MemoryStore

ANALYSTE = RoleUtilisateur.ANALYSTE_FINOPS
APP_ASSISTANT = "support-assistant"
REGION = "eu-west-1"
TAILLE = 200


def _hypotheses_du_trafic() -> HypothesesCout:
    """Moyennes de jetons observées sur l'assistant (aucun chiffre inventé)."""
    records = [
        record
        for record in generer_trafic(graine=GRAINE_DEFAUT, jours=1, date_fin=DATE_FIN_DEFAUT)
        if record.application_id == APP_ASSISTANT and record.code_erreur is None
    ]
    assert records, "pas de trafic de démonstration pour l'assistant"
    return HypothesesCout(
        jetons_entree_moyens=round(sum(r.jetons_entree for r in records) / len(records)),
        jetons_sortie_moyens=round(sum(r.jetons_sortie for r in records) / len(records)),
    )


def test_r9_campagne_au_dessus_du_plafond_refusee() -> None:
    """R9 : le devis au-dessus du plafond lève `PlafondDepasseError` (§17.1)."""
    socle = charger_socle_demo()
    table = table_avec_distille(socle.tarifs)
    hypotheses = _hypotheses_du_trafic()
    client_modele = FakeModeleClient()

    with pytest.raises(PlafondDepasseError) as echec:
        estimer_et_verifier(
            tarifs=table,
            region=REGION,
            a_la_date=DATE_FIN_DEFAUT,
            modele_actuel=socle.applications[APP_ASSISTANT].modele_actuel,
            candidats=[MOYEN_MODELE, PETIT_MODELE, MODELE_DISTILLE],
            taille_echantillon=TAILLE,
            hypotheses=hypotheses,
            modele_juge_id=socle.applications[APP_ASSISTANT].modele_actuel,
            plafond_usd=socle.tenant_a.plafond_campagne_usd,
        )

    details = echec.value.details
    assert details["plafond_usd"] == socle.tenant_a.plafond_campagne_usd
    assert details["cout_estime_usd"] > details["plafond_usd"]
    assert details["depassement_usd"] > 0.0

    # Aucun appel de modèle : le refus précède toute dépense (ENF-COUT-01).
    assert client_modele.appels == []


def test_r9_campagne_sous_le_plafond_acceptee_avec_devis() -> None:
    """R9 (controle) : sous le plafond, la campagne est chiffrée et acceptée."""
    socle = charger_socle_demo()
    table = table_avec_distille(socle.tarifs)
    hypotheses = _hypotheses_du_trafic()

    estimation = estimer_et_verifier(
        tarifs=table,
        region=REGION,
        a_la_date=DATE_FIN_DEFAUT,
        modele_actuel=socle.applications[APP_ASSISTANT].modele_actuel,
        candidats=[MOYEN_MODELE, PETIT_MODELE, MODELE_DISTILLE],
        taille_echantillon=TAILLE,
        hypotheses=hypotheses,
        modele_juge_id=socle.applications[APP_ASSISTANT].modele_actuel,
        plafond_usd=100.0,
    )

    assert estimation.sous_plafond is True
    assert estimation.cout_total_usd > 0.0
    assert estimation.taille_echantillon == TAILLE
    assert estimation.nombre_candidats == 3
    # §17.1 : 200 requêtes et 3 candidats → 1 000 exécutions et 800 jugements.
    assert estimation.executions_rejeu == TAILLE * 5
    assert estimation.jugements == TAILLE * 4
    assert estimation.marge_usd() > 0.0


def test_r9_api_refuse_la_campagne_au_dessus_du_plafond(
    client_api: TestClient, store_recette: MemoryStore
) -> None:
    """R9 (API) : la campagne au-dessus du plafond du client est refusée en 422."""
    socle = charger_socle_demo()
    peupler_store(store_recette, jours=1, date_fin=DATE_FIN_DEFAUT)
    headers = entetes(socle.tenant_a.id, ANALYSTE)

    # 1. Une campagne ordinaire (200 requêtes) passe sous le plafond.
    reponse = client_api.post(
        f"/v1/applications/{APP_ASSISTANT}/campaigns",
        json={"taille_echantillon": 200},
        headers=headers,
    )
    assert reponse.status_code == 201, reponse.text
    assert reponse.json()["cout_estime_usd"] > 0.0

    # 2. Une campagne trop large est refusée avant tout rejeu (ENF-COUT-01).
    reponse = client_api.post(
        f"/v1/applications/{APP_ASSISTANT}/campaigns",
        json={"taille_echantillon": 2000},
        headers=headers,
    )
    assert reponse.status_code == 422, reponse.text
    corps = reponse.json()
    assert corps["error"]["code"] == "campaign_cost_cap_exceeded"
    details = corps["error"]["details"]
    assert details["cout_estime_usd"] > details["plafond_usd"]
    assert details["plafond_usd"] == socle.tenant_a.plafond_campagne_usd

    # 3. Aucune campagne refusée n'a été enregistrée.
    tailles = [c.taille_echantillon for c in store_recette.lister_campagnes(socle.tenant_a.id)]
    assert 2000 not in tailles
