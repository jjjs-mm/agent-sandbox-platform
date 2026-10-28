#!/usr/bin/env python3
"""Command-line interface for load testing."""

import argparse
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.load_test.analysis import analyze_and_recommend
from scripts.load_test.config import DEFAULTS, ENVIRONMENTS, SUPPORTED_LANGUAGES
from scripts.load_test.models import LoadTestConfig, LoadTestReport
from scripts.load_test.report import ReportGenerator, print_progress
from scripts.load_test.runner import LoadTestRunner
from scripts.load_test.scenarios import (
    SCENARIOS,
    get_all_scenarios,
    get_scenario_by_id,
    get_scenarios_by_category,
)
from scripts.load_test.scenarios.cpu_bound import CPULightScenario, CPUMediumScenario
from scripts.load_test.scenarios.multi_language import get_baseline_scenarios


def create_parser() -> argparse.ArgumentParser:
    """Create argument parser."""
    parser = argparse.ArgumentParser(
        prog="python -m scripts.load_test",
        description="Load testing tool for VM sizing of Code Interpreter API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Quick CPU test
  python -m scripts.load_test --category cpu --max-concurrency 20

  # Full VM sizing suite
  python -m scripts.load_test --full-suite --env https://localhost

  # Sustained Python load
  python -m scripts.load_test --language py --sustained --duration 300

  # Test all languages
  python -m scripts.load_test --category language --ramp-up

  # Compare environments
  python -m scripts.load_test --env https://staging.example.com --full-suite
""",
    )

    # Environment options
    env_group = parser.add_argument_group("Environment")
    env_group.add_argument(
        "--env",
        "-e",
        default="https://localhost",
        help="Environment URL or profile name (dev/staging/prod)",
    )
    env_group.add_argument(
        "--api-key",
        "-k",
        default=os.environ.get("API_KEY", "test-api-key-for-development-only"),
        help="API key (default: $API_KEY or test key)",
    )
    env_group.add_argument(
        "--output-dir",
        "-o",
        default="./load_test_results",
        help="Output directory for results (default: ./load_test_results)",
    )

    # Scenario selection
    scenario_group = parser.add_argument_group("Scenario Selection")
    scenario_group.add_argument(
        "--scenario",
        "-s",
        action="append",
        dest="scenarios",
        help="Scenario ID to run (can repeat)",
    )
    scenario_group.add_argument(
        "--category",
        "-c",
        choices=["cpu", "memory", "io", "language", "control", "all"],
        default=None,
        help="Category of scenarios to run",
    )
    scenario_group.add_argument(
        "--language",
        "-l",
        action="append",
        dest="languages",
        choices=SUPPORTED_LANGUAGES,
        help="Language filter (can repeat)",
    )

    # Concurrency options
    conc_group = parser.add_argument_group("Concurrency")
    conc_group.add_argument(
        "--min-concurrency",
        type=int,
        default=DEFAULTS.min_concurrency,
        help=f"Starting concurrency (default: {DEFAULTS.min_concurrency})",
    )
    conc_group.add_argument(
        "--max-concurrency",
        type=int,
        default=DEFAULTS.max_concurrency,
        help=f"Maximum concurrency (default: {DEFAULTS.max_concurrency})",
    )
    conc_group.add_argument(
        "--step",
        type=int,
        default=DEFAULTS.concurrency_step,
        help=f"Concurrency step size (default: {DEFAULTS.concurrency_step})",
    )
    conc_group.add_argument(
        "--requests",
        type=int,
        default=DEFAULTS.requests_per_step,
        help=f"Requests per concurrency level (default: {DEFAULTS.requests_per_step})",
    )

    # Test type options
    test_group = parser.add_argument_group("Test Type")
    test_group.add_argument(
        "--health-baseline",
        action="store_true",
        help="Benchmark HTTP control-plane overhead without sandbox execution",
    )
    test_group.add_argument(
        "--ramp-up",
        action="store_true",
        help="Run ramp-up test (gradually increase concurrency)",
    )
    test_group.add_argument(
        "--sustained",
        action="store_true",
        help="Run sustained load test at fixed concurrency",
    )
    test_group.add_argument(
        "--duration",
        type=int,
        default=60,
        help="Duration for sustained test in seconds (default: 60)",
    )
    test_group.add_argument(
        "--quick",
        action="store_true",
        help="Run quick test (single concurrency level)",
    )
    test_group.add_argument(
        "--full-suite",
        action="store_true",
        help="Run complete VM sizing test suite",
    )

    # Output options
    output_group = parser.add_argument_group("Output")
    output_group.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Minimal output",
    )
    output_group.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose output",
    )
    output_group.add_argument(
        "--no-report",
        action="store_true",
        help="Skip report generation",
    )
    output_group.add_argument(
        "--vm-sizing",
        action="store_true",
        help=(
            "Infer VM size only when the load generator runs on the API host "
            "and its resource metrics represent the server"
        ),
    )
    output_group.add_argument(
        "--server-container",
        default=None,
        help="Docker container name to sample for server-side CPU and memory",
    )

    # Other
    parser.add_argument(
        "--list-scenarios",
        action="store_true",
        help="List available scenarios and exit",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=DEFAULTS.warmup_requests,
        help=f"Warmup requests (default: {DEFAULTS.warmup_requests})",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULTS.timeout_seconds,
        help=f"Request timeout in seconds (default: {DEFAULTS.timeout_seconds})",
    )

    return parser


def list_scenarios() -> None:
    """Print list of available scenarios."""
    print("\nAvailable Scenarios:")
    print("=" * 70)

    for category, scenarios in SCENARIOS.items():
        print(f"\n{category.upper()}:")
        print("-" * 40)
        for scenario in scenarios:
            print(f"  {scenario.id:<25} {scenario.name}")
            if scenario.description:
                print(f"    {scenario.description}")

    print("\n")


def get_base_url(env: str) -> str:
    """Get base URL from environment name or URL."""
    if env in ENVIRONMENTS:
        return ENVIRONMENTS[env].base_url
    return env


def select_scenarios(args) -> List:
    """Select scenarios based on arguments."""
    scenarios = []

    # Specific scenarios by ID
    if args.scenarios:
        for scenario_id in args.scenarios:
            scenario = get_scenario_by_id(scenario_id)
            if scenario:
                scenarios.append(scenario)
            else:
                print(f"Warning: Unknown scenario '{scenario_id}'")

    # Category filter
    if args.category:
        if args.category == "all":
            scenarios.extend(get_all_scenarios())
        else:
            scenarios.extend(get_scenarios_by_category(args.category))

    # Language filter
    if args.languages:
        # Filter existing scenarios by language
        if scenarios:
            scenarios = [s for s in scenarios if s.language in args.languages]
        else:
            # Get language baseline scenarios for specified languages
            from scripts.load_test.scenarios.multi_language import (
                LanguageBaselineScenario,
                LanguageComputeScenario,
            )

            for lang in args.languages:
                scenarios.append(LanguageBaselineScenario(lang))
                scenarios.append(LanguageComputeScenario(lang))

    # Default: quick CPU test
    if not scenarios:
        scenarios = [CPULightScenario(), CPUMediumScenario()]

    return scenarios


async def run_load_test(args) -> Optional[LoadTestReport]:
    """Run load test with given arguments."""
    from datetime import timezone
    import uuid

    base_url = get_base_url(args.env)
    scenarios = [] if args.health_baseline else select_scenarios(args)

    if not scenarios and not args.health_baseline:
        print("No scenarios selected")
        return None

    # Create config
    config = LoadTestConfig(
        base_url=base_url,
        api_key=args.api_key,
        environment=args.env,
        min_concurrency=args.min_concurrency,
        max_concurrency=args.max_concurrency,
        concurrency_step=args.step,
        requests_per_step=args.requests,
        warmup_requests=args.warmup,
        timeout_seconds=args.timeout,
        output_dir=args.output_dir,
        enable_vm_sizing=args.vm_sizing,
        server_container=args.server_container,
    )

    # Progress callback
    def progress(msg: str) -> None:
        if not args.quiet:
            print_progress(msg)

    progress(f"Starting load test against {base_url}")
    progress(f"Scenarios: {len(scenarios)}")
    progress(f"Concurrency: {args.min_concurrency} to {args.max_concurrency}")

    # Run tests
    async with LoadTestRunner(config, progress) as runner:
        if args.health_baseline:
            start_time = datetime.now(timezone.utc)
            scenario_results = [
                await runner.run_health_baseline(
                    concurrency=args.max_concurrency,
                    num_requests=args.requests,
                )
            ]
            end_time = datetime.now(timezone.utc)
            report = LoadTestReport(
                test_id=f"load-test-{start_time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}",
                environment=args.env,
                start_time=start_time,
                end_time=end_time,
                config=config,
                scenarios=scenario_results,
                overall_system_metrics=scenario_results[0].results[0].system_metrics,
            )
        elif args.full_suite:
            report = await runner.run_full_suite(scenarios)
        elif args.sustained:
            # Run sustained test at max concurrency
            start_time = datetime.now(timezone.utc)
            scenario_results = await runner.run_scenarios(
                scenarios,
                test_type="sustained",
                concurrency=args.max_concurrency,
                duration_seconds=args.duration,
            )
            end_time = datetime.now(timezone.utc)
            # Create report manually
            report = LoadTestReport(
                test_id=f"load-test-{start_time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}",
                environment=args.env,
                start_time=start_time,
                end_time=end_time,
                config=config,
                scenarios=scenario_results,
            )
        elif args.quick:
            start_time = datetime.now(timezone.utc)
            scenario_results = await runner.run_scenarios(
                scenarios,
                test_type="quick",
                concurrency=args.min_concurrency,
                num_requests=args.requests,
            )
            end_time = datetime.now(timezone.utc)
            report = LoadTestReport(
                test_id=f"load-test-{start_time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}",
                environment=args.env,
                start_time=start_time,
                end_time=end_time,
                config=config,
                scenarios=scenario_results,
            )
        else:
            # Default: ramp-up test
            report = await runner.run_full_suite(scenarios)

    if (
        report.scenarios
        and report.scenarios[0].results
        and report.overall_system_metrics.memory_mb_used == 0
    ):
        report.overall_system_metrics = report.scenarios[0].results[0].system_metrics

    return report


def main() -> int:
    """Main entry point."""
    parser = create_parser()
    args = parser.parse_args()

    # List scenarios and exit
    if args.list_scenarios:
        list_scenarios()
        return 0

    # Run load test
    try:
        report = asyncio.run(run_load_test(args))
    except KeyboardInterrupt:
        print("\nTest interrupted")
        return 1
    except Exception as e:
        print(f"Error: {e}")
        if args.verbose:
            import traceback

            traceback.print_exc()
        return 1

    if report is None:
        return 1

    # Analyze and add recommendations
    analyze_and_recommend(report)

    # Generate reports
    if not args.no_report:
        generator = ReportGenerator(args.output_dir)

        # Save JSON
        json_path = generator.generate_json(report)
        print(f"\nJSON report saved: {json_path}")

        # Print console summary
        if not args.quiet:
            generator.print_console_summary(report)

    return 0


if __name__ == "__main__":
    sys.exit(main())
