#!/bin/sh
# Wrapper for Rust's remote-test-client to discard exit status.
# This is neeed because cargo aborts tests if it sees a non-zero status, but
# we want it to keep going because lots of our benchmarks get SIGPROT half way
# through.
# Otherwise, we could just call the test client directly from cargo, which
# would be a bit cleaner.
# This script expects the path to the test client as its first argument,
# followed by the benchmark to run.
# This save duplicating path information.

CLIENT="$1"
shift 1 # remove first argmuent from "$@"
"$CLIENT" run 0 "$@"
exit 0
