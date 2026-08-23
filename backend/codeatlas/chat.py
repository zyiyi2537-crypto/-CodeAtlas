"""Retrieval-augmented chat over indexed code (OpenAI-compatible LLM)."""

from __future__ import annotations

import httpx

from .models import User
from .retrieval import CodeRetriever
from .settings import Settings

_SYSTEM_PROMPT = (
    "You are CodeAtlas, an assistant that answers questions strictly from the "
    "provided code evidence. Rules:\n"
    "1. Answer in the same language the user asked in.\n"
    "2. Cite evidence inline as [1], [2], ... matching the numbered snippets.\n"
    "3. If the evidence is insufficient, say so plainly instead of guessing.\n"
    "4. Keep the answer focused; use short code quotes when helpful."
)

_MAX_CONTEXT_CHARS = 12_000
_MAX_HISTORY = 6


class ChatUnavailableError(RuntimeError):
    """Raised when chat is requested but no LLM provider is configured."""


class ChatService:
    def __init__(self, settings: Settings, retriever: CodeRetriever, provider=None):
        self.settings = settings
        self.retriever = retriever
        self.provider = provider

    @property
    def base_url(self) -> str:
        return self.provider.base_url if self.provider else self.settings.llm_base_url

    @property
    def api_key(self) -> str:
        return self.provider.api_key if self.provider else self.settings.llm_api_key

    @property
    def model(self) -> str:
        return self.provider.model if self.provider else self.settings.llm_model

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)

    def ask(
        self,
        question: str,
        user: User | None,
        repository_ids: list[str] | None = None,
        history: list[dict] | None = None,
    ) -> dict:
        if not self.enabled:
            raise ChatUnavailableError("chat provider is not configured")
        question = question.strip()
        if not question or len(question) > 1000:
            raise ValueError("question must contain between 1 and 1000 characters")

        evidence = self.retriever.search(
            question, user, repository_ids=repository_ids, limit=8
        )
        messages = self._build_messages(question, evidence, history or [])
        answer = self._complete(messages)
        return {
            "answer": answer,
            "citations": [self._citation(item) for item in evidence],
        }

    def _build_messages(
        self, question: str, evidence: list[dict], history: list[dict]
    ) -> list[dict]:
        context_parts: list[str] = []
        budget = _MAX_CONTEXT_CHARS
        for index, item in enumerate(evidence, start=1):
            meta = item["metadata"]
            snippet = str(item["document"])
            block = (
                f"[{index}] repo={meta['repo']} path={meta['path']} "
                f"lines={meta['start_line']}-{meta['end_line']} "
                f"symbol={meta.get('symbol') or '-'}\n{snippet}"
            )
            if len(block) > budget:
                break
            context_parts.append(block)
            budget -= len(block)
        context = "\n\n".join(context_parts) or "(no matching code found)"

        messages: list[dict] = [{"role": "system", "content": _SYSTEM_PROMPT}]
        for turn in history[-_MAX_HISTORY:]:
            role = turn.get("role")
            content = str(turn.get("content", ""))[:2000]
            if role in {"user", "assistant"} and content:
                messages.append({"role": role, "content": content})
        messages.append({
            "role": "user",
            "content": f"Code evidence:\n{context}\n\nQuestion: {question}",
        })
        return messages

    def _complete(self, messages: list[dict]) -> str:
        response = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "messages": messages,
                "temperature": 0.2,
            },
            timeout=90,
        )
        response.raise_for_status()
        payload = response.json()
        choices = payload.get("choices") or []
        if not choices:
            raise RuntimeError("LLM provider returned no choices")
        content = choices[0].get("message", {}).get("content", "")
        if not content:
            raise RuntimeError("LLM provider returned an empty answer")
        return str(content)

    @staticmethod
    def _citation(item: dict) -> dict:
        meta = item["metadata"]
        return {
            "repo": meta["repo"],
            "path": meta["path"],
            "symbol": meta.get("symbol") or "",
            "start_line": meta["start_line"],
            "end_line": meta["end_line"],
        }
