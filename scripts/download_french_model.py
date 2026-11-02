"""Explicit provisioning step; the application never downloads models on request."""

import argparse
from pathlib import Path

import stanza


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True,
                        help="Artifact directory excluded from Git, e.g. data/models/stanza")
    parser.add_argument("--package", choices=["sequoia", "combined"], default="combined")
    args = parser.parse_args()
    stanza.download("fr", model_dir=str(args.model_dir), package=args.package,
                    processors="tokenize,mwt,pos,lemma,depparse", resources_version="1.11.0")


if __name__ == "__main__":
    main()
