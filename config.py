from typing import Optional, Literal
from dataclasses import dataclass
from enum import Enum
from prompts import VOICE_AI_ASSISTANT


SUPPORTED_OPENAI_VOICES = Literal[
    "alloy",
    "ash", 
    "ballad",
    "coral",
    "echo",
    "sage",
    "shimmer",
    "verse",
    "cedar",
    "marin"
]

SUPPORTED_OPENAI_MODELS = Literal[
    "gpt-4o-realtime-preview",
    "gpt-realtime"
]



@dataclass
class VadConfig:
    name: Optional[str] = "server_vad"
    create_response: Optional[bool] = True
    threshold: Optional[float] = 0.5
    prefix_padding_ms: Optional[int] = 300
    silence_duration_ms: Optional[int] = 500


@dataclass
class AudioConfig:
    input_format: Optional[str] = "g711_ulaw"
    output_format: Optional[str] = "g711_ulaw"
    voice: Optional[SUPPORTED_OPENAI_VOICES] = "alloy"
    sample_rate: Optional[int] = 24000
    channels: Optional[int] = 1
    speed: Optional[float] = 1.0


@dataclass
class TwilioConfig:
    twilio_voice: str = "Google.en-US-Chirp3-HD-Aoede"
    input_format: str = "g711_ulaw"  # µ-law format
    output_format: str = "g711_ulaw"  # µ-law format
    sample_rate: int = 8000  # Twilio uses 8kHz
    channels: int = 1
    
    openai_voice: Optional[SUPPORTED_OPENAI_VOICES] = "alloy"
    greeting_message: str = (
        "Please wait while we connect your call to the A. I. voice assistant, "
        "powered by Twilio and the Open A I Realtime API"
    )
    ready_message: str = "O.K. you can start talking!"
    pause_length: int = 1  # seconds
    
    # Audio processing settings
    max_audio_memory_mb: int = 50
    max_chunks_per_response: int = 1000
    audio_queue_size: int = 100
    
    # Interruption settings
    enable_interruption: bool = True
    interruption_threshold: float = 0.5
    silence_duration_ms: int = 500
    prefix_padding_ms: int = 300


@dataclass
class OpenAIConfig:
    model: Optional[SUPPORTED_OPENAI_MODELS] = "gpt-4o-realtime-preview"
    temperature: Optional[float] = 0.8
    instructions: Optional[str] = VOICE_AI_ASSISTANT
    # Connection settings for better reliability
    ping_interval: int = 15  # Send keepalive ping every 15 seconds  
    ping_timeout: int = 10   # Wait 10 seconds for pong response
    close_timeout: int = 10  # Wait 10 seconds for close handshake


@dataclass
class SessionConfig:
    timeout: Optional[float] = 3600  # 1 hour
    cleanup_interval: Optional[float] = 300  # 5 minutes
    max_errors: Optional[int] = 5
    session_creation_timeout_secs: Optional[int] = 5
    turn_detection_type: Optional[VadConfig] = None
    audio_config: Optional[AudioConfig] = None
    openai_config: Optional[OpenAIConfig] = None
    
    def __post_init__(self):
        if self.audio_config is None:
            self.audio_config = AudioConfig()
        
        if self.turn_detection_type is None:
            self.turn_detection_type = VadConfig()

        if self.openai_config is None:
            self.openai_config = OpenAIConfig()


class VoiceAssistantState(Enum):
    """Voice assistant state enumeration"""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    LISTENING = "listening"
    SPEAKING = "speaking"
    ERROR = "error"
