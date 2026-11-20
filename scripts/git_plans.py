#!/usr/bin/env python3
"""Read or prepare plans against a pinned Git snapshot without a checkout."""
import argparse
import base64
import datetime as dt
import io
import json
import os
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
import plans
from model import NeedFiles, PlanError, instant, require, zone
from periods import path_for, read as read_period

FULL = {"init", "apply", "configure", "split-series", "save-report",
        "integration", "reindex", "verify"}


class GitSnapshot:
    def __init__(self, repo, ref):
        self.repo = repo
        self.commit = self.git("rev-parse", "--verify", "--end-of-options",
                               ref + "^{commit}").decode().strip()

    def git(self, *args, data=None, env=None):
        result = subprocess.run(["git", "--no-replace-objects", "-C", self.repo, *args],
                                input=data, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=env, check=False)
        if result.returncode:
            raise PlanError("Git " + args[0] + " failed")
        return result.stdout

    def load(self, objects):
        if not objects:
            return {}
        values = list(dict.fromkeys(objects.values()))
        require(all("\n" not in value and "\r" not in value for value in values),
                "Invalid Git batch object name")
        request = ("\n".join(values) + "\n").encode()
        with io.BytesIO(self.git("cat-file", "--batch", data=request)) as output:
            contents = {}
            for value in values:
                header = output.readline().split()
                require(len(header) == 3 and header[1] == b"blob",
                        "Missing or invalid Git blob: " + value)
                size = int(header[2])
                contents[value] = output.read(size)
                require(len(contents[value]) == size and output.read(1) == b"\n",
                        "Incomplete Git batch response")
            require(not output.read(1), "Unexpected Git batch response")
        return {path: contents[value] for path, value in objects.items()}

    def inventory(self, *paths):
        listing = self.git("ls-tree", "-r", "-z", self.commit, "--", *(paths or ("plans",)))
        objects = {}
        for entry in listing.split(b"\0")[:-1]:
            metadata, path = entry.split(b"\t", 1)
            mode, kind, oid = metadata.split()
            require(mode == b"100644" and kind == b"blob",
                    "Expected regular files in plans/")
            objects[plans.safe_path(path.decode())] = oid.decode()
        return objects

    def run(self, envelope, args):
        complete = args.command in FULL
        files = self.load(self.inventory()) if complete else {}
        store = plans.Store({"snapshot": self.commit, "complete": complete},
                            raw_files=files)
        if args.command == "review":
            return self.review(store, args)
        return self.resolve(store, lambda: plans.run_store(store, envelope, args.command, args))

    def resolve(self, store, operation):
        while True:
            try:
                return operation()
            except NeedFiles as missing:
                if store.complete:
                    raise PlanError("Incomplete snapshot: " + ", ".join(missing.paths))
                store.files.update(self.load({
                    path: self.commit + ":" + plans.safe_path(path)
                    for path in missing.paths
                }))

    def review(self, store, args):
        require(1 <= args.days <= 31, "Review days must be between 1 and 31")
        vault, _ = self.resolve(store, store.entry)
        as_of = args.as_of or plans.now()
        local_date = instant(as_of).astimezone(zone(vault["timezone"])).date()
        end = local_date.isoformat()
        start = (local_date - dt.timedelta(days=args.days - 1)).isoformat()
        result = self.resolve(store, lambda: plans.report(
            store, start, end, as_of, include_progress=True))
        result["timezone"] = vault["timezone"]

        scopes = [
            dict(kind="day", start=date, end=date, timezone=vault["timezone"],
                 label=date, plan_id=None)
            for date in plans.days_between(start, end)
        ]
        paths = {path_for(scope): scope for scope in scopes}
        # An absent optional note is proven by the pinned tree, not by a failed
        # object download. All present daily notes are loaded as one batch.
        present = self.inventory(*paths)
        store.files.update(self.load(present))
        result["daily_feedback"] = []
        for path in present:
            notes = read_period(store, paths[path])["record"]["notes"]
            if notes:
                latest = notes[-1]
                text = latest["text"]
                result["daily_feedback"].append({
                    "date": paths[path]["start"], "path": path, "note_count": len(notes),
                    "latest": {**latest, "text": text[:500]},
                    "text_truncated": len(text) > 500,
                })
        result["daily_feedback"].sort(key=lambda entry: entry["date"], reverse=True)
        return result

    def branch_head(self, remote, branch):
        ref = "refs/heads/" + branch
        self.git("check-ref-format", ref)
        response = self.git("ls-remote", "--exit-code", "--refs", "--", remote, ref)
        oid, name = response.decode().strip().split("\t")
        require(name == ref, "Unexpected remote branch")
        return oid

    def publish(self, prepared, remote, branch):
        require(prepared["status"] == "prepared" and prepared["base_commit"] == self.commit,
                "Only a proposal prepared from this snapshot can be published")
        read_urls = self.git("remote", "get-url", "--all", "--", remote).splitlines()
        write_urls = self.git("remote", "get-url", "--push", "--all", "--", remote).splitlines()
        require(len(read_urls) == 1 and read_urls == write_urls,
                "Publishing requires one Git remote with the same fetch and push URL")
        require(self.branch_head(remote, branch) == self.commit,
                "Branch advanced: fetch its new commit and prepare again")
        updates = prepared["files_base64"]
        deletions = prepared["delete_paths"]
        if not updates and not deletions:
            return {"status": "no_changes", "commit": self.commit, "remote_confirmed": True}

        with tempfile.TemporaryDirectory(prefix="plans-index-") as directory:
            env = dict(os.environ, GIT_INDEX_FILE=os.path.join(directory, "index"))
            self.git("read-tree", self.commit, env=env)
            entries = []
            for path in deletions:
                zero = "0" * len(self.commit)
                entries.append(f"0 {zero}\t{plans.safe_path(path)}\0")
            for path, content in updates.items():
                oid = self.git("hash-object", "-w", "--stdin",
                               data=base64.b64decode(content, validate=True)).decode().strip()
                entries.append(f"100644 {oid}\t{plans.safe_path(path)}\0")
            self.git("update-index", "-z", "--index-info",
                     data="".join(entries).encode(), env=env)
            tree = self.git("write-tree", env=env).decode().strip()
        candidate = self.git("commit-tree", tree, "-p", self.commit,
                             "-m", prepared["message"]).decode().strip()
        ref = "refs/heads/" + branch
        # The explicit lease makes the update compare-and-swap. The candidate is
        # constructed with that exact parent, so the update is always fast-forward.
        try:
            self.git("push", f"--force-with-lease={ref}:{self.commit}", "--",
                     remote, f"{candidate}:{ref}")
        except PlanError:
            # Transport can fail after the server accepted the update.
            pass
        try:
            confirmed = self.branch_head(remote, branch) == candidate
        except PlanError:
            confirmed = False
        return {"status": "published" if confirmed else "publication_unconfirmed",
                "commit": candidate, "base_commit": self.commit,
                "remote_confirmed": confirmed, "changed_paths": sorted(updates),
                "delete_paths": deletions}


def operation_args(operation):
    if operation and operation[0] == "review":
        parser = argparse.ArgumentParser(prog="git_plans.py review")
        parser.add_argument("--days", type=int, default=3)
        parser.add_argument("--as-of")
        return parser.parse_args(operation[1:], namespace=argparse.Namespace(command="review"))
    return plans.parser().parse_args(["--stdin", *operation])


def main():
    transport = argparse.ArgumentParser(description=__doc__, add_help=False)
    transport.add_argument("--repo", required=True)
    transport.add_argument("--ref", required=True)
    transport.add_argument("--publish", nargs=2, metavar=("REMOTE", "BRANCH"))
    options, operation = transport.parse_known_args()
    args = operation_args(operation)
    try:
        request = "" if sys.stdin.isatty() else sys.stdin.read()
        envelope = json.loads(request) if request.strip() else {}
        require(isinstance(envelope, dict), "Request must be a JSON object")
        require(not {"snapshot", "complete", "files_base64", "index_paths"} & envelope.keys(),
                "Git transport supplies the snapshot, completeness and file inventory")
        require(args.command != "review" or not options.publish, "Review is read-only")
        snapshot = GitSnapshot(os.path.abspath(options.repo), options.ref)
        result = snapshot.run(envelope, args)
        if options.publish and result.get("status") == "prepared":
            result = snapshot.publish(result, *options.publish)
    except (PlanError, OSError, ValueError, KeyError, TypeError, OverflowError,
            RecursionError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return int(result.get("status") == "publication_unconfirmed")


if __name__ == "__main__":
    sys.exit(main())
