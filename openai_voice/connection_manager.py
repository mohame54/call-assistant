import json
import asyncio
import websockets
import logging
import time
from typing import Optional, Dict, Any
from .base import EventHandler

logger = logging.getLogger(__name__)


class OpenAIConnectionManager(EventHandler):
    """Manages WebSocket connection to OpenAI Realtime API."""
    
    def __init__(self, api_key: str, model: str = "gpt-4o-realtime-preview"):
        self.api_key = api_key
        self.model = model
        self.websocket: Optional[websockets.WebSocketServerProtocol] = None
        self.is_connected = False
        
        # Keepalive settings (will be updated from config)
        self.ping_interval = 15  # Send ping every 15 seconds
        self.ping_timeout = 10   # Wait 10 seconds for pong
        self.close_timeout = 10  # Wait 10 seconds for close
        self.last_ping_time = 0
        self.keepalive_task: Optional[asyncio.Task] = None
        
        # Event callbacks
        self.on_message_received: Optional[callable] = None
        self.on_connection_lost: Optional[callable] = None
        self.on_connection_established: Optional[callable] = None
    
    async def connect(self) -> None:
        """Establish WebSocket connection to OpenAI."""
        if self.is_connected:
            logger.warning("Already connected to OpenAI")
            return
        
        try:
            url = f"wss://api.openai.com/v1/realtime?model={self.model}"
            headers = {"Authorization": f"Bearer {self.api_key}"}
            
            logger.info(f"Connecting to OpenAI Realtime API: {url}")
            self.websocket = await websockets.connect(
                url, 
                additional_headers=headers,
                ping_interval=self.ping_interval,
                ping_timeout=self.ping_timeout,
                close_timeout=self.close_timeout
            )
            self.is_connected = True
            
            # Start keepalive task
            self.keepalive_task = asyncio.create_task(self._keepalive_loop())
            
            if self.on_connection_established:
                await self.on_connection_established()
            
            logger.info("Successfully connected to OpenAI Realtime API")
            
        except Exception as e:
            logger.error(f"Failed to connect to OpenAI: {e}")
            self.is_connected = False
            raise
    
    async def disconnect(self) -> None:
        """Close WebSocket connection."""
        self.is_connected = False
        
        # Cancel keepalive task
        if self.keepalive_task and not self.keepalive_task.done():
            self.keepalive_task.cancel()
            try:
                await self.keepalive_task
            except asyncio.CancelledError:
                pass
        
        if self.websocket:
            try:
                await self.websocket.close()
            except Exception as e:
                logger.debug(f"Error closing websocket: {e}")
            self.websocket = None
            
        logger.info("Disconnected from OpenAI Realtime API")
    
    async def send_message(self, message: Dict[str, Any]) -> None:
        """Send a message to OpenAI."""
        if not self.websocket or not self.is_connected:
            raise RuntimeError("Not connected to OpenAI")
        
        try:
            await self.websocket.send(json.dumps(message))
            logger.debug(f"📤 Sent to OpenAI: {message.get('type', 'unknown')}")
        except Exception as e:
            logger.error(f"Failed to send message to OpenAI: {e}")
            raise
    
    async def receive_message(self) -> Optional[Dict[str, Any]]:
        """Receive a message from OpenAI."""
        if not self.websocket or not self.is_connected:
            return None
        
        try:
            message = await asyncio.wait_for(self.websocket.recv(), timeout=1.0)
            data = json.loads(message)
            logger.debug(f"📥 Received from OpenAI: {data.get('type', 'unknown')}")
            return data
        except asyncio.TimeoutError:
            return None
        except websockets.exceptions.ConnectionClosed as e:
            logger.error(f"❌ OpenAI connection closed: {e}")
            self.is_connected = False
            if self.on_connection_lost:
                await self.on_connection_lost(e)
            return None
        except json.JSONDecodeError as e:
            logger.error(f"❌ Failed to parse OpenAI message: {e}")
            return None
        except Exception as e:
            logger.error(f"❌ Unexpected error receiving message: {e}")
            return None
    
    async def start_message_loop(self) -> None:
        """Start the message receiving loop."""
        logger.info("🔄 Starting OpenAI message loop...")
        
        try:
            while self.is_connected:
                message = await self.receive_message()
                if message and self.on_message_received:
                    await self.on_message_received(message)
                    
        except Exception as e:
            logger.error(f"Message loop error: {e}")
            self.is_connected = False
            if self.on_connection_lost:
                await self.on_connection_lost(e)
        
        logger.warning("🛑 OpenAI message loop ended")
    
    async def _keepalive_loop(self) -> None:
        """Send periodic pings to keep connection alive."""
        logger.info("🫀 Starting keepalive loop")
        try:
            while self.is_connected:
                await asyncio.sleep(self.ping_interval)
                if self.websocket and self.is_connected:
                    try:
                        # Send a ping
                        await self.websocket.ping()
                        self.last_ping_time = time.time()
                        logger.debug(f"📡 Sent keepalive ping")
                    except Exception as e:
                        logger.error(f"❌ Keepalive ping failed: {e}")
                        self.is_connected = False
                        if self.on_connection_lost:
                            await self.on_connection_lost(e)
                        break
        except asyncio.CancelledError:
            logger.info("🫀 Keepalive loop cancelled")
        except Exception as e:
            logger.error(f"❌ Keepalive loop error: {e}")
    
    async def handle_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Handle events by sending them to OpenAI."""
        await self.send_message({"type": event_type, **data})

