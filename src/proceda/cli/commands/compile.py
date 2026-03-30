"""ABOUTME: CLI command for compiling SOP steps into Python functions.
ABOUTME: Orchestrates codegen, verification, and storage of compiled steps."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from rich.console import Console

from proceda.config import ProcedaConfig
from proceda.skills.loader import load_skill

console = Console()


def compile_skill_cmd(
    skill_path: str = typer.Argument(..., help="Path to SKILL.md or directory containing it"),
    num_traces: int = typer.Option(10, help="Number of traces to use"),
    prompt_examples: int = typer.Option(3, help="Number of examples in codegen prompt"),
    model: str = typer.Option(
        "anthropic/claude-sonnet-4-20250514", help="LLM model for code generation"
    ),
    max_retries: int = typer.Option(2, help="Max codegen retries per step"),
) -> None:
    """Compile SOP steps into Python functions using LLM codegen."""
    from proceda.compile.compiler import compile_skill
    from proceda.compile.store import CompiledSkillStore

    skill = load_skill(skill_path)
    config = ProcedaConfig.load()

    console.print(f"Compiling skill '{skill.name}' ({skill.step_count} steps)...")
    console.print(f"  Model: {model}")
    held_out = num_traces - prompt_examples
    console.print(f"  Traces: {num_traces} (prompt={prompt_examples}, held-out={held_out})")
    console.print()

    manifest, functions = asyncio.run(
        compile_skill(
            skill=skill,
            run_dir_base=config.logging.run_dir,
            num_traces=num_traces,
            num_prompt_examples=prompt_examples,
            codegen_model=model,
            max_retries=max_retries,
        )
    )

    store = CompiledSkillStore(str(Path(config.logging.run_dir).parent / "compiled"))
    path = store.save(manifest, functions)
    console.print(f"[green]Compiled skill saved to: {path}[/green]")
    console.print()

    compiled_count = sum(1 for s in manifest.steps.values() if s.status == "compiled")
    failed_count = sum(1 for s in manifest.steps.values() if s.status == "failed")
    console.print(f"Results: {compiled_count} compiled, {failed_count} failed")
    console.print()

    for idx in sorted(manifest.steps):
        info = manifest.steps[idx]
        if info.status == "compiled":
            console.print(
                f"  Step {idx}: [green]compiled[/green] "
                f"(verified {info.verified_against} traces, "
                f"{info.held_out_passed} held-out passed)"
            )
        else:
            console.print(f"  Step {idx}: [red]failed[/red] — {info.failure_reason[:80]}")
