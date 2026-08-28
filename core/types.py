"""Neutral types the core speaks. No harness shape leaks in here.

A harness adapter maps its own message/tool representation to and from these; the core
never sees opencode's message array, hook input, or Prime Agent's kernel objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

# An Event Log address: the immutable, monotonically increasing row id of an episode.
Seq = int

Role = Literal["system", "user", "assistant", "memory", "tool"]


@dataclass(frozen=True)
class ToolContext:
    """Identity carried on every tool call. Supplied by the adapter from its own context.

    sessionID is the harness session the call ran in; agent is the acting agent name
    (e.g. build/plan/explore/general/reviewer); callID identifies the specific tool
    invocation when the harness exposes it (opencode's tool.execute.after does; the
    ToolContext passed to a tool body may not, which is why it is optional).
    """

    sessionID: str = ""
    agent: str = ""
    callID: Optional[str] = None


@dataclass
class Episode:
    """One raw, non-lossy unit of memory: a turn, a memory_write, or an evicted tool result.

    Keyed by `thread` (the conversation root session id) so recall is scoped per
    conversation. `seq` is assigned by the store on append (the row id / Event Log
    address); it is None before persistence.
    """

    thread: str
    session: str
    agent: str
    role: str
    content: str
    ts: int
    seq: Optional[Seq] = None


@dataclass
class ToolResult:
    """A completed tool result as the core sees it, decoupled from any harness part shape.

    `call_id` ties it back to the invocation; `text` is the payload the model produced or
    the tool returned; `compacted` marks it evicted from the live view (the durable copy
    lives in the Event Log by `seq`). The adapter is responsible for translating this to
    and from the harness's native representation.
    """

    call_id: str
    text: str
    compacted: bool = False
    seq: Optional[Seq] = None


@dataclass
class Turn:
    """A single conversation turn in neutral form: role + text + reasoning + any tool results.

    The context policy operates on lists of these, never on a harness message array.
    `reasoning` is the model's thinking text; it is counted toward the input budget
    (reasoning-aware estimation) but is never itself evicted -- only tool results are.
    """

    role: str
    text: str = ""
    reasoning: str = ""
    agent: str = ""
    session: str = ""
    tool_results: list[ToolResult] = field(default_factory=list)
