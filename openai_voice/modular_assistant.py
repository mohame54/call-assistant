import json
import base64
import asyncio
import logging
from typing import Optional
from audio_handlers import AudioHandler
from config import SessionConfig, VoiceAssistantState
from .base import BaseVoiceAssistant
from .connection_manager import OpenAIConnectionManager
from .audio_processor import AudioProcessor
from .function_call_processor import FunctionCallProcessor
from .session_manager import SessionManager
from .event_dispatcher import EventDispatcher, OpenAIEventRouter


class RealTimeOpenAiVoiceAssistantV2(BaseVoiceAssistant):    
    def __init__(
        self, 
        api_key: str,
        session_id: str,
        session_config: Optional[SessionConfig] = None,
        audio_handler: Optional[AudioHandler] = None,
        non_blocking_tools_calling: Optional[bool] = True
    ):
        super().__init__(api_key, session_id)
        
        self.session_config = session_config if session_config else SessionConfig()
        self.audio_handler = audio_handler
        
        # Initialize components
        self.logger = logging.getLogger(__name__)

        self._initialize_components(non_blocking_tools_calling)
        
        # Setup event routing
        self._setup_event_routing()
        
        # Setup component callbacks
        self._setup_component_callbacks()
    
    def _initialize_components(self, non_blocking_tools_calling: bool) -> None:
        """Initialize all assistant components."""
        # Core components
        self.connection_manager = OpenAIConnectionManager(
            self.api_key, 
            self.session_config.openai_config.model
        )
        
        # Get audio streaming configuration
        audio_config = self.session_config.audio_config
        self.audio_processor = AudioProcessor(
            max_memory_mb=50,
            max_chunks_per_response=1000,
            streaming_mode=audio_config.streaming_mode,
            window_size_chunks=audio_config.window_size_chunks,
            window_timeout_ms=audio_config.window_timeout_ms,
            immediate_threshold_bytes=audio_config.immediate_threshold_bytes
        )
        
        self.function_call_processor = FunctionCallProcessor(
            non_blocking=non_blocking_tools_calling
        )
        
        self.session_manager = SessionManager(self.session_config)
        
        # Event system
        self.event_dispatcher = EventDispatcher()
        self.openai_event_router = OpenAIEventRouter(self.event_dispatcher)
    
    def _setup_event_routing(self) -> None:
        """Setup event routing between components."""
        # Register audio processor for audio events
        self.event_dispatcher.register_handler('audio_output_delta', self.audio_processor)
        self.event_dispatcher.register_handler('response_done', self.audio_processor)
        self.event_dispatcher.register_handler('speech_started', self.audio_processor)
        
        # Register function call processor for function events
        self.event_dispatcher.register_handler('function_call_arguments.delta', self.function_call_processor)
        self.event_dispatcher.register_handler('function_call_arguments.done', self.function_call_processor)
        
        # Register session manager for session events
        self.event_dispatcher.register_handler('session.created', self.session_manager)
        self.event_dispatcher.register_handler('session.updated', self.session_manager)
        self.event_dispatcher.register_handler('error', self.session_manager)
    
    def _setup_component_callbacks(self) -> None:
        # Connection manager callbacks
        self.connection_manager.on_message_received = self._handle_openai_message
        self.connection_manager.on_connection_lost = self._handle_connection_lost
        
        # Audio processor callbacks
        self.audio_processor.on_audio_ready = self._send_audio_to_handler
        self.audio_processor.on_memory_limit_reached = self._handle_audio_memory_limit
        
        # Function call processor callbacks
        self.function_call_processor.on_tool_result = self._handle_tool_result
        self.function_call_processor.on_tool_error = self._handle_tool_error
        self.function_call_processor.on_tool_started = self._handle_tool_started
        
        # Session manager callbacks
        self.session_manager.on_session_created = self._handle_session_created
        self.session_manager.on_session_error = self._handle_session_error
    
    # Public API methods (inherited from BaseVoiceAssistant)
    
    async def connect(self) -> None:
        if self.state != VoiceAssistantState.DISCONNECTED:
            raise RuntimeError("Assistant is already connected or connecting")
        
        self.set_state(VoiceAssistantState.CONNECTING)
        
        try:
            await self.connection_manager.connect()
            await self.session_manager.initialize_session(
                self.connection_manager.websocket, 
                self.function_call_processor.tools
            )
            self.set_state(VoiceAssistantState.CONNECTED)
            
        except Exception as e:
            self.logger.error(f"Failed to connect: {e}")
            self.set_state(VoiceAssistantState.ERROR)
            if self.on_error:
                await self.on_error(f"Connection failed: {e}")
            raise
    
    async def disconnect(self) -> None:
        """Disconnect from OpenAI Realtime API."""
        # Cancel all background tasks
        await self.function_call_processor.cancel_all_tasks()
        
        # Clear accumulated data
        self.audio_processor.clear_accumulation()
        self.function_call_processor.clear_pending_calls()
        
        # Disconnect from OpenAI
        await self.connection_manager.disconnect()
        
        self.set_state(VoiceAssistantState.DISCONNECTED)
        self.logger.info("Assistant disconnected successfully")
    
    async def start_conversation(self, initial_message: str = None) -> None:
        """Start a conversation with optional initial message."""
        if self.state != VoiceAssistantState.CONNECTED:
            raise RuntimeError("Assistant must be connected before starting conversation")
        
        if initial_message:
            await self.session_manager.send_initial_message(
                self.connection_manager.websocket, 
                initial_message
            )
        
        # Start processing loops
        self.logger.info(f"🚀 Starting conversation loops for session {self.session_id}")
        await asyncio.gather(
            self._audio_input_loop(),
            self.connection_manager.start_message_loop()
        )
    
    async def send_text_message(self, message: str) -> None:
        """Send a text message to the assistant."""
        conversation_item = {
            "type": "conversation.item.create",
            "item": {
                "id": f"user_msg_{int(asyncio.get_event_loop().time() * 1000)}",
                "type": "message", 
                "role": "user",
                "content": [{
                    "type": "input_text",
                    "text": message
                }]
            }
        }
        
        await self.connection_manager.send_message(conversation_item)
        await self.connection_manager.send_message({"type": "response.create"})
        
        self.logger.info(f"✅ Text message sent: '{message[:50]}...'")
    
    async def send_audio_data(self, audio_data: bytes) -> None:
        self.audio_processor.track_input_audio(audio_data)
        
        # Convert to base64 and send
        audio_b64 = base64.b64encode(audio_data).decode('utf-8')
        audio_append = {
            "type": "input_audio_buffer.append",
            "audio": audio_b64
        }
        
        await self.connection_manager.send_message(audio_append)
        self.logger.debug(f"✅ Audio data sent: {len(audio_data)} bytes")
    
    async def interrupt_response(self) -> None:
        """Interrupt the current assistant response."""
        audio_end_ms = self.audio_processor.calculate_interrupt_timing()
        
        if self.audio_processor.last_assistant_item and audio_end_ms > 0:
            truncate_event = {
                "type": "conversation.item.truncate", 
                "item_id": self.audio_processor.last_assistant_item,
                "content_index": 0,
                "audio_end_ms": audio_end_ms
            }
            
            await self.connection_manager.send_message(truncate_event)
            
            # Clear accumulated audio
            self.audio_processor.clear_accumulation()
            
            if self.audio_handler:
                await self.audio_handler.clear_audio_buffer()
            
            self.logger.info(f"Interrupted response (audio_end_ms: {audio_end_ms}ms)")
        else:
            self.logger.warning("⚠️ No active response to interrupt")
    
    # Tool management methods
    
    def add_tool(self, tool) -> None:
        """Add a tool to the assistant."""
        self.function_call_processor.add_tool(tool)
    
    def remove_tool(self, tool_name: str) -> None:
        """Remove a tool from the assistant."""
        self.function_call_processor.remove_tool(tool_name)
    
    # Audio streaming management methods
    
    def get_streaming_mode(self) -> str:
        """Get current audio streaming mode."""
        return self.audio_processor.streaming_mode.value
    
    def get_audio_memory_info(self) -> dict:
        """Get audio memory and processing information."""
        audio_info = self.audio_processor.get_memory_info()
        function_info = self.function_call_processor.get_status_info()
        
        return {**audio_info, **function_info}
    
    def set_audio_streaming_mode(self, mode, **kwargs):
        """Change audio streaming mode dynamically."""
        from config import AudioStreamingMode
        
        if isinstance(mode, str):
            mode = AudioStreamingMode(mode)
        
        self.audio_processor.set_streaming_mode(mode, **kwargs)
        self.logger.info(f"Audio streaming mode changed to: {mode.value}")
    
    # Private event handling methods
    
    async def _send_audio_to_handler(self, audio_data: bytes) -> None:
        """Send audio data to the WebSocket handler for frontend playback."""
        try:
            if self.audio_handler:
                await self.audio_handler.send_audio(audio_data)
                self.logger.info(f"🔊 Forwarded {len(audio_data)} bytes of audio to WebSocket handler")
            else:
                self.logger.warning("⚠️ No audio handler available to send audio")
        except Exception as e:
            self.logger.error(f"❌ Error forwarding audio to handler: {e}", exc_info=True)
    
    async def _handle_audio_memory_limit(self, current_chunks: list) -> None:
        """Handle audio memory limit reached by sending accumulated audio."""
        if current_chunks and self.audio_handler:
            combined_audio = b''.join(current_chunks)
            await self.audio_handler.send_audio(combined_audio)
            self.logger.info(f"🔊 Sent early audio batch due to memory limit: {len(current_chunks)} chunks, {len(combined_audio)} bytes")
    
    async def _handle_connection_lost(self, error: Exception) -> None:
        """Handle lost connection to OpenAI."""
        error_msg = str(error)
        
        # Check if it's a keepalive timeout
        if "keepalive ping timeout" in error_msg or "1011" in error_msg:
            self.logger.warning("⚠️ OpenAI connection lost due to keepalive timeout - this is usually network related")
            self.logger.info("💡 Tips to prevent this:")
        else:
            self.logger.error(f"❌ Connection to OpenAI lost: {error}")
        
        self.set_state(VoiceAssistantState.ERROR)
        if self.on_error:
            await self.on_error(f"Connection lost: {error}")
    
    async def _handle_openai_message(self, message: dict) -> None:
        response_type = message.get('type')
        
        # Log important events only, reduce audio spam
        if response_type in ['error', 'session.created', 'session.updated']:
            self.logger.info(f"📥 OpenAI event: {response_type}")
        elif response_type in ['response.output_audio.done', 'response.done']:
            self.logger.info(f"🔊 Audio event: {response_type}")
        else:
            self.logger.debug(f"📥 OpenAI event: {response_type}")
        
        await self.openai_event_router.handle_event(response_type, message)
        
        await self._handle_direct_events(response_type, message)
    
    async def _handle_direct_events(self, response_type: str, message: dict) -> None:
        if response_type == 'input_audio_buffer.speech_started':
            self.set_state(VoiceAssistantState.LISTENING)
            if self.on_speech_started:
                await self.on_speech_started()
            
            # Interrupt current response if playing
            if self.audio_processor.last_assistant_item:
                await self.interrupt_response()
        
        elif response_type == 'input_audio_buffer.speech_stopped':
            if self.on_speech_ended:
                await self.on_speech_ended()
        
        elif response_type == 'response.output_audio_transcript.delta':
            delta = message.get('delta', '')
            if delta:
                # Accumulate transcript instead of logging each character
                if not hasattr(self, '_current_transcript'):
                    self._current_transcript = ""
                self._current_transcript += delta
        
        elif response_type == 'response.output_audio_transcript.done':
            # Log complete transcript when done
            if hasattr(self, '_current_transcript') and self._current_transcript:
                self.logger.info(f"💬 AI Complete Response: {self._current_transcript.strip()}")
                self._current_transcript = ""
        
        elif response_type == 'error':
            await self._handle_openai_error(message)
    
    async def _handle_openai_error(self, message: dict) -> None:
        """Handle OpenAI errors."""
        error_details = message.get('error', {})
        error_msg = error_details.get('message', 'Unknown error')
        error_type = error_details.get('type', 'unknown')
        error_code = error_details.get('code', 'unknown')
        
        self.logger.error(f"❌ OpenAI error: {error_msg}")
        self.logger.error(f"   Error type: {error_type}, Code: {error_code}")
        
        if self.on_error:
            await self.on_error(f"OpenAI error: {error_msg}")
    
    async def _handle_tool_started(self, tool_name: str, call_id: str) -> None:
        """Handle tool execution started - send immediate acknowledgment."""
        self.logger.info(f"🔧 Tool started: {tool_name} (call_id: {call_id})")
        
        # Send immediate response to prevent AI blocking
        immediate_response = {
            "type": "conversation.item.create",
            "item": {
                "type": "function_call_output",
                "call_id": call_id,
                "output": json.dumps({
                    "status": "executing",
                    "message": f"Working on {tool_name} for you. I'll have the result shortly and will let you know!"
                })
            }
        }
        await self.connection_manager.send_message(immediate_response)
        self.logger.info(f"📤 Sent immediate tool acknowledgment for: {tool_name}")
    
    async def _handle_tool_result(self, call_id: str, tool_name: str, result) -> None:
        """Handle tool execution result."""
        try:
            # Check if this is an immediate response or final result
            is_immediate = isinstance(result, dict) and result.get("status") == "executing"
            
            function_result = {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps(result)
                }
            }
            
            await self.connection_manager.send_message(function_result)
            
            # Only create response for final results, not immediate acknowledgments
            if not is_immediate:
                await asyncio.sleep(0.1)  # Prevent timing conflicts
                await self.connection_manager.send_message({"type": "response.create"})
                self.logger.info(f"📤 Sent final tool result for: {tool_name}")
            else:
                self.logger.info(f"📤 Sent immediate acknowledgment for: {tool_name}")
                
        except Exception as e:
            self.logger.error(f"❌ Failed to send tool result for {tool_name}: {e}", exc_info=True)
    
    async def _handle_tool_error(self, call_id: str, error_message: str) -> None:
        """Handle tool execution error."""
        error_result = {
            "type": "conversation.item.create", 
            "item": {
                "type": "function_call_output",
                "call_id": call_id,
                "output": json.dumps({"error": error_message})
            }
        }
        
        await self.connection_manager.send_message(error_result)
        await asyncio.sleep(0.1)  # Prevent timing conflicts
        await self.connection_manager.send_message({"type": "response.create"})
        
        self.logger.info(f"📤 Sent tool error: {error_message}")
    
    async def _handle_session_created(self) -> None:
        """Handle session created event."""
        self.logger.info("✅ Session ready for conversation")
    
    async def _handle_session_error(self, error_message: str) -> None:
        """Handle session error."""
        self.logger.error(f"❌ Session error: {error_message}")
        self.set_state(VoiceAssistantState.ERROR)
        if self.on_error:
            await self.on_error(f"Session error: {error_message}")
    
    async def _audio_input_loop(self) -> None:
        if not self.audio_handler:
            self.logger.warning("No audio handler available")
            return
        
        self.logger.info(f"🎤 Starting audio input loop for session {self.session_id}")
        try:
            while self.state in [VoiceAssistantState.CONNECTED, VoiceAssistantState.LISTENING]:
                audio_data = await self.audio_handler.receive_audio()
                if audio_data:
                    self.logger.debug(f"🎤 Processing input audio: {len(audio_data)} bytes")
                    await self.send_audio_data(audio_data)
                await asyncio.sleep(0.01)
                
        except Exception as e:
            self.logger.error(f"Audio input loop error: {e}")
            if self.on_error:
                await self.on_error(f"Audio input error: {e}")
        
        self.logger.info(f"🎤 Audio input loop ended for session {self.session_id}")

