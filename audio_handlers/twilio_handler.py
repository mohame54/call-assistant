import os
import asyncio
import base64
import logging
from typing import Optional, Dict, Any
from .base import AudioHandler


def encode_audio(audio_data:bytes):
    audio_payload = base64.b64encode(audio_data).decode('utf-8')
    return audio_payload


class TwilioAudioHandler(AudioHandler):    
    def __init__(self, websocket, session_id: str, connection_manager):
        # Set environment variable for Twilio-specific queue size before calling super
        if "TWILIO_AUDIO_QUEUE_SIZE" in os.environ:
            os.environ["AUDIO_QUEUE_SIZE"] = os.environ["TWILIO_AUDIO_QUEUE_SIZE"]
        
        super().__init__(session_id, connection_manager)
        
        self.websocket = websocket
        self.logger = logging.getLogger(__name__)
        
        # Twilio-specific state
        self.stream_sid: Optional[str] = None
        self.mark_queue: list = []
        
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
                    
                    # Encode audio data to base64 for Twilio (already in pcmu format from OpenAI)
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
                        self.logger.debug(f"🔊 Sent audio to Twilio: {len(audio_data)} bytes -> {len(audio_payload)} base64 chars")
                        
                        # Send mark for synchronization
                        await self._send_mark()
                    
                except asyncio.TimeoutError:
                    # No audio data available, continue
                    continue
                except Exception as e:
                    self.logger.error(f"Error processing output audio for Twilio session {self.session_id}: {e}")
                    break
                    
        except Exception as e:
            self.logger.error(f"Output audio processing failed for Twilio session {self.session_id}: {e}")
    
    async def _send_mark(self):
        if self.stream_sid:
            mark_event = {
                "event": "mark",
                "streamSid": self.stream_sid,
                "mark": {"name": "responsePart"}
            }
            await self.websocket.send_json(mark_event)
            self.mark_queue.append('responsePart')
            self.logger.debug(f"📌 Sent mark event to Twilio")
    
    async def send_audio(self, audio_data: bytes) -> None:
        try:
            if not audio_data:
                return
                
            self.logger.debug(f"🔊 Received audio for Twilio session {self.session_id}: {len(audio_data)} bytes")
                
            # Add to output queue for processing
            if not self.output_queue.full():
                await self.output_queue.put(audio_data)
            else:
                self.logger.warning(f"Output audio queue full for Twilio session {self.session_id}, dropping audio")
                
        except Exception as e:
            self.logger.error(f"Error sending audio for Twilio session {self.session_id}: {e}")
    
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
            self.logger.error(f"Error receiving audio for Twilio session {self.session_id}: {e}")
            return None
    
    async def add_input_audio(self, audio_data: str) -> None:
        try:
            if not audio_data:
                self.logger.warning(f"Empty audio data received for Twilio session {self.session_id}")
                return
            
            try:
                mulaw_bytes = base64.b64decode(audio_data)
                if not self.input_queue.full():
                    await self.input_queue.put(mulaw_bytes)
                    self.logger.debug(f"✅ Added {len(mulaw_bytes)} bytes of µ-law audio to queue for Twilio session {self.session_id}")
                else:
                    self.logger.warning(f"Input audio queue full for Twilio session {self.session_id}, dropping {len(mulaw_bytes)} bytes")
                    
            except Exception as decode_error:
                self.logger.error(f"Failed to decode base64 audio for Twilio session {self.session_id}: {decode_error}")
                return
                
        except Exception as e:
            self.logger.error(f"Error adding input audio for Twilio session {self.session_id}: {e}")
    
    async def handle_twilio_event(self, event_data: Dict[str, Any]) -> None:
        event_type = event_data.get('event')
        
        if event_type == 'start':
            self.stream_sid = event_data.get('start', {}).get('streamSid')
            self.latest_media_timestamp = 0
            self.last_assistant_item = None
            self.response_start_timestamp = None
            self.logger.info(f"📞 Twilio stream started: {self.stream_sid}")
            
        elif event_type == 'media':
            media_data = event_data.get('media', {})
            if media_data.get('payload'):
                self.latest_media_timestamp = int(media_data.get('timestamp', 0))
                await self.add_input_audio(media_data['payload'])
                
        elif event_type == 'mark':
            # Handle mark events for synchronization
            if self.mark_queue:
                self.mark_queue.pop(0)
            self.logger.debug(f"📌 Received mark event from Twilio")
    
    async def handle_speech_started(self) -> None:
        """Twilio-specific speech started handler with buffer clearing."""
        # Call parent method for common logic
        await super().handle_speech_started()
        
        self.logger.info("🎤 Speech started detected in Twilio call")
        
        if self.stream_sid:
            # Clear Twilio audio buffer
            await self.websocket.send_json({
                "event": "clear",
                "streamSid": self.stream_sid
            })
            
            # Clear mark queue
            self.mark_queue.clear()
            self.logger.info("⏹️ Cleared Twilio audio buffer and mark queue")
    
    
    async def clear_audio_buffer(self) -> None:
        """Twilio-specific buffer clearing including mark queue."""
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
            
            # Clear Twilio-specific mark queue
            self.mark_queue.clear()
                    
            self.logger.info(f"Cleared audio buffers for Twilio session {self.session_id}")
            
        except Exception as e:
            self.logger.error(f"Error clearing audio buffers for Twilio session {self.session_id}: {e}")
    
    async def shutdown(self):
        """Twilio-specific shutdown with output task cleanup."""
        try:
            # Signal output processing task to stop
            if not self.output_queue.full():
                await self.output_queue.put(None)
            
            # Call parent shutdown method
            await super().shutdown()
            
            self.logger.info(f"Twilio audio handler shutdown for session {self.session_id}")
            
        except Exception as e:
            self.logger.error(f"Error during Twilio audio handler shutdown for session {self.session_id}: {e}")
