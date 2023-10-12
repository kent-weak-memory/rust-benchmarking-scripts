This repository holds a few scripts we used to generate benchmark data for the paper Rust for Morello: Always-on Memory Safety, Even in Unsafe Code.
A lot of the code here was written in a hurry and has ended up being quite messy.
Running it may be troublesome, but hopefully it will at least provide some pointers as to what we did for anyone wanting to do anything similar.

The purpose of these scripts is to clone a number of Rust crates, apply compatibility patches where needed, build everything with our experimental Morello port of Rust, run benchmarks for every crate, and finally capture the benchmark results for analysis.

What the included files are:
- `run.py` automatically clones and runs benchmarks 
- `runner.sh` support script for `run.py` (see comments included in the script)
- `patches/` fixes applied by `run.py` to make some crates build
- `process.hs` analyses results produced by `run.py`
- `plotdata.gpi` TODO: I (seharris) am not sure what this is for exactly

This repository was put together several months after we ran this experiment.
Lots of this information is scraped together from memory, outdated scraps of documentation, and skimming code, so it may be wrong in places.

# Dependencies
- Python 3
- Haskell
- some implementation of a Unix shell (sh, bash, dash, etc)
- clone of our Morello Rust compiler
- some reasonably mundane build machine (x86 Linux, aarch64 Mac OS, and so on)
- Morello target machine

# Using `run.py`
`run.py` generates benchmarks for four configurations:

- hybrid mode (plain aarch64) with bounds checks
- hybrid mode (plain aarch64) without bounds checks
- purecap mode (Morello) with bounds checks
- purecap mode (Morello) without bounds checks

Benchmarks hosted on Git will be cloned, the compiler and tools built, and
each benchmark built and run.
Cloned Git benchmarks can optionally have a patch applied to work around
compatibility problems.

Logs of output from each benchmark will be written to `<mode>-output.log` in
the project's directory.
A CSV file containing data will be written to `./tmp/`.

Before running, make sure you set `rust_path` at line 63 of `run.py` to the
path to your clone of the Rust compiler repository.

Build the remote test server program from `src/tools/remote-test-server-deoxidised` and copy it to your Morello machine.
Start it up.
Set up some way to forward connections to TCP port 12345 from your build machine to your Morello machine.
We used `ssh`.

`cd` to this repository and then `python run.py`.
The script should clone all the benchmark crates and start compiling and running benchmarks.
The number of compiler rebuilds and test suites make this process long-winded (several hours), be prepared.
Loss of connection to the target machine is likely to break benchmarking, a stable connection is recommended.
If using `ssh`, you may want to set `ServerAliveInterval` to, say, 60 to stop idle timeout (`ssh -o ServerAliveInterval=60 ...`)
Note that all failures in the test client are ignored, so failure to connect will show up as "benchmark <whatever> generated no results", and the benchmark's `<mode>-output.log` will contain one or more "failed with connection refused" warnings.

While `run.py` has facilities to run some analyses and produce graphs, we didn't use these for the paper, and they may be broken.
Use `process.hs`.

# Using `process.hs`
TODO: say something useful here.
I (seharris) didn't use `process.hs`, so I don't know how it works.
Nothing very complicated happens here, we just grab the data and do some averages.
