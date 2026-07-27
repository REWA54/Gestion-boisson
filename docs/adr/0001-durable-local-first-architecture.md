# ADR-0001 : Architecture durable local-first

**Statut :** Accepté  
**Date :** 2026-07-27  
**Décideur :** Mainteneur de Cellier

## Contexte

Cellier doit rester utilisable sans Internet, empêcher les stocks négatifs lors
d’actions concurrentes, être auto-hébergeable par une personne seule et pouvoir
évoluer sans réécriture de ses données.

Les quatre parcours prioritaires sont l’ajout, le retrait, la recherche et le
déplacement. Un double envoi réseau ne doit jamais doubler un mouvement.

## Décision

- Backend monolithique modulaire FastAPI/SQLAlchemy.
- PostgreSQL comme source de vérité, avec migrations Alembic.
- Transactions courtes, verrouillage pessimiste et contraintes SQL pour les
  invariants de stock.
- Journal d’événements avant/après et identifiant d’opération unique.
- PWA React/TypeScript, cache local et file IndexedDB ordonnée.
- Médias sur volume local distinct, sauvegardés avec la base.
- Déploiement reproductible en conteneurs non privilégiés avec healthchecks.

## Options considérées

### SQLite dans un monolithe minimal

| Dimension | Évaluation |
|---|---|
| Installation | Excellente |
| Concurrence multi-utilisateur | Limitée |
| Exploitation | Simple |
| Évolution à long terme | Moyenne |

Cette option a été écartée : le verrouillage global en écriture et la migration
future vers PostgreSQL créeraient un risque inutile pour une application
multi-utilisateur.

### PostgreSQL et frontend compilé séparément

| Dimension | Évaluation |
|---|---|
| Installation | Bonne avec Compose |
| Concurrence multi-utilisateur | Excellente |
| Exploitation | Modérée |
| Évolution à long terme | Excellente |

Option retenue. Le coût opérationnel supplémentaire est absorbé par Docker
Compose, les migrations automatiques, les healthchecks et les sauvegardes.

### Microservices

| Dimension | Évaluation |
|---|---|
| Installation | Faible |
| Isolation | Excellente |
| Exploitation personnelle | Mauvaise |
| Besoin actuel | Non justifié |

Écartée. Les frontières restent explicites dans le code afin de permettre une
extraction ultérieure si la charge ou l’équipe le justifie.

## Conséquences

- Les opérations de stock disposent de garanties fortes, y compris après un
  timeout client.
- Les évolutions de schéma sont versionnées.
- La restauration exige de restaurer à la fois PostgreSQL et le volume média.
- PostgreSQL est une dépendance obligatoire.
- Une future synchronisation multi-serveurs nécessitera un protocole distinct ;
  elle n’est pas supposée par cette architecture.

