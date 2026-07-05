"""Inventory & shop routes (Phase 39b) — SPEC §12.2.

Endpoints (all require Authorization: Bearer <token>; the session
lookup enforces ownership, so cross-user access 404s):

- GET  /api/sessions/{sid}/inventory          → view grouped by category
- POST /api/sessions/{sid}/inventory/equip    → {item_id, slot} → swap + AC diff
- POST /api/sessions/{sid}/inventory/unequip  → {slot} → clear slot
- POST /api/sessions/{sid}/inventory/drop     → {item_id, quantity?}
- POST /api/sessions/{sid}/inventory/attune   → {item_id} (PHB p. 138 cap 3)
- POST /api/sessions/{sid}/inventory/unattune → {item_id}
- POST /api/sessions/{sid}/inventory/buy      → {vendor_id, item_id, quantity?}
- POST /api/sessions/{sid}/inventory/sell     → {item_id, quantity?}
- GET  /api/sessions/{sid}/shop/{vendor_id}   → vendor catalog with prices

All mutations run through ``engine/inventory.py`` (no LLM). Engine
errors map to: 402 (insufficient gold), 404 (vendor not found), 422
(everything else — bad slot, item missing, attunement cap, NPC not a
vendor). Mutations target the player character by default; an optional
``character_id`` lets the player manage companion gear too (same
owner, same session — no extra authz needed).
"""
from __future__ import annotations

from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from auto_dm.engine.inventory import (
    EQUIP_SLOTS,
    InventoryResult,
    attune_item,
    buy_item,
    equip_item,
    remove_item,
    resolve_catalog_item,
    sell_item,
    unattune_item,
    unequip_item,
)
from auto_dm.state.models import Character, GameState, NPC
from auto_dm.web.auth import current_user
from auto_dm.web.models import User
from auto_dm.web.routes_game import get_session_manager
from auto_dm.web.sessions import SessionManager

router = APIRouter(prefix="/api", tags=["inventory"])


# ============================================================================
# Schemas
# ============================================================================


class EquipRequest(BaseModel):
    item_id: str = Field(..., min_length=1, max_length=120)
    slot: str = Field(..., min_length=1, max_length=24)
    character_id: Optional[str] = Field(default=None, max_length=64)


class UnequipRequest(BaseModel):
    slot: str = Field(..., min_length=1, max_length=24)
    character_id: Optional[str] = Field(default=None, max_length=64)


class ItemQuantityRequest(BaseModel):
    item_id: str = Field(..., min_length=1, max_length=120)
    quantity: int = Field(default=1, ge=1, le=999)
    character_id: Optional[str] = Field(default=None, max_length=64)


class AttuneRequest(BaseModel):
    item_id: str = Field(..., min_length=1, max_length=120)
    character_id: Optional[str] = Field(default=None, max_length=64)


class BuyRequest(BaseModel):
    vendor_id: str = Field(..., min_length=1, max_length=64)
    item_id: str = Field(..., min_length=1, max_length=120)
    quantity: int = Field(default=1, ge=1, le=999)
    character_id: Optional[str] = Field(default=None, max_length=64)


# ============================================================================
# Helpers
# ============================================================================


async def _load_session(sm: SessionManager, user: User, session_id: str):
    sess = await sm.get(user.id, session_id)
    if sess is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found or expired",
        )
    return sess


def _resolve_character(state: GameState, character_id: Optional[str]) -> Character:
    """Player character by default; any party member when id is given."""
    if character_id:
        target = next((c for c in state.party if c.id == character_id), None)
        if target is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Character not in party: {character_id}",
            )
        return target
    player = next(
        (c for c in state.party if c.id == state.player_character_id), None,
    )
    if player is None:
        player = next((c for c in state.party if c.is_player), None)
    if player is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No player character in this session",
        )
    return player


def _find_vendor(state: GameState, vendor_id: str) -> NPC:
    vendor = next((n for n in state.npcs if n.id == vendor_id), None)
    if vendor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"NPC not found: {vendor_id}",
        )
    return vendor


def _raise_for_errors(result: InventoryResult) -> None:
    """Map engine errors to HTTP: 402 for gold, 422 otherwise."""
    if result.ok:
        return
    detail = "; ".join(result.errors) or "operação inválida"
    if any("ouro insuficiente" in e for e in result.errors):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=detail,
        )
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail,
    )


def _result_payload(
    session_id: str, character: Character, result: InventoryResult,
) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "result": {
            "ok": result.ok,
            "warnings": result.warnings,
            "ac_before": result.ac_before,
            "ac_after": result.ac_after,
            "ac_delta": result.ac_delta,
            "gold_gp": result.gold_gp,
        },
        "character": character.model_dump(mode="json"),
    }


# ============================================================================
# Read endpoints
# ============================================================================


@router.get("/sessions/{session_id}/inventory")
async def get_inventory(
    session_id: str,
    user: Annotated[User, Depends(current_user)],
    sm: Annotated[SessionManager, Depends(get_session_manager)],
    character_id: Optional[str] = None,
) -> dict[str, Any]:
    """Inventory view grouped by item category, plus gold/attunement."""
    sess = await _load_session(sm, user, session_id)
    character = _resolve_character(sess.state, character_id)
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in character.inventory:
        groups.setdefault(item.type.value, []).append(item.model_dump(mode="json"))
    return {
        "session_id": session_id,
        "character_id": character.id,
        "gold_gp": character.gold_gp,
        "attuned_items": character.attuned_items,
        "equipped": character.equipped.model_dump(mode="json"),
        "slots": list(EQUIP_SLOTS),
        "groups": groups,
    }


@router.get("/sessions/{session_id}/shop/{vendor_id}")
async def get_shop(
    session_id: str,
    vendor_id: str,
    user: Annotated[User, Depends(current_user)],
    sm: Annotated[SessionManager, Depends(get_session_manager)],
) -> dict[str, Any]:
    """A vendor NPC's catalog with resolved item details and prices."""
    sess = await _load_session(sm, user, session_id)
    vendor = _find_vendor(sess.state, vendor_id)
    if not vendor.vendor:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{vendor.name} não é um vendedor",
        )
    player = _resolve_character(sess.state, None)
    stock = []
    for entry in vendor.shop_inventory:
        item = resolve_catalog_item(entry.item_id)
        stock.append({
            "item_id": entry.item_id,
            "price_gp": entry.price_gp,
            "restock_daily": entry.restock_daily,
            "item": item.model_dump(mode="json") if item is not None else None,
        })
    return {
        "session_id": session_id,
        "vendor_id": vendor.id,
        "vendor_name": vendor.name,
        "gold_gp": player.gold_gp,
        "stock": stock,
    }


# ============================================================================
# Mutations
# ============================================================================


@router.post("/sessions/{session_id}/inventory/equip")
async def post_equip(
    session_id: str,
    body: EquipRequest,
    user: Annotated[User, Depends(current_user)],
    sm: Annotated[SessionManager, Depends(get_session_manager)],
) -> dict[str, Any]:
    sess = await _load_session(sm, user, session_id)
    character = _resolve_character(sess.state, body.character_id)
    result = equip_item(character, body.slot, body.item_id)
    _raise_for_errors(result)
    await sm.save(sess)
    return _result_payload(session_id, character, result)


@router.post("/sessions/{session_id}/inventory/unequip")
async def post_unequip(
    session_id: str,
    body: UnequipRequest,
    user: Annotated[User, Depends(current_user)],
    sm: Annotated[SessionManager, Depends(get_session_manager)],
) -> dict[str, Any]:
    sess = await _load_session(sm, user, session_id)
    character = _resolve_character(sess.state, body.character_id)
    result = unequip_item(character, body.slot)
    _raise_for_errors(result)
    await sm.save(sess)
    return _result_payload(session_id, character, result)


@router.post("/sessions/{session_id}/inventory/drop")
async def post_drop(
    session_id: str,
    body: ItemQuantityRequest,
    user: Annotated[User, Depends(current_user)],
    sm: Annotated[SessionManager, Depends(get_session_manager)],
) -> dict[str, Any]:
    sess = await _load_session(sm, user, session_id)
    character = _resolve_character(sess.state, body.character_id)
    result = remove_item(character, body.item_id, body.quantity)
    _raise_for_errors(result)
    await sm.save(sess)
    return _result_payload(session_id, character, result)


@router.post("/sessions/{session_id}/inventory/attune")
async def post_attune(
    session_id: str,
    body: AttuneRequest,
    user: Annotated[User, Depends(current_user)],
    sm: Annotated[SessionManager, Depends(get_session_manager)],
) -> dict[str, Any]:
    sess = await _load_session(sm, user, session_id)
    character = _resolve_character(sess.state, body.character_id)
    result = attune_item(character, body.item_id)
    _raise_for_errors(result)
    await sm.save(sess)
    return _result_payload(session_id, character, result)


@router.post("/sessions/{session_id}/inventory/unattune")
async def post_unattune(
    session_id: str,
    body: AttuneRequest,
    user: Annotated[User, Depends(current_user)],
    sm: Annotated[SessionManager, Depends(get_session_manager)],
) -> dict[str, Any]:
    sess = await _load_session(sm, user, session_id)
    character = _resolve_character(sess.state, body.character_id)
    result = unattune_item(character, body.item_id)
    _raise_for_errors(result)
    await sm.save(sess)
    return _result_payload(session_id, character, result)


@router.post("/sessions/{session_id}/inventory/buy")
async def post_buy(
    session_id: str,
    body: BuyRequest,
    user: Annotated[User, Depends(current_user)],
    sm: Annotated[SessionManager, Depends(get_session_manager)],
) -> dict[str, Any]:
    sess = await _load_session(sm, user, session_id)
    character = _resolve_character(sess.state, body.character_id)
    vendor = _find_vendor(sess.state, body.vendor_id)
    result = buy_item(character, vendor, body.item_id, body.quantity)
    _raise_for_errors(result)
    await sm.save(sess)
    return _result_payload(session_id, character, result)


@router.post("/sessions/{session_id}/inventory/sell")
async def post_sell(
    session_id: str,
    body: ItemQuantityRequest,
    user: Annotated[User, Depends(current_user)],
    sm: Annotated[SessionManager, Depends(get_session_manager)],
) -> dict[str, Any]:
    sess = await _load_session(sm, user, session_id)
    character = _resolve_character(sess.state, body.character_id)
    result = sell_item(character, body.item_id, body.quantity)
    _raise_for_errors(result)
    await sm.save(sess)
    return _result_payload(session_id, character, result)
