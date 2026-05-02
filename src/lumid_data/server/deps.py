"""FastAPI dependency wiring."""

from typing import AsyncIterator

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from .state import AppState


def get_state(request: Request) -> AppState:
    state: AppState = request.app.state.app_state
    return state


async def get_session(state: AppState = Depends(get_state)) -> AsyncIterator[AsyncSession]:
    async with state.sessionmaker() as session:
        yield session
