---
title: Démarrer
description: Décrire les caméras dans cameras.yaml, ranger leurs mots de passe, puis générer et lancer une copie par caméra.
order: 1
---

## Ce qu'il faut {#requirements}

- Un hôte Linux avec Docker et Docker Compose, branché sur le réseau local des clients qui liront les copies.
- [just](https://github.com/casey/just) : chaque étape est une recette, et chaque recette tourne dans Docker.
- Une adresse libre sur le réseau local pour chaque copie, hors de la plage DHCP. Chaque copie a sa propre adresse
  et sa propre adresse MAC (un réseau `macvlan`), comme une vraie caméra.

## Le réseau {#network}

Copiez `.env.example` en `.env` et donnez l'interface de l'hôte, le sous-réseau et la passerelle sur lesquels les
copies prennent leur adresse.

Avec `macvlan`, l'hôte Docker lui-même ne joint pas les copies ; les autres machines, si. Faites tourner les
clients sur un autre hôte, ou ajoutez une interface `macvlan` de liaison sur l'hôte.

## Décrire les caméras {#cameras}

Copiez `cameras.example.yaml` en `cameras.yaml` et ajoutez une entrée par caméra :

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

## Générer et lancer {#render-and-start}

```sh
just render
just up
```

`just render` écrit dans `generated/` une configuration go2rtc par caméra et le fichier compose. `just up` génère,
construit l'image go2rtc la première fois, puis lance ou met à jour chaque copie.

- `just logs` suit les journaux de toutes les copies, `just logs cam-entree` ceux d'une seule.
- `just down` arrête toutes les copies.
- `just check` vérifie le code et un rendu de l'exemple.

Ensuite : [ajouter les copies à un client](onvif-clients.md).
