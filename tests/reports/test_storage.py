"""Tests des rendus et de l'archivage (EF-RAP-01, EF-RAP-03).

Markdown et JSON sont produits sans réseau : le gabarit est un fichier du
paquet, le dépôt S3 n'est jamais instancié pour de vrai (boto3 n'est chargé
qu'au premier appel, et les tests utilisent `DepotMemoire`).
"""

from __future__ import annotations

import json

import pytest

from jauge.reports.builder import construire_rapport
from jauge.reports.storage import (
    CONTENT_TYPE_JSON,
    CONTENT_TYPE_MARKDOWN,
    DepotMemoire,
    DepotS3,
    cle_rapport,
    publier,
    rendre_json,
    rendre_markdown,
    vers_dict,
)
from jauge.store.memory import MemoryStore

MOIS = (2026, 10)


@pytest.fixture
def rapport(store_peuple: MemoryStore, tarifs_table: list):
    """Rapport d'octobre 2026 complet, table des tarifs datée incluse."""
    return construire_rapport(store_peuple, "t-demo-a", *MOIS, tarifs=tarifs_table)


def test_markdown_contient_les_sections_attendues(rapport) -> None:
    """Le gabarit produit un document lisible par un directeur financier."""
    markdown = rendre_markdown(rapport)
    titres = [
        "# Bedrock AI cost report — Client A — Démo",
        "## 2. Key figures",
        "## 3. Spend by application",
        "## 4. Spend by team",
        "## 5. Spend by model",
        "## 6. Bill reconciliation",
        "## 7. Savings levers",
        "## 8. Savings — expected and verified",
        "## 9. Recommendations awaiting approval",
        "## 10. Staged rollouts",
        "## Appendix A — Methodology",
    ]
    for titre in titres:
        assert titre in markdown


def test_markdown_formate_les_chiffres(rapport) -> None:
    """Les montants, parts et volumes sont formatés, sans recalcul."""
    markdown = rendre_markdown(rapport)
    assert "55.00 USD" in markdown
    assert "1.79%" in markdown
    assert "37.50%" in markdown
    assert "9.50" in markdown
    assert "12.00" in markdown
    assert "200 requests" in markdown
    assert "2026-01-01" in markdown


def test_markdown_traduit_les_ratios_et_le_sens(rapport) -> None:
    """Ratios (0–1) rendus en pourcentage, sens de tendance en anglais."""
    markdown = rendre_markdown(rapport)
    assert "95.00%" in markdown  # seuil de qualité, stocké en ratio
    assert "85.00%" in markdown  # confiance du levier, stockée en ratio
    assert "increase" in markdown
    assert "hausse" not in markdown
    assert "0.85%" not in markdown


def test_markdown_traduit_les_libelles_metier(rapport) -> None:
    """Les leviers et statuts apparaissent en anglais, jamais en code brut."""
    markdown = rendre_markdown(rapport)
    assert "Oversized model" in markdown
    assert "No prompt cache" in markdown
    assert "Applied" in markdown
    assert "modele_surdimensionne" not in markdown
    assert "Distillation candidate" not in markdown  # levier rejeté, exclu


def test_markdown_signale_les_deux_familles_d_economies(rapport) -> None:
    """Attendues et constatées sont présentées distinctement, avec leur écart."""
    markdown = rendre_markdown(rapport)
    assert "Expected savings (approved recommendations)" in markdown
    assert "Verified savings (invoice)" in markdown
    assert "Gap between expected and verified" in markdown
    assert "2.50" in markdown


def test_markdown_sans_synthese_reste_valide(rapport) -> None:
    """Un rapport sans synthèse s'affiche quand même (repli explicite)."""
    assert "No narrative summary was attached" in rendre_markdown(rapport)


def test_json_est_un_contrat_stable(rapport) -> None:
    """Le JSON est parsable, versionné et expose les blocs du portail."""
    charge = json.loads(rendre_json(rapport))
    assert charge["schema_version"] == "1.0"
    assert charge["tenant_id"] == "t-demo-a"
    assert charge["annee"] == 2026
    assert charge["mois"] == 10
    assert charge["periode_debut"] == "2026-10-01"
    assert charge["cout_total_usd"] == pytest.approx(55.0)
    assert charge["economies"]["attendues_usd"] == pytest.approx(12.0)
    assert charge["economies"]["constatees_usd"] == pytest.approx(9.5)
    assert charge["ecart_facture"]["ecart_pourcentage"] == pytest.approx(1.79)
    assert charge["methodologie"]["date_table_tarifs"] == "2026-01-01"
    assert charge["synthese"] is None


def test_json_et_markdown_portent_les_memes_chiffres(rapport) -> None:
    """Aucune divergence entre les deux rendus : même source, mêmes valeurs."""
    charge = json.loads(rendre_json(rapport))
    markdown = rendre_markdown(rapport)
    assert f"{charge['cout_total_usd']:,.2f} USD" in markdown
    assert f"{charge['economies']['constatees_usd']:,.2f}" in markdown


def test_vers_dict_rend_des_types_json(rapport) -> None:
    """`vers_dict` produit des types sérialisables (dates en ISO)."""
    charge = vers_dict(rapport)
    assert isinstance(charge["periode_fin"], str)
    assert json.dumps(charge)  # sérialisable sans adaptateur


def test_cle_de_rapport_est_deterministe(rapport) -> None:
    """La clé d'archivage range les rapports par client et par mois."""
    assert cle_rapport(rapport, "md") == "reports/t-demo-a/2026-10/report.md"
    assert cle_rapport(rapport, "json", "finops") == "finops/t-demo-a/2026-10/report.json"


def test_publier_depose_markdown_et_json(rapport) -> None:
    """`publier` archive les deux formats et rend les clés écrites."""
    depot = DepotMemoire()
    cles = publier(rapport, depot)
    assert cles == ["reports/t-demo-a/2026-10/report.md", "reports/t-demo-a/2026-10/report.json"]
    assert depot.objets[cles[0]][1] == CONTENT_TYPE_MARKDOWN
    assert depot.objets[cles[1]][1] == CONTENT_TYPE_JSON
    assert depot.lire(cles[0]) == rendre_markdown(rapport)


def test_publication_idempotente(rapport) -> None:
    """Republier le même rapport écrase sans dupliquer."""
    depot = DepotMemoire()
    publier(rapport, depot)
    publier(rapport, depot)
    assert len(depot.objets) == 2


def test_rendu_sans_reseau(rapport, monkeypatch) -> None:
    """Aucun client boto3 n'est construit pour rendre ou archiver en mémoire."""
    import jauge.reports.storage as storage

    def refus(*args: object, **kwargs: object):
        raise AssertionError("boto3 ne doit pas être appelé par le rendu")

    monkeypatch.setattr("boto3.client", refus, raising=False)
    depot = DepotMemoire()
    publier(rapport, depot)
    assert storage.CONTENT_TYPE_MARKDOWN in depot.objets["reports/t-demo-a/2026-10/report.md"][1]


class ClientS3Factice:
    """Client S3 factice : note les dépôts, sans réseau."""

    def __init__(self) -> None:
        self.appels: list[dict] = []

    def put_object(self, **parametres: object) -> dict:
        self.appels.append(parametres)
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}


def test_depot_s3_testé_par_son_interface(rapport) -> None:
    """L'adaptateur S3 est éprouvé via un client factice, sans réseau."""
    client = ClientS3Factice()
    depot = DepotS3("bucket-demo", prefixe="finops", client=client)
    cles = publier(rapport, depot)
    assert cles == ["finops/t-demo-a/2026-10/report.md", "finops/t-demo-a/2026-10/report.json"]
    assert client.appels[0]["Bucket"] == "bucket-demo"
    assert client.appels[0]["ServerSideEncryption"] == "aws:kms"
    assert client.appels[1]["ContentType"] == CONTENT_TYPE_JSON
