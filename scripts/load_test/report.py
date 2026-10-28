"""Report generation for load test results."""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .models import LoadTestReport, ScenarioSummary, ConcurrencyTestResult


class ReportGenerator:
    """Generate reports in various formats."""

    def __init__(self, output_dir: str = "./load_test_results"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_json(self, report: LoadTestReport) -> Path:
        """Generate JSON report file."""
        filename = f"{report.test_id}.json"
        filepath = self.output_dir / filename

        with open(filepath, "w") as f:
            json.dump(report.to_dict(), f, indent=2)

        return filepath

    def print_console_summary(self, report: LoadTestReport) -> None:
        """Print rich console summary."""
        self._print_header("LOAD TEST REPORT")
        print(f"  Test ID:      {report.test_id}")
        print(f"  Environment:  {report.environment}")
        print(f"  Started:      {report.start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  Duration:     {report.duration_seconds:.0f} seconds")
        print()

        # Overall summary
        self._print_header("SUMMARY")
        print(f"  Total Requests:     {report.total_requests}")
        print(f"  Successful:         {report.total_successful}")
        print(f"  Success Rate:       {report.overall_success_rate:.1f}%")
        print(f"  Scenarios Tested:   {len(report.scenarios)}")
        print()

        # System metrics
        metrics = report.overall_system_metrics
        self._print_header("LOAD GENERATOR SYSTEM METRICS")
        print(
            f"  CPU Usage:          avg {metrics.cpu_percent_avg:.1f}%, max {metrics.cpu_percent_max:.1f}%"
        )
        print(
            f"  Memory Usage:       avg {metrics.memory_percent_avg:.1f}%, max {metrics.memory_percent_max:.1f}%"
        )
        print(f"  Memory Used:        {metrics.memory_mb_used:.0f} MB")
        print(
            f"  Disk I/O:           read {metrics.disk_read_mb:.1f} MB, write {metrics.disk_write_mb:.1f} MB"
        )
        print(
            f"  Network I/O:        sent {metrics.network_sent_mb:.1f} MB, recv {metrics.network_recv_mb:.1f} MB"
        )
        print()

        # Scenario results
        self._print_header("SCENARIO RESULTS")
        print()
        print(
            f"  {'Scenario':<30} {'Best RPS':<12} {'Best Conc':<12} {'P99 (ms)':<12} {'Success':<10}"
        )
        print(f"  {'-' * 76}")

        for scenario in report.scenarios:
            if not scenario.results:
                continue
            best_result = max(scenario.results, key=lambda r: r.throughput_rps)
            print(
                f"  {scenario.scenario_name[:28]:<30} "
                f"{best_result.throughput_rps:<12.1f} "
                f"{best_result.concurrency:<12} "
                f"{best_result.p99_latency_ms:<12.0f} "
                f"{best_result.success_rate:<10.1f}%"
            )

            if scenario.breaking_point:
                bp = scenario.breaking_point
                print(
                    f"    -> Breaking point at concurrency {bp.concurrency}: {bp.reason}"
                )
            if best_result.quota_rejections:
                print(
                    f"    -> Tenant quota rejections (HTTP 429): {best_result.quota_rejections}"
                )
            if best_result.pool_hits or best_result.pool_misses:
                print(
                    "    -> Pool: "
                    f"{best_result.pool_hits} hits, "
                    f"{best_result.pool_misses} misses, "
                    f"{best_result.pool_hit_rate:.1f}% hit rate"
                )
            if best_result.container_sources:
                sources = ", ".join(
                    f"{name}={count}"
                    for name, count in sorted(best_result.container_sources.items())
                )
                print(f"    -> Response sources: {sources}")

        print()

        # VM Recommendations
        rec = report.vm_recommendations
        self._print_header("VM SIZING RECOMMENDATIONS")
        if rec.bottleneck == "not_applicable":
            print("  Skipped: server-side resource metrics were not collected.")
            for note in rec.notes:
                print(f"  - {note}")
            print()
            self._print_footer()
            return
        print()
        print(f"  Estimated Resources:")
        print(f"    CPU Cores:        {rec.cpu_cores}")
        print(f"    Memory:           {rec.memory_gb} GB")
        print()
        print(f"  Cloud VM Types:")
        print(f"    Azure:            {rec.azure_vm_type}")
        print(f"    AWS:              {rec.aws_instance_type}")
        print(f"    GCP:              {rec.gcp_machine_type}")
        print()
        print(f"  Performance:")
        print(f"    Safe Concurrency: {rec.max_safe_concurrency}")
        print(f"    Breaking Point:   {rec.breaking_point_concurrency}")
        print(f"    Bottleneck:       {rec.bottleneck}")
        print(f"    Confidence:       {rec.confidence_score * 100:.0f}%")
        print()

        if rec.notes:
            print(f"  Notes:")
            for note in rec.notes:
                print(f"    - {note}")
        print()

        self._print_footer()

    def print_scenario_detail(self, scenario: ScenarioSummary) -> None:
        """Print detailed results for a single scenario."""
        self._print_header(f"SCENARIO: {scenario.scenario_name}")
        print(f"  ID:        {scenario.scenario_id}")
        print(f"  Category:  {scenario.category}")
        print(f"  Language:  {scenario.language}")
        print()

        print(
            f"  {'Concurrency':<12} {'Requests':<10} {'Success':<10} {'RPS':<10} {'P50':<10} {'P95':<10} {'P99':<10}"
        )
        print(f"  {'-' * 72}")

        for result in scenario.results:
            print(
                f"  {result.concurrency:<12} "
                f"{result.total_requests:<10} "
                f"{result.success_rate:<10.1f}% "
                f"{result.throughput_rps:<10.1f} "
                f"{result.p50_latency_ms:<10.0f} "
                f"{result.p95_latency_ms:<10.0f} "
                f"{result.p99_latency_ms:<10.0f}"
            )

        if scenario.breaking_point:
            bp = scenario.breaking_point
            print()
            print(f"  Breaking Point:")
            print(f"    Concurrency: {bp.concurrency}")
            print(f"    Reason:      {bp.reason}")
            print(f"    Details:     {bp.details}")

        print()

    def print_comparison(self, reports: List[LoadTestReport]) -> None:
        """Print comparison of multiple test runs."""
        if len(reports) < 2:
            print("Need at least 2 reports for comparison")
            return

        self._print_header("TEST RUN COMPARISON")
        print()

        # Header row
        header = f"  {'Metric':<30}"
        for report in reports:
            header += f" {report.test_id[:15]:<18}"
        print(header)
        print(f"  {'-' * (30 + 18 * len(reports))}")

        # Duration
        row = f"  {'Duration (s)':<30}"
        for report in reports:
            row += f" {report.duration_seconds:<18.0f}"
        print(row)

        # Total requests
        row = f"  {'Total Requests':<30}"
        for report in reports:
            row += f" {report.total_requests:<18}"
        print(row)

        # Success rate
        row = f"  {'Success Rate (%)':<30}"
        for report in reports:
            row += f" {report.overall_success_rate:<18.1f}"
        print(row)

        # Safe concurrency
        row = f"  {'Safe Concurrency':<30}"
        for report in reports:
            row += f" {report.vm_recommendations.max_safe_concurrency:<18}"
        print(row)

        # Breaking point
        row = f"  {'Breaking Point':<30}"
        for report in reports:
            row += f" {report.vm_recommendations.breaking_point_concurrency:<18}"
        print(row)

        # Bottleneck
        row = f"  {'Bottleneck':<30}"
        for report in reports:
            row += f" {report.vm_recommendations.bottleneck:<18}"
        print(row)

        # Recommended VM
        row = f"  {'Azure VM':<30}"
        for report in reports:
            row += f" {report.vm_recommendations.azure_vm_type:<18}"
        print(row)

        print()
        self._print_footer()

    def _print_header(self, title: str) -> None:
        """Print section header."""
        print()
        print("=" * 80)
        print(f"  {title}")
        print("=" * 80)

    def _print_footer(self) -> None:
        """Print footer."""
        print("=" * 80)
        print()


def print_progress(message: str) -> None:
    """Print progress message."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}")


def generate_ascii_chart(
    data: List[tuple],  # [(x, y), ...]
    width: int = 60,
    height: int = 15,
    x_label: str = "X",
    y_label: str = "Y",
) -> str:
    """Generate a simple ASCII chart."""
    if not data:
        return "No data"

    x_values = [d[0] for d in data]
    y_values = [d[1] for d in data]

    min_x, max_x = min(x_values), max(x_values)
    min_y, max_y = min(y_values), max(y_values)

    if max_y == min_y:
        max_y = min_y + 1

    # Create grid
    grid = [[" " for _ in range(width)] for _ in range(height)]

    # Plot points
    for x, y in data:
        col = int((x - min_x) / (max_x - min_x) * (width - 1)) if max_x > min_x else 0
        row = height - 1 - int((y - min_y) / (max_y - min_y) * (height - 1))
        row = max(0, min(height - 1, row))
        col = max(0, min(width - 1, col))
        grid[row][col] = "*"

    # Build chart
    lines = []
    lines.append(f"{y_label} ^")
    lines.append(f"  {max_y:.0f} |" + "".join(grid[0]))

    for i, row in enumerate(grid[1:-1], 1):
        y_val = max_y - (max_y - min_y) * i / (height - 1)
        lines.append(f"      |" + "".join(row))

    lines.append(f"  {min_y:.0f} |" + "".join(grid[-1]))
    lines.append("      +" + "-" * width + f"> {x_label}")
    lines.append(f"       {min_x:.0f}" + " " * (width - 10) + f"{max_x:.0f}")

    return "\n".join(lines)
