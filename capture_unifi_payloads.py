#!/usr/bin/env python3
"""
Script per catturare e analizzare i payload WebSocket da una telecamera UniFi originale.
Questo script intercetta il traffico sulla porta 7442 e estrae i payload JSON.
"""
import asyncio
import json
import logging
import ssl
import websockets
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def capture_websocket_traffic(host: str, port: int, token: str, mac: str, cert: str):
    """Cattura il traffico WebSocket da una telecamera UniFi originale"""
    uri = f"wss://{host}:{port}/camera/1.0/ws?token={token}"
    headers = {"camera-mac": mac}
    
    # Set up SSL context
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    ssl_context.load_cert_chain(cert, cert)
    
    logger.info(f"Connecting to {uri}")
    logger.info("=" * 80)
    logger.info("CAPTURING WEBSOCKET TRAFFIC FROM UNIFI CAMERA")
    logger.info("=" * 80)
    
    try:
        async with websockets.connect(
            uri,
            additional_headers=headers,
            ssl=ssl_context,
            subprotocols=["secure_transfer"],
        ) as ws:
            logger.info("✅ Connected! Listening for messages...")
            logger.info("Press Ctrl+C to stop")
            logger.info("=" * 80)
            
            message_count = 0
            while True:
                try:
                    msg = await ws.recv()
                    message_count += 1
                    
                    try:
                        msg_dict = json.loads(msg)
                        fn = msg_dict.get("functionName", "unknown")
                        payload = msg_dict.get("payload", {})
                        
                        # Log all messages, but highlight Smart Detect related ones
                        if fn in ["EventSmartDetect", "EventAnalytics"]:
                            logger.info("=" * 80)
                            logger.info(f"🎯 MESSAGE #{message_count}: {fn}")
                            logger.info("=" * 80)
                            logger.info(f"Full message:\n{json.dumps(msg_dict, indent=2)}")
                            logger.info("=" * 80)
                        elif fn in ["GetRequest", "ChangeSmartDetectSettings"]:
                            logger.info(f"📋 MESSAGE #{message_count}: {fn}")
                            logger.info(f"Payload:\n{json.dumps(payload, indent=2)}")
                        else:
                            logger.debug(f"MESSAGE #{message_count}: {fn}")
                            
                    except json.JSONDecodeError:
                        logger.debug(f"MESSAGE #{message_count}: (non-JSON)")
                        
                except websockets.exceptions.ConnectionClosed:
                    logger.info("Connection closed")
                    break
                    
    except KeyboardInterrupt:
        logger.info("\nStopped by user")
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Capture WebSocket traffic from UniFi camera")
    parser.add_argument("--host", required=True, help="UniFi Protect host")
    parser.add_argument("--token", required=True, help="Adoption token")
    parser.add_argument("--mac", required=True, help="Camera MAC address")
    parser.add_argument("--cert", required=True, help="Path to client.pem")
    parser.add_argument("--port", type=int, default=7442, help="WebSocket port (default: 7442)")
    
    args = parser.parse_args()
    
    asyncio.run(capture_websocket_traffic(
        args.host,
        args.port,
        args.token,
        args.mac,
        args.cert
    ))

