# AI Voice Assistant Framework

A modular, extensible framework for building AI voice assistants that work across multiple platforms including Twilio, WebSocket browsers, Discord, and more.

## Features

- 🎯 **Multi-Platform Support**: Built-in support for Twilio with extensible architecture for WebSocket, Discord, and custom platforms
- 🎙️ **Real-time Audio Processing**: Low-latency audio streaming with configurable buffers and formats
- 🤖 **OpenAI Integration**: Built-in integration with OpenAI's Realtime API for natural conversations
- 🔧 **Modular Architecture**: Clean separation of concerns with pluggable audio handlers and connection managers
- 🛡️ **Production Ready**: Comprehensive error handling, logging, and health monitoring
- 📚 **Well Documented**: Complete guides for extending to new platforms

## Quick Start

### Twilio Voice Calls

```python
from managers.twilio_manager import TwilioConnectionManager
from fastapi import FastAPI, WebSocket

app = FastAPI()
twilio_manager = TwilioConnectionManager()

@app.websocket("/twilio")
async def twilio_endpoint(websocket: WebSocket, call_sid: str):
    await twilio_manager.connect(websocket, call_sid)
    await twilio_manager.handle_message_stream(websocket, call_sid)
```

### WebSocket Browser Audio

```python
from managers.websocket_manager import WebSocketConnectionManager

websocket_manager = WebSocketConnectionManager(
    initial_message="Hello! How can I help you today?"
)

@app.websocket("/ws/audio/{session_id}")
async def websocket_audio(websocket: WebSocket, session_id: str):
    await websocket_manager.connect(websocket, session_id)
    await websocket_manager.handle_message_stream(websocket, session_id)
```

## Architecture Overview

The framework is built around a hierarchical base class system:

```
BaseConnectionManager (Abstract)
├── BaseAudioConnectionManager (Abstract)
    ├── TwilioConnectionManager ✅
    ├── WebSocketConnectionManager 📝 (See guide)
    └── DiscordConnectionManager 📝 (See guide)

AudioHandler (Abstract)  
├── TwilioAudioHandler ✅
├── WebSocketAudioHandler 📝 (See guide)
└── DiscordAudioHandler 📝 (See guide)
```

## Creating Custom Implementations

For detailed instructions on creating your own platform integrations, see:

**📖 [Custom Implementation Guide](CUSTOM_IMPLEMENTATION_GUIDE.md)**

The guide includes:
- Complete WebSocket browser implementation example
- Discord bot integration example
- Best practices and troubleshooting
- Configuration options and tuning

## Base Classes Reference

### Connection Managers

All connection managers inherit from `BaseConnectionManager` or `BaseAudioConnectionManager`:

#### Required Methods
- `connect(websocket, session_id)` - Establish connection
- `disconnect(session_id)` - Clean disconnect  
- `send_message(session_id, message)` - Send messages to client
- `handle_message_stream(websocket, session_id)` - Handle incoming messages

#### Audio-Specific Methods (BaseAudioConnectionManager)
- `create_audio_handler(websocket, session_id)` - Create audio handler
- `create_voice_assistant(session_id, audio_handler)` - Create voice assistant

### Audio Handlers

All audio handlers inherit from `AudioHandler`:

#### Required Methods
- `send_audio(audio_data)` - Send audio to output

#### Provided Methods  
- `receive_audio()` - Get audio from input queue
- `add_input_audio(audio_data)` - Add audio to input queue
- `clear_audio_buffer()` - Clear audio buffers
- `handle_speech_started()` - Handle speech interruption
- `shutdown()` - Cleanup and shutdown

## Configuration

### Environment Variables

```bash
# OpenAI Configuration
OPENAI_API_KEY=your_openai_api_key

# Audio Queue Tuning
TWILIO_AUDIO_QUEUE_SIZE=100
WEBSOCKET_AUDIO_QUEUE_SIZE=100
DISCORD_AUDIO_QUEUE_SIZE=50

# Server Configuration
HOST=0.0.0.0
PORT=8000
```

### Audio Formats

The framework supports multiple audio formats:

- **Twilio**: µ-law (G.711) at 8kHz mono
- **WebSocket**: PCM16 at 24kHz mono  
- **Discord**: Opus at 48kHz stereo

## Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd call-assistant
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Set up environment variables:
```bash
export OPENAI_API_KEY=your_api_key
```

4. Run the server:
```bash
python server.py
```

## Examples

### Minimal WebSocket Client

```html
<script>
const ws = new WebSocket('ws://localhost:8000/ws/audio/session123');

ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (data.type === 'audio') {
        // Play received audio
        playAudio(data.data);
    }
};

// Send audio to server
function sendAudio(audioBlob) {
    const base64 = btoa(audioBlob);
    ws.send(JSON.stringify({
        type: 'audio',
        data: base64
    }));
}
</script>
```

### Discord Bot Integration

```python
import discord
from managers.discord_manager import DiscordConnectionManager

discord_manager = DiscordConnectionManager()

@bot.command()
async def join_voice(ctx):
    if ctx.author.voice:
        channel = ctx.author.voice.channel
        voice_client = await channel.connect()
        session_id = f"{ctx.guild.id}:{channel.id}:{ctx.author.id}"
        await discord_manager.connect(voice_client, session_id)
```

## Health Monitoring

```python
# Check system health
status = manager.get_health_status()
print(f"Status: {status['status']}")
print(f"Active connections: {status['active_connections']}")
print(f"Active audio handlers: {status['active_audio_handlers']}")
```

## Contributing

1. Follow the established patterns in base classes
2. Implement all required abstract methods
3. Add comprehensive error handling and logging
4. Include platform-specific configuration
5. Update documentation with examples

## License

[Your License Here]

## Support

For questions about extending the framework or implementing new platforms, see the [Custom Implementation Guide](CUSTOM_IMPLEMENTATION_GUIDE.md) or open an issue.
