---
title: Le mode proxy
description: Le mode proxy partage une caméra ONVIF entre plusieurs clients, pan-tilt et événements compris.
order: 3
---

## Pourquoi un proxy {#why-a-proxy}

Certaines caméras n'acceptent qu'une connexion par flux. Quand plusieurs clients veulent la même caméra, en
`mode: proxy` la copie se présente comme la caméra :

- go2rtc tient la seule session RTSP de chaque flux (`source` et `source_sub`), sans réencodage ;
- un petit proxy ONVIF relaie vers la vraie caméra les lectures, les commandes de pan-tilt et les événements, et
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
    onvif_url: http://192.0.2.11
    ptz_hold: 5
    proxy_log: writes
```

- `onvif_url` est l'adresse ONVIF de la vraie caméra, sans compte ni chemin. Sans port, c'est le port 80 ; ajoutez
  celui de la caméra s'il est autre.
- Un seul compte : les clients donnent à la copie le compte de la vraie caméra, pour ONVIF comme pour RTSP.
- Le proxy répond sur les ports 80 et 2020. Dans ce mode, l'API de go2rtc (MJPEG, HLS, WebRTC) passe sur le port 1984.

Une caméra configurée pas à pas en mode proxy est décrite dans [Cas testés](tested-setups.md#tapo).

## Plusieurs clients ensemble {#several-clients}

1. Retirez la vraie caméra de tout client qui la lisait en direct.
2. Ajoutez la copie à chaque client ONVIF, par son adresse (port 80 ou 2020) et avec le compte de la caméra : chacun
   a les flux, le pan-tilt et les événements. Voir [Ajouter à un client](onvif-clients.md).
3. Un outil qui parle encore à la vraie caméra pour ses réglages propres (mode privé, alarme, voyant) peut rester,
   à condition de ne pas ouvrir son flux : sinon il prend la seule session de la caméra.

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
