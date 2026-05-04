"""
agentic_loopkit/loops/react.py — ReActExecutor bounded tool-use loop.

ReAct = Think → Execute (Thought → Action → Observation)

Design rules (from the architecture spec):
  - Loops MUST be bounded (max_steps hard cap)
  - LLM is called in think() — the primary reasoning phase
  - execute() is deterministic — no LLM calls here
  - action="done" is the terminal signal; action_input becomes the answer
  - on_step() hook exists for dashboard live-tail / telemetry
  - follow_up() emits a downstream event on completion (mirrors RALF)

Composition pattern:
  OODA governs strategy (outer loop).
  ReAct governs step-level tool use (inner loop).
  Wire ReActExecutor inside AgentBase.act() — await executor.run(event).

    OODA (outer):
      observe()  → gather signals
      orient()   → LLM reasons about what needs to happen
      decide()   → choose executor
      act()      → await ReActExecutor.run(event)
                       └─ ReAct (inner):
                            think()   → LLM picks next tool
                            execute() → call tool, get observation
                            (repeat until action="done")
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional, TYPE_CHECKING

from ..events.models import Event

if TYPE_CHECKING:
    from ..bus import EventBus

log = logging.getLogger("agentic_loopkit.react")


@dataclass
class ReActStep:
    """One Thought → Action → Observation turn in a ReAct trace."""

    step_number:  int
    thought:      str
    action:       str   # tool name; "done" = terminal signal
    action_input: Any
    observation:  str   # tool output; empty string on "done" step


@dataclass
class ReActResult:
    """Final output of a ReAct loop run."""

    status: str                           # "complete" | "error" | "max_steps_reached"
    answer: Any                           # final output on "complete"; None otherwise
    trace:  list[ReActStep] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return self.status == "complete"


class ReActExecutor(ABC):
    """
    Bounded tool-use execution loop (ReAct pattern).

    Subclass and implement think() and execute().
    The loop runs until action="done" or max_steps is reached.

    Example:

        class SearchExecutor(ReActExecutor):
            max_steps = 5

            async def think(self, event, trace):
                # call LLM with current trace context
                thought = "I need to search for recent events"
                action = "search"
                action_input = {"query": event.payload["question"]}
                return thought, action, action_input

            async def execute(self, action, action_input):
                if action == "search":
                    results = await search_api(action_input["query"])
                    return str(results)
                raise ValueError(f"Unknown action: {action}")

            async def follow_up(self, event, result):
                if result.is_complete:
                    return event.caused("search.complete", self.name,
                                        {"answer": result.answer})
                return None

    Wire inside an OODA agent's act() phase:

        class MyAgent(AgentBase):
            def __init__(self, name, bus):
                super().__init__(name, bus)
                self._executor = SearchExecutor("search", bus)

            async def act(self, event, action):
                result = await self._executor.run(event)
    """

    max_steps: int = 10

    def __init__(self, name: str, bus: "EventBus") -> None:
        self.name = name
        self._bus = bus

    # ── ReAct phases ───────────────────────────────────────────────────────────

    @abstractmethod
    async def think(
        self, event: Event, trace: list[ReActStep]
    ) -> tuple[str, str, Any]:
        """
        THINK — reason about the next step (primary LLM phase).
        Returns (thought, action_name, action_input).
        Return action_name="done" to terminate; action_input becomes the answer.
        """
        ...

    @abstractmethod
    async def execute(self, action: str, action_input: Any) -> str:
        """
        EXECUTE — run the named tool, return an observation string.
        Deterministic — no LLM calls here.
        Raise an exception on hard failure; the loop captures it as status="error".
        """
        ...

    async def on_step(self, step: ReActStep) -> None:
        """
        HOOK — called after each completed step (including the terminal "done" step).
        Override for logging, dashboard live-tail telemetry, or step-level event emission.
        Default: no-op.
        """
        pass

    async def follow_up(self, event: Event, result: ReActResult) -> Optional[Event]:
        """
        FOLLOW-UP — return a downstream event to publish, or None.
        Called once on terminal result.  Use event.caused() for full traceability.
        Default: no-op.
        """
        return None

    # ── Loop runner ────────────────────────────────────────────────────────────

    async def run(self, event: Event) -> ReActResult:
        """
        Execute the bounded ReAct loop.

        Terminates on:
          - action="done" returned from think()  → status="complete"
          - max_steps exhausted without "done"   → status="max_steps_reached"
          - unhandled exception in think/execute → status="error"

        Always calls follow_up() on the terminal result and publishes if not None.
        """
        log.info("[%s] starting ReAct loop for %s", self.name, event.event_type)

        trace: list[ReActStep] = []
        result: ReActResult

        try:
            for step_number in range(1, self.max_steps + 1):
                thought, action, action_input = await self.think(event, trace)

                if action == "done":
                    step = ReActStep(
                        step_number  = step_number,
                        thought      = thought,
                        action       = "done",
                        action_input = action_input,
                        observation  = "",
                    )
                    trace.append(step)
                    await self.on_step(step)

                    log.info("[%s] ReAct loop complete at step %d", self.name, step_number)
                    result = ReActResult(status="complete", answer=action_input, trace=trace)
                    break

                observation = await self.execute(action, action_input)

                step = ReActStep(
                    step_number  = step_number,
                    thought      = thought,
                    action       = action,
                    action_input = action_input,
                    observation  = observation,
                )
                trace.append(step)
                await self.on_step(step)

                log.debug(
                    "[%s] step %d/%d — action=%s",
                    self.name, step_number, self.max_steps, action,
                )

            else:
                # Loop exhausted without "done"
                log.warning(
                    "[%s] max_steps=%d reached without completion",
                    self.name, self.max_steps,
                )
                result = ReActResult(
                    status = "max_steps_reached",
                    answer = None,
                    trace  = trace,
                )

        except Exception as exc:
            log.error(
                "[%s] unhandled error in ReAct loop: %s",
                self.name, exc, exc_info=True,
            )
            result = ReActResult(
                status = "error",
                answer = None,
                trace  = trace,
            )

        follow_up_event = await self.follow_up(event, result)
        if follow_up_event is not None:
            await self._bus.publish(follow_up_event)

        log.info("[%s] ReAct loop done — status=%s", self.name, result.status)
        return result
