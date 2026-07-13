# Ad Placement Optimization

Write `solution.cpp` in the project root. This workspace mirrors the current
EdgeBench `ad_placement_optimization` work contract while keeping all fixture
cases public and synthetic.

Place rectangular ads for `n` companies on a `10000 x 10000` grid. Company `i`
requires rectangle `i` to contain `(x_i + 0.5, y_i + 0.5)`, and its satisfaction
is highest when the rectangle area is close to `r_i`. Rectangles must use
integer coordinates, remain inside the grid, and not overlap.

For a valid rectangle with area `s_i`, satisfaction is
`1 - (1 - min(r_i, s_i) / max(r_i, s_i))^2`. The tester reports the nearest
integer to `1,000,000,000 * sum(satisfaction) / n`.

## Input

```text
n
x_0 y_0 r_0
...
x_{n-1} y_{n-1} r_{n-1}
```

## Output

Print one rectangle per ad in input order:

```text
x1_0 y1_0 x2_0 y2_0
...
x1_{n-1} y1_{n-1} x2_{n-1} y2_{n-1}
```

## Local testing

The generator accepts a seed file, not a raw seed:

```bash
printf '0\n' > /tmp/seeds.txt
rm -rf /tmp/ad_cases
./tools/bin/gen /tmp/seeds.txt -d /tmp/ad_cases
g++ -std=c++17 -O2 solution.cpp -o /tmp/ad_solution
/tmp/ad_solution < /tmp/ad_cases/0000.txt > /tmp/output.txt
./tools/bin/tester /tmp/ad_cases/0000.txt /tmp/output.txt
# stderr: Score = <number>
```

For multiple cases, write one seed per line, for example `seq 0 9 >
/tmp/seeds.txt`. The solver has a five-second limit per local case.
The EdgeBench work contract also specifies a 1 GB memory limit, no GPU, and no
internet access; this local fixture does not simulate a container-level memory
limit.

Only `solution.cpp` is submitted/editable. Do not modify `tools/` or
`.goal-plus-verifiers/`. A hidden EdgeBench judge is deliberately not included.
