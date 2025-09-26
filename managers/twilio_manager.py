import os
import json
import asyncio
import logging
from typing import Dict, Optional
from fastapi import WebSocket, HTTPException
from openai_voice import RealTimeOpenAiVoiceAssistantV2
from audio_handlers.twilio_handler import TwilioAudioHandler
from config import (
    AudioConfig,
    SessionConfig,
    VadConfig,
    OpenAIConfig
)
from prompts import VOICE_AI_ASSISTANT

logger = logging.getLogger(__name__)


class TwilioConnectionManager:
    
    def __init__(self):
        self.active_calls: Dict[str, dict] = {}
        self.voice_assistants: Dict[str, RealTimeOpenAiVoiceAssistantV2] = {}
        
    async def connect(self, websocket: WebSocket, call_sid: str):
        await websocket.accept()
        self.active_calls[call_sid] = {
            'websocket': websocket,
            'stream_sid': None,
            'connected_at': asyncio.get_event_loop().time()
        }
        logger.info(f"📞 Twilio call {call_sid} connected")
        
    def disconnect(self, call_sid: str):
        if call_sid in self.active_calls:
            del self.active_calls[call_sid]

        if call_sid in self.voice_assistants:
            # Clean up voice assistant
            asyncio.create_task(self.voice_assistants[call_sid].disconnect())
            del self.voice_assistants[call_sid]
        logger.info(f"📞 Twilio call {call_sid} disconnected")
        
    async def send_message(self, call_sid: str, message: dict):
        """Send message to Twilio call."""
        if call_sid in self.active_calls:
            websocket = self.active_calls[call_sid]['websocket']
            try:
                await websocket.send_text(json.dumps(message))
            except Exception as e:
                logger.error(f"Error sending message to Twilio call {call_sid}: {e}")
                self.disconnect(call_sid)
    
    async def handle_media_stream(self, websocket: WebSocket, call_sid: str):
        try:
            audio_handler = TwilioAudioHandler(websocket, call_sid, self)
            
            # Create voice assistant for this call
            assistant = await create_twilio_voice_assistant(
                call_sid,
                manager=self,
                audio_handler=audio_handler
            )
            
            self.voice_assistants[call_sid] = assistant
            
    
            await assistant.connect()
            
            async for message in websocket.iter_text():
                try:
                    data = json.loads(message)
                    event_type = data.get('event')
                    
                    logger.debug(f"📥 Twilio event: {event_type}")
                    
                    if event_type == 'start':
                        stream_sid = data.get('start', {}).get('streamSid')
                        self.active_calls[call_sid]['stream_sid'] = stream_sid
                        audio_handler.stream_sid = stream_sid  # Set stream_sid in audio handler
                        logger.info(f"📞 Twilio stream started: {stream_sid}")
                        
                        initial_message = "Hello! I am an AI voice assistant powered by Twilio and OpenAI. How can I help you today?"
                        asyncio.create_task(assistant.start_conversation(initial_message))
                        logger.info(f"🚀 Started conversation for call {call_sid}")
                        
                    elif event_type == 'media':
                        media_data = data.get('media', {})
                        if media_data.get('payload'):
                            audio_handler.latest_media_timestamp = int(media_data.get('timestamp', 0))
                            await audio_handler.add_input_audio(media_data['payload'])
                            
                    elif event_type == 'mark':
                        if audio_handler.mark_queue:
                            audio_handler.mark_queue.pop(0)
                            
                except json.JSONDecodeError:
                    logger.error("Invalid JSON received from Twilio")
                    continue
                except Exception as e:
                    logger.error(f"Error handling Twilio event: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"Error in Twilio media stream handler: {e}")
            self.disconnect(call_sid)


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
            input_format="g711_ulaw",
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
