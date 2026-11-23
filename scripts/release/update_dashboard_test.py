#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Google LLC
#
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for signed gh-pages publishing in scripts/release/9-update_dashboard.sh."""

import base64
import json
import os
import stat
import subprocess
import tempfile
import unittest


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
UPDATE_DASHBOARD_SH = os.path.join(REPO_ROOT, "scripts", "release", "9-update_dashboard.sh")


MOCK_GH_SCRIPT = """#!/usr/bin/env python3
import json
import os
import sys

state_dir = os.environ["MOCK_GH_STATE_DIR"]
calls_file = os.path.join(state_dir, "calls.jsonl")
args = sys.argv[1:]

record = {"args": args}

if "--input" in args:
    idx = args.index("--input")
    with open(args[idx + 1], encoding="utf-8") as f:
        record["input"] = json.load(f)

with open(calls_file, "a", encoding="utf-8") as f:
    f.write(json.dumps(record) + "\\n")

# 1. Fetch remote data/runs.json
if any("contents/data/runs.json" in a for a in args):
    count_path = os.path.join(state_dir, "fetch_count")
    count = int(open(count_path).read().strip()) if os.path.exists(count_path) else 0
    count += 1
    with open(count_path, "w", encoding="utf-8") as f:
        f.write(str(count))
    runs_variant = os.path.join(state_dir, f"remote_runs_{count}.json")
    if not os.path.exists(runs_variant):
        runs_variant = os.path.join(state_dir, "remote_runs.json")
    with open(runs_variant, encoding="utf-8") as f:
        sys.stdout.write(f.read())
    sys.exit(0)

# 2. Fetch gh-pages HEAD OID
if any("git/ref/heads/" in a for a in args):
    count_path = os.path.join(state_dir, "ref_count")
    count = int(open(count_path).read().strip()) if os.path.exists(count_path) else 0
    count += 1
    with open(count_path, "w", encoding="utf-8") as f:
        f.write(str(count))
    oids = json.loads(os.environ.get("MOCK_GH_HEAD_OIDS", '["aaa1111"]'))
    oid = oids[min(count - 1, len(oids) - 1)]
    sys.stdout.write(oid + "\\n")
    sys.exit(0)

# 3. GraphQL createCommitOnBranch
if len(args) >= 2 and args[0] == "api" and args[1] == "graphql":
    count_path = os.path.join(state_dir, "graphql_count")
    count = int(open(count_path).read().strip()) if os.path.exists(count_path) else 0
    count += 1
    with open(count_path, "w", encoding="utf-8") as f:
        f.write(str(count))
    fail_times = int(os.environ.get("MOCK_GH_GRAPHQL_FAIL_TIMES", "0"))
    if count <= fail_times:
        sys.stderr.write("GraphQL: Expected branch to point to stale OID (createCommitOnBranch)\\n")
        sys.exit(1)
    resp = {
        "data": {
            "createCommitOnBranch": {
                "commit": {
                    "oid": "fff9999deadbeef",
                    "url": "https://github.com/gardener/gardener-gdc-ci/commit/fff9999deadbeef",
                    "signature": {"isValid": True},
                }
            }
        }
    }
    sys.stdout.write(json.dumps(resp) + "\\n")
    sys.exit(0)

# 4. Trigger GitHub Pages build
if any("pages/builds" in a for a in args):
    sys.stdout.write('{"status":"queued"}\\n')
    sys.exit(0)

sys.stderr.write(f"unexpected gh invocation: {args}\\n")
sys.exit(1)
"""


class UpdateDashboardTest(unittest.TestCase):
    def _setup_workspace(self, tmpdir: str, remote_runs: list[dict]) -> tuple[str, str, str, str]:
        bin_dir = os.path.join(tmpdir, "bin")
        state_dir = os.path.join(tmpdir, "state")
        dashboard_dir = os.path.join(tmpdir, "dashboard")
        os.makedirs(bin_dir)
        os.makedirs(state_dir)
        os.makedirs(os.path.join(dashboard_dir, "data"))

        mock_gh = os.path.join(bin_dir, "gh")
        with open(mock_gh, "w", encoding="utf-8") as f:
            f.write(MOCK_GH_SCRIPT)
        os.chmod(mock_gh, stat.S_IRWXU)

        with open(os.path.join(dashboard_dir, "index.html"), "w", encoding="utf-8") as f:
            f.write("<!doctype html><title>Dashboard</title>\n")
        with open(os.path.join(dashboard_dir, "data", "runs.json"), "w", encoding="utf-8") as f:
            f.write("[]\n")
        with open(os.path.join(state_dir, "remote_runs.json"), "w", encoding="utf-8") as f:
            json.dump(remote_runs, f)

        summary_file = os.path.join(tmpdir, "summary.md")
        return bin_dir, state_dir, dashboard_dir, summary_file

    def _run_script(
        self,
        bin_dir: str,
        state_dir: str,
        dashboard_dir: str,
        summary_file: str,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
        env["MOCK_GH_STATE_DIR"] = state_dir
        env["DASHBOARD_DIR"] = dashboard_dir
        env["DASHBOARD_DATA_FILE"] = os.path.join(dashboard_dir, "data", "runs.json")
        env["GITHUB_REPOSITORY"] = "gardener/gardener-gdc-ci"
        env["GITHUB_REF"] = "refs/heads/main"
        env["GH_TOKEN"] = "test-token"
        env["GITHUB_RUN_ID"] = "45152286982"
        env["GITHUB_RUN_NUMBER"] = "209"
        env["GITHUB_EVENT_NAME"] = "schedule"
        env["GITHUB_ACTOR"] = "dilipkumar2k6"
        env["OVERALL_PIPELINE_STATUS"] = "success"
        env["PIPELINE_DURATION"] = "1h 54m 1s"
        env["RELEASE_METADATA_FILE"] = os.path.join(state_dir, "nonexistent-metadata.yaml")
        env["GITHUB_STEP_SUMMARY"] = summary_file
        if extra_env:
            env.update(extra_env)

        return subprocess.run(
            ["bash", UPDATE_DASHBOARD_SH],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

    def _read_calls(self, state_dir: str) -> list[dict]:
        calls_file = os.path.join(state_dir, "calls.jsonl")
        if not os.path.exists(calls_file):
            return []
        with open(calls_file, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def test_publishes_signed_commit_via_graphql_and_triggers_pages_build(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            remote_runs = [{"runId": "44281341296", "runNumber": "206", "status": "failure"}]
            bin_dir, state_dir, dashboard_dir, summary_file = self._setup_workspace(tmpdir, remote_runs)

            res = self._run_script(bin_dir, state_dir, dashboard_dir, summary_file)
            self.assertEqual(res.returncode, 0, msg=res.stderr)
            self.assertIn("Published signed dashboard commit fff9999deadbeef to gh-pages (signature valid: true).", res.stdout)

            calls = self._read_calls(state_dir)
            graphql_calls = [c for c in calls if c["args"][:2] == ["api", "graphql"]]
            self.assertEqual(len(graphql_calls), 1)

            gql_input = graphql_calls[0]["input"]["variables"]["input"]
            self.assertEqual(gql_input["branch"], {"repositoryNameWithOwner": "gardener/gardener-gdc-ci", "branchName": "gh-pages"})
            self.assertEqual(gql_input["expectedHeadOid"], "aaa1111")
            self.assertEqual(gql_input["message"]["headline"], "chore(dashboard): record snapshot run #209 (success)")

            additions = {a["path"]: base64.b64decode(a["contents"]) for a in gql_input["fileChanges"]["additions"]}
            self.assertEqual(set(additions.keys()), {".nojekyll", "data/runs.json", "index.html"})
            self.assertEqual(additions[".nojekyll"], b"")
            self.assertIn(b"<title>Dashboard</title>", additions["index.html"])

            committed_runs = json.loads(additions["data/runs.json"].decode("utf-8"))
            self.assertEqual([r["runNumber"] for r in committed_runs], ["209", "206"])
            self.assertEqual(committed_runs[0]["status"], "success")

            pages_calls = [c for c in calls if any("pages/builds" in a for a in c["args"])]
            self.assertEqual(len(pages_calls), 1)

    def test_retries_on_stale_head_oid_and_preserves_interleaved_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            remote_runs_initial = [{"runId": "44281341296", "runNumber": "206", "status": "failure"}]
            remote_runs_retry = [
                {"runId": "44715630379", "runNumber": "208", "status": "failure"},
                {"runId": "44281341296", "runNumber": "206", "status": "failure"},
            ]
            bin_dir, state_dir, dashboard_dir, summary_file = self._setup_workspace(tmpdir, remote_runs_initial)
            with open(os.path.join(state_dir, "remote_runs_2.json"), "w", encoding="utf-8") as f:
                json.dump(remote_runs_retry, f)

            res = self._run_script(
                bin_dir,
                state_dir,
                dashboard_dir,
                summary_file,
                extra_env={
                    "MOCK_GH_HEAD_OIDS": '["aaa1111","bbb2222"]',
                    "MOCK_GH_GRAPHQL_FAIL_TIMES": "1",
                },
            )
            self.assertEqual(res.returncode, 0, msg=res.stderr)
            self.assertIn("::warning::Attempt 1/3", res.stderr)
            self.assertIn("Published signed dashboard commit fff9999deadbeef", res.stdout)

            calls = self._read_calls(state_dir)
            graphql_calls = [c for c in calls if c["args"][:2] == ["api", "graphql"]]
            self.assertEqual(len(graphql_calls), 2)
            self.assertEqual(graphql_calls[1]["input"]["variables"]["input"]["expectedHeadOid"], "bbb2222")

            additions = {
                a["path"]: base64.b64decode(a["contents"])
                for a in graphql_calls[1]["input"]["variables"]["input"]["fileChanges"]["additions"]
            }
            committed_runs = json.loads(additions["data/runs.json"].decode("utf-8"))
            self.assertEqual([r["runNumber"] for r in committed_runs], ["209", "208", "206"])

    def test_emits_warning_without_failing_stage_when_publish_keeps_failing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            remote_runs = [{"runId": "44281341296", "runNumber": "206", "status": "failure"}]
            bin_dir, state_dir, dashboard_dir, summary_file = self._setup_workspace(tmpdir, remote_runs)

            res = self._run_script(
                bin_dir,
                state_dir,
                dashboard_dir,
                summary_file,
                extra_env={
                    "DASHBOARD_PUBLISH_MAX_ATTEMPTS": "2",
                    "MOCK_GH_GRAPHQL_FAIL_TIMES": "5",
                },
            )
            self.assertEqual(res.returncode, 0, msg=res.stderr)
            self.assertIn("::warning::Failed to publish dashboard to gh-pages after 2 attempt(s)", res.stderr)

            calls = self._read_calls(state_dir)
            pages_calls = [c for c in calls if any("pages/builds" in a for a in c["args"])]
            self.assertEqual(len(pages_calls), 0)

    def test_skips_publish_on_non_main_branch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            remote_runs = [{"runId": "44281341296", "runNumber": "206", "status": "failure"}]
            bin_dir, state_dir, dashboard_dir, summary_file = self._setup_workspace(tmpdir, remote_runs)

            res = self._run_script(
                bin_dir,
                state_dir,
                dashboard_dir,
                summary_file,
                extra_env={"GITHUB_REF": "refs/heads/feature-branch"},
            )
            self.assertEqual(res.returncode, 0, msg=res.stderr)

            calls = self._read_calls(state_dir)
            graphql_calls = [c for c in calls if c["args"][:2] == ["api", "graphql"]]
            self.assertEqual(len(graphql_calls), 0)


if __name__ == "__main__":
    unittest.main()
