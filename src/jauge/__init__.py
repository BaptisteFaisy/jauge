"""Jauge — FinOps for generative AI on Amazon Bedrock.

Trois verbes : **attribuer** chaque dollar d'inférence, **prouver** qu'un modèle
moins cher convient (rejeu de requêtes réelles + évaluation), **appliquer** la
bascule par paliers puis vérifier l'économie sur la facture.

Règle structurante (ENF-IA-01) : aucun chiffre n'est calculé par un modèle de
langage. Tout nombre vient de ce paquet ; les agents ne rédigent que du texte.
"""

from __future__ import annotations

__version__ = "0.1.0"
