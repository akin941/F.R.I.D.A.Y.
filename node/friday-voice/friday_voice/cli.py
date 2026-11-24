"""`friday-voice` console script (Phase 11 brief D16).

    friday-voice run                      serve one host over stdin/stdout
    friday-voice conformance [--check | --build | --live]
    friday-voice conformance-child --case <id> [--cases <dir>] [--time-scale <f>]
    friday-voice replay [--check | --write-baseline | --server <url> --token-file <path>]
    friday-voice selftest | wake-eval   (branch 6)

`run` takes no configuration: the host hands the child everything over
stdin (`hello`, `credential`, ...). Never pass a credential in argv or the
environment (brief D3).
"""

import argparse
import os
import sys

LATER = {
    "selftest": "feat/p11-wake-eval-harness",
    "wake-eval": "feat/p11-wake-eval-harness",
}


def make_run_pipeline(clock):
    """The pipeline `friday-voice run` serves: Friday's HTTPS client on a
    thread worker. No detector is loaded in branch 3: push-to-talk needs
    none, and the arming policy refuses always-on until branch 5 (which
    loads Silero, smart-turn v3 and openWakeWord from the verified model
    directory before it may arm)."""
    from .client import HttpFridayClient
    from .pipeline import RefuseAlwaysOn, VoicePipeline
    from .workers import ThreadWorker

    def build(post):
        return VoicePipeline(clock, HttpFridayClient(), ThreadWorker(post), arming=RefuseAlwaysOn())

    return build


def _run(_args) -> int:
    from .child import protect_stdout, run_stdio
    from .ports import RealClock

    stdout = protect_stdout()
    clock = RealClock()
    code = run_stdio(sys.stdin.buffer, stdout, clock, make_pipeline=make_run_pipeline(clock))
    sys.stderr.flush()
    # The stdin reader thread may still be blocked in read(): leave without
    # the interpreter's shutdown sequence, which would abort on its lock.
    os._exit(code)


def _conformance(args) -> int:
    from .conformance import build, runner

    if args.build:
        for path in build.write():
            print(f"wrote {path.relative_to(build.PACKAGE_DIR.parent)}")
        return 0
    if args.check:
        drift = build.check()
        for path in drift:
            print(f"drift: {path}")
        if drift:
            print("conformance files are stale: run `friday-voice conformance --build`.")
            return 1
        print("friday-voice conformance files: up to date")
        return 0
    failed = 0
    families = (
        ("host", runner.run_host_case, runner.run_host_case_live),
        ("child", runner.run_child_case, runner.run_child_case_live),
    )
    for family, in_process, live in families:
        cases = build.load_cases(family)
        passed = skipped = 0
        for case in cases:
            failures = (live if args.live else in_process)(case)
            if failures == ["skipped: manual clock"]:
                skipped += 1
            elif failures:
                failed += 1
                print(f"FAIL {case['id']}")
                for failure in failures:
                    print(f"  {failure}")
            else:
                passed += 1
        mode = "live" if args.live else "reference"
        note = f", {skipped} skipped (manual clock)" if skipped else ""
        print(f"{family} cases ({mode}): {passed}/{len(cases) - skipped} pass{note}")
    print("all-pass" if not failed else f"{failed} case(s) failed")
    return 1 if failed else 0


def _replay(args) -> int:
    from . import replay

    if args.server:
        if not args.token_file:
            print("friday-voice replay --server needs --token-file", file=sys.stderr)
            return 2
        with open(args.token_file, encoding="utf-8") as handle:
            token = handle.read().strip()
        return replay.run_server(args.server, token, ca_file=args.ca_file, ca_sha256=args.ca_sha256)
    results = [replay.run_scenario(scenario) for scenario in replay.load_scenarios()]
    current = replay.report(results)
    for result in results:
        stages = " ".join(f"{k}={v}" for k, v in sorted(result["stages"].items()))
        print(f"{result['id']}: {result['played_utterances']} sentence(s) {stages}")
    print("stage                    p50     p95")
    for stage, values in current["stages"].items():
        print(f"{stage:<22} {values['p50']:>6}  {values['p95']:>6}")
    if args.write_baseline:
        replay.BASELINE_PATH.write_text(replay.render_baseline(current), encoding="utf-8")
        print(f"wrote {replay.BASELINE_PATH.name}")
        return 0
    failed = [r["id"] for r in results if r["turns"] != 1 or not r["played_utterances"]]
    for scenario_id in failed:
        print(f"FAIL {scenario_id}: the turn did not complete with audio")
    failures = replay.check(current, replay.load_baseline()) if args.check else []
    for failure in failures:
        print(f"FAIL {failure}")
    return 1 if failed or failures else 0


def _conformance_child(args) -> int:
    from .conformance import hostile

    return hostile.main(args.case, args.cases, args.time_scale, args.settle_ms)


def _later(args) -> int:
    print(f"friday-voice {args.command}: lands in {LATER[args.command]}", file=sys.stderr)
    return 2


def build_parser():
    parser = argparse.ArgumentParser(prog="friday-voice")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="serve one host over stdin/stdout").set_defaults(func=_run)

    conformance = sub.add_parser("conformance", help="run or check the friday-voice/v1 suite")
    group = conformance.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="fail when the files drift")
    group.add_argument("--build", action="store_true", help="write the case and vector files")
    group.add_argument("--live", action="store_true", help="run against real processes")
    conformance.set_defaults(func=_conformance)

    hostile = sub.add_parser("conformance-child", help="play the hostile child of one host case")
    hostile.add_argument("--case", required=True)
    hostile.add_argument("--cases", default=None, help="the cases directory (default: bundled)")
    hostile.add_argument("--time-scale", type=float, default=1.0)
    hostile.add_argument(
        "--settle-ms", type=int, default=300, help="pause at each expect so the host can check"
    )
    hostile.set_defaults(func=_conformance_child)

    replay = sub.add_parser(
        "replay", help="play the fixture set through the pipeline", allow_abbrev=False
    )
    group = replay.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="fail on an overhead regression")
    group.add_argument("--write-baseline", action="store_true", help="store the baseline")
    group.add_argument("--server", default=None, help="a running Friday (push-to-talk only)")
    replay.add_argument("--token-file", default=None, help="file holding a node_voice token")
    replay.add_argument("--ca-file", default=None, help="extra CA bundle for an https server")
    replay.add_argument("--ca-sha256", default=None, help="pinned CA fingerprint (https)")
    replay.set_defaults(func=_replay)

    for name in LATER:
        sub.add_parser(name, help=f"lands in {LATER[name]}").set_defaults(func=_later)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
