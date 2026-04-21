#!/usr/bin/env python3
"""Seed the GCS model-artifacts bucket with pretrained base weights.

Downloads roberta-base (classifier) from HuggingFace + optionally a Qwen
tokenizer stub (architect) and uploads to `gs://BUCKET/classifier/` and
`gs://BUCKET/architect/`. The full Qwen 2.5 model weights are ~14 GB and
intentionally NOT seeded here — operators who want to run the local Qwen
proxy instead of Gemini should upload those themselves.

Usage:
    # First: get the bucket name from Terraform
    BUCKET=$(terraform -chdir=infra/terraform output -raw models_bucket_name)

    # Then: seed
    python scripts/seed_default_models.py --bucket "$BUCKET"

Requirements:
    pip install huggingface_hub google-cloud-storage

Auth:
    The script uses Application Default Credentials. Run once per
    workstation: `gcloud auth application-default login`.

Notes:
    The running classifier + architect services currently DON'T use these
    files — they're on the regex-stub / Gemini-API path. This seed is for
    when a future deploy flips `AUTOMEND_CLASSIFIER_ENDPOINT=/predict_anomaly`
    or `AUTOMEND_ARCHITECT_PROVIDER=local` and expects real weights in
    /models/<component>/.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def download_roberta_base(dest: Path) -> None:
    """Fetch roberta-base tokenizer + model weights to a local dir."""
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("huggingface_hub missing. Install with: pip install huggingface_hub", file=sys.stderr)
        sys.exit(1)

    dest.mkdir(parents=True, exist_ok=True)
    print(f"Downloading roberta-base → {dest} (~500 MB)…")
    snapshot_download(
        repo_id="roberta-base",
        local_dir=str(dest),
        # Skip .msgpack / .h5 / pytorch_model.bin — safetensors is enough.
        allow_patterns=[
            "config.json",
            "tokenizer.json",
            "tokenizer_config.json",
            "vocab.json",
            "merges.txt",
            "model.safetensors",
        ],
    )
    print(f"Downloaded {len(list(dest.rglob('*')))} files.")


def download_qwen(dest: Path, variant: str) -> None:
    """Fetch a Qwen 2.5 Instruct tokenizer + model weights to a local dir."""
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("huggingface_hub missing. Install with: pip install huggingface_hub", file=sys.stderr)
        sys.exit(1)

    # Approximate on-disk sizes for reference.
    sizes = {
        "0.5B": "~1 GB",
        "1.5B": "~3 GB",
        "3B":   "~6 GB",
        "7B":   "~14 GB",
    }
    repo_id = f"Qwen/Qwen2.5-{variant}-Instruct"
    dest.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {repo_id} → {dest} ({sizes.get(variant, 'unknown size')})…")
    snapshot_download(
        repo_id=repo_id,
        local_dir=str(dest),
        allow_patterns=[
            "config.json",
            "generation_config.json",
            "tokenizer.json",
            "tokenizer_config.json",
            "vocab.json",
            "merges.txt",
            "special_tokens_map.json",
            "model.safetensors",
            # multi-file variants (3B+ ship sharded)
            "model-*.safetensors",
            "model.safetensors.index.json",
        ],
    )
    print(f"Downloaded {len(list(dest.rglob('*')))} files.")


def upload_dir_to_gcs(local_dir: Path, bucket_name: str, prefix: str) -> None:
    """Recursively upload `local_dir/*` → `gs://bucket/prefix/*`."""
    try:
        from google.cloud import storage
    except ImportError:
        print("google-cloud-storage missing. Install with: pip install google-cloud-storage",
              file=sys.stderr)
        sys.exit(1)

    client = storage.Client()
    bucket = client.bucket(bucket_name)

    # Skip HuggingFace's cache metadata — lockfiles, .metadata, CACHEDIR.TAG,
    # etc. live under `.cache/` and shouldn't ship as part of "our model".
    SKIP_DIR_PARTS = {".cache", "__pycache__"}
    SKIP_SUFFIXES = {".lock", ".metadata"}

    print(f"Uploading to gs://{bucket_name}/{prefix}/ …")
    uploaded = 0
    skipped = 0
    for path in local_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(local_dir)
        # Drop anything inside a `.cache/` (or similar) subtree or ending
        # in `.lock` / `.metadata`.
        if any(part in SKIP_DIR_PARTS for part in rel.parts):
            skipped += 1
            continue
        if path.suffix in SKIP_SUFFIXES:
            skipped += 1
            continue

        blob_name = f"{prefix}/{rel.as_posix()}"
        blob = bucket.blob(blob_name)
        print(f"  → {blob_name} ({path.stat().st_size / 1024 / 1024:.1f} MB)")
        blob.upload_from_filename(str(path))
        uploaded += 1
    print(f"Uploaded {uploaded} files (skipped {skipped} cache/metadata files).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bucket",
        required=True,
        help="GCS bucket name (from `terraform output -raw models_bucket_name`)",
    )
    parser.add_argument(
        "--cache-dir",
        default=os.path.expanduser("~/.cache/automend-model-seed"),
        help="Local directory to download weights to before upload",
    )
    parser.add_argument(
        "--component",
        choices=["classifier", "architect", "all"],
        default="all",
        help="Which model to seed. `all` does classifier + architect (default).",
    )
    parser.add_argument(
        "--qwen-variant",
        choices=["0.5B", "1.5B", "3B", "7B"],
        default="0.5B",
        help="Qwen 2.5 size to seed for the architect. 0.5B (~1 GB) is the default — small enough to download fast + still a real Qwen model. Bump to 7B (~14 GB) for the vLLM inference path.",
    )
    args = parser.parse_args()

    cache = Path(args.cache_dir)

    if args.component in ("classifier", "all"):
        print("=== Classifier (RoBERTa-base) ===")
        cls_dir = cache / "classifier"
        download_roberta_base(cls_dir)
        upload_dir_to_gcs(cls_dir, args.bucket, "classifier")
        print()

    if args.component in ("architect", "all"):
        print(f"=== Architect (Qwen2.5-{args.qwen_variant}-Instruct) ===")
        arch_dir = cache / "architect"
        download_qwen(arch_dir, args.qwen_variant)
        upload_dir_to_gcs(arch_dir, args.bucket, "architect")
        print()

    print(f"Done. Verify with:")
    print(f"  gcloud storage ls gs://{args.bucket}/classifier/")
    print(f"  gcloud storage ls gs://{args.bucket}/architect/")


if __name__ == "__main__":
    main()
