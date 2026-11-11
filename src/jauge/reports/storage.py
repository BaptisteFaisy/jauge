"""Rendus du rapport mensuel : Markdown pour l'humain, JSON pour le portail.

Trois sorties, toutes construites à partir du même `RapportMensuel` :

- `rendre_markdown` : gabarit Jinja2 `templates/report.md.j2`, lisible par un
  directeur financier (chiffres clés, leviers, économies, recommandations,
  appendice méthodologique). Le gabarit et son contenu sont en anglais ;
- `vers_dict` / `rendre_json` : contrat stable consommé par le portail
  (EF-RAP-01) et par l'API du plan de contrôle, versionné par
  `schema_version` ;
- `DepotRapports` : port d'archivage, avec `DepotS3` pour la production et
  `DepotMemoire` pour les tests et la démo hors-ligne.

Aucun de ces rendus ne calcule un chiffre : ils mettent en forme ce que
`jauge.reports.builder` a déjà fixé (ENF-IA-01) et ne transportent aucune
donnée de requête (ENF-CONF-01).
"""

from __future__ import annotations

import json
from typing import Protocol

from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape

from jauge.common.errors import ServiceIndisponibleError
from jauge.reports.schema import RapportMensuel

CONTENT_TYPE_MARKDOWN = "text/markdown; charset=utf-8"
CONTENT_TYPE_JSON = "application/json; charset=utf-8"

# Libellés anglais des leviers : le rapport est destiné au client final.
LIBELLES_LEVIER = {
    "modele_surdimensionne": "Oversized model",
    "cache_prompts_absent": "No prompt cache",
    "traitement_differable": "Deferrable batch work left on Standard",
    "sorties_trop_longues": "Over-long outputs",
    "requetes_repetees": "Repeated requests",
    "erreurs_et_relances": "Errors and retries",
    "priority_sans_besoin": "Priority tier without need",
    "distillation": "Distillation candidate",
}

LIBELLES_SENS = {"hausse": "increase", "baisse": "decrease", "stable": "stable"}

LIBELLES_STATUT_LEVIER = {
    "detecte": "Detected",
    "en_cours_de_preuve": "Under proof",
    "prouve": "Proven",
    "applique": "Applied",
    "rejete": "Rejected",
}


def _filtre_usd(valeur: float) -> str:
    """Montant en USD, deux décimales, séparateur de milliers pour la lecture."""
    return f"{valeur:,.2f}"


def _filtre_pct(valeur: float) -> str:
    """Pourcentage à deux décimales."""
    return f"{valeur:.2f}%"


def _filtre_ratio(valeur: float) -> str:
    """Ratio (0–1) rendu en pourcentage : 0.95 → 95.00%."""
    return f"{valeur * 100.0:.2f}%"


def _filtre_nombre(valeur: int) -> str:
    """Entier avec séparateur de milliers."""
    return f"{valeur:,}"


def _environnement() -> Environment:
    """Environnement Jinja2 des gabarits de rapport (templates du paquet)."""
    environnement = Environment(
        loader=PackageLoader("jauge.reports", "templates"),
        autoescape=select_autoescape(enabled_extensions=("html",), default=False),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        undefined=StrictUndefined,
    )
    environnement.filters["usd"] = _filtre_usd
    environnement.filters["pct"] = _filtre_pct
    environnement.filters["ratio"] = _filtre_ratio
    environnement.filters["nombre"] = _filtre_nombre
    return environnement


def rendre_markdown(rapport: RapportMensuel) -> str:
    """Rend le rapport en Markdown à partir du gabarit `report.md.j2`."""
    gabarit = _environnement().get_template("report.md.j2")
    return gabarit.render(
        rapport=rapport,
        libelles_levier=LIBELLES_LEVIER,
        libelles_statut=LIBELLES_STATUT_LEVIER,
        libelles_sens=LIBELLES_SENS,
    )


def vers_dict(rapport: RapportMensuel) -> dict[str, object]:
    """Contrat JSON stable du rapport (dates en ISO-8601, montants en USD)."""
    return rapport.model_dump(mode="json")


def rendre_json(rapport: RapportMensuel) -> str:
    """Rend le rapport en JSON indenté, prêt pour le portail ou un archivage."""
    return json.dumps(vers_dict(rapport), ensure_ascii=False, indent=2) + "\n"


def cle_rapport(rapport: RapportMensuel, extension: str, prefixe: str = "reports") -> str:
    """Clé d'archivage déterministe : `reports/<client>/<AAAA-MM>/report.<ext>`."""
    return f"{prefixe}/{rapport.tenant_id}/{rapport.annee}-{rapport.mois:02d}/report.{extension}"


class DepotRapports(Protocol):
    """Port d'archivage des rapports rendus.

    Le préfixe de clé appartient au dépôt : deux clients peuvent archiver dans
    deux compartiments avec des arborescences différentes.
    """

    prefixe: str

    def enregistrer(self, cle: str, contenu: str, content_type: str) -> str:
        """Écrit un objet et rend sa clé."""
        ...


class DepotMemoire:
    """Dépôt en mémoire : tests et démonstration sans AWS."""

    def __init__(self, prefixe: str = "reports") -> None:
        self.prefixe = prefixe
        self.objets: dict[str, tuple[str, str]] = {}

    def enregistrer(self, cle: str, contenu: str, content_type: str) -> str:
        """Range le contenu sous sa clé (écrasement idempotent)."""
        self.objets[cle] = (contenu, content_type)
        return cle

    def lire(self, cle: str) -> str:
        """Relit un objet archivé (utile aux tests)."""
        return self.objets[cle][0]


class DepotS3:
    """Dépôt S3 réel (boto3 importé paresseusement, jamais appelé en test)."""

    def __init__(self, bucket: str, prefixe: str = "reports", client: object | None = None) -> None:
        self.bucket = bucket
        self.prefixe = prefixe
        self._client = client

    def _client_s3(self) -> object:
        """Client S3, créé à la première utilisation."""
        if self._client is None:
            import boto3  # import paresseux : aucun réseau à l'import

            self._client = boto3.client("s3")
        return self._client

    def enregistrer(self, cle: str, contenu: str, content_type: str) -> str:
        """Dépose l'objet dans le compartiment, chiffré côté S3 (ENF-SEC-03)."""
        try:
            self._client_s3().put_object(
                Bucket=self.bucket,
                Key=cle,
                Body=contenu.encode("utf-8"),
                ContentType=content_type,
                ServerSideEncryption="aws:kms",
            )
        except Exception as erreur:  # toute panne d'archivage est remontée en 502
            raise ServiceIndisponibleError(
                f"Archivage S3 impossible ({self.bucket}/{cle}) : {erreur}", cle=cle
            ) from erreur
        return cle


def publier(rapport: RapportMensuel, depot: DepotRapports) -> list[str]:
    """Archive le rapport en Markdown et en JSON, et rend les clés écrites."""
    return [
        depot.enregistrer(
            cle_rapport(rapport, "md", depot.prefixe),
            rendre_markdown(rapport),
            CONTENT_TYPE_MARKDOWN,
        ),
        depot.enregistrer(
            cle_rapport(rapport, "json", depot.prefixe), rendre_json(rapport), CONTENT_TYPE_JSON
        ),
    ]
