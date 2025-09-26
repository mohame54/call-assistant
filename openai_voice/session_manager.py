import json
import asyncio
import logging
from typing import Dict, Any, Optional
from .base import EventHandler
from config import SessionConfig

logger = logging.getLogger(__name__)


class SessionManager(EventHandler):
    """Manages OpenAI session initialization and configuration."""
    
    def __init__(self, session_config: SessionConfig):
        self.session_config = session_config
        self.session_created = False
        
        # Event callbacks
        self.on_session_created: Optional[callable] = None
        self.on_session_error: Optional[callable] = None
    
    async def initialize_session(self, websocket, tools: Dict[str, Any]) -> None:
        """Initialize the OpenAI session with configuration."""
        tools_config = []
        if tools:
            for tool in tools.values():
                tool_json = tool.json
                tool_json.update({"type": "function"})
                tools_config.append(tool_json)
        
        session_update = {
            "type": "session.update",
            "session": {
                "type": "realtime",
                "instructions": self.session_config.openai_config.instructions,
                "tools": tools_config,
                "tool_choice": "auto" if tools_config else "none",
                "audio": {
                    "input": {
                        "format": {
                            "type": self.session_config.audio_config.input_format,
                            "rate": self.session_config.audio_config.sample_rate
                        },
                        "turn_detection": {
                            "type": self.session_config.turn_detection_type.name,
                            "create_response": self.session_config.turn_detection_type.create_response,
                            "threshold": self.session_config.turn_detection_type.threshold,
                            "prefix_padding_ms": self.session_config.turn_detection_type.prefix_padding_ms,
                            "silence_duration_ms": self.session_config.turn_detection_type.silence_duration_ms
                        },
                    },
                    "output": {
                        "format": {
                            "type": self.session_config.audio_config.output_format,
                            "rate": self.session_config.audio_config.sample_rate
                        },
                        "voice": self.session_config.audio_config.voice,
                        "speed": self.session_config.audio_config.speed
                    },
                }
            }
        }
        
        logger.info(f"Initializing session with config: {json.dumps(session_update, indent=2)}")
        await websocket.send(json.dumps(session_update))
        logger.info("Session initialization message sent")
        
        # Wait for session.created response
        await self._wait_for_session_created(websocket)
    
    async def _wait_for_session_created(self, websocket) -> None:
        """Wait for session.created response from OpenAI."""
        logger.info("⏳ Waiting for session.created response...")
        
        for i in range(self.session_config.session_creation_timeout_secs):
            try:
                response = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                data = json.loads(response)
                response_type = data.get('type', 'unknown')
                logger.info(f"📥 Session init response: {response_type}")
                
                if response_type == "session.created":
                    logger.info("✅ Session created successfully!")
                    self.session_created = True
                    if self.on_session_created:
                        await self.on_session_created()
                    return
                
                elif response_type == "error":
                    await self._handle_session_error(data)
                    return
                    
            except asyncio.TimeoutError:
                logger.info(f"⏳ Still waiting for session.created... ({i+1}/{self.session_config.session_creation_timeout_secs})")
            except json.JSONDecodeError as e:
                logger.error(f"❌ Failed to parse session response: {e}")
                continue
        
        if not self.session_created:
            error_msg = "Timeout waiting for session.created"
            logger.error(f"❌ {error_msg}")
            if self.on_session_error:
                await self.on_session_error(error_msg)
            raise RuntimeError(error_msg)
    
    async def _handle_session_error(self, data: Dict[str, Any]) -> None:
        """Handle session creation errors."""
        error_details = data.get('error', {})
        error_msg = error_details.get('message', 'Unknown error')
        error_code = error_details.get('code', 'unknown')
        logger.error(f"❌ OpenAI Session Error: {error_msg} (code: {error_code})")
        
        # Provide helpful error context
        if "403" in str(error_code) or "unauthorized" in error_msg.lower():
            logger.error("💡 This usually means:")
        elif "401" in str(error_code):
            logger.error("💡 Authentication failed - check your API key")
        elif "429" in str(error_code):
            logger.error("💡 Rate limited - try again in a moment")
        
        if self.on_session_error:
            await self.on_session_error(error_msg)
        
        raise RuntimeError(f"Session creation failed: {error_msg}")
    
    async def send_initial_message(self, websocket, message: str) -> None:
        """Send an initial message to start the conversation."""
        conversation_item = {
            "type": "conversation.item.create",
            "item": {
                "type": "message",
                "role": "user",
                "content": [{
                    "type": "input_text",
                    "text": message
                }]
            }
        }
        
        await websocket.send(json.dumps(conversation_item))
        await websocket.send(json.dumps({"type": "response.create"}))
        logger.info(f"📤 Sent initial message: '{message[:50]}...'")
    
    def is_session_ready(self) -> bool:
        """Check if the session is ready for use."""
        return self.session_created
    
    async def handle_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Handle session-related events."""
        if event_type == "session.created":
            logger.info("✅ OpenAI session successfully created")
            self.session_created = True
            if self.on_session_created:
                await self.on_session_created()
        
        elif event_type == "session.updated":
            logger.info("✅ OpenAI session configuration updated")
        
        elif event_type == "error" and not self.session_created:
            await self._handle_session_error(data)

