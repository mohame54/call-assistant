import json
import uuid
import time
import base64
import asyncio
import websockets
from typing import Dict, Optional, Callable, Any
from audio_handlers import AudioHandler
from config import SessionConfig, VoiceAssistantState
import logging


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class RealTimeOpenAiVoiceAssistant:    
    def __init__(
        self, 
        api_key: str,
        session_id: str,
        session_config: Optional[SessionConfig] = None,
        audio_handler: Optional[AudioHandler] = None,
        non_blocking_tools_calling: Optional[bool] = True
    ):
        self.api_key = api_key
        self.session_id = session_id
        self.session_config = session_config if session_config  else  SessionConfig()
        self.audio_handler = audio_handler
        
        # Internal state
        self.state = VoiceAssistantState.DISCONNECTED
        self.openai_ws = None
        self.tools: dict = {}
        self.non_blocking_tools_calling = non_blocking_tools_calling
        
        # Audio tracking
        self.audio_start_time = None  # Track actual time when audio starts
        self.total_audio_duration_ms = 0  # Track cumulative audio duration in milliseconds
        self.last_assistant_item = None
        self.response_start_timestamp = None
        
        # Audio accumulation for natural playback
        self.current_audio_chunks = []  # Accumulate chunks for current response
        self.current_response_id = None  # Track current response ID
        
        # Audio memory management
        self.max_audio_memory_mb = 50  # Maximum 50MB of audio in memory
        self.max_chunks_per_response = 1000  # Maximum chunks per response
        self.current_audio_size = 0  # Track current audio size in bytes
        
        # Tool execution tracking
        self.active_tool_tasks: set = set()  # Track background tool executions
        
        # Function call accumulation
        self.pending_function_calls: Dict[str, Dict] = {}  # Track streaming function calls
        
        
        # Event callbacks
        self.on_state_change: Optional[Callable] = None
        self.on_speech_started: Optional[Callable] = None
        self.on_speech_ended: Optional[Callable] = None
        self.on_response_started: Optional[Callable] = None
        self.on_response_ended: Optional[Callable] = None
        self.on_error: Optional[Callable] = None
        
    def add_tool(self, tool) -> None:
        self.tools[tool.name] = tool
        logger.info(f"Added tool: {tool.name}")
    
    def remove_tool(self, tool_name: str) -> None:
        """Remove a tool/function"""
        if tool_name in self.tools:
            del self.tools[tool_name]
            logger.info(f"Removed tool: {tool_name}")
    
    def get_audio_memory_info(self) -> Dict[str, Any]:
        return {
            "current_chunks": len(self.current_audio_chunks),
            "current_size_bytes": self.current_audio_size,
            "current_size_mb": self.current_audio_size / 1024 / 1024,
            "max_memory_mb": self.max_audio_memory_mb,
            "max_chunks": self.max_chunks_per_response,
            "memory_usage_percent": (self.current_audio_size / (self.max_audio_memory_mb * 1024 * 1024)) * 100,
            "chunk_usage_percent": (len(self.current_audio_chunks) / self.max_chunks_per_response) * 100,
            "active_tool_tasks": len(self.active_tool_tasks),
            "pending_function_calls": len(self.pending_function_calls),
            "non_blocking_tools": self.non_blocking_tools_calling
        }
    
    async def connect(self) -> None:
        if self.state != VoiceAssistantState.DISCONNECTED:
            raise RuntimeError("Assistant is already connected or connecting")
            
        self._set_state(VoiceAssistantState.CONNECTING)
        
        try:
            url = f"wss://api.openai.com/v1/realtime?model={self.session_config.openai_config.model}"
            headers = {"Authorization": f"Bearer {self.api_key}"}
            
            logger.info(f"Connecting to OpenAI Realtime API: {url}")            
            self.openai_ws = await websockets.connect(url, additional_headers=headers)
            await self._initialize_session()
            self._set_state(VoiceAssistantState.CONNECTED)
            
            logger.info("Successfully connected to OpenAI Realtime API")
            
        except Exception as e:
            logger.error(f"Failed to connect to OpenAI Realtime API: {e}")
            logger.error(f"Exception type: {type(e).__name__}")
            self._set_state(VoiceAssistantState.ERROR)
            if self.on_error:
                await self.on_error(f"Connection failed: {e}")
            raise
    
    async def disconnect(self) -> None:
        """Disconnect from OpenAI Realtime API"""
        if self.openai_ws:
            await self.openai_ws.close()
            self.openai_ws = None
        
        # Clean up any remaining audio data
        self._clear_audio_accumulation()
        
        # Cancel any running background tool tasks
        if self.active_tool_tasks:
            logger.info(f"🛑 Cancelling {len(self.active_tool_tasks)} active tool tasks")
            for task in self.active_tool_tasks.copy():
                if not task.done():
                    task.cancel()
            # Wait for tasks to complete cancellation
            if self.active_tool_tasks:
                await asyncio.gather(*self.active_tool_tasks, return_exceptions=True)
            self.active_tool_tasks.clear()
        
        # Clear any pending function calls
        if self.pending_function_calls:
            logger.info(f"🧹 Clearing {len(self.pending_function_calls)} pending function calls")
            self.pending_function_calls.clear()
        
        self._set_state(VoiceAssistantState.DISCONNECTED)
        logger.info("Disconnected from OpenAI Realtime API")
    
    async def start_conversation(self, initial_message: str = None) -> None:
        """Start a conversation, optionally with an initial message"""
        if self.state != VoiceAssistantState.CONNECTED:
            raise RuntimeError("Assistant must be connected before starting conversation")
        
        if initial_message:
            await self._send_initial_message(initial_message)
        
        # Start the main processing loops
        await asyncio.gather(
            self._audio_input_loop(),
            self._openai_response_loop()
        )
    
    async def send_text_message(self, message: str) -> None:
        """Send a text message to the assistant"""
        if not self.openai_ws:
            raise RuntimeError("Not connected to OpenAI")
            
        conversation_item = {
            "type": "conversation.item.create",
            "item": {
                "id": f"user_msg_{int(time.time() * 1000)}",
                "type": "message", 
                "role": "user",
                "content": [{
                    "type": "input_text",
                    "text": message
                }]
            }
        }
        
        response_create = {"type": "response.create"}
        
        try:
            await self.openai_ws.send(json.dumps(conversation_item))
            await self.openai_ws.send(json.dumps(response_create))
            
            logger.info(f"✅ Text message sent to OpenAI: '{message[:50]}...'")
            
        except Exception as e:
            logger.error(f"❌ Failed to send text message to OpenAI: {e}", exc_info=True)
            raise
    
    async def send_audio_data(self, audio_data: bytes) -> None:
        if not self.openai_ws:
            raise RuntimeError("Not connected to OpenAI")
        
        # Convert audio to base64 if needed
        if isinstance(audio_data, bytes):
            audio_b64 = base64.b64encode(audio_data).decode('utf-8')
        else:
            audio_b64 = audio_data
            
        audio_append = {
            "type": "input_audio_buffer.append",
            "audio": audio_b64
        }
        
        try:
            await self.openai_ws.send(json.dumps(audio_append))
            audio_duration_ms = (len(audio_data) / 2) / 24 * 1000  # Convert bytes to milliseconds
            self.total_audio_duration_ms += audio_duration_ms
            
            # Track session start time
            if self.audio_start_time is None:
                self.audio_start_time = time.time() * 1000  # Convert to milliseconds
            
            logger.debug(f"✅ Audio data sent to OpenAI: {len(audio_data)} bytes ({audio_duration_ms:.1f}ms)")
            
        except Exception as e:
            logger.error(f"❌ Failed to send audio data to OpenAI: {e}", exc_info=True)
            raise
    
    async def interrupt_response(self) -> None:
        """Interrupt the current assistant response"""
        if self.last_assistant_item and self.response_start_timestamp is not None:
            # Calculate elapsed time since response started using actual wall-clock time
            current_time_ms = time.time() * 1000
            elapsed_time = current_time_ms - self.response_start_timestamp
            
            # Ensure we don't send a value larger than the actual audio duration
            # Use the minimum of elapsed time or total audio duration
            audio_end_ms = min(elapsed_time, self.total_audio_duration_ms)
            
            truncate_event = {
                "type": "conversation.item.truncate", 
                "item_id": self.last_assistant_item,
                "content_index": 0,
                "audio_end_ms": int(audio_end_ms)  # Ensure it's an integer
            }
            
            await self.openai_ws.send(json.dumps(truncate_event))
            
            # Clear any accumulated audio chunks since the response is being interrupted
            self._clear_audio_accumulation()
            
            if self.audio_handler:
                await self.audio_handler.clear_audio_buffer()
                
            self.last_assistant_item = None
            self.response_start_timestamp = None
            
            logger.info(f"Interrupted assistant response (audio_end_ms: {int(audio_end_ms)}ms)")
        else:
            logger.warning("⚠️ No active response to interrupt")
    
    def _clear_audio_accumulation(self) -> None:
        """Clear accumulated audio chunks"""
        if self.current_audio_chunks:
            logger.info(f"🧹 Clearing {len(self.current_audio_chunks)} accumulated audio chunks ({self.current_audio_size / 1024 / 1024:.2f} MB)")
            self.current_audio_chunks = []
            self.current_response_id = None
            self.current_audio_size = 0
    
    def _set_state(self, new_state: VoiceAssistantState) -> None:
        """Update the assistant state and trigger callback"""
        old_state = self.state
        self.state = new_state
        
        if self.on_state_change and old_state != new_state:
            asyncio.create_task(self.on_state_change(old_state, new_state))
    
    async def _initialize_session(self) -> None:
        tools_config = []
        if self.tools:
            for tt in self.tools.values():
                tool_json = tt.json
                tool_json.update({"type":"function"})
                tools_config.append(tool_json)
            
        session_update = {
            "type": "session.update",
            "session": {
                "type": "realtime",
                "instructions": self.session_config.openai_config.instructions,
                "tools": tools_config,
                "tool_choice": "auto" if tools_config else "none",
                "audio": {
                    "input": {
                        "format": {
                            "type": self.session_config.audio_config.input_format,
                            "rate": self.session_config.audio_config.sample_rate
                        },
                        "turn_detection": {
                            "type": self.session_config.turn_detection_type.name,
                            "create_response": self.session_config.turn_detection_type.create_response,
                            "threshold": self.session_config.turn_detection_type.threshold,
                            "prefix_padding_ms": self.session_config.turn_detection_type.prefix_padding_ms,
                            "silence_duration_ms": self.session_config.turn_detection_type.silence_duration_ms
                        },
                    },
                    "output": {
                        "format": {
                            "type": self.session_config.audio_config.output_format,
                            "rate": self.session_config.audio_config.sample_rate
                        },
                        "voice": self.session_config.audio_config.voice,
                        "speed": self.session_config.audio_config.speed
                    },
                }
            }
        }
        
        logger.info(f"Initializing session with config: {json.dumps(session_update, indent=2)}")
        await self.openai_ws.send(json.dumps(session_update))
        logger.info("Session initialization message sent")
        
        # Wait for session.created response
        logger.info("⏳ Waiting for session.created response...")
        session_created = False
        
        for i in range(self.session_config.session_creation_timeout_secs):  # Wait up to 5 seconds
            try:
                response = await asyncio.wait_for(self.openai_ws.recv(), timeout=1.0)
                data = json.loads(response)
                response_type = data.get('type', 'unknown')
                logger.info(f"📥 Session init response: {response_type}")
                
                if response_type == "session.created":
                    logger.info("✅ Session created successfully!")
                    session_created = True
                    break
                elif response_type == "error":
                    error_details = data.get('error', {})
                    error_msg = error_details.get('message', 'Unknown error')
                    error_code = error_details.get('code', 'unknown')
                    logger.error(f"❌ OpenAI Session Error: {error_msg} (code: {error_code})")
                    if "403" in str(error_code) or "unauthorized" in error_msg.lower():
                        logger.error("💡 This usually means:")
                    elif "401" in str(error_code):
                        logger.error("💡 Authentication failed - check your API key")
                    elif "429" in str(error_code):
                        logger.error("💡 Rate limited - try again in a moment")
                    
                    raise RuntimeError(f"Session creation failed: {error_msg}")
                    
            except asyncio.TimeoutError:
                logger.info(f"⏳ Still waiting for session.created... ({i+1}/10)")
            except json.JSONDecodeError as e:
                logger.error(f"❌ Failed to parse session response: {e}")
                continue
        
        if not session_created:
            logger.error("❌ Timeout waiting for session.created")
            raise RuntimeError("Timeout waiting for session.created")
    
    async def _send_initial_message(self, message: str) -> None:
        """Send an initial message to start the conversation"""
        conversation_item = {
            "type": "conversation.item.create",
            "item": {
                "type": "message",
                "role": "user", 
                "content": [{
                    "type": "input_text",
                    "text": message
                }]
            }
        }
        
        await self.openai_ws.send(json.dumps(conversation_item))
        await self.openai_ws.send(json.dumps({"type": "response.create"}))
    
    async def _audio_input_loop(self) -> None:
        if not self.audio_handler:
            logger.warning("[WARN] class RealTimeOpenAiVoiceAssistant._audio_input_loop no audio handler")
            return
            
        try:
            while self.state in [VoiceAssistantState.CONNECTED, VoiceAssistantState.LISTENING]:
                audio_data = await self.audio_handler.receive_audio()
                if audio_data:
                    await self.send_audio_data(audio_data)
                await asyncio.sleep(0.01)  # Small delay to prevent tight loop
                
        except Exception as e:
            logger.error(f"Audio input loop error: {e}", exc_info=True)
            if self.on_error:
                await self.on_error(f"Audio input error: {e}")
    
    async def _openai_response_loop(self) -> None:
        """Loop for processing OpenAI responses"""
        logger.info("🔄 Starting OpenAI response loop...")
        
        try:
            while self.state != VoiceAssistantState.DISCONNECTED:
                try:
                    # Use recv() with timeout like in diagnostic
                    message = await asyncio.wait_for(self.openai_ws.recv(), timeout=1.0)
                    
                    try:
                        response = json.loads(message)
                        await self._handle_openai_response(response)
                    except json.JSONDecodeError as e:
                        logger.error(f"❌ Failed to parse OpenAI message: {e}")
                        logger.error(f"   Raw message: {message}")
                    except Exception as e:
                        logger.error(f"❌ Error handling OpenAI response: {e}")
                        logger.error(f"   Response data: {message[:500]}...")
                        
                except asyncio.TimeoutError:
                    # No message received, continue loop (allows graceful shutdown)
                    continue
                except websockets.exceptions.ConnectionClosed as e:
                    logger.error(f"❌ OpenAI WebSocket connection closed: {e}")
                    self._set_state(VoiceAssistantState.ERROR)
                    if self.on_error:
                        await self.on_error(f"OpenAI connection lost: {e}")
                    break
                    
        except Exception as e:
            logger.error(f"❌ OpenAI response loop error: {e}", exc_info=True)
            logger.error(f"   Error type: {type(e).__name__}")
            self._set_state(VoiceAssistantState.ERROR)
            if self.on_error:
                await self.on_error(f"Response processing error: {e}")
        
        logger.warning("🛑 OpenAI response loop ended")
    
    async def _handle_openai_response(self, response: Dict[str, Any]) -> None:
        """Handle different types of responses from OpenAI"""
        response_type = response.get('type')
        
        
        # Log important OpenAI events only
        if response_type in ['error', 'session.created', 'session.updated']:
            logger.info(f"📥 OpenAI event: {response_type}")
            logger.info(f"OpenAI response details: {json.dumps(response, indent=2)}")
        else:
            logger.debug(f"📥 OpenAI event: {response_type}")
        
        # Handle different response types based on diagnostic patterns
        if response_type == 'response.audio.delta':
            await self._handle_audio_delta(response)
        elif response_type == 'response.function_call_arguments.delta':
            await self._handle_function_call_delta(response)
        elif response_type == 'response.function_call_arguments.done':
            await self._handle_function_call_done(response)
        elif response_type == 'input_audio_buffer.speech_started':
            await self._handle_speech_started()
        elif response_type == 'input_audio_buffer.speech_stopped':
            await self._handle_speech_stopped()
        elif response_type == 'response.output_audio.delta':
            await self._handle_audio_output_delta(response)
        elif response_type == 'response.output_audio_transcript.delta':
            await self._handle_transcript_delta(response)
        elif response_type == 'response.done':
            await self._handle_response_done(response)
        elif response_type == 'conversation.item.added':
            await self._handle_conversation_item_added(response)
        elif response_type == 'response.created':
            await self._handle_response_created(response)
        elif response_type == 'response.content_part.added':
            await self._handle_content_part_added(response)
        elif response_type == 'session.created':
            logger.info("✅ OpenAI session successfully created")
        elif response_type == 'session.updated':
            logger.info("✅ OpenAI session configuration updated")
        elif response_type in ['error']:
            error_details = response.get('error', {})
            error_msg = error_details.get('message', 'Unknown error')
            error_type = error_details.get('type', 'unknown')
            error_code = error_details.get('code', 'unknown')
            param = error_details.get('param', '')
            
            logger.error(f"❌ OpenAI error: {error_msg}")
            logger.error(f"   Error type: {error_type}, Code: {error_code}")
            if param:
                logger.error(f"   Parameter: {param}")
            
            # Provide specific guidance for audio format errors
            if 'audio' in error_msg.lower() and 'pcm16' in error_msg.lower():
                logger.error("💡 Audio format issue detected:")
            elif 'shorter than' in error_msg.lower() and 'ms' in error_msg.lower():
                logger.error("💡 Audio timing issue detected:")
            
            if self.on_error:
                await self.on_error(f"OpenAI error: {error_msg}")
        else:
            logger.debug(f"Unhandled OpenAI event: {response_type} - {response}")
    
    async def _handle_audio_delta(self, response: Dict[str, Any]) -> None:
        if 'delta' in response and self.audio_handler:
            audio_data = base64.b64decode(response['delta'])
            await self.audio_handler.send_audio(audio_data)
    
    async def _handle_audio_output_delta(self, response: Dict[str, Any]) -> None:
        if 'delta' in response and self.audio_handler:
            # Track response timing using wall-clock time
            if response.get("item_id") and response["item_id"] != self.last_assistant_item:
                self.response_start_timestamp = time.time() * 1000  # Current time in milliseconds
                self.last_assistant_item = response["item_id"]
                
                # Start new audio accumulation for this response
                self.current_audio_chunks = []
                self.current_response_id = response["item_id"]
                self.current_audio_size = 0
                
                if self.on_response_started:
                    await self.on_response_started()
            
            # Accumulate audio data instead of sending immediately
            try:
                audio_data = base64.b64decode(response['delta'])
                audio_size = len(audio_data)
                
                # Check memory limits before accumulating
                max_memory_bytes = self.max_audio_memory_mb * 1024 * 1024
                
                if (self.current_audio_size + audio_size) > max_memory_bytes:
                    logger.warning(f"⚠️ Audio memory limit reached ({self.max_audio_memory_mb}MB), sending accumulated audio early")
                    # Send current accumulated audio before adding new chunk
                    if self.current_audio_chunks and self.audio_handler:
                        combined_audio = b''.join(self.current_audio_chunks)
                        await self.audio_handler.send_audio(combined_audio)
                        logger.info(f"🔊 Sent early audio batch: {len(self.current_audio_chunks)} chunks, {self.current_audio_size / 1024 / 1024:.2f} MB")
                    
                    # Reset accumulation for new batch
                    self.current_audio_chunks = [audio_data]
                    self.current_audio_size = audio_size
                    
                elif len(self.current_audio_chunks) >= self.max_chunks_per_response:
                    logger.warning(f"⚠️ Audio chunk limit reached ({self.max_chunks_per_response}), sending accumulated audio early")
                    # Send current accumulated audio before adding new chunk
                    if self.current_audio_chunks and self.audio_handler:
                        combined_audio = b''.join(self.current_audio_chunks)
                        await self.audio_handler.send_audio(combined_audio)
                        logger.info(f"🔊 Sent early audio batch: {len(self.current_audio_chunks)} chunks, {self.current_audio_size / 1024 / 1024:.2f} MB")
                    
                    # Reset accumulation for new batch
                    self.current_audio_chunks = [audio_data]
                    self.current_audio_size = audio_size
                    
                else:
                    # Normal accumulation
                    self.current_audio_chunks.append(audio_data)
                    self.current_audio_size += audio_size
                
                logger.debug(f"🔊 Audio chunk accumulated ({audio_size} bytes, total: {len(self.current_audio_chunks)} chunks, {self.current_audio_size / 1024:.1f} KB)")
                
            except Exception as e:
                logger.error(f"❌ Error processing audio delta: {e}")
    
    async def _handle_function_call_delta(self, response: Dict[str, Any]) -> None:
        call_id = response.get('call_id')
        delta = response.get('delta', '')
        
        if not call_id:
            logger.warning("⚠️ Function call delta without call_id")
            return
        
        # Initialize or update the function call accumulation
        if call_id not in self.pending_function_calls:
            self.pending_function_calls[call_id] = {
                'arguments': '',
                'name': None,
                'call_id': call_id
            }
        
        # Accumulate the delta
        self.pending_function_calls[call_id]['arguments'] += delta
        
        logger.info(f"🔧 Accumulating function call args for {call_id}: +{len(delta)} chars (total: {len(self.pending_function_calls[call_id]['arguments'])})")
        
        # Store function name if available in response
        if 'name' in response:
            self.pending_function_calls[call_id]['name'] = response['name']
    
    async def _handle_function_call_done(self, response: Dict[str, Any]) -> None:
        function_name = response.get('name')
        call_id = response.get('call_id', str(uuid.uuid4()))
        
        # Use accumulated arguments if available, otherwise fall back to response
        if call_id in self.pending_function_calls:
            accumulated_data = self.pending_function_calls[call_id]
            arguments_str = accumulated_data['arguments']
            if not function_name:
                function_name = accumulated_data['name']
            del self.pending_function_calls[call_id]
            logger.info(f"🔧 Using accumulated arguments for {function_name}: {len(arguments_str)} chars")
        else:
            # Fallback to response arguments (shouldn't happen with proper streaming)
            arguments_str = response.get('arguments', '{}')
            logger.warning(f"⚠️ No accumulated arguments found for call {call_id}, using response args")
        
        if function_name not in self.tools:
            logger.error(f"❌ Unknown function: {function_name}")
            await self._send_tool_error(f"Unknown function: {function_name}", call_id)
            return
            
        try:
            arguments = json.loads(arguments_str)
            tool = self.tools[function_name]
            logger.info(f"🔧 Function call completed: {function_name} with args: {arguments}")
            
            if self.non_blocking_tools_calling:
                # Send immediate acknowledgment
                immediate_response = {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": json.dumps({
                            "status": "executing",
                            "message": f"we are working on {function_name} for you. I'll have the result shortly and will let you know!"
                        })
                    }
                }
                await self.openai_ws.send(json.dumps(immediate_response))
                
                # Execute tool in background task
                task = asyncio.create_task(self._execute_tool_async(tool, arguments, call_id, function_name))
                self.active_tool_tasks.add(task)
                task.add_done_callback(self.active_tool_tasks.discard)
                
            else:
                # Blocking execution (original behavior)
                result = await tool.acall(**arguments)
                await self._send_tool_result(result, call_id, function_name)
            
        except json.JSONDecodeError as e:
            logger.error(f"❌ Invalid JSON in function arguments: {e}")
            logger.error(f"   Arguments string: {arguments_str}")
            await self._send_tool_error(f"Invalid JSON in function arguments: {e}", call_id)
        except Exception as e:
            logger.error(f"❌ Tool execution error: {e}")
            await self._send_tool_error(str(e), call_id)
    
    async def _execute_tool_async(self, tool, arguments: dict, call_id: str, function_name: str) -> None:
        try:
            logger.info(f"🔧 Starting async execution of tool: {function_name}")
            
            result = await tool.acall(**arguments)
            
            # Send the actual result
            await self._send_tool_result(result, call_id, function_name)
            logger.info(f"✅ Async tool execution completed: {function_name}")
            
        except Exception as e:
            logger.error(f"❌ Async tool execution failed: {function_name} - {e}")
            await self._send_tool_error(str(e), call_id)
    
    async def _send_tool_result(self, result: Any, call_id: str, function_name: str) -> None:
        try:
            function_result = {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps(result)
                }
            }
            
            await self.openai_ws.send(json.dumps(function_result))
            
            # Add a small delay before creating response to avoid timing conflicts
            await asyncio.sleep(0.1)
            
            await self.openai_ws.send(json.dumps({"type": "response.create"}))
            logger.info(f"📤 Sent tool result for: {function_name}")
            
        except Exception as e:
            logger.error(f"❌ Failed to send tool result: {e}")
    
    async def _send_tool_error(self, error_message: str, call_id: str) -> None:
        """Send tool execution error back to OpenAI"""
        try:
            error_result = {
                "type": "conversation.item.create", 
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps({"error": error_message})
                }
            }
            await self.openai_ws.send(json.dumps(error_result))
            
            # Add a small delay before creating response to avoid timing conflicts
            await asyncio.sleep(0.1)
            
            await self.openai_ws.send(json.dumps({"type": "response.create"}))
            logger.info(f"📤 Sent tool error: {error_message}")
            
        except Exception as e:
            logger.error(f"❌ Failed to send tool error: {e}")
    
    async def _handle_speech_started(self) -> None:
        self._set_state(VoiceAssistantState.LISTENING)
        if self.on_speech_started:
            await self.on_speech_started()
        
        # Reset audio timing when user starts speaking
        self.audio_start_time = time.time() * 1000
        self.total_audio_duration_ms = 0
        
        # Interrupt current response if playing
        if self.last_assistant_item:
            await self.interrupt_response()
    
    async def _handle_speech_stopped(self) -> None:
        if self.on_speech_ended:
            await self.on_speech_ended()
    
    async def _handle_transcript_delta(self, response: Dict[str, Any]) -> None:
        delta = response.get('delta', '')
        if delta:
            logger.info(f"💬 AI: {delta}")
            # Store transcript for debugging and monitoring
            if not hasattr(self, '_current_transcript'):
                self._current_transcript = ""
            self._current_transcript += delta
    
    async def _handle_response_done(self, response: Dict[str, Any]) -> None:
        logger.info("✅ AI response completed")
        
        # Send accumulated audio as one complete chunk for natural playback
        if self.current_audio_chunks and self.audio_handler:
            try:
                # Combine all audio chunks into one continuous stream
                combined_audio = b''.join(self.current_audio_chunks)
                total_chunks = len(self.current_audio_chunks)
                total_size = len(combined_audio)
                
                logger.info(f"🔊 Sending complete audio response: {total_chunks} chunks, {total_size} bytes")
                
                # Send the complete audio stream
                await self.audio_handler.send_audio(combined_audio)
                
                self.current_audio_chunks = []
                self.current_response_id = None
                self.current_audio_size = 0
                
            except Exception as e:
                logger.error(f"❌ Error sending accumulated audio: {e}")
        
        # Log the complete transcript if available
        if hasattr(self, '_current_transcript') and self._current_transcript:
            logger.info(f"📝 Complete AI response: '{self._current_transcript.strip()}'")
            self._current_transcript = ""  # Reset for next response
        
        if self.on_response_ended:
            await self.on_response_ended()
    
    async def _handle_conversation_item_added(self, response: Dict[str, Any]) -> None:
        item = response.get('item', {})
        role = item.get('role', 'unknown')
        item_type = item.get('type', 'unknown')
        logger.info(f"💭 Conversation item added: {role} - {item_type}")
    
    async def _handle_response_created(self, response: Dict[str, Any]) -> None:
        logger.info("🚀 AI response generation started")
        if self.on_response_started:
            await self.on_response_started()
    
    async def _handle_content_part_added(self, response: Dict[str, Any]) -> None:
        part = response.get('part', {})
        part_type = part.get('type', 'unknown')
        logger.info(f"📝 Content part added: {part_type}")
        
        if part_type == 'audio':
            # Audio content part added
            logger.info(f"Audio content part: {part}")
        elif part_type == 'text':
            # Text content part added
            logger.info(f"Text content part: {part}")
        else:
            logger.info(f"Unknown content part type: {part_type}")

