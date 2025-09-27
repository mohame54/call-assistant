from abc import ABC, abstractmethod
from typing import Optional
import asyncio
import logging
import os


class AudioHandler(ABC):
    def __init__(self, session_id: str, connection_manager=None):
        self.session_id = session_id
        self.connection_manager = connection_manager
        
        # Common audio processing state
        self.is_processing = False
        self.latest_media_timestamp = 0
        self._wait_input_timeout = 0.1
        self.response_start_timestamp: Optional[float] = None
        self.last_assistant_item: Optional[str] = None
        
        # Initialize audio queues with configurable size
        queue_size = int(os.environ.get("AUDIO_QUEUE_SIZE", "100"))
        self.input_queue = asyncio.Queue(maxsize=queue_size)
        self.output_queue = asyncio.Queue(maxsize=queue_size)
        
        # Background task management
        self._output_task = None
        self._shutdown_event = asyncio.Event()
        self.logger = logging.getLogger(__name__)
    
    @abstractmethod
    async def send_audio(self, audio_data: bytes) -> None:
        """Send audio data to output"""
        pass
    
    async def receive_audio(self) -> Optional[bytes]:
        """Receive audio data from input"""
        try:
            audio_data = await asyncio.wait_for(
                self.input_queue.get(), 
                timeout=self._wait_input_timeout
            )
            return audio_data
            
        except asyncio.TimeoutError:
            return None
        except Exception as e:
            self.logger.error(f"Error receiving audio for Twilio session {self.session_id}: {e}")
            return None
    
  
    async def clear_audio_buffer(self) -> None:
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
                    
            
        except Exception as e:
            self.logger.error(f"Error clearing audio buffers for Twilio session {self.session_id}: {e}")
    
    async def add_input_audio(self, audio_data: bytes) -> None:
        """Add audio data to input queue."""
        try:
            if not audio_data:
                self.logger.warning(f"Empty audio data received for session {self.session_id}")
                return
            
            if not self.input_queue.full():
                await self.input_queue.put(audio_data)
                self.logger.debug(f"✅ Added {len(audio_data)} bytes of audio to input queue for session {self.session_id}")
            else:
                self.logger.warning(f"Input audio queue full for session {self.session_id}, dropping {len(audio_data)} bytes")
                
        except Exception as e:
            self.logger.error(f"Error adding input audio for session {self.session_id}: {e}")
    
    async def get_output_audio(self) -> Optional[bytes]:
        """Get output audio data from queue."""
        try:
            return await asyncio.wait_for(self.output_queue.get(), timeout=0.1)
        except asyncio.TimeoutError:
            return None
        except Exception as e:
            self.logger.error(f"Error getting output audio for session {self.session_id}: {e}")
            return None
    
    async def handle_speech_started(self) -> None:
        """Handle speech started event - can be overridden by specific implementations."""
        self.logger.info(f"🎤 Speech started detected for session {self.session_id}")
        
        if self.last_assistant_item and self.response_start_timestamp:
            # Clear audio buffers on speech start
            await self.clear_audio_buffer()
            self.last_assistant_item = None
            self.response_start_timestamp = None
    
    def set_response_timing(self, item_id: str, start_timestamp: float) -> None:
        """Set timing information for response tracking."""
        self.last_assistant_item = item_id
        self.response_start_timestamp = start_timestamp
        self.logger.debug(f"📊 Set response timing: item_id={item_id}, timestamp={start_timestamp}")
    
    async def shutdown(self) -> None:
        try:
            # Signal shutdown
            self._shutdown_event.set()
            
            # Cancel background tasks
            if self._output_task and not self._output_task.done():
                self._output_task.cancel()
                try:
                    await self._output_task
                except asyncio.CancelledError:
                    pass
            
            # Clear buffers
            await self.clear_audio_buffer()
            
            
        except Exception as e:
            self.logger.error(f"Error during audio handler shutdown for session {self.session_id}: {e}")
    
    def __del__(self):
        """Cleanup on destruction."""
        if self._output_task and not self._output_task.done():
            self._output_task.cancel()
