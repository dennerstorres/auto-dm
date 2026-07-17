"""Usage aggregation + cost computation for the admin panel.

All date/window SQL lives here (dialect-aware) so the route handlers and
limits module never inline date logic. Two dialects are supported: the
production Postgres (``date_trunc``, ``TIMESTAMPTZ``) and the in-memory
SQLite used by tests (``strftime``). The dialect is detected from the
session's bind.

The "today" / "this month" windows are computed in Python (UTC midnight
boundaries) and passed as parameters, which keeps the common queries
fully portable; only the per-day grouping and the distinct-minute count
need SQL date functions.
"""
from __future__ import annotations

from datetime import datetime, time, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from auto_dm.llm.pricing import get_token_price
from auto_dm.llm.usage import UsageReport
from auto_dm.web.config import Settings
from auto_dm.web.models import UsageEvent


# ============================================================================
# Cost
# ============================================================================


def compute_cost(report: UsageReport, settings: Settings) -> Decimal:
    """USD cost using the model catalog, with a configurable legacy fallback."""
    price = get_token_price(report.provider, report.model)
    if price is not None:
        input_rate = Decimal(str(price.input_per_million_usd))
        output_rate = Decimal(str(price.output_per_million_usd))
        divisor = Decimal(1_000_000)
    else:
        input_rate = Decimal(str(settings.token_price_per_1k_input_usd))
        output_rate = Decimal(str(settings.token_price_per_1k_output_usd))
        divisor = Decimal(1000)
    in_cost = Decimal(report.prompt_tokens) * input_rate / divisor
    out_cost = Decimal(report.completion_tokens) * output_rate / divisor
    return (in_cost + out_cost).quantize(Decimal("0.00000001"))


# ============================================================================
# Windows (UTC)
# ============================================================================


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def start_of_today_utc(now: Optional[datetime] = None) -> datetime:
    """Midnight at the start of today (UTC)."""
    now = now or utc_now()
    return datetime.combine(now.date(), time.min, tzinfo=timezone.utc)


def start_of_month_utc(now: Optional[datetime] = None) -> datetime:
    """First instant of the current month (UTC)."""
    now = now or utc_now()
    return datetime(now.year, now.month, 1, tzinfo=timezone.utc)


# ============================================================================
# Dialect helper
# ============================================================================


def _is_sqlite(session: AsyncSession) -> bool:
    bind = session.bind
    dialect = getattr(bind, "dialect", None)
    return getattr(dialect, "name", "") == "sqlite"


# ============================================================================
# Aggregations
# ============================================================================


async def usage_today(session: AsyncSession, user_id: int) -> int:
    """Total tokens consumed by ``user_id`` since UTC midnight."""
    since = start_of_today_utc()
    result = await session.execute(
        select(func.coalesce(func.sum(UsageEvent.total_tokens), 0)).where(
            UsageEvent.user_id == user_id, UsageEvent.created_at >= since
        )
    )
    return int(result.scalar_one() or 0)


async def minutes_today(session: AsyncSession, user_id: int) -> int:
    """Distinct active minutes since UTC midnight (activity proxy)."""
    since = start_of_today_utc()
    if _is_sqlite(session):
        minute = func.strftime("%Y-%m-%d %H:%M", UsageEvent.created_at)
    else:
        minute = func.date_trunc("minute", UsageEvent.created_at)
    result = await session.execute(
        select(func.count(func.distinct(minute))).where(
            UsageEvent.user_id == user_id, UsageEvent.created_at >= since
        )
    )
    return int(result.scalar_one() or 0)


async def cost_in_range(
    session: AsyncSession, user_id: int, start: datetime, end: Optional[datetime] = None
) -> Decimal:
    """Sum of ``cost_usd`` for a user in ``[start, end)``."""
    stmt = select(func.coalesce(func.sum(UsageEvent.cost_usd), 0)).where(
        UsageEvent.user_id == user_id, UsageEvent.created_at >= start
    )
    if end is not None:
        stmt = stmt.where(UsageEvent.created_at < end)
    result = await session.execute(stmt)
    return Decimal(str(result.scalar_one() or 0)).quantize(Decimal("0.00000001"))


async def cost_this_month(session: AsyncSession, user_id: int) -> Decimal:
    return await cost_in_range(session, user_id, start_of_month_utc())


async def usage_by_day(
    session: AsyncSession, user_id: int, start: datetime, end: Optional[datetime] = None
) -> list[dict]:
    """Per-day token + cost series for a user in ``[start, end)``."""
    if _is_sqlite(session):
        day = func.strftime("%Y-%m-%d", UsageEvent.created_at)
    else:
        day = func.date_trunc("day", UsageEvent.created_at)
    stmt = (
        select(
            day.label("d"),
            func.coalesce(func.sum(UsageEvent.total_tokens), 0).label("tokens"),
            func.coalesce(func.sum(UsageEvent.cost_usd), 0).label("cost"),
        )
        .where(UsageEvent.user_id == user_id, UsageEvent.created_at >= start)
        .group_by(day)
        .order_by(day)
    )
    if end is not None:
        stmt = stmt.where(UsageEvent.created_at < end)
    result = await session.execute(stmt)
    rows = []
    for d, tokens, cost in result.all():
        rows.append(
            {
                "date": str(d),
                "tokens": int(tokens or 0),
                "cost": float(Decimal(str(cost or 0))),
            }
        )
    return rows


async def persist_usage_events(
    session: AsyncSession,
    *,
    user_id: int,
    endpoint: str,
    reports: list[UsageReport],
    settings: Settings,
    session_id: Optional[str] = None,
    kind: str = "player",
    credential_source: str = "legacy",
) -> None:
    """Insert one :class:`UsageEvent` per report and commit.

    Best-effort: a failure here must not break the game turn, so callers
    wrap this in try/except. ``kind`` defaults to ``"player"``; the DM
    follow-up narration and companion turns can override it.

    ``credential_source`` (Phase 51d) tags which key paid for the call:
    ``"legacy"`` = the deploy's global ``AUTO_DM_API_KEY``;
    ``"byok"`` = the user's own encrypted key. Routed from the resolved LLM
    context so admin cost reports can separate BYOK diagnostic usage from
    calls paid by the deploy's global key.
    """
    if not reports:
        return
    for report in reports:
        session.add(
            UsageEvent(
                user_id=user_id,
                session_id=session_id,
                endpoint=endpoint,
                kind=kind,
                provider=report.provider,
                model=report.model,
                source=report.source,
                credential_source=credential_source,
                prompt_tokens=report.prompt_tokens,
                completion_tokens=report.completion_tokens,
                total_tokens=report.total_tokens,
                cost_usd=compute_cost(report, settings),
            )
        )
    await session.commit()
