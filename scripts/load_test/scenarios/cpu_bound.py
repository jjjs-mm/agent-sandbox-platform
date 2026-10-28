"""CPU-bound test scenarios."""

from .base import BaseScenario


class CPULightScenario(BaseScenario):
    """Light CPU computation - simple math."""

    id = "cpu_light"
    name = "CPU Light"
    description = "Simple math operations (sum/range)"
    category = "cpu"
    language = "py"
    expected_latency_range = (20, 200)

    def get_code(self, iteration: int = 0) -> str:
        return f"""
result = sum(range(10000))
print(f'Sum: {{result}}, iteration: {iteration}')
"""


class CPUMediumScenario(BaseScenario):
    """Medium CPU computation - moderate math."""

    id = "cpu_medium"
    name = "CPU Medium"
    description = "Moderate computation (squared sum)"
    category = "cpu"
    language = "py"
    expected_latency_range = (50, 500)

    def get_code(self, iteration: int = 0) -> str:
        return f"""
result = sum(i**2 for i in range(100000))
print(f'Sum of squares: {{result}}, iteration: {iteration}')
"""


class CPUHeavyScenario(BaseScenario):
    """Heavy CPU computation - matrix multiplication."""

    id = "cpu_heavy"
    name = "CPU Heavy"
    description = "Heavy computation (matrix multiplication)"
    category = "cpu"
    language = "py"
    expected_latency_range = (100, 2000)

    def get_code(self, iteration: int = 0) -> str:
        return f"""
import numpy as np
size = 500
a = np.random.rand(size, size)
b = np.random.rand(size, size)
c = np.dot(a, b)
print(f'Matrix {iteration}: shape={{c.shape}}, sum={{c.sum():.4f}}')
"""


class CPUSklearnScenario(BaseScenario):
    """ML model training with sklearn."""

    id = "cpu_sklearn"
    name = "CPU Sklearn"
    description = "Machine learning model training"
    category = "cpu"
    language = "py"
    expected_latency_range = (200, 3000)

    def get_code(self, iteration: int = 0) -> str:
        return f"""
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.datasets import make_classification

X, y = make_classification(n_samples=500, n_features=20, n_informative=10, random_state={iteration})
model = RandomForestClassifier(n_estimators=50, random_state={iteration})
model.fit(X, y)
score = model.score(X, y)
print(f'Iteration {iteration}: RandomForest score={{score:.4f}}')
"""


class CPUPrimeScenario(BaseScenario):
    """Prime number computation."""

    id = "cpu_prime"
    name = "CPU Prime"
    description = "Prime number calculation"
    category = "cpu"
    language = "py"
    expected_latency_range = (50, 500)

    def get_code(self, iteration: int = 0) -> str:
        return f"""
def is_prime(n):
    if n < 2:
        return False
    for i in range(2, int(n**0.5) + 1):
        if n % i == 0:
            return False
    return True

primes = [n for n in range(10000) if is_prime(n)]
print(f'Iteration {iteration}: Found {{len(primes)}} primes, largest={{primes[-1]}}')
"""


class CPUFibonacciScenario(BaseScenario):
    """Fibonacci sequence computation."""

    id = "cpu_fibonacci"
    name = "CPU Fibonacci"
    description = "Fibonacci sequence computation"
    category = "cpu"
    language = "py"
    expected_latency_range = (30, 300)

    def get_code(self, iteration: int = 0) -> str:
        return f"""
def fib(n):
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a

result = fib(10000 + {iteration})
print(f'Iteration {iteration}: fib={{str(result)[:50]}}...')
"""


class QuotaHoldScenario(BaseScenario):
    """Hold an execution slot long enough to verify quota rejection."""

    id = "quota_hold"
    name = "Tenant Quota Hold"
    description = "Sleep for one second to create overlapping executions"
    category = "control"
    language = "py"
    expected_latency_range = (1000, 3000)

    def get_code(self, iteration: int = 0) -> str:
        return f"import time; time.sleep(1); print('quota iteration {iteration}')"
