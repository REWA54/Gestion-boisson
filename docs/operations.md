# Exploitation de Cellier

## Contrôles courants

```bash
docker compose ps
docker compose logs --tail=100 app
curl --fail http://127.0.0.1:8080/api/health
```

Les services `db` et `app` doivent être `healthy`. Un redémarrage de
l’application ne doit pas nécessiter de redémarrer PostgreSQL.

## Sauvegardes

La base et les médias forment une seule sauvegarde logique. Conservez une copie
hors du serveur (NAS, disque chiffré ou stockage objet) et testez périodiquement
la restauration.

Déclencher immédiatement une sauvegarde SQL :

```bash
docker compose exec -T db pg_dump -U cellier -d cellier \
  --format=plain --no-owner --no-privileges \
  | gzip -9 > backups/cellier-manual.sql.gz
```

## Restauration testée

1. Arrêter `app` et `backup`.
2. Créer une base vide ou supprimer/recréer la base cible de test.
3. Restaurer le SQL avec `psql`.
4. Restaurer les médias au même instant logique.
5. Démarrer `app`, vérifier `/api/health`, se connecter et contrôler plusieurs
   quantités et photos.

N’utilisez jamais une restauration de test contre la base de production.

## Rotation des secrets

- Le mot de passe PostgreSQL doit être modifié dans PostgreSQL puis dans `.env`.
- Les jetons Home Assistant doivent être révoqués côté Home Assistant avant
  d’être remplacés.
- Une déconnexion de l’utilisateur révoque la session courante. Les sessions
  expirent automatiquement.

## Incidents de synchronisation

Une opération refusée reste dans IndexedDB avec l’état `requires_review`. Le
stock serveur reste la source de vérité. Ne corrigez pas directement les tables :
utilisez une correction d’inventaire ou l’API afin de conserver un événement.

## Mise à jour et retour arrière

Toujours sauvegarder avant mise à jour. Les migrations sont montantes. Pour un
retour arrière complet, restaurez ensemble l’image applicative, la base et les
médias provenant du même point de sauvegarde.

