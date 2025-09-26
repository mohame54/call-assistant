from typing import Optional
from abc import abstractmethod, ABC
from .utils import openai_tool


class BaseTool(ABC):
    def __init__(self, name: Optional[str]=None, description: Optional[str]=None):
        self.name = name
        self.description = description

    @abstractmethod
    def __call__(self, inputs):
        raise NotImplementedError(f"__call__ method for tool: {self.name} is not yet implemented")
    
    @abstractmethod
    async def acall(self, inputs):
        raise NotImplementedError(f"acall method for tool: {self.name} is not yet implemented")
    
    @property
    def json(self):
        if not hasattr(self.__call__, "__openai_tool__"):
            raise NotImplementedError("couldn't find the decorator for the calling methods for this tool you should use `openai_tool` decorator")
        js_dict = self.__call__.__openai_tool__
        if self.name:
          js_dict['name'] = self.name
        if self.description:
            js_dict['description'] = self.description
        return js_dict