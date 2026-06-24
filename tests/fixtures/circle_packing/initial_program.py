# EVOLVE-BLOCK-START
"""Constructive circle packing baseline for 26 circles in a unit square."""

import math


def construct_packing():
    """Return centers, radii, and radius sum for 26 non-overlapping circles."""
    centers = [(0.5, 0.5)]

    for index in range(8):
        angle = 2.0 * math.pi * index / 8.0
        centers.append((0.5 + 0.28 * math.cos(angle), 0.5 + 0.28 * math.sin(angle)))

    for index in range(16):
        angle = 2.0 * math.pi * index / 16.0
        centers.append((0.5 + 0.46 * math.cos(angle), 0.5 + 0.46 * math.sin(angle)))

    centers.append((0.05, 0.05))
    centers = [(min(0.99, max(0.01, x)), min(0.99, max(0.01, y))) for x, y in centers]
    radii = compute_max_radii(centers)
    return centers, radii, sum(radii)


def compute_max_radii(centers):
    radii = [min(x, y, 1.0 - x, 1.0 - y) for x, y in centers]

    for _ in range(4):
        for left in range(len(centers)):
            for right in range(left + 1, len(centers)):
                lx, ly = centers[left]
                rx, ry = centers[right]
                distance = math.hypot(lx - rx, ly - ry)
                limit = max(0.0, distance - 1e-9)
                current = radii[left] + radii[right]
                if current > limit and current > 0.0:
                    scale = limit / current
                    radii[left] *= scale
                    radii[right] *= scale

    return radii


# EVOLVE-BLOCK-END


def run_packing():
    return construct_packing()


if __name__ == "__main__":
    _centers, _radii, _sum_radii = run_packing()
    print(f"sum_radii={_sum_radii:.6f}")
