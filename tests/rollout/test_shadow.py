"""Tests du trafic fantôme : bornes, plafond, coût, paires, écart labo ↔ prod.

Piste n° 7 du top 10. Les adaptateurs réels (Bedrock, tâche d'évaluation) ne
sont **jamais** appelés : les tests utilisent les doubles hors ligne du dépôt
(`FakeModeleClient`, `FakeJugeClient`). Leur emploi vérifie au passage que les
ports locaux de `jauge.rollout.ports` sont structurellement compatibles avec
ceux de M4 et M5 : aucun adaptateur n'est nécessaire.

Aucun réseau, aucun AWS, aucun chiffre inventé : les montants viennent de la
table des tarifs partagée et les taux sont calculés par le code.
"""

from __future__ import annotations

import json
from datetime import date

import pytest
from tests.conftest import GRAND_MODELE, MOYEN_MODELE, PETIT_MODELE, REGION

from jauge.common.errors import CloisonnementError, PlafondDepasseError, ValidationError
from jauge.engine.calculator import TableTarifs
from jauge.evaluation.judge import FakeJugeClient, VerdictJuge
from jauge.models.campaign import ResultatCandidat
from jauge.models.tenant import Tenant
from jauge.replay.client import FakeModeleClient
from jauge.rollout.devis import BornesEchantillon
from jauge.rollout.ports import DepotPairesMemoire
from jauge.rollout.shadow import MoteurShadow, ObservationReelle, ParametresShadow

# Date d'effet de la table des tarifs partagée (`tests/conftest.py`).
DATE_EFFET = date(2026, 1, 1)


def observation(index: int, tenant_id: str = "t-demo-a") -> ObservationReelle:
    """Une requête du trafic réel, avec la réponse que l'utilisateur a reçue."""
    return ObservationReelle(
        tenant_id=tenant_id,
        application_id="ticket-classifier",
        requete_id=f"req-{index:03d}",
        texte_requete=f"classer le ticket {index}",
        reponse_reelle=f"categorie-{index}",
        jetons_entree=120,
        jetons_sortie=40,
        latence_ms=300,
    )


def observations(nombre: int = 20) -> list[ObservationReelle]:
    """Trafic réel observé : `nombre` requêtes de l'application de classement."""
    return [observation(index) for index in range(nombre)]


def parametres(tenant: Tenant, **surcharges: object) -> ParametresShadow:
    """Paramètres d'un shadow : bornes, plafond et seuils, tous explicites."""
    defauts: dict[str, object] = {
        "modele_actuel": GRAND_MODELE,
        "modele_candidat": PETIT_MODELE,
        "bornes": BornesEchantillon(taille_max=10, taux=0.5),
        "plafond_usd": tenant.plafond_campagne_usd,
        "seuil_qualite": 0.95,
        "seuil_ecart_labo_production": 0.05,
        "jetons_entree_juge": 1200,
        "jetons_sortie_juge": 400,
    }
    defauts.update(surcharges)
    return ParametresShadow(**defauts)  # type: ignore[arg-type]


def monter(tarifs, *, client=None, juge=None):
    """Moteur de shadow hors ligne : doubles uniquement, aucun appel réseau."""
    modele = client or FakeModeleClient(jetons_sortie=40, jetons_entree_fixe=120, latence_ms=200)
    notation = juge or FakeJugeClient(modele_id=MOYEN_MODELE)
    depot = DepotPairesMemoire()
    moteur = MoteurShadow(
        client=modele,
        juge=notation,
        tarifs=TableTarifs(tarifs),
        depot=depot,
        region=REGION,
        a_la_date=DATE_EFFET,
    )
    return (moteur, modele, depot)


# --- Plafond de coût (ENF-COUT-01) -----------------------------------------


def test_shadow_refuse_au_dela_du_plafond(tenant: Tenant, tarifs) -> None:
    """Un devis au-dessus du plafond est refusé **avant** tout appel de modèle."""
    moteur, modele, _ = monter(tarifs)
    with pytest.raises(PlafondDepasseError):
        moteur.preparer(
            tenant_id=tenant.id,
            application_id="ticket-classifier",
            observations=observations(),
            parametres=parametres(tenant, plafond_usd=0.0001),
        )
    assert modele.appels == []


def test_shadow_sous_le_plafond_est_prepare(tenant: Tenant, tarifs) -> None:
    """Sous le plafond, le shadow est préparé : devis chiffré et échantillon tiré."""
    moteur, _, _ = monter(tarifs)
    plan = moteur.preparer(
        tenant_id=tenant.id,
        application_id="ticket-classifier",
        observations=observations(),
        parametres=parametres(tenant),
    )
    assert plan.estimation.cout_total_usd > 0
    assert plan.estimation.sous_plafond
    assert plan.estimation.marge_usd() > 0
    assert len(plan.observations) == 10
    assert plan.volume_observe == 20


def test_bornes_taille_et_taux_sont_appliquees(tenant: Tenant, tarifs) -> None:
    """L'échantillon ne dépasse ni la taille maximale ni la part du trafic."""
    moteur, _, _ = monter(tarifs)
    plan = moteur.preparer(
        tenant_id=tenant.id,
        application_id="ticket-classifier",
        observations=observations(100),
        parametres=parametres(tenant, bornes=BornesEchantillon(taille_max=20, taux=0.1)),
    )
    assert len(plan.observations) == 10
    assert plan.volume_observe == 100


def test_echantillon_reproductible(tenant: Tenant, tarifs) -> None:
    """Même graine, même échantillon : la mesure du shadow est reproductible."""
    moteur, _, _ = monter(tarifs)
    premier = moteur.preparer(
        tenant_id=tenant.id,
        application_id="ticket-classifier",
        observations=observations(),
        parametres=parametres(tenant),
    )
    second = moteur.preparer(
        tenant_id=tenant.id,
        application_id="ticket-classifier",
        observations=observations(),
        parametres=parametres(tenant),
    )
    assert premier.requete_ids == second.requete_ids


def test_cloisonnement_refuse_le_trafic_d_un_autre_client(tenant: Tenant, tarifs) -> None:
    """Le trafic d'un autre client n'entre pas dans le shadow (ENF-SEC-02)."""
    moteur, _, _ = monter(tarifs)
    with pytest.raises(CloisonnementError):
        moteur.preparer(
            tenant_id=tenant.id,
            application_id="ticket-classifier",
            observations=[observation(1, tenant_id="t-demo-b")],
            parametres=parametres(tenant),
        )


# --- Coût et latence induits par le shadow ----------------------------------


def test_cout_du_shadow_chiffre_et_non_nul(tenant: Tenant, tarifs) -> None:
    """Le shadow a un prix : coût chiffré, non nul, et jetons comptés."""
    moteur, _, _ = monter(tarifs)
    plan = moteur.preparer(
        tenant_id=tenant.id,
        application_id="ticket-classifier",
        observations=observations(),
        parametres=parametres(tenant),
    )
    bilan = moteur.executer(plan)
    assert plan.estimation.cout_total_usd > 0
    assert bilan.cout_candidat_usd > 0
    assert bilan.cout_judge_usd > 0
    assert bilan.cout_shadow_usd == pytest.approx(
        bilan.cout_candidat_usd + bilan.cout_judge_usd, abs=1e-9
    )
    assert bilan.jetons_entree_shadow > 0
    assert bilan.jetons_sortie_shadow > 0
    assert bilan.latence_shadow_p50_ms == 200
    assert bilan.surcout_latence_p50_ms == 0


def test_paires_deposees_dans_le_plan_de_donnees(tenant: Tenant, tarifs) -> None:
    """Les paires (réponse réelle, réponse candidate) restent au plan de données."""
    moteur, _, depot = monter(tarifs)
    plan = moteur.preparer(
        tenant_id=tenant.id,
        application_id="ticket-classifier",
        observations=observations(),
        parametres=parametres(tenant),
    )
    bilan = moteur.executer(plan)
    paires = depot.paires(plan.run_id)
    assert len(paires) == bilan.nombre_paires
    assert paires[0].reponse_reference.startswith("categorie-")
    assert paires[0].reponse_candidate.startswith("reponse-")
    assert not hasattr(bilan, "paires")


def test_aucun_texte_dans_le_bilan_rendu(tenant: Tenant, tarifs) -> None:
    """Le bilan rendu au plan de contrôle ne contient aucun texte (ENF-CONF-01)."""
    moteur, _, _ = monter(tarifs)
    plan = moteur.preparer(
        tenant_id=tenant.id,
        application_id="ticket-classifier",
        observations=observations(),
        parametres=parametres(tenant),
    )
    bilan = moteur.executer(plan)
    rendu = json.dumps(bilan.vers_dict(), ensure_ascii=False, default=str)
    assert "classer le ticket" not in rendu
    assert "categorie-" not in rendu
    assert "reponse-" not in rendu
    assert "req-000" not in rendu


# --- Écart labo ↔ production (§21) ------------------------------------------


def test_divergence_labo_production_detectee(tenant: Tenant, tarifs) -> None:
    """Un écart entre le rejeu et la production est un résultat, pas une note."""
    moteur, _, _ = monter(tarifs)
    plan = moteur.preparer(
        tenant_id=tenant.id,
        application_id="ticket-classifier",
        observations=observations(),
        parametres=parametres(tenant),
    )
    rejeu = ResultatCandidat(
        modele_id=PETIT_MODELE,
        nombre_requetes_rejouees=200,
        taux_equivalence=0.60,
        intervalle_confiance_bas=0.52,
        intervalle_confiance_haut=0.68,
        cout_par_requete_usd=0.002,
    )
    bilan = moteur.executer(plan, rejeu)
    assert bilan.taux_equivalence_rejeu == 0.60
    assert bilan.ecart_labo_production == pytest.approx(0.40)
    assert bilan.divergence is True
    assert bilan.concluant is False


def test_shadow_aligne_sur_le_rejeu_est_concluant(tenant: Tenant, tarifs) -> None:
    """Sans divergence et au-dessus du seuil, le shadow conclut."""
    moteur, _, _ = monter(tarifs)
    plan = moteur.preparer(
        tenant_id=tenant.id,
        application_id="ticket-classifier",
        observations=observations(),
        parametres=parametres(tenant),
    )
    rejeu = ResultatCandidat(
        modele_id=PETIT_MODELE,
        nombre_requetes_rejouees=200,
        taux_equivalence=1.0,
        intervalle_confiance_bas=0.96,
        intervalle_confiance_haut=1.0,
        cout_par_requete_usd=0.002,
    )
    bilan = moteur.executer(plan, rejeu)
    assert bilan.divergence is False
    assert bilan.concluant is True
    assert bilan.ecart_labo_production == pytest.approx(0.0)


def test_qualite_insuffisante_le_shadow_ne_conclut_pas(tenant: Tenant, tarifs) -> None:
    """Sous le seuil de l'application, le shadow ne conclut pas."""
    juge = FakeJugeClient(modele_id=MOYEN_MODELE, verdict_defaut=VerdictJuge(equivalent=False))
    moteur, _, _ = monter(tarifs, juge=juge)
    plan = moteur.preparer(
        tenant_id=tenant.id,
        application_id="ticket-classifier",
        observations=observations(),
        parametres=parametres(tenant),
    )
    bilan = moteur.executer(plan)
    assert bilan.taux_equivalence == 0.0
    assert bilan.concluant is False


def test_panne_du_candidat_est_comptee(tenant: Tenant, tarifs) -> None:
    """Un candidat en panne n'interrompt pas la mesure : l'erreur est comptée."""
    modele = FakeModeleClient(modeles_en_erreur={PETIT_MODELE})
    moteur, _, _ = monter(tarifs, client=modele)
    plan = moteur.preparer(
        tenant_id=tenant.id,
        application_id="ticket-classifier",
        observations=observations(),
        parametres=parametres(tenant),
    )
    bilan = moteur.executer(plan)
    assert bilan.erreurs_candidat == 10
    assert bilan.nombre_paires == 0
    assert bilan.concluant is False


def test_bornes_invalides_refusees() -> None:
    """Une borne nulle ou négative est refusée (donnée invalide)."""
    with pytest.raises(ValidationError):
        BornesEchantillon(taille_max=0)
    with pytest.raises(ValidationError):
        BornesEchantillon(taille_max=10, taux=1.5)
