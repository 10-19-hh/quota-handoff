"""Command-line interface.

    quota-handoff status  (--fixture FILE | --demo) [--policy FILE] [--now TS] [--json]
    quota-handoff plan    (--fixture FILE | --demo) [--task NAME] [--prefer PROVIDER] [--policy FILE] [--now TS] [--json]
    quota-handoff handoff create --input FILE [--output FILE] [--json-output FILE] [--force]

Exit codes: 0 ok, 2 input/schema error, 3 uncertain / no recommendation, 4 safety gate refused.
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone

from quota_handoff import __version__
from quota_handoff.adapters.demo import DemoProvider
from quota_handoff.adapters.fixture import FixtureProvider
from quota_handoff.errors import EXIT_INPUT_ERROR, EXIT_OK, EXIT_UNCERTAIN, InputError, QuotaHandoffError
from quota_handoff.handoff import load_task, render_markdown, to_json_dict
from quota_handoff.jsonio import read_json_file, same_file, write_text_files
from quota_handoff.normalizer import PROVIDER_RE, parse_timestamp
from quota_handoff.policy import DEFAULT_POLICY, RECOMMENDED, assess_snapshot, load_policy, plan
from quota_handoff.report import plan_to_dict, plan_to_text, status_to_dict, status_to_text

TASK_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def _parse_now(value):
    if value is None:
        return datetime.now(timezone.utc).replace(microsecond=0)
    issues = []
    parsed = parse_timestamp(value, "--now", issues)
    if issues:
        raise InputError("invalid --now", issues)
    return parsed


def _load_policy(path):
    if path is None:
        return DEFAULT_POLICY
    return load_policy(read_json_file(path, "policy"))


def _dump_json(data):
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _snapshots(args, now):
    if args.demo:
        return DemoProvider(now).load()
    return FixtureProvider(args.fixture).load()


def cmd_status(args, out):
    policy = _load_policy(args.policy)
    now = _parse_now(args.now)
    snapshots = _snapshots(args, now)
    assessments = [assess_snapshot(s, now, policy) for s in snapshots]
    if args.json:
        out.write(_dump_json(status_to_dict(assessments, now, policy, demo=args.demo)))
    else:
        out.write(status_to_text(assessments, now, policy, demo=args.demo))
    return EXIT_OK


def cmd_plan(args, out):
    if args.task is not None and not TASK_RE.match(args.task):
        raise InputError(f"--task must match {TASK_RE.pattern}")
    if args.prefer is not None and not PROVIDER_RE.match(args.prefer):
        raise InputError(f"--prefer must match {PROVIDER_RE.pattern}")
    policy = _load_policy(args.policy)
    now = _parse_now(args.now)
    snapshots = _snapshots(args, now)
    result = plan(snapshots, now, task=args.task, prefer=args.prefer, policy=policy)
    if args.json:
        out.write(_dump_json(plan_to_dict(result, now, args.task, demo=args.demo)))
    else:
        out.write(plan_to_text(result, now, args.task, demo=args.demo))
    return EXIT_OK if result.decision == RECOMMENDED else EXIT_UNCERTAIN


def cmd_handoff_create(args, out, err):
    task = load_task(read_json_file(args.input, "task"), "task")
    markdown = render_markdown(task)
    if task.redactions:
        err.write(
            "warning: masked {} value(s) matching secret/home-path patterns in: {}. "
            "Masking is pattern-based and not a guarantee; review before sharing.\n".format(
                len(task.redactions), ", ".join(sorted({p for p, _ in task.redactions}))
            )
        )
    outputs = []
    if args.output:
        outputs.append((args.output, markdown))
    if args.json_output:
        outputs.append((args.json_output, _dump_json(to_json_dict(task))))
    # All targets are checked before anything is written; the input file is never a valid target.
    write_text_files(outputs, force=args.force, protected=[args.input])
    for path, _ in outputs:
        err.write(f"wrote {path}\n")
    if not args.output:
        out.write(markdown)
    return EXIT_OK


_same_path = same_file  # kept for existing tests and callers


def build_parser():
    parser = argparse.ArgumentParser(
        prog="quota-handoff",
        description="Read-only usage status, explainable recommendation and manual handoff documents. "
        "Never runs agents, calls model APIs or reads credentials.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def usage_args(p):
        source = p.add_mutually_exclusive_group(required=True)
        source.add_argument("--fixture", help="usage snapshot JSON file (schema_version 0.1 or 0.2)")
        source.add_argument("--demo", action="store_true",
                            help="use built-in synthetic usage observed 30s before --now (clearly marked DEMO)")
        p.add_argument("--policy", help="policy JSON file (default: built-in 80/95%%, 300s freshness)")
        p.add_argument("--now", help="evaluation time, ISO 8601 with offset (default: current time); "
                                     "use to replay a fixture deterministically")
        p.add_argument("--json", action="store_true", help="machine-readable output")

    p_status = sub.add_parser("status", help="show normalized usage with source and freshness")
    usage_args(p_status)
    p_status.set_defaults(handler=lambda a, o, e: cmd_status(a, o))

    p_plan = sub.add_parser("plan", help="deterministic recommendation with reasons (manual approval required)")
    usage_args(p_plan)
    p_plan.add_argument("--task", help="task label, used to look up policy task_preferences (e.g. code)")
    p_plan.add_argument("--prefer", help="preferred provider; applied only among safe candidates")
    p_plan.set_defaults(handler=lambda a, o, e: cmd_plan(a, o))

    p_handoff = sub.add_parser("handoff", help="handoff documents")
    handoff_sub = p_handoff.add_subparsers(dest="handoff_command", required=True)
    p_create = handoff_sub.add_parser("create", help="render a handoff document from a task JSON file")
    p_create.add_argument("--input", required=True, help="user-written task/checkpoint JSON")
    p_create.add_argument("--output", help="Markdown output file (default: stdout)")
    p_create.add_argument("--json-output", help="also write the normalized handoff JSON to this file")
    p_create.add_argument("--force", action="store_true", help="allow replacing existing output files")
    p_create.set_defaults(handler=cmd_handoff_create)
    return parser


def main(argv=None, out=None, err=None):
    out = out or sys.stdout
    err = err or sys.stderr
    for stream in (out, err):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            try:
                reconfigure(errors="replace")
            except (ValueError, OSError):
                pass
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args, out, err)
    except QuotaHandoffError as exc:
        err.write(f"error: {exc}\n")
        return exc.exit_code
    except (RecursionError, OverflowError, OSError) as exc:
        # Last-resort guard for abnormal input: no traceback, and only the exception type is shown,
        # so no input text can be echoed. Known cases are reported more precisely before this.
        err.write(f"error: input could not be processed ({type(exc).__name__})\n")
        return EXIT_INPUT_ERROR


if __name__ == "__main__":
    sys.exit(main())
