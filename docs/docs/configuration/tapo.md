---
sidebar_position: 7
---

# Tapo

Support for Tapo cameras (C200, C220, C500, etc.) via ONVIF protocol and RTSP streams.

## Options

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
  --single-stream {stream1,stream2}
                        Use only one stream for both main and sub (stream1=HD, stream2=SD)
```

## Tapo C200/C220/C500

- [x] Supports full time recording
- [x] Supports motion events via ONVIF (including person detection)
- [x] Supports HD and SD streams
- [x] Supports ONVIF snapshots
- Notes:
  - Requires ONVIF enabled on camera
  - Motion detection must be configured in camera settings
  - Uses PullPointManager for ONVIF events

### Basic Usage

#### Separate streams (default)
```sh
unifi-cam-proxy --mac '{unique MAC}' -H {NVR IP} -i {camera IP} -c /client.pem -t {Adoption token} \
    tapo \
    -u {username} \
    -p {password} \
    --ffmpeg-args='-rtsp_transport tcp -timeout 15000000 -c:v libx264 -preset ultrafast -tune zerolatency -pix_fmt yuv420p -profile:v baseline -level 4.0 -x264-params keyint=30:min-keyint=30:scenecut=0:nal-hrd=cbr -g 30 -sc_threshold 0 -b:v 3000k -maxrate 3000k -bufsize 6000k -bsf:v filter_units=remove_types=6 -c:a aac -ar 32000 -ac 1 -b:a 32k'
```

#### Single stream (HD only)
```sh
unifi-cam-proxy --mac '{unique MAC}' -H {NVR IP} -i {camera IP} -c /client.pem -t {Adoption token} \
    tapo \
    -u {username} \
    -p {password} \
    --single-stream stream1 \
    --ffmpeg-args='-rtsp_transport tcp -timeout 15000000 -c:v libx264 -preset ultrafast -tune zerolatency -pix_fmt yuv420p -profile:v baseline -level 4.0 -x264-params keyint=30:min-keyint=30:scenecut=0:nal-hrd=cbr -g 30 -sc_threshold 0 -b:v 3000k -maxrate 3000k -bufsize 6000k -bsf:v filter_units=remove_types=6 -c:a aac -ar 32000 -ac 1 -b:a 32k'
```

#### Single stream (SD only)
```sh
unifi-cam-proxy --mac '{unique MAC}' -H {NVR IP} -i {camera IP} -c /client.pem -t {Adoption token} \
    tapo \
    -u {username} \
    -p {password} \
    --single-stream stream2 \
    --ffmpeg-args='-rtsp_transport tcp -timeout 15000000 -c:v libx264 -preset ultrafast -tune zerolatency -pix_fmt yuv420p -profile:v baseline -level 4.0 -x264-params keyint=30:min-keyint=30:scenecut=0:nal-hrd=cbr -g 30 -sc_threshold 0 -b:v 3000k -maxrate 3000k -bufsize 6000k -bsf:v filter_units=remove_types=6 -c:a aac -ar 32000 -ac 1 -b:a 32k'
```

### Docker Compose Example

```yaml
version: '3.8'

services:
  camera1:
    build: .
    container_name: camera1
    restart: always
    volumes: [ "./client.pem:/client.pem:ro" ]
    environment:
      - PROTECT_HOST=192.168.70.2
      - PROTECT_TOKEN=your_adoption_token_here
    command: >-
      unifi-cam-proxy -H ${PROTECT_HOST} --mac '02:42:ac:11:00:10'
      -i 192.168.31.10 -c /client.pem -t ${PROTECT_TOKEN}
      tapo -u username -p password
      --ffmpeg-args='-rtsp_transport tcp -timeout 15000000 -c:v libx264 -preset ultrafast -tune zerolatency -pix_fmt yuv420p -profile:v baseline -level 4.0 -x264-params keyint=30:min-keyint=30:scenecut=0:nal-hrd=cbr -g 30 -sc_threshold 0 -b:v 3000k -maxrate 3000k -bufsize 6000k -bsf:v filter_units=remove_types=6 -c:a aac -ar 32000 -ac 1 -b:a 32k'
```

## Configuration

### ONVIF Setup

1. Access camera web interface
2. Go to "Settings" > "Advanced" > "ONVIF"
3. Enable ONVIF and set username/password
4. Ensure motion detection is enabled

### RTSP Streams

Tapo cameras support two RTSP streams:
- **stream1**: Main HD stream
- **stream2**: Sub SD stream

RTSP URLs format:
```
rtsp://username:password@ip:554/stream1
rtsp://username:password@ip:554/stream2
```

### Motion Events

The module supports two types of ONVIF events:
- **Motion Detection**: General movement detection (`IsMotion`)
- **Person Detection**: Specific person detection (`IsPeople`)

When a person is detected, UniFi will receive a "Person detected" notification instead of a generic "Motion detected" notification, allowing for:
- Smart filtering in the UniFi interface
- Separate analytics for people vs general motion
- Enhanced notification system

### FFmpeg Parameters

Optimized FFmpeg parameters for Tapo cameras:
- `-rtsp_transport tcp`: Use TCP for stable RTSP connection
- `-timeout 15000000`: Extended timeout for slow connections
- `-c:v libx264`: H.264 video encoding
- `-preset ultrafast -tune zerolatency`: Low latency optimization
- `-bsf:v filter_units=remove_types=6`: Remove problematic metadata
- `-c:a aac -ar 32000 -ac 1 -b:a 32k`: Optimized audio settings
