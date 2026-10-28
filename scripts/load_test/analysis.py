"""VM sizing analysis and recommendations."""

from typing import Dict, List, Optional, Tuple

from .config import (
    AZURE_VM_TYPES,
    AWS_INSTANCE_TYPES,
    GCP_MACHINE_TYPES,
    DEFAULT_THRESHOLDS,
    get_vm_type,
)
from .models import (
    BreakingPoint,
    ConcurrencyTestResult,
    LoadTestReport,
    ScenarioSummary,
    VMRecommendations,
)


class VMSizingAnalyzer:
    """Analyze load test results and provide VM sizing recommendations."""

    def __init__(
        self,
        p99_threshold_ms: float = 1000.0,
        error_rate_threshold: float = 5.0,
        throughput_degradation_threshold: float = 20.0,
    ):
        self.p99_threshold_ms = p99_threshold_ms
        self.error_rate_threshold = error_rate_threshold
        self.throughput_degradation_threshold = throughput_degradation_threshold

    def find_breaking_point(
        self,
        results: List[ConcurrencyTestResult],
    ) -> Optional[BreakingPoint]:
        """Find the concurrency level where performance degrades."""
        if not results or len(results) < 2:
            return None

        # Track peak throughput for degradation detection
        peak_throughput = 0.0
        peak_concurrency = 0

        for result in results:
            # Check P99 latency threshold
            if result.p99_latency_ms > self.p99_threshold_ms:
                return BreakingPoint(
                    concurrency=result.concurrency,
                    reason="latency",
                    details=f"P99 latency ({result.p99_latency_ms:.0f}ms) exceeded threshold ({self.p99_threshold_ms:.0f}ms)",
                    threshold_value=self.p99_threshold_ms,
                    actual_value=result.p99_latency_ms,
                )

            # Check error rate threshold
            if result.error_rate > self.error_rate_threshold:
                return BreakingPoint(
                    concurrency=result.concurrency,
                    reason="error_rate",
                    details=f"Error rate ({result.error_rate:.1f}%) exceeded threshold ({self.error_rate_threshold:.1f}%)",
                    threshold_value=self.error_rate_threshold,
                    actual_value=result.error_rate,
                )

            # Track peak throughput
            if result.throughput_rps > peak_throughput:
                peak_throughput = result.throughput_rps
                peak_concurrency = result.concurrency

            # Check throughput degradation
            if peak_throughput > 0 and result.concurrency > peak_concurrency:
                degradation = (
                    (peak_throughput - result.throughput_rps) / peak_throughput
                ) * 100
                if degradation > self.throughput_degradation_threshold:
                    return BreakingPoint(
                        concurrency=result.concurrency,
                        reason="throughput",
                        details=f"Throughput degraded {degradation:.1f}% from peak at concurrency {peak_concurrency}",
                        threshold_value=self.throughput_degradation_threshold,
                        actual_value=degradation,
                    )

        return None

    def identify_bottleneck(
        self,
        results: List[ConcurrencyTestResult],
    ) -> str:
        """Identify the primary bottleneck (cpu, memory, io, network)."""
        if not results:
            return "unknown"

        # Analyze resource usage trends
        cpu_values = []
        memory_values = []

        for result in results:
            metrics = result.system_metrics
            cpu_values.append(metrics.cpu_percent_max)
            memory_values.append(metrics.memory_percent_max)

        # Check which resource is most constrained
        max_cpu = max(cpu_values) if cpu_values else 0
        max_memory = max(memory_values) if memory_values else 0

        # CPU bottleneck: high CPU usage
        if max_cpu > 80:
            return "cpu"

        # Memory bottleneck: high memory usage
        if max_memory > 80:
            return "memory"

        # Check I/O patterns from latency
        # If latency is high but CPU/memory are low, likely I/O bound
        if results:
            last_result = results[-1]
            if last_result.p99_latency_ms > 500 and max_cpu < 50 and max_memory < 50:
                return "io"

        # Default: assume CPU if unable to determine
        if max_cpu > max_memory:
            return "cpu"
        elif max_memory > 30:
            return "memory"

        return "cpu"

    def calculate_safe_concurrency(
        self,
        results: List[ConcurrencyTestResult],
        target_p99_ms: Optional[float] = None,
        target_error_rate: Optional[float] = None,
    ) -> int:
        """Calculate the maximum safe operating concurrency."""
        if not results:
            return 1

        target_p99 = target_p99_ms or self.p99_threshold_ms * 0.8  # 80% of threshold
        target_error = (
            target_error_rate or self.error_rate_threshold * 0.5
        )  # 50% of threshold

        safe_concurrency = 1

        for result in results:
            # Check if this concurrency level is safe
            if (
                result.p99_latency_ms <= target_p99
                and result.error_rate <= target_error
            ):
                safe_concurrency = result.concurrency
            else:
                break

        return safe_concurrency

    def estimate_resource_requirements(
        self,
        results: List[ConcurrencyTestResult],
        target_concurrency: int,
    ) -> Tuple[int, int]:
        """Estimate CPU cores and memory GB needed for target concurrency."""
        if not results:
            return (2, 8)  # Default minimum

        # Find result closest to target concurrency
        closest_result = min(
            results, key=lambda r: abs(r.concurrency - target_concurrency)
        )

        metrics = closest_result.system_metrics

        # Calculate resource needs based on utilization
        # If at 50% CPU with current concurrency, double concurrency needs double CPU
        current_cpu_percent = metrics.cpu_percent_avg or 50
        current_memory_percent = metrics.memory_percent_avg or 30

        # Estimate cores needed (assuming current machine)
        # We want to be at ~70% utilization at target concurrency
        scaling_factor = target_concurrency / max(closest_result.concurrency, 1)
        target_cpu_percent = min(current_cpu_percent * scaling_factor, 100)

        # Map to actual cores (assuming 70% target utilization)
        estimated_cores = max(2, int((target_cpu_percent / 70) * 4))  # Base 4 cores

        # Round to common VM sizes
        if estimated_cores <= 2:
            cores = 2
        elif estimated_cores <= 4:
            cores = 4
        elif estimated_cores <= 8:
            cores = 8
        elif estimated_cores <= 16:
            cores = 16
        elif estimated_cores <= 32:
            cores = 32
        else:
            cores = 64

        # Memory: estimate based on observed usage
        # Use at least 2GB per core, more for memory-bound workloads
        memory_per_core = 4 if current_memory_percent > 50 else 2
        memory_gb = cores * memory_per_core

        return (cores, memory_gb)

    def analyze_report(self, report: LoadTestReport) -> VMRecommendations:
        """Analyze a complete load test report and generate recommendations."""
        recommendations = VMRecommendations()
        notes = []

        if not report.config.enable_vm_sizing:
            recommendations.bottleneck = "not_applicable"
            recommendations.notes = [
                "VM sizing disabled: client-host metrics do not represent "
                "the Dockerized API server"
            ]
            return recommendations

        if report.scenarios and all(
            scenario.category == "control" for scenario in report.scenarios
        ):
            recommendations.bottleneck = "not_applicable"
            recommendations.notes = [
                "Control-plane tests validate HTTP and quota behavior; "
                "they do not support VM sizing conclusions"
            ]
            return recommendations

        # Collect all results
        all_results: List[ConcurrencyTestResult] = []
        breaking_points: List[BreakingPoint] = []

        for scenario in report.scenarios:
            all_results.extend(scenario.results)
            bp = self.find_breaking_point(scenario.results)
            if bp:
                breaking_points.append(bp)
                scenario.breaking_point = bp

        if not all_results:
            notes.append("No test results to analyze")
            recommendations.notes = notes
            return recommendations

        # Find overall breaking point (earliest across scenarios)
        if breaking_points:
            earliest_bp = min(breaking_points, key=lambda bp: bp.concurrency)
            recommendations.breaking_point_concurrency = earliest_bp.concurrency
            notes.append(
                f"Breaking point at concurrency {earliest_bp.concurrency}: {earliest_bp.reason}"
            )
        else:
            # No breaking point found - use max tested
            recommendations.breaking_point_concurrency = max(
                r.concurrency for r in all_results
            )
            notes.append("No breaking point found within tested range")

        # Calculate safe concurrency (80% of breaking point)
        # Sort results by concurrency to ensure ascending order (fixes mixed scenario results)
        sorted_results = sorted(all_results, key=lambda r: r.concurrency)
        recommendations.max_safe_concurrency = self.calculate_safe_concurrency(
            sorted_results
        )
        if (
            recommendations.max_safe_concurrency
            < recommendations.breaking_point_concurrency
        ):
            notes.append(
                f"Recommended safe concurrency: {recommendations.max_safe_concurrency}"
            )

        # Identify bottleneck
        recommendations.bottleneck = self.identify_bottleneck(all_results)
        notes.append(f"Primary bottleneck: {recommendations.bottleneck}")

        # Estimate resource requirements for safe concurrency
        cores, memory = self.estimate_resource_requirements(
            all_results,
            recommendations.max_safe_concurrency,
        )
        recommendations.cpu_cores = cores
        recommendations.memory_gb = memory
        notes.append(f"Estimated requirements: {cores} cores, {memory}GB RAM")

        # Get VM type recommendations
        recommendations.azure_vm_type = get_vm_type(cores, memory, "azure")
        recommendations.aws_instance_type = get_vm_type(cores, memory, "aws")
        recommendations.gcp_machine_type = get_vm_type(cores, memory, "gcp")

        # Calculate confidence score
        recommendations.confidence_score = self._calculate_confidence(
            all_results, breaking_points
        )

        # Add bottleneck-specific notes
        if recommendations.bottleneck == "cpu":
            notes.append("Consider compute-optimized VMs for better performance")
        elif recommendations.bottleneck == "memory":
            notes.append("Consider memory-optimized VMs for better performance")
        elif recommendations.bottleneck == "io":
            notes.append(
                "Consider VMs with faster storage (SSD/NVMe) for better performance"
            )

        recommendations.notes = notes
        return recommendations

    def _calculate_confidence(
        self,
        results: List[ConcurrencyTestResult],
        breaking_points: List[BreakingPoint],
    ) -> float:
        """Calculate confidence score for recommendations."""
        if not results:
            return 0.0

        score = 0.5  # Base score

        # More results = higher confidence
        if len(results) >= 10:
            score += 0.2
        elif len(results) >= 5:
            score += 0.1

        # Breaking point found = higher confidence
        if breaking_points:
            score += 0.1

        # Low error rates = higher confidence
        avg_error_rate = sum(r.error_rate for r in results) / len(results)
        if avg_error_rate < 1:
            score += 0.1
        elif avg_error_rate < 5:
            score += 0.05

        # Consistent results = higher confidence
        if len(results) >= 3:
            throughputs = [r.throughput_rps for r in results if r.throughput_rps > 0]
            if throughputs:
                avg_tp = sum(throughputs) / len(throughputs)
                variance = sum((t - avg_tp) ** 2 for t in throughputs) / len(
                    throughputs
                )
                cv = (
                    (variance**0.5) / avg_tp if avg_tp > 0 else 1
                )  # Coefficient of variation
                if cv < 0.2:
                    score += 0.1

        return min(score, 1.0)


def analyze_and_recommend(report: LoadTestReport) -> VMRecommendations:
    """Convenience function to analyze report and get recommendations."""
    analyzer = VMSizingAnalyzer()
    recommendations = analyzer.analyze_report(report)
    report.vm_recommendations = recommendations
    return recommendations
