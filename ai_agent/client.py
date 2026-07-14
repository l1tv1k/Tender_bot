import httpx
import asyncio
import logging
import os


class MistralClient:
    def __init__(self, api_key: str, max_concurrent_requests: int = 3):
        self.api_key = api_key
        # Читаем базовый URL из окружения. Если его нет - используем официальный
        self.base_url = os.getenv("MISTRAL_API_URL", "https://api.mistral.ai")
        # Склеиваем с нужным эндпоинтом
        self.url = f"{self.base_url.rstrip('/')}/v1/chat/completions"

        self.semaphore = asyncio.Semaphore(max_concurrent_requests)
        self.logger = logging.getLogger(__name__)

    async def chat_completion(self, prompt: str, json_mode: bool = False) -> str:
        # ... остальной код метода остается без изменений ...
        async with self.semaphore:
            async with httpx.AsyncClient() as client:
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "model": "mistral-large-latest",
                    "messages": [{"role": "user", "content": prompt}],
                    "response_format": {"type": "json_object"} if json_mode else None
                }

                try:
                    response = await client.post(self.url, json=payload, headers=headers, timeout=60.0)
                    response.raise_for_status()
                    return response.json()['choices'][0]['message']['content']
                except Exception as e:
                    self.logger.error(f"Mistral API Error: {e}")
                    raise