"""RBAC для Pass Docs AI API.

Роли (от минимальной к максимальной):
  viewer   — только чтение health, summary, queue, analysis
  operator — viewer + запуск анализа
  reviewer — operator + подтверждение/отклонение результатов AI
  admin    — всё + управление токенами и просмотр audit log

operator_id берётся из поля description токена (доверенная сторона),
а НЕ из заголовка X-Operator-Id (клиент-контролируемое — нельзя доверять).
Клиент не может подделать actor_id в audit log.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, Header, HTTPException, Request, status

Role = Literal["viewer", "operator", "reviewer", "admin"]

_ROLE_WEIGHT: dict[Role, int] = {
    "viewer": 10,
    "operator": 20,
    "reviewer": 30,
    "admin": 40,
}

_HEADER_API_KEY = "x-api-key"


@dataclass(frozen=True)
class TokenEntry:
    token_hash: str  # SHA-256 hex of the raw token
    role: Role
    # description идентифицирует владельца токена и используется как operator_id в audit log.
    # Пример: "alice", "ops-bot", "reviewer-1".
    description: str = ""


@dataclass
class TokenRegistry:
    """Реестр токенов из переменных окружения или файла JSON.

    Формат файла (AI_TOKEN_FILE):
        [{"token": "raw-value", "role": "reviewer", "description": "alice"}]

    Переменные окружения (при отсутствии файла):
        AI_TOKEN_VIEWER, AI_TOKEN_OPERATOR, AI_TOKEN_REVIEWER, AI_TOKEN_ADMIN
    """

    _entries: list[TokenEntry] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> TokenRegistry:
        registry = cls()
        token_file = os.getenv("AI_TOKEN_FILE", "").strip()
        if token_file and Path(token_file).is_file():
            try:
                raw = json.loads(Path(token_file).read_text(encoding="utf-8"))
                for item in raw:
                    registry._entries.append(
                        TokenEntry(
                            token_hash=_sha256(item["token"]),
                            role=item["role"],
                            description=item.get("description", ""),
                        )
                    )
                return registry
            except Exception:
                pass
        for role in ("viewer", "operator", "reviewer", "admin"):
            value = os.getenv(f"AI_TOKEN_{role.upper()}", "").strip()
            if value:
                registry._entries.append(
                    TokenEntry(
                        token_hash=_sha256(value),
                        role=role,  # type: ignore[arg-type]
                        description=f"env:{role}",
                    )
                )
        return registry

    def resolve(self, raw_token: str) -> TokenEntry | None:
        h = _sha256(raw_token)
        for entry in self._entries:
            if hmac.compare_digest(entry.token_hash, h):
                return entry
        return None

    def is_empty(self) -> bool:
        return len(self._entries) == 0


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AuthContext:
    # Идентификатор оператора из description токена — доверенный, клиент не может изменить.
    operator_id: str
    role: Role
    ip: str


def require_role(minimum_role: Role):
    """FastAPI dependency: проверяет X-Api-Key и возвращает AuthContext.

    operator_id берётся из TokenEntry.description — клиент не может его подделать.
    """

    def _dependency(
        request: Request,
        x_api_key: Annotated[str | None, Header()] = None,
    ) -> AuthContext:
        registry: TokenRegistry = request.app.state.token_registry

        if registry.is_empty():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Токены не настроены. Задайте AI_TOKEN_* в окружении.",
            )

        if not x_api_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Требуется X-Api-Key",
                headers={"WWW-Authenticate": "ApiKey"},
            )

        entry = registry.resolve(x_api_key)
        if entry is None:
            _rate_limit_sleep()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Неверный X-Api-Key",
                headers={"WWW-Authenticate": "ApiKey"},
            )

        if _ROLE_WEIGHT[entry.role] < _ROLE_WEIGHT[minimum_role]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(f"Требуется роль {minimum_role}, у вас {entry.role}"),
            )

        ip = (
            request.headers.get("x-real-ip")
            or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or (request.client.host if request.client else "unknown")
        )
        # operator_id из description токена — это доверенная идентичность владельца ключа.
        operator_id = entry.description or f"token:{entry.token_hash[:12]}"
        return AuthContext(operator_id=operator_id, role=entry.role, ip=ip)

    return _dependency


def _rate_limit_sleep() -> None:
    """Минимальная задержка против brute-force."""
    time.sleep(0.1)


# Готовые dependency-объекты
require_viewer = Depends(require_role("viewer"))
require_operator = Depends(require_role("operator"))
require_reviewer = Depends(require_role("reviewer"))
require_admin = Depends(require_role("admin"))
