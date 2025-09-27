from abc import ABC, abstractmethod
from typing import Dict, Optional, Callable, Any
from config import VoiceAssistantState


class EventHandler(ABC):
    """Abstract base class for event handling."""
    
    @abstractmethod
    async def handle_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Handle an event with the given type and data."""
        pass


class StateManager(ABC):
    """Abstract base class for state management."""
    
    def __init__(self):
        self.state = VoiceAssistantState.DISCONNECTED
        self.on_state_change: Optional[Callable] = None
    
    def set_state(self, new_state: VoiceAssistantState) -> None:
        """Set the assistant state and trigger callbacks."""
        old_state = self.state
        self.state = new_state
        
        if self.on_state_change and old_state != new_state:
            import asyncio
            asyncio.create_task(self.on_state_change(old_state, new_state))
    
    def get_state(self) -> VoiceAssistantState:
        """Get the current assistant state."""
        return self.state


class BaseVoiceAssistant(StateManager):
    
    def __init__(self, api_key: str, session_id: str):
        super().__init__()
        self.api_key = api_key
        self.session_id = session_id
        
        # Event callbacks (extending StateManager's on_state_change)
        self.on_speech_started: Optional[Callable] = None
        self.on_speech_ended: Optional[Callable] = None
        self.on_response_started: Optional[Callable] = None
        self.on_response_ended: Optional[Callable] = None
        self.on_error: Optional[Callable] = None
    
    @abstractmethod
    async def connect(self) -> None:
        """Connect to the voice service."""
        pass
    
    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from the voice service."""
        pass
    
    @abstractmethod
    async def send_text_message(self, message: str) -> None:
        """Send a text message."""
        pass
    
    @abstractmethod
    async def send_audio_data(self, audio_data: bytes) -> None:
        """Send audio data."""
        pass
    
    @abstractmethod
    async def interrupt_response(self) -> None:
        """Interrupt the current response."""
        pass
    
    async def start_conversation(self, initial_message: Optional[str] = None) -> None:
        """Start a conversation with an optional initial message."""
        if initial_message:
            await self.send_text_message(initial_message)
    
    async def trigger_speech_started(self) -> None:
        """Trigger speech started event callback."""
        if self.on_speech_started:
            await self.on_speech_started()
    
    async def trigger_speech_ended(self) -> None:
        """Trigger speech ended event callback."""
        if self.on_speech_ended:
            await self.on_speech_ended()
    
    async def trigger_response_started(self) -> None:
        """Trigger response started event callback."""
        if self.on_response_started:
            await self.on_response_started()
    
    async def trigger_response_ended(self) -> None:
        """Trigger response ended event callback."""
        if self.on_response_ended:
            await self.on_response_ended()
    
    async def trigger_error(self, error_message: str) -> None:
        """Trigger error event callback."""
        if self.on_error:
            await self.on_error(error_message)


class ToolManager(ABC):
    
    def __init__(self):
        self.tools: Dict[str, Any] = {}
    
    @abstractmethod
    def add_tool(self, tool) -> None:
        """Add a tool to the manager."""
        pass
    
    @abstractmethod
    def remove_tool(self, tool_name: str) -> None:
        """Remove a tool from the manager."""
        pass
    
    @abstractmethod
    async def execute_tool(self, tool_name: str, arguments: dict, call_id: str) -> Any:
        """Execute a tool with the given arguments."""
        pass

