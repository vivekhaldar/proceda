"""ABOUTME: Orchestrates skill execution by wiring together LLM, MCP tools, and event sinks.
ABOUTME: Provides Runtime (top-level executor) and RunHandle (async event stream handle).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from proceda.config import ProcedaConfig
from proceda.events import (
    CompositeEventSink,
    EventType,
    RunEvent,
)
from proceda.exceptions import ExecutionError
from proceda.human import AutoApproveHumanInterface, HumanInterface
from proceda.internal.executor import Executor
from proceda.internal.summary import generate_run_summary
from proceda.internal.tool_executor import ToolExecutor
from proceda.llm.runtime import LLMRuntime
from proceda.mcp.orchestrator import MCPOrchestrator
from proceda.session import RunResult, RunSession, RunStatus
from proceda.skill import Skill
from proceda.store.event_log import EventLogReader, EventLogWriter, RunDirectoryManager

logger = logging.getLogger(__name__)


class RunHandle:
    """A handle to a running or completed skill execution."""

    def __init__(
        self,
        session: RunSession,
        skill: Skill,
        event_log_path: Path | None = None,
    ) -> None:
        self.session = session
        self.skill = skill
        self.event_log_path = event_log_path
        self._events: list[RunEvent] = []
        self._event_queue: asyncio.Queue[RunEvent | None] = asyncio.Queue()

    async def handle(self, event: RunEvent) -> None:
        """Receive an event (implements EventSink protocol)."""
        self._events.append(event)
        await self._event_queue.put(event)

    async def events(self) -> AsyncIterator[RunEvent]:
        """Async iterator over events as they arrive."""
        while True:
            event = await self._event_queue.get()
            if event is None:
                break
            yield event

    def to_result(self) -> RunResult:
        """Convert to a final RunResult."""
        summary = generate_run_summary(self.session, self.skill)
        failed_step = None
        if self.session.status == RunStatus.FAILED:
            failed_step = self.session.current_step

        return RunResult(
            session_id=self.session.id,
            status=self.session.status,
            summary=summary,
            completed_steps=len(self.session.completed_steps),
            total_steps=self.skill.step_count,
            failed_step=failed_step,
            event_log_path=self.event_log_path,
            prompt_tokens=self.session.total_prompt_tokens,
            completion_tokens=self.session.total_completion_tokens,
            total_tokens=self.session.total_llm_tokens,
        )


class Runtime:
    """The top-level runtime for executing skills."""

    def __init__(
        self,
        config: ProcedaConfig | None = None,
        human: HumanInterface | None = None,
    ) -> None:
        self._config = config or ProcedaConfig()
        self._human = human or AutoApproveHumanInterface()
        self._orchestrator: MCPOrchestrator | None = None
        self._last_handle: RunHandle | None = None

    @property
    def last_session(self) -> RunSession:
        """The session from the most recent run. Raises if no run has been executed."""
        if self._last_handle is None:
            raise RuntimeError("No run has been executed yet")
        return self._last_handle.session

    async def run(
        self,
        skill: Skill,
        variables: dict[str, str] | None = None,
        event_sinks: list[Any] | None = None,
    ) -> RunResult:
        """Execute a skill to completion and return the result."""
        handle = await self.start(skill, variables=variables, event_sinks=event_sinks)
        self._last_handle = handle

        # Wait for completion (events are processed internally)
        while not handle.session.status.is_terminal:
            await asyncio.sleep(0.01)

        return handle.to_result()

    async def start(
        self,
        skill: Skill,
        variables: dict[str, str] | None = None,
        event_sinks: list[Any] | None = None,
    ) -> RunHandle:
        """Start a skill execution and return a handle for monitoring."""
        session = RunSession.create(skill.id, skill.name, variables)

        # Set up run directory and event log
        dir_manager = RunDirectoryManager(self._config.logging.run_dir)
        run_dir = dir_manager.create_run_dir(session.id)
        log_writer = EventLogWriter(run_dir, redact_secrets=self._config.logging.redact_secrets)
        await log_writer.open()

        handle = RunHandle(session, skill, event_log_path=run_dir)

        # Compose event sinks
        composite = CompositeEventSink()
        composite.add(log_writer)
        composite.add(handle)
        if event_sinks:
            for sink in event_sinks:
                composite.add(sink)

        # Emit run.created
        await composite.handle(
            RunEvent.create(
                session.id,
                EventType.RUN_CREATED,
                {
                    "skill_name": skill.name,
                    "skill_id": skill.id,
                    "step_count": skill.step_count,
                },
            )
        )

        # Write metadata
        log_writer.write_metadata(
            {
                "run_id": session.id,
                "skill_name": skill.name,
                "skill_id": skill.id,
                "step_count": skill.step_count,
                "variables": variables or {},
                "model": self._config.llm.model,
                "created_at": session.created_at.isoformat(),
            }
        )

        # Connect MCP apps
        orchestrator = MCPOrchestrator(
            app_configs=self._config.apps,
            security=self._config.security,
            required_tools=skill.required_tools,
        )
        self._orchestrator = orchestrator

        if self._config.apps:
            await orchestrator.connect_all()

        missing = orchestrator.check_required_tools()
        if missing:
            raise ExecutionError(f"Required tools not available: {', '.join(missing)}")

        # Set up tool executor
        tool_executor = ToolExecutor(orchestrator, session.id) if self._config.apps else None
        tool_schemas = orchestrator.get_tool_schemas() if self._config.apps else []

        # Create LLM runtime
        llm = LLMRuntime(self._config.llm)

        # Load execution cache if enabled
        skill_cache = None
        if self._config.cache.enabled:
            from proceda.cache.store import CacheStore

            cache_store = CacheStore(str(Path(self._config.logging.run_dir).parent / "cache"))
            skill_cache = cache_store.load(skill.id, skill.raw_content)
            if skill_cache:
                logger.info(
                    "Loaded execution cache for skill %s (%d step recipes)",
                    skill.name,
                    len(skill_cache.step_recipes),
                )

        # Create executor
        executor = Executor(
            skill=skill,
            session=session,
            llm=llm,
            tool_executor=tool_executor,
            human=self._human,
            emit=composite.handle,
            tool_schemas=tool_schemas,
            skill_cache=skill_cache,
            cache_config=self._config.cache,
        )

        async def _run() -> None:
            try:
                await executor.execute()
            finally:
                # Clean up
                if self._orchestrator:
                    await self._orchestrator.disconnect_all()

                # Write summary
                summary = generate_run_summary(session, skill)
                log_writer.write_summary(summary)
                await log_writer.close()

                # Auto-build cache after successful run if enabled
                if (
                    self._config.cache.enabled
                    and session.status == RunStatus.COMPLETED
                    and skill_cache is None
                ):
                    self._try_auto_build_cache(skill)

                # Signal end of events
                await handle._event_queue.put(None)

        # Run in background
        asyncio.create_task(_run())

        return handle

    def _try_auto_build_cache(self, skill: Skill) -> None:
        """Attempt to build a cache if enough successful traces exist."""
        from proceda.cache.analyzer import TraceAnalyzer
        from proceda.cache.store import CacheStore

        try:
            dir_manager = RunDirectoryManager(self._config.logging.run_dir)
            run_dirs = dir_manager.list_runs()

            matching_dirs = []
            for run_dir in run_dirs:
                reader = EventLogReader(run_dir)
                metadata = reader.read_metadata()
                if metadata.get("skill_id") == skill.id and reader.exists:
                    events = reader.read_events()
                    if any(e.type == EventType.RUN_COMPLETED for e in events):
                        matching_dirs.append(run_dir)

            if len(matching_dirs) >= self._config.cache.min_traces:
                analyzer = TraceAnalyzer(min_traces=self._config.cache.min_traces)
                cache = analyzer.analyze(
                    matching_dirs[: self._config.cache.min_traces],
                    skill.raw_content,
                )
                if cache:
                    cache.skill_id = skill.id
                    store = CacheStore(str(Path(self._config.logging.run_dir).parent / "cache"))
                    path = store.save(cache)
                    logger.info("Auto-built execution cache for skill %s at %s", skill.name, path)
        except Exception:
            logger.debug("Failed to auto-build cache", exc_info=True)
