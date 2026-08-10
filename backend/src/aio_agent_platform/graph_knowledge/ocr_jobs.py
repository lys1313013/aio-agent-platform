"""Background OCR jobs for scanned PDFs via MinerU (扫描版 PDF 后台解析任务).

Follows the same fire-and-forget pattern as extraction.py: the upload route
creates a placeholder document (status=parsing), then schedules the MinerU
parse + chunking on the running event loop with its own DB session.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import structlog
from sqlalchemy import func, select

from aio_agent_platform.db.connection import current_user_id, get_session_factory
from aio_agent_platform.db.models import GraphChunk, GraphDocument
from aio_agent_platform.graph_knowledge import mineru
from aio_agent_platform.graph_knowledge.chunking import chunk_text

logger = structlog.get_logger()


async def _run_ocr_job(doc_id: UUID, filename: str, data: bytes) -> None:
    try:
        factory = get_session_factory()
        async with factory() as db:
            doc = await db.get(GraphDocument, doc_id)
            if not doc:
                return
            # Background task writes as the document creator (RLS context).
            current_user_id.set(str(doc.created_by))
            await db.execute(
                select(func.set_config("app.current_user_id", str(doc.created_by), True))
            )
            try:
                text = await mineru.parse_document(filename, data)
                # Document may have been deleted while OCR was running.
                exists = (
                    await db.execute(
                        select(GraphDocument.id).where(GraphDocument.id == doc_id)
                    )
                ).scalar_one_or_none()
                if exists is None:
                    logger.info("graph_doc_ocr_aborted_doc_deleted", doc_id=str(doc_id))
                    return
                chunks = chunk_text(text)
                doc.content = text
                for seq, chunk_content in enumerate(chunks):
                    db.add(
                        GraphChunk(
                            document_id=doc.id,
                            knowledge_base_id=doc.knowledge_base_id,
                            seq=seq,
                            content=chunk_content,
                        )
                    )
                doc.status = "chunked" if chunks else "failed"
                doc.chunk_count = len(chunks)
                logger.info(
                    "graph_doc_ocr_done",
                    doc_id=str(doc_id),
                    chunks=len(chunks),
                    chars=len(text),
                )
            except mineru.MinerUParseError as e:
                logger.warning("graph_doc_ocr_failed", doc_id=str(doc_id), error=str(e))
                doc.status = "failed"
            await db.commit()
    except Exception:
        logger.exception("graph_doc_ocr_job_failed", doc_id=str(doc_id))
        try:
            factory = get_session_factory()
            async with factory() as db:
                doc = await db.get(GraphDocument, doc_id)
                if doc:
                    doc.status = "failed"
                    await db.commit()
        except Exception:
            logger.exception("graph_doc_ocr_mark_failed_failed", doc_id=str(doc_id))


_background_tasks: set[asyncio.Task] = set()


def start_ocr_job(doc_id: UUID, filename: str, data: bytes) -> None:
    """Fire-and-forget: schedule the OCR job on the running event loop."""
    task = asyncio.create_task(_run_ocr_job(doc_id, filename, data))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
