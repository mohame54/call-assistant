# Custom Implementation Guide

This guide explains how to extend the Call Assistant framework to support new platforms and audio protocols. The framework is built with a modular architecture that makes it easy to add support for Discord, WebSocket browsers, or any other audio communication platform.

## Architecture Overview

The framework uses a hierarchical base class system:

```
BaseConnectionManager (Abstract)
├── BaseAudioConnectionManager (Abstract)
    ├── TwilioConnectionManager (Concrete)
    ├── WebSocketConnectionManager (Your Implementation)
    └── DiscordConnectionManager (Your Implementation)

AudioHandler (Abstract)
├── TwilioAudioHandler (Concrete)
├── WebSocketAudioHandler (Your Implementation)
└── DiscordAudioHandler (Your Implementation)

BaseVoiceAssistant (Abstract)
├── StateManager (Abstract)
└── RealTimeOpenAiVoiceAssistantV2 (Concrete)
```

## Core Components

### 1. Base Classes

#### `BaseConnectionManager`
Manages WebSocket connections and session lifecycle.

**Abstract Methods:**
- `connect(websocket, session_id)` - Establish connection
- `disconnect(session_id)` - Clean disconnect
- `send_message(session_id, message)` - Send messages to client
- `handle_message_stream(websocket, session_id)` - Handle incoming messages

**Concrete Methods:**
- `cleanup_session(session_id)` - Common cleanup logic
- `broadcast_message(message, exclude_session)` - Broadcast to all sessions
- `get_health_status()` - Health monitoring
- `shutdown_all()` - Graceful shutdown

#### `BaseAudioConnectionManager`
Extends `BaseConnectionManager` with audio-specific functionality.

**Abstract Methods:**
- `create_audio_handler(websocket, session_id)` - Create audio handler
- `create_voice_assistant(session_id, audio_handler)` - Create voice assistant

**Concrete Methods:**
- `setup_audio_session(websocket, session_id)` - Common audio session setup
- Enhanced `cleanup_session()` with audio handler cleanup

#### `AudioHandler`
Manages audio input/output queues and processing.

**Abstract Methods:**
- `send_audio(audio_data)` - Send audio to output

**Concrete Methods:**
- `receive_audio()` - Get audio from input queue
- `add_input_audio(audio_data)` - Add audio to input queue
- `clear_audio_buffer()` - Clear audio buffers
- `handle_speech_started()` - Handle speech interruption
- `shutdown()` - Cleanup and shutdown

## Creating a WebSocket Implementation

Here's a complete example for browser-based WebSocket audio:

### Step 1: Create WebSocket Audio Handler

```python
# audio_handlers/websocket_handler.py
import os
import asyncio
import base64
import logging
import json
from typing import Optional, Dict, Any
from .base import AudioHandler

logger = logging.getLogger(__name__)

class WebSocketAudioHandler(AudioHandler):
    """WebSocket audio handler for browser-based applications."""
    
    def __init__(self, websocket, session_id: str, connection_manager):
        super().__init__(session_id, connection_manager)
        
        self.websocket = websocket
        self.audio_format = "pcm16"
        self.sample_rate = 24000
        self.channels = 1
        
        # Start output processing
        self._start_output_processing()
    
    def _start_output_processing(self):
        """Start background task for processing output audio."""
        if self._output_task is None or self._output_task.done():
            self._output_task = asyncio.create_task(self._process_output_audio())
    
    async def _process_output_audio(self):
        """Process audio from output queue and send to WebSocket."""
        try:
            while True:
                try:
                    audio_data = await asyncio.wait_for(
                        self.output_queue.get(), timeout=1.0
                    )
                    
                    if audio_data is None:  # Shutdown signal
                        break
                    
                    # Encode for WebSocket transmission
                    audio_payload = base64.b64encode(audio_data).decode('utf-8')
                    
                    # Send to browser
                    message = {
                        "type": "audio",
                        "data": audio_payload,
                        "format": self.audio_format,
                        "sample_rate": self.sample_rate
                    }
                    
                    await self.websocket.send_text(json.dumps(message))
                    
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    logger.error(f"Error processing audio: {e}")
                    break
                    
        except Exception as e:
            logger.error(f"Audio processing failed: {e}")
    
    async def send_audio(self, audio_data: bytes) -> None:
        """Required: Send audio data to output queue."""
        try:
            if not audio_data:
                return
                
            if not self.output_queue.full():
                await self.output_queue.put(audio_data)
            else:
                logger.warning("Output queue full, dropping audio")
                
        except Exception as e:
            logger.error(f"Error sending audio: {e}")
    
    async def add_input_audio(self, audio_data: str) -> None:
        """Add base64 encoded audio to input queue."""
        try:
            if not audio_data:
                return
            
            # Decode base64 audio
            audio_bytes = base64.b64decode(audio_data)
            
            if not self.input_queue.full():
                await self.input_queue.put(audio_bytes)
            else:
                logger.warning("Input queue full, dropping audio")
                
        except Exception as e:
            logger.error(f"Error adding input audio: {e}")
    
    async def handle_websocket_message(self, message: Dict[str, Any]) -> None:
        """Handle incoming WebSocket messages."""
        message_type = message.get('type')
        
        if message_type == 'audio':
            audio_data = message.get('data')
            if audio_data:
                await self.add_input_audio(audio_data)
                
        elif message_type == 'config':
            # Update audio configuration
            self.audio_format = message.get('format', self.audio_format)
            self.sample_rate = message.get('sample_rate', self.sample_rate)
    
    async def shutdown(self):
        """Shutdown with WebSocket-specific cleanup."""
        try:
            # Signal shutdown
            if not self.output_queue.full():
                await self.output_queue.put(None)
            
            # Notify client
            await self.websocket.send_text(json.dumps({
                "type": "disconnected"
            }))
            
            # Call parent shutdown
            await super().shutdown()
            
        except Exception as e:
            logger.error(f"Shutdown error: {e}")
```

### Step 2: Create WebSocket Connection Manager

```python
# managers/websocket_manager.py
import os
import json
import asyncio
import logging
from typing import Optional
from fastapi import WebSocket, HTTPException
from openai_voice import RealTimeOpenAiVoiceAssistantV2
from audio_handlers.websocket_handler import WebSocketAudioHandler
from .base import BaseAudioConnectionManager
from config import AudioConfig, SessionConfig, VadConfig, OpenAIConfig
from prompts import VOICE_AI_ASSISTANT

class WebSocketConnectionManager(BaseAudioConnectionManager):
    """Connection manager for browser WebSocket audio."""
    
    def __init__(self, initial_message: Optional[str] = None):
        super().__init__(initial_message)
        self.logger = logging.getLogger(__name__)
        
    async def connect(self, websocket: WebSocket, session_id: str):
        """Required: Establish WebSocket connection."""
        await websocket.accept()
        self.active_connections[session_id] = {
            'websocket': websocket,
            'connected_at': asyncio.get_event_loop().time(),
            'client_type': 'browser'
        }
        self.logger.info(f"WebSocket session {session_id} connected")
        
    async def disconnect(self, session_id: str):
        """Required: Disconnect and cleanup."""
        await self.cleanup_session(session_id)
        self.logger.info(f"WebSocket session {session_id} disconnected")
        
    async def send_message(self, session_id: str, message: dict) -> None:
        """Required: Send message to client."""
        if session_id in self.active_connections:
            websocket = self.active_connections[session_id]['websocket']
            try:
                await websocket.send_text(json.dumps(message))
            except Exception as e:
                self.logger.error(f"Error sending message: {e}")
                await self.disconnect(session_id)
    
    async def create_audio_handler(self, websocket: WebSocket, session_id: str) -> WebSocketAudioHandler:
        """Required: Create audio handler."""
        return WebSocketAudioHandler(websocket, session_id, self)
    
    async def create_voice_assistant(self, session_id: str, audio_handler: WebSocketAudioHandler):
        """Required: Create voice assistant."""
        return await create_websocket_voice_assistant(
            session_id, manager=self, audio_handler=audio_handler
        )
    
    async def handle_message_stream(self, websocket: WebSocket, session_id: str):
        """Required: Handle incoming message stream."""
        try:
            # Setup audio session using base class method
            audio_handler, assistant = await self.setup_audio_session(websocket, session_id)
            
            # Connect voice assistant
            await assistant.connect()
            
            # Send connection confirmation
            await self.send_message(session_id, {
                "type": "connected",
                "session_id": session_id
            })
            
            # Start conversation if initial message provided
            if self.initial_message:
                asyncio.create_task(assistant.start_conversation(self.initial_message))
            
            # Process incoming messages
            async for message in websocket.iter_text():
                try:
                    data = json.loads(message)
                    message_type = data.get('type')
                    
                    if message_type == 'audio':
                        await audio_handler.handle_websocket_message(data)
                        
                    elif message_type == 'text':
                        text_message = data.get('message', '')
                        if text_message:
                            await assistant.send_text_message(text_message)
                            
                    elif message_type == 'interrupt':
                        await assistant.interrupt_response()
                        
                    else:
                        await audio_handler.handle_websocket_message(data)
                        
                except json.JSONDecodeError:
                    self.logger.error("Invalid JSON received")
                    continue
                except Exception as e:
                    self.logger.error(f"Error handling message: {e}")
                    continue
                    
        except Exception as e:
            self.logger.error(f"Error in message handler: {e}")
            await self.disconnect(session_id)


async def create_websocket_voice_assistant(
    session_id: str,
    manager: WebSocketConnectionManager,
    audio_handler: WebSocketAudioHandler,
    audio_config: Optional[AudioConfig] = None,
    openai_config: Optional[OpenAIConfig] = None,
    vad_config: Optional[VadConfig] = None
):
    """Create voice assistant for WebSocket connections."""
    
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="OpenAI API key not configured")
    
    # Configure for browser audio
    if audio_config is None:
        audio_config = AudioConfig(
            input_format="pcm16",
            output_format="pcm16",
            voice="alloy",
            sample_rate=24000,
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
        session_id=session_id,
        session_config=session_config,
        audio_handler=audio_handler,
        non_blocking_tools_calling=True
    )
    
    # Setup callbacks
    async def on_state_change(old_state, new_state):
        await manager.send_message(session_id, {
            "type": "state_change",
            "old_state": old_state.value,
            "new_state": new_state.value
        })
    
    async def on_speech_started():
        await manager.send_message(session_id, {
            "type": "speech_started"
        })
        await audio_handler.handle_speech_started()
    
    async def on_speech_ended():
        await manager.send_message(session_id, {
            "type": "speech_ended"
        })
    
    async def on_response_started():
        await manager.send_message(session_id, {
            "type": "response_started"
        })
    
    async def on_response_ended():
        await manager.send_message(session_id, {
            "type": "response_ended"
        })
    
    async def on_error(error_message):
        await manager.send_message(session_id, {
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
    
    # Setup audio processing
    assistant.audio_processor.on_audio_ready = audio_handler.send_audio
    
    return assistant
```

### Step 3: Integrate with FastAPI Server

```python
# Add to your server.py
from managers.websocket_manager import WebSocketConnectionManager

# Create WebSocket manager
websocket_manager = WebSocketConnectionManager(
    initial_message="Hello! I'm an AI assistant. How can I help you?"
)

@app.websocket("/ws/audio/{session_id}")
async def websocket_audio_endpoint(websocket: WebSocket, session_id: str):
    """WebSocket endpoint for browser audio connections."""
    try:
        await websocket_manager.connect(websocket, session_id)
        await websocket_manager.handle_message_stream(websocket, session_id)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        await websocket_manager.disconnect(session_id)
```

### Step 4: Client-Side Integration

```html
<!DOCTYPE html>
<html>
<head>
    <title>Voice Assistant</title>
</head>
<body>
    <button id="startBtn">Start</button>
    <button id="stopBtn">Stop</button>
    <div id="status">Disconnected</div>
    
    <script>
        class VoiceAssistant {
            constructor() {
                this.ws = null;
                this.mediaRecorder = null;
                this.audioContext = null;
                this.isRecording = false;
            }
            
            async connect() {
                const sessionId = Math.random().toString(36).substring(7);
                this.ws = new WebSocket(`ws://localhost:8000/ws/audio/${sessionId}`);
                
                this.ws.onopen = () => {
                    document.getElementById('status').textContent = 'Connected';
                };
                
                this.ws.onmessage = (event) => {
                    const data = JSON.parse(event.data);
                    this.handleMessage(data);
                };
                
                this.ws.onclose = () => {
                    document.getElementById('status').textContent = 'Disconnected';
                };
            }
            
            handleMessage(data) {
                switch(data.type) {
                    case 'audio':
                        this.playAudio(data.data);
                        break;
                    case 'state_change':
                        console.log('State:', data.new_state);
                        break;
                    case 'speech_started':
                        console.log('Speech detected');
                        break;
                }
            }
            
            async startRecording() {
                const stream = await navigator.mediaDevices.getUserMedia({ 
                    audio: { sampleRate: 24000 } 
                });
                
                this.mediaRecorder = new MediaRecorder(stream);
                this.mediaRecorder.ondataavailable = (event) => {
                    if (event.data.size > 0) {
                        this.sendAudio(event.data);
                    }
                };
                
                this.mediaRecorder.start(100); // 100ms chunks
                this.isRecording = true;
            }
            
            stopRecording() {
                if (this.mediaRecorder) {
                    this.mediaRecorder.stop();
                    this.isRecording = false;
                }
            }
            
            async sendAudio(audioBlob) {
                const arrayBuffer = await audioBlob.arrayBuffer();
                const base64 = btoa(String.fromCharCode(...new Uint8Array(arrayBuffer)));
                
                this.ws.send(JSON.stringify({
                    type: 'audio',
                    data: base64
                }));
            }
            
            playAudio(base64Data) {
                const audioData = atob(base64Data);
                const arrayBuffer = new ArrayBuffer(audioData.length);
                const view = new Uint8Array(arrayBuffer);
                
                for (let i = 0; i < audioData.length; i++) {
                    view[i] = audioData.charCodeAt(i);
                }
                
                // Play audio using Web Audio API
                this.audioContext.decodeAudioData(arrayBuffer, (buffer) => {
                    const source = this.audioContext.createBufferSource();
                    source.buffer = buffer;
                    source.connect(this.audioContext.destination);
                    source.start();
                });
            }
        }
        
        const assistant = new VoiceAssistant();
        
        document.getElementById('startBtn').onclick = async () => {
            if (!assistant.ws) {
                await assistant.connect();
            }
            await assistant.startRecording();
        };
        
        document.getElementById('stopBtn').onclick = () => {
            assistant.stopRecording();
        };
    </script>
</body>
</html>
```

## Discord Implementation Example

Here's a minimal Discord bot implementation:

### Discord Audio Handler

```python
# audio_handlers/discord_handler.py
import asyncio
import logging
from typing import Optional, Dict, Any
from .base import AudioHandler
import discord

logger = logging.getLogger(__name__)

class DiscordAudioHandler(AudioHandler):
    """Discord bot voice audio handler."""
    
    def __init__(self, voice_client, session_id: str, connection_manager):
        super().__init__(session_id, connection_manager)
        
        self.voice_client = voice_client
        self.guild_id = None
        self.channel_id = None
        self.user_id = None
        
        # Discord uses Opus codec at 48kHz
        self.audio_format = "opus"
        self.sample_rate = 48000
        self.channels = 2
        
        self._start_output_processing()
    
    def _start_output_processing(self):
        if self._output_task is None or self._output_task.done():
            self._output_task = asyncio.create_task(self._process_output_audio())
    
    async def _process_output_audio(self):
        """Process output audio for Discord voice."""
        try:
            while True:
                try:
                    audio_data = await asyncio.wait_for(
                        self.output_queue.get(), timeout=1.0
                    )
                    
                    if audio_data is None:
                        break
                    
                    # Send to Discord voice channel
                    if self.voice_client and self.voice_client.is_connected():
                        await self._send_discord_audio(audio_data)
                    
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    logger.error(f"Discord audio processing error: {e}")
                    break
                    
        except Exception as e:
            logger.error(f"Discord audio processing failed: {e}")
    
    async def _send_discord_audio(self, audio_data: bytes):
        """Send audio to Discord voice channel."""
        # Implementation depends on your Discord library
        # This is a placeholder for discord.py integration
        pass
    
    async def send_audio(self, audio_data: bytes) -> None:
        """Required: Send audio data to output queue."""
        try:
            if not audio_data:
                return
                
            if not self.output_queue.full():
                await self.output_queue.put(audio_data)
            else:
                logger.warning("Discord output queue full")
                
        except Exception as e:
            logger.error(f"Error sending Discord audio: {e}")
    
    def set_discord_context(self, guild_id: str, channel_id: str, user_id: str):
        """Set Discord context information."""
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.user_id = user_id
    
    async def join_voice_channel(self, channel):
        """Join Discord voice channel."""
        try:
            self.voice_client = await channel.connect()
            self.channel_id = str(channel.id)
            logger.info(f"Joined Discord voice channel {channel.name}")
        except Exception as e:
            logger.error(f"Error joining voice channel: {e}")
    
    async def leave_voice_channel(self):
        """Leave Discord voice channel."""
        try:
            if self.voice_client and self.voice_client.is_connected():
                await self.voice_client.disconnect()
                logger.info("Left Discord voice channel")
        except Exception as e:
            logger.error(f"Error leaving voice channel: {e}")
```

### Discord Connection Manager

```python
# managers/discord_manager.py
import asyncio
import logging
from typing import Optional
from .base import BaseAudioConnectionManager
from audio_handlers.discord_handler import DiscordAudioHandler
from openai_voice import RealTimeOpenAiVoiceAssistantV2

class DiscordConnectionManager(BaseAudioConnectionManager):
    """Connection manager for Discord bot interactions."""
    
    def __init__(self, discord_client, initial_message: Optional[str] = None):
        super().__init__(initial_message)
        self.discord_client = discord_client
        self.logger = logging.getLogger(__name__)
    
    async def connect(self, voice_client, session_id: str):
        """Connect Discord voice client."""
        self.active_connections[session_id] = {
            'voice_client': voice_client,
            'connected_at': asyncio.get_event_loop().time(),
            'client_type': 'discord'
        }
        self.logger.info(f"Discord session {session_id} connected")
    
    async def disconnect(self, session_id: str):
        """Disconnect Discord session."""
        await self.cleanup_session(session_id)
        self.logger.info(f"Discord session {session_id} disconnected")
    
    async def send_message(self, session_id: str, message: dict) -> None:
        """Send message to Discord channel."""
        # Implementation for sending Discord messages
        pass
    
    async def create_audio_handler(self, voice_client, session_id: str) -> DiscordAudioHandler:
        """Create Discord audio handler."""
        return DiscordAudioHandler(voice_client, session_id, self)
    
    async def create_voice_assistant(self, session_id: str, audio_handler: DiscordAudioHandler):
        """Create voice assistant for Discord."""
        # Similar to WebSocket implementation but with Discord-specific config
        pass
    
    async def handle_message_stream(self, voice_client, session_id: str):
        """Handle Discord voice stream."""
        try:
            audio_handler, assistant = await self.setup_audio_session(voice_client, session_id)
            await assistant.connect()
            
            # Discord-specific voice handling logic
            
        except Exception as e:
            self.logger.error(f"Discord stream error: {e}")
            await self.disconnect(session_id)
```

## Best Practices

### 1. Error Handling
- Always wrap audio processing in try-catch blocks
- Use proper logging with context (session_id, platform)
- Implement graceful degradation when audio fails

### 2. Resource Management
- Use `asyncio.Queue` with maxsize to prevent memory leaks
- Implement proper shutdown sequences
- Clean up background tasks in `shutdown()` methods

### 3. Audio Format Handling
- Support multiple audio formats (PCM16, Opus, µ-law)
- Implement format conversion when needed
- Use appropriate sample rates for each platform

### 4. Configuration
- Create platform-specific config classes
- Use environment variables for tuning
- Document all configuration options

### 5. Testing
- Unit test each component separately
- Mock WebSocket/Discord connections for testing
- Test audio pipeline with synthetic data

## Configuration Options

### Audio Queue Sizes
```python
# Environment variables for tuning
WEBSOCKET_AUDIO_QUEUE_SIZE=100
DISCORD_AUDIO_QUEUE_SIZE=50
TWILIO_AUDIO_QUEUE_SIZE=100
```

### Platform-Specific Settings
```python
# WebSocket Configuration
websocket_config = AudioConfig(
    input_format="pcm16",
    output_format="pcm16",
    sample_rate=24000,
    channels=1
)

# Discord Configuration  
discord_config = AudioConfig(
    input_format="opus",
    output_format="opus", 
    sample_rate=48000,
    channels=2
)
```

## Troubleshooting

### Common Issues

1. **Audio Queue Full**: Increase queue size or improve processing speed
2. **Connection Drops**: Implement reconnection logic
3. **Audio Latency**: Reduce buffer sizes and processing delays
4. **Memory Leaks**: Ensure proper cleanup in shutdown methods

### Debug Logging
```python
import logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)
```

### Health Monitoring
```python
# Check manager health
status = manager.get_health_status()
print(f"Active connections: {status['active_connections']}")
print(f"Active audio handlers: {status['active_audio_handlers']}")
```

This guide provides a complete foundation for extending the framework to any audio platform. The key is following the established patterns and implementing the required abstract methods while leveraging the concrete functionality provided by the base classes.

