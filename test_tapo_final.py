#!/usr/bin/env python3

import asyncio
import argparse
import logging

# Importa solo le parti necessarie
import sys
sys.path.insert(0, '.')

from unifi.cams.tapo import TapoCam

class MockUnifiProtectApi:
    """Mock UniFi Protect API for testing"""
    def __init__(self):
        self.bootstrap = MockBootstrap()

class MockBootstrap:
    """Mock Bootstrap"""
    def __init__(self):
        self.cameras = {}

class MockArgs:
    def __init__(self):
        self.ip = "192.168.31.19"
        self.username = "username"
        self.password = "password"
        self.main_stream = "stream1"
        self.sub_stream = "stream2"
        self.snapshot_url = None
        self.cert = "/tmp/test.pem"

async def test_tapo_pullpoint():
    """Test Tapo camera with PullPoint motion detection"""
    
    # Setup logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("TapoTest")
    
    # Create mock args
    args = MockArgs()
    
    # Create Tapo camera instance
    logger.info("🎯 Testing Tapo camera with PullPoint...")
    camera = TapoCam(args, logger)
    
    try:
        # Initialize ONVIF
        await camera._initialize_onvif()
        
        if camera.pullpoint_service:
            logger.info("✅ PullPoint service available!")
            
            # Test motion detection for 30 seconds
            logger.info("Testing motion detection for 30 seconds...")
            logger.info("🚨 MOVE IN FRONT OF THE CAMERA TO TEST MOTION DETECTION!")
            
            # Create test task with timeout
            async def test_motion():
                motion_filters = [
                    ("tns1:RuleEngine/CellMotionDetector/Motion", ["IsMotion"]),
                    ("tns1:RuleEngine/PeopleDetector/People", ["IsPeople"]),
                ]
                
                req = camera.pullpoint_service.create_type("PullMessages")
                req.MessageLimit = 10
                from datetime import timedelta
                req.Timeout = timedelta(seconds=2)
                
                motion_count = 0
                
                for i in range(150):  # 30 seconds with 0.2s intervals
                    try:
                        resp = await camera.pullpoint_service.PullMessages(req)
                        
                        # Check for notifications
                        for n in camera._iter_notifications(resp):
                            # Extract topic
                            topic = None
                            try:
                                topic = (
                                    getattr(n, "Topic", None) or {}
                                ).get("_value_1") if isinstance(getattr(n, "Topic", None), dict) else getattr(getattr(n, "Topic", None), "_value_1", None)
                            except Exception:
                                topic = None
                            
                            if topic:
                                # Extract payload data
                                payload = getattr(n, "Message", None)
                                payload_val = getattr(payload, "_value_1", {}) if payload else {}
                                data = payload_val.get("Data", {}) if isinstance(payload_val, dict) else getattr(payload_val, "Data", {})
                                simple_items = []
                                try:
                                    simple_items = data.get("SimpleItem", []) if isinstance(data, dict) else getattr(data, "SimpleItem", []) or []
                                except Exception:
                                    simple_items = []
                                
                                # Check for motion
                                for it in simple_items:
                                    name = it.get("Name") if isinstance(it, dict) else getattr(it, "Name", None)
                                    value = it.get("Value") if isinstance(it, dict) else getattr(it, "Value", None)
                                    
                                    if name and str(value).lower() == "true":
                                        motion_count += 1
                                        logger.info(f"🚨 MOTION DETECTED! Topic: {topic}, {name}={value}")
                        
                        await asyncio.sleep(0.2)
                        
                    except Exception as e:
                        logger.debug(f"PullMessages error: {e}")
                        await asyncio.sleep(0.5)
                
                return motion_count
            
            motion_events = await test_motion()
            logger.info(f"📊 Test completed! Motion events detected: {motion_events}")
            
        else:
            logger.error("❌ PullPoint service not available")
            
    except Exception as e:
        logger.error(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        # Cleanup
        await camera._cleanup_onvif()

if __name__ == "__main__":
    print("🎯 TAPO CAMERA PULLPOINT TEST")
    print("📷 Camera: 192.168.31.19")
    print("🔑 Credentials: username/password")
    print("⏱️  Duration: 30 seconds")
    print("🚨 MOVE IN FRONT OF THE CAMERA TO TEST!")
    print("=" * 50)
    
    try:
        asyncio.run(test_tapo_pullpoint())
    except KeyboardInterrupt:
        print("\n⏹️  Test interrupted by user")
