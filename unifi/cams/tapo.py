import argparse
import asyncio
import logging
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, UTC
from pathlib import Path
from typing import Any, Optional, Dict, Tuple

import aiohttp
import httpx
from onvif import ONVIFCamera, ONVIFError
from onvif.managers import PullPointManager

from unifi.cams.base import SmartDetectObjectType, UnifiCamBase


class TapoCam(UnifiCamBase):
    def __init__(self, args: argparse.Namespace, logger: logging.Logger) -> None:
        super().__init__(args, logger)
        self.snapshot_dir = tempfile.mkdtemp()
        self.motion_in_progress: bool = False
        self._last_event_timestamp: float = 0
        self._smart_detect_active: bool = False  # Track if Smart Detect event is active
        self._last_smart_detect_time: float = 0  # Track when last Smart Detect event was sent
        self._pending_generic_motion: bool = False  # Track if we have a pending generic motion event
        self._pending_motion_timestamp: float = 0  # Timestamp of pending motion event
        
        # Motion detection state tracking
        self.last_motion_state: Dict[Tuple[str, str], str] = {}
        
        # Stream resolution cache - will be populated by _analyze_stream
        self._stream_resolutions: Dict[str, Tuple[int, int]] = {}
        
        # Initialize ONVIF camera connection
        try:
            # Find WSDL directory - works both in development and Docker
            import os
            wsdl_dir = None
            possible_paths = [
                "/app/wsdl",  # Docker container path
                os.path.abspath("wsdl"),  # Local development absolute path
                "venv/lib/python3.13/site-packages/onvif/wsdl",  # Relative venv
                None  # Let onvif-zeep-async use default
            ]
            
            for path in possible_paths:
                if path is None:
                    wsdl_dir = None
                    break
                if os.path.exists(path):
                    wsdl_dir = path
                    self.logger.info(f"Using WSDL directory: {wsdl_dir}")
                    break
            
            self.cam = ONVIFCamera(
                self.args.ip, 
                2020,  # Tapo cameras use port 2020 for ONVIF
                self.args.username, 
                self.args.password,
                wsdl_dir
            )
            
            # Initialize services as None - will be set up asynchronously
            self.media = None
            self.events = None
            self.profiles = []
            self.pullpoint_manager = None
            self.pullpoint_service = None
            
        except Exception as e:
            self.logger.warning(f"ONVIF initialization failed: {e}")
            self.logger.info("Will use fallback motion detection method")
            self.cam = None
            self.media = None
            self.events = None
            self.profiles = []
            self.pullpoint_manager = None
            self.pullpoint_service = None
    
    async def _initialize_onvif(self):
        """Initialize ONVIF services asynchronously"""
        if not self.cam:
            return
            
        try:
            await self.cam.update_xaddrs()
            
            # Get device capabilities asynchronously
            self.media = await self.cam.create_media_service()
            self.events = await self.cam.create_events_service()
            
            # Get stream profiles asynchronously
            self.profiles = await self.media.GetProfiles()
            self.logger.info(f"Found {len(self.profiles)} stream profiles")
            
            # Initialize PullPoint manager for motion detection
            try:
                self.pullpoint_manager = await self.cam.create_pullpoint_manager(
                    timedelta(seconds=60), subscription_lost_callback=None
                )
                self.pullpoint_service = await self.cam.create_pullpoint_service()
                self.logger.info("✅ PullPointManager initialized for motion detection")
            except Exception as e:
                self.logger.warning(f"PullPointManager initialization failed: {e}")
                self.pullpoint_manager = None
                self.pullpoint_service = None
            
        except Exception as e:
            self.logger.warning(f"ONVIF async initialization failed: {e}")
            self.logger.info("Will use fallback motion detection method")
            self.cam = None
            self.media = None
            self.events = None
            self.profiles = []
            self.pullpoint_manager = None
            self.pullpoint_service = None

    @classmethod
    def add_parser(cls, parser: argparse.ArgumentParser) -> None:
        super().add_parser(parser)
        parser.add_argument("--username", "-u", required=True, help="Camera username")
        parser.add_argument("--password", "-p", required=True, help="Camera password")
        parser.add_argument(
            "--main-stream",
            "-m",
            default="stream1",
            choices=["stream1", "stream2"],
            help="Main stream profile to use (stream1=HD, stream2=SD)",
        )
        parser.add_argument(
            "--sub-stream", 
            "-s",
            default="stream2",
            choices=["stream1", "stream2"],
            help="Sub stream profile to use (stream1=HD, stream2=SD)",
        )
        parser.add_argument(
            "--snapshot-url",
            default=None,
            help="Custom snapshot URL (optional, will use ONVIF if not provided)",
        )
        parser.add_argument(
            "--single-stream",
            choices=["stream1", "stream2"],
            default=None,
            help="Use only one stream for both main and sub (stream1=HD, stream2=SD)",
        )

    async def get_feature_flags(self) -> dict[str, Any]:
        """Return feature flags indicating Smart Detect support"""
        base_flags = await super().get_feature_flags()
        flags = {
            **base_flags,
            **{
                "smartDetect": [
                    "person",
                ],
            },
        }
        self.logger.info(f"Returning feature flags: {flags}")
        return flags

    async def get_snapshot(self) -> Path:
        img_file = Path(self.snapshot_dir, "screen.jpg")
        
        if self.args.snapshot_url:
            # Use custom snapshot URL if provided
            await self.fetch_to_file(self.args.snapshot_url, img_file)
        else:
            # Usa go2rtc per ottenere snapshot dallo stream RTSP (più affidabile)
            # Prendi lo stream SD da go2rtc che è più veloce
            # Costruisce il nome dello stream basandosi sull'IP della telecamera
            ip_normalized = self.args.ip.replace(".", "_")
            stream_name = f"tapo_{ip_normalized}_sd"
            rtsp_url = f"rtsp://127.0.0.1:8554/{stream_name}"
            
            try:
                # Usa FFmpeg per estrarre un frame dallo stream RTSP
                cmd = (
                    f'ffmpeg -nostdin -y -rtsp_transport tcp -i "{rtsp_url}" '
                    f'-frames:v 1 -f image2 -update 1 "{img_file}"'
                )
                result = await asyncio.create_subprocess_shell(
                    cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(result.communicate(), timeout=5.0)
                
                if result.returncode == 0 and img_file.exists() and img_file.stat().st_size > 0:
                    self.logger.debug(f"Snapshot captured from go2rtc stream: {img_file}")
                    return img_file
                else:
                    self.logger.warning(f"FFmpeg snapshot failed: {stderr.decode()[:200]}")
            except asyncio.TimeoutError:
                self.logger.warning("Snapshot capture timed out")
            except Exception as e:
                self.logger.warning(f"Failed to get snapshot from go2rtc: {e}")
            
            # Fallback: prova HTTP snapshot
            http_urls = [
                f"http://{self.args.username}:{self.args.password}@{self.args.ip}/streaming/snapshot.jpg",
                f"http://{self.args.username}:{self.args.password}@{self.args.ip}/snapshot.jpg",
            ]
            
            snapshot_fetched = False
            for url in http_urls:
                try:
                    if await self.fetch_to_file(url, img_file):
                        snapshot_fetched = True
                        break
                except Exception:
                    continue
            
            # Fallback finale: ONVIF snapshot
            if not snapshot_fetched and self.media and len(self.profiles) > 0:
                try:
                    profile = self.profiles[0]
                    snapshot_uri = await self.media.GetSnapshotUri({
                        'ProfileToken': profile.token
                    })
                    snapshot_uri = snapshot_uri.Uri
                    await self.fetch_to_file(snapshot_uri, img_file)
                except Exception as e:
                    self.logger.warning(f"Failed to get ONVIF snapshot: {e}")
        
        return img_file

    async def get_stream_source(self, stream_index: str) -> str:
        """Get RTSP stream URL for the specified stream index"""
        # Usa go2rtc per il transcoding invece della camera direttamente
        # go2rtc espone solo lo stream SD (stream2) per evitare connessioni multiple
        # Tutti gli stream (video1, video2, video3) usano lo stesso stream SD
        ip_normalized = self.args.ip.replace(".", "_")
        
        # Tutti gli stream usano lo stesso stream SD
        stream_name = f"tapo_{ip_normalized}_sd"
        
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
            result = subprocess.run(
                cmd,
                stderr=subprocess.PIPE,
                stdout=subprocess.PIPE,
                timeout=10
            )
            
            output = result.stderr.decode('utf-8', errors='ignore')
            
            # Extract resolution from output (format: "1920x1080" or "Stream #0:0: Video: h264, 1920x1080")
            width, height = None, None
            import re
            # Try to find resolution in format WIDTHxHEIGHT
            resolution_match = re.search(r'(\d{3,5})x(\d{3,5})', output)
            if resolution_match:
                width = int(resolution_match.group(1))
                height = int(resolution_match.group(2))
                self._stream_resolutions[stream_index] = (width, height)
                self.logger.info(f"Stream {stream_index} resolution detected: {width}x{height}")
            
            # Extract key information for logging
            info_lines = []
            for line in output.split('\n'):
                if any(keyword in line.lower() for keyword in [
                    'stream', 'video', 'audio', 'resolution', 'fps', 
                    'bitrate', 'codec', 'pixel', 'yuv', 'sar', 'dar'
                ]):
                    info_lines.append(line.strip())
            
            if info_lines:
                self.logger.info(f"Stream {stream_index} analysis:")
                for line in info_lines[:15]:  # Limit to first 15 lines
                    self.logger.info(f"  {line}")
            else:
                self.logger.warning(f"Could not extract stream info for {stream_index}")
                
        except subprocess.TimeoutExpired:
            self.logger.warning(f"Stream analysis timeout for {stream_index}")
        except Exception as e:
            self.logger.warning(f"Could not analyze stream {stream_index}: {e}")

    async def maybe_end_motion_event(self, timestamp: float) -> None:
        """End motion event if no new events have occurred"""
        await asyncio.sleep(2)  # Wait 2 seconds
        
        if timestamp == self._last_event_timestamp:
            if self.motion_in_progress:
                self.motion_in_progress = False
                # Don't reset _smart_detect_active immediately - keep it active for a bit longer
                # to prevent generic motion events from overriding Smart Detect events
                # The flag will be reset when a new motion event starts (if not Smart Detect)
                self.logger.info("Motion event ended")
                await self.trigger_motion_stop()

    async def run(self) -> None:
        """Main event loop for handling motion events using PullPointManager with auto-reconnect"""
        self.logger.info("Starting motion detection for Tapo camera using PullPointManager")
        
        # Motion detection filters - based on your working script
        motion_filters = [
            ("tns1:RuleEngine/CellMotionDetector/Motion", ["IsMotion"]),
            ("tns1:RuleEngine/PeopleDetector/People", ["IsPeople"]),
        ]
        
        retry_delay = 5  # Start with 5 seconds delay
        max_retry_delay = 60  # Max 60 seconds delay
        
        while True:
            try:
                # Initialize/reinitialize ONVIF services
                await self._initialize_onvif()
                
                # Check if PullPoint is available
                if not self.pullpoint_service:
                    self.logger.warning(f"PullPoint service not available, retrying in {retry_delay} seconds...")
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, max_retry_delay)  # Exponential backoff
                    continue
                
                # Reset retry delay on successful connection
                retry_delay = 5
                
                self.logger.info("✅ PullPoint service connected, starting motion detection")
                self.logger.info("Starting PullPoint motion detection with filters:")
                for topic, keys in motion_filters:
                    self.logger.info(f"  • {topic}  Keys={','.join(keys)}")
                
                # Create PullMessages request
                req = self.pullpoint_service.create_type("PullMessages")
                req.MessageLimit = 10
                req.Timeout = timedelta(seconds=2)
                
                # Main loop for pulling messages
                while True:
                    try:
                        # Pull messages from the subscription
                        resp = await self.pullpoint_service.PullMessages(req)
                        
                        # Process notifications
                        await self._process_pullpoint_notifications(resp, motion_filters)
                        
                        await asyncio.sleep(0.02)  # micro pausa per non saturare
                        
                    except Exception as e:
                        self.logger.warning(f"PullMessages error: {e}; reconnecting...")
                        # Break inner loop to reconnect
                        break
                        
            except Exception as e:
                self.logger.warning(f"ONVIF connection error: {e}; retrying in {retry_delay} seconds...")
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_retry_delay)  # Exponential backoff
                continue
    
    def _iter_notifications(self, msgs) -> list:
        """Normalize PullMessages response"""
        if msgs is None:
            return []
        # dict-like
        if isinstance(msgs, dict):
            return msgs.get("NotificationMessage", []) or []
        # oggetto zeep
        try:
            nm = getattr(msgs, "NotificationMessage", None)
            return nm or []
        except Exception:
            return []

    async def _process_pullpoint_notifications(self, resp, filters):
        """Process PullPoint notifications - simplified logic"""
        for n in self._iter_notifications(resp):
            # Extract topic
            topic = None
            try:
                topic = (
                    getattr(n, "Topic", None) or {}
                ).get("_value_1") if isinstance(getattr(n, "Topic", None), dict) else getattr(getattr(n, "Topic", None), "_value_1", None)
            except Exception:
                topic = None
            if not topic:
                continue

            # Extract payload data
            payload = getattr(n, "Message", None)
            payload_val = getattr(payload, "_value_1", {}) if payload else {}
            data = payload_val.get("Data", {}) if isinstance(payload_val, dict) else getattr(payload_val, "Data", {})
            simple_items = []
            try:
                simple_items = data.get("SimpleItem", []) if isinstance(data, dict) else getattr(data, "SimpleItem", []) or []
            except Exception:
                simple_items = []

            # Apply topic filter
            filt_keys = None
            for ftopic, fkeys in filters:
                if ftopic == topic:
                    filt_keys = fkeys or None
                    break
            if filt_keys is None:
                continue

            # Process motion events
            for it in simple_items:
                name = it.get("Name") if isinstance(it, dict) else getattr(it, "Name", None)
                value = it.get("Value") if isinstance(it, dict) else getattr(it, "Value", None)
                if not name:
                    continue
                if filt_keys and name not in filt_keys:
                    continue

                key = (topic, name)
                val = str(value).lower()
                
                # Check for state change
                if val != self.last_motion_state.get(key):
                    self.last_motion_state[key] = val
                    
                    if val == "true":
                        self._last_event_timestamp = time.time()
                        
                        if name == "IsPeople":
                            # Person detected - upgrade existing motion event to Smart Detect
                            if self.motion_in_progress and self._motion_object_type is None:
                                # Generic motion is active, upgrade it to Person Smart Detect
                                self._smart_detect_active = True
                                self._last_smart_detect_time = time.time()
                                await self.trigger_motion_start(SmartDetectObjectType.PERSON)
                            elif not self.motion_in_progress:
                                # No motion active, send Person Smart Detect directly
                                self.motion_in_progress = True
                                self._smart_detect_active = True
                                self._last_smart_detect_time = time.time()
                                await self.trigger_motion_start(SmartDetectObjectType.PERSON)
                            # If Person event already active, ignore duplicate
                            
                        elif name == "IsMotion":
                            # Generic motion detected - send motion event immediately
                            # If Person arrives later, we'll upgrade it to Smart Detect
                            if not self.motion_in_progress:
                                self.motion_in_progress = True
                                await self.trigger_motion_start()
                            # If motion already active, ignore duplicate
                        else:
                            # Other motion types - send immediately
                            if not self.motion_in_progress:
                                self.motion_in_progress = True
                            await self.trigger_motion_start()
                        
                        # Schedule motion end check
                        asyncio.ensure_future(
                            self.maybe_end_motion_event(self._last_event_timestamp)
                        )
                    
                    elif val == "false" and self.motion_in_progress:
                        # Motion ended - handled by maybe_end_motion_event
                        pass


    

    


    def get_extra_ffmpeg_args(self, stream_index: str) -> str:
        """Get extra FFmpeg arguments for stream processing"""
        # go2rtc fa già il transcoding video, quindi usiamo -c:v copy
        # Per l'audio, forziamo la transcodifica in AAC perché FLV richiede AAC
        # e go2rtc potrebbe passare PCM_ALAW dalla telecamera originale
        return (
            "-c:v copy -bsf:v filter_units=remove_types=6 "
            "-c:a aac -ar 32000 -ac 1 -b:a 32k"
        )

    async def get_video_settings(self) -> dict[str, Any]:
        """Get current video settings from camera"""
        try:
            # Get imaging settings from first profile
            profile = self.profiles[0]
            imaging = self.cam.create_imaging_service()
            imaging_settings = imaging.GetImagingSettings({
                'VideoSourceToken': profile.VideoSourceConfiguration.SourceToken
            })
            
            return {
                "brightness": int(imaging_settings.Brightness * 100 / 255),
                "contrast": int(imaging_settings.Contrast * 100 / 255),
                "saturation": int(imaging_settings.ColorSaturation * 100 / 255),
                "sharpness": int(imaging_settings.Sharpness * 100 / 255),
            }
        except Exception as e:
            self.logger.warning(f"Could not get video settings: {e}")
            return {}

    async def change_video_settings(self, options: dict[str, Any]) -> None:
        """Change video settings on camera"""
        try:
            profile = self.profiles[0]
            imaging = self.cam.create_imaging_service()
            
            # Get current settings
            current_settings = imaging.GetImagingSettings({
                'VideoSourceToken': profile.VideoSourceConfiguration.SourceToken
            })
            
            # Update settings based on options
            if 'brightness' in options:
                current_settings.Brightness = int(options['brightness'] * 255 / 100)
            if 'contrast' in options:
                current_settings.Contrast = int(options['contrast'] * 255 / 100)
            if 'saturation' in options:
                current_settings.ColorSaturation = int(options['saturation'] * 255 / 100)
            if 'sharpness' in options:
                current_settings.Sharpness = int(options['sharpness'] * 255 / 100)
            
            # Apply settings
            imaging.SetImagingSettings({
                'VideoSourceToken': profile.VideoSourceConfiguration.SourceToken,
                'ImagingSettings': current_settings
            })
            
            self.logger.info(f"Updated video settings: {options}")
            
        except Exception as e:
            self.logger.warning(f"Could not change video settings: {e}")

    async def _cleanup_onvif(self):
        """Clean up ONVIF connections"""
        try:
            if self.pullpoint_service:
                await self.pullpoint_service.close()
            if self.pullpoint_manager:
                await self.pullpoint_manager.stop()
            if self.cam:
                # Close camera connection
                if hasattr(self.cam, 'close'):
                    await self.cam.close()
                elif hasattr(self.cam, 'async_close'):
                    await self.cam.async_close()
        except Exception as e:
            self.logger.debug(f"Error during ONVIF cleanup: {e}")

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Cleanup when exiting context"""
        await self._cleanup_onvif()
        await super().__aexit__(exc_type, exc_val, exc_tb)
