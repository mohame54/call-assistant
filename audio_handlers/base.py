from abc import ABC, abstractmethod
from typing import Optional
import asyncio
from typing import Optional


class AudioHandler(ABC):
    @abstractmethod
    async def send_audio(self, audio_data: bytes) -> None:
        """Send audio data to output"""
        pass
    
    @abstractmethod
    async def receive_audio(self) -> Optional[bytes]:
        """Receive audio data from input"""
        pass
    
    @abstractmethod
    async def clear_audio_buffer(self) -> None:
        """Clear any buffered audio"""
        pass


class SimpleAudioHandler(AudioHandler):
    """Simple audio handler that stores audio in memory queues"""
    
    def __init__(self):
        self.input_queue = asyncio.Queue()
        self.output_queue = asyncio.Queue()
    
    async def send_audio(self, audio_data: bytes) -> None:
        await self.output_queue.put(audio_data)
    
    async def receive_audio(self) -> Optional[bytes]:
        try:
            return await asyncio.wait_for(self.input_queue.get(), timeout=0.1)
        except asyncio.TimeoutError:
            return None
    
    async def clear_audio_buffer(self) -> None:
        while not self.output_queue.empty():
            try:
                self.output_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
    
    async def add_input_audio(self, audio_data: bytes) -> None:
        await self.input_queue.put(audio_data)
    
    async def get_output_audio(self) -> Optional[bytes]:
        try:
            return await asyncio.wait_for(self.output_queue.get(), timeout=0.1)
        except asyncio.TimeoutError:
            return None

