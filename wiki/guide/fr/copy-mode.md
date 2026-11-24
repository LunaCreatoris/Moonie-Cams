---
title: Le mode copie
description: Partager la seule connexion d'une source entre plusieurs clients, chaque sortie isolée des autres.
order: 4
---

## À quoi il sert {#purpose}

`mode: copy` fait de la copie un simple répartiteur : une connexion à la source, copiée vers chaque sortie à la fois,
le format d'entrée compris, passé tel quel. C'est utile pour une source qui n'accepte qu'un client, comme une
ESP32-CAM ou un flux de Tapo, quand plusieurs clients en veulent une copie.

```yaml
cameras:
  - name: atelier
    mode: copy
    input: rtsp
    source: rtsp://cams:${ATELIER_SOURCE_PASSWORD}@192.0.2.12:554/stream1
    outputs: [rtsp, mjpeg]
    ip: 198.51.100.23
```

Home Assistant et Protect lisent tous deux `rtsp://cams:MOT_DE_PASSE@198.51.100.23:554/atelier`, et un navigateur
lit `http://198.51.100.23/api/stream.mjpeg?src=atelier_mjpeg`.

## Ce que chaque sortie a {#what-each-output-gets}

- **Sa propre file** : un client en retard perd ses propres images et ne retient pas les autres.
- **Son propre ffmpeg** : chaque sortie qui réencode lit son propre flux, `NOM_SORTIE`. Un encodeur qui plante ne
  touche que sa sortie.
- **Son propre redémarrage** : un client qui part est retiré seul, la connexion à la source reste ouverte tant qu'un
  client reste.

## Suivre la santé {#health}

```sh
just health atelier
```

La recette affiche, pour chaque flux, l'état de la source, les clients, le débit et les pertes. Elle ne montre jamais
d'adresse, d'URL ni de secret.

## Les limites {#limits}

- Avec une sortie `onvif`, les flux de chaque sortie apparaissent aussi comme profils ONVIF : choisissez le flux
  principal dans le client.
- Les modes copie et proxy ne se combinent pas.

Le détail est dans la section
[Copy mode](https://github.com/LunaCreatoris/Moonie-Cams/blob/main/README.md#copy-mode) du README.
