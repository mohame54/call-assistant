import os
import json
import asyncio
import logging
from typing import  Optional
from fastapi import WebSocket, HTTPException
from openai_voice import RealTimeOpenAiVoiceAssistantV2
from audio_handlers.twilio_handler import TwilioAudioHandler
from .base import BaseAudioConnectionManager
from config import (
    AudioConfig,
    SessionConfig,
    VadConfig,
    OpenAIConfig
)
from prompts import VOICE_AI_ASSISTANT


class TwilioConnectionManager(BaseAudioConnectionManager):
    def __init__(self, initial_message: Optional[str] = None):
        super().__init__(initial_message)
        self.logger = logging.getLogger(__name__)
        
    async def connect(self, websocket: WebSocket, call_sid: str):
        await super().connect(websocket, call_sid)
        self.active_connections[call_sid] = {
            'websocket': websocket,
            'stream_sid': None,
            'connected_at': asyncio.get_event_loop().time()
        }
        self.logger.info(f"📞 Twilio call {call_sid} connected")
        
    async def disconnect(self, call_sid: str):
        """Disconnect and cleanup a Twilio call session."""
        await self.cleanup_session(call_sid)
        self.logger.info(f"📞 Twilio call {call_sid} disconnected")
        
    async def send_message(self, session_id: str, message: dict) -> None:
        """Send message to Twilio call."""
        if session_id in self.active_connections:
            websocket = self.active_connections[session_id]['websocket']
            try:
                await websocket.send_text(json.dumps(message))
            except Exception as e:
                self.logger.error(f"Error sending message to Twilio call {session_id}: {e}")
                await self.disconnect(session_id)
    
    async def create_audio_handler(self, websocket: WebSocket, session_id: str) -> TwilioAudioHandler:
        """Create a Twilio audio handler for the session."""
        return TwilioAudioHandler(websocket, session_id, self)
    
    async def create_voice_assistant(self, session_id: str, audio_handler: TwilioAudioHandler, **kwargs) -> RealTimeOpenAiVoiceAssistantV2:
        return await create_twilio_voice_assistant(
            session_id,
            manager=self,
            audio_handler=audio_handler,
            **kwargs
        )
    
    async def handle_message_stream(self, websocket: WebSocket, call_sid: str):
        """Handle Twilio media stream - alias for compatibility."""
        try:
            # Create audio handler and store it
            audio_handler, assistant = await self.setup_audio_session(websocket, call_sid)
        
            await assistant.connect()
            
            async for message in websocket.iter_text():
                try:
                    data = json.loads(message)
                    event_type = data.get('event')
                    
                    self.logger.debug(f"📥 Twilio event: {event_type}")
                    
                    # Use audio handler's centralized event processing
                    await audio_handler.handle_twilio_event(data)
                    
                    # Handle manager-specific logic for 'start' event
                    if event_type == 'start':
                        stream_sid = data.get('start', {}).get('streamSid')
                        self.active_connections[call_sid]['stream_sid'] = stream_sid
                        initial_message = self.initial_message
                        if not initial_message:
                            initial_message = "Hello! I am an AI voice assistant powered by Twilio and OpenAI. How can I help you today?"
                        asyncio.create_task(assistant.start_conversation(initial_message))
                        self.logger.info(f"🚀 Started conversation for call {call_sid}")
                            
                except json.JSONDecodeError:
                    self.logger.error("Invalid JSON received from Twilio")
                    continue
                except Exception as e:
                    self.logger.error(f"Error handling Twilio event: {e}")
                    continue
                    
        except Exception as e:
            self.logger.error(f"Error in Twilio media stream handler: {e}")
            await self.disconnect(call_sid)
    
    # Compatibility method for existing code
    async def handle_media_stream(self, websocket: WebSocket, call_sid: str):
        """Compatibility wrapper for handle_message_stream."""
        await self.handle_message_stream(websocket, call_sid)


async def create_twilio_voice_assistant(
    call_sid: str,
    manager: TwilioConnectionManager,
    audio_handler: TwilioAudioHandler,
    audio_config: Optional[AudioConfig] = None,
    openai_config: Optional[OpenAIConfig] = None,
    vad_config: Optional[VadConfig] = None
) -> RealTimeOpenAiVoiceAssistantV2:
    
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="OpenAI API key not configured")
    
    # Twilio-specific configuration
    if audio_config is None:
        audio_config = AudioConfig(
            input_format="g711_ulaw", # Twilio uses 8kHz
            output_format="g711_ulaw",
            voice="alloy",
            sample_rate=8000,  # Twilio uses 8kHz
            channels=1
        )
    
    if openai_config is None:
        openai_config = OpenAIConfig(
            model="gpt-4o-realtime-preview",
            temperature=0.8,
            instructions=VOICE_AI_ASSISTANT
        )

    if vad_config is None:
        vad_config = VadConfig(
            name="server_vad",
            create_response=True,
            threshold=0.5,
            prefix_padding_ms=300,
            silence_duration_ms=500
        )

    session_config = SessionConfig(
        turn_detection_type=vad_config,
        audio_config=audio_config,
        openai_config=openai_config
    )
    
    assistant = RealTimeOpenAiVoiceAssistantV2(
        api_key=api_key,
        session_id=call_sid,
        session_config=session_config,
        audio_handler=audio_handler,
        non_blocking_tools_calling=True
    )
    
    # Setup Twilio-specific callbacks
    async def on_state_change(old_state, new_state):
        await manager.send_message(call_sid, {
            "type": "state_change",
            "old_state": old_state.value,
            "new_state": new_state.value
        })
    
    async def on_speech_started():
        await manager.send_message(call_sid, {
            "type": "speech_started"
        })
        # Handle Twilio-specific speech interruption
        await audio_handler.handle_speech_started()
    
    async def on_speech_ended():
        await manager.send_message(call_sid, {
            "type": "speech_ended"
        })
    
    async def on_response_started():
        await manager.send_message(call_sid, {
            "type": "response_started"
        })
    
    async def on_response_ended():
        await manager.send_message(call_sid, {
            "type": "response_ended"
        })
    
    async def on_error(error_message):
        await manager.send_message(call_sid, {
            "type": "error",
            "message": error_message
        })
    
    # Assign callbacks
    assistant.on_state_change = on_state_change
    assistant.on_speech_started = on_speech_started
    assistant.on_speech_ended = on_speech_ended
    assistant.on_response_started = on_response_started
    assistant.on_response_ended = on_response_ended
    assistant.on_error = on_error
    
    # Setup Twilio-specific audio processing
    assistant.audio_processor.on_audio_ready = audio_handler.send_audio
    
    return assistant
