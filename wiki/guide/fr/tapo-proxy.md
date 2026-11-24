---
title: Partager une Tapo
description: Le mode proxy partage une Tapo entre UniFi Protect et Home Assistant, pan-tilt et événements compris.
order: 3
---

## Pourquoi un proxy {#why-a-proxy}

Une Tapo n'accepte qu'une connexion par flux, et Protect comme Home Assistant la veulent. En `mode: proxy`, la copie
se présente comme la caméra :

- go2rtc tient la seule session RTSP de chaque flux (`stream1` et `stream2`), sans réencodage ;
- un petit proxy ONVIF relaie vers la vraie Tapo les lectures, les commandes de pan-tilt et les événements, et
  réécrit les adresses pour qu'elles pointent sur la copie.

Tout autre réglage (redémarrage, réseau, encodeur, image) est refusé : il changerait la caméra pour tous les clients.

## Configurer {#configure}

```yaml
cameras:
  - name: salon
    input: rtsp
    source: rtsp://cams:${SALON_SOURCE_PASSWORD}@192.0.2.11:554/stream1
    source_sub: rtsp://cams:${SALON_SOURCE_PASSWORD}@192.0.2.11:554/stream2
    outputs: [onvif]
    ip: 198.51.100.22
    mode: proxy
    onvif_url: http://192.0.2.11:2020
    ptz_hold: 5
    proxy_log: writes
```

- `onvif_url` est l'adresse ONVIF de la Tapo, port 2020, sans compte ni chemin.
- Un seul compte : les clients donnent à la copie le compte caméra de la Tapo, pour ONVIF comme pour RTSP.
- Le proxy répond sur les ports 80 et 2020. Dans ce mode, l'API de go2rtc (MJPEG, HLS, WebRTC) passe sur le port 1984.

## Protect et Home Assistant ensemble {#protect-and-home-assistant}

1. Retirez la Tapo de Protect si elle y était adoptée en direct, puis adoptez la copie par son adresse avec le compte
   caméra. Voir [Adopter dans UniFi Protect](unifi-protect.md).
2. Dans Home Assistant, ajoutez l'intégration ONVIF pointée sur la copie (port 80 ou 2020, compte caméra) : elle a
   les flux, le pan-tilt et les événements.
3. L'intégration Tapo de HACS peut garder la vraie caméra pour ses réglages en plus (mode privé, alarme, voyant), mais
   coupez son flux ou ses entités caméra : sinon elle prend la seule session de la Tapo.

## L'arbitrage du pan-tilt {#ptz-arbitration}

Priorité au premier qui envoie une commande. Le premier client (par adresse IP) qui bouge la caméra garde la main
jusqu'à `ptz_hold` secondes (5 par défaut) après sa dernière commande.

- Les commandes de l'autre client reçoivent une erreur « PTZ is in use by another client » plutôt qu'un faux succès,
  pour que son interface sache que la caméra n'a pas bougé.
- Stop compte comme une commande : l'autre client ne peut pas arrêter un mouvement en cours.
- Les lectures (position, préréglages) ne sont jamais bloquées.

## Le journal {#logs}

Le proxy écrit une ligne par requête :

```sh
just logs cam-salon-onvif
```

```text
2026-11-23T10:00:01Z 198.51.100.5 /onvif/service ContinuousMove[pan=0.5 tilt=0 zoom=0 profile=profile_1] ptz-claim 200 41ms
2026-11-23T10:00:03Z 198.51.100.6 /onvif/service GotoPreset[preset=3 profile=profile_1] ptz-busy:holder=198.51.100.5,left=3.0s - 0ms
2026-11-23T10:00:04Z 198.51.100.6 /onvif/device_service SystemReboot refused:camera-command - 1ms
```

Chaque ligne donne l'heure, le client, le service, l'opération (avec ce que demande une commande de pan-tilt), la
décision, la réponse de la caméra et la durée. Elle ne contient jamais de mot de passe ni d'en-tête.
`proxy_log` choisit le volume : `all` (tout), `writes` (par défaut : commandes, refus, pan-tilt et erreurs) ou `off`.

Le détail des champs est dans la section
[Proxy log](https://github.com/LunaCreatoris/Moonie-Cams/blob/main/README.md#proxy-log) du README.
