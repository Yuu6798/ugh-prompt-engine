"""svprpe intent-status — Intent Graph v0 の frontier/blocked/pending 一覧表示。"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import typer

from svp_rpe.cli._app import app, console

if TYPE_CHECKING:
    from svp_rpe.intent.frontier import FrontierReport

DEFAULT_GRAPH_PATH = "docs/intent/graph.yaml"


def _render_report(report: "FrontierReport", *, graph_path: Path) -> None:
    console.print(f"[bold]Intent Graph: {graph_path}[/bold]")

    console.print("[bold]status counts[/bold]")
    for status, count in report.status_counts.items():
        console.print(f"  {status}: {count}")

    console.print("[bold]frontier[/bold]")
    if not report.frontier:
        console.print("  (none)")
    for entry in report.frontier:
        console.print(f"  {entry.id} — {entry.claim}")

    console.print("[bold]blocked[/bold]")
    if not report.blocked:
        console.print("  (none)")
    for entry in report.blocked:
        ancestors = ", ".join(entry.blocking_dead_ancestors)
        console.print(f"  {entry.id} — {entry.claim} [dim](blocked by: {ancestors})[/dim]")

    console.print("[bold]machine_dependent[/bold]")
    if not report.machine_dependent:
        console.print("  (none)")
    for entry in report.machine_dependent:
        console.print(f"  {entry.id} — {entry.claim}")


@app.command("intent-status")
def intent_status_cmd(
    graph: str = typer.Option(
        DEFAULT_GRAPH_PATH,
        "--graph",
        help=(
            "Path to the Intent Graph YAML (default: docs/intent/graph.yaml, "
            "resolved relative to the current working directory)."
        ),
    ),
) -> None:
    """Load the Intent Graph v0 ledger and print its derived frontier/blocked status.

    Intent Graph v0 (`docs/intent/graph.yaml`, schema `intent-graph/0.1`,
    `svp_rpe.intent`) is a machine-readable pointer ledger for what is
    satisfied/unsatisfied about the "AI writes a score" intent, backed by
    evidence pointers into `docs/`/PR history. This command is a **read-only**
    instrument: it never writes to `--graph`, never updates any node's
    `status` (status transitions are manual, PR-review-only edits — see
    `docs/intent_graph.md`), and emits no weighting/scoring/rendering — only
    the derived classification from `svp_rpe.intent.frontier.derive_frontier`:

    - **status counts** — node count per `status` value.
    - **frontier** — `status="untested"` nodes that are not blocked and whose
      direct `depends_on` are all `verified`/`partial` (i.e. next in line to
      verify).
    - **blocked** — nodes (of any status) whose transitive `depends_on` chain
      reaches a `status="dead"` node, with the blocking dead ancestor id(s).
    - **machine_dependent** — `status="machine_dependent"` nodes, listed
      separately (never counted as frontier).

    `pending` (`status="untested"` but neither frontier nor blocked) is part
    of the derivation but intentionally not printed here — see
    `svp_rpe.intent.frontier.FrontierReport.pending` for programmatic access.

    Exit codes: `0` on success; `2` if the graph fails to load or fails its
    consistency checks (`svp_rpe.intent.loader.load_intent_graph`) — a
    duplicate node id, an unknown `depends_on` reference, a `depends_on`
    cycle, a `status="dead"` node missing `reentry`, or a repository-relative
    `evidence` path that does not exist.
    """
    from svp_rpe.intent.frontier import derive_frontier
    from svp_rpe.intent.loader import load_intent_graph

    graph_path = Path(graph)
    try:
        intent_graph = load_intent_graph(graph_path)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    report = derive_frontier(intent_graph)
    _render_report(report, graph_path=graph_path)
