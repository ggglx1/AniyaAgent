from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, File, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .dependencies import require_auth, services
from .errors import install_error_handlers
from .schemas import MemoryActionRequest, MessageRequest, PermissionRequest, ProviderRequest, RedactRequest, TrackRequest, WeixinBindingRequest


def ok(**payload): return {"ok": True, **payload}


def create_api(application, bridge, auth_token: str = "") -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        runtime = application.web_runtime()
        runtime.registry.register(bridge)
        application.runtime.permissions.ask_user = bridge.ask_user
        # The separate scheduler process owns the lease; Web only observes its state.
        try: yield
        finally:
            bridge.close()

    app = FastAPI(title="AniyaAgent API", version="1.0.0", lifespan=lifespan)
    app.state.auth_token = auth_token
    app.state.services = {"application": application, "bridge": bridge, "memory": bridge.memory_admin, "llm": bridge.llm_control, "attachments": application.attachments, "mcp": application.mcp}
    origins = [value.strip() for value in os.getenv("ANIYAAGENT_CORS_ORIGINS", "http://localhost,http://127.0.0.1").split(",") if value.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False, allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["Authorization", "Content-Type", "X-AniyaAgent-Token"])
    install_error_handlers(app)
    auth = [Depends(require_auth)]

    system = APIRouter()
    @system.get("/health")
    async def health(data=Depends(services)):
        application=data["application"]
        return ok(api="ok", runtime="ready", scheduler=application.scheduler.health(), mcp=application.mcp.health())
    @system.get("/channels", dependencies=auth)
    async def channels(data=Depends(services)): return ok(channels=data["bridge"].channel_runtime.registry.list_channels())

    conversation = APIRouter(prefix="/conversation", dependencies=auth)
    @conversation.get("/state")
    async def state(data=Depends(services)):
        bridge=data["bridge"]
        return ok(modes={mode:bridge.resolve_track({"mode":mode}, create=True) for mode in ("assistant","qa","coding")})
    @conversation.get("/history")
    async def history(mode: str = "assistant", scope_id: str = "personal", track_id: str = "assistant:personal", limit: int = Query(50, ge=1, le=500), before_sequence: int | None = Query(None, ge=1), data=Depends(services)):
        memory = data["memory"]
        if memory is None: raise RuntimeError("Memory service unavailable")
        messages=memory.track_messages(mode=mode, scope_id=scope_id, track_id=track_id, limit=limit, before_sequence=before_sequence)
        return ok(track={"mode":mode,"scope_id":scope_id,"track_id":track_id}, messages=messages, has_more=len(messages) >= limit)
    @conversation.post("/track")
    async def track(payload: TrackRequest, data=Depends(services)): return ok(track=data["bridge"].resolve_track(payload.model_dump(), create=True, force_new=payload.force_new))
    @conversation.get("/search")
    async def search_conversation(query: str, mode: str = "assistant", limit: int = Query(50, ge=1, le=200), data=Depends(services)): return ok(messages=data["memory"].search_messages(query, mode, limit))

    message_router = APIRouter(dependencies=auth)
    @message_router.post("/message")
    async def message(payload: MessageRequest, data=Depends(services)):
        result = data["bridge"].submit_message(payload.model_dump())
        if not result.get("ok"): from fastapi import HTTPException; raise HTTPException(status_code=400, detail={"error":"message_rejected","message":result.get("error", "Message rejected.")})
        return result
    @message_router.post("/permission")
    async def permission(payload: PermissionRequest, data=Depends(services)):
        if not data["bridge"].answer_permission(payload.request_id, payload.allow): from fastapi import HTTPException; raise HTTPException(status_code=404, detail={"error":"permission_request_not_found","message":"Permission request is unavailable or expired."})
        return ok(accepted=True)

    attachments = APIRouter(prefix="/attachments", dependencies=auth)
    @attachments.post("")
    async def upload_attachment(file: UploadFile = File(...), data=Depends(services)):
        raw = await file.read(); return ok(attachment=data["attachments"].upload(file.filename or "upload", raw, file.content_type or ""))
    @attachments.get("/{attachment_id}")
    async def attachment(attachment_id: str, data=Depends(services)): return ok(attachment=data["attachments"].get(attachment_id))
    @attachments.delete("/{attachment_id}")
    async def delete_attachment(attachment_id: str, data=Depends(services)): data["attachments"].delete(attachment_id); return ok()

    providers = APIRouter(prefix="/llm", dependencies=auth)
    @providers.get("/providers")
    async def list_providers(data=Depends(services)):
        if data["llm"] is None: raise RuntimeError("LLM provider control unavailable")
        return ok(**data["llm"].list_providers())
    @providers.post("/provider")
    async def select_provider(payload: ProviderRequest, data=Depends(services)):
        if data["llm"] is None: raise RuntimeError("LLM provider control unavailable")
        return ok(**data["llm"].select_provider(payload.provider))

    memory = APIRouter(prefix="/memory", dependencies=auth)
    @memory.get("/messages")
    async def memory_messages(date: str = "", limit: int = Query(100, ge=1, le=500), data=Depends(services)): return ok(messages=data["memory"].factual_messages(date, limit))
    @memory.get("/daily")
    async def memory_daily(date: str = "", data=Depends(services)): return ok(daily=data["memory"].daily_memory(date), days=data["memory"].daily_memories())
    @memory.post("/daily/{local_date}/rebuild")
    async def rebuild_daily(local_date: str, data=Depends(services)): return ok(daily=data["memory"].rebuild_daily_memory(local_date))
    @memory.get("/long-term")
    async def memory_long_term(status: str = "", limit: int = Query(100, ge=1, le=500), data=Depends(services)): return ok(memories=data["memory"].long_term_memories(status, limit))
    @memory.get("/export")
    async def memory_export(data=Depends(services)): return ok(export=data["memory"].retention.export())
    @memory.post("/redact")
    async def memory_redact(payload: RedactRequest, data=Depends(services)): return ok(result=data["memory"].retention.redact(payload.message_id))
    @memory.post("/long-term/action")
    async def memory_action(payload: MemoryActionRequest, data=Depends(services)):
        manager = data["memory"].personal_memory
        if payload.action == "confirm": result = manager.confirm(payload.memory_id)
        elif payload.action == "correct": result = manager.supersede(payload.memory_id, payload.content)
        elif payload.action == "archive": result = manager.archive(payload.memory_id)
        else: result = manager.forget(payload.memory_id)
        return ok(memory=result.to_dict())

    plans = APIRouter(prefix="/plans", dependencies=auth)
    @plans.get("")
    async def list_plans(limit: int = Query(100, ge=1, le=500), data=Depends(services)): return ok(**data["memory"].plans(limit))
    @plans.post("/action")
    async def plan_action(payload: dict, data=Depends(services)): return ok(**data["memory"].plan_action(payload))

    notifications = APIRouter(dependencies=auth)
    @notifications.get("/notifications")
    async def list_notifications(limit: int = Query(100, ge=1, le=500), data=Depends(services)): return ok(notifications=data["memory"].notification_status(limit))

    weixin = APIRouter(prefix="/weixin", dependencies=auth)
    @weixin.get("/binding")
    async def binding(data=Depends(services)): return ok(binding=data["memory"].weixin_binding())
    @weixin.post("/binding/code")
    async def binding_code(data=Depends(services)): return ok(code=data["memory"].issue_weixin_binding_code())
    @weixin.post("/binding/confirm")
    async def binding_confirm(payload: WeixinBindingRequest, data=Depends(services)): return ok(confirmed=data["memory"].notifications.confirm_binding_code(payload.code, payload.recipient_id, payload.context_token))
    @weixin.post("/binding/invalidate")
    async def binding_invalidate(data=Depends(services)): return ok(invalidated=data["memory"].invalidate_weixin_binding())

    mcp = APIRouter(prefix="/mcp", dependencies=auth)
    @mcp.get("/servers")
    async def mcp_servers(data=Depends(services)): return ok(servers=data["mcp"].list_servers())
    @mcp.post("/servers/{server_id}/connect")
    async def mcp_connect(server_id: str, data=Depends(services)): return ok(**data["mcp"].connect(server_id))
    @mcp.post("/servers/{server_id}/disconnect")
    async def mcp_disconnect(server_id: str, data=Depends(services)): return ok(**data["mcp"].disconnect(server_id))
    @mcp.get("/capabilities")
    async def mcp_capabilities(mode: str = "assistant", data=Depends(services)): return ok(capabilities=data["mcp"].list_capabilities(mode))
    @mcp.get("/health")
    async def mcp_health(data=Depends(services)): return ok(**data["mcp"].health())

    stream = APIRouter(dependencies=auth)
    @stream.get("/stream")
    async def event_stream(request: Request, request_id: str, after_sequence: int = Query(0, ge=0), data=Depends(services)):
        header_value = request.headers.get("last-event-id", "").strip()
        try:
            header_sequence = max(0, int(header_value)) if header_value else 0
        except ValueError:
            header_sequence = 0
        iterator = data["bridge"].stream(request_id, max(after_sequence, header_sequence))
        def next_event():
            try: return True, next(iterator)
            except StopIteration: return False, None
        async def emit():
            while True:
                alive, event = await asyncio.to_thread(next_event)
                if not alive: break
                if event.get("type") == "ping":
                    yield ": ping\n\n"
                    continue
                event_id = event.get("event_id") or event.get("event_sequence")
                id_line = f"id: {event_id}\n" if event_id else ""
                yield f"{id_line}event: {event.get('type', 'event')}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
        return StreamingResponse(emit(), media_type="text/event-stream", headers={"Cache-Control":"no-cache", "X-Accel-Buffering":"no"})

    runs = APIRouter(prefix="/runs", dependencies=auth)
    @runs.get("/active")
    async def active_runs(conversation_id: str = "", data=Depends(services)): return ok(runs=data["bridge"].active_runs(conversation_id))
    @runs.get("/{run_id}")
    async def run_state(run_id: str, data=Depends(services)):
        state=data["bridge"].run_state(run_id)
        if state is None: from fastapi import HTTPException; raise HTTPException(status_code=404, detail={"error":"run_not_found","message":"Run not found."})
        return ok(run=state)
    @runs.post("/{run_id}/cancel")
    async def cancel_run(run_id: str, data=Depends(services)):
        if not data["bridge"].cancel_run(run_id): from fastapi import HTTPException; raise HTTPException(status_code=409, detail={"error":"run_not_cancellable","message":"Run is already terminal or unavailable."})
        return ok(cancelled=True)

    for router in (system, conversation, message_router, attachments, providers, memory, plans, notifications, weixin, mcp, stream, runs): app.include_router(router)
    return app
