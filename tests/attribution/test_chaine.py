"""Test de bout en bout de la chaîne d'attribution (§8.2, EF-ATT-05..08).

Le parcours complet de la spécification, sur les fixtures du module :

1. les journaux d'invocation du jour sont lus (modèle, jetons, cache, niveau de
   service, étiquettes) ;
2. chaque requête reçoit un coût de la table des tarifs (socle) ;
3. les coûts sont agrégés par application, équipe, environnement, modèle, jour ;
4. le total recalculé est comparé au total facturé de l'export CUR 2.0 ;
5. la part non attribuée et ses causes sont affichées.

Aucun appel réseau, aucun service AWS : tout vient des fixtures JSON locales.
"""

from __future__ import annotations

import pytest
from tests.attribution.conftest import CUR, JOUR, JOURNAUX

from jauge.attribution.aggregate import AgregateurQuotidien, agregats_par_dimension
from jauge.attribution.logs import RapportLecture, lire_journaux
from jauge.attribution.reconcile import (
    StatistiquesCur,
    facture_par_jour,
    lire_fichier_cur,
    rapprocher_periode,
)
from jauge.attribution.tags import PointAcces
from jauge.engine.calculator import TableTarifs
from jauge.models.cost import AgregatQuotidien, RapprochementJour
from jauge.models.tenant import Application, Tenant


def test_chaine_complete_du_jour(
    tenant: Tenant, tarifs_demo: TableTarifs, applications: list[Application]
) -> None:
    """Journaux → coûts → agrégats → rapprochement, sur une journée de démo."""
    # 1-3 : lecture des journaux et agrégation par application, équipe, modèle, jour.
    rapport = RapportLecture()
    agregateur = AgregateurQuotidien(tenant, tarifs_demo, applications=applications)
    resultat = agregateur.agreger(lire_journaux(JOURNAUX, rapport), rapport)

    assert rapport.exploitables == 6
    assert all(isinstance(agregat, AgregatQuotidien) for agregat in resultat.agregats)
    assert all(agregat.jour == JOUR for agregat in resultat.agregats)
    assert resultat.cout_recalcule_usd > 0

    # 4 : rapprochement avec la facture CUR 2.0 du même jour.
    stats_cur = StatistiquesCur()
    lignes = list(lire_fichier_cur(CUR, stats_cur))
    factures = facture_par_jour(lignes)
    rapprochements = rapprocher_periode([resultat], factures.values(), tenant.id)

    assert stats_cur.retenues == 5
    assert len(rapprochements) == 1
    rapprochement = rapprochements[0]
    assert isinstance(rapprochement, RapprochementJour)
    assert rapprochement.jour == JOUR
    assert rapprochement.cout_facture_usd == 0.136

    # 5 : la part non attribuée et ses causes accompagnent l'écart.
    assert rapprochement.part_non_attribuee_pourcentage > 0
    assert rapprochement.causes_non_attribuees


def test_couverture_par_application(
    tenant: Tenant, tarifs_demo: TableTarifs, applications: list[Application]
) -> None:
    """Les trois applications client et la ligne non attribuée sont couvertes."""
    agregateur = AgregateurQuotidien(tenant, tarifs_demo, applications=applications)
    resultat = agregateur.agreger(lire_journaux(JOURNAUX))

    par_application = agregats_par_dimension(resultat.agregats, "application")

    assert set(par_application) == {
        "ticket-classifier",
        "support-assistant",
        "resume-nocturne",
        "non-attribue",
    }
    assert sum(par_application.values()) > 0


def test_niveau_flex_moins_cher_que_standard(
    tenant: Tenant, tarifs_demo: TableTarifs, applications: list[Application]
) -> None:
    """Le niveau Flex journalisé est bien facturé au tarif Flex (§3.2)."""
    agregateur = AgregateurQuotidien(tenant, tarifs_demo, applications=applications)
    resultat = agregateur.agreger(lire_journaux(JOURNAUX))

    # req-0002 est la seule requête Flex du journal : 900 jetons en entrée,
    # 60 en sortie, 300 lus du cache, 100 écrits au cache (tarif Flex).
    flex = 900 / 1000 * 0.0075 + 60 / 1000 * 0.0375 + 300 / 1000 * 0.0 + 100 / 1000 * 0.0
    assistant = next(a for a in resultat.agregats if a.application_id == "support-assistant")

    assert assistant.cout_recalcule_usd == pytest.approx(round(flex, 4))


def test_points_d_acces_journalises_dans_la_fixture(
    tenant: Tenant, tarifs_demo: TableTarifs
) -> None:
    """Tous les enregistrements de la fixture passent par `bedrock-runtime`."""
    enregistrements = list(lire_journaux(JOURNAUX))

    assert all(
        enregistrement.point_acces is PointAcces.RUNTIME for enregistrement in enregistrements
    )
    assert all(enregistrement.point_acces.journalise for enregistrement in enregistrements)
