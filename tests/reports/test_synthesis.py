"""Tests de la synthèse rédigée : ornement, jamais calcul (ENF-IA-01, EF-RAP-03).

Trois choses sont vérifiées ici :

1. la synthèse déterministe ne modifie aucun chiffre du rapport ;
2. un modèle qui cite un nombre non fourni est rejeté et le repli s'applique ;
3. un modèle indisponible laisse le rapport valide (synthèse déterministe).

Aucun test n'appelle Bedrock, Strands ou un quelconque service réseau.
"""

from __future__ import annotations

import io
import json

import pytest

from jauge.common.errors import ServiceIndisponibleError
from jauge.reports.builder import construire_rapport
from jauge.reports.synthesis import (
    ContexteSynthese,
    RedacteurBedrock,
    RedacteurDeterministe,
    appliquer_synthese,
    contexte_depuis_rapport,
    empreinte_chiffres,
    nombres_autorises,
    nombres_cites,
    texte_conforme,
)
from jauge.store.memory import MemoryStore

MOIS = (2026, 10)


class RedacteurFictif:
    """Rédacteur de test : rend un texte fixé, sans réseau."""

    def __init__(self, texte: str) -> None:
        self.texte = texte
        self.recu: ContexteSynthese | None = None

    @property
    def nom(self) -> str:
        return "fictif:test"

    def rediger(self, contexte: ContexteSynthese) -> str:
        self.recu = contexte
        return self.texte


class RedacteurEnPanne:
    """Rédacteur qui simule un fournisseur indisponible."""

    @property
    def nom(self) -> str:
        return "fictif:panne"

    def rediger(self, contexte: ContexteSynthese) -> str:
        raise ServiceIndisponibleError("Bedrock indisponible")


class ClientBedrockFactice:
    """Client bedrock-runtime factice : rend la réponse JSON fixée, sans réseau."""

    def __init__(self, texte: str) -> None:
        self.texte = texte
        self.corps: list[bytes] = []

    def invoke_model(self, modelId: str, body: bytes) -> dict:
        self.corps.append(body)
        contenu = json.dumps({"content": [{"text": json.dumps({"synthesis": self.texte})}]})
        return {"body": io.BytesIO(contenu.encode("utf-8"))}


class ClientBedrockEnPanne:
    """Client bedrock-runtime qui échoue, comme un service indisponible."""

    def invoke_model(self, modelId: str, body: bytes) -> dict:
        raise RuntimeError("ThrottlingException")


@pytest.fixture
def rapport(store_peuple: MemoryStore):
    """Rapport d'octobre 2026, sans synthèse."""
    return construire_rapport(store_peuple, "t-demo-a", *MOIS)


def test_synthese_deterministe_ne_change_aucun_chiffre(rapport) -> None:
    """L'empreinte de tous les champs chiffrés est identique avant et après."""
    avant = empreinte_chiffres(rapport)
    enrichi = appliquer_synthese(rapport, RedacteurDeterministe())
    assert empreinte_chiffres(enrichi) == avant
    assert enrichi.synthese is not None
    assert enrichi.synthese.source == "deterministe"
    assert enrichi.synthese.degradee is False


def test_synthese_deterministe_cite_les_chiffres_du_rapport(rapport) -> None:
    """Le texte déterministe reprend les montants, sans en inventer."""
    texte = appliquer_synthese(rapport).synthese.texte
    assert "55.00" in texte
    assert "40.00" in texte
    assert "9.50" in texte
    assert "12.00" in texte
    assert texte_conforme(texte, contexte_depuis_rapport(rapport)) is True


def test_sans_redacteur_la_synthese_reste_deterministe(rapport) -> None:
    """Aucun rédacteur fourni : la synthèse est produite par le code."""
    enrichi = appliquer_synthese(rapport)
    assert enrichi.synthese is not None
    assert enrichi.synthese.modele is None
    assert enrichi.synthese.source == "deterministe"


def test_contexte_ne_contient_que_des_chiffres_et_des_faits(rapport) -> None:
    """Le contexte transmet des nombres déjà calculés et des phrases factuelles."""
    contexte = contexte_depuis_rapport(rapport)
    assert contexte.chiffres["cout_total_usd"] == pytest.approx(55.0)
    assert contexte.chiffres["economies_constatees_usd"] == pytest.approx(9.5)
    assert contexte.faits
    assert all(isinstance(fait, str) and fait for fait in contexte.faits)


def test_modele_conforme_est_accepte_et_tracede(rapport) -> None:
    """Un texte qui ne cite que des nombres fournis est retenu, avec provenance."""
    redacteur = RedacteurFictif("Spend reached 55.00 USD with verified savings of 9.50 USD.")
    enrichi = appliquer_synthese(rapport, redacteur)
    assert enrichi.synthese.source == "modele"
    assert enrichi.synthese.modele == "fictif:test"
    assert enrichi.synthese.degradee is False
    assert empreinte_chiffres(enrichi) == empreinte_chiffres(rapport)


def test_modele_qui_invente_un_chiffre_est_rejete(rapport) -> None:
    """Un nombre absent du contexte invalide la synthèse et déclenche le repli."""
    redacteur = RedacteurFictif("Spend fell by 43.7 percent, saving 1234.56 USD.")
    enrichi = appliquer_synthese(rapport, redacteur)
    assert enrichi.synthese.source == "deterministe"
    assert enrichi.synthese.degradee is True
    assert enrichi.synthese.modele == "fictif:test"
    assert "43.7" not in enrichi.synthese.texte


def test_modele_en_panne_laisse_le_rapport_valide(rapport) -> None:
    """Une panne du fournisseur ne casse pas le rapport : repli déterministe."""
    enrichi = appliquer_synthese(rapport, RedacteurEnPanne())
    assert enrichi.synthese.source == "deterministe"
    assert enrichi.synthese.degradee is True
    assert enrichi.cout_total_usd == pytest.approx(55.0)


def test_modele_vide_est_rejete(rapport) -> None:
    """Une réponse vide n'est pas une synthèse : repli déterministe."""
    enrichi = appliquer_synthese(rapport, RedacteurFictif("   "))
    assert enrichi.synthese.source == "deterministe"
    assert enrichi.synthese.degradee is True


def test_nombres_autorises_couvrent_chiffres_et_faits(rapport) -> None:
    """Les nombres autorisés incluent ceux des faits, et rien d'autre."""
    contexte = contexte_depuis_rapport(rapport)
    autorises = nombres_autorises(contexte)
    assert "55.00" in autorises
    assert "999.99" not in autorises
    assert nombres_cites("Le coût est de 55.00 USD") == {"55.00"}
    assert texte_conforme("Le coût est de 55.00 USD", contexte) is True
    assert texte_conforme("Le coût est de 77.77 USD", contexte) is False


def test_synthese_ne_touche_pas_aux_champs_chiffres(rapport) -> None:
    """Comparaison champ par champ : seule la synthèse apparaît."""
    enrichi = appliquer_synthese(rapport, RedacteurFictif("Spend reached 55.00 USD."))
    avant = rapport.model_dump(mode="json")
    apres = enrichi.model_dump(mode="json")
    avant.pop("synthese")
    apres.pop("synthese")
    assert apres == avant


def test_redacteur_bedrock_testé_par_son_interface(rapport) -> None:
    """Le rédacteur Bedrock est éprouvé via un client factice, sans réseau."""
    client = ClientBedrockFactice("Spend reached 55.00 USD with 9.50 USD verified.")
    redacteur = RedacteurBedrock("modele-juge-demo", client=client)
    enrichi = appliquer_synthese(rapport, redacteur)
    assert enrichi.synthese.source == "modele"
    assert enrichi.synthese.modele == "bedrock:modele-juge-demo"
    assert "55.00" in enrichi.synthese.texte
    assert client.corps and b"FACTS" in client.corps[0]


def test_redacteur_bedrock_en_panne_degrade_le_rapport(rapport) -> None:
    """Une panne Bedrock devient une synthèse déterministe, jamais une exception."""
    redacteur = RedacteurBedrock("modele-juge-demo", client=ClientBedrockEnPanne())
    enrichi = appliquer_synthese(rapport, redacteur)
    assert enrichi.synthese.source == "deterministe"
    assert enrichi.synthese.degradee is True
    assert enrichi.synthese.modele == "bedrock:modele-juge-demo"
    assert enrichi.cout_total_usd == pytest.approx(55.0)


def test_redacteur_bedrock_leve_service_indisponible_sans_client() -> None:
    """L'échec du fournisseur est traduit en erreur métier du socle."""
    redacteur = RedacteurBedrock("modele-juge-demo", client=ClientBedrockEnPanne())
    contexte = ContexteSynthese(tenant_nom="Client A", periode="2026-10", devise="USD")
    with pytest.raises(ServiceIndisponibleError):
        redacteur.rediger(contexte)
