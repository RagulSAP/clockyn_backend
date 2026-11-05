#!/usr/bin/env python3
"""AWS Rekognition setup helper for the face recognition backend (stdlib + boto3).

    python scripts/rekognition_setup.py check            # region, collection, face count, which IAM actions work
    python scripts/rekognition_setup.py create           # create the collection when it does not exist
    python scripts/rekognition_setup.py policy           # least-privilege IAM policy JSON for the APP role
    python scripts/rekognition_setup.py policy --admin   # extra policy for the human OPERATOR (create / list / delete)

Settings come from the same environment variables as the app (the backend `.env` is read when python-dotenv is installed):
REKOGNITION_REGION (else AWS_S3_REGION, else ap-south-1), REKOGNITION_COLLECTION_ID (default clockyn-faces), and the usual
AWS credentials (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY, a profile, or an IAM role). `--region` / `--collection` override.

`check` makes only cheap, harmless calls. The DetectFaces / IndexFaces / SearchFacesByImage probes send a 1x1 pixel image:
the expected answer is "no face" (the action is allowed); only an AccessDenied answer counts as missing permission. Each probe
processes one tiny image, which Rekognition bills like any other image: check the AWS pricing page for the current rate.
The face count of the collection comes from DescribeCollection. Exit code 0 = everything the app needs works.
"""
import argparse
import json
import os
import struct
import sys
import zlib

DEFAULT_COLLECTION_ID = "clockyn-faces"
DEFAULT_REGION = "ap-south-1"
ACCOUNT_PLACEHOLDER = "<ACCOUNT_ID>"

# Actions the app role needs on the collection (resource-level) and the one that has no resource-level scoping.
APP_COLLECTION_ACTIONS = [
    "rekognition:IndexFaces",
    "rekognition:SearchFacesByImage",
    "rekognition:DeleteFaces",
    "rekognition:ListFaces",
    "rekognition:DescribeCollection",
]
APP_WILDCARD_ACTIONS = ["rekognition:DetectFaces"]
ADMIN_COLLECTION_ACTIONS = ["rekognition:CreateCollection", "rekognition:DeleteCollection"]
ADMIN_WILDCARD_ACTIONS = ["rekognition:ListCollections"]


def _env_defaults():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    region = (os.getenv("REKOGNITION_REGION") or os.getenv("AWS_S3_REGION") or DEFAULT_REGION).strip()
    collection = (os.getenv("REKOGNITION_COLLECTION_ID") or DEFAULT_COLLECTION_ID).strip()
    return region, collection


def collection_arn(region, account, collection):
    return "arn:aws:rekognition:%s:%s:collection/%s" % (region, account, collection)


def build_app_policy(region, account, collection):
    """Least-privilege policy for the role the backend runs as."""
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "ClockynFacesCollection",
                "Effect": "Allow",
                "Action": list(APP_COLLECTION_ACTIONS),
                "Resource": collection_arn(region, account, collection),
            },
            {
                "Sid": "ClockynDetectFaces",
                "Effect": "Allow",
                "Action": list(APP_WILDCARD_ACTIONS),
                "Resource": "*",
            },
        ],
    }


def build_admin_policy(region, account, collection):
    """Policy for the human operator who creates / lists / deletes collections (NOT for the app role). The last statement
    lets the operator run the optional live test (tests/test_rekognition_live.py), which uses a throw-away collection
    named clockyn-test-<hex>."""
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "ClockynCollectionAdmin",
                "Effect": "Allow",
                "Action": list(ADMIN_COLLECTION_ACTIONS),
                "Resource": collection_arn(region, account, collection),
            },
            {
                "Sid": "ClockynListCollections",
                "Effect": "Allow",
                "Action": list(ADMIN_WILDCARD_ACTIONS),
                "Resource": "*",
            },
            {
                "Sid": "ClockynLiveTestCollections",
                "Effect": "Allow",
                "Action": list(ADMIN_COLLECTION_ACTIONS) + list(APP_COLLECTION_ACTIONS),
                "Resource": collection_arn(region, account, "clockyn-test-*"),
            },
        ],
    }


def tiny_png():
    """A valid 1x1 PNG built with the standard library (no face in it)."""
    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
    raw = b"\x00\xff\xff\xff"                                   # filter byte + one white RGB pixel
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _client(region):
    import boto3
    return boto3.client("rekognition", region_name=region)


def _account_id(region):
    try:
        import boto3
        return boto3.client("sts", region_name=region).get_caller_identity()["Account"]
    except Exception:
        return None


def _code(exc):
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        return str((response.get("Error") or {}).get("Code") or type(exc).__name__)
    return type(exc).__name__


# ------------------------------------------------------------------------------------------------ commands
def cmd_policy(args):
    region, collection = args.region, args.collection
    account = _account_id(region) or ACCOUNT_PLACEHOLDER
    policy = build_admin_policy(region, account, collection) if args.admin else build_app_policy(region, account, collection)
    print(json.dumps(policy, indent=2))
    if account == ACCOUNT_PLACEHOLDER:
        print("# note: could not read the account id (sts:GetCallerIdentity): replace %s" % ACCOUNT_PLACEHOLDER, file=sys.stderr)
    return 0


def cmd_create(args):
    client = _client(args.region)
    try:
        info = client.describe_collection(CollectionId=args.collection)
        print("collection %s already exists in %s (faces: %s)" % (args.collection, args.region, info.get("FaceCount")))
        return 0
    except Exception as exc:
        if _code(exc) != "ResourceNotFoundException":
            print("cannot describe the collection (%s). Needs rekognition:DescribeCollection." % _code(exc), file=sys.stderr)
            return 1
    try:
        res = client.create_collection(CollectionId=args.collection)
    except Exception as exc:
        print("create failed (%s). The caller needs rekognition:CreateCollection: "
              "run `policy --admin` for the operator policy." % _code(exc), file=sys.stderr)
        return 1
    print("created collection %s in %s: %s (HTTP %s)" % (args.collection, args.region, res.get("CollectionArn"),
                                                        res.get("StatusCode")))
    return 0


def _probe(label, fn, ok_codes):
    """Run one probe; 'allowed' when it succeeds or fails with one of ok_codes (the request was authorised)."""
    try:
        fn()
        return label, "OK", ""
    except Exception as exc:
        code = _code(exc)
        if code in ok_codes:
            return label, "OK", "(%s: authorised, nothing changed)" % code
        if code in ("AccessDeniedException", "AccessDenied"):
            return label, "DENIED", "missing IAM action %s" % label
        return label, "ERROR", code


def cmd_check(args):
    region, collection = args.region, args.collection
    print("region:        %s" % region)
    print("collection id: %s" % collection)
    account = _account_id(region)
    print("account:       %s" % (account or "unknown (sts:GetCallerIdentity not allowed or no credentials)"))
    try:
        client = _client(region)
    except Exception as exc:
        print("cannot create the AWS client: %s" % _code(exc), file=sys.stderr)
        return 1

    results = []
    exists = False
    try:
        info = client.describe_collection(CollectionId=collection)
        exists = True
        print("face count:    %s" % info.get("FaceCount"))
        print("face model:    %s" % info.get("FaceModelVersion"))
        print("collection arn: %s" % info.get("CollectionARN"))
        results.append(("rekognition:DescribeCollection", "OK", ""))
    except Exception as exc:
        code = _code(exc)
        if code == "ResourceNotFoundException":
            print("collection %s does NOT exist: run `python scripts/rekognition_setup.py create`" % collection)
            results.append(("rekognition:DescribeCollection", "OK", "(authorised; collection missing)"))
        elif code in ("AccessDeniedException", "AccessDenied"):
            results.append(("rekognition:DescribeCollection", "DENIED", "missing IAM action rekognition:DescribeCollection"))
        else:
            results.append(("rekognition:DescribeCollection", "ERROR", code))

    png = {"Bytes": tiny_png()}
    no_face = ("InvalidParameterException", "InvalidImageFormatException", "ImageTooLargeException")
    results.append(_probe("rekognition:DetectFaces",
                          lambda: client.detect_faces(Image=png), no_face))
    if exists:
        results.append(_probe("rekognition:ListFaces",
                              lambda: client.list_faces(CollectionId=collection, MaxResults=1), ()))
        results.append(_probe("rekognition:IndexFaces",
                              lambda: client.index_faces(CollectionId=collection, Image=png, ExternalImageId="setup-check",
                                                         MaxFaces=1, QualityFilter="AUTO", DetectionAttributes=[]), no_face))
        results.append(_probe("rekognition:SearchFacesByImage",
                              lambda: client.search_faces_by_image(CollectionId=collection, Image=png, MaxFaces=1,
                                                                   FaceMatchThreshold=90.0, QualityFilter="AUTO"), no_face))
        results.append(_probe("rekognition:DeleteFaces",
                              lambda: client.delete_faces(CollectionId=collection,
                                                          FaceIds=["00000000-0000-0000-0000-000000000000"]), ()))
    else:
        for action in ("ListFaces", "IndexFaces", "SearchFacesByImage", "DeleteFaces"):
            results.append(("rekognition:" + action, "SKIPPED", "collection does not exist"))
    results.append(_probe("rekognition:ListCollections (operator only)",
                          lambda: client.list_collections(MaxResults=1), ()))

    width = max(len(r[0]) for r in results)
    print()
    for label, status, note in results:
        print("%-*s  %-8s %s" % (width, label, status, note))
    failed = [r for r in results if r[1] in ("DENIED", "ERROR") and "operator only" not in r[0]]
    if not exists:
        failed.append(("collection", "MISSING", ""))
    if failed:
        print("\nNOT READY. Least-privilege policy for the app role: python scripts/rekognition_setup.py policy")
        return 1
    print("\nREADY: the collection exists and the app actions are allowed.")
    return 0


def main(argv=None):
    default_region, default_collection = _env_defaults()
    parser = argparse.ArgumentParser(description="AWS Rekognition setup helper (face recognition backend).")
    parser.add_argument("--region", default=default_region, help="default: REKOGNITION_REGION / AWS_S3_REGION / ap-south-1")
    parser.add_argument("--collection", default=default_collection, help="default: REKOGNITION_COLLECTION_ID / clockyn-faces")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="describe the collection and probe the IAM actions").set_defaults(func=cmd_check)
    sub.add_parser("create", help="create the collection if it is missing").set_defaults(func=cmd_create)
    p = sub.add_parser("policy", help="print the least-privilege IAM policy JSON")
    p.add_argument("--admin", action="store_true", help="print the operator (create / list / delete) policy instead")
    p.set_defaults(func=cmd_policy)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
