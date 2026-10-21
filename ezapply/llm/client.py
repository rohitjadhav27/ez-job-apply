"""OpenAI-compatible LLM provider chain: the only way the pipeline talks to an LLM."""
from __future__ import annotations

import json
import re
import time
from typing import Callable

import openai

from ezapply.core.config import ProviderConfig

ATTEMPTS = 3
BACKOFF_SECONDS = (2, 4)
TIMEOUT_SECONDS = 60
RATE_LIMIT_DEFAULT_WAIT = 20.0
RATE_LIMIT_MAX_WAIT = 60.0
RATE_LIMIT_WAITS = 2        # waits-and-retries on the same provider per call
EXHAUSTED_AFTER = 3         # consecutive calls ending in rate-limit exhaustion = daily-quota signal
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


class LLMUnavailable(Exception):
    """Every configured provider failed or is exhausted."""


def _loads_dict(text: str) -> dict | None:
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _balanced_span(text: str) -> str | None:
    """First balanced {...} span, honouring string literals."""
    start = text.find("{")
    while start != -1:
        depth, in_str, escaped = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        start = text.find("{", start + 1)
    return None


def extract_json(text: str) -> dict | None:
    """Parse a JSON object from raw text, a fenced block, or the first balanced {...} span."""
    if not text:
        return None
    candidates = [text.strip()]
    candidates += [m.strip() for m in _FENCE.findall(text)]
    span = _balanced_span(text)
    if span:
        candidates.append(span)
    for candidate in candidates:
        parsed = _loads_dict(candidate)
        if parsed is not None:
            return parsed
    return None


class LLMClient:
    def __init__(
        self,
        providers: list[ProviderConfig],
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        make_client: Callable[..., openai.OpenAI] = openai.OpenAI,
    ) -> None:
        self._providers = list(providers)
        self._sleep = sleep
        self._clock = clock
        self._last_call: dict[str, float] = {}
        self._rl_streak: dict[str, int] = {}
        self._make_client = make_client
        self._clients: dict[str, openai.OpenAI] = {}
        self._exhausted: set[str] = set()
        self.last_provider: str | None = None

    def _client_for(self, p: ProviderConfig) -> openai.OpenAI:
        if p.name not in self._clients:
            self._clients[p.name] = self._make_client(
                base_url=p.base_url,
                api_key=p.api_key,
                timeout=TIMEOUT_SECONDS,
                max_retries=0,
                default_headers={"User-Agent": "ezapply/0.1"},
            )
        return self._clients[p.name]

    def _attempt(self, p: ProviderConfig, system: str, user: str,
                 required_keys: list[str], temperature: float) -> dict | None:
        """One request. None = failed attempt; RateLimitError propagates."""
        try:
            resp = self._client_for(p).chat.completions.create(
                model=p.model,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}],
                response_format={"type": "json_object"},
                temperature=temperature,
            )
        except openai.RateLimitError:
            raise
        except openai.APIError:
            return None
        try:
            content = resp.choices[0].message.content
        except (IndexError, TypeError, AttributeError):
            return None
        parsed = extract_json(content if isinstance(content, str) else "")
        if parsed is None or any(k not in parsed for k in required_keys):
            return None
        return parsed

    def _pace(self, p: ProviderConfig) -> None:
        last = self._last_call.get(p.name)
        if last is not None and p.min_interval_seconds > 0:
            wait = p.min_interval_seconds - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)
        self._last_call[p.name] = self._clock()

    @staticmethod
    def _retry_after(err: openai.RateLimitError) -> float:
        try:
            raw = err.response.headers.get("retry-after")
            if raw is not None:
                return max(0.0, min(float(raw), RATE_LIMIT_MAX_WAIT))
        except (AttributeError, TypeError, ValueError):
            pass
        return RATE_LIMIT_DEFAULT_WAIT

    def complete_json(self, system: str, user: str, required_keys: list[str],
                      temperature: float = 0.2) -> dict:
        for p in self._providers:
            if p.name in self._exhausted:
                continue
            attempt = rl_waits = 0
            while attempt < ATTEMPTS:
                self._pace(p)
                try:
                    result = self._attempt(p, system, user, required_keys, temperature)
                except openai.RateLimitError as e:
                    if rl_waits < RATE_LIMIT_WAITS:
                        rl_waits += 1
                        self._sleep(self._retry_after(e))
                        continue
                    self._rl_streak[p.name] = self._rl_streak.get(p.name, 0) + 1
                    if self._rl_streak[p.name] >= EXHAUSTED_AFTER:
                        self._exhausted.add(p.name)
                    break
                if result is not None:
                    self.last_provider = p.name
                    self._rl_streak[p.name] = 0
                    return result
                attempt += 1
                if attempt < ATTEMPTS:
                    self._sleep(BACKOFF_SECONDS[attempt - 1])
        raise LLMUnavailable("all LLM providers failed or are exhausted")
