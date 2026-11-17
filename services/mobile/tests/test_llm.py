"""Тесты клиента Qwen: ограничение параллельности и ожидание при 429.

Запуск из папки контейнера:  python -m unittest discover tests
"""

import asyncio
import json
import os
import sys
import unittest

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import llm  # noqa: E402

OK = {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": {"total_tokens": 3}}


class LlmClientTest(unittest.TestCase):
    def setUp(self):
        self._client, self._sleep = llm.httpx.AsyncClient, llm.asyncio.sleep
        self.sleeps = []

        async def fake_sleep(seconds):
            self.sleeps.append(seconds)
        llm.asyncio.sleep = fake_sleep

    def tearDown(self):
        llm.httpx.AsyncClient, llm.asyncio.sleep = self._client, self._sleep
        llm._parallel = asyncio.Semaphore(llm.LLM_MAX_PARALLEL)

    def serve(self, handler):
        real = self._client
        llm.httpx.AsyncClient = lambda **kw: real(transport=httpx.MockTransport(handler), **kw)

    def test_429_waits_retry_after_then_succeeds(self):
        answers = [httpx.Response(429, headers={"retry-after": "7"}), httpx.Response(200, json=OK)]
        self.serve(lambda request: answers.pop(0))
        r = asyncio.run(llm.ask_llm("x"))
        self.assertEqual(r["text"], "ok")
        self.assertEqual(self.sleeps, [7.0])

    def test_429_gives_up_after_retries(self):
        self.serve(lambda request: httpx.Response(429))
        with self.assertRaises(httpx.HTTPStatusError):
            asyncio.run(llm.ask_llm("x"))
        self.assertEqual(len(self.sleeps), llm.LLM_RATE_RETRIES)

    def test_other_errors_not_retried(self):
        self.serve(lambda request: httpx.Response(500))
        with self.assertRaises(httpx.HTTPStatusError):
            asyncio.run(llm.ask_llm("x"))
        self.assertEqual(self.sleeps, [])

    def test_request_body(self):
        seen = {}

        def handler(request):
            seen.update(json.loads(request.content))
            return httpx.Response(200, json=OK)
        self.serve(handler)
        asyncio.run(llm.ask_llm("x", think=False))
        self.assertEqual(seen["chat_template_kwargs"], {"enable_thinking": False})
        self.assertEqual(seen["max_tokens"], llm.LLM_MAX_TOKENS)

    def test_parallel_limited(self):
        state = {"now": 0, "max": 0}

        async def handler(request):
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
            await self._sleep(0.02)
            state["now"] -= 1
            return httpx.Response(200, json=OK)

        async def main():
            llm._parallel = asyncio.Semaphore(2)
            await asyncio.gather(*(llm.ask_llm("x") for _ in range(6)))
        self.serve(handler)
        asyncio.run(main())
        self.assertEqual(state["max"], 2)


if __name__ == "__main__":
    unittest.main()
