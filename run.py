#!/usr/bin/python3
from enum import Enum
import os
from os import path
import pickle
import platform
import re
import subprocess
import sys

# Maps platform names to compiler targets.
# Targets not in this list may work, we just haven't needed to add them yet.
SUPPORTED_PLATFORMS = {
	("Linux", "x86_64") : "x86_64-unknown-linux-gnu"
,   ("Darwin", "arm64") : "aarch64-apple-darwin"
}

TTY_RED="\033[31m" if sys.stdout.isatty() else ""
TTY_GREEN="\033[32m" if sys.stdout.isatty() else ""
TTY_RESET="\033[39m\033[49m" if sys.stdout.isatty() else ""

class RunMode(Enum):
	BENCH = 1
	BUILD = 2
	TEST = 3

run_mode = RunMode.BENCH

if "--build-only" in sys.argv:
	run_mode = RunMode.BUILD
if "--test-only" in sys.argv:
	run_mode = RunMode.TEST

skip_install = ("--skip-install" in sys.argv)
clone_only = ("--clone-only" in sys.argv)
do_plot = ("--plot" in sys.argv)

if "--help" in sys.argv:
	print("./run.py [OPTIONS]")
	print("  --build-only       Only build projects, do not run benchmarks")
	print("  --clone-only       Only clone projects, do not build or run benchmarks")
	print("  --clean            Clean all cloned git repos")
	print("  --plot             Build PGF plot")
	print("  --skip-install     Skip reinstalling Rust")
	print("  --test-only        Only build projects, do not run benchmarks")
	print("  --line-count       Count lines of code and write to CSV file")
	exit(0)

def print_result(result):
	if result.returncode == 0:
		print(f"{TTY_GREEN}OK{TTY_RESET}")
	else:
		print(f"{TTY_RED}FAIL{TTY_RESET}")
		exit(result.returncode)

target = SUPPORTED_PLATFORMS.get((platform.system(), platform.machine()))
if target is None:
	print("ERROR: unknown OS or hardware, please add target triple information for this host")
	exit(1)

# Path to the root of the Rust compiler directory.
# This should contain a clone of the compiler to use.
rust_path = "PLACEHOLDER: replace with real path"
# Directory containing benchmarks, assumed to be current working directory.
benchmark_path = os.getcwd()
# Directory containing patches, must be an absolute path.
patch_path = path.join(benchmark_path, "patches")
# Path to Rust compiler.
rustc = path.join(rust_path, "build/install-stage2-latest/bin/rustc")
# Path to Cargo.
cargo = path.join(rust_path, "build/install-stage2-latest/bin/cargo")
# Path to remote test client, note that this is called via the runner.sh wrapper.
# See comments in the wrapper for explanation.
test_client = path.join(rust_path, f"build/{target}/stage0-bootstrap-tools/{target}/release/remote-test-client")
# Path to runner script.
runner = path.join(benchmark_path, "runner.sh")
# Linker to use for Morello hybrid mode.
aarch64_linker = path.join(rust_path, "clang-freebsd.sh")
# Linker to use for Morello purecap mode.
purecap_linker = path.join(rust_path, "clang-morello.sh")
# Regex to find benchmark result lines in Cargo output.
data_regex = re.compile(r"\ntest ([^ ]+) +\.\.\. bench: +([0-9,]+) ns/iter \(\+/- ([0-9,]+)\)")
# Path to cargo count binary.
# Cargo count is available from: https://github.com/kbknapp/cargo-count 
# This is a bit of dirty hack thrown together in a hurry so sorry it's a mess.
count_path = "/Users/simon/github/cargo-count/target/debug/cargo-count"
# Regex to find line count numbers in output.
count_regex = re.compile(r"\nTotals:[ \t]+([0-9]+)[ \t]+([0-9]+)[ \t]+([0-9]+)[ \t]+([0-9]+)[ \t]+([0-9]+)[ \t]+([0-9]+)[ \t]+\(.+%\)")


def run_cmd(cmd, cwd=benchmark_path, env=os.environ.copy()):
	result = subprocess.run(cmd, cwd=cwd, env=env, encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
	if result.returncode != 0:
		print("WARN: failed to run `{}' in `{}'".format(" ".join(cmd), cwd))
		print(result.stdout)
	return result


class Suite:
	def __init__(self, directory, repo=None, branch=None, patch=None, subprojects=[None], extra_bench_flags=[]):
		"""
		Suite represents a benchmark suite.

		directory -- Destination directory in `benchmark_path` to clone into or use (string).
		repo -- Git repository to clone (string), set to `None` to use an already existing directory.
		branch -- Git branch to checkout when cloning (string), set to `None` to use default branch.
		patch -- Patch file in `patch_path` to apply (string), set to `None` to skip.
		subprojects -- Subprojects to run benchmarks from (list of strings), set to `None` if subprojects are not in use.
		"""
		self.repo = repo
		self.branch = branch
		self.directory = directory
		self.patch_file = patch
		self.subprojects = subprojects
		self.extra_bench_flags = extra_bench_flags

		# Private
		self._cargo_env = os.environ.copy()
		assert(rustc.count(":") == 0)
		self._cargo_env["PATH"] = path.dirname(rustc)+":"+self._cargo_env.get("PATH", "")

	def log_path(self, configuration):
		return path.join(benchmark_path, self.directory, "{}-output.log".format(configuration.name))

	def clean(self):
		if self.repo is not None: 
			print("Removing {}... ".format(self.directory), end="")
			sys.stdout.flush()
			subprocess.run(["rm", "-rf", self.directory])
			print(f"{TTY_GREEN}OK{TTY_RESET}")
	
	def clone_and_patch(self):
		# Skip benchmarks that aren't from Git repositories.
		if self.repo is None:
			if self.patch_file is not None:
				print("WARN: patch specified for non-Git benchmark {}, ignored".format(suite.directory))
			return

		if (not os.path.isdir(path.join(benchmark_path, self.directory))):
			print("Cloning {}... ".format(self.directory), end="")
			sys.stdout.flush()
			cmd = ["git", "clone", self.repo]
			if self.branch is not None:
				cmd += ["--branch", self.branch]
			cmd += [self.directory]
			res = run_cmd(cmd, benchmark_path)
			print_result(res)
			if self.patch_file is not None:
				print("  applying patch {}... ".format(self.patch_file), end="")
				sys.stdout.flush()
				cmd = ["git", "apply", path.join(patch_path, self.patch_file)]
				res = run_cmd(cmd, cwd=path.join(benchmark_path, self.directory))
				print_result(res)

	# Count lines of code in test repository.
	# Returns (total lines, lines of code (excludes comments, blanks, unsafe), lines of unsafe).
	# Needs cargo count to have been built.
	def line_count(self):
		# TODO: it would be nice to count lines in dependencies via cargo tree.
		# TODO: handle subprojects.
		if len(self.subprojects) != 0 and self.subprojects != [None]:
			print("WARN: subprojects not supported for line counting")
			return (0, 0, 0)

		print("Counting {}...".format(self.directory))
		result = run_cmd([count_path, "count", "--unsafe-statistics", "-l", "rs"],
			cwd=path.join(benchmark_path, self.directory),
			env=self._cargo_env
		)
		if result.returncode != 0:
			return (0, 0, 0)
		matches = count_regex.findall(result.stdout)
		if len(matches) != 1:
			print("WARN: wrong number of line count lines")
			return (0, 0, 0)
		match = matches[0]
		lines = int(match[1])
		code = int(match[4])
		unsafe = int(match[5])
		return (lines, code, unsafe)

	def cargo(self, configurtaion, cmd, extra_flags=[]):
		return run_cmd([cargo, cmd, "--target", configuration.target] + extra_flags,
			cwd=path.join(benchmark_path, self.directory),
			env=self._cargo_env
		)

	def build(self, configuration):
		self.cargo(configuration, "build")
	
	def test(self, configuration):
		return self.cargo(configuration, "test")

	def bench(self, configuration):
		output = self.cargo(configuration, "bench", extra_flags=self.extra_bench_flags)
		# Write output to log file for debugging.
		with open(self.log_path(configuration), "wb") as file:
			file.write(bytes(output.stdout, "utf-8"))

	def parse_bench_output(self, configuration, results):
		# Parse output to find results for each benchmark in this suite.
		with open(self.log_path(configuration), "rb") as file:
			output = file.read().decode("utf-8")

		success = False
		for item in data_regex.finditer(output):
			success = True

			# Extract data.
			bench_name = item.group(1)
			name = f"{directory}/{bench_name}"
			time = int(item.group(2).replace(",", ""))
			time_range = int(item.group(3).replace(",", ""))

			# Store data.
			benchmark_data = results.get(name, {})
			mode_data = benchmark_data.get(configuration.name, [])
			if len(mode_data) > round:
				print("ERROR: unexpected extra run of benchmark {}".format(name))
				# exit(1)
			else:
				mode_data.append((time, time_range))
				benchmark_data[configuration.name] = mode_data
				results[name] = benchmark_data
			assert(len(mode_data) == round+1)
		if not success:
			print("ERROR: benchmark suite {} generated no results".format(directory))
			print(output)
			# exit(1)

class Configuration:
	"""
	Run Configuration

	name -- short-hand name for this config
	target -- target tripple
	rust_flags -- rustc flags
	"""
	def __init__(self, name, target, rust_flags=""):
		self.name = name
		self.target = target
		self.rust_flags = rust_flags
	
	def write_cargo_config(self):
		cargo_config_dir = path.join(benchmark_path, ".cargo")
		if not os.path.exists(cargo_config_dir):
			os.mkdir(cargo_config_dir)
		with open(path.join(cargo_config_dir, "config.toml"), "w") as cargo_config_file:
			cargo_config_file.write("\n".join([
				"""[build]""",
				"""rustflags="{}\"""".format(self.rust_flags),
				"",
				"""[target.aarch64-unknown-freebsd]""",
				"""runner = "{} {}\"""".format(runner, test_client),
				"""linker = "{}\"""".format(aarch64_linker),
				"",
				"""[target.aarch64-unknown-freebsd-purecap]""",
				"""runner = "{} {}\"""".format(runner, test_client),
				"""linker = "{}\"""".format(purecap_linker),
			]))

	
	# Ensure compiler and tools have been built.
	def build_rust(self):
		if skip_install:
			return
		env = os.environ.copy()
		env["RUSTFLAGS_STAGE_NOT_0"] = self.rust_flags

		x = path.join(rust_path, "x.py")
		print("Building Rust... ", end="")
		sys.stdout.flush()
		res = run_cmd(["python3", x, "build", "std", "core", "rustc", "cargo"], cwd=rust_path, env=env)
		print_result(res)

		print("Building remote-test-client... ", end="")
		sys.stdout.flush()
		res = run_cmd(["python3", x, "build", "src/tools/remote-test-client", "--target", target], cwd=rust_path)
		print_result(res)

		print("Installing Rust compiler... ", end="")
		sys.stdout.flush()
		print_result(run_cmd(["python3", x, "install"], cwd=rust_path, env=env))

		print("Installing Rust tools... ", end="")
		sys.stdout.flush()
		print_result(run_cmd(["python3", x, "install", 
			"cargo", "library/std"
			], cwd=rust_path, env=env))


working = [
	Suite(repo="https://github.com/bluss/arrayvec", branch="0.7.2", directory="arrayvec-0.7.2"),
	# TODO: unexpected extra run of benchmark block-ciphers/aes/{encrypt,decrypt}
	Suite(repo="https://github.com/RustCrypto/block-ciphers", branch="aes-v0.7.2", directory="block-ciphers", subprojects=["aes"]),
	Suite(repo="https://github.com/rust-lang/hashbrown", branch="v0.11.2", directory="hashbrown-0.11.2"),
	Suite(repo="https://github.com/RustCrypto/hashes", branch="sha2-v0.10.2", directory="hashes-sha2-v0.10.2", subprojects=["sha2", "sha3"]),
	Suite(repo="https://github.com/bluss/indexmap", branch="1.8.2", directory="indexmap-1.8.2", patch="indexmap-1.8.2.patch"),
	Suite(repo="https://github.com/dtolnay/itoa", branch="1.0.3", directory="itoa-1.0.3"),
	# TODO: LTO needs to be disabled (??)
	Suite(repo="https://github.com/johannesvollmer/lebe", branch="0.5.0", directory="lebe-0.5.0", patch="lebe-0.5.0.patch"),
	Suite(repo="https://github.com/bluss/matrixmultiply/", branch="0.3.2", directory="matrixmultiply-0.3.2"),

	# TODO:
	# ERROR: unexpected extra run of benchmark ndarray-0.15.6/map_regular
	# ERROR: unexpected extra run of benchmark ndarray-0.15.6/iter_sum_2d_cutout
	# ERROR: unexpected extra run of benchmark ndarray-0.15.6/iter_sum_2d_regular
	Suite(repo="https://github.com/rust-ndarray/ndarray", branch="0.15.6", directory="ndarray-0.15.6"),
	Suite(repo="https://github.com/rust-num/num-bigint", branch="num-bigint-0.4.3", directory="num-bigint-0.4.3"),

	# TODO:
	# ERROR: unexpected extra run of benchmark petgraph-0.6.0/full_edges_in
	# ERROR: unexpected extra run of benchmark petgraph-0.6.0/full_edges_out
	Suite(repo="https://github.com/petgraph/petgraph", branch="0.6.0", directory="petgraph-0.6.0"),

	# TODO: Probably working, very slow!
	# Suite(repo="https://github.com/rust-random/rand", branch="0.8.5", directory="rand-0.8.5"),

	Suite(repo="https://github.com/paupino/rust-decimal", branch="1.23.1", directory="rust-decimal-1.23.1", patch="rust-decimal-1.23.1.patch"),
	Suite(repo="https://github.com/mgeisler/smawk", branch="0.2.0", directory="smawk-0.2.0"),
	Suite(repo="https://github.com/dtolnay/ryu", branch="1.0.12", directory="ryu-1.0.12"),
	Suite(repo="https://github.com/dguo/strsim-rs", branch="0.10.0", directory="strsim-rs-0.10.0"),
	# Suite(repo="https://github.com/rust-random/rand", branch="0.8.5", directory="rand-0.8.5"),
	Suite(repo="https://github.com/garro95/priority-queue", branch=None, directory="priority-queue-1.3.1", patch="priority-queue-1.3.1.patch", extra_bench_flags=["--features",  "benchmarks"]),
	Suite(repo="https://github.com/uuid-rs/uuid", branch="1.3.0", directory="uuid-rs-1.3.0", patch="uuid-rs-1.3.0.patch"),
	Suite(repo="https://github.com/petgraph/fixedbitset", branch="0.3.1", directory="fixedbitset-0.3.1"),
]
# Benchmarks to run.
suites = working
more = [
]

broken = [
	# No benches run not sure why

	# TODO: SIGPROT after 2 benches :(
	Suite(repo="https://github.com/hyperium/hyper", branch="v0.14.24", directory="hyper-0.14.24", patch="hyper-0.14.24.patch", extra_bench_flags=["--features", "full"]),

	# TODO: SIGPROT on Morello, not sure why
	Suite(repo="https://github.com/ejmahler/RustFFT", branch="6.0.1", directory="RustFFT-6.0.1"),

	# Critereon broken...
	Suite(repo="https://github.com/marshallpierce/rust-base64", branch="v0.13.1", directory="rust-base64-0.13.1"),
	Suite(repo="https://github.com/unicode-rs/unicode-xid", branch="v0.2.4", directory="unicode-xid-0.2.4"),
	Suite(repo="https://github.com/Lokathor/tinyvec", branch="v1.6.0", directory="tinyvec-1.6.0", extra_bench_flags=["--features", "alloc,real_blackbox"]),
	Suite(repo="https://github.com/seanmonstar/httparse", branch="v1.8.0", directory="httparse-1.8.0", patch="httparse-1.8.0.patch"),
	Suite(repo="https://github.com/bheisler/criterion.rs", branch="0.3.6", directory="criterion-0.3.6"),
	Suite(repo="https://github.com/rust-itertools/itertools", branch="v0.10.4", directory="itertools-0.10.4", patch="itertools-0.10.4.patch"),
	Suite(repo="https://github.com/hyperium/http", branch="v0.2.9", directory="http-0.2.9"),

	# TODO: Critereon broken, LTO needs to be disabled (??).
	Suite(repo="https://github.com/dimforge/nalgebra", branch="v0.31.1", directory="nalgebra-0.31.1", extra_bench_flags=["--features", "rand"]),


	# No benches
	Suite(repo="https://github.com/bytecodealliance/wasm-tools", branch="wasm-smith-0.4.4", directory="wasm-tools-0.4.4"),

	# Requires AVX2
	Suite(repo="https://github.com/binaryfields/resid-rs", branch="1.0.4", directory="resid-rs-1.04"),

	# Various dependency problems 
	Suite(repo="https://github.com/dtolnay/syn", branch="1.0.101", directory="syn-1.0.101"),


	# TODO: Something is broken in Critereon, otherwise the tests work :(
	Suite(repo="https://github.com/fitzgen/generational-arena", branch="0.2.8", directory="generational-arena-0.2.8"),

	# Not sure what's going on here...
	Suite(repo="https://github.com/serde-rs/serde", branch="v1.0.125", directory="serde-1.0.125"),

	# Illegal transmute using once_cell 
	Suite(repo="https://github.com/serde-rs/json", branch="v1.0.91", directory="serde-json-1.0.91"),

	# Illegal transmute in wait-timeout
	Suite(repo="https://github.com/vorner/arc-swap", branch="v1.5.1", directory="arc-swap-1.5.1"),

	# Illegal transmute in dependencies
	Suite(repo="https://github.com/zesterer/flume", branch=None, directory="flume"),

	# Casts int to PC, invalid on Morello:
	Suite(repo="https://github.com/rust-lang/backtrace-rs", branch="0.3.66", directory="backtrace-rs-0.3.66"),
	# Dependency requires edition2021:
	# Directory name included version 0.10.14 but without a branch this isn't guaranteed, so it's removed for the moment.

	# Need to pin once_cell at "=1.8.0", proc-macro2 at version "=1.0.42", inscrutable linker error:
	Suite(repo="https://github.com/briansmith/ring", branch="0.10.0", directory="ring-0.10.0"),
	# Fatal error: arm_neon.h: No such file or directory:
	Suite(repo="https://github.com/shssoichiro/oxipng", branch="v5.0.1", directory="oxipng-5.0.1"),
	# Need to pin thread local at 1.1.0, broken use of C ABI with transmute:
	Suite(repo="https://github.com/dalance/amber", branch="v0.5.8", directory="amber-0.5.8"),
	# Broken use of C ABI with transmute:
	Suite(repo="https://github.com/Canop/broot", branch="v1.3.1", directory="broot-1.3.1"),
	# Broken use of C ABI with transmute:
	Suite(repo="https://github.com/kivikakk/comrak", branch="0.14.0", directory="comrak-0.14.0"),
	# Build seems cooked, unclear why:
	Suite(repo="https://github.com/connorskees/grass", branch="bd83410a8af0c97da78f88b44f8e08682dc47658", directory="grass-0.11.2"),
]

# Number of times to run each benchmark (averages are calculated).
benchmark_rounds = 3
# Path to write results out to.
output_path = "./tmp/"
# Paths to write debugging information to.
debug_readable = "./tmp/benchmark_raw.log"
debug_raw_data = "./tmp/benchmark_raw.pickle"

# Clean up if asked.
if "--clean" in sys.argv:
	for suite in suites:
		suite.clean()
	exit(0)

# Fetch all the repos, apply patches.
for suite in suites:
	suite.clone_and_patch()
if clone_only:
	exit(0)

# Do line count.
if "--line-count" in sys.argv:
	with open(path.join(output_path, "line_count.csv"), "w") as file:
		# Write headers.
		file.write("Benchmark, Total lines, Code lines, Unsafe lines\n")
		# Write statistics.
		for suite in suites:
			(total, code, unsafe) = suite.line_count()
			if suite.directory.count(",") != 0:
				print("ERROR: can't use benchmark name in CSV due to comma")
				exit(1)
			file.write("{}, {}, {}, {}\n".format(suite.directory, total, code, unsafe))
	exit(0);

# Map from benchmark name to data map.
# Data map in turn maps compiler configuration name to times.
# Times is a list of (time, range) pairs, of length `benchmark_rounds`.
# Note that benchmark name means a *single* benchmark, not a benchmark suite
# given in `suites`.
results = {}

# Iterate over compiler configurations, rebuilding libraries and benchmarks.
# The order in which these loops are nested matters. We take advantage of Cargo
# caching builds of libraries and std to save time, and the cache is
# invalidated every time we change compiler options. For efficient
# benchmarking, we want to run all benchmarks for a given compiler
# configuration before using another configuration.
configurations = [
	Configuration("purecap-bounds", "aarch64-unknown-freebsd-purecap", ""),
	Configuration("purecap-nobounds", "aarch64-unknown-freebsd-purecap", "-C drop-bounds-checks=yes"),
	Configuration("hybrid-bounds", "aarch64-unknown-freebsd", ""),
	Configuration("hybrid-nobounds", "aarch64-unknown-freebsd", "-C drop-bounds-checks=yes"),
]
for configuration in configurations:
	# Build and run benchmarks.
	configuration.build_rust()
	for suite in suites:
		subprojects = [None] if suite.subprojects is None else suite.subprojects
		for subproject in subprojects:
			directory = suite.directory if subproject is None else path.join(suite.directory, subproject)
			configuration.write_cargo_config()

			# Run suite multiple times for better accuracy.
			for round in range(benchmark_rounds):
				print("{:30s} {:20s} round {}".format(directory, configuration.name, round+1))
				if run_mode == RunMode.BUILD:
					res = suite.build(configuration)
					print_result(res)
				if run_mode == RunMode.TEST:
					res = suite.test(configuration)
					print(res.stdout)
				elif run_mode == RunMode.BENCH:
					suite.bench(configuration)
					suite.parse_bench_output(configuration, results)

# Dump intermediate results for debugging in case something crashes later.
# with open(debug_readable, "w") as file:
# 	file.write(str(results))
# with open(debug_raw_data, "wb") as file:
# 	pickle.dump(results, file)

# Write statistics to CSV file.
def write_stats():
	with open(path.join(output_path, "benchmark_data.csv"), "w") as file:
		# Write headers.
		top_line = "benchmark"
		bottom_line = " "
		for configuration in configurations:
			assert(benchmark_rounds > 0)
			first = True
			for _ in range(benchmark_rounds):
				if first:
					first = False
					top_line += ", {}, ".format(configuration.name)
				else:
					top_line += ", , "
				bottom_line += ", time/ns, +-/ns"
			top_line += ", , , "
			bottom_line += ", mean/ns, -/ns, +/ns"
		file.write(top_line+"\n"+bottom_line+"\n")

		# Write data.
		for benchmark, benchmark_data in results.items():
			file.write(benchmark)
			for configuration in configurations:
				# mode, target, flags
				# Collect data and statistics together, marking modes that crashed.
				mode_data = benchmark_data.get(configuration.name)
				if mode_data:
					assert(len(mode_data) == benchmark_rounds) # make sure data is sensible
					sum = 0
					min = None
					max = None
					for time, time_range in mode_data:
						sum += time
						low = time-time_range
						high = time+time_range
						if (not min) or low < min: min = low
						if (not max) or high > max: max = high
					mean = sum/benchmark_rounds
					assert(str(mean).find(",") == -1) # make sure locale doesn't break CSV file
					assert(min != None and max != None and max >= min)
					assert(min <= mean)
					assert(max >= mean)
					range_negative = mean-min
					range_positive = max-mean
				else:
					mode_data = [("-", "-")]*benchmark_rounds
					mean = "-"
					range_negative = "-"
					range_positive = "-"

				# Add data and statistics to file.
				for time, time_range in mode_data:
					file.write(", {}, {}".format(time, time_range))
				file.write(", {}, {}, {}".format(mean, range_negative, range_positive))
			file.write("\n")

# Write statistics to Pgfplots table file.
def plot_data():
	with open(path.join(output_path, "benchmark_data.dat"), "w") as file:
		# Write notes.
		file.write("# This is benchmark data formatted for rendering via LaTeX and Pgfplots.\n")
		file.write("# If you need the list of symbolic values used, copy this:\n")
		file.write("# symbolic y coords={")
		first = True
		for benchmark in results.keys():
			if first:
				first = False
			else:
				file.write(",")
			assert(benchmark.find(" ") == -1)
			file.write(benchmark.replace("_", "\\_"))
		file.write("}\n")

		# Get modes to compare to one baseline.
		assert(len(configurations) >= 2)
		baseline_mode = "hybrid-bounds"
		other_modes = []
		for mode, target, flags in configurations:
			if mode != baseline_mode:
				other_modes.append(mode)

		# Write headers.
		file.write("benchmark")
		for mode in other_modes:
			file.write(" {}-mean {}-error-negative {}-error-positive".format(mode, mode, mode))
		file.write("\n")

		# Write processed data.
		for benchmark, benchmark_data in results.items():
			# Skip benchmarks we didn't get a full set of results from.
			if any(map(lambda mode_data: mode_data is None, benchmark_data.values())):
				continue

			# Calculate means and error ranges for each mode.
			computed_data = {}
			for mode, target, flags in configurations:
				mode_data = benchmark_data[mode]
				assert(len(mode_data) == benchmark_rounds) # make sure data is sensible
				assert(benchmark_rounds > 0)
				sum = 0
				min = None
				max = None
				for time, time_range in mode_data:
					sum += time
					low = time-time_range
					high = time+time_range
					if (not min) or low < min: min = low
					if (not max) or high > max: max = high
				mean = sum/benchmark_rounds
				assert(min != None and max != None and max >= min)
				assert(min <= mean)
				assert(max >= mean)
				computed_data[mode] = (mean, min, max)

			# Write speedup relative to baseline to file.
			baseline_data = computed_data[baseline_mode]
			assert(benchmark.find(" ") == -1)
			file.write(benchmark.replace("_", "\\_"))
			for mode in other_modes:
				mode_data = computed_data[mode]
				mean = mode_data[0]/baseline_data[0]
				assert(mean > 0)
				min = None
				max = None
				for a in baseline_data:
					for b in mode_data:
						factor = 0 if a == 0 else b/a
						assert(factor > 0)
						if min is None or factor < min: min = factor
						if max is None or factor > max: max = factor
				assert(min is not None)
				assert(max is not None)
				assert(min <= mean)
				assert(max >= mean)
				range_negative = mean-min
				range_positive = max-mean

				# Make sure locale doesn't break table file.
				assert(str(mean).find(" ") == -1)
				assert(str(range_negative).find(" ") == -1)
				assert(str(range_positive).find(" ") == -1)
				file.write(" {} {} {}".format(mean, range_negative, range_positive))
			file.write("\n")

if run_mode is not RunMode.BENCH:
	exit(0)
elif run_mode is RunMode.BENCH:
	write_stats()
	if do_plot:
		plot_data()
