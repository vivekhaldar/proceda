"""ABOUTME: CLI commands for managing step-level execution cache.
ABOUTME: Provides build, show, and clear subcommands for cache lifecycle."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from proceda.config import ProcedaConfig
from proceda.events import EventType
from proceda.skills.loader import load_skill
from proceda.store.event_log import EventLogReader, RunDirectoryManager

console = Console()

cache_app = typer.Typer(help="Manage step-level execution cache.")


@cache_app.command("build")
def cache_build(
    skill_path: str = typer.Argument(..., help="Path to SKILL.md or directory containing it"),
    min_traces: int = typer.Option(3, help="Minimum successful traces required"),
) -> None:
    """Analyze traces and build an execution cache for a skill."""
    from proceda.cache.analyzer import TraceAnalyzer
    from proceda.cache.store import CacheStore

    skill = load_skill(skill_path)
    config = ProcedaConfig.load()

    dir_manager = RunDirectoryManager(config.logging.run_dir)
    run_dirs = dir_manager.list_runs()

    matching = []
    for d in run_dirs:
        reader = EventLogReader(d)
        meta = reader.read_metadata()
        if meta.get("skill_id") == skill.id and reader.exists:
            events = reader.read_events()
            if any(e.type == EventType.RUN_COMPLETED for e in events):
                matching.append(d)

    if len(matching) < min_traces:
        console.print(
            f"[yellow]Only {len(matching)} completed runs found "
            f"(need {min_traces}). Run the skill more times first.[/yellow]"
        )
        raise typer.Exit(1)

    console.print(f"Analyzing {min_traces} traces for skill '{skill.name}'...")
    analyzer = TraceAnalyzer(min_traces=min_traces)
    cache = analyzer.analyze(matching[:min_traces], skill.raw_content)

    if cache is None:
        console.print("[red]Could not build cache — patterns were not consistent enough.[/red]")
        raise typer.Exit(1)

    # Set the skill_id (analyzer doesn't have it)
    cache.skill_id = skill.id

    store = CacheStore(str(Path(config.logging.run_dir).parent / "cache"))
    path = store.save(cache)
    console.print(f"[green]Cache built: {path}[/green]")
    console.print(f"  Steps cached: {list(cache.step_recipes.keys())}")
    for idx, recipe in sorted(cache.step_recipes.items()):
        tools = [tc.tool_name for tc in recipe.tool_call_recipes]
        console.print(f"  Step {idx}: {tools or '(no tools)'} confidence={recipe.confidence:.1%}")


@cache_app.command("clear")
def cache_clear(
    skill_path: str = typer.Argument(..., help="Path to SKILL.md or directory containing it"),
) -> None:
    """Delete the execution cache for a skill."""
    from proceda.cache.store import CacheStore

    skill = load_skill(skill_path)
    config = ProcedaConfig.load()
    store = CacheStore(str(Path(config.logging.run_dir).parent / "cache"))
    if store.delete(skill.id):
        console.print(f"[green]Cache cleared for skill '{skill.name}'[/green]")
    else:
        console.print(f"[yellow]No cache found for skill '{skill.name}'[/yellow]")


@cache_app.command("show")
def cache_show(
    skill_path: str = typer.Argument(..., help="Path to SKILL.md or directory containing it"),
) -> None:
    """Show the current cache for a skill."""
    from proceda.cache.store import CacheStore

    skill = load_skill(skill_path)
    config = ProcedaConfig.load()
    store = CacheStore(str(Path(config.logging.run_dir).parent / "cache"))
    cache = store.load(skill.id, skill.raw_content)

    if cache is None:
        console.print(f"[yellow]No valid cache for skill '{skill.name}'[/yellow]")
        raise typer.Exit(1)

    console.print(f"[bold]Cache for: {skill.name}[/bold] (id: {cache.skill_id})")
    console.print(f"Built from: {len(cache.source_run_ids)} runs")
    console.print(f"Created: {cache.created_at}")
    console.print(f"Steps cached: {len(cache.step_recipes)}")
    console.print()

    for idx, recipe in sorted(cache.step_recipes.items()):
        console.print(f"  Step {idx} (confidence: {recipe.confidence:.1%}):")
        for tc in recipe.tool_call_recipes:
            args_desc = {}
            for name, mapping in tc.argument_mappings.items():
                args_desc[name] = f"{mapping.source.value}:{mapping.key or mapping.literal_value}"
            console.print(f"    → {tc.tool_name}({args_desc})")
        if recipe.result_captures:
            fields = [rc.field_name for rc in recipe.result_captures]
            console.print(f"    captures: {fields}")
