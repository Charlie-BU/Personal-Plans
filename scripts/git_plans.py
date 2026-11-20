#!/usr/bin/env python3
"""Read or prepare plans against a pinned Git snapshot without a checkout."""
import argparse
import base64
import io
import json
import os
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
import plans
from model import NeedFiles, PlanError, require

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

    def inventory(self):
        listing = self.git("ls-tree", "-r", "-z", self.commit, "--", "plans")
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
        while True:
            try:
                return plans.run_store(store, envelope, args.command, args)
            except NeedFiles as missing:
                if complete:
                    raise PlanError("Incomplete snapshot: " + ", ".join(missing.paths))
                files.update(self.load({
                    path: self.commit + ":" + plans.safe_path(path)
                    for path in missing.paths
                }))


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

def main():
    transport = argparse.ArgumentParser(description=__doc__, add_help=False)
    transport.add_argument("--repo", required=True)
    transport.add_argument("--ref", required=True)
    transport.add_argument("--publish", nargs=2, metavar=("REMOTE", "BRANCH"))
    options, operation = transport.parse_known_args()
    args = plans.parser().parse_args(["--stdin", *operation])
    try:
        request = "" if sys.stdin.isatty() else sys.stdin.read()
        envelope = json.loads(request) if request.strip() else {}
        require(isinstance(envelope, dict), "Request must be a JSON object")
        require(not {"snapshot", "complete", "files_base64", "index_paths"} & envelope.keys(),
                "Git transport supplies the snapshot, completeness and file inventory")
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
