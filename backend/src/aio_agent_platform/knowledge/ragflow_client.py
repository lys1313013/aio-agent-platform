"""RAGFlow /dify/retrieval HTTP client."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import structlog

logger = structlog.get_logger()


class RagflowError(Exception):
    """Raised when RAGFlow returns a non-2xx response or is unreachable."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


async def test_connection(
    base_url: str,
    api_key: str,
    dataset_id: str,
    timeout: float = 15.0,
) -> int:
    """Test RAGFlow connectivity by querying a single dataset.

    Unlike `retrieve`, this function propagates errors instead of swallowing them.
    Returns the number of records found.
    Raises RagflowError on any HTTP/network failure.
    """
    url = f"{base_url.rstrip('/')}/api/v1/dify/retrieval"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "knowledge_id": dataset_id,
        "query": "test",
        "retrieval_setting": {
            "top_k": 3,
            "score_threshold": 0.0,
        },
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
    except httpx.TimeoutException as e:
        raise RagflowError(f"连接超时 ({timeout}s)：{e}") from e
    except httpx.ConnectError as e:
        raise RagflowError(f"无法连接到 RAGFlow 服务 ({base_url})：{e}") from e
    except Exception as e:
        raise RagflowError(f"网络错误：{e}") from e

    if resp.status_code != 200:
        body = resp.text[:300]
        # Try to parse a structured error message
        try:
            data = resp.json()
            body = data.get("message", body)
        except Exception:
            pass
        raise RagflowError(
            f"RAGFlow 返回 HTTP {resp.status_code}：{body}",
            status_code=resp.status_code,
        )

    data = resp.json()
    records = data.get("records", [])
    return len(records)


async def retrieve(
    base_url: str,
    api_key: str,
    dataset_ids: list[str],
    query: str,
    top_k: int = 5,
    score_threshold: float = 0.3,
    timeout: float = 30.0,
) -> list[dict[str, Any]]:
    """Concurrently query RAGFlow /dify/retrieval for multiple dataset_ids, merge and rank results.

    Returns a list of records sorted by score (descending), capped at top_k.
    Each record contains: content, score, title, metadata, dataset_id.
    """
    if not dataset_ids:
        return []

    logger.info(
        "ragflow_retrieve_start",
        base_url=base_url,
        dataset_ids=dataset_ids,
        query=query,
        top_k=top_k,
        score_threshold=score_threshold,
        timeout=timeout,
    )

    async def _retrieve_one(dataset_id: str) -> list[dict[str, Any]]:
        url = f"{base_url.rstrip('/')}/api/v1/dify/retrieval"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "knowledge_id": dataset_id,
            "query": query,
            "retrieval_setting": {
                "top_k": top_k,
                "score_threshold": score_threshold,
            },
        }

        logger.debug(
            "ragflow_retrieve_request",
            dataset_id=dataset_id,
            url=url,
            query=query,
            top_k=top_k,
            score_threshold=score_threshold,
        )

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                records = data.get("records", [])
                total = data.get("total", {})
                for r in records:
                    r["dataset_id"] = dataset_id

                logger.info(
                    "ragflow_retrieve_response",
                    dataset_id=dataset_id,
                    status_code=resp.status_code,
                    record_count=len(records),
                    total=total,
                    scores=[r.get("score") for r in records],
                )

                return records
        except httpx.TimeoutException:
            logger.warning(
                "ragflow_retrieval_timeout",
                dataset_id=dataset_id,
                timeout=timeout,
                url=url,
            )
            return []
        except httpx.HTTPStatusError as e:
            logger.warning(
                "ragflow_retrieval_http_error",
                dataset_id=dataset_id,
                status_code=e.response.status_code,
                response_body=e.response.text[:500],
                url=url,
            )
            return []
        except Exception as e:
            logger.warning(
                "ragflow_retrieval_failed",
                dataset_id=dataset_id,
                error=str(e),
                error_type=type(e).__name__,
                url=url,
            )
            return []

    # 并发查询所有 dataset
    tasks = [_retrieve_one(did) for did in dataset_ids]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_records: list[dict[str, Any]] = []
    for r in results:
        if isinstance(r, list):
            all_records.extend(r)
        elif isinstance(r, Exception):
            logger.warning("ragflow_retrieve_exception", error=str(r))

    # 按 score 降序排列，取 top_k
    all_records.sort(key=lambda x: x.get("score", 0), reverse=True)
    final_records = all_records[:top_k]

    logger.info(
        "ragflow_retrieve_complete",
        total_records_fetched=len(all_records),
        records_returned=len(final_records),
        query=query,
    )

    return final_records
