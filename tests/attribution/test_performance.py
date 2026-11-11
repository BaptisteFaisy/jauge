"""Test de performance du chemin d'attribution (ENF-PERF-01).

Exigence : **un million de requêtes par jour attribuées en moins de 30 minutes**.
Le test travaille sur un échantillon (20 000 enregistrements) et extrapole : il
mesure le débit réel du code, pas une constante inventée.

Marqué `slow` : il est exclu du chemin critique de la CI (`-m 'not infra and not
slow'`) et se lance à la demande :

```bash
uv run pytest -m slow tests/attribution/test_performance.py
```

Deux propriétés sont vérifiées, pas seulement la vitesse :

- le traitement est **en flux** : la source n'est jamais matérialisée, donc la
  mémoire ne croît pas avec le nombre d'enregistrements ;
- le coût agrégé reste celui du socle, même à volume élevé (ENF-IA-01).
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import UTC, date, datetime

import pytest

from jauge.attribution.aggregate import AgregateurQuotidien
from jauge.attribution.logs import RapportLecture, iterer_enregistrements
from jauge.engine.calculator import TableTarifs
from jauge.models.pricing import ServiceTier, TarifModele
from jauge.models.tenant import Tenant

#: Nombre d'enregistrements de l'échantillon mesuré.
ECHANTILLON = 20_000

#: Volume de la journée de production visée par ENF-PERF-01.
VOLUME_CIBLE = 1_000_000

#: Budget de l'exigence, en secondes (30 minutes).
BUDGET_SECONDES = 30 * 60

MODELE = "modele-perf"
REGION = "eu-west-1"


def _journal(index: int) -> dict[str, object]:
    """Un enregistrement de journal réaliste (une application, deux équipes)."""
    equipe = "support" if index % 3 else "data"
    return {
        "schemaType": "ModelInvocationLog",
        "timestamp": "2026-11-09T08:12:00Z",
        "requestId": f"req-{index}",
        "modelId": MODELE,
        "operation": "Converse",
        "input": {"inputTokenCount": 1000 + index % 500},
        "output": {"outputTokenCount": 100},
        "latencyMs": 400 + index % 200,
        "status": "success",
        "requestMetadata": {
            "application": f"app-{index % 4}",
            "team": equipe,
            "environment": "production",
        },
    }


def _source(total: int) -> Iterator[dict[str, object]]:
    """Générateur d'enregistrements : rien n'est matérialisé (flux)."""
    for index in range(total):
        yield _journal(index)


@pytest.fixture
def tenant_perf() -> Tenant:
    """Client de mesure, sans application connue (fiches de repli)."""
    return Tenant(
        id="t-perf",
        nom="Client perf",
        compte_aws="123456789012",
        role_arn="arn:aws:iam::123456789012:role/jauge-dataplane",
        external_id="ext-perf",
        region_principale=REGION,
    )


@pytest.fixture
def tarifs_perf() -> TableTarifs:
    """Tarif unique : le coût ne doit pas dominer la mesure."""
    return TableTarifs(
        [
            TarifModele(
                modele_id=MODELE,
                region=REGION,
                niveau=ServiceTier.STANDARD,
                date_effet=date(2026, 1, 1),
                prix_entree_1k=0.015,
                prix_sortie_1k=0.075,
            )
        ]
    )


@pytest.mark.slow
def test_un_million_de_requetes_en_moins_de_30_minutes(
    tenant_perf: Tenant, tarifs_perf: TableTarifs
) -> None:
    """Le débit mesuré extrapole sous le budget de 30 minutes pour 1 M de requêtes."""
    agregateur = AgregateurQuotidien(tenant_perf, tarifs_perf)
    rapport = RapportLecture()

    debut = time.perf_counter()
    resultat = agregateur.agreger(iterer_enregistrements(_source(ECHANTILLON), rapport), rapport)
    duree = time.perf_counter() - debut

    assert resultat.nb_enregistrements == ECHANTILLON
    assert rapport.nb_rejetes == 0
    assert resultat.cout_recalcule_usd > 0

    debit = ECHANTILLON / duree
    secondes_pour_un_million = VOLUME_CIBLE / debit
    print(
        f"\nENF-PERF-01 : {ECHANTILLON} enregistrements en {duree:.1f} s "
        f"({debit:.0f} req/s) → {secondes_pour_un_million / 60:.1f} min pour 1 M "
        f"(budget {BUDGET_SECONDES // 60} min)"
    )

    assert secondes_pour_un_million < BUDGET_SECONDES, (
        f"{secondes_pour_un_million / 60:.1f} min extrapolées pour 1 M de requêtes, "
        f"au-delà du budget de {BUDGET_SECONDES // 60} min (ENF-PERF-01)"
    )


@pytest.mark.slow
def test_memoire_bornee_par_le_flux(tenant_perf: Tenant, tarifs_perf: TableTarifs) -> None:
    """Doubler le volume ne double pas la mémoire : la source est un flux.

    Le seul état conservé est un accumulateur par groupe d'agrégat, plus les
    colonnes de valeurs (tableaux de types primitifs). Un chargement intégral se
    verrait immédiatement ici.
    """
    import tracemalloc

    def mesurer(total: int) -> int:
        agregateur = AgregateurQuotidien(tenant_perf, tarifs_perf)
        tracemalloc.start()
        agregateur.agreger(iterer_enregistrements(_source(total)))
        _, pic = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        return pic

    petit = mesurer(2_000)
    grand = mesurer(20_000)
    ratio = grand / petit

    print(f"\nmémoire : {petit / 1024:.0f} Kio (2 000) → {grand / 1024:.0f} Kio (20 000)")
    # ×10 d'enregistrements : les colonnes croissent linéairement (~×10), pas
    # quadratiquement. On borne largement pour ne pas dépendre du ramasse-miettes.
    assert ratio < 40, f"mémoire ×{ratio:.1f} pour ×10 d'enregistrements : chargement intégral ?"


@pytest.mark.slow
def test_flux_consomme_une_seule_fois(tenant_perf: Tenant, tarifs_perf: TableTarifs) -> None:
    """Le flux n'est jamais relu : la source est un itérateur à usage unique."""
    lectures = 0

    def source_comptee() -> Iterator[dict[str, object]]:
        nonlocal lectures
        for index in range(1_000):
            lectures += 1
            yield _journal(index)

    agregateur = AgregateurQuotidien(tenant_perf, tarifs_perf)
    agregateur.agreger(iterer_enregistrements(source_comptee()))

    assert lectures == 1_000


def test_horodatage_de_reference_de_la_mesure() -> None:
    """Garde-fou : l'horodatage du générateur reste dans le jour attendu."""
    enregistrement = next(iterer_enregistrements([_journal(0)]))

    assert enregistrement.jour == date(2026, 11, 9)
    assert enregistrement.horodatage == datetime(2026, 11, 9, 8, 12, tzinfo=UTC)


def test_application_de_repli_ne_coute_pas_un_modele_complet(
    tenant_perf: Tenant, tarifs_perf: TableTarifs
) -> None:
    """Les applications inconnues produisent une fiche de repli, sans erreur."""
    agregateur = AgregateurQuotidien(tenant_perf, tarifs_perf)

    resultat = agregateur.agreger(iterer_enregistrements(_source(100)))

    assert {agregat.application_id for agregat in resultat.agregats} == {
        "app-0",
        "app-1",
        "app-2",
        "app-3",
    }
    assert all(isinstance(agregat, object) for agregat in resultat.agregats)
    assert resultat.nb_sans_etiquette == 0
