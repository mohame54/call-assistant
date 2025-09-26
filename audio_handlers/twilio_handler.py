import os
import asyncio
import base64
import logging
from typing import Optional, Dict, Any
from .base import AudioHandler

logger = logging.getLogger(__name__)


def encode_audio(audio_data:bytes):
    audio_payload = base64.b64encode(audio_data).decode('utf-8')
    return audio_payload


class TwilioAudioHandler(AudioHandler):    
    def __init__(self, websocket, session_id: str, connection_manager):
        self.websocket = websocket
        self.session_id = session_id
        self.connection_manager = connection_manager
        
        size = int(os.environ.get("TWILIO_AUDIO_QUEUE_SIZE", "100"))
        self.input_queue = asyncio.Queue(maxsize=size)
        self.output_queue = asyncio.Queue(maxsize=size)
        
        # Twilio-specific state
        self.stream_sid: Optional[str] = None
        self.latest_media_timestamp: int = 0
        self.mark_queue: list = []
        self.response_start_timestamp: Optional[float] = None
        self.last_assistant_item: Optional[str] = None
        
        # Audio processing state
        self.is_processing = False
        self._output_task = None
        
        # Start output processing task
        self._start_output_processing()
    
    def _start_output_processing(self):
        if self._output_task is None or self._output_task.done():
            self._output_task = asyncio.create_task(self._process_output_audio())
    
    async def _process_output_audio(self):
        try:
            while True:
                try:
                    # Get audio data from output queue
                    audio_data = await asyncio.wait_for(
                        self.output_queue.get(), 
                        timeout=1.0
                    )
                    
                    if audio_data is None:  # Shutdown signal
                        break
                    
                    # Convert PCM16 to µ-law for Twilio
                    if isinstance(audio_data, bytes):
                        audio_payload = encode_audio(audio_data)
                    else:
                        audio_payload = audio_data
                    
                    # Send audio to Twilio Media Stream
                    if self.stream_sid:
                        audio_delta = {
                            "event": "media",
                            "streamSid": self.stream_sid,
                            "media": {
                                "payload": audio_payload
                            }
                        }
                        
                        await self.websocket.send_json(audio_delta)
                        logger.debug(f"🔊 Sent audio to Twilio: {len(audio_data)} bytes -> {len(audio_payload)} base64 chars")
                        
                        # Send mark for synchronization
                        await self._send_mark()
                    
                except asyncio.TimeoutError:
                    # No audio data available, continue
                    continue
                except Exception as e:
                    logger.error(f"Error processing output audio for Twilio session {self.session_id}: {e}")
                    break
                    
        except Exception as e:
            logger.error(f"Output audio processing failed for Twilio session {self.session_id}: {e}")
    
    async def _send_mark(self):
        if self.stream_sid:
            mark_event = {
                "event": "mark",
                "streamSid": self.stream_sid,
                "mark": {"name": "responsePart"}
            }
            await self.websocket.send_json(mark_event)
            self.mark_queue.append('responsePart')
            logger.debug(f"📌 Sent mark event to Twilio")
    
    async def send_audio(self, audio_data: bytes) -> None:
        try:
            if not audio_data:
                return
                
            logger.debug(f"🔊 Received audio for Twilio session {self.session_id}: {len(audio_data)} bytes")
                
            # Add to output queue for processing
            if not self.output_queue.full():
                await self.output_queue.put(audio_data)
            else:
                logger.warning(f"Output audio queue full for Twilio session {self.session_id}, dropping audio")
                
        except Exception as e:
            logger.error(f"Error sending audio for Twilio session {self.session_id}: {e}")
    
    async def receive_audio(self) -> Optional[bytes]:
        try:
            audio_data = await asyncio.wait_for(
                self.input_queue.get(), 
                timeout=0.1
            )
            return audio_data
            
        except asyncio.TimeoutError:
            return None
        except Exception as e:
            logger.error(f"Error receiving audio for Twilio session {self.session_id}: {e}")
            return None
    
    async def add_input_audio(self, audio_data: str) -> None:
         try:
             if not audio_data:
                 logger.warning(f"Empty audio data received for Twilio session {self.session_id}")
                 return
             
             try:
                 mulaw_bytes = base64.b64decode(audio_data)
                 if not self.input_queue.full():
                     await self.input_queue.put(mulaw_bytes)
                     logger.debug(f"✅ Added {len(mulaw_bytes)} bytes of µ-law audio to queue for Twilio session {self.session_id}")
                 else:
                     logger.warning(f"Input audio queue full for Twilio session {self.session_id}, dropping {len(mulaw_bytes)} bytes")
                     
             except Exception as decode_error:
                 logger.error(f"Failed to decode base64 audio for Twilio session {self.session_id}: {decode_error}")
                 return
                 
         except Exception as e:
             logger.error(f"Error adding input audio for Twilio session {self.session_id}: {e}")
    
    async def handle_twilio_event(self, event_data: Dict[str, Any]) -> None:
        event_type = event_data.get('event')
        
        if event_type == 'start':
            self.stream_sid = event_data.get('start', {}).get('streamSid')
            self.latest_media_timestamp = 0
            self.last_assistant_item = None
            self.response_start_timestamp = None
            logger.info(f"📞 Twilio stream started: {self.stream_sid}")
            
        elif event_type == 'media':
            media_data = event_data.get('media', {})
            if media_data.get('payload'):
                self.latest_media_timestamp = int(media_data.get('timestamp', 0))
                await self.add_input_audio(media_data['payload'])
                
        elif event_type == 'mark':
            # Handle mark events for synchronization
            if self.mark_queue:
                self.mark_queue.pop(0)
            logger.debug(f"📌 Received mark event from Twilio")
    
    async def handle_speech_started(self) -> None:
        """Handle speech started event for interruption."""
        logger.info("🎤 Speech started detected in Twilio call")
        
        if self.last_assistant_item and self.response_start_timestamp:
            # Calculate elapsed time for truncation
            elapsed_time = self.latest_media_timestamp - self.response_start_timestamp
            
            logger.info(f"⏹️ Interrupting Twilio response: {elapsed_time}ms elapsed")
            
            # Clear Twilio audio buffer
            await self.websocket.send_json({
                "event": "clear",
                "streamSid": self.stream_sid
            })
            
            # Clear mark queue
            self.mark_queue.clear()
            self.last_assistant_item = None
            self.response_start_timestamp = None
    
    def set_response_timing(self, item_id: str, start_timestamp: float) -> None:
        """Set timing information for response interruption."""
        self.last_assistant_item = item_id
        self.response_start_timestamp = start_timestamp
        logger.debug(f"📊 Set response timing: item_id={item_id}, timestamp={start_timestamp}")
    
    async def clear_audio_buffer(self) -> None:
        """Clear audio buffers."""
        try:
            # Clear input queue
            while not self.input_queue.empty():
                try:
                    self.input_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            
            # Clear output queue
            while not self.output_queue.empty():
                try:
                    self.output_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                    
            logger.info(f"Cleared audio buffers for Twilio session {self.session_id}")
            
        except Exception as e:
            logger.error(f"Error clearing audio buffers for Twilio session {self.session_id}: {e}")
    
    async def get_output_audio(self) -> Optional[bytes]:
        """Get output audio data."""
        try:
            return await asyncio.wait_for(self.output_queue.get(), timeout=0.1)
        except asyncio.TimeoutError:
            return None
        except Exception as e:
            logger.error(f"Error getting output audio for Twilio session {self.session_id}: {e}")
            return None
    
    async def shutdown(self):
        try:
            # Signal output processing task to stop
            if not self.output_queue.full():
                await self.output_queue.put(None)
            
            # Cancel output processing task
            if self._output_task and not self._output_task.done():
                self._output_task.cancel()
                try:
                    await self._output_task
                except asyncio.CancelledError:
                    pass
            
            # Clear buffers
            await self.clear_audio_buffer()
            
            logger.info(f"Twilio audio handler shutdown for session {self.session_id}")
            
        except Exception as e:
            logger.error(f"Error during Twilio audio handler shutdown for session {self.session_id}: {e}")
    
    def __del__(self):
        if self._output_task and not self._output_task.done():
            self._output_task.cancel()
