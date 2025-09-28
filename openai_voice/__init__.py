"""
OpenAI Voice Assistant Package

This package provides both monolithic and modular implementations of the OpenAI Realtime Voice Assistant.
"""


# New modular implementation
from .modular_assistant import RealTimeOpenAiVoiceAssistantV2

# Base classes for extension
from .base import BaseVoiceAssistant, EventHandler, StateManager, ToolManager

# Individual components for custom assembly
from .connection_manager import OpenAIConnectionManager
from .audio_processor import AudioProcessor
from .function_call_processor import FunctionCallProcessor
from .session_manager import SessionManager
from .event_dispatcher import EventDispatcher, OpenAIEventRouter

__all__ = [
    'RealTimeOpenAiVoiceAssistantV2', 
    
    # Base classes
    'BaseVoiceAssistant',
    'EventHandler',
    'StateManager',
    'ToolManager',
    
    # Components
    'OpenAIConnectionManager',
    'AudioProcessor',
    'FunctionCallProcessor',
    'SessionManager',
    'EventDispatcher',
    'OpenAIEventRouter'
]

