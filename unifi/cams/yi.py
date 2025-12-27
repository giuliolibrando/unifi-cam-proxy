import argparse
import asyncio
import logging
import re
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
        
        # Usa go2rtc per il transcoding invece della camera direttamente
        # go2rtc espone solo lo stream SD (ch0_0.h264) per evitare connessioni multiple
        # Tutti gli stream (video1, video2, video3) usano lo stesso stream SD
        
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
        """Avvia uno stream per generare snapshot dallo stream SD di go2rtc"""
        if not self.snapshot_stream or self.snapshot_stream.poll() is not None:
            # Usa go2rtc per ottenere snapshot dallo stream RTSP (più affidabile)
            # Prendi lo stream SD da go2rtc che è più veloce
            ip_normalized = self.args.ip.replace(".", "_")
            rtsp_url = f"rtsp://127.0.0.1:8554/yi_{ip_normalized}_sd"
            
            cmd = (
                f"ffmpeg -nostdin -y -re -rtsp_transport tcp "
                f'-i "{rtsp_url}" '
                "-r 1 "
                f"-update 1 {self.snapshot_dir}/screen.jpg"
            )
            self.logger.info(f"Avvio stream per snapshot da go2rtc: {cmd}")
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
        """Get RTSP stream URL for the specified stream index"""
        # Usa go2rtc per il transcoding invece della camera direttamente
        # go2rtc espone solo lo stream SD (ch0_0.h264) per evitare connessioni multiple
        # Tutti gli stream (video1, video2, video3) usano lo stesso stream SD
        ip_normalized = self.args.ip.replace(".", "_")
        
        # Tutti gli stream usano lo stesso stream SD
        stream_name = f"yi_{ip_normalized}_sd"
        
        stream_url = f"rtsp://127.0.0.1:8554/{stream_name}"
        self.logger.info(f"Using go2rtc stream SD for {stream_index} (IP: {self.args.ip}): {stream_url}")
        
        # Analyze stream properties on first access
        if not hasattr(self, '_stream_analyzed'):
            self._stream_analyzed = set()
        if stream_index not in self._stream_analyzed:
            await self._analyze_stream(stream_url, stream_index)
            self._stream_analyzed.add(stream_index)
        
        return stream_url
    
    async def _analyze_stream(self, stream_url: str, stream_index: str) -> None:
        """Analyze stream properties using ffmpeg and extract resolution"""
        try:
            # Use ffmpeg to get stream information
            cmd = [
                "ffmpeg", "-rtsp_transport", "tcp",
                "-i", stream_url,
                "-t", "1", "-f", "null", "-"
            ]
            result = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(result.communicate(), timeout=5.0)
            
            # Parse resolution from stderr
            stderr_str = stderr.decode()
            import re
            resolution_match = re.search(r'(\d+)x(\d+)', stderr_str)
            if resolution_match:
                width = int(resolution_match.group(1))
                height = int(resolution_match.group(2))
                self._stream_resolutions[stream_index] = (width, height)
                self.logger.info(f"Stream {stream_index} resolution: {width}x{height}")
        except Exception as e:
            self.logger.warning(f"Failed to analyze stream {stream_index}: {e}")

