from abc import ABC, abstractmethod
from typing import Dict, Optional, Any
import asyncio
import logging
import json
from fastapi import WebSocket
from audio_handlers import AudioHandler
from openai_voice import RealTimeOpenAiVoiceAssistantV2


class BaseConnectionManager(ABC):    
    def __init__(self, initial_message: Optional[str] = None):
        self.active_connections: Dict[str, dict] = {}
        self.voice_assistants: Dict[str, RealTimeOpenAiVoiceAssistantV2] = {}
        self.initial_message = initial_message
        self._shutdown_event = asyncio.Event()
        self.logger = logging.getLogger(__name__)
    

    async def connect(self, websocket: WebSocket, session_id: str) -> None:
        await websocket.accept()
    
    @abstractmethod
    async def disconnect(self, session_id: str) -> None:
        """Disconnect and cleanup a session."""
        pass
    
    @abstractmethod
    async def handle_message_stream(self, websocket: WebSocket, session_id: str) -> None:
        """Handle incoming message stream from websocket."""
        pass
    
    # Common utility methods
    def get_connection_info(self, session_id: str) -> Optional[dict]:
        """Get connection information for a session."""
        return self.active_connections.get(session_id)
    
    def get_active_sessions(self) -> list:
        """Get list of active session IDs."""
        return list(self.active_connections.keys())
    
    def get_session_count(self) -> int:
        """Get total number of active sessions."""
        return len(self.active_connections)
    
    def get_assistant_count(self) -> int:
        """Get total number of active voice assistants."""
        return len(self.voice_assistants)
    
    async def cleanup_session(self, session_id: str) -> None:
        try:
            # Remove from active connections
            if session_id in self.active_connections:
                del self.active_connections[session_id]
            
            # Cleanup voice assistant
            if session_id in self.voice_assistants:
                assistant = self.voice_assistants[session_id]
                if hasattr(assistant, 'disconnect'):
                    await assistant.disconnect()
                del self.voice_assistants[session_id]
                
            self.logger.info(f"🧹 Cleaned up session {session_id}")
            
        except Exception as e:
            self.logger.error(f"Error during session cleanup for {session_id}: {e}")
    
    async def broadcast_message(self, message: dict, exclude_session: Optional[str] = None) -> None:
        """Broadcast a message to all active connections except the excluded one."""
        tasks = []
        for session_id in self.active_connections.keys():
            if exclude_session and session_id == exclude_session:
                continue
            tasks.append(self.send_message(session_id, message))
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    
    async def send_message(self, session_id: str, websocket: WebSocket, message: dict) -> None:
        """Common method to send JSON messages via websocket."""
        if session_id in self.active_connections:
            websocket = self.active_connections[session_id]['websocket']
            try:
                await websocket.send_text(json.dumps(message))
            except Exception as e:
                self.logger.error(f"Error sending message to Twilio call {session_id}: {e}")
                await self.disconnect(session_id)
        else:
            self.logger.error(f"Error session: {session_id} hasn't been initialized")
    
    async def shutdown_all(self) -> None:
        try:
            self.logger.info(f"🛑 Shutting down connection manager with {len(self.active_connections)} active sessions")
            
            # Signal shutdown
            self._shutdown_event.set()
            
            # Disconnect all sessions
            disconnect_tasks = []
            for session_id in list(self.active_connections.keys()):
                disconnect_tasks.append(self.disconnect(session_id))
            
            if disconnect_tasks:
                await asyncio.gather(*disconnect_tasks, return_exceptions=True)
            
            self.logger.info("✅ Connection manager shutdown complete")
            
        except Exception as e:
            self.logger.error(f"Error during connection manager shutdown: {e}")
    
    def get_health_status(self) -> Dict[str, Any]:
        """Get health status of the connection manager."""
        return {
            "status": "healthy" if not self._shutdown_event.is_set() else "shutting_down",
            "active_connections": self.get_session_count(),
            "active_assistants": self.get_assistant_count(),
            "sessions": self.get_active_sessions()
        }


class BaseAudioConnectionManager(BaseConnectionManager):
    """Base class for connection managers that handle audio streams."""
    
    def __init__(self, initial_message: Optional[str] = None):
        super().__init__(initial_message)
        self.audio_handlers: Dict[str, AudioHandler] = {}
    
    @abstractmethod
    async def create_audio_handler(self, websocket: WebSocket, session_id: str) -> Any:
        """Create an audio handler for the session - to be implemented by subclasses."""
        pass
    
    @abstractmethod
    async def create_voice_assistant(self, session_id: str, audio_handler: Any, **kwargs) -> Any:
        """Create a voice assistant for the session - to be implemented by subclasses."""
        pass
    
    async def setup_audio_session(self, websocket: WebSocket, session_id: str, assistant_kwargs) -> tuple:
        """Common setup for audio sessions. Returns (audio_handler, voice_assistant)."""
        try:
            # Create audio handler and store it
            audio_handler = await self.create_audio_handler(websocket, session_id)
            self.audio_handlers[session_id] = audio_handler
            
            # Create voice assistant for this session
            assistant = await self.create_voice_assistant(session_id, audio_handler, **assistant_kwargs)
            self.voice_assistants[session_id] = assistant
    
            return audio_handler, assistant
            
        except Exception as e:
            self.logger.error(f"Error setting up audio session {session_id}: {e}")
            await self.cleanup_session(session_id)
            raise
    
    async def cleanup_session(self, session_id: str) -> None:
        """Enhanced cleanup for audio sessions."""
        try:
            # Cleanup audio handler
            if session_id in self.audio_handlers:
                audio_handler = self.audio_handlers[session_id]
                if hasattr(audio_handler, 'shutdown'):
                    await audio_handler.shutdown()
                del self.audio_handlers[session_id]
            
            # Call parent cleanup
            await super().cleanup_session(session_id)
            
        except Exception as e:
            self.logger.error(f"Error during audio session cleanup for {session_id}: {e}")
    
    def get_health_status(self) -> Dict[str, Any]:
        """Get enhanced health status including audio handlers."""
        status = super().get_health_status()
        status["active_audio_handlers"] = len(self.audio_handlers)
        return status

