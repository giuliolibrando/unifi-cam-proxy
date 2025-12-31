[![unifi-cam-proxy Discord](https://img.shields.io/discord/937237037466124330?color=0559C9&label=Discord&logo=discord&logoColor=%23FFFFFF&style=for-the-badge)](https://discord.gg/Bxk9uGT6MW)

# UniFi Camera Proxy

## About

This enables using non-Ubiquiti cameras within the UniFi Protect ecosystem. This is
particularly useful to use existing RTSP-enabled cameras in the same UI and
mobile app as your other Unifi devices.

Things that work:

* Live streaming
* Full-time recording
* Motion detection with certain cameras
* Smart Detections using [Frigate](https://github.com/blakeblackshear/frigate)

## Camera ID Configuration

For Smart Detect events to work properly with newer UniFi Protect firmware versions, you need to configure the camera ID for each camera in your `docker-compose.yaml` file.

### How to retrieve Camera IDs

1. Access your UniFi Protect API endpoint:
   ```
   https://<NVR_IP>/proxy/protect/api/bootstrap
   ```

2. In the JSON response, find the `cameras` array and locate your camera by MAC address.

3. Copy the `id` field (UUID) for each camera.

4. Add the `CAMERA_ID` environment variable to each camera service in `docker-compose.yaml`:
   ```yaml
   environment:
     - CAMERA_ID=694e718700657603e40474b6
   ```

**Note:** The camera ID is optional but recommended for better Smart Detect event compatibility with UniFi Protect firmware 5.1.217+.

## Documentation

View the documentation at <https://unifi-cam-proxy.com>

## Donations

If you would like to make a donation to support development, please use [Github Sponsors](https://github.com/sponsors/keshavdv).
