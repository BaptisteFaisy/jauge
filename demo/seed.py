"""Amorçage du store de démonstration (§18.1).

`peupler_store` remplit un `jauge.store.memory.MemoryStore` **via l'API
publique du socle uniquement** : deux clients, quatre applications, les
agrégats quotidiens sur 14 jours, les rapprochements quotidiens, les leviers
détectés, une campagne de rejeu par application prouvée, les recommandations,
une bascule en cours et sa vérification d'économie.

Les deux clients, deux des applications et la table des tarifs viennent des
fixtures du socle (`tests/conftest.py`, rechargées sans être modifiées) ; les
deux autres applications de démonstration sont définies dans `demo.socle`.

`python -m demo.seed` remplit un store en mémoire et affiche un résumé console
(dépense par application, leviers, économie attendue).

Aucune donnée réelle : tout est généré par `demo.traffic` et `demo.cur`.
"""

from __future__ import annotations

from datetime import date
from functools import cache

from demo.analyse import analyser_trafic, construire_leviers
from demo.cur import facture_par_jour, generer_lignes_cur
from demo.profils import (
    DATE_FIN_DEFAUT,
    GRAINE_DEFAUT,
    JOURS_DEFAUT,
    PROFILS,
    TENANT_A,
)
from demo.scenario import (
    construire_bascules,
    construire_campagnes,
    construire_recommandations,
    construire_verifications,
)
from demo.socle import SocleDemo, charger_socle_demo
from demo.traffic import JournalInvocation, generer_trafic
from jauge.engine.attribution import agreger_requetes, rapprocher_facture
from jauge.engine.calculator import TableTarifs, calculer_cout_requete
from jauge.models.cost import AgregatQuotidien
from jauge.models.tenant import Application, Tenant
from jauge.store.memory import MemoryStore
from jauge.store.protocol import Store

__all__ = [
    "SocleDemo",
    "charger_socle_demo",
    "comptes_demo",
    "construire_agregats",
    "main",
    "peupler_store",
    "resume",
]


@cache
def comptes_demo() -> dict[str, str]:
    """Compte AWS fictif de chaque client, lu dans les fixtures du socle."""
    socle = charger_socle_demo()
    return {
        socle.tenant_a.id: socle.tenant_a.compte_aws,
        socle.tenant_b.id: socle.tenant_b.compte_aws,
    }


def peupler_store(
    store: Store,
    graine: int = GRAINE_DEFAUT,
    jours: int = JOURS_DEFAUT,
    date_fin: date = DATE_FIN_DEFAUT,
) -> None:
    """Remplit le store de démonstration (API publique du socle seulement)."""
    socle = charger_socle_demo()
    _enregistrer_referentiel(store, socle)
    records = generer_trafic(graine=graine, jours=jours, date_fin=date_fin)
    table = TableTarifs(socle.tarifs)
    agregats = construire_agregats(records, socle, table)
    store.enregistrer_agregats(agregats)
    _enregistrer_rapprochements(store, socle, records, agregats, table, graine)
    stats = analyser_trafic(records, table, PROFILS, jours, date_fin)
    for levier in construire_leviers(stats, table, date_fin):
        store.enregistrer_levier(levier)
    campagnes = construire_campagnes(stats, table, date_fin)
    for campagne in campagnes:
        store.enregistrer_campagne(campagne)
    recos = construire_recommandations(stats, campagnes)
    for reco in recos:
        store.enregistrer_recommandation(reco)
    for bascule in construire_bascules(recos):
        store.enregistrer_bascule(bascule)
    for verif in construire_verifications(recos):
        store.enregistrer_verification(verif)


def _enregistrer_referentiel(store: Store, socle: SocleDemo) -> None:
    """Enregistre les deux clients et leurs quatre applications."""
    store.enregistrer_tenant(socle.tenant_a)
    store.enregistrer_tenant(socle.tenant_b)
    for application in socle.applications.values():
        store.enregistrer_application(application)


def construire_agregats(
    records: list[JournalInvocation], socle: SocleDemo, table: TableTarifs
) -> list[AgregatQuotidien]:
    """Agrège le trafic par application et par jour (§8.2, EF-ATT-05)."""
    agregats: list[AgregatQuotidien] = []
    for application in socle.applications.values():
        tenant = socle.tenant_a if application.tenant_id == TENANT_A else socle.tenant_b
        agregats.extend(_agregats_application(records, tenant, application, table))
    return agregats


def _agregats_application(
    records: list[JournalInvocation],
    tenant: Tenant,
    application: Application,
    table: TableTarifs,
) -> list[AgregatQuotidien]:
    """Agrégats quotidiens d'une application."""
    par_jour: dict[date, list[JournalInvocation]] = {}
    for record in records:
        if record.application_id != application.id:
            continue
        par_jour.setdefault(record.event_time.date(), []).append(record)
    agregats: list[AgregatQuotidien] = []
    for jour in sorted(par_jour):
        lignes = par_jour[jour]
        tarif = table.trouver(
            application.modele_actuel, tenant.region_principale, lignes[0].niveau, jour
        )
        couples = [(r.metriques(), calculer_cout_requete(tarif, r.metriques())) for r in lignes]
        agregats.append(agreger_requetes(tenant, application, jour, couples))
    return agregats


def _enregistrer_rapprochements(
    store: Store,
    socle: SocleDemo,
    records: list[JournalInvocation],
    agregats: list[AgregatQuotidien],
    table: TableTarifs,
    graine: int,
) -> None:
    """Compare, jour par jour, le recalcul des journaux et la facture CUR."""
    lignes_cur = generer_lignes_cur(records, table, graine, comptes_demo())
    for tenant in (socle.tenant_a, socle.tenant_b):
        facture = facture_par_jour(lignes_cur, tenant.compte_aws)
        recalcul: dict[date, float] = {}
        for agregat in agregats:
            if agregat.tenant_id != tenant.id:
                continue
            recalcul[agregat.jour] = recalcul.get(agregat.jour, 0.0) + agregat.cout_recalcule_usd
        for jour in sorted(recalcul):
            store.enregistrer_rapprochement(
                rapprocher_facture(
                    tenant_id=tenant.id,
                    jour=jour,
                    cout_recalcule=recalcul[jour],
                    cout_facture=facture.get(jour, 0.0),
                )
            )


# --- Résumé console -----------------------------------------------------------


def resume(store: Store, jours: int = JOURS_DEFAUT) -> str:
    """Résumé console : dépense par application, leviers, économie attendue."""
    lignes: list[str] = ["Jauge — démonstration hors ligne", ""]
    for tenant in store.lister_tenants():
        lignes.extend(_resume_client(store, tenant, jours))
        lignes.append("")
    lignes.extend(_resume_leviers(store))
    lignes.extend(_resume_decisions(store))
    return "\n".join(lignes)


def _resume_client(store: Store, tenant: Tenant, jours: int) -> list[str]:
    """Tableau de la dépense d'un client, application par application."""
    agregats = store.lister_agregats(tenant.id)
    lignes = [f"{tenant.nom} (compte {tenant.compte_aws})"]
    lignes.append(
        f"  {'application':<20} {'requêtes':>9} {'dépense période':>16} "
        f"{'projection/mois':>16} {'leviers':>8}"
    )
    total_requetes = 0
    total_cout = 0.0
    for application in store.lister_applications(tenant.id):
        siens = [a for a in agregats if a.application_id == application.id]
        requetes = sum(a.nombre_requetes for a in siens)
        cout = sum(a.cout_recalcule_usd for a in siens)
        nombre_leviers = len(store.lister_leviers(tenant.id, application.id))
        total_requetes += requetes
        total_cout += cout
        lignes.append(
            f"  {application.id:<20} {requetes:>9} {cout:>15.2f} $ "
            f"{_projection(cout, jours):>15.2f} $ {nombre_leviers:>8}"
        )
    lignes.append(
        f"  {'total':<20} {total_requetes:>9} {total_cout:>15.2f} $ "
        f"{_projection(total_cout, jours):>15.2f} $"
    )
    rapprochements = store.lister_rapprochements(tenant.id)
    if rapprochements:
        conformes = sum(1 for r in rapprochements if r.conforme)
        ecart = sum(r.ecart_pourcentage for r in rapprochements) / len(rapprochements)
        lignes.append(
            f"  rapprochement : écart moyen {ecart:.2f} % — "
            f"{conformes}/{len(rapprochements)} jours conformes (< 3 %)"
        )
    return lignes


def _resume_leviers(store: Store) -> list[str]:
    """Liste des leviers détectés et économie mensuelle estimée."""
    lignes = ["Leviers détectés"]
    total = 0.0
    for tenant in store.lister_tenants():
        for levier in store.lister_leviers(tenant.id):
            total += levier.economie_mensuelle_estimee_usd
            lignes.append(
                f"  {levier.application_id:<20} {levier.type_levier.value:<24} "
                f"{levier.economie_mensuelle_estimee_usd:>8.2f} $/mois  [{levier.statut.value}]"
            )
    lignes.append(f"  {'économie mensuelle estimée':<46} {total:>8.2f} $/mois")
    lignes.append("")
    return lignes


def _resume_decisions(store: Store) -> list[str]:
    """Recommandations par statut et bascule en cours."""
    lignes = ["Décisions"]
    for tenant in store.lister_tenants():
        for reco in store.lister_recommandations(tenant.id):
            lignes.append(
                f"  {reco.applicationId:<20} {reco.status.value:<20} "
                f"économie {reco.cost.expectedMonthlySaving:>8.2f} $/mois "
                f"(cible {reco.targetModel})"
            )
        for bascule in store.lister_bascules(tenant.id):
            lignes.append(
                f"  {bascule.application_id:<20} bascule en cours   "
                f"palier {bascule.palier_actuel_pourcentage} % — "
                f"qualité en ligne {bascule.qualite_en_ligne_mesuree:.3f}"
            )
    return lignes


def _projection(cout: float, jours: int) -> float:
    """Projection mensuelle du coût observé sur la période."""
    return cout * 30.0 / max(1, jours)


def main() -> int:
    """Point d'entrée `python -m demo.seed`."""
    store = MemoryStore()
    peupler_store(store)
    print(resume(store))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
