import logging
from typing import Dict, Any, List
from .base import EventHandler

logger = logging.getLogger(__name__)


class EventDispatcher:    
    def __init__(self):
        self.handlers: Dict[str, List[EventHandler]] = {}
        self.global_handlers: List[EventHandler] = []
    
    def register_handler(self, event_type: str, handler: EventHandler) -> None:
        if event_type not in self.handlers:
            self.handlers[event_type] = []
        self.handlers[event_type].append(handler)
        logger.debug(f"Registered handler for event type: {event_type}")
    
    def register_global_handler(self, handler: EventHandler) -> None:
        """Register a handler for all events."""
        self.global_handlers.append(handler)
        logger.debug("Registered global event handler")
    
    def unregister_handler(self, event_type: str, handler: EventHandler) -> None:
        """Unregister a handler for a specific event type."""
        if event_type in self.handlers and handler in self.handlers[event_type]:
            self.handlers[event_type].remove(handler)
            logger.debug(f"Unregistered handler for event type: {event_type}")
    
    def unregister_global_handler(self, handler: EventHandler) -> None:
        """Unregister a global handler."""
        if handler in self.global_handlers:
            self.global_handlers.remove(handler)
            logger.debug("Unregistered global event handler")
    
    async def dispatch_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Dispatch an event to all registered handlers."""
        logger.debug(f"📡 Dispatching event: {event_type}")
        
        # Send to specific handlers
        if event_type in self.handlers:
            for handler in self.handlers[event_type]:
                try:
                    await handler.handle_event(event_type, data)
                except Exception as e:
                    logger.error(f"❌ Error in event handler for {event_type}: {e}")
        
        # Send to global handlers
        for handler in self.global_handlers:
            try:
                await handler.handle_event(event_type, data)
            except Exception as e:
                logger.error(f"❌ Error in global event handler: {e}")
    
    def get_handler_count(self, event_type: str = None) -> int:
        """Get the number of handlers for an event type or total handlers."""
        if event_type:
            return len(self.handlers.get(event_type, []))
        else:
            total = len(self.global_handlers)
            for handlers in self.handlers.values():
                total += len(handlers)
            return total
    
    def clear_handlers(self, event_type: str = None) -> None:
        """Clear handlers for a specific event type or all handlers."""
        if event_type:
            if event_type in self.handlers:
                self.handlers[event_type] = []
                logger.info(f"Cleared handlers for event type: {event_type}")
        else:
            self.handlers = {}
            self.global_handlers = []
            logger.info("Cleared all event handlers")


class OpenAIEventRouter(EventHandler):
    """Routes OpenAI events to the appropriate event types."""
    
    # Mapping of OpenAI event types to internal event types
    EVENT_MAPPING = {
        'response.audio.delta': 'audio_delta',
        'response.output_audio.delta': 'audio_output_delta',
        'response.output_audio_transcript.delta': 'transcript_delta',
        'response.function_call_arguments.delta': 'function_call_arguments.delta',
        'response.function_call_arguments.done': 'function_call_arguments.done',
        'input_audio_buffer.speech_started': 'speech_started',
        'input_audio_buffer.speech_stopped': 'speech_stopped',
        'response.done': 'response_done',
        'response.created': 'response_created',
        'conversation.item.added': 'conversation_item_added',
        'response.content_part.added': 'content_part_added',
        'session.created': 'session.created',
        'session.updated': 'session.updated',
        'error': 'error'
    }
    
    def __init__(self, dispatcher: EventDispatcher):
        self.dispatcher = dispatcher
    
    async def handle_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Handle OpenAI events by routing them through the dispatcher."""
        # Map OpenAI event type to internal event type
        internal_event_type = self.EVENT_MAPPING.get(event_type, event_type)
        
        # Dispatch the mapped event
        await self.dispatcher.dispatch_event(internal_event_type, data)
        
        # Also dispatch the original event type for specific handling
        if internal_event_type != event_type:
            await self.dispatcher.dispatch_event(event_type, data)

