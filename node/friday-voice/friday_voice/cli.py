"""`friday-voice` console script (Phase 11 brief D16).

    friday-voice run                      serve one host over stdin/stdout
    friday-voice conformance [--check | --build | --live]
    friday-voice conformance-child --case <id> [--cases <dir>] [--time-scale <f>]
    friday-voice replay | selftest | wake-eval   (branches 3 and 6)

`run` takes no configuration: the host hands the child everything over
stdin (`hello`, `credential`, ...). Never pass a credential in argv or the
environment (brief D3).
"""

import argparse
import os
import sys

LATER = {
    "replay": "feat/p11-pipecat-pipeline",
    "selftest": "feat/p11-wake-eval-harness",
    "wake-eval": "feat/p11-wake-eval-harness",
}


def _run(_args) -> int:
    from .child import protect_stdout, run_stdio
    from .ports import RealClock

    stdout = protect_stdout()
    code = run_stdio(sys.stdin.buffer, stdout, RealClock())
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

    for name in LATER:
        sub.add_parser(name, help=f"lands in {LATER[name]}").set_defaults(func=_later)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
