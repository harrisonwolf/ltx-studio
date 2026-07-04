#!/usr/bin/env python
"""Fetch the LTX 0.9.8 2B checkpoint (user-authorized 2026-07-04).

Network-only, NO CUDA. Lists candidate repos, finds a 2B 0.9.8 .safetensors,
and downloads exactly ONE matching single-file (preferring the 'distilled' one
that director.py's --ltx_variant distilled path references). Prints the local
path so the studio can point at it. Downloads nothing if no match is found.
"""
import sys

try:
    from huggingface_hub import HfApi, hf_hub_download
except Exception as e:  # pragma: no cover
    print(f"IMPORT_ERROR: {e}")
    sys.exit(3)

CANDIDATE_REPOS = ["Lightricks/LTX-Video"]  # the base repo hosts many single-file ckpts
api = HfApi()


def find():
    hits = []
    for repo in CANDIDATE_REPOS:
        try:
            files = api.list_repo_files(repo)
        except Exception as e:
            print(f"LIST_ERROR {repo}: {e}")
            continue
        for f in files:
            if "0.9.8" in f.lower():
                print(f"[avail] {repo}: {f}")
        for f in files:
            fl = f.lower()
            if fl.endswith(".safetensors") and "0.9.8" in fl and "2b" in fl:
                hits.append((repo, f))
    return hits


def main():
    hits = find()
    if not hits:
        print("NO_098_2B_FOUND: no 2B 0.9.8 .safetensors in the checked repos. Nothing downloaded.")
        return 2
    # prefer the 'distilled' single-file that the code already references
    hits.sort(key=lambda rf: (0 if "distilled" in rf[1].lower() else 1, rf[1]))
    repo, fname = hits[0]
    print(f"FETCHING: {repo} :: {fname}")
    try:
        path = hf_hub_download(repo_id=repo, filename=fname)
    except Exception as e:
        print(f"DOWNLOAD_ERROR: {e}")
        return 4
    print(f"DOWNLOADED_TO: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
