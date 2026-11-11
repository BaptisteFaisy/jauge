"""Lignes CUR 2.0 synthétiques, cohérentes avec le trafic de la démo (§8.2).

Le CUR 2.0 est l'export de facturation détaillé d'AWS : une ligne par usage
facturé. Ces lignes sont **fabriquées à partir du trafic synthétique**, afin
que le rapprochement du chapitre 8.2 ait un sens : le total facturé reconstitué
est proche du coût recalculé depuis les journaux d'invocation.

COLONNES (noms de la spécification CUR 2.0, en `snake_case`)

    bill_id, payer_account_id, line_item_usage_start_date,
    line_item_usage_end_date, product_code, product_family,
    line_item_usage_type, line_item_operation, resource_id,
    line_item_usage_amount, line_item_unblended_cost, region,
    service_tier, model_id, resource_tags_user_application,
    resource_tags_user_team, resource_tags_user_environment

Les trois dernières colonnes `resource_tags_user_*` correspondent aux
étiquettes utilisateur d'allocation des coûts (application, équipe,
environnement) : dans un export CUR 2.0 réel, une étiquette `user:<clé>` est
normalisée en colonne `resource_tags/user:<clé>`, d'où ce nommage — c'est celui
que reconnaît le parseur CUR de M1 (`CHAMPS_CUR`, `jauge/attribution/reconcile.py`).
Dans un compte réel, ces valeurs proviennent du profil d'inférence d'application
et des étiquettes de la requête, pas du CUR lui-même.

Le montant facturé d'une journée est le recalcul du trafic **majoré d'un
écart déterministe de moins de 1,2 %** : le rapprochement sort « conforme »
(cible de production : moins de 3 %, §4).

Aucune donnée réelle : tous les montants sont calculés par le code.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from demo.profils import REGION
from demo.socle import charger_socle_demo
from demo.traffic import JournalInvocation
from jauge.common.ids import graine_de
from jauge.engine.calculator import TableTarifs, calculer_cout_requete
from jauge.models.pricing import MetriquesRequete, ServiceTier

# Écart déterministe entre facture et recalcul (reste dans la marge de 3 %).
ECART_MAX_POURCENT = 1.2

# (clé de métrique, libellé du type d'usage CUR)
COMPOSANTES = (
    ("entree", "Input Tokens"),
    ("sortie", "Output Tokens"),
    ("cache_ecriture", "Cache Write Tokens"),
    ("cache_lecture", "Cache Read Tokens"),
)


@dataclass(frozen=True)
class LigneCur:
    """Une ligne d'export CUR 2.0 (usage facturé)."""

    bill_id: str
    payer_account_id: str
    line_item_usage_start_date: str
    line_item_usage_end_date: str
    product_code: str
    product_family: str
    line_item_usage_type: str
    line_item_operation: str
    resource_id: str
    line_item_usage_amount: float
    line_item_unblended_cost: float
    region: str
    service_tier: str
    model_id: str
    resource_tags_user_application: str
    resource_tags_user_team: str
    resource_tags_user_environment: str

    def vers_dictionnaire(self) -> dict[str, Any]:
        """Ligne sous forme de dictionnaire (colonnes du CUR 2.0)."""
        return {
            "bill_id": self.bill_id,
            "payer_account_id": self.payer_account_id,
            "line_item_usage_start_date": self.line_item_usage_start_date,
            "line_item_usage_end_date": self.line_item_usage_end_date,
            "product_code": self.product_code,
            "product_family": self.product_family,
            "line_item_usage_type": self.line_item_usage_type,
            "line_item_operation": self.line_item_operation,
            "resource_id": self.resource_id,
            "line_item_usage_amount": self.line_item_usage_amount,
            "line_item_unblended_cost": self.line_item_unblended_cost,
            "region": self.region,
            "service_tier": self.service_tier,
            "model_id": self.model_id,
            "resource_tags_user_application": self.resource_tags_user_application,
            "resource_tags_user_team": self.resource_tags_user_team,
            "resource_tags_user_environment": self.resource_tags_user_environment,
        }


def generer_lignes_cur(
    records: list[JournalInvocation],
    table_tarifs: TableTarifs,
    graine: int = 0,
    comptes: dict[str, str] | None = None,
) -> list[LigneCur]:
    """Reconstitue l'export CUR 2.0 à partir du trafic synthétique.

    `comptes` associe un identifiant de client à son compte AWS fictif ;
    il est déduit des fixtures du socle quand il n'est pas fourni.
    """
    comptes = comptes or _comptes_par_defaut()
    lignes: list[LigneCur] = []
    for cle, groupe in _grouper(records).items():
        lignes.extend(_lignes_du_groupe(cle, groupe, table_tarifs, graine, comptes))
    lignes.sort(
        key=lambda ligne: (
            ligne.line_item_usage_start_date,
            ligne.resource_tags_user_application,
        )
    )
    return lignes


def facture_par_jour(lignes: list[LigneCur], compte_aws: str) -> dict[date, float]:
    """Total facturé par jour pour un compte (somme des coûts non amortis)."""
    totaux: dict[date, float] = {}
    for ligne in lignes:
        if ligne.payer_account_id != compte_aws:
            continue
        jour = date.fromisoformat(ligne.line_item_usage_start_date[:10])
        totaux[jour] = totaux.get(jour, 0.0) + ligne.line_item_unblended_cost
    return totaux


def lignes_en_json(lignes: list[LigneCur]) -> list[str]:
    """Sérialise les lignes CUR en JSON (une chaîne par objet)."""
    return [
        json.dumps(ligne.vers_dictionnaire(), ensure_ascii=False, sort_keys=True)
        for ligne in lignes
    ]


# --- Interne ------------------------------------------------------------------


def _comptes_par_defaut() -> dict[str, str]:
    """Comptes AWS fictifs des clients, lus dans les fixtures du socle."""
    socle = charger_socle_demo()
    return {
        socle.tenant_a.id: socle.tenant_a.compte_aws,
        socle.tenant_b.id: socle.tenant_b.compte_aws,
    }


def _grouper(records: list[JournalInvocation]) -> dict[tuple, list[JournalInvocation]]:
    """Regroupe les enregistrements par (client, jour, application, modèle, niveau)."""
    groupes: dict[tuple, list[JournalInvocation]] = {}
    for record in records:
        cle = (
            record.tenant_id,
            record.event_time.date(),
            record.application_id,
            record.modele_id,
            record.niveau,
        )
        groupes.setdefault(cle, []).append(record)
    return groupes


def _lignes_du_groupe(
    cle: tuple,
    groupe: list[JournalInvocation],
    table_tarifs: TableTarifs,
    graine: int,
    comptes: dict[str, str],
) -> list[LigneCur]:
    """Produit les lignes CUR d'un groupe (une par composante de coût)."""
    tenant_id, jour, application_id, modele_id, niveau = cle
    premier = groupe[0]
    tarif = table_tarifs.trouver(modele_id, REGION, niveau, jour)
    metriques = _metriques_agregees(groupe)
    cout = calculer_cout_requete(tarif, metriques)
    facteur = _facteur_ecart(tenant_id, jour, graine)
    quantites = {
        "entree": metriques.jetons_entree,
        "sortie": metriques.jetons_sortie,
        "cache_ecriture": metriques.jetons_cache_ecriture,
        "cache_lecture": metriques.jetons_cache_lecture,
    }
    montants = {
        "entree": cout.cout_entree,
        "sortie": cout.cout_sortie,
        "cache_ecriture": cout.cout_cache_ecriture,
        "cache_lecture": cout.cout_cache_lecture,
    }
    lignes: list[LigneCur] = []
    for cle_composante, libelle_usage in COMPOSANTES:
        quantite = quantites[cle_composante]
        if quantite <= 0 or montants[cle_composante] <= 0:
            continue
        lignes.append(
            _ligne(
                tenant_id=tenant_id,
                jour=jour,
                application_id=application_id,
                modele_id=modele_id,
                niveau=niveau,
                libelle_usage=libelle_usage,
                quantite=quantite,
                montant=montants[cle_composante] * facteur,
                equipe=premier.equipe,
                environnement=premier.environnement,
                comptes=comptes,
            )
        )
    return lignes


def _metriques_agregees(groupe: list[JournalInvocation]) -> MetriquesRequete:
    """Somme des métriques d'un groupe d'enregistrements."""
    return MetriquesRequete(
        jetons_entree=sum(r.jetons_entree for r in groupe),
        jetons_sortie=sum(r.jetons_sortie for r in groupe),
        jetons_cache_ecriture=sum(r.jetons_cache_ecriture for r in groupe),
        jetons_cache_lecture=sum(r.jetons_cache_lecture for r in groupe),
        latence_ms=0,
    )


def _facteur_ecart(tenant_id: str, jour: date, graine: int) -> float:
    """Écart déterministe entre facture et recalcul (moins de 1,2 %)."""
    rnd = random.Random(graine_de(graine, "cur", tenant_id, jour.isoformat()))
    amplitude = ECART_MAX_POURCENT / 100.0
    return 1.0 + rnd.uniform(-amplitude, amplitude)


def _ligne(
    *,
    tenant_id: str,
    jour: date,
    application_id: str,
    modele_id: str,
    niveau: ServiceTier,
    libelle_usage: str,
    quantite: int,
    montant: float,
    equipe: str,
    environnement: str,
    comptes: dict[str, str],
) -> LigneCur:
    """Construit une ligne CUR 2.0."""
    debut = datetime(jour.year, jour.month, jour.day, tzinfo=UTC)
    fin = debut + timedelta(days=1)
    return LigneCur(
        bill_id=f"Jauge-DEMO-{debut:%Y%m}",
        payer_account_id=comptes[tenant_id],
        line_item_usage_start_date=debut.strftime("%Y-%m-%dT%H:%M:%SZ"),
        line_item_usage_end_date=fin.strftime("%Y-%m-%dT%H:%M:%SZ"),
        product_code="AmazonBedrock",
        product_family="Amazon Bedrock",
        line_item_usage_type=f"{_libelle_modele(modele_id)} {libelle_usage}",
        line_item_operation="InvokeModel",
        resource_id=application_id,
        line_item_usage_amount=float(quantite),
        line_item_unblended_cost=round(montant, 8),
        region=REGION,
        service_tier=niveau.value.capitalize(),
        model_id=modele_id,
        resource_tags_user_application=application_id,
        resource_tags_user_team=equipe,
        resource_tags_user_environment=environnement,
    )


def _libelle_modele(modele_id: str) -> str:
    """Libellé lisible du modèle, pour le type d'usage CUR."""
    parties = modele_id.split(".")
    if len(parties) < 2:
        return "Bedrock Model"
    libelle = parties[1].split(":")[0].replace("-", " ").title()
    return libelle or "Bedrock Model"
