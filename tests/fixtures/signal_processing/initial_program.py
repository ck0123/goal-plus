# EVOLVE-BLOCK-START
"""Baseline causal filter for non-stationary one-dimensional signals."""

import math


def process_signal(input_signal, window_size=20):
    """Return a filtered signal with fixed causal-window latency."""
    values = [float(value) for value in input_signal]
    if len(values) < window_size:
        return []

    output = []
    previous = sum(values[:window_size]) / window_size
    alpha = 0.28

    for index in range(window_size - 1, len(values)):
        window = values[index - window_size + 1 : index + 1]
        weighted = 0.0
        total_weight = 0.0
        for offset, value in enumerate(window):
            weight = math.exp((offset - window_size + 1) / max(1.0, window_size / 3.0))
            weighted += value * weight
            total_weight += weight
        window_estimate = weighted / total_weight
        previous = alpha * window_estimate + (1.0 - alpha) * previous
        output.append(previous)

    return output


# EVOLVE-BLOCK-END


def run_signal_processing(input_signal=None, signal_length=400, noise_level=0.3, window_size=20):
    if input_signal is None:
        input_signal = [
            math.sin(2.0 * math.pi * 0.03 * index) + noise_level * math.sin(index)
            for index in range(signal_length)
        ]
    return {"filtered_signal": process_signal(input_signal, window_size)}


if __name__ == "__main__":
    result = run_signal_processing()
    print(f"filtered={len(result['filtered_signal'])}")
