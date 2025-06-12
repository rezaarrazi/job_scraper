from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List
import os
from pydantic import BaseModel
from google.genai import types

class AIProvider(ABC):
    """Abstract base class for AI providers"""
    
    @abstractmethod
    def generate_text(self, prompt: str, response_schema: Optional[BaseModel] = None, parse_response: Optional[bool] = False, **kwargs) -> str:
        """Generate text based on the prompt"""
        pass

    @abstractmethod
    def generate_embedding(self, text: str, **kwargs) -> List[float]:
        """Generate embedding based on the text"""
        pass
    
class OpenAIProvider(AIProvider):
    """OpenAI implementation of AIProvider"""
    
    def __init__(self, api_key: Optional[str] = None):
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("OpenAI package not installed. Please install it with 'pip install openai'")
            
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OpenAI API key not provided and not found in environment variables")
            
        self.client = OpenAI(api_key=self.api_key)
    
    def generate_text(self, prompt: str, response_schema: Optional[BaseModel] = None, parse_response: Optional[bool] = False, **kwargs) -> str:
        model = kwargs.get("model", "gpt-4.1-mini")

        if response_schema:
            response = self.client.beta.chat.completions.parse(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                response_format=response_schema
            )
        else:
            response = self.client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}]
            )

        output = None
        if parse_response:
            output = response.choices[0].message.parsed
        else:
            output = response.choices[0].message.content

        return output

    def generate_embedding(self, text: str, **kwargs) -> List[float]:
        model = kwargs.get("model", "text-embedding-3-small")
        return self.client.embeddings.create(input=text, model=model).data[0].embedding

class GeminiProvider(AIProvider):
    """Google's Gemini implementation of AIProvider"""
    
    def __init__(self, api_key: Optional[str] = None):
        try:
            from google import genai
        except ImportError:
            raise ImportError("Google genai package not installed. Please install it with 'pip install google-genai'")
            
        self.api_key = api_key or os.getenv("GOOGLE_API_KEY")
        if not self.api_key:
            raise ValueError("Google API key not provided and not found in environment variables")
        
        self.model = genai.Client(api_key=self.api_key)
    
    def generate_text(self, prompt: str, response_schema: Optional[BaseModel] = None, parse_response: Optional[bool] = False, **kwargs) -> str:
        model = kwargs.pop("model", "gemini-2.0")
        if response_schema:
            config = kwargs.pop('config', {})
            config['response_schema'] = response_schema
            kwargs['config'] = config

        file_content = kwargs.pop('file_content', None)
        if file_content:
            contents = [types.Part.from_bytes(data=file_content, mime_type='application/pdf'),
                        prompt]
        else:
            contents = [prompt]

        response = self.model.models.generate_content(model=model, contents=contents, **kwargs)

        if parse_response:
            if response.parsed:
                output = response.parsed
            else:
                raise ValueError("No parsed response found, text response: " + response.text)
        else:
            output = response.text

        return output

    def generate_embedding(self, text: str, **kwargs) -> List[float]:
        """Generate embedding using Google's text-embedding model"""
        model = kwargs.get("model", "text-embedding-004")
        
        response = self.model.models.embed_content(
            model=model,
            content=text
        )
        
        return response.embedding

class AIClient:
    """Main AI client wrapper that manages different AI providers"""
    
    PROVIDERS = {
        "openai": OpenAIProvider,
        "gemini": GeminiProvider
    }
    
    def __init__(self, provider: str = "openai", api_key: Optional[str] = None):
        """
        Initialize AI client with specified provider
        
        Args:
            provider: The AI provider to use ("openai" or "gemini")
            api_key: Optional API key for the provider
        """
        if provider not in self.PROVIDERS:
            raise ValueError(f"Unsupported provider: {provider}. Available providers: {list(self.PROVIDERS.keys())}")
            
        self.provider = self.PROVIDERS[provider](api_key)
    
    def generate_text(self, prompt: str, response_schema: Optional[BaseModel] = None, parse_response: Optional[bool] = False, **kwargs) -> str:
        """
        Generate text using the configured AI provider
        
        Args:
            prompt: The input prompt
            response_schema: Optional Pydantic model for the response
            parse_response: Whether to parse the response using the response schema
            **kwargs: Additional arguments to pass to the provider
        
        Returns:
            Generated text response
        """
        return self.provider.generate_text(prompt, response_schema, parse_response, **kwargs)
    
    def generate_embedding(self, text: str, **kwargs) -> List[float]:
        """
        Generate embedding using the configured AI provider
        
        Args:
            text: The input text to generate embedding for
        """
        return self.provider.generate_embedding(text, **kwargs)
