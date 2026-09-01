from dataclasses import dataclass
from time import perf_counter_ns


@dataclass(frozen=True)
class LatencySample:
    started_ns: int
    finished_ns: int

    @property
    def milliseconds(self) -> float:
        return (self.finished_ns - self.started_ns) / 1_000_000


def start_timer() -> int:
    return perf_counter_ns()


def finish_timer(started_ns: int) -> LatencySample:
    return LatencySample(started_ns=started_ns, finished_ns=perf_counter_ns())
