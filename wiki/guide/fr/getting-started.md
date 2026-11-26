---
title: Démarrer
description: Décrire les caméras dans cameras.yaml, ranger leurs mots de passe, générer la stack, puis la lancer avec Docker Compose, en stack Portainer ou avec just.
order: 1
---

## Ce qu'il faut {#requirements}

- Un hôte Linux avec Docker et Docker Compose, branché sur le réseau local des clients qui liront les copies.
- Une adresse libre sur le réseau local pour chaque copie, hors de la plage DHCP. Chaque copie a sa propre adresse
  et sa propre adresse MAC (un réseau `macvlan`), comme une vraie caméra.
- Rien d'autre : chaque étape tourne dans Docker. [just](https://github.com/casey/just) est facultatif, c'est un
  raccourci pour les mêmes étapes.

## Deux façons de déployer {#two-ways}

- **Docker Compose ou Portainer, sans just** : le dépôt, de simples commandes `docker compose`, et l'éditeur web de
  Portainer si vous utilisez Portainer. Voir [Générer](#render), puis [Lancer avec Docker Compose](#docker-compose)
  ou [En stack Portainer](#portainer).
- **Avec just** : les mêmes étapes, en recettes. Voir [Avec just](#render-and-start).

Les deux lisent les mêmes fichiers (`.env`, `cameras.yaml`, `secrets/`) et génèrent le même dossier, `generated/`.
Aucune image n'est publiée sur un registre : chaque image est construite à partir du dépôt, sur l'hôte Docker. La
première construction de l'image go2rtc modifiée prend quelques minutes.

## Récupérer le dépôt {#get-the-repository}

Sur l'hôte Docker, clonez le dépôt, ici dans `/opt/moonie-cams` (n'importe quel dossier convient) :

```sh
git clone https://github.com/LunaCreatoris/Moonie-Cams.git /opt/moonie-cams
cd /opt/moonie-cams
```

Sans git, téléchargez le ZIP depuis la page du dépôt (Code, puis Download ZIP) et décompressez-le à cet endroit.
Toutes les commandes qui suivent se lancent depuis ce dossier.

## Le réseau {#network}

Copiez `.env.example` en `.env` et donnez l'interface de l'hôte, le sous-réseau et la passerelle sur lesquels les
copies prennent leur adresse :

```sh
cp .env.example .env
```

Sous Windows, dans PowerShell : `Copy-Item .env.example .env`. Modifiez ensuite `.env` avec n'importe quel éditeur
de texte.

Avec `macvlan`, l'hôte Docker lui-même ne joint pas les copies ; les autres machines, si. Faites tourner les
clients sur un autre hôte, ou ajoutez une interface `macvlan` de liaison sur l'hôte.

## Décrire les caméras {#cameras}

Copiez `cameras.example.yaml` en `cameras.yaml` (`cp cameras.example.yaml cameras.yaml`) et ajoutez une entrée
par caméra :

```yaml
cameras:
  - name: entree
    input: rtsp
    source: rtsp://cams:${ENTREE_SOURCE_PASSWORD}@192.0.2.10:554/stream1
    source_sub: rtsp://cams:${ENTREE_SOURCE_PASSWORD}@192.0.2.10:554/stream2
    outputs: [onvif, mjpeg]
    ip: 198.51.100.20

  - name: garage
    input: mjpeg
    source: http://192.0.2.30:8080/video.mjpg
    outputs: [onvif]
    ip: 198.51.100.21
```

- `name` est aussi le chemin du flux : `rtsp://198.51.100.20:554/entree`.
- `input` est le format de la source, `outputs` les formats servis. Une sortie ne peut pas être le format d'entrée,
  sauf en [mode copie](copy-mode.md).
- `ip` est l'adresse de la copie, nécessaire pour ONVIF.
- Une source `mjpeg` est l'adresse HTTP du flux MJPEG lui-même, pas celle d'une page qui l'affiche. Une webcam
  d'imprimante 3D est décrite dans [Cas testés](tested-setups.md#octoprint).

Toutes les clés sont décrites dans la section
[Add a camera](https://github.com/LunaCreatoris/Moonie-Cams/blob/main/README.md#add-a-camera) du README.

## Les mots de passe {#secrets}

Un mot de passe ne s'écrit jamais dans `cameras.yaml` : on y met `${CLE}`, et le mot de passe brut va dans le fichier
`secrets/CLE`, seul, sans guillemets.

- `secrets/ENTREE_SOURCE_PASSWORD` : le mot de passe que la copie donne à la vraie caméra, celui de son compte.
- `secrets/ENTREE_OUTPUT_PASSWORD` : le mot de passe du compte RTSP de la copie (utilisateur `cams`), que les clients donnent
  à la copie.

Les caractères `@`, `^`, `:` et `/` sont acceptés. Seuls `"`, `\`, les caractères de contrôle et les espaces
Unicode (comme l'espace insécable) sont refusés : la copie s'arrête alors avec une ligne qui nomme le fichier, jamais
sa valeur.

Écrivez chaque fichier avec un éditeur de texte (un retour à la ligne final ne gêne pas), ou depuis le terminal :

```sh
printf '%s' 'le-mot-de-passe-de-la-camera' > secrets/ENTREE_SOURCE_PASSWORD
printf '%s' 'le-mot-de-passe-de-la-copie' > secrets/ENTREE_OUTPUT_PASSWORD
```

Sous Windows, dans PowerShell : `Set-Content -NoNewline -Path secrets\ENTREE_SOURCE_PASSWORD -Value 'le-mot-de-passe-de-la-camera'`.
Un mot de passe tapé en ligne de commande reste dans l'historique du terminal : effacez-le, ou passez par l'éditeur.

## Générer {#render}

```sh
docker compose -f tools/compose.yml run --rm render
```

La première fois, cette commande construit la petite image d'outils, puis elle écrit dans `generated/` une
configuration go2rtc par caméra et le fichier compose. Elle n'a besoin que de Docker, pas de Python, et tourne sur
tout système qui a Docker. Une caméra qui ne peut pas fonctionner est refusée, nommément, avant que rien ne démarre.

Générez à nouveau après chaque modification de `cameras.yaml`, des réglages du webhook dans `.env`, ou de la liste
des fichiers de secrets. Une copie lit sa configuration go2rtc au démarrage : si sa caméra a changé et qu'elle garde
ses anciens réglages, redémarrez-la.

## Lancer avec Docker Compose {#docker-compose}

```sh
docker compose --env-file .env -f generated/compose.yml up -d --build --remove-orphans
```

Le premier lancement construit les images, puis chaque copie démarre ; la même commande les met à jour après un
nouveau rendu. `--env-file .env` est nécessaire, car `.env` est à la racine et le fichier compose dans `generated/`.

- `docker compose --env-file .env -f generated/compose.yml logs -f` suit les journaux de toutes les copies ;
  ajoutez `cam-entree` pour ceux d'une seule.
- `docker compose --env-file .env -f generated/compose.yml restart cam-entree` redémarre une copie.
- `docker compose --env-file .env -f generated/compose.yml down` arrête toutes les copies.

## En stack Portainer {#portainer}

Portainer déploie la stack générée depuis son éditeur web. Les fichiers restent sur l'hôte Docker : la stack les
désigne par leur chemin sur cet hôte.

Sur l'hôte Docker, dans `/opt/moonie-cams`, écrivez `.env`, `cameras.yaml` et `secrets/` comme plus haut. Puis
générez, en désignant les fichiers par leur chemin sur l'hôte, et construisez les images sur place :

```sh
docker compose -f tools/compose.yml run --rm render cameras.yaml generated --env=.env --host-dir=/opt/moonie-cams/generated
docker compose --env-file .env -f generated/compose.yml build
```

Les secrets sont alors cherchés dans `/opt/moonie-cams/secrets`, à côté de `generated/`.

Dans Portainer, sur l'environnement de cet hôte : Stacks, Add stack. Donnez à la stack le nom de
`COMPOSE_PROJECT_NAME` dans `.env` (`moonie-cams` par défaut), choisissez Web editor, et collez le contenu de
`generated/compose.yml`. Sous Environment variables, chargez votre `.env` (Load variables from .env file), ou ajoutez
un par un `CAMS_PARENT`, `CAMS_SUBNET`, `CAMS_GATEWAY` et `TZ`, plus ceux du webhook si vous les avez renseignés.
Puis Deploy the stack.

Portainer peut signaler qu'un fichier de secret n'existe pas : son compose le cherche dans le conteneur de Portainer
lui-même. La copie le reçoit quand même depuis l'hôte. `just webeditor-check` prouve que cette voie fonctionne, avec
un client compose qui n'a que le texte du fichier compose.

Pour mettre à jour : générez et construisez à nouveau avec les deux commandes ci-dessus, puis dans Portainer ouvrez la stack, collez le
nouveau `generated/compose.yml` dans son éditeur, et Update the stack. Les valeurs d'Environment variables doivent
rester celles du `.env` lu par le rendu.

Pourquoi pas une stack depuis un dépôt Git :

- `generated/` est écrit par `app.py` à partir de votre `cameras.yaml`. Portainer ne sait pas lancer cette étape, et
  les deux restent hors du dépôt, puisqu'ils décrivent votre réseau.
- Portainer indique que la construction d'images depuis une stack Git
  [n'est pas entièrement prise en charge](https://docs.portainer.io/2.39-lts/faqs/troubleshooting/stacks-deployments-and-updates/can-i-build-an-image-while-deploying-a-stack-application-from-git),
  et que depuis la version 2.29.2
  [elle échoue](https://docs.portainer.io/faqs/known-issues/docker-compose-files-including-build-steps-fail) sur un
  environnement joint par un agent.
- Chaque copie monte son fichier de configuration, et l'édition Community ne monte pas de fichiers depuis le dépôt
  (les [chemins relatifs](https://docs.portainer.io/advanced/relative-paths) sont réservés à l'édition Business).

## Avec just {#render-and-start}

```sh
just render
just up
```

`just render` lance le [rendu](#render) ci-dessus. `just up` génère, construit les images la première fois, puis
lance ou met à jour chaque copie.

- `just logs` suit les journaux de toutes les copies, `just logs cam-entree` ceux d'une seule.
- `just down` arrête toutes les copies.

En dehors de ce raccourci, just est l'outil des contributeurs : `just check` vérifie le code et un rendu de
l'exemple, et `just ci` est la vérification que passe chaque modification avant d'être fusionnée.

## Les mêmes étapes, dans les deux cas {#commands}

Les autres pages donnent la recette just ; voici la commande qu'elle remplace.

| Avec just              | Sans just                                                                                      |
|------------------------|------------------------------------------------------------------------------------------------|
| `just render`          | `docker compose -f tools/compose.yml run --rm render`                                          |
| `just up`              | le rendu, puis `docker compose --env-file .env -f generated/compose.yml up -d --build --remove-orphans` |
| `just logs cam-NOM`    | `docker compose --env-file .env -f generated/compose.yml logs -f --tail 100 cam-NOM`           |
| `just down`            | `docker compose --env-file .env -f generated/compose.yml down`                                 |
| `just health NOM`      | `docker cp health.py cams-NOM:/tmp/health.py`, puis `docker exec cams-NOM python3 /tmp/health.py 2` |

Dans Portainer, les journaux et le redémarrage d'une copie se trouvent sur la page de son conteneur.

Ensuite : [ajouter les copies à un client](onvif-clients.md).
