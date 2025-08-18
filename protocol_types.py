from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, List, Union, TypedDict


JsonValue = Union[None, bool, int, float, str, List["JsonValue"], Dict[str, "JsonValue"]]


class RpcRequest(TypedDict, total=False):
    jsonrpc: str
    id: Union[str, int]
    method: str
    params: Dict[str, Any]


class RpcError(TypedDict):
    code: int
    message: str
    data: Optional[JsonValue]


class RpcResponse(TypedDict, total=False):
    jsonrpc: str
    id: Union[str, int, None]
    result: Optional[JsonValue]
    error: Optional[RpcError]


@dataclass
class PaneInfo:
    id: str
    workdir: str
    father: Optional[str] = None


@dataclass
class Message:
    id: int
    from_id: str
    text: str
    ts: float
