import logging
from typing import Any, Optional, List, Dict
from groq import Groq
from config.settings import get_settings

logger = logging.getLogger(__name__)


class GroqChatProvider:
    """
    Chat provider using Groq API with Llama models.
    Implements ChatProviderProtocol for dependency injection.
    """
    
    def __init__(self, api_key: str = None, model_name: str = None):
        settings = get_settings()
        key = api_key or settings.GROQ_API_KEY
        if not key:
            raise ValueError("GROQ_API_KEY must be configured")
        
        self._client = Groq(api_key=key)
        self._model = model_name or settings.GROQ_MODEL
        logger.info(f"GroqChatProvider initialized with model: {self._model}")

    def start_chat(self, system_context: str = None) -> "GroqChatSession":
        """Start a new chat session with optional system context."""
        return GroqChatSession(
            client=self._client,
            model=self._model,
            system_context=system_context
        )

    def send_message(self, chat_session: "GroqChatSession", message: str) -> Optional[str]:
        """Send a message to an existing chat session."""
        return chat_session.send_message(message)


class GroqChatSession:
    """
    Represents an active Groq chat session with message history.
    """
    
    def __init__(self, client: Groq, model: str, system_context: str = None):
        self._client = client
        self._model = model
        self._history: List[Dict[str, str]] = []
        
        if system_context:
            self._history.append({
                "role": "system",
                "content": system_context
            })
    
    def restore_turn(self, prompt: str, answer: str) -> None:
        self._history.extend([{"role": "user", "content": prompt}, {"role": "assistant", "content": answer}])

    def send_message(self, message: str) -> Optional[str]:
        """Send a message and get a response."""
        self._history.append({
            "role": "user",
            "content": message
        })
        
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=self._history,
                max_tokens=1500,
                temperature=0.7
            )
            
            if not response.choices:
                self._history.pop()
                return None
            
            assistant_message = response.choices[0].message.content
            
            self._history.append({
                "role": "assistant",
                "content": assistant_message
            })
            
            return assistant_message
            
        except Exception as e:
            # A failed request must not leave an unanswered user message behind
            # when the interface offers a retry.
            if self._history and self._history[-1]["role"] == "user":
                self._history.pop()
            logger.error(f"Groq API error: {e}")
            raise
