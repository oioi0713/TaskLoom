"""Deterministic worker runtime with observable parallel execution."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

from app.contracts.result import WorkerResult
from app.graph.state import WorkerInput, WorkflowEvent


@dataclass(frozen=True, slots=True)
class ExecutionRecord:
    """One worker start or finish observation."""

    task_id: str
    phase: str
    timestamp: float
    active_workers: int
    thread_id: int


class ExecutionTracker:
    """Thread-safe evidence that Send fan-out executed workers concurrently."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active_workers = 0
        self._peak_active_workers = 0
        self._records: list[ExecutionRecord] = []

    def worker_started(self, task_id: str) -> None:
        with self._lock:
            self._active_workers += 1
            self._peak_active_workers = max(
                self._peak_active_workers,
                self._active_workers,
            )
            self._records.append(
                ExecutionRecord(
                    task_id=task_id,
                    phase="started",
                    timestamp=time.perf_counter(),
                    active_workers=self._active_workers,
                    thread_id=threading.get_ident(),
                )
            )

    def worker_finished(self, task_id: str) -> None:
        with self._lock:
            self._records.append(
                ExecutionRecord(
                    task_id=task_id,
                    phase="finished",
                    timestamp=time.perf_counter(),
                    active_workers=self._active_workers,
                    thread_id=threading.get_ident(),
                )
            )
            self._active_workers -= 1

    @property
    def peak_active_workers(self) -> int:
        with self._lock:
            return self._peak_active_workers

    @property
    def records(self) -> tuple[ExecutionRecord, ...]:
        with self._lock:
            return tuple(self._records)

    def started_at(self, task_id: str) -> float:
        return next(
            record.timestamp
            for record in self.records
            if record.task_id == task_id and record.phase == "started"
        )

    def finished_at(self, task_id: str) -> float:
        return next(
            record.timestamp
            for record in self.records
            if record.task_id == task_id and record.phase == "finished"
        )


class MockWorkerRuntime:
    """Execute fixed task outputs without an LLM or external tool."""

    def __init__(
        self,
        *,
        delay_seconds: float = 0.05,
        tracker: ExecutionTracker | None = None,
    ) -> None:
        if delay_seconds < 0:
            raise ValueError("delay_seconds cannot be negative")
        self.delay_seconds = delay_seconds
        self.tracker = tracker or ExecutionTracker()

    def execute(self, worker_input: WorkerInput) -> dict:
        """Run one isolated mock task and return a reducer-compatible update."""

        task = worker_input["task"]
        spec = worker_input["spec"]
        dependency_results = worker_input["dependency_results"]
        started = time.perf_counter()
        self.tracker.worker_started(task.task_id)
        try:
            if self.delay_seconds:
                time.sleep(self.delay_seconds)
            output = self._build_output(task.task_id, dependency_results)
            elapsed_ms = max(0, int((time.perf_counter() - started) * 1000))
            result = WorkerResult(
                run_id=worker_input["run_id"],
                task_id=task.task_id,
                agent_id=spec.agent_id,
                status="completed",
                output_contract=task.output_contract,
                output=output,
                sources=[],
                execution_time_ms=elapsed_ms,
                token_usage=0,
                tool_calls=[],
            )
            return {
                "worker_results": [result],
                "events": [
                    WorkflowEvent(
                        event_type="worker.completed",
                        message=f"Mock worker completed {task.task_id}.",
                        data={
                            "task_id": task.task_id,
                            "dependency_count": len(dependency_results),
                        },
                    )
                ],
            }
        finally:
            self.tracker.worker_finished(task.task_id)

    @staticmethod
    def _build_output(
        task_id: str,
        dependency_results: list[WorkerResult],
    ) -> dict[str, Any]:
        if task_id == "T1":
            return {
                "summary": "Redis 适合共享、低延迟且带过期时间的短期记忆。",
                "findings": [
                    "Redis 支持 TTL，可控制临时记忆生命周期。",
                    "集中式服务便于多个 Worker 共享状态。",
                ],
                "sources": [],
            }
        if task_id == "T2":
            return {
                "summary": "向量数据库适合基于语义相似度召回长期知识。",
                "findings": [
                    "向量检索能够发现关键词不同但语义相关的内容。",
                    "需要额外的嵌入模型、索引和召回质量评估。",
                ],
                "sources": [],
            }
        if task_id == "T3":
            return {
                "summary": "文件式记忆实现简单，适合小规模和可审计场景。",
                "findings": [
                    "文件内容容易检查、备份和版本管理。",
                    "并发写入和复杂检索能力有限。",
                ],
                "sources": [],
            }
        if task_id == "T4":
            received = {result.task_id for result in dependency_results}
            if received != {"T1", "T2", "T3"}:
                raise ValueError("T4 requires completed results from T1, T2, and T3")
            return {
                "summary": "三种方案分别适合短期共享、语义召回和简单审计。",
                "criteria": ["访问延迟", "检索能力", "运维复杂度"],
                "options": [
                    {
                        "name": "Redis",
                        "strengths": ["低延迟", "支持 TTL", "便于共享"],
                        "weaknesses": ["需要独立服务"],
                    },
                    {
                        "name": "向量数据库",
                        "strengths": ["支持语义检索"],
                        "weaknesses": ["依赖嵌入模型和索引"],
                    },
                    {
                        "name": "文件系统",
                        "strengths": ["简单", "容易审计"],
                        "weaknesses": ["并发和检索能力有限"],
                    },
                ],
                "recommendation": "短期状态使用 Redis，长期知识使用向量数据库，文件用于归档。",
            }
        if task_id == "T5":
            if {result.task_id for result in dependency_results} != {"T4"}:
                raise ValueError("T5 requires the completed T4 comparison result")
            comparison = dependency_results[0].output or {}
            return {
                "title": "Agent Memory 后端方案比较报告",
                "executive_summary": comparison.get(
                    "recommendation",
                    "根据任务需求组合使用不同记忆后端。",
                ),
                "sections": [
                    {
                        "heading": "方案比较",
                        "content": comparison.get(
                            "summary",
                            "三种记忆后端具有不同适用范围。",
                        ),
                    }
                ],
            }
        raise ValueError(f"Unknown mock task: {task_id}")
