import argparse
import asyncio
import logging
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
        
        # Motion detection state tracking
        self.last_motion_state: Dict[Tuple[str, str], str] = {}
        
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

    async def get_snapshot(self) -> Path:
        img_file = Path(self.snapshot_dir, "screen.jpg")
        
        if self.args.snapshot_url:
            # Use custom snapshot URL if provided
            await self.fetch_to_file(self.args.snapshot_url, img_file)
        else:
            # Use ONVIF snapshot
            try:
                # Get first profile for snapshot
                profile = self.profiles[0]
                snapshot_uri = await self.media.GetSnapshotUri({
                    'ProfileToken': profile.token
                })
                snapshot_uri = snapshot_uri.Uri
                
                await self.fetch_to_file(snapshot_uri, img_file)
            except Exception as e:
                self.logger.warning(f"Failed to get ONVIF snapshot: {e}")
                # Fallback to HTTP snapshot
                fallback_url = f"http://{self.args.ip}/snapshot.jpg"
                await self.fetch_to_file(fallback_url, img_file)
        
        return img_file

    async def get_stream_source(self, stream_index: str) -> str:
        """Get RTSP stream URL for the specified stream index"""
        # If single-stream is specified, use it for both main and sub
        if self.args.single_stream:
            stream_profile = self.args.single_stream
            self.logger.info(f"Using single stream: {stream_profile}")
        else:
            # Use separate streams for main and sub
            if stream_index == "video1":
                stream_profile = self.args.main_stream
            else:
                stream_profile = self.args.sub_stream
        
        # Tapo cameras typically use these RTSP URLs:
        # stream1: rtsp://username:password@ip:554/stream1
        # stream2: rtsp://username:password@ip:554/stream2
        
        return (
            f"rtsp://{self.args.username}:{self.args.password}@{self.args.ip}:554"
            f"/{stream_profile}"
        )

    async def maybe_end_motion_event(self, timestamp: float) -> None:
        """End motion event if no new events have occurred"""
        await asyncio.sleep(2)  # Wait 2 seconds
        
        if timestamp == self._last_event_timestamp:
            if self.motion_in_progress:
                self.motion_in_progress = False
                self.logger.info("Motion event ended")
                await self.trigger_motion_stop()

    async def run(self) -> None:
        """Main event loop for handling motion events using PullPointManager"""
        self.logger.info("Starting motion detection for Tapo camera using PullPointManager")
        
        # Initialize ONVIF services
        await self._initialize_onvif()
        
        # Check if PullPoint is available
        if not self.pullpoint_service:
            self.logger.warning("PullPoint service not available, motion detection not possible")
            return
        
        # Motion detection filters - based on your working script
        motion_filters = [
            ("tns1:RuleEngine/CellMotionDetector/Motion", ["IsMotion"]),
            ("tns1:RuleEngine/PeopleDetector/People", ["IsPeople"]),
        ]
        
        self.logger.info("Starting PullPoint motion detection with filters:")
        for topic, keys in motion_filters:
            self.logger.info(f"  • {topic}  Keys={','.join(keys)}")
        
        # Create PullMessages request
        req = self.pullpoint_service.create_type("PullMessages")
        req.MessageLimit = 10
        req.Timeout = timedelta(seconds=2)
        
        while True:
            try:
                # Pull messages from the subscription
                resp = await self.pullpoint_service.PullMessages(req)
                
                # Process notifications
                await self._process_pullpoint_notifications(resp, motion_filters)
                
                await asyncio.sleep(0.02)  # micro pausa per non saturare
                
            except Exception as e:
                self.logger.warning(f"PullMessages error: {e}; retry...")
                await asyncio.sleep(0.5)
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
        """Process PullPoint notifications using your working approach"""
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
                        # Motion detected!
                        self._last_event_timestamp = time.time()
                        
                        if not self.motion_in_progress:
                            self.motion_in_progress = True
                            
                            # Determine event type based on ONVIF event
                            if name == "IsPeople":
                                # Person detected - send smart detect event
                                self.logger.info(f"👤 Person detected via PullPoint: {topic} {name}={value}")
                                await self.trigger_motion_start(SmartDetectObjectType.PERSON)
                            else:
                                # General motion detected
                                self.logger.info(f"🚨 Motion detected via PullPoint: {topic} {name}={value}")
                                await self.trigger_motion_start()
                        
                        # End motion event after 2 seconds of no updates
                        asyncio.ensure_future(
                            self.maybe_end_motion_event(self._last_event_timestamp)
                        )
                    
                    elif val == "false" and self.motion_in_progress:
                        # Motion ended
                        self.logger.info(f"Motion ended: {topic} {name}={value}")


    

    


    def get_extra_ffmpeg_args(self, stream_index: str) -> str:
        """Get extra FFmpeg arguments for stream processing"""
        # Tapo cameras typically use H.264 encoding
        # Adjust tick rate based on stream quality
        if stream_index == "video1":
            # Main stream (HD) - higher frame rate
            tick_rate = "30000/1001"  # ~30 fps
        else:
            # Sub stream (SD) - lower frame rate
            tick_rate = "15000/1001"  # ~15 fps
        
        return (
            f"-c:v copy -ar 32000 -ac 1 -codec:a aac -b:a 32k "
            f'-vbsf "h264_metadata=tick_rate={tick_rate}"'
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
