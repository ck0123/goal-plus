# Local Testing Tools

These public synthetic tools use the same command-line and file contracts as
EdgeBench's `ad_placement_optimization` work image.

The generator reads one integer seed per line and writes numbered task inputs:

```bash
printf '0\n' > /tmp/seeds.txt
rm -rf /tmp/ad_cases
./tools/bin/gen /tmp/seeds.txt -d /tmp/ad_cases
```

The tester reads one input file and one solver output file. A valid result exits
zero and writes `Score = <integer>` to stderr:

```bash
./tools/bin/tester /tmp/ad_cases/0000.txt output.txt
```

The generated cases are public deterministic fixture data. They are not the
official EdgeBench case distribution or hidden judge data.
