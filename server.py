import os
import logging
import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from twilio.twiml.voice_response import VoiceResponse, Connect
from managers.twilio_manager import TwilioConnectionManager
from config import TwilioConfig
from utils import check_env_variables
from contextlib import asynccontextmanager


# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


check_env_variables(
    [
        "OPENAI_API_KEY",
    ]
)

async def shutdown_tasks(manager: TwilioConnectionManager):
    print("🛑 Shutting down Voice AI WebSocket Server...")
    
    # Import here to avoid circular imports
    try:        
        if hasattr(manager, 'voice_assistants'):
            for session_id, assistant in manager.voice_assistants.items():
                try:
                    await assistant.disconnect()
                    print(f"   Disconnected session: {session_id}")
                except Exception as e:
                    print(f"   Error disconnecting session {session_id}: {e}")
    except ImportError:
        print("   Warning: Could not import manager for cleanup")
    
    print("✅ Shutdown complete")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    print("🚀 Starting Voice AI WebSocket Server...")
    app.state.twilio_manager = TwilioConnectionManager()
    app.state.twilio_config = TwilioConfig()
    print("✅ Server startup complete")
    
    yield
    
    # Shutdown
    await shutdown_tasks(app.state.twilio_manager)


app = FastAPI(title="Voice AI WebSocket Server", version="1.0.0", lifespan=lifespan)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify your frontend domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/", response_class=JSONResponse)
async def index_page():
    return {"message": "Twilio Media Stream Server is running!"}


@app.api_route("/incoming-call", methods=["GET", "POST"])
async def handle_incoming_call(request: Request):
    twilio_config: TwilioConfig = request.app.state.twilio_config
    
    response = VoiceResponse()
    greet_message = os.environ.get("TWILIO_GREETING_MSSG", twilio_config.greeting_message)
    response.say(
        greet_message,
        voice=twilio_config.twilio_voice
    )
    
    response.pause(length=twilio_config.pause_length)
    
    response.say(
        twilio_config.ready_message,
        voice=twilio_config.twilio_voice
    )
    
    # Connect to Media Stream
    host = request.url.hostname
    connect = Connect()
    connect.stream(url=f'wss://{host}/media-stream')
    response.append(connect)
    
    logger.info(f"📞 Incoming call handled, connecting to media stream")
    return HTMLResponse(content=str(response), media_type="application/xml")


@app.websocket("/media-stream")
async def handle_media_stream(websocket: WebSocket):
    """Handle Twilio Media Stream WebSocket connections."""
    logger.info("📞 Twilio Media Stream WebSocket connected")
    
    try:
        call_sid = f"call_{int(asyncio.get_event_loop().time() * 1000)}"
        
        twilio_manager: TwilioConnectionManager = websocket.app.state.twilio_manager
        
        await twilio_manager.connect(websocket, call_sid)
        
        await twilio_manager.handle_media_stream(websocket, call_sid)
        
    except WebSocketDisconnect:
        logger.info("📞 Twilio Media Stream WebSocket disconnected")
    except Exception as e:
        logger.error(f"Error in Twilio media stream: {e}", exc_info=True)


@app.get("/health")
async def health_check(request: Request):
    """Health check endpoint."""
    twilio_manager: TwilioConnectionManager = request.app.state.twilio_manager
    return {
        "status": "healthy", 
        "active_calls": len(twilio_manager.active_connections),
        "active_assistants": len(twilio_manager.voice_assistants)
    }


@app.get("/twilio/calls")
async def get_active_calls(request: Request):
    """Get information about active Twilio calls."""
    twilio_manager: TwilioConnectionManager = request.app.state.twilio_manager
    calls = []
    
    for call_sid in twilio_manager.active_connections.keys():
        assistant = twilio_manager.voice_assistants.get(call_sid)
        call_info = {
            "call_sid": call_sid,
            "stream_sid": twilio_manager.active_connections[call_sid].get('stream_sid'),
            "state": assistant.state.value if assistant else "unknown",
            "connected_at": twilio_manager.active_connections[call_sid].get('connected_at')
        }
        
        # Add audio memory information if assistant is available
        if assistant and hasattr(assistant, 'get_audio_memory_info'):
            audio_info = assistant.get_audio_memory_info()
            call_info["audio_memory"] = audio_info
            call_info["streaming_mode"] = audio_info.get("streaming_mode", "unknown")
        
        calls.append(call_info)
    
    return {"calls": calls}
