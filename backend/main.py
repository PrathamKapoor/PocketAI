"""PocketAI backend entry point.

Run from anywhere (paths are resolved from this file's location):

    python backend/main.py

Serves on 127.0.0.1:8090 (config/runtime.json) and talks to the llama.cpp
server on 127.0.0.1:8091 (config/model.json). Localhost only, no internet.
"""

from __future__ import annotations

import asyncio
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uvicorn
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from backend import __version__
from backend.config.loader import PocketAIConfig, load_config
from backend.schemas import ChatRequest, ChatResponse, SearchRequest, SearchResponse
from backend.storage.db import Storage
from backend.supervisor.router import Supervisor, SupervisorError
from backend.image.errors import ImageError
from backend.tools.llama_client import LlamaClient, LlamaError
from backend.tools.sysinfo import get_cpu, get_memory
from rag.pipeline import RagError, RagPipeline

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
FRONTEND_DIR = ROOT / "frontend"
SAVED_CHATS_DIR = ROOT / "saved_chats"


def _save_conversation_to_disk(
    cfg, conversation: dict, messages: list[dict]
) -> Path:
    """Save a conversation as markdown + metadata JSON.

    Creates a unique folder under saved_chats/ with timestamp-based name.
    Returns the path to the created folder.
    """
    import json
    from datetime import datetime

    # Create unique folder name with timestamp
    now = datetime.now()
    folder_name = now.strftime("%Y-%m-%d_%H-%M-%S") + "_chat"
    save_dir = SAVED_CHATS_DIR / folder_name
    save_dir.mkdir(parents=True, exist_ok=True)

    # Generate markdown content
    md_lines = []
    md_lines.append(f"# {conversation.get('title', 'Untitled Conversation')}")
    md_lines.append("")
    md_lines.append(f"**Date:** {now.strftime('%Y-%m-%d %H:%M:%S')}")
    md_lines.append(f"**Conversation ID:** {conversation.get('id', 'unknown')}")
    md_lines.append("")
    md_lines.append("---")
    md_lines.append("")

    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        skill = msg.get("skill", "")

        if role == "user":
            md_lines.append("## User")
            md_lines.append("")
            md_lines.append(content)
            md_lines.append("")
        elif role == "assistant":
            md_lines.append("## Assistant")
            if skill:
                md_lines.append(f"*Mode: {skill}*")
            md_lines.append("")
            md_lines.append(content)
            md_lines.append("")

    md_content = "\n".join(md_lines)

    # Write markdown file
    md_file = save_dir / "conversation.md"
    md_file.write_text(md_content, encoding="utf-8")

    # Write metadata JSON
    metadata = {
        "conversation_id": conversation.get("id"),
        "title": conversation.get("title", "Untitled Conversation"),
        "created_at": conversation.get("created_at"),
        "updated_at": conversation.get("updated_at"),
        "saved_at": now.isoformat(),
        "message_count": len(messages),
        "messages": messages,
    }
    metadata_file = save_dir / "metadata.json"
    metadata_file.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return save_dir


def create_app(
    config: PocketAIConfig | None = None,
    transport=None,
) -> FastAPI:
    """Build the app. `transport` lets tests inject a mock llama-server."""
    cfg = config or load_config(ROOT)
    storage = Storage(cfg.database_path)
    client = LlamaClient(
        host=cfg.model_server.host,
        port=cfg.model_server.port,
        api_key=cfg.model_server.api_key,
        alias=cfg.model_server.alias,
        timeout_seconds=cfg.model_server.timeout_seconds,
        max_parallel=1,  # server runs -np 1: queue, never parallelize
        transport=transport,
    )
    rag = RagPipeline(
        uploads_dir=cfg.rag_uploads_dir,
        db_path=cfg.rag_db_path,
        max_upload_mb=cfg.rag.max_upload_mb,
        chunk_chars=cfg.rag.chunk_chars,
        chunk_overlap=cfg.rag.chunk_overlap,
        chat_top_k=cfg.rag.chat_top_k,
        chat_context_max_chars=cfg.rag.chat_context_max_chars,
        allowed_extensions=set(cfg.rag.allowed_extensions),
    )
    supervisor = Supervisor(cfg, storage, client, rag=rag)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        await client.aclose()
        rag.close()

    app = FastAPI(
        title="PocketAI Backend",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.config = cfg
    app.state.storage = storage
    app.state.client = client
    app.state.rag = rag
    app.state.supervisor = supervisor

    @app.get("/health")
    async def health() -> dict:
        probe = await client.health(timeout=2.0)
        if probe is not None:
            runtime_status = "running"
            model_status = (
                "ready" if probe.get("status") == "ok" else str(probe.get("status"))
            )
        else:
            runtime_status, model_status = "stopped", "unavailable"
        return {
            "status": "ok",
            "backend": {"status": "ok", "version": __version__},
            "model": {
                "status": model_status,
                "alias": cfg.model_server.alias,
                # Display name for the UI (config/model.json); falls back
                # to the server alias when unset.
                "name": cfg.model_info.get("name") or cfg.model_server.alias,
            },
            "runtime": {
                "status": runtime_status,
                "host": cfg.model_server.host,
                "port": cfg.model_server.port,
            },
        }

    @app.get("/system")
    async def system() -> dict:
        memory = get_memory()
        cpu = get_cpu()
        n_ctx, n_parallel = client.server_caps(await client.props(timeout=2.0))
        return {
            "ram": {
                "total_mb": memory.total_mb,
                "available_mb": memory.available_mb,
            },
            "cpu": {
                "logical_cores": cpu.logical_cores,
                "physical_cores": cpu.physical_cores,
                "arch": cpu.arch,
            },
            "profile": {
                "name": supervisor.profile_name,
                **supervisor.profile.model_dump(),
            },
            "model_server": {
                "context": n_ctx,
                "parallel_slots": n_parallel,
            },
            "python": sys.version.split()[0],
        }

    @app.get("/skills")
    async def skills() -> list[dict]:
        # Internal architecture is not user-facing: the skill registry is
        # only exposed when developer mode is enabled in config/runtime.json.
        if not cfg.developer_mode:
            return JSONResponse(status_code=404, content={"error": "not found"})
        return [
            {
                "id": skill.id,
                "name": skill.name,
                "description": skill.description,
                "category": skill.category,
                "priority": skill.priority,
                "modes": list(skill.modes),
            }
            for skill in sorted(supervisor.registry.values(), key=lambda s: s.id)
        ]

    @app.post("/chat", response_model=ChatResponse)
    async def chat(req: ChatRequest) -> ChatResponse:
        return await supervisor.handle(req)

    @app.post("/chat/stream")
    async def chat_stream(req: ChatRequest) -> StreamingResponse:
        """Token streaming over Server-Sent Events.

        Events: meta (conversation id, style), delta (visible content
        only — thinking tokens are never emitted), done (timings/warning),
        error. Preparation errors are raised before the response starts so
        they surface as normal JSON error responses.
        """
        prepared = await supervisor.prepare(req)

        async def generate():
            async for event in supervisor.stream_events(prepared):
                payload = json.dumps(event["data"], ensure_ascii=False)
                yield f"event: {event['type']}\ndata: {payload}\n\n"

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ---------------- conversation history ----------------

    @app.get("/conversations")
    async def conversations() -> list[dict]:
        return await asyncio.to_thread(storage.list_conversations, 100)

    @app.get("/conversations/{conversation_id}")
    async def conversation_detail(conversation_id: int) -> dict:
        conversation = await asyncio.to_thread(
            storage.get_conversation, conversation_id
        )
        if conversation is None:
            return JSONResponse(
                status_code=404,
                content={"error": f"conversation {conversation_id} not found"},
            )
        messages = await asyncio.to_thread(
            storage.get_messages, conversation_id
        )
        return {"conversation": conversation, "messages": messages}

    @app.delete("/conversations/{conversation_id}")
    async def delete_conversation(conversation_id: int) -> dict:
        deleted = await asyncio.to_thread(
            storage.delete_conversation, conversation_id
        )
        if not deleted:
            return JSONResponse(
                status_code=404,
                content={"error": f"conversation {conversation_id} not found"},
            )
        return {"deleted": conversation_id}

    @app.post("/conversations/{conversation_id}/save")
    async def save_conversation(conversation_id: int) -> dict:
        """Save a conversation to disk as markdown + metadata JSON."""
        conversation = await asyncio.to_thread(
            storage.get_conversation, conversation_id
        )
        if conversation is None:
            return JSONResponse(
                status_code=404,
                content={"error": f"conversation {conversation_id} not found"},
            )
        messages = await asyncio.to_thread(
            storage.get_messages, conversation_id
        )
        try:
            save_dir = await asyncio.to_thread(
                _save_conversation_to_disk,
                cfg,
                conversation,
                messages,
            )
            return {"saved": True, "directory": str(save_dir)}
        except Exception as exc:
            return JSONResponse(
                status_code=500,
                content={"error": f"Failed to save conversation: {exc}"},
            )

    # ---------------- RAG: documents + search ----------------

    @app.post("/documents/upload")
    async def upload_document(file: UploadFile = File(...)) -> dict:
        # Read cap+1 bytes so oversized uploads fail without unbounded memory.
        max_bytes = cfg.rag.max_upload_mb * 1024 * 1024 + 1
        data = await file.read(max_bytes)
        return await asyncio.to_thread(
            rag.index_upload, file.filename or "upload", data
        )

    @app.get("/documents")
    async def documents() -> list[dict]:
        return await asyncio.to_thread(rag.list_documents)

    @app.delete("/documents/{doc_id}")
    async def delete_document(doc_id: str) -> dict:
        await asyncio.to_thread(rag.delete_document, doc_id)
        return {"deleted": doc_id}

    @app.post("/search", response_model=SearchResponse)
    async def search(req: SearchRequest) -> SearchResponse:
        hits = await asyncio.to_thread(rag.search, req.query, req.top_k)
        return SearchResponse(results=hits)

    @app.exception_handler(SupervisorError)
    async def _supervisor_error_handler(_: FastAPI, exc: SupervisorError):
        return JSONResponse(status_code=exc.status_code, content={"error": str(exc)})

    @app.exception_handler(ImageError)
    async def _image_error_handler(_: FastAPI, exc: ImageError):
        return JSONResponse(status_code=exc.status_code, content={"error": str(exc)})

    @app.exception_handler(LlamaError)
    async def _llama_error_handler(_: FastAPI, exc: LlamaError):
        return JSONResponse(status_code=502, content={"error": str(exc)})

    @app.exception_handler(RagError)
    async def _rag_error_handler(_: FastAPI, exc: RagError):
        return JSONResponse(status_code=exc.status_code, content={"error": str(exc)})

    # Vanilla HTML/CSS/JS dashboard, served by the backend itself (no build
    # step, no CDN — everything works offline). Guarded so the API still
    # starts if the frontend folder is missing.
    if (FRONTEND_DIR / "index.html").is_file():
        app.mount(
            "/static", StaticFiles(directory=FRONTEND_DIR), name="static"
        )

        @app.get("/", include_in_schema=False)
        async def index() -> FileResponse:
            return FileResponse(FRONTEND_DIR / "index.html")

    return app


def main() -> None:
    cfg = load_config(ROOT)
    if cfg.require_loopback_bind and cfg.backend.host not in LOOPBACK_HOSTS:
        sys.exit(
            f"[PocketAI] refusing to bind non-loopback host {cfg.backend.host!r}"
            " (security.require_loopback_bind is enabled in config/runtime.json)"
        )
    app = create_app(cfg)
    uvicorn.run(
        app,
        host=cfg.backend.host,
        port=cfg.backend.port,
        log_level=cfg.backend.log_level,
    )


if __name__ == "__main__":
    main()
