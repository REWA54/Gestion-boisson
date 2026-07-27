# Cellier

Cellier est une application open source, local-first et auto-hébergeable pour
gérer vins, bières, cidres, spiritueux, boissons sans alcool et autres
collections. L’interface est une PWA mobile-first et les données restent sur
votre serveur.

## Ce qui est livré

- plusieurs collections et utilisateurs avec rôles/permissions ;
- références, variantes, conditionnements, achats et médias locaux ;
- emplacements hiérarchiques et règle stricte « une variante par emplacement » ;
- ajout, retrait, ouverture, déplacement, réservation et dégustation ;
- recherche locale par nom, producteur, région, année, tag et emplacement ;
- file d’opérations hors ligne IndexedDB avec reprise automatique ;
- idempotence serveur et transactions empêchant tout stock négatif ;
- journal, annulation/rétablissement avec détection des modifications ultérieures ;
- mode soirée ;
- synthèse financière protégée par permission ;
- API Home Assistant et état de santé ;
- export JSON, sauvegardes PostgreSQL quotidiennes et médias persistants ;
- Docker Compose, migrations Alembic et tests de concurrence.

## Installation

Prérequis : Docker Engine avec le plugin Compose.

```bash
cp .env.example .env
```

Remplacez impérativement `POSTGRES_PASSWORD` par un secret long. Puis :

```bash
docker compose up -d --build
docker compose ps
```

Ouvrez `http://adresse-du-serveur:8080`. Le premier compte créé devient
administrateur.

Cellier est prévu pour un réseau local ou un accès extérieur via VPN. Ne
publiez pas directement le port sur Internet sans HTTPS, filtrage réseau et
reverse proxy correctement configuré.

## Mise à jour

Sauvegardez d’abord, puis :

```bash
git pull --ff-only
docker compose build --pull app
docker compose up -d
docker compose ps
```

L’application applique les migrations au démarrage. Ne revenez pas à une image
plus ancienne après une migration sans restaurer la sauvegarde correspondante.

## Sauvegarde et restauration

Le service `backup` produit chaque jour :

- `backups/cellier-<date>.sql.gz` pour PostgreSQL ;
- une archive du volume média lorsque celui-ci est monté.

La conservation par défaut est de 14 jours. Une sauvegarde n’est fiable qu’après
un test de restauration sur une autre installation.

Restauration de la base sur une installation arrêtée :

```bash
docker compose stop app backup
gzip -dc backups/cellier-YYYYMMDDTHHMMSSZ.sql.gz \
  | docker compose exec -T db psql -U cellier -d cellier
docker compose up -d
```

Adaptez l’utilisateur et le nom de base si vous les avez modifiés. La procédure
détaillée est dans [docs/operations.md](docs/operations.md).

## Développement

Backend :

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
POSTGRES_PASSWORD=cellier-test \
  docker compose -f compose.yaml -f compose.test.yaml up -d db
CELLIER_DATABASE_URL=postgresql+asyncpg://cellier:cellier-test@127.0.0.1:55432/cellier_test \
CELLIER_ENVIRONMENT=test .venv/bin/pytest
```

Frontend :

```bash
cd frontend
npm ci
npm run build
```

Qualité :

```bash
.venv/bin/ruff check app tests
cd frontend && npm run lint
```

## Structure

```text
app/api/          routes HTTP
app/services/     invariants métier et transactions
app/models.py     modèle relationnel
frontend/         PWA React/TypeScript
alembic/          migrations de schéma
tests/            tests API et concurrence
docker/           démarrage, sauvegarde, initialisation
docs/adr/         décisions d’architecture
```

L’API interactive est disponible sur `/api/docs` pour un administrateur du
réseau local.

## Licence

GNU Affero General Public License v3.0. Voir [LICENSE](LICENSE).

