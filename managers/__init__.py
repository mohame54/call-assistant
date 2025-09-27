"""
Connection Manager Module

This module provides base classes and implementations for managing connections
and audio streams for different platforms.
"""

from .base import BaseConnectionManager, BaseAudioConnectionManager
from .twilio_manager import TwilioConnectionManager

__all__ = [
    "BaseConnectionManager",
    "BaseAudioConnectionManager", 
    "TwilioConnectionManager"
]
