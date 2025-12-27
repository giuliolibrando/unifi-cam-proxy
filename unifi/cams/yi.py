import argparse
import logging
import subprocess
import tempfile
from pathlib import Path

from aiohttp import web

from unifi.cams.base import UnifiCamBase


class YiCam(UnifiCamBase):
    """
    Implementazione per le camere Yi che supportano RTSP e ONVIF.
    Gli stream Yi sono tipicamente:
    - ch0_1.h264: stream HD (video1)
    - ch0_0.h264: stream SD (video2/video3)
    """

    def __init__(self, args: argparse.Namespace, logger: logging.Logger) -> None:
        super().__init__(args, logger)
        self.args = args
        self.event_id = 0
        self.snapshot_dir = tempfile.mkdtemp()
        self.snapshot_stream = None
        self.runner = None
        
        # Costruisci gli URL RTSP per le camere Yi
        # ch0_1.h264 = HD stream (video1)
        # ch0_0.h264 = SD stream (video2/video3)
        base_url = f"rtsp://{self.args.username}:{self.args.password}@{self.args.ip}"
        self.stream_source = {
            "video1": f"{base_url}/ch0_1.h264",  # Stream HD
            "video2": f"{base_url}/ch0_0.h264",  # Stream SD
            "video3": f"{base_url}/ch0_0.h264",  # Stream SD (stesso di video2)
        }
        
        if not self.args.snapshot_url:
            self.start_snapshot_stream()

    @classmethod
    def add_parser(cls, parser: argparse.ArgumentParser) -> None:
        super().add_parser(parser)
        parser.add_argument(
            "--username",
            "-u",
            required=True,
            help="Username per la camera Yi",
        )
        parser.add_argument(
            "--password",
            "-p",
            required=True,
            help="Password per la camera Yi",
        )
        parser.add_argument(
            "--snapshot-url",
            "-i",
            default=None,
            type=str,
            required=False,
            help="URL HTTP per ottenere snapshot (opzionale)",
        )
        parser.add_argument(
            "--http-api",
            default=0,
            type=int,
            help="Porta per abilitare HTTP API (default: disabilitato)",
        )
        parser.add_argument(
            "--mqtt-host",
            default=None,
            type=str,
            required=False,
            help="Host MQTT per eventi (opzionale)",
        )
        parser.add_argument(
            "--mqtt-port",
            default=1883,
            type=int,
            required=False,
            help="Porta MQTT (default: 1883)",
        )
        parser.add_argument(
            "--mqtt-topic",
            default=None,
            type=str,
            required=False,
            help="Topic MQTT per eventi (opzionale)",
        )

    def start_snapshot_stream(self) -> None:
        """Avvia uno stream per generare snapshot dallo stream SD"""
        if not self.snapshot_stream or self.snapshot_stream.poll() is not None:
            # Usa lo stream SD (ch0_0.h264) per gli snapshot
            snapshot_url = self.stream_source["video2"]
            cmd = (
                f"ffmpeg -nostdin -y -re -rtsp_transport {self.args.rtsp_transport} "
                f'-i "{snapshot_url}" '
                "-r 1 "
                f"-update 1 {self.snapshot_dir}/screen.jpg"
            )
            self.logger.info(f"Avvio stream per snapshot: {cmd}")
            self.snapshot_stream = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=True
            )

    async def get_snapshot(self) -> Path:
        """Ottiene uno snapshot dalla camera"""
        img_file = Path(self.snapshot_dir, "screen.jpg")
        if self.args.snapshot_url:
            await self.fetch_to_file(self.args.snapshot_url, img_file)
        else:
            self.start_snapshot_stream()
        return img_file

    async def run(self) -> None:
        """Esegue il loop principale per eventi e API HTTP"""
        if self.args.http_api:
            self.logger.info(f"Abilitazione HTTP API sulla porta {self.args.http_api}")

            app = web.Application()

            async def start_motion(request):
                self.logger.debug("Avvio motion")
                await self.trigger_motion_start()
                return web.Response(text="ok")

            async def stop_motion(request):
                self.logger.debug("Stop motion")
                await self.trigger_motion_stop()
                return web.Response(text="ok")

            app.add_routes([web.get("/start_motion", start_motion)])
            app.add_routes([web.get("/stop_motion", stop_motion)])

            self.runner = web.AppRunner(app)
            await self.runner.setup()
            site = web.TCPSite(self.runner, port=self.args.http_api)
            await site.start()

        # TODO: Implementare supporto MQTT per eventi se necessario
        # if self.args.mqtt_host:
        #     await self.setup_mqtt()

    async def close(self) -> None:
        """Pulizia risorse"""
        await super().close()
        if self.runner:
            await self.runner.cleanup()

        if self.snapshot_stream:
            self.snapshot_stream.kill()

    async def get_stream_source(self, stream_index: str) -> str:
        """Restituisce l'URL dello stream per l'indice specificato"""
        return self.stream_source.get(stream_index, self.stream_source["video2"])

