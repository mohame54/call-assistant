import base64
import time
import logging
from typing import List, Optional, Dict, Any
from .base import EventHandler

logger = logging.getLogger(__name__)


class AudioProcessor(EventHandler):    
    def __init__(self, max_memory_mb: int = 50, max_chunks_per_response: int = 1000):
        # Audio accumulation for natural playback
        self.current_audio_chunks: List[bytes] = []
        self.current_response_id: Optional[str] = None
        self.current_audio_size: int = 0
        
        # Audio memory management
        self.max_audio_memory_mb = max_memory_mb
        self.max_chunks_per_response = max_chunks_per_response
        
        # Audio timing tracking
        self.audio_start_time: Optional[float] = None
        self.total_audio_duration_ms: float = 0
        self.last_assistant_item: Optional[str] = None
        self.response_start_timestamp: Optional[float] = None
        
        # Event callbacks
        self.on_audio_ready: Optional[callable] = None
        self.on_memory_limit_reached: Optional[callable] = None
    
    def reset_for_new_response(self, response_id: str) -> None:
        """Reset audio accumulation for a new response."""
        if self.current_audio_chunks:
            logger.debug(f"🧹 Clearing {len(self.current_audio_chunks)} chunks for new response")
        
        self.current_audio_chunks = []
        self.current_response_id = response_id
        self.current_audio_size = 0
        self.response_start_timestamp = time.time() * 1000
    
    async def add_audio_chunk(self, audio_data: bytes, item_id: str) -> bool:
        """
        Add an audio chunk to the current response.
        Returns True if chunk was added, False if limits exceeded.
        """
        if item_id != self.last_assistant_item:
            self.last_assistant_item = item_id
            self.reset_for_new_response(item_id)
        
        audio_size = len(audio_data)
        max_memory_bytes = self.max_audio_memory_mb * 1024 * 1024
        
        # Check memory limits
        if (self.current_audio_size + audio_size) > max_memory_bytes:
            logger.warning(f"⚠️ Audio memory limit reached ({self.max_audio_memory_mb}MB)")
            if self.on_memory_limit_reached:
                await self.on_memory_limit_reached(self.current_audio_chunks)
            return False
        
        # Check chunk limits
        if len(self.current_audio_chunks) >= self.max_chunks_per_response:
            logger.warning(f"⚠️ Audio chunk limit reached ({self.max_chunks_per_response})")
            if self.on_memory_limit_reached:
                await self.on_memory_limit_reached(self.current_audio_chunks)
            return False
        
        # Add chunk
        self.current_audio_chunks.append(audio_data)
        self.current_audio_size += audio_size
        
        logger.debug(f"🔊 Audio chunk added ({audio_size} bytes, total: {len(self.current_audio_chunks)} chunks, {self.current_audio_size / 1024:.1f} KB)")
        return True
    
    async def finalize_response(self) -> Optional[bytes]:
        """
        Finalize the current response and return combined audio.
        Returns None if no audio available.
        """
        if not self.current_audio_chunks:
            return None
        
        try:
            # Combine all audio chunks
            combined_audio = b''.join(self.current_audio_chunks)
            total_chunks = len(self.current_audio_chunks)
            total_size = len(combined_audio)
            
            logger.info(f"🔊 Finalizing audio response: {total_chunks} chunks, {total_size} bytes")
            
            # Clear accumulation
            self.clear_accumulation()
            
            if self.on_audio_ready:
                await self.on_audio_ready(combined_audio)
            
            return combined_audio
            
        except Exception as e:
            logger.error(f"❌ Error finalizing audio response: {e}")
            return None
    
    def clear_accumulation(self) -> None:
        """Clear all accumulated audio data."""
        if self.current_audio_chunks:
            logger.info(f"🧹 Clearing {len(self.current_audio_chunks)} accumulated audio chunks ({self.current_audio_size / 1024 / 1024:.2f} MB)")
        
        self.current_audio_chunks = []
        self.current_response_id = None
        self.current_audio_size = 0
    
    def track_input_audio(self, audio_data: bytes) -> None:
        """Track input audio timing for interruption calculations."""
        # Calculate duration from PCM16 data
        # PCM16 at 24kHz, mono = 2 bytes per sample, 24000 samples per second
        audio_duration_ms = (len(audio_data) / 2) / 24 * 1000
        self.total_audio_duration_ms += audio_duration_ms
        
        # Track session start time
        if self.audio_start_time is None:
            self.audio_start_time = time.time() * 1000
        
        logger.debug(f"📊 Input audio tracked: {len(audio_data)} bytes ({audio_duration_ms:.1f}ms)")
    
    def calculate_interrupt_timing(self) -> int:
        """Calculate proper timing for audio interruption."""
        if not self.response_start_timestamp:
            return 0
        
        current_time_ms = time.time() * 1000
        elapsed_time = current_time_ms - self.response_start_timestamp
        
        # Use minimum of elapsed time or total audio duration
        audio_end_ms = min(elapsed_time, self.total_audio_duration_ms)
        return int(audio_end_ms)
    
    def reset_input_timing(self) -> None:
        """Reset input audio timing when user starts speaking."""
        self.audio_start_time = time.time() * 1000
        self.total_audio_duration_ms = 0
    
    def get_memory_info(self) -> Dict[str, Any]:
        """Get current audio memory usage information."""
        return {
            "current_chunks": len(self.current_audio_chunks),
            "current_size_bytes": self.current_audio_size,
            "current_size_mb": self.current_audio_size / 1024 / 1024,
            "max_memory_mb": self.max_audio_memory_mb,
            "max_chunks": self.max_chunks_per_response,
            "memory_usage_percent": (self.current_audio_size / (self.max_audio_memory_mb * 1024 * 1024)) * 100,
            "chunk_usage_percent": (len(self.current_audio_chunks) / self.max_chunks_per_response) * 100,
            "response_id": self.current_response_id
        }
    
    async def handle_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Handle audio-related events."""
        if event_type == "audio_delta":
            audio_data = base64.b64decode(data.get('delta', ''))
            item_id = data.get('item_id', '')
            await self.add_audio_chunk(audio_data, item_id)
        
        elif event_type == "audio_output_delta":
            # Handle output audio deltas from OpenAI (for assistant speech)
            audio_data = base64.b64decode(data.get('delta', ''))
            item_id = data.get('item_id', '')
            logger.debug(f"🔊 Processing output audio delta: {len(audio_data)} bytes, item_id: {item_id}")
            await self.add_audio_chunk(audio_data, item_id)
        
        elif event_type == "response_done":
            await self.finalize_response()
        
        elif event_type == "speech_started":
            self.reset_input_timing()
        
        elif event_type == "clear_audio":
            self.clear_accumulation()

