"""LangGraph answer graph and the streaming runner around it.

direct (default): START -> answer -> END            (one LLM call, fastest)
web:              START -> search -> answer -> END   (search results cited as [n])
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field
from typing import Any, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from app.agent.prompts import NO_QUESTION, system_prompt, user_prompt
from app.providers.factory import Providers

log = logging.getLogger(__name__)

SEARCH_TIMEOUT_S = 3.0


class AnswerState(TypedDict, total=False):
    transcript: str
    typed_question: str | None
    mode: str
    trigger: str
    owner: str
    brief: str
    search_results: list[dict]
    answer: str


@dataclass
class AnswerEvent:
    kind: str  # "sources" | "token" | "suppressed" | "done"
    data: Any = None
    info: dict = field(default_factory=dict)


# Returns the text inside a message chunk whether content is a string or content blocks. — pradeep.singh@rodicconsultants.com
def chunk_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") in (None, "text"))
    return ""


# Picks the search query: the typed question, else the last client line. — pradeep.singh@rodicconsultants.com
def search_query(state: AnswerState) -> str:
    if state.get("typed_question"):
        return state["typed_question"]
    for line in reversed(state.get("transcript", "").splitlines()):
        if line.startswith("Client:"):
            return line.removeprefix("Client:").strip()
    return state.get("transcript", "")[-300:]


# Compiles the answer graph bound to one provider bundle. — pradeep.singh@rodicconsultants.com
def build_graph(providers: Providers):
    # Runs web search with a hard timeout; returns no results on failure. — pradeep.singh@rodicconsultants.com
    async def search_node(state: AnswerState) -> dict:
        try:
            results = await asyncio.wait_for(providers.search.search(search_query(state), k=3), SEARCH_TIMEOUT_S)
        except Exception as exc:  # search is best-effort; the answer still comes from the model
            log.warning("search failed: %s", exc)
            results = []
        return {"search_results": [asdict(r) for r in results]}

    # Calls the LLM once; tokens stream to the caller through LangGraph's messages mode. — pradeep.singh@rodicconsultants.com
    async def answer_node(state: AnswerState) -> dict:
        results = state.get("search_results") or []
        block = "\n".join(f"[{i}] {r['title']} ({r['url']}): {r['snippet']}" for i, r in enumerate(results, 1))
        messages = [
            SystemMessage(system_prompt(state["owner"], state["trigger"], bool(results), bool(state.get("typed_question")), state.get("brief", ""))),
            HumanMessage(user_prompt(state.get("transcript", ""), state.get("typed_question"), block)),
        ]
        msg = await providers.llm.ainvoke(messages)
        return {"answer": chunk_text(msg.content)}

    # Chooses the first node from the answer mode. — pradeep.singh@rodicconsultants.com
    def route(state: AnswerState) -> str:
        return "search" if state.get("mode") == "web" else "answer"

    graph = StateGraph(AnswerState)
    graph.add_node("search", search_node)
    graph.add_node("answer", answer_node)
    graph.add_conditional_edges(START, route, {"search": "search", "answer": "answer"})
    graph.add_edge("search", "answer")
    graph.add_edge("answer", END)
    return graph.compile()


# Runs the graph and yields sources, tokens and a final done event; suppresses NO_QUESTION replies. — pradeep.singh@rodicconsultants.com
async def stream_answer(graph, state: AnswerState) -> AsyncIterator[AnswerEvent]:
    started = time.perf_counter()
    first_token_ms: int | None = None
    detect = state.get("trigger") == "auto"
    held = ""          # text held back until we know it is not NO_QUESTION
    released = detect is False
    full = []

    async for mode, payload in graph.astream(state, stream_mode=["messages", "updates"]):
        if mode == "updates":
            update = payload.get("search") if isinstance(payload, dict) else None
            if update and update.get("search_results"):
                yield AnswerEvent("sources", update["search_results"])
            continue
        chunk, meta = payload
        if meta.get("langgraph_node") != "answer":
            continue
        text = chunk_text(getattr(chunk, "content", ""))
        if not text:
            continue
        if not released:
            held += text
            probe = held.lstrip()
            if probe.startswith(NO_QUESTION) or (len(probe) < len(NO_QUESTION) and NO_QUESTION.startswith(probe)):
                if probe.startswith(NO_QUESTION):
                    yield AnswerEvent("suppressed")
                    return
                continue
            released, text = True, held
        if first_token_ms is None:
            first_token_ms = int((time.perf_counter() - started) * 1000)
        full.append(text)
        yield AnswerEvent("token", text)

    if not released:  # stream ended while still ambiguous (e.g. empty answer)
        if held.strip() and not NO_QUESTION.startswith(held.strip()):
            full.append(held)
            yield AnswerEvent("token", held)
        else:
            yield AnswerEvent("suppressed")
            return
    yield AnswerEvent("done", "".join(full), {
        "first_token_ms": first_token_ms,
        "total_ms": int((time.perf_counter() - started) * 1000),
    })
