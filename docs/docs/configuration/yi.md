---
sidebar_position: 8
---

# Yi Camera

Supporto per le camere Yi che utilizzano protocolli RTSP e ONVIF. Le camere Yi supportano anche l'invio di eventi tramite MQTT.

## Opzioni

```text
optional arguments:
  --username USERNAME, -u USERNAME
                        Username per la camera Yi
  --password PASSWORD, -p PASSWORD
                        Password per la camera Yi
  --rtsp-transport {tcp,udp}
                        Trasporto RTSP (default: tcp)
  --snapshot-url SNAPSHOT_URL, -i SNAPSHOT_URL
                        URL HTTP per ottenere snapshot (opzionale)
  --http-api HTTP_API
                        Porta per abilitare HTTP API (default: disabilitato)
  --mqtt-host MQTT_HOST
                        Host MQTT per eventi (opzionale)
  --mqtt-port MQTT_PORT
                        Porta MQTT (default: 1883)
  --mqtt-topic MQTT_TOPIC
                        Topic MQTT per eventi (opzionale)
```

## Caratteristiche

- [x] Supporta registrazione continua
- [x] Supporta stream HD e SD
- [x] Supporta RTSP nativo
- [x] Supporto ONVIF (per eventi futuri)
- [x] Supporto MQTT (per eventi futuri)
- Note:
  - Gli stream RTSP devono essere abilitati sulla camera
  - Le credenziali RTSP devono essere configurate sulla camera

## Stream RTSP

Le camere Yi supportano due stream RTSP:
- **ch0_1.h264**: Stream HD (video1)
- **ch0_0.h264**: Stream SD (video2/video3)

Formato URL RTSP:
```
rtsp://username:password@ip/ch0_1.h264  # HD
rtsp://username:password@ip/ch0_0.h264  # SD
```

## Utilizzo Base

### Comando Standalone

```sh
unifi-cam-proxy --mac '{unique MAC}' -H {NVR IP} -i {camera IP} -c /client.pem -t {Adoption token} \
    yi \
    -u {username} \
    -p {password}
```

### Esempio con trasporto TCP

```sh
unifi-cam-proxy --mac '{unique MAC}' -H {NVR IP} -i {camera IP} -c /client.pem -t {Adoption token} \
    yi \
    -u {username} \
    -p {password} \
    --rtsp-transport tcp
```

### Esempio con HTTP API per eventi esterni

```sh
unifi-cam-proxy --mac '{unique MAC}' -H {NVR IP} -i {camera IP} -c /client.pem -t {Adoption token} \
    yi \
    -u {username} \
    -p {password} \
    --http-api 8080
```

Questo abilita un'API HTTP su porta 8080 con endpoint:
- `http://localhost:8080/start_motion` - Avvia evento motion
- `http://localhost:8080/stop_motion` - Termina evento motion

## Docker Compose Example

```yaml
version: '3.8'

services:
  go2rtc:
    image: alexxit/go2rtc:latest
    container_name: go2rtc_yi
    restart: always
    network_mode: host
    volumes:
      - "./go2rtc.yaml:/config/go2rtc.yaml:ro"
    environment:
      - TZ=Europe/Rome

  camera_yi_1:
    build: .
    image: unifi-cam-proxy_camera_yi:latest
    container_name: camera_yi_1
    restart: always
    network_mode: host
    depends_on:
      - go2rtc
    volumes: 
      - "./client.pem:/client.pem:ro"
    environment:
      - PROTECT_HOST=${PROTECT_HOST}
      - PROTECT_TOKEN=${PROTECT_TOKEN}
      - CAMERA_IP=192.168.31.11
      - CAMERA_MAC=02:42:ac:11:00:11
      - CAMERA_NAME=Yi Camera 11
      - CAMERA_USERNAME=${YI_USERNAME}
      - CAMERA_PASSWORD=${YI_PASSWORD}
      - TZ=Europe/Rome
    command: >-
      sh -c "
      sleep 5 &&
      unifi-cam-proxy -H $${PROTECT_HOST} --mac $${CAMERA_MAC}
      --model 'UVC G4 Pro' -i $${CAMERA_IP} -c /client.pem -t $${PROTECT_TOKEN}
      --name \"$${CAMERA_NAME}\" yi -u $${CAMERA_USERNAME} -p $${CAMERA_PASSWORD}
      "
```

## Configurazione

### Setup RTSP sulla Camera Yi

1. Accedi all'interfaccia web della camera Yi
2. Vai a "Impostazioni" > "RTSP"
3. Abilita RTSP e configura username/password
4. Verifica che gli stream siano attivi:
   - `rtsp://username:password@ip/ch0_1.h264` (HD)
   - `rtsp://username:password@ip/ch0_0.h264` (SD)

### Configurazione go2rtc

Aggiungi la configurazione per la camera Yi nel file `go2rtc.yaml`:

```yaml
streams:
  yi_192_168_31_11_sd:
    - rtsp://username:password@192.168.31.11/ch0_0.h264#rtsp_transport=tcp#timeout=30s
    - "ffmpeg:rtsp://username:password@192.168.31.11/ch0_0.h264#rtsp_transport=tcp#timeout=30s#video=copy#audio=aac#audio=ar=32000#audio=ac=1#audio=b=32k"
  
  yi_192_168_31_11_hd:
    - rtsp://username:password@192.168.31.11/ch0_1.h264#rtsp_transport=tcp#timeout=30s
    - "ffmpeg:rtsp://username:password@192.168.31.11/ch0_1.h264#rtsp_transport=tcp#timeout=30s#video=copy#audio=aac#audio=ar=32000#audio=ac=1#audio=b=32k"
```

### Eventi Motion

Attualmente, gli eventi motion possono essere attivati tramite:
- **HTTP API**: Se abilitata con `--http-api`, puoi chiamare gli endpoint per triggerare eventi
- **MQTT**: Supporto futuro per eventi MQTT nativi dalla camera
- **ONVIF**: Supporto futuro per eventi ONVIF

## Troubleshooting

### Problemi di connessione RTSP

Se lo stream non si connette:
1. Verifica che RTSP sia abilitato sulla camera
2. Controlla username e password
3. Prova con `--rtsp-transport tcp` per maggiore stabilità
4. Verifica che la camera sia raggiungibile dalla rete

### Test dello stream RTSP

Puoi testare lo stream direttamente con VLC o ffmpeg:

```sh
# Test stream SD
ffplay rtsp://username:password@192.168.31.11/ch0_0.h264

# Test stream HD
ffplay rtsp://username:password@192.168.31.11/ch0_1.h264
```

### Log e Debug

Per vedere i log dettagliati, usa il flag `--verbose`:

```sh
unifi-cam-proxy --verbose --mac '{MAC}' -H {NVR IP} -i {camera IP} -c /client.pem -t {token} \
    yi -u {username} -p {password}
```

