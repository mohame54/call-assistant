import json
import uuid
import asyncio
import logging
from typing import Dict, Any, Optional, Set
from .base import EventHandler, ToolManager



class FunctionCallProcessor(EventHandler, ToolManager):
    """Handles function call streaming, accumulation, and tool execution."""
    
    def __init__(self, non_blocking: bool = True):
        super().__init__()
        self.non_blocking_tools_calling = non_blocking
        
        # Function call accumulation
        self.pending_function_calls: Dict[str, Dict] = {}
        
        # Tool execution tracking
        self.active_tool_tasks: Set[asyncio.Task] = set()
        self.logger = logging.getLogger(__name__)

        
        # Event callbacks
        self.on_tool_result: Optional[callable] = None
        self.on_tool_error: Optional[callable] = None
        self.on_tool_started: Optional[callable] = None
    
    def add_tool(self, tool) -> None:
        """Add a tool to the processor."""
        self.tools[tool.name] = tool
        self.logger.info(f"Added tool: {tool.name}")
    
    def remove_tool(self, tool_name: str) -> None:
        """Remove a tool from the processor."""
        if tool_name in self.tools:
            del self.tools[tool_name]
            self.logger.info(f"Removed tool: {tool_name}")
    
    async def handle_function_call_delta(self, response: Dict[str, Any]) -> None:
        """Handle streaming function call argument deltas."""
        call_id = response.get('call_id')
        delta = response.get('delta', '')
        
        if not call_id:
            self.logger.warning("⚠️ Function call delta without call_id")
            return
        
        # Initialize or update function call accumulation
        if call_id not in self.pending_function_calls:
            self.pending_function_calls[call_id] = {
                'arguments': '',
                'name': None,
                'call_id': call_id
            }
        
        # Accumulate the delta
        self.pending_function_calls[call_id]['arguments'] += delta
        
        self.logger.debug(f"🔧 Accumulating function call args for {call_id}: +{len(delta)} chars (total: {len(self.pending_function_calls[call_id]['arguments'])})")
        
        # Store function name if available
        if 'name' in response:
            self.pending_function_calls[call_id]['name'] = response['name']
    
    async def handle_function_call_done(self, response: Dict[str, Any]) -> None:
        """Handle completed function calls."""
        function_name = response.get('name')
        call_id = response.get('call_id', str(uuid.uuid4()))
        
        # Use accumulated arguments if available
        if call_id in self.pending_function_calls:
            accumulated_data = self.pending_function_calls[call_id]
            arguments_str = accumulated_data['arguments']
            if not function_name:
                function_name = accumulated_data['name']
            del self.pending_function_calls[call_id]
            self.logger.info(f"🔧 Using accumulated arguments for {function_name}: {len(arguments_str)} chars")
        else:
            # Fallback to response arguments
            arguments_str = response.get('arguments', '{}')
            self.logger.warning(f"⚠️ No accumulated arguments found for call {call_id}")
        
        if function_name not in self.tools:
            self.logger.error(f"❌ Unknown function: {function_name}")
            if self.on_tool_error:
                await self.on_tool_error(call_id, f"Unknown function: {function_name}")
            return
        
        try:
            arguments = json.loads(arguments_str)
            await self.execute_tool(function_name, arguments, call_id)
            
        except json.JSONDecodeError as e:
            self.logger.error(f"❌ Invalid JSON in function arguments: {e}")
            if self.on_tool_error:
                await self.on_tool_error(call_id, f"Invalid JSON: {e}")
        except Exception as e:
            self.logger.error(f"❌ Function call processing error: {e}")
            if self.on_tool_error:
                await self.on_tool_error(call_id, str(e))
    
    async def execute_tool(self, tool_name: str, arguments: dict, call_id: str) -> Any:
        """Execute a tool with the given arguments."""
        if tool_name not in self.tools:
            raise ValueError(f"Tool '{tool_name}' not found")
        
        tool = self.tools[tool_name]
        self.logger.info(f"🔧 Function call completed: {tool_name} with args: {arguments}")
        
        if self.non_blocking_tools_calling:
            # Send immediate acknowledgment to OpenAI
            if self.on_tool_started:
                await self.on_tool_started(tool_name, call_id)
            
            # Send immediate response to prevent AI blocking
            immediate_response = {
                "status": "executing",
                "message": f"Working on {tool_name} for you. I'll have the result shortly and will let you know!"
            }
            if self.on_tool_result:
                await self.on_tool_result(call_id, tool_name, immediate_response)
            
            # Execute tool in background
            task = asyncio.create_task(self._execute_tool_async(tool, arguments, call_id, tool_name))
            self.active_tool_tasks.add(task)
            task.add_done_callback(self.active_tool_tasks.discard)
            
        else:
            # Blocking execution
            result = await tool.acall(**arguments)
            if self.on_tool_result:
                await self.on_tool_result(call_id, tool_name, result)
            return result
    
    async def _execute_tool_async(self, tool, arguments: dict, call_id: str, function_name: str) -> None:
        """Execute tool asynchronously without blocking."""
        try:
            self.logger.info(f"🔧 Starting async execution of tool: {function_name}")
            
            # Execute the tool
            result = await tool.acall(**arguments)
            
            # Send result via callback
            if self.on_tool_result:
                await self.on_tool_result(call_id, function_name, result)
            
            self.logger.info(f"✅ Async tool execution completed: {function_name}")
            
        except Exception as e:
            self.logger.error(f"❌ Async tool execution failed: {function_name} - {e}")
            if self.on_tool_error:
                await self.on_tool_error(call_id, str(e))
    
    async def cancel_all_tasks(self) -> None:
        """Cancel all running tool tasks."""
        if self.active_tool_tasks:
            self.logger.info(f"🛑 Cancelling {len(self.active_tool_tasks)} active tool tasks")
            for task in self.active_tool_tasks.copy():
                if not task.done():
                    task.cancel()
            # Wait for cancellation
            if self.active_tool_tasks:
                await asyncio.gather(*self.active_tool_tasks, return_exceptions=True)
            self.active_tool_tasks.clear()
    
    def clear_pending_calls(self) -> None:
        """Clear all pending function calls."""
        if self.pending_function_calls:
            self.logger.info(f"🧹 Clearing {len(self.pending_function_calls)} pending function calls")
            self.pending_function_calls.clear()
    
    def get_status_info(self) -> Dict[str, Any]:
        """Get current function call processor status."""
        return {
            "active_tool_tasks": len(self.active_tool_tasks),
            "pending_function_calls": len(self.pending_function_calls),
            "non_blocking_tools": self.non_blocking_tools_calling,
            "available_tools": list(self.tools.keys())
        }
    
    async def handle_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Handle function call related events."""
        if event_type == "function_call_arguments.delta":
            await self.handle_function_call_delta(data)
        
        elif event_type == "function_call_arguments.done":
            await self.handle_function_call_done(data)
        
        elif event_type == "cancel_all_tools":
            await self.cancel_all_tasks()
        
        elif event_type == "clear_pending_calls":
            self.clear_pending_calls()

