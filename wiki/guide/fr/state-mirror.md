---
title: Le miroir d'état
description: Une copie s'éteint tant que sa caméra est hors ligne et revient seule avec elle.
order: 5
---

## Pourquoi {#why}

Sans miroir, une copie dont la caméra est éteinte continue de répondre en ONVIF, et Protect la montre en ligne sans
image. Avec le miroir, tant que la vraie caméra est hors ligne, la copie ressemble à une caméra débranchée : ni ping,
ni ONVIF, ni RTSP. Elle revient seule avec la caméra.

## Comment ça marche {#how-it-works}

Chaque copie a une sentinelle à côté d'elle, `cam-NOM-mirror`.

- Toutes les 5 secondes, elle ouvre une connexion TCP vers la caméra et la referme aussitôt, sans ouvrir de session :
  une Tapo n'en accepte qu'une par flux.
- Après 3 échecs de suite, la copie se ferme ; après 2 réussites de suite, elle se rouvre.
- Fermée, la copie ignore tout paquet entrant, sauf ceux de la caméra elle-même, pour que la sentinelle et go2rtc la
  voient revenir.
- La sentinelle n'agit que dans le réseau de sa copie, jamais sur l'hôte ni sur une autre copie.

Chaque changement d'état est une ligne de journal :

```sh
just logs cam-NOM-mirror
```

## Le désactiver {#opt-out}

Mettez `mirror_state: false` sur une caméra pour garder sa copie toujours en ligne. C'est nécessaire pour une source
sans hôte à sonder (`ffmpeg:`, `exec:`) : `just render` le signale.

## Si une copie reste fermée {#stuck-closed}

Après un `docker compose stop` puis `start` d'une copie, sa sentinelle peut rester arrêtée. Lancez `just up`, qui la
redémarre et la remet à jour.

## Une alerte à chaque changement {#webhook}

Le service optionnel `cams-webhook` (conteneur `<projet>-cams-webhook-1`, `moonie-cams-cams-webhook-1` par défaut)
envoie un POST JSON à l'adresse de votre choix (n8n, Home Assistant ou tout autre récepteur) chaque fois qu'une
caméra passe hors ligne ou revient en ligne. Sa mise en place est dans la section
[Webhook support](https://github.com/LunaCreatoris/Moonie-Cams/blob/main/README.md#webhook-support) du README.

Le détail est dans la section
[State mirror](https://github.com/LunaCreatoris/Moonie-Cams/blob/main/README.md#state-mirror-a-copy-is-down-while-its-camera-is)
du README.
