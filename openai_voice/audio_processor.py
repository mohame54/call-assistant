import base64
import time
import asyncio
import logging
from typing import List, Optional, Dict, Any
from .base import EventHandler
from config import AudioStreamingMode


class AudioProcessor(EventHandler):    
    def __init__(self, max_memory_mb: int = 50, max_chunks_per_response: int = 1000, 
                 streaming_mode: AudioStreamingMode = AudioStreamingMode.ACCUMULATE,
                 window_size_chunks: int = 10, window_timeout_ms: int = 200,
                 immediate_threshold_bytes: int = 1024):
        self.current_audio_chunks: List[bytes] = []
        self.current_response_id: Optional[str] = None
        self.current_audio_size: int = 0
        
        self.logger = logging.getLogger(__name__)
        # Audio memory management
        self.max_audio_memory_mb = max_memory_mb
        self.max_chunks_per_response = max_chunks_per_response
        
        # Audio streaming configuration
        self.streaming_mode = streaming_mode
        self.window_size_chunks = window_size_chunks
        self.window_timeout_ms = window_timeout_ms / 1000.0  # Convert to seconds
        self.immediate_threshold_bytes = immediate_threshold_bytes
        
        # Windowed mode state
        self.window_chunks: List[bytes] = []
        self.window_timer_task: Optional[asyncio.Task] = None
        self.window_start_time: Optional[float] = None
        
        # Audio timing tracking
        self.audio_start_time: Optional[float] = None
        self.total_audio_duration_ms: float = 0
        self.last_assistant_item: Optional[str] = None
        self.response_start_timestamp: Optional[float] = None
        
        # Event callbacks
        self.on_audio_ready: Optional[callable] = None
        self.on_memory_limit_reached: Optional[callable] = None
    
    def set_streaming_mode(self, mode: AudioStreamingMode, **kwargs) -> None:
        """Dynamically change streaming mode and parameters."""
        old_mode = self.streaming_mode
        self.streaming_mode = mode
        
        # Update optional parameters
        if 'window_size_chunks' in kwargs:
            self.window_size_chunks = kwargs['window_size_chunks']
        if 'window_timeout_ms' in kwargs:
            self.window_timeout_ms = kwargs['window_timeout_ms'] / 1000.0
        if 'immediate_threshold_bytes' in kwargs:
            self.immediate_threshold_bytes = kwargs['immediate_threshold_bytes']
        
        self.logger.info(f"🔄 Audio streaming mode changed: {old_mode.value} → {mode.value}")
        
        # Cancel any active window timer when switching modes
        if self.window_timer_task and not self.window_timer_task.done():
            self.window_timer_task.cancel()
    
    def reset_for_new_response(self, response_id: str) -> None:
        """Reset audio accumulation for a new response."""
        if self.current_audio_chunks:
            self.logger.debug(f"🧹 Clearing {len(self.current_audio_chunks)} chunks for new response")
        
        # Cancel window timer if active
        if self.window_timer_task and not self.window_timer_task.done():
            self.window_timer_task.cancel()
            
        # Reset state
        self.current_audio_chunks = []
        self.window_chunks = []
        self.current_response_id = response_id
        self.current_audio_size = 0
        self.response_start_timestamp = time.time() * 1000
        self.window_start_time = None
    
    async def _send_window_chunks(self) -> None:
        """Send accumulated window chunks and reset window."""
        if not self.window_chunks:
            return
            
        combined_audio = b''.join(self.window_chunks)
        chunk_count = len(self.window_chunks)
        
        self.logger.debug(f"📼 Sending windowed audio: {chunk_count} chunks, {len(combined_audio)} bytes")
        
        if self.on_audio_ready:
            await self.on_audio_ready(combined_audio)
        
        # Reset window
        self.window_chunks = []
        self.window_start_time = None
        
        # Cancel timer if active
        if self.window_timer_task and not self.window_timer_task.done():
            self.window_timer_task.cancel()
    
    async def _window_timeout_handler(self) -> None:
        """Handle window timeout - send accumulated chunks."""
        try:
            await asyncio.sleep(self.window_timeout_ms)
            await self._send_window_chunks()
        except asyncio.CancelledError:
            pass  # Timer was cancelled, which is normal
    
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
        
        # Check memory limits for accumulate mode only
        if self.streaming_mode == AudioStreamingMode.ACCUMULATE:
            if (self.current_audio_size + audio_size) > max_memory_bytes:
                self.logger.warning(f"⚠️ Audio memory limit reached ({self.max_audio_memory_mb}MB)")
                if self.on_memory_limit_reached:
                    await self.on_memory_limit_reached(self.current_audio_chunks)
                return False
            
            if len(self.current_audio_chunks) >= self.max_chunks_per_response:
                self.logger.warning(f"⚠️ Audio chunk limit reached ({self.max_chunks_per_response})")
                if self.on_memory_limit_reached:
                    await self.on_memory_limit_reached(self.current_audio_chunks)
                return False
        
        # Handle different streaming modes
        if self.streaming_mode == AudioStreamingMode.INDIVIDUAL:
            # Send each delta immediately
            self.logger.debug(f"⚡ Sending individual audio delta: {audio_size} bytes")
            if self.on_audio_ready:
                await self.on_audio_ready(audio_data)
                
        elif self.streaming_mode == AudioStreamingMode.WINDOWED:
            # Add to window and check if we should send
            self.window_chunks.append(audio_data)
            
            # Start window timer on first chunk
            if self.window_start_time is None:
                self.window_start_time = time.time()
                self.window_timer_task = asyncio.create_task(self._window_timeout_handler())
            
            # Check if window is full or chunk is large enough for immediate send
            should_send_window = (
                len(self.window_chunks) >= self.window_size_chunks or
                audio_size >= self.immediate_threshold_bytes
            )
            
            if should_send_window:
                await self._send_window_chunks()
                
        elif self.streaming_mode == AudioStreamingMode.ACCUMULATE:
            # Original accumulation behavior
            self.current_audio_chunks.append(audio_data)
            self.current_audio_size += audio_size
            self.logger.debug(f"🔊 Audio chunk accumulated ({audio_size} bytes, total: {len(self.current_audio_chunks)} chunks, {self.current_audio_size / 1024:.1f} KB)")
        
        return True
    
    async def finalize_response(self) -> Optional[bytes]:
        """
        Finalize the current response and return combined audio.
        Returns None if no audio available.
        """
        # Handle windowed mode - send any remaining chunks
        if self.streaming_mode == AudioStreamingMode.WINDOWED and self.window_chunks:
            await self._send_window_chunks()
        
        # Handle accumulate mode - send all accumulated chunks
        if self.streaming_mode == AudioStreamingMode.ACCUMULATE and self.current_audio_chunks:
            try:
                combined_audio = b''.join(self.current_audio_chunks)
                total_chunks = len(self.current_audio_chunks)
                total_size = len(combined_audio)
                
                self.logger.info(f"🔊 Finalizing accumulated audio response: {total_chunks} chunks, {total_size} bytes")
                
                if self.on_audio_ready:
                    await self.on_audio_ready(combined_audio)
                
                # Clear accumulation after sending
                self.clear_accumulation()
                return combined_audio
                
            except Exception as e:
                self.logger.error(f"❌ Error finalizing audio response: {e}")
                return None
        
        # For individual mode, nothing to finalize (already sent)
        if self.streaming_mode == AudioStreamingMode.INDIVIDUAL:
            self.logger.debug(f"✅ Individual mode response finalized (deltas already sent)")
        
        # Clear state for all modes
        self.clear_accumulation()
        return None
    
    def clear_accumulation(self) -> None:
        """Clear all accumulated audio data."""
        if self.current_audio_chunks:
            self.logger.info(f"🧹 Clearing {len(self.current_audio_chunks)} accumulated audio chunks ({self.current_audio_size / 1024 / 1024:.2f} MB)")
        
        # Cancel window timer if active
        if self.window_timer_task and not self.window_timer_task.done():
            self.window_timer_task.cancel()
        
        # Clear all state
        self.current_audio_chunks = []
        self.window_chunks = []
        self.current_response_id = None
        self.current_audio_size = 0
        self.window_start_time = None
    
    def track_input_audio(self, audio_data: bytes) -> None:
        """Track input audio timing for interruption calculations."""
        # Calculate duration from PCM16 data
        # PCM16 at 24kHz, mono = 2 bytes per sample, 24000 samples per second
        audio_duration_ms = (len(audio_data) / 2) / 24 * 1000
        self.total_audio_duration_ms += audio_duration_ms
        
        # Track session start time
        if self.audio_start_time is None:
            self.audio_start_time = time.time() * 1000
        
        self.logger.debug(f"📊 Input audio tracked: {len(audio_data)} bytes ({audio_duration_ms:.1f}ms)")
    
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
            "streaming_mode": self.streaming_mode.value,
            "current_chunks": len(self.current_audio_chunks),
            "window_chunks": len(self.window_chunks),
            "current_size_bytes": self.current_audio_size,
            "current_size_mb": self.current_audio_size / 1024 / 1024,
            "max_memory_mb": self.max_audio_memory_mb,
            "max_chunks": self.max_chunks_per_response,
            "memory_usage_percent": (self.current_audio_size / (self.max_audio_memory_mb * 1024 * 1024)) * 100 if self.streaming_mode == AudioStreamingMode.ACCUMULATE else 0,
            "chunk_usage_percent": (len(self.current_audio_chunks) / self.max_chunks_per_response) * 100 if self.streaming_mode == AudioStreamingMode.ACCUMULATE else 0,
            "response_id": self.current_response_id,
            "window_config": {
                "window_size_chunks": self.window_size_chunks,
                "window_timeout_ms": self.window_timeout_ms * 1000,
                "immediate_threshold_bytes": self.immediate_threshold_bytes
            }
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
            self.logger.debug(f"🔊 Processing output audio delta: {len(audio_data)} bytes, item_id: {item_id}")
            await self.add_audio_chunk(audio_data, item_id)
        
        elif event_type == "response_done":
            await self.finalize_response()
        
        elif event_type == "speech_started":
            self.reset_input_timing()
        
        elif event_type == "clear_audio":
            self.clear_accumulation()

