---
sidebar_position: 7
---

# Tapo

Supporto per le telecamere Tapo (C200, C220, C500, ecc.) tramite protocollo ONVIF e stream RTSP.

## Opzioni

```text
optional arguments:
  --ffmpeg-args FFMPEG_ARGS, -f FFMPEG_ARGS
                        Transcoding args for `ffmpeg -i <src> <args> <dst>`
  --rtsp-transport {tcp,udp,http,udp_multicast}
                        RTSP transport protocol used by stream
  --username USERNAME, -u USERNAME
                        Camera username
  --password PASSWORD, -p PASSWORD
                        Camera password
  --main-stream {stream1,stream2}, -m {stream1,stream2}
                        Main stream profile to use (stream1=HD, stream2=SD)
  --sub-stream {stream1,stream2}, -s {stream1,stream2}
                        Sub stream profile to use (stream1=HD, stream2=SD)
  --snapshot-url SNAPSHOT_URL
                        Custom snapshot URL (optional, will use ONVIF if not provided)
```

## Configurazione

### Configurazione ONVIF

Prima di utilizzare il modulo Tapo, è necessario configurare l'account ONVIF sulla telecamera:

1. Accedi all'interfaccia web della telecamera Tapo
2. Vai su "Impostazioni" > "Avanzate" > "ONVIF"
3. Abilita ONVIF e imposta username e password
4. Assicurati che il rilevamento movimento sia abilitato

### Esempio di utilizzo

```sh
unifi-cam-proxy --mac '{unique MAC}' -H {NVR IP} -i {camera IP} -c /client.pem -t {Adoption token} \
    tapo \
    -u {username} \
    -p {password} \
    -m "stream1" \
    -s "stream2" \
    --ffmpeg-args='-c:v copy -bsf:v "h264_metadata=tick_rate=30000/1001" -ar 32000 -ac 1 -codec:a aac -b:a 32k'
```

### Stream RTSP

Le telecamere Tapo supportano due stream RTSP:

- **stream1**: Stream principale in alta definizione (HD)
- **stream2**: Stream secondario in definizione standard (SD)

Gli URL RTSP seguono il formato:
```
rtsp://username:password@ip:554/stream1
rtsp://username:password@ip:554/stream2
```

### Eventi ONVIF

Il modulo utilizza il protocollo ONVIF per rilevare gli eventi di movimento. Gli eventi vengono monitorati tramite:

1. Sottoscrizione agli eventi ONVIF
2. Polling periodico dei messaggi di notifica
3. Parsing degli eventi di movimento

### Snapshot

Il modulo supporta tre metodi per ottenere gli snapshot:

1. **ONVIF Snapshot**: Utilizza il servizio ONVIF per ottenere snapshot
2. **HTTP Snapshot**: Fallback su endpoint HTTP se ONVIF non è disponibile
3. **Custom URL**: URL personalizzato specificato tramite `--snapshot-url`

## Modelli supportati

- [x] Tapo C200
- [x] Tapo C220  
- [x] Tapo C500
- [x] Altri modelli Tapo con supporto ONVIF

### Funzionalità

- [x] Supporto per registrazione continua
- [x] Eventi di movimento tramite ONVIF
- [x] Stream HD e SD
- [x] Snapshot automatici
- [x] Controllo delle impostazioni video (se supportato dalla telecamera)

### Note

- Assicurati che ONVIF sia abilitato sulla telecamera
- Il rilevamento movimento deve essere configurato nell'interfaccia della telecamera
- Alcune telecamere potrebbero richiedere configurazioni specifiche per gli eventi ONVIF
- Se gli eventi ONVIF non funzionano, considera l'uso del modulo RTSP generico
