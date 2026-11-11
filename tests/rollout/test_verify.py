"""Tests de la vérification d'économie à volume constant (EF-VER-01, EF-VER-02).

Le scénario R12 exige « coût par requête avant et après, à volume constant,
cohérent avec la facture ». Ces tests fixent les trois points : le volume de
référence est celui de l'après, l'économie attendue vient de la recommandation
recalculée au même volume, et la facture par étiquette confirme ou infirme.
"""

from __future__ import annotations

from datetime import date

import pytest
from tests.rollout.conftest import agregats

from jauge.common.errors import CloisonnementError, ValidationError
from jauge.models.recommendation import Recommandation
from jauge.models.tenant import Application, Tenant
from jauge.rollout.verify import (
    baisse_observee,
    confirmer_par_etiquette,
    cout_par_requete,
    ecart_relatif,
    verifier_economie,
    volume,
)


def test_cout_par_requete_moyen(tenant: Tenant, app_classement: Application) -> None:
    """Coût total divisé par le nombre de requêtes, sur plusieurs jours."""
    lignes = agregats(tenant.id, app_classement.id, 0.01, 600, date(2026, 10, 1))
    lignes += agregats(tenant.id, app_classement.id, 0.01, 400, date(2026, 10, 2))

    assert volume(lignes) == 1000
    assert cout_par_requete(lignes, tenant.id) == pytest.approx(0.01)


def test_cout_par_requete_volume_nul_refuse(tenant: Tenant, app_classement: Application) -> None:
    """Zéro requête : pas de coût moyen silencieux, mais une erreur explicite."""
    lignes = agregats(tenant.id, app_classement.id, 0.01, 0)
    with pytest.raises(ValidationError):
        cout_par_requete(lignes, tenant.id)
    with pytest.raises(ValidationError):
        cout_par_requete([], tenant.id)


def test_cloisonnement_refuse(
    tenant: Tenant, tenant_b: Tenant, app_classement: Application
) -> None:
    """Des agrégats d'un autre client ne participent jamais au calcul (R11)."""
    lignes = agregats(tenant.id, app_classement.id, 0.01, 100)
    lignes += agregats(tenant_b.id, app_classement.id, 0.01, 100)

    with pytest.raises(CloisonnementError):
        cout_par_requete(lignes, tenant.id)


def test_verification_a_volume_constant(
    tenant: Tenant, app_classement: Application, recommandation: Recommandation
) -> None:
    """Le volume de l'après sert de base commune, même si l'avant était plus gros."""
    avant = agregats(tenant.id, app_classement.id, 0.01, 5000, date(2026, 9, 1))
    apres = agregats(tenant.id, app_classement.id, 0.002, 1000, date(2026, 10, 15))

    verification = verifier_economie(tenant.id, app_classement, recommandation, avant, apres)

    assert verification.volume_periode == 1000
    assert verification.cout_par_requete_avant == pytest.approx(0.01)
    assert verification.cout_par_requete_apres == pytest.approx(0.002)
    assert verification.economie_constatee_usd == pytest.approx(8.0)
    assert verification.economie_attendue_usd == pytest.approx(8.0)
    assert verification.ecart_pourcentage == pytest.approx(0.0)
    assert baisse_observee(verification) == pytest.approx(80.0)


def test_ecart_quand_l_economie_est_moindre(
    tenant: Tenant, app_classement: Application, recommandation: Recommandation
) -> None:
    """Coût après plus élevé que prévu : écart négatif, chiffré, jamais masqué."""
    avant = agregats(tenant.id, app_classement.id, 0.01, 1000, date(2026, 9, 1))
    apres = agregats(tenant.id, app_classement.id, 0.005, 1000, date(2026, 10, 15))

    verification = verifier_economie(tenant.id, app_classement, recommandation, avant, apres)

    assert verification.economie_constatee_usd == pytest.approx(5.0)
    assert verification.economie_attendue_usd == pytest.approx(8.0)
    assert verification.ecart_pourcentage == pytest.approx(-37.5)


def test_verification_refuse_un_autre_client(
    tenant: Tenant,
    tenant_b: Tenant,
    app_classement: Application,
    recommandation: Recommandation,
) -> None:
    """Client B demandant la vérification du client A → refus (ENF-SEC-02)."""
    avant = agregats(tenant.id, app_classement.id, 0.01, 1000, date(2026, 9, 1))
    apres = agregats(tenant.id, app_classement.id, 0.002, 1000, date(2026, 10, 15))

    with pytest.raises(CloisonnementError):
        verifier_economie(tenant_b.id, app_classement, recommandation, avant, apres)


def test_ecart_relatif_sans_attendu() -> None:
    """Une économie attendue nulle ne divise pas par zéro."""
    assert ecart_relatif(10.0, 0.0) == 0.0


def test_confirmation_par_etiquette_conforme(tenant: Tenant) -> None:
    """Les dollars facturés par étiquette confirment l'économie (EF-VER-02)."""
    confirmation = confirmer_par_etiquette(
        tenant.id,
        "ticket-classifier",
        dollars_avant={"ticket-classifier": 700.0, "support-assistant": 300.0},
        dollars_apres={"ticket-classifier": 560.0, "support-assistant": 240.0},
        economie_attendue_usd=200.0,
    )

    assert confirmation.economie_facturee_usd == pytest.approx(200.0)
    assert confirmation.ecart_pourcentage == pytest.approx(0.0)
    assert confirmation.conforme is True
    assert confirmation.etiquettes == ("support-assistant", "ticket-classifier")


def test_confirmation_par_etiquette_non_conforme(tenant: Tenant) -> None:
    """Un écart au-delà de la tolérance est signalé comme non conforme."""
    confirmation = confirmer_par_etiquette(
        tenant.id,
        "ticket-classifier",
        dollars_avant={"ticket-classifier": 1000.0},
        dollars_apres={"ticket-classifier": 950.0},
        economie_attendue_usd=200.0,
    )

    assert confirmation.economie_facturee_usd == pytest.approx(50.0)
    assert confirmation.conforme is False
    assert confirmation.ecart_pourcentage == pytest.approx(-75.0)


def test_confirmation_par_etiquette_absente_vaut_zero(tenant: Tenant) -> None:
    """Une étiquette absente d'une période compte pour 0 $ (pas d'erreur)."""
    confirmation = confirmer_par_etiquette(
        tenant.id,
        "ticket-classifier",
        dollars_avant={"ticket-classifier": 300.0},
        dollars_apres={"ticket-classifier": 100.0, "resume-nuit": 0.0},
        economie_attendue_usd=200.0,
    )

    assert confirmation.etiquettes == ("resume-nuit", "ticket-classifier")
    assert confirmation.conforme is True


def test_confirmation_par_etiquette_refuse_les_entrees_vides(tenant: Tenant) -> None:
    """Sans aucune ligne facturée, il n'y a rien à confirmer."""
    with pytest.raises(ValidationError):
        confirmer_par_etiquette(tenant.id, "ticket-classifier", {}, {}, 10.0)


def test_confirmation_par_etiquette_refuse_un_montant_negatif(tenant: Tenant) -> None:
    """Un montant négatif signale une lecture CUR 2.0 incohérente."""
    with pytest.raises(ValidationError):
        confirmer_par_etiquette(
            tenant.id,
            "ticket-classifier",
            {"ticket-classifier": -5.0},
            {"ticket-classifier": 1.0},
            10.0,
        )
