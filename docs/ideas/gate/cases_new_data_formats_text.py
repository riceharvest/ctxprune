"""Runnable fidelity cases for ten new lossless data-format ideas (#1434-#1443). See harness.py for what a pass means.

  #1434 byte-array source literals (0x0a, 0x1e, ...) to joined hex lines        roundtrip
  #1435 kubectl get secret -o yaml: printable base64 values to stringData       roundtrip
  #1436 HTML tables (pandas to_html) to TSV                                     equivalent
  #1437 SQL DDL: unquote plain lowercase identifiers                            equivalent
  #1438 Java .properties: decode \\uXXXX runs to characters                      equivalent
  #1439 UUIDs without dashes (one legend line)                                  roundtrip
  #1440 CSV/TSV rows padded with trailing empty fields by spreadsheet exports   equivalent
  #1441 CSV cells holding JSON (doubled quotes) to TSV                          equivalent
  #1442 Cargo.lock: alias the crates.io source, inline dependency arrays        roundtrip (+ tomllib oracle)
  #1443 MarkItDown / pandas spreadsheet Markdown: NaN cells, Unnamed: N, .0     equivalent

Conventions: every encoder is wrapped by `_total` (anything that raises, or any result that is not strictly shorter,
hands the input back untouched); in-band markers start with the sigil "§" (or a fixed TOML/YAML comment line) and text
that already starts like an encoding is escaped with a "raw" line, so those roundtrip cases set idempotent=False.
Fixtures marked "real" are captured from the tool in this container (protobuf-go v1.31.0 sources, cargo, pandas 3,
MarkItDown, LibreOffice, openssl, csv); the rest are reconstructed from memory and say so.
"""
import base64
import bisect
import csv
import functools
import html
import io
import json
import random
import re
import sqlite3
import tomllib
import uuid
from html.parser import HTMLParser

from harness import Fixture, case

SIGIL = "§"


def _total(fn):
    """Fail open and self-gate: hand the input back unless the result is strictly shorter."""

    @functools.wraps(fn)
    def wrapper(text):
        try:
            out = fn(text)
        except Exception:  # noqa: BLE001
            return text
        return out if isinstance(out, str) and len(out) < len(text) else text

    return wrapper


def _failopen(fn):
    """Fail open without the length gate (escape paths may legitimately grow the text)."""

    @functools.wraps(fn)
    def wrapper(text):
        try:
            out = fn(text)
        except Exception:  # noqa: BLE001
            return text
        return out if isinstance(out, str) else text

    return wrapper


# ===============================================================================================================
# Real captures (see module docstring). Raw strings: they contain backslashes and tabs.

# protobuf-go v1.31.0, types/known/durationpb/duration.pb.go lines 290-326 (BSD-3-Clause), real protoc-gen-go output
GO_DURATION = r'''}

var File_google_protobuf_duration_proto protoreflect.FileDescriptor

var file_google_protobuf_duration_proto_rawDesc = []byte{
	0x0a, 0x1e, 0x67, 0x6f, 0x6f, 0x67, 0x6c, 0x65, 0x2f, 0x70, 0x72, 0x6f, 0x74, 0x6f, 0x62, 0x75,
	0x66, 0x2f, 0x64, 0x75, 0x72, 0x61, 0x74, 0x69, 0x6f, 0x6e, 0x2e, 0x70, 0x72, 0x6f, 0x74, 0x6f,
	0x12, 0x0f, 0x67, 0x6f, 0x6f, 0x67, 0x6c, 0x65, 0x2e, 0x70, 0x72, 0x6f, 0x74, 0x6f, 0x62, 0x75,
	0x66, 0x22, 0x3a, 0x0a, 0x08, 0x44, 0x75, 0x72, 0x61, 0x74, 0x69, 0x6f, 0x6e, 0x12, 0x18, 0x0a,
	0x07, 0x73, 0x65, 0x63, 0x6f, 0x6e, 0x64, 0x73, 0x18, 0x01, 0x20, 0x01, 0x28, 0x03, 0x52, 0x07,
	0x73, 0x65, 0x63, 0x6f, 0x6e, 0x64, 0x73, 0x12, 0x14, 0x0a, 0x05, 0x6e, 0x61, 0x6e, 0x6f, 0x73,
	0x18, 0x02, 0x20, 0x01, 0x28, 0x05, 0x52, 0x05, 0x6e, 0x61, 0x6e, 0x6f, 0x73, 0x42, 0x83, 0x01,
	0x0a, 0x13, 0x63, 0x6f, 0x6d, 0x2e, 0x67, 0x6f, 0x6f, 0x67, 0x6c, 0x65, 0x2e, 0x70, 0x72, 0x6f,
	0x74, 0x6f, 0x62, 0x75, 0x66, 0x42, 0x0d, 0x44, 0x75, 0x72, 0x61, 0x74, 0x69, 0x6f, 0x6e, 0x50,
	0x72, 0x6f, 0x74, 0x6f, 0x50, 0x01, 0x5a, 0x31, 0x67, 0x6f, 0x6f, 0x67, 0x6c, 0x65, 0x2e, 0x67,
	0x6f, 0x6c, 0x61, 0x6e, 0x67, 0x2e, 0x6f, 0x72, 0x67, 0x2f, 0x70, 0x72, 0x6f, 0x74, 0x6f, 0x62,
	0x75, 0x66, 0x2f, 0x74, 0x79, 0x70, 0x65, 0x73, 0x2f, 0x6b, 0x6e, 0x6f, 0x77, 0x6e, 0x2f, 0x64,
	0x75, 0x72, 0x61, 0x74, 0x69, 0x6f, 0x6e, 0x70, 0x62, 0xf8, 0x01, 0x01, 0xa2, 0x02, 0x03, 0x47,
	0x50, 0x42, 0xaa, 0x02, 0x1e, 0x47, 0x6f, 0x6f, 0x67, 0x6c, 0x65, 0x2e, 0x50, 0x72, 0x6f, 0x74,
	0x6f, 0x62, 0x75, 0x66, 0x2e, 0x57, 0x65, 0x6c, 0x6c, 0x4b, 0x6e, 0x6f, 0x77, 0x6e, 0x54, 0x79,
	0x70, 0x65, 0x73, 0x62, 0x06, 0x70, 0x72, 0x6f, 0x74, 0x6f, 0x33,
}

var (
	file_google_protobuf_duration_proto_rawDescOnce sync.Once
	file_google_protobuf_duration_proto_rawDescData = file_google_protobuf_duration_proto_rawDesc
)

func file_google_protobuf_duration_proto_rawDescGZIP() []byte {
	file_google_protobuf_duration_proto_rawDescOnce.Do(func() {
		file_google_protobuf_duration_proto_rawDescData = protoimpl.X.CompressGZIP(file_google_protobuf_duration_proto_rawDescData)
	})
	return file_google_protobuf_duration_proto_rawDescData
}

var file_google_protobuf_duration_proto_msgTypes = make([]protoimpl.MessageInfo, 1)
var file_google_protobuf_duration_proto_goTypes = []interface{}{
'''

# protobuf-go v1.31.0, types/known/structpb/struct.pb.go lines 651-712 (BSD-3-Clause)
GO_STRUCT = r'''}

var File_google_protobuf_struct_proto protoreflect.FileDescriptor

var file_google_protobuf_struct_proto_rawDesc = []byte{
	0x0a, 0x1c, 0x67, 0x6f, 0x6f, 0x67, 0x6c, 0x65, 0x2f, 0x70, 0x72, 0x6f, 0x74, 0x6f, 0x62, 0x75,
	0x66, 0x2f, 0x73, 0x74, 0x72, 0x75, 0x63, 0x74, 0x2e, 0x70, 0x72, 0x6f, 0x74, 0x6f, 0x12, 0x0f,
	0x67, 0x6f, 0x6f, 0x67, 0x6c, 0x65, 0x2e, 0x70, 0x72, 0x6f, 0x74, 0x6f, 0x62, 0x75, 0x66, 0x22,
	0x98, 0x01, 0x0a, 0x06, 0x53, 0x74, 0x72, 0x75, 0x63, 0x74, 0x12, 0x3b, 0x0a, 0x06, 0x66, 0x69,
	0x65, 0x6c, 0x64, 0x73, 0x18, 0x01, 0x20, 0x03, 0x28, 0x0b, 0x32, 0x23, 0x2e, 0x67, 0x6f, 0x6f,
	0x67, 0x6c, 0x65, 0x2e, 0x70, 0x72, 0x6f, 0x74, 0x6f, 0x62, 0x75, 0x66, 0x2e, 0x53, 0x74, 0x72,
	0x75, 0x63, 0x74, 0x2e, 0x46, 0x69, 0x65, 0x6c, 0x64, 0x73, 0x45, 0x6e, 0x74, 0x72, 0x79, 0x52,
	0x06, 0x66, 0x69, 0x65, 0x6c, 0x64, 0x73, 0x1a, 0x51, 0x0a, 0x0b, 0x46, 0x69, 0x65, 0x6c, 0x64,
	0x73, 0x45, 0x6e, 0x74, 0x72, 0x79, 0x12, 0x10, 0x0a, 0x03, 0x6b, 0x65, 0x79, 0x18, 0x01, 0x20,
	0x01, 0x28, 0x09, 0x52, 0x03, 0x6b, 0x65, 0x79, 0x12, 0x2c, 0x0a, 0x05, 0x76, 0x61, 0x6c, 0x75,
	0x65, 0x18, 0x02, 0x20, 0x01, 0x28, 0x0b, 0x32, 0x16, 0x2e, 0x67, 0x6f, 0x6f, 0x67, 0x6c, 0x65,
	0x2e, 0x70, 0x72, 0x6f, 0x74, 0x6f, 0x62, 0x75, 0x66, 0x2e, 0x56, 0x61, 0x6c, 0x75, 0x65, 0x52,
	0x05, 0x76, 0x61, 0x6c, 0x75, 0x65, 0x3a, 0x02, 0x38, 0x01, 0x22, 0xb2, 0x02, 0x0a, 0x05, 0x56,
	0x61, 0x6c, 0x75, 0x65, 0x12, 0x3b, 0x0a, 0x0a, 0x6e, 0x75, 0x6c, 0x6c, 0x5f, 0x76, 0x61, 0x6c,
	0x75, 0x65, 0x18, 0x01, 0x20, 0x01, 0x28, 0x0e, 0x32, 0x1a, 0x2e, 0x67, 0x6f, 0x6f, 0x67, 0x6c,
	0x65, 0x2e, 0x70, 0x72, 0x6f, 0x74, 0x6f, 0x62, 0x75, 0x66, 0x2e, 0x4e, 0x75, 0x6c, 0x6c, 0x56,
	0x61, 0x6c, 0x75, 0x65, 0x48, 0x00, 0x52, 0x09, 0x6e, 0x75, 0x6c, 0x6c, 0x56, 0x61, 0x6c, 0x75,
	0x65, 0x12, 0x23, 0x0a, 0x0c, 0x6e, 0x75, 0x6d, 0x62, 0x65, 0x72, 0x5f, 0x76, 0x61, 0x6c, 0x75,
	0x65, 0x18, 0x02, 0x20, 0x01, 0x28, 0x01, 0x48, 0x00, 0x52, 0x0b, 0x6e, 0x75, 0x6d, 0x62, 0x65,
	0x72, 0x56, 0x61, 0x6c, 0x75, 0x65, 0x12, 0x23, 0x0a, 0x0c, 0x73, 0x74, 0x72, 0x69, 0x6e, 0x67,
	0x5f, 0x76, 0x61, 0x6c, 0x75, 0x65, 0x18, 0x03, 0x20, 0x01, 0x28, 0x09, 0x48, 0x00, 0x52, 0x0b,
	0x73, 0x74, 0x72, 0x69, 0x6e, 0x67, 0x56, 0x61, 0x6c, 0x75, 0x65, 0x12, 0x1f, 0x0a, 0x0a, 0x62,
	0x6f, 0x6f, 0x6c, 0x5f, 0x76, 0x61, 0x6c, 0x75, 0x65, 0x18, 0x04, 0x20, 0x01, 0x28, 0x08, 0x48,
	0x00, 0x52, 0x09, 0x62, 0x6f, 0x6f, 0x6c, 0x56, 0x61, 0x6c, 0x75, 0x65, 0x12, 0x3c, 0x0a, 0x0c,
	0x73, 0x74, 0x72, 0x75, 0x63, 0x74, 0x5f, 0x76, 0x61, 0x6c, 0x75, 0x65, 0x18, 0x05, 0x20, 0x01,
	0x28, 0x0b, 0x32, 0x17, 0x2e, 0x67, 0x6f, 0x6f, 0x67, 0x6c, 0x65, 0x2e, 0x70, 0x72, 0x6f, 0x74,
	0x6f, 0x62, 0x75, 0x66, 0x2e, 0x53, 0x74, 0x72, 0x75, 0x63, 0x74, 0x48, 0x00, 0x52, 0x0b, 0x73,
	0x74, 0x72, 0x75, 0x63, 0x74, 0x56, 0x61, 0x6c, 0x75, 0x65, 0x12, 0x3b, 0x0a, 0x0a, 0x6c, 0x69,
	0x73, 0x74, 0x5f, 0x76, 0x61, 0x6c, 0x75, 0x65, 0x18, 0x06, 0x20, 0x01, 0x28, 0x0b, 0x32, 0x1a,
	0x2e, 0x67, 0x6f, 0x6f, 0x67, 0x6c, 0x65, 0x2e, 0x70, 0x72, 0x6f, 0x74, 0x6f, 0x62, 0x75, 0x66,
	0x2e, 0x4c, 0x69, 0x73, 0x74, 0x56, 0x61, 0x6c, 0x75, 0x65, 0x48, 0x00, 0x52, 0x09, 0x6c, 0x69,
	0x73, 0x74, 0x56, 0x61, 0x6c, 0x75, 0x65, 0x42, 0x06, 0x0a, 0x04, 0x6b, 0x69, 0x6e, 0x64, 0x22,
	0x3b, 0x0a, 0x09, 0x4c, 0x69, 0x73, 0x74, 0x56, 0x61, 0x6c, 0x75, 0x65, 0x12, 0x2e, 0x0a, 0x06,
	0x76, 0x61, 0x6c, 0x75, 0x65, 0x73, 0x18, 0x01, 0x20, 0x03, 0x28, 0x0b, 0x32, 0x16, 0x2e, 0x67,
	0x6f, 0x6f, 0x67, 0x6c, 0x65, 0x2e, 0x70, 0x72, 0x6f, 0x74, 0x6f, 0x62, 0x75, 0x66, 0x2e, 0x56,
	0x61, 0x6c, 0x75, 0x65, 0x52, 0x06, 0x76, 0x61, 0x6c, 0x75, 0x65, 0x73, 0x2a, 0x1b, 0x0a, 0x09,
	0x4e, 0x75, 0x6c, 0x6c, 0x56, 0x61, 0x6c, 0x75, 0x65, 0x12, 0x0e, 0x0a, 0x0a, 0x4e, 0x55, 0x4c,
	0x4c, 0x5f, 0x56, 0x41, 0x4c, 0x55, 0x45, 0x10, 0x00, 0x42, 0x7f, 0x0a, 0x13, 0x63, 0x6f, 0x6d,
	0x2e, 0x67, 0x6f, 0x6f, 0x67, 0x6c, 0x65, 0x2e, 0x70, 0x72, 0x6f, 0x74, 0x6f, 0x62, 0x75, 0x66,
	0x42, 0x0b, 0x53, 0x74, 0x72, 0x75, 0x63, 0x74, 0x50, 0x72, 0x6f, 0x74, 0x6f, 0x50, 0x01, 0x5a,
	0x2f, 0x67, 0x6f, 0x6f, 0x67, 0x6c, 0x65, 0x2e, 0x67, 0x6f, 0x6c, 0x61, 0x6e, 0x67, 0x2e, 0x6f,
	0x72, 0x67, 0x2f, 0x70, 0x72, 0x6f, 0x74, 0x6f, 0x62, 0x75, 0x66, 0x2f, 0x74, 0x79, 0x70, 0x65,
	0x73, 0x2f, 0x6b, 0x6e, 0x6f, 0x77, 0x6e, 0x2f, 0x73, 0x74, 0x72, 0x75, 0x63, 0x74, 0x70, 0x62,
	0xf8, 0x01, 0x01, 0xa2, 0x02, 0x03, 0x47, 0x50, 0x42, 0xaa, 0x02, 0x1e, 0x47, 0x6f, 0x6f, 0x67,
	0x6c, 0x65, 0x2e, 0x50, 0x72, 0x6f, 0x74, 0x6f, 0x62, 0x75, 0x66, 0x2e, 0x57, 0x65, 0x6c, 0x6c,
	0x4b, 0x6e, 0x6f, 0x77, 0x6e, 0x54, 0x79, 0x70, 0x65, 0x73, 0x62, 0x06, 0x70, 0x72, 0x6f, 0x74,
	0x6f, 0x33,
}

var (
	file_google_protobuf_struct_proto_rawDescOnce sync.Once
	file_google_protobuf_struct_proto_rawDescData = file_google_protobuf_struct_proto_rawDesc
)

func file_google_protobuf_struct_proto_rawDescGZIP() []byte {
	file_google_protobuf_struct_proto_rawDescOnce.Do(func() {
		file_google_protobuf_struct_proto_rawDescData = protoimpl.X.CompressGZIP(file_google_protobuf_struct_proto_rawDescData)
'''

# cargo 1.x `cargo generate-lockfile` for serde, serde_json, clap and anyhow (real, 30 packages)
CARGO_LOCK = r'''# This file is automatically @generated by Cargo.
# It is not intended for manual editing.
version = 4

[[package]]
name = "anstream"
version = "1.0.0"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "824a212faf96e9acacdbd09febd34438f8f711fb84e09a8916013cd7815ca28d"
dependencies = [
 "anstyle",
 "anstyle-parse",
 "anstyle-query",
 "anstyle-wincon",
 "colorchoice",
 "is_terminal_polyfill",
 "utf8parse",
]

[[package]]
name = "anstyle"
version = "1.0.14"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "940b3a0ca603d1eade50a4846a2afffd5ef57a9feac2c0e2ec2e14f9ead76000"

[[package]]
name = "anstyle-parse"
version = "1.0.0"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "52ce7f38b242319f7cabaa6813055467063ecdc9d355bbb4ce0c68908cd8130e"
dependencies = [
 "utf8parse",
]

[[package]]
name = "anstyle-query"
version = "1.1.5"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "40c48f72fd53cd289104fc64099abca73db4166ad86ea0b4341abe65af83dadc"
dependencies = [
 "windows-sys",
]

[[package]]
name = "anstyle-wincon"
version = "3.0.11"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "291e6a250ff86cd4a820112fb8898808a366d8f9f58ce16d1f538353ad55747d"
dependencies = [
 "anstyle",
 "once_cell_polyfill",
 "windows-sys",
]

[[package]]
name = "anyhow"
version = "1.0.104"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "330a5ed07fa54e4702c9d6c4174f74427fc0ef6e214bbd677ae50a5099946470"

[[package]]
name = "clap"
version = "4.6.7"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "aa8876b300ab35ba921adea3dfd70157a46249b33f95c9084ae5709785478946"
dependencies = [
 "clap_builder",
 "clap_derive",
]

[[package]]
name = "clap_builder"
version = "4.6.7"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "ec0797fb7aeb1406c84efac526901f7ec3ead2124f946b494e72879d4b54704d"
dependencies = [
 "anstream",
 "anstyle",
 "clap_lex",
 "strsim",
]

[[package]]
name = "clap_derive"
version = "4.6.7"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "f9c751b79415d4e559e3d1fcf128e09e720eb673a06d26cf6f392d37d75b66e0"
dependencies = [
 "heck",
 "proc-macro2",
 "quote",
 "syn",
]

[[package]]
name = "clap_lex"
version = "1.1.1"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "1c133bc6a41be0d194c306b5506d15e6feeea7b1d6604bd3f8310dfb2ca96486"

[[package]]
name = "colorchoice"
version = "1.0.5"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "1d07550c9036bf2ae0c684c4297d503f838287c83c53686d05370d0e139ae570"

[[package]]
name = "demo"
version = "0.1.0"
dependencies = [
 "anyhow",
 "clap",
 "serde",
 "serde_json",
]

[[package]]
name = "heck"
version = "0.5.0"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "2304e00983f87ffb38b55b444b5e3b60a884b5d30c0fca7d82fe33449bbe55ea"

[[package]]
name = "is_terminal_polyfill"
version = "1.70.2"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "a6cb138bb79a146c1bd460005623e142ef0181e3d0219cb493e02f7d08a35695"

[[package]]
name = "itoa"
version = "1.0.18"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "8f42a60cbdf9a97f5d2305f08a87dc4e09308d1276d28c869c684d7777685682"

[[package]]
name = "memchr"
version = "2.8.3"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "cf8baf1c55e62ffcace7a9f06f4bd9cd3f0c4beb022d3b367256b91b87513d98"

[[package]]
name = "once_cell_polyfill"
version = "1.70.2"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "384b8ab6d37215f3c5301a95a4accb5d64aa607f1fcb26a11b5303878451b4fe"

[[package]]
name = "proc-macro2"
version = "1.0.107"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "985e7ec9bb745e6ce6535b544d84d6cd6f7ad8bd711c398938ae983b91a766d9"
dependencies = [
 "unicode-ident",
]

[[package]]
name = "quote"
version = "1.0.47"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "1fbf4db142a473a8d80c26bbf18454ed458bf8d26c8219c331daecfdbd079001"
dependencies = [
 "proc-macro2",
]

[[package]]
name = "serde"
version = "1.0.229"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "4148590afebada386688f18773da617792bf2ef03ffc1e4cbd2b1d45b023e0ba"
dependencies = [
 "serde_core",
 "serde_derive",
]

[[package]]
name = "serde_core"
version = "1.0.229"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "67dca2c9c51e58a4791a4b1ed58308b39c64224d349a935ab5039aa360942a48"
dependencies = [
 "serde_derive",
]

[[package]]
name = "serde_derive"
version = "1.0.229"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "e7a5d71263a5a7d47b41f6b3f06ba276f10cc18b0931f1799f710578e2309348"
dependencies = [
 "proc-macro2",
 "quote",
 "syn",
]

[[package]]
name = "serde_json"
version = "1.0.154"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "e7e9cc8b1b85264074fbcc02a88680c4096b1e47df8f739dceb03bf482f04bd6"
dependencies = [
 "itoa",
 "memchr",
 "serde",
 "serde_core",
 "zmij",
]

[[package]]
name = "strsim"
version = "0.11.1"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "7da8b5736845d9f2fcb837ea5d9e2628564b3b043a70948a3f0b778838c5fb4f"

[[package]]
name = "syn"
version = "3.0.9"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "d78c8dee4c7bf0e14673097256fed6142ce9d3b85a408189d07482442145823b"
dependencies = [
 "proc-macro2",
 "quote",
 "unicode-ident",
]

[[package]]
name = "unicode-ident"
version = "1.0.27"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "a2c754d6c33795a1c324727428e5a7dedb5b06195f9890bdbcba760d3e246563"

[[package]]
name = "utf8parse"
version = "0.2.2"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "06abde3611657adf66d383f00b093d7faecc7fa57071cce2578660c9f1010821"

[[package]]
name = "windows-link"
version = "0.2.1"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "f0805222e57f7521d6a62e36fa9163bc891acd422f971defe97d64e70d0a4fe5"

[[package]]
name = "windows-sys"
version = "0.61.2"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "ae137229bcbd6cdf0f7b80a31df61766145077ddf49416a728b02cb3921ff3fc"
dependencies = [
 "windows-link",
]

[[package]]
name = "zmij"
version = "1.0.23"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "29666d0abbfad1e3dc4dcf6144730dd3a3ab225bbbdac83319345b1b44ccfc1b"
'''

# LibreOffice 24.x `soffice --convert-to csv` of a sheet whose widest used cell is column N (real)
LO_PAD_CSV = r'''order_id,sku,qty,unit_price,customer,note,,,,,,,,reviewed
1001,SKU-0037,2,3.85,Li Wei,"gift, wrap",,,,,,,,
1002,SKU-0074,3,5.2,Sam Okafor,,,,,,,,,
1003,SKU-0111,4,6.55,Mia Rossi,backorder,,,,,,,,
1004,SKU-0148,5,7.9,Tom Brandt,"fragile, handle with care",,,,,,,,
1005,SKU-0185,1,9.25,Eva Novak,,,,,,,,,
1006,SKU-0222,2,10.6,Ana Silva,"said ""call first""",,,,,,,,
1007,SKU-0259,3,11.95,Li Wei,,,,,,,,,
1008,SKU-0296,4,13.3,Sam Okafor,,,,,,,,,
1009,SKU-0333,5,14.65,Mia Rossi,"gift, wrap",,,,,,,,
1010,SKU-0370,1,16,Tom Brandt,,,,,,,,,
1011,SKU-0407,2,17.35,Eva Novak,backorder,,,,,,,,ok
1012,SKU-0444,3,18.7,Ana Silva,"fragile, handle with care",,,,,,,,
1013,SKU-0481,4,20.05,Li Wei,,,,,,,,,
1014,SKU-0518,5,21.4,Sam Okafor,"said ""call first""",,,,,,,,
1015,SKU-0555,1,22.75,Mia Rossi,,,,,,,,,
1016,SKU-0592,2,24.1,Tom Brandt,,,,,,,,,
1017,SKU-0629,3,25.45,Eva Novak,"gift, wrap",,,,,,,,
1018,SKU-0666,4,26.8,Ana Silva,,,,,,,,,
'''

# pandas 3.0 DataFrame.to_html() and DataFrame._repr_html_() (real)
HTML_DF = r'''<table border="1" class="dataframe">
  <thead>
    <tr style="text-align: right;">
      <th></th>
      <th>qty</th>
      <th>price</th>
      <th>note</th>
    </tr>
    <tr>
      <th>sku</th>
      <th></th>
      <th></th>
      <th></th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <th>SKU-0001</th>
      <td>3</td>
      <td>1.25</td>
      <td>ok</td>
    </tr>
    <tr>
      <th>SKU-0002</th>
      <td>6</td>
      <td>2.50</td>
      <td>ok</td>
    </tr>
    <tr>
      <th>SKU-0003</th>
      <td>9</td>
      <td>3.75</td>
      <td>check</td>
    </tr>
    <tr>
      <th>SKU-0004</th>
      <td>12</td>
      <td>5.00</td>
      <td>ok</td>
    </tr>
    <tr>
      <th>SKU-0005</th>
      <td>15</td>
      <td>6.25</td>
      <td>check</td>
    </tr>
  </tbody>
</table>'''
HTML_REPR = r'''<div>
<style scoped>
    .dataframe tbody tr th:only-of-type {
        vertical-align: middle;
    }

    .dataframe tbody tr th {
        vertical-align: top;
    }

    .dataframe thead th {
        text-align: right;
    }
</style>
<table border="1" class="dataframe">
  <thead>
    <tr style="text-align: right;">
      <th></th>
      <th>qty</th>
      <th>price</th>
      <th>note</th>
    </tr>
    <tr>
      <th>sku</th>
      <th></th>
      <th></th>
      <th></th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <th>SKU-0001</th>
      <td>3</td>
      <td>1.25</td>
      <td>ok</td>
    </tr>
    <tr>
      <th>SKU-0002</th>
      <td>6</td>
      <td>2.50</td>
      <td>ok</td>
    </tr>
    <tr>
      <th>SKU-0003</th>
      <td>9</td>
      <td>3.75</td>
      <td>check</td>
    </tr>
    <tr>
      <th>SKU-0004</th>
      <td>12</td>
      <td>5.00</td>
      <td>ok</td>
    </tr>
    <tr>
      <th>SKU-0005</th>
      <td>15</td>
      <td>6.25</td>
      <td>check</td>
    </tr>
  </tbody>
</table>
</div>
'''

# MarkItDown 0.1.7 on an .xlsx written with openpyxl (real converter output)
MARKITDOWN_MD = r'''## Q3 sales
| Region | Product | Units | Revenue | Discount | Unnamed: 5 | Rep | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| EMEA | Widget | 120.0 | 3400.50 | NaN | NaN | Ana | NaN |
| EMEA | Gadget | 75.0 | 2100.00 | 0.10 | NaN | Ana | promo |
| APAC | Widget | 98.0 | 2900.25 | NaN | NaN | Li | NaN |
| APAC | Gizmo | NaN | NaN | NaN | NaN | Li | backorder |
| AMER | Widget | 210.0 | 6100.00 | 0.05 | NaN | Sam | NaN |
| AMER | Gadget | 44.0 | 1320.00 | NaN | NaN | Sam | NaN |
| AMER | Gizmo | 31.0 | 1550.00 | NaN | NaN | Sam | new |

## Targets
| Region | Target |
| --- | --- |
| EMEA | 5000 |
| APAC | 4500 |
| AMER | 9000 |'''

# LibreOffice share/wizards/resources_{de,ja,fr}.properties excerpts (MPL-2.0, real, uppercase \uXXXX escapes)
PROPS_DE = r'''RID_COMMON_0=Das Verzeichnis '%1' konnte nicht erstellt werden.<BR>M\u00F6glicherweise ist auf Ihrer Festplatte nicht gen\u00FCgend Speicherplatz vorhanden.
RID_COMMON_1=Das Textdokument konnte nicht erstellt werden.<BR>Bitte \u00FCberpr\u00FCfen Sie, ob das Modul \u201E%PRODUCTNAME Writer\u201C installiert ist.
RID_COMMON_2=Das Tabellendokument konnte nicht erstellt werden.<BR>Bitte pr\u00FCfen Sie, ob das Modul \u201E%PRODUCTNAME Calc\u201C installiert ist.
RID_COMMON_3=Die Pr\u00E4sentation konnte nicht erstellt werden.<BR>Bitte \u00FCberpr\u00FCfen Sie, ob das Modul \u201E%PRODUCTNAME Impress\u201C installiert ist.
RID_COMMON_4=Die Zeichnung konnte nicht erstellt werden.<BR>Bitte \u00FCberpr\u00FCfen Sie, ob das Modul \u201E%PRODUCTNAME Draw\u201C installiert ist.
RID_COMMON_5=Das Formeldokument konnte nicht erstellt werden.<BR>Bitte pr\u00FCfen Sie, ob das Modul \u201E%PRODUCTNAME Math\u201C installiert ist.
RID_COMMON_6=Die ben\u00F6tigten Dateien konnten nicht gefunden werden.<BR>Bitte starten Sie das %PRODUCTNAME-Installationsprogramm und w\u00E4hlen Sie die Option 'Reparatur' aus.
RID_COMMON_7=Die Datei '<PATH>' existiert bereits.<BR><BR>M\u00F6chten Sie die bestehende Datei \u00FCberschreiben?
RID_COMMON_8=Ja
RID_COMMON_9=Ja, alle
'''
PROPS_JA = r'''RID_COMMON_0=\u30C7\u30A3\u30EC\u30AF\u30C8\u30EA '%1' \u3092\u4F5C\u6210\u3067\u304D\u307E\u305B\u3093\u3067\u3057\u305F\u3002 <BR>\u30C7\u30A3\u30EC\u30AF\u30C8\u30EA\u306E\u4F5C\u6210\u306B\u5FC5\u8981\u306A\u30C7\u30A3\u30B9\u30AF\u5BB9\u91CF\u304C\u8DB3\u308A\u306A\u3044\u53EF\u80FD\u6027\u304C\u3042\u308A\u307E\u3059\u3002
RID_COMMON_1=\u6587\u66F8\u30C9\u30AD\u30E5\u30E1\u30F3\u30C8\u3092\u4F5C\u6210\u3067\u304D\u307E\u305B\u3093\u3067\u3057\u305F\u3002<BR>\u300C%PRODUCTNAME Writer\u300D\u30E2\u30B8\u30E5\u30FC\u30EB\u304C\u30A4\u30F3\u30B9\u30C8\u30FC\u30EB\u3055\u308C\u3066\u3044\u308B\u304B\u78BA\u8A8D\u3057\u3066\u304F\u3060\u3055\u3044\u3002
RID_COMMON_2=\u8868\u8A08\u7B97\u30C9\u30AD\u30E5\u30E1\u30F3\u30C8\u3092\u4F5C\u6210\u3067\u304D\u307E\u305B\u3093\u3067\u3057\u305F\u3002<BR>\u300C%PRODUCTNAME Calc\u300D\u30E2\u30B8\u30E5\u30FC\u30EB\u304C\u30A4\u30F3\u30B9\u30C8\u30FC\u30EB\u3055\u308C\u3066\u3044\u308B\u304B\u78BA\u8A8D\u3057\u3066\u304F\u3060\u3055\u3044\u3002
RID_COMMON_3=\u30D7\u30EC\u30BC\u30F3\u30C6\u30FC\u30B7\u30E7\u30F3\u3092\u4F5C\u6210\u3067\u304D\u307E\u305B\u3093\u3067\u3057\u305F\u3002<BR>\u300C%PRODUCTNAME Impress\u300D\u30E2\u30B8\u30E5\u30FC\u30EB\u304C\u30A4\u30F3\u30B9\u30C8\u30FC\u30EB\u3055\u308C\u3066\u3044\u308B\u304B\u3069\u3046\u304B\u78BA\u8A8D\u3057\u3066\u304F\u3060\u3055\u3044\u3002
RID_COMMON_4=\u56F3\u5F62\u63CF\u753B\u3092\u4F5C\u6210\u3067\u304D\u307E\u305B\u3093\u3067\u3057\u305F\u3002<BR>\u300C%PRODUCTNAME Draw\u300D\u30E2\u30B8\u30E5\u30FC\u30EB\u304C\u30A4\u30F3\u30B9\u30C8\u30FC\u30EB\u3055\u308C\u3066\u3044\u308B\u304B\u3069\u3046\u304B\u78BA\u8A8D\u3057\u3066\u304F\u3060\u3055\u3044\u3002
'''
PROPS_FR = r'''RID_COMMON_0=Impossible de cr\u00E9er le dossier '%1'. <BR>Il n'y a sans doute plus assez de place sur le disque dur.
RID_COMMON_1=Impossible de cr\u00E9er le document texte.<BR>Veuillez v\u00E9rifier si le module \u00AB %PRODUCTNAME Writer \u00BB est install\u00E9.
RID_COMMON_2=Impossible de cr\u00E9er la feuille de calcul.<BR>Veuillez v\u00E9rifier si le module \u00AB %PRODUCTNAME Calc \u00BB est install\u00E9.
RID_COMMON_3=Impossible de cr\u00E9er la pr\u00E9sentation.<BR>Veuillez v\u00E9rifier si le module \u00AB %PRODUCTNAME Impress \u00BB est install\u00E9.
RID_COMMON_4=Impossible de cr\u00E9er le dessin.<BR>Veuillez v\u00E9rifier si le module \u00AB %PRODUCTNAME Draw \u00BB est install\u00E9.
RID_COMMON_5=Impossible de cr\u00E9er la formule.<BR>Veuillez v\u00E9rifier si le module \u00AB %PRODUCTNAME Math \u00BB est install\u00E9.
'''

# openssl req -x509 (real, throwaway, public certificate only)
CERT_PEM = r'''-----BEGIN CERTIFICATE-----
MIIDLzCCAhegAwIBAgIUT0Sk0H+Rl7ST2OBybPb2X54URBAwDQYJKoZIhvcNAQEL
BQAwJzEWMBQGA1UEAwwNZGVtby5pbnRlcm5hbDENMAsGA1UECgwEQWNtZTAeFw0y
NjExMDQxNzQ3NDhaFw0yNjEyMDQxNzQ3NDhaMCcxFjAUBgNVBAMMDWRlbW8uaW50
ZXJuYWwxDTALBgNVBAoMBEFjbWUwggEiMA0GCSqGSIb3DQEBAQUAA4IBDwAwggEK
AoIBAQCxCCvcS2ww4o35uDT4WcqpX4Ls2kXgh4GylIXkcoYrOFqwh34zuwPsiZl/
nFikmE5xp9doWzXDiVvwuYSvtRRg8Wr8HueIubPsLyR/Xq4ADtJnDYNTM0+jAAch
Amf/OntuwksKpfiv4QO2JpN8ilxCmTxTWAafhljxlru56H0rZHkL+iVuovgNC7yz
mSkQb6490XPiULHdw594nVeyIVhiqHOVqgk8IdpSDHClVjfvaJQ0S0AtGEh6nbAH
c+v1D9SW5v1oYOrJyP1K8EMqLGL6Il77PJDr+4GGQd4BAkqHIpdfwRRfXmTIBlAL
kBgsJf+so8w87RO9lOq6pPRkoLvHAgMBAAGjUzBRMB0GA1UdDgQWBBSqoSDaCoPa
xu/eSdKPfrdnDCkBwjAfBgNVHSMEGDAWgBSqoSDaCoPaxu/eSdKPfrdnDCkBwjAP
BgNVHRMBAf8EBTADAQH/MA0GCSqGSIb3DQEBCwUAA4IBAQCkEJYP2DECfLvBijpa
/+ElcPDCya3M7qtpBp9I1ZB+XOH7FISsr11vRqZKiBekq9ygMUNPjrJzkULefacN
XOWYIuAhRwcL+wd6SrrnyEBGhD4lGsgJhzEMWKwtJOzRwNNqpJzYv3umhBJWn6vx
Y5PBnUM/YUWDlRNgOSGkesH6y4NNeumsjVqEj1dS9PnWs5bSqjjpC6zCLIb0yVZh
d+iYxnq6bc4khOUd6Uyflhuo8tJF6i9Uw4S+yXWROL9TDlZdS/vI7ikgn17OFYwc
6I2b7KaZX7ujB/nWKF5b5+r3THDZxeFnRx68bKBcYpzmuatT3fWmYE+0k8dmMF2P
NzyE
-----END CERTIFICATE-----
'''


# ===============================================================================================================
# #1434 Byte-array source literals to joined hex lines. protoc-gen-go writes the file descriptor as
#   var file_x_rawDesc = []byte{\n\t0x0a, 0x1e, ... 16 per line,\n}   and `xxd -i` writes 12 per line, no comma on the last.
# Each regular run of lines "0xNN, 0xNN, ..." becomes one header line plus one line of joined hex pairs per source line.
# Roundtrip: the header carries the width, the line count and whether the last line ends with a comma; the indent is
# the header's own indent, so the original is rebuilt byte for byte.

_HEXLINE = re.compile(r"^([ \t]*)((?:0x[0-9a-fA-F]{2}, )*0x[0-9a-fA-F]{2})(,?)$")
_BHDR = re.compile(r"^([ \t]*)§bytes cols=(\d+) lines=(\d+) tail=([cn])$")
HEX_LEGEND = "§hexlit: lines after a '§bytes' header are hex pairs standing for 0xNN, literals\n"
HEX_RAW = "§raw\n"


def _fold_bytes(text: str) -> str | None:
    lines = text.split("\n")
    out: list[str] = []
    i, n, changed = 0, len(lines), False
    while i < n:
        m = _HEXLINE.match(lines[i])
        if not m or not m.group(3):
            out.append(lines[i])
            i += 1
            continue
        ind, k = m.group(1), m.group(2).count("0x")
        run = [m]
        j = i + 1
        while j < n:
            mj = _HEXLINE.match(lines[j])
            if not mj or mj.group(1) != ind:
                break
            c = mj.group(2).count("0x")
            if c > k:
                break
            run.append(mj)
            j += 1
            if c < k or not mj.group(3):  # a short or comma-less line can only be the last of a run
                break
        full = all(r.group(2).count("0x") == k and r.group(3) for r in run[:-1])
        if len(run) < 3 or k < 4 or not full:
            out.extend(lines[i:j])
            i = j
            continue
        tail = "c" if run[-1].group(3) else "n"
        out.append(f"{ind}§bytes cols={k} lines={len(run)} tail={tail}")
        out.extend(ind + r.group(2).replace("0x", "").replace(", ", "") for r in run)
        changed = True
        i = j
    return HEX_LEGEND + "\n".join(out) if changed else None


@_failopen
def fold_bytes(text: str) -> str:
    if text.startswith(SIGIL):
        return HEX_RAW + text
    if "§bytes " in text:
        return text
    body = _fold_bytes(text)
    return body if body is not None and len(body) < len(text) else text


def unfold_bytes(text: str) -> str:
    if text.startswith(HEX_RAW):
        return text[len(HEX_RAW):]
    if not text.startswith(HEX_LEGEND):
        return text
    lines = text[len(HEX_LEGEND):].split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        m = _BHDR.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue
        ind, k, cnt, tail = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)
        body = lines[i + 1 : i + 1 + cnt]
        for t, ln in enumerate(body):
            h = ln[len(ind):]
            items = ", ".join("0x" + h[p : p + 2] for p in range(0, len(h), 2))
            out.append(ind + items + ("," if t < cnt - 1 or tail == "c" else ""))
        i += 1 + cnt
    return "\n".join(out)


def _xxd_i(name: str, size: int, seed: int, upper: bool = False) -> str:
    rng = random.Random(seed)  # reconstructed from memory: `xxd -i file` layout
    data = bytes(rng.randrange(256) for _ in range(size))
    fmt = "0x%02X" if upper else "0x%02x"
    rows = [", ".join(fmt % b for b in data[p : p + 12]) for p in range(0, size, 12)]
    body = ",\n".join("  " + r for r in rows)
    return f"unsigned char {name}[] = {{\n{body}\n}};\nunsigned int {name}_len = {size};\n"


SMALL_SLICES = (
    "package main\n\nvar pngMagic = []byte{0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a}\n\n"
    "var table = []byte{\n\t0x00, // NUL\n\t0x0a, // LF\n\t0x0d, // CR\n}\n\nvar dec = []int{\n\t137, 80, 78, 71, 13, 10, 26, 10,\n\t0, 0, 0, 13, 73, 72, 68, 82,\n}\n"
)
SHORT_RUN = "var k = []byte{\n\t0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08,\n\t0x09, 0x0a,\n}\n"  # two lines only: not worth a header

case(
    1434,
    claim="Runs of 3+ lines of '0xNN, 0xNN, ...' byte literals (equal width, same indent, comma at line end) become a '§bytes' header plus one joined-hex line per source line; decode rebuilds the exact original, including the width of the last line and a comma-less last line.",
    encode=fold_bytes,
    decode=unfold_bytes,
    fixtures=[
        Fixture("real protoc-gen-go durationpb rawDesc (17 lines of bytes)", GO_DURATION, True),
        Fixture("real protoc-gen-go structpb rawDesc (49 lines)", GO_STRUCT, True),
        Fixture("two blocks in one file", GO_DURATION + "\n" + GO_STRUCT, True),
        Fixture("xxd -i, 12 per line, no comma on the last line (reconstructed from memory)", _xxd_i("logo_png", 200, 5), True),
        Fixture("xxd -i with uppercase hex digits", _xxd_i("fw_bin", 150, 9, upper=True), True),
        Fixture("small slices, commented table, decimal array", SMALL_SLICES, False),
        Fixture("two-line run is not worth a header", SHORT_RUN, False),
        Fixture("plain prose", "Download the model weights and verify the checksum before use.\n", False),
        Fixture("input that starts like encoder output", HEX_LEGEND + "§bytes cols=2 lines=1 tail=c\n0a0b\n", None),
        Fixture("sigil header in the middle of source", GO_DURATION + "\t§bytes cols=4 lines=3 tail=c\n", None),
        Fixture("starts with the sigil", "§ price list\n" + GO_DURATION, None),
    ],
    min_gain=0.3,
    idempotent=False,  # re-encoding an encoded text must escape it (it starts with the sigil), so it grows by design
)

# ===============================================================================================================
# #1435 kubectl get secret -o yaml: every `data:` value of a Secret is base64. When ALL values of a block are canonical
# base64 of printable UTF-8, the block is rewritten as `stringData:` (the write-only plain-text twin that `kubectl apply`
# accepts) with plain scalars, double-quoted scalars or `|` block scalars. Decode re-encodes each value. Blocks with any
# binary value stay base64. A YAML comment line is the header; text that starts with it is escaped.

SEC_HDR = "# ctxprune: Secret data decoded from base64 into stringData (base64 of each value is the original)\n"
SEC_RAW = "# ctxprune: raw\n"
_SDATA = re.compile(r"^( *)data:$")
_SENTRY = re.compile(r"^( *)([A-Za-z0-9._-]+): ([A-Za-z0-9+/]+={0,2})$")
_SPLAIN = re.compile(r"^[A-Za-z][A-Za-z0-9_./@+=?&%~()$*!;:,-]*(?: [A-Za-z0-9_./@+=?&%~()$*!;:,-]+)*$")
_YAML_WORDS = frozenset("y n yes no on off true false null".split())


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _in_secret(lines: list[str], i: int, end: int, col: int) -> bool:
    """True when a `kind: Secret` key sits at indent `col` in the same mapping as lines[i] (a document or list item)."""
    want = " " * col + "kind: Secret"
    j = i - 1
    while j >= 0:
        ln = lines[j]
        if ln.strip():
            ind = _indent_of(ln)
            if ln == want or (ind == col - 2 and ln[ind:].startswith("- ") and " " * col + ln[ind + 2 :] == want):
                return True
            if ln.startswith("---") and ind == 0:
                break
            if ind < col:
                break
        j -= 1
    j = end
    while j < len(lines):
        ln = lines[j]
        if ln.strip():
            if ln == want:
                return True
            if _indent_of(ln) < col or (ln.startswith("---") and _indent_of(ln) == 0):
                break
        j += 1
    return False


def _b64_text(b64: str) -> str | None:
    try:
        raw = base64.b64decode(b64, validate=True)
        if base64.b64encode(raw).decode() != b64:
            return None
        s = raw.decode("utf-8")
    except Exception:  # noqa: BLE001
        return None
    if not s or not all(c.isprintable() or c in "\n\t" for c in s):
        return None
    return s


def _yaml_value(key: str, s: str, ind: str) -> list[str]:
    if "\n" not in s:
        if _SPLAIN.match(s) and ": " not in s and " #" not in s and not s.endswith(":") and s.lower() not in _YAML_WORDS:
            return [f"{ind}{key}: {s}"]
        return [f"{ind}{key}: {json.dumps(s, ensure_ascii=False)}"]
    core = s.rstrip("\n")
    k = len(s) - len(core)
    ls = core.split("\n")
    ok = bool(core) and not ls[0].startswith((" ", "\t")) and ls[0] != "" and not any(l.strip() == "" and l != "" for l in ls)
    if not ok:
        return [f"{ind}{key}: {json.dumps(s, ensure_ascii=False)}"]
    chomp = "-" if k == 0 else ("" if k == 1 else "+")
    body = ls + [""] * (k - 1 if k >= 2 else 0)
    return [f"{ind}{key}: |{chomp}"] + [(ind + "  " + l) if l else "" for l in body]


def _fold_secret(text: str) -> str | None:
    lines = text.split("\n")
    if any(re.match(r"^ *stringData:$", l) for l in lines):
        return None
    out: list[str] = []
    i, changed = 0, False
    while i < len(lines):
        m = _SDATA.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue
        col = len(m.group(1))
        j, entries = i + 1, []
        while j < len(lines):
            e = _SENTRY.match(lines[j])
            if not e or len(e.group(1)) != col + 2:
                break
            entries.append((e.group(2), e.group(3)))
            j += 1
        nxt_ok = j >= len(lines) or (lines[j].strip() != "" and _indent_of(lines[j]) <= col)
        vals = [_b64_text(v) for _, v in entries]
        if not entries or not nxt_ok or any(v is None for v in vals) or not _in_secret(lines, i, j, col):
            out.append(lines[i])
            i += 1
            continue
        out.append(m.group(1) + "stringData:")
        for (k, _), v in zip(entries, vals):
            out.extend(_yaml_value(k, v, " " * (col + 2)))
        changed = True
        i = j
    return SEC_HDR + "\n".join(out) if changed else None


@_failopen
def fold_secret(text: str) -> str:
    if text.startswith("# ctxprune"):
        return SEC_RAW + text
    body = _fold_secret(text)
    return body if body is not None and len(body) < len(text) else text


def unfold_secret(text: str) -> str:
    if text.startswith(SEC_RAW):
        return text[len(SEC_RAW):]
    if not text.startswith(SEC_HDR):
        return text
    lines = text[len(SEC_HDR):].split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        m = re.match(r"^( *)stringData:$", lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue
        col = len(m.group(1))
        pre = " " * (col + 2)
        out.append(m.group(1) + "data:")
        i += 1
        while i < len(lines) and lines[i].startswith(pre) and not lines[i].startswith(pre + " "):
            key, _, rest = lines[i][len(pre):].partition(": ")
            i += 1
            if rest.startswith("|"):
                chomp = rest[1:]
                blk = []
                while i < len(lines) and (lines[i] == "" or lines[i].startswith(pre + "  ")):
                    blk.append(lines[i][len(pre) + 2 :])
                    i += 1
                if chomp == "+":
                    s = "\n".join(blk) + "\n"
                else:
                    while blk and blk[-1] == "" and chomp != "+":
                        blk.pop()  # blank lines after a clip/strip block are not content
                    s = "\n".join(blk) + ("\n" if chomp == "" else "")
            elif rest.startswith('"'):
                s = json.loads(rest)
            else:
                s = rest
            out.append(f"{pre}{key}: {base64.b64encode(s.encode()).decode()}")
    return "\n".join(out)


def _b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


def _secret(name: str, entries: dict[str, str], ns: str = "prod", kind: str = "Secret", raw: dict[str, str] | None = None) -> str:
    """`kubectl get secret NAME -o yaml` layout (reconstructed from memory): keys sorted, data values base64."""
    rows = [f"  {k}: {_b64(v)}" for k, v in entries.items()] + [f"  {k}: {v}" for k, v in (raw or {}).items()]
    rows.sort()
    return (
        f"apiVersion: v1\ndata:\n" + "\n".join(rows) + f"\nkind: {kind}\nmetadata:\n  creationTimestamp: \"2026-10-02T09:12:44Z\"\n"
        f"  name: {name}\n  namespace: {ns}\n  resourceVersion: \"184233\"\n  uid: 7f9c2d3e-5a1b-4c8e-9d20-3b6a1f0e4c77\ntype: Opaque\n"
    )


_PRIV = "-----BEGIN " + "PRIVATE KEY-----"  # split so the test file holds no literal key header
_FAKE_KEY = _PRIV + "\n" + "\n".join(base64.b64encode(random.Random(i).randbytes(48)).decode() for i in range(6)) + "\n-----END " + "PRIVATE KEY-----\n"
SECRET_APP = _secret(
    "app-credentials",
    {
        "DATABASE_URL": "postgres://app:s3cr3t-Pa55@db.prod.svc.cluster.local:5432/orders?sslmode=require",
        "REDIS_PASSWORD": "kV7w2pQ9xLm4",
        "api-token": "ghp_exampleTokenValue0123456789abcdef",
        "config.json": '{\n  "region": "eu-west-1",\n  "retries": 3,\n  "endpoints": ["https://a.example.com", "https://b.example.com"]\n}\n',
        "tls.crt": CERT_PEM,
        "tls.key": _FAKE_KEY,
    },
)
def _as_item(doc: str) -> str:
    """Indent one document as a `- ` list item (kubectl get secret -A -o yaml)."""
    ls = doc.rstrip("\n").split("\n")
    return "\n".join(["- " + ls[0]] + ["  " + l for l in ls[1:]]) + "\n"


_DOCKERCFG = json.dumps({"auths": {"ghcr.io": {"username": "ci-bot", "password": "ghp_exampleTokenValue0123456789abcdef", "auth": _b64("ci-bot:ghp_exampleTokenValue0123456789abcdef")}}}, separators=(",", ":"))
SECRET_LIST = (
    "apiVersion: v1\nitems:\n"
    + _as_item(_secret("regcred", {".dockerconfigjson": _DOCKERCFG}, ns="staging"))
    + _as_item(_secret("web-tls", {"tls.crt": CERT_PEM, "tls.key": _FAKE_KEY}, ns="staging"))
    + _as_item(_secret("db-admin", {"username": "admin", "password": "Zk3!mQ-77aa"}, ns="staging"))
    + "kind: List\nmetadata:\n  resourceVersion: \"\"\n"
)
SECRET_SHORT = _secret("tiny", {"username": "admin", "password": "r3ad0nly"})
BIN_BLOCK = _secret("keystore", {"alias": "main"}, raw={"keystore.jks": base64.b64encode(random.Random(3).randbytes(40)).decode()})
CONFIGMAP = "apiVersion: v1\ndata:\n  LOG_LEVEL: debug\n  FEATURE_X: enabled\nkind: ConfigMap\nmetadata:\n  name: app-config\n  namespace: prod\n"
NOT_K8S = "services:\n  web:\n    data:\n      token: dGVzdC10b2tlbi12YWx1ZQ==\n      user: YWRtaW4tdXNlcg==\n"
TRICKY = _secret(
    "tricky",
    {
        "number-like": "123456",
        "bool-like": "true",
        "colon-space": "a: b",
        "hash": "x #y",
        "trailing-nl-2": "line1\nline2\n\n",
        "no-trailing-nl": "line1\nline2",
        "leading-space": "  indented\nsecond\n",
        "quote": 'say "hi"',
        "unicode": "café 你好",
        "tab-line": "a\n\tb\n",
    },
)

case(
    1435,
    claim="In a Secret's `data:` block whose values are all canonical base64 of printable UTF-8, the block becomes `stringData:` with plain, quoted or `|` block scalars (header comment line); decode re-encodes every value to the identical base64 and restores `data:`. Blocks with a binary value, ConfigMaps and non-Secret YAML are left alone.",
    encode=fold_secret,
    decode=unfold_secret,
    fixtures=[
        Fixture("Opaque secret: URL, token, JSON config, real certificate, PEM-shaped key (reconstructed layout)", SECRET_APP, True),
        Fixture("kubectl get secret -A -o yaml List of three (reconstructed layout)", SECRET_LIST, True),
        Fixture("values that need quoting, chomping or indentation care", TRICKY, None),
        Fixture("two short values: the header costs more than it saves", SECRET_SHORT, False),
        Fixture("secret with one binary value keeps base64", BIN_BLOCK, False),
        Fixture("ConfigMap data is already plain text", CONFIGMAP, False),
        Fixture("data: block outside Kubernetes", NOT_K8S, False),
        Fixture("input that starts like the header", SEC_HDR + SECRET_APP, None),
        Fixture("already has stringData", SECRET_APP.replace("kind: Secret", "stringData:\n  extra: x\nkind: Secret"), None),
    ],
    min_gain=0.08,
    idempotent=False,  # an encoded text starts with the header comment, so re-encoding escapes it and grows
)


# ===============================================================================================================
# #1436 HTML tables (pandas DataFrame.to_html, notebooks) to TSV. Equivalent, not roundtrip: table/row attributes and the
# thead/tbody tags are gone, entities are spelled as characters. The cell grid, each cell's th/td role and its text
# (unescaped) and all text outside tables are preserved. Only plain tables convert: no colspan/rowspan, no tags inside
# cells, rectangular, cells without tab or newline, the table on its own lines and not inside a comment, script or pre.

_ATTR = r"""(?:\s+[A-Za-z_:][-\w:.]*(?:\s*=\s*(?:"[^"<>]*"|'[^'<>]*'|[^\s"'<>=`]+))?)"""
_TAG = re.compile(rf"<(/?)([A-Za-z][A-Za-z0-9]*)({_ATTR}*)\s*(/?)>")
_TBL_OPEN = re.compile(r"(?im)^([ \t]*)<table\b")
_TBL_CLOSE = re.compile(r"</table\s*>", re.I)
_RAW_ZONES = ("<!--", "<script", "<style", "<textarea", "<pre", "<xmp", "<code")
_ZONE_END = {"<!--": "-->", "<script": "</script", "<style": "</style", "<textarea": "</textarea", "<pre": "</pre", "<xmp": "</xmp", "<code": "</code"}


def _in_raw_zone(low: str, pos: int) -> bool:
    return any(low.rfind(z, 0, pos) > low.rfind(_ZONE_END[z], 0, pos) for z in _RAW_ZONES)


def _parse_table_inner(inner: str):
    """rows of (kind, raw cell text) from the inside of <table>, or None when the table is not plain."""
    rows, row, cell, kind = [], None, None, None
    pos, n = 0, len(inner)
    while pos < n:
        if inner[pos] == "<":
            m = _TAG.match(inner, pos)
            if not m or m.group(4):
                return None
            closing, name, attrs = bool(m.group(1)), m.group(2).lower(), m.group(3)
            pos = m.end()
            if name in ("thead", "tbody", "tfoot"):
                if row is not None:
                    return None
            elif name == "tr":
                if closing:
                    if row is None or cell is not None:
                        return None
                    rows.append(row)
                    row = None
                else:
                    if row is not None:
                        return None
                    row = []
            elif name in ("th", "td"):
                if closing:
                    if cell is None or kind != name:
                        return None
                    row.append((kind, cell))
                    cell = kind = None
                else:
                    if row is None or cell is not None or re.search(r"(?i)\b(colspan|rowspan|headers)\b", attrs):
                        return None
                    cell, kind = "", name
            else:
                return None
        else:
            nxt = inner.find("<", pos)
            nxt = n if nxt < 0 else nxt
            seg = inner[pos:nxt]
            pos = nxt
            if ">" in seg:
                return None
            if cell is not None:
                cell += seg
            elif seg.strip():
                return None
    if row is not None or cell is not None or not rows:
        return None
    return rows


def _shape(rows):
    """(head rows, rowth) when the th/td roles follow the regular pattern, else None."""
    cols = len(rows[0])
    if cols < 1 or any(len(r) != cols for r in rows):
        return None
    head = 0
    while head < len(rows) and all(k == "th" for k, _ in rows[head]):
        head += 1
    body = rows[head:]
    rowth = 1 if body and body[0][0][0] == "th" else 0
    want = ["th" if (j == 0 and rowth) else "td" for j in range(cols)]
    if any([k for k, _ in r] != want for r in body):
        return None
    return head, rowth


@_total
def html_tables_to_tsv(text: str) -> str:
    low = text.lower()
    opens = [m.start() for m in re.finditer(r"(?i)<table\b", text)]
    closes = [m.start() for m in _TBL_CLOSE.finditer(text)]
    out: list[str] = []
    pos = 0
    for m in _TBL_OPEN.finditer(text):
        if m.start() < pos:
            continue
        if bisect.bisect_left(opens, m.start()) != bisect.bisect_left(closes, m.start()):
            continue  # nested in (or after an unclosed) another table
        tag_at = m.start() + len(m.group(1))
        op = _TAG.match(text, tag_at)
        if not op or op.group(1) or op.group(2).lower() != "table" or op.group(4):
            continue
        end = _TBL_CLOSE.search(text, op.end())
        if not end or re.search(r"(?i)<table\b", text[op.end() : end.start()]):
            continue
        after = end.end()
        if after < len(text) and text[after] != "\n":
            continue
        if _in_raw_zone(low, m.start()):
            continue
        rows = _parse_table_inner(text[op.end() : end.start()])
        shape = _shape(rows) if rows else None
        if shape is None:
            continue
        cells = [[html.unescape(c) for _, c in r] for r in rows]
        if any(re.search(r"[\t\n\r]", c) for r in cells for c in r):
            continue
        head, rowth = shape
        out.append(text[pos : m.start()])
        out.append(f"{m.group(1)}§table rows={len(rows)} cols={len(rows[0])} head={head} rowth={rowth}\n" + "\n".join("\t".join(r) for r in cells))
        pos = after
    if not out:
        return text
    out.append(text[pos:])
    return "".join(out)


# --- oracle: html.parser for HTML tables, a line reader for the TSV blocks; outside text must match exactly ----------

_BLOCK_HDR = re.compile(r"^[ \t]*§table rows=(\d+) cols=(\d+) head=(\d+) rowth=([01])$")


class _Cells(HTMLParser):
    """Canonical rows of one table span; `ok` turns False for anything that is not a plain th/td grid."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.row, self.kind, self.buf, self.ok = [], None, None, None, True

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        elif tag in ("th", "td"):
            if any(k in ("colspan", "rowspan", "headers") for k, _ in attrs):
                self.ok = False
            self.kind, self.buf = tag, []
        elif tag not in ("table", "thead", "tbody", "tfoot"):
            self.ok = False

    def handle_startendtag(self, tag, attrs):
        self.ok = False

    def handle_endtag(self, tag):
        if tag in ("th", "td") and self.row is not None and self.buf is not None:
            self.row.append((self.kind, "".join(self.buf)))
            self.buf = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None

    def handle_data(self, data):
        if self.buf is not None:
            self.buf.append(data)
        elif data.strip():
            self.ok = False

    def handle_comment(self, data):
        self.ok = False


class _Spans(HTMLParser):
    """Absolute (start, end) of every outermost <table>...</table> in the text."""

    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.text, self.depth, self.start, self.spans = text, 0, 0, []
        self.line_at = [0]
        for ln in text.split("\n")[:-1]:
            self.line_at.append(self.line_at[-1] + len(ln) + 1)

    def _here(self):
        ln, col = self.getpos()
        return self.line_at[ln - 1] + col

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            if self.depth == 0:
                self.start = self._here()
            self.depth += 1

    def handle_endtag(self, tag):
        if tag == "table" and self.depth:
            self.depth -= 1
            if self.depth == 0:
                at = self._here()
                self.spans.append((self.start, self.text.index(">", at) + 1))


def _tsv_blocks(text):
    lines = text.split("\n")
    starts, o = [], 0
    for ln in lines:
        starts.append(o)
        o += len(ln) + 1
    res, i = [], 0
    while i < len(lines):
        m = _BLOCK_HDR.match(lines[i])
        if m:
            r, c, h, t = (int(g) for g in m.groups())
            body = lines[i + 1 : i + 1 + r]
            if r >= 1 and len(body) == r and all(len(b.split("\t")) == c for b in body):
                rows = [[("th" if k < h or (j == 0 and t) else "td", cell) for j, cell in enumerate(b.split("\t"))] for k, b in enumerate(body)]
                res.append((starts[i] + lines[i].index("§"), starts[i + r] + len(lines[i + r]), rows))
                i += r + 1
                continue
        i += 1
    return res


def _table_facts(text):
    blocks = _tsv_blocks(text)
    masked = text
    for s, e, _ in blocks:
        masked = masked[:s] + "\x00" * (e - s) + masked[e:]
    sp = _Spans(masked)
    sp.feed(masked)
    sp.close()
    items = list(blocks)
    for s, e in sp.spans:
        c = _Cells()
        c.feed(text[s:e])
        c.close()
        items.append((s, e, c.rows if c.ok and c.rows else ("raw", text[s:e])))
    items.sort(key=lambda t: t[0])
    res, pos = [], 0
    for s, e, rows in items:
        res += [text[pos:s], rows]
        pos = e
    res.append(text[pos:])
    return res


def tables_equivalent(a: str, b: str) -> bool:
    return a == b or _table_facts(a) == _table_facts(b)


def _tables_fixture(*dfs: str) -> str:
    return "\n".join(dfs)


HTML_WIDE = (
    "Result of orders.head():\n"
    + "<table>\n<thead>\n<tr><th>id</th><th>customer</th><th>total</th><th>status</th></tr>\n</thead>\n<tbody>\n"
    + "".join(
        f"<tr><td>{1000 + i}</td><td>{n}</td><td>{t}</td><td>{s}</td></tr>\n"
        for i, (n, t, s) in enumerate([("Ana &amp; Co", "12.50", "paid"), ("Li Wei", "99.00", "open"), ("Sam &lt;sales&gt;", "7.25", "paid"), ("Mia", "", "void")])
    )
    + "</tbody>\n</table>\nthen a second query:\n"
    + HTML_DF
)
HTML_COLSPAN = '<table border="1">\n  <tr><th colspan="2">Totals</th></tr>\n  <tr><td>a</td><td>b</td></tr>\n</table>\n'
HTML_NESTED = "<table>\n  <tr><th>name</th><th>link</th></tr>\n  <tr><td>docs</td><td><a href=\"/d\">open</a></td></tr>\n</table>\n"
HTML_COMMENTED = "<!--\n<table>\n<tr><th>a</th></tr>\n<tr><td>1</td></tr>\n</table>\n-->\n"
HTML_TAB_CELL = "<table>\n<tr><th>k</th><th>v</th></tr>\n<tr><td>x&#9;y</td><td>1</td></tr>\n</table>\n"

case(
    1436,
    claim="Plain HTML tables (th/td grid, no spans, no tags in cells, own lines) become a '§table rows= cols= head= rowth=' line plus TSV rows with entities unescaped; every cell's text and th/td role, and all text outside tables, is unchanged.",
    loses="table, row and cell attributes (border, class, style), thead/tbody/tfoot wrapper tags, the spelling of entities and the layout whitespace between tags",
    encode=html_tables_to_tsv,
    equivalent=tables_equivalent,
    fixtures=[
        Fixture("real pandas to_html with a named index (two header rows)", HTML_DF, True),
        Fixture("real pandas _repr_html_ (style block stays)", HTML_REPR, True),
        Fixture("hand-written table with entities, empty cell, then a second table", HTML_WIDE, True),
        Fixture("colspan header is not a plain grid", HTML_COLSPAN, False),
        Fixture("link inside a cell", HTML_NESTED, False),
        Fixture("table inside an HTML comment", HTML_COMMENTED, False),
        Fixture("cell text with a tab", HTML_TAB_CELL, False),
        Fixture("prose only", "No tables in this answer, only a sentence about <b>bold</b> text.\n", False),
        Fixture("text that already looks like a block, plus a table", "§table rows=2 cols=2 head=1 rowth=0\na\tb\n1\t2\n" + HTML_DF, None),
        Fixture("fake block header with too few rows", "§table rows=5 cols=2 head=1 rowth=0\na\tb\n" + HTML_DF, None),
        Fixture("indented table followed by text on the same line", "  <table><tr><td>1</td></tr></table> tail\n  <table>\n<tr><td>2</td></tr>\n</table>\n", None),
    ],
    min_gain=0.3,
)

# ===============================================================================================================
# #1437 SQL DDL: unquote plain lowercase identifiers. Django's sqlite schema editor, sqlmigrate and pg_dump quote every
# name with "..."; MySQL SHOW CREATE TABLE quotes with `...`. Quotes come off a name only inside a CREATE/ALTER ... TABLE
# INDEX VIEW ... statement, when it matches [a-z_][a-z0-9_]*, is not a reserved word in PostgreSQL, MySQL, SQLite or the
# standard, and does not touch a word character after unquoting. Double quotes are treated as identifier quotes only
# when the text has no backtick (in MySQL "x" is a string). Anything with a backslash is left alone.

_SQL_RESERVED = frozenset(
    """
    abort action add after all alter analyse analyze and any array as asc asymmetric attach authorization autoincrement
    before begin between binary both by cascade case cast check collate column commit conflict constraint create cross
    current current_catalog current_date current_role current_schema current_time current_timestamp current_user database
    default deferrable deferred delete desc detach distinct do drop each else end escape except exclusive exists explain
    fail false fetch for foreign freeze from full generated glob grant group groups having if ignore ilike immediate in
    index indexed initially inner insert instead intersect into is isnull join key keys lateral leading left like limit
    localtime localtimestamp match materialized natural no not notnull null nulls of offset on only or order others outer
    over partition placing plan pragma precision primary query raise range read recursive references regexp reindex
    release rename replace restrict returning right rollback row rows savepoint select session_user set similar some
    symmetric system table temp temporary then to trailing transaction trigger true union unique update user using
    vacuum values variadic verbose view virtual when where window with without
    rank dense_rank row_number lag lead first_value last_value ntile cume_dist percent_rank over
    int integer bigint smallint tinyint mediumint decimal numeric real float double char varchar text blob date time
    datetime timestamp boolean bool bit serial interval json jsonb uuid enum
    accessible analyze asensitive before bigint blob call change character condition continue convert cursor databases
    day_hour day_microsecond day_minute day_second declare delayed describe deterministic distinctrow div dual elseif
    enclosed exit float4 float8 force fulltext get high_priority hour_microsecond hour_minute hour_second infile inout
    insensitive int1 int2 int3 int4 int8 iterate kill leave linear lines load lock long longblob longtext loop
    low_priority master_ssl_verify_server_cert maxvalue mediumblob mediumtext middleint minute_microsecond minute_second
    mod modifies no_write_to_binlog optimize optimizer_costs option optionally out outfile purge range reads
    require resignal return revoke rlike schema schemas second_microsecond sensitive separator show signal spatial
    specific sql sqlexception sqlstate sqlwarning sql_big_result sql_calc_found_rows sql_small_result ssl starting
    stored straight_join terminated tinyblob tinytext undo unlock unsigned usage utc_date utc_time utc_timestamp
    varbinary varcharacter varying virtual while write xor year_month zerofill
    """.split()
)
_PLAIN_ID = re.compile(r"[a-z_][a-z0-9_]*\Z")
_STMT_START = re.compile(
    r"[ \t]*(?:Create Table:[ \t]+)?(?:CREATE|ALTER)\s+(?:(?:OR\s+REPLACE|TEMP|TEMPORARY|UNIQUE|VIRTUAL|MATERIALIZED|CLUSTERED)\s+)*"
    r"(?:TABLE|INDEX|VIEW|SEQUENCE|TRIGGER|TYPE|SCHEMA|DATABASE)\b",
    re.I,
)
_BLANK_AHEAD = re.compile(r"[ \t]*(?:\n|\Z)")
_DOLLAR = re.compile(r"\$([A-Za-z_]\w*)?\$")


def _plain_ident(name: str) -> bool:
    return bool(_PLAIN_ID.match(name)) and name not in _SQL_RESERVED


@_total
def unquote_sql(text: str) -> str:
    if "\\" in text:
        return text
    has_bt = "`" in text
    n, i = len(text), 0
    out: list[str] = []
    in_stmt, line_start, stripped = False, True, False
    while i < n:
        c = text[i]
        if line_start and not in_stmt and _STMT_START.match(text, i):
            in_stmt = True
        line_start = False
        if c == "\n":
            out.append(c)
            i += 1
            line_start = True
            if in_stmt and _BLANK_AHEAD.match(text, i):
                in_stmt = False
            continue
        if c == "'":
            j = i + 1
            while True:
                j = text.find("'", j)
                if j < 0:
                    j = n - 1
                    break
                if text[j + 1 : j + 2] == "'":
                    j += 2
                    continue
                break
            out.append(text[i : j + 1])
            i = j + 1
            continue
        if text.startswith("--", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(text[i:j])
            i = j
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(text[i:j])
            i = j
            continue
        if c == "$":
            m = _DOLLAR.match(text, i)
            if m:
                j = text.find(m.group(0), m.end())
                j = n if j < 0 else j + len(m.group(0))
                out.append(text[i:j])
                i = j
                continue
        if c in '"`':
            j = i + 1
            while True:
                j = text.find(c, j)
                if j < 0:
                    j = n - 1
                    break
                if text[j + 1 : j + 2] == c:
                    j += 2
                    continue
                break
            tok = text[i : j + 1]
            inner = tok[1:-1]
            left = text[i - 1] if i else ""
            right = text[j + 1] if j + 1 < n else ""
            ok = (
                in_stmt
                and len(tok) >= 3
                and tok[-1] == c
                and (c == "`" or not has_bt)
                and _plain_ident(inner)
                and not re.match(r"[\w$]", left or " ")
                and not re.match(r"[\w$]", right or " ")
            )
            out.append(inner if ok else tok)
            stripped = stripped or ok
            i = j + 1
            continue
        out.append(c)
        i += 1
        if c == ";" and in_stmt:
            in_stmt = False
    return "".join(out) if stripped else text


_SQL_TOK = re.compile(
    r"""(?P<cm>--[^\n]*|/\*.*?\*/)|(?P<st>'(?:[^']|'')*')|(?P<dl>\$\$.*?\$\$|\$(?P<tag>[A-Za-z_]\w*)\$.*?\$(?P=tag)\$)
    |(?P<qi>"(?:[^"]|"")*")|(?P<bi>`(?:[^`]|``)*`)|(?P<ws>\s+)|(?P<w>\w+)|(?P<o>.)""",
    re.S | re.X,
)


def _sql_tokens(text: str):
    return [(m.lastgroup if m.lastgroup != "tag" else "dl", m.group(0), m.start()) for m in _SQL_TOK.finditer(text)]


def _sql_norm(text: str, has_bt: bool):
    """Token list with plain quoted names inside DDL statements mapped to bare words (region logic is token based)."""
    res, in_stmt, line_start = [], False, True
    for kind, val, at in _sql_tokens(text):
        if kind == "ws":
            parts = val.split("\n")
            if len(parts) > 1:
                line_start = True
                if in_stmt and any(not p.strip(" \t") for p in parts[1:-1]):  # a blank line ends the statement
                    in_stmt = False
            res.append(("ws", val))
            continue
        if line_start and not in_stmt and kind == "w" and _STMT_START.match(text, text.rfind("\n", 0, at) + 1):
            in_stmt = True
        line_start = False
        if kind in ("qi", "bi") and in_stmt:
            inner = val[1:-1]
            if _plain_ident(inner) and (kind == "bi" or not has_bt):
                res.append(("w", inner))
                continue
        res.append((kind, val))
        if kind == "o" and val == ";" and in_stmt:
            in_stmt = False
    return res


def sql_equivalent(a: str, b: str) -> bool:
    if a == b:
        return True
    if "\\" in a:
        return False
    bt = "`" in a
    return _sql_norm(a, bt) == _sql_norm(b, bt)


def _sqlite_schema() -> str:
    """Django-style DDL (reconstructed from memory) stored by a real sqlite3 engine, printed like `.schema`."""
    ddl = [
        'CREATE TABLE "django_migrations" ("id" integer NOT NULL PRIMARY KEY AUTOINCREMENT, "app" varchar(255) NOT NULL, "name" varchar(255) NOT NULL, "applied" datetime NOT NULL)',
        'CREATE TABLE "auth_user" ("id" integer NOT NULL PRIMARY KEY AUTOINCREMENT, "password" varchar(128) NOT NULL, "last_login" datetime NULL, "is_superuser" bool NOT NULL, "username" varchar(150) NOT NULL UNIQUE, "email" varchar(254) NOT NULL, "is_staff" bool NOT NULL, "is_active" bool NOT NULL, "date_joined" datetime NOT NULL)',
        'CREATE TABLE "shop_order" ("id" integer NOT NULL PRIMARY KEY AUTOINCREMENT, "created_at" datetime NOT NULL, "total_cents" integer NOT NULL, "status" varchar(20) NOT NULL, "user_id" integer NOT NULL REFERENCES "auth_user" ("id") DEFERRABLE INITIALLY DEFERRED)',
        'CREATE TABLE "shop_orderline" ("id" integer NOT NULL PRIMARY KEY AUTOINCREMENT, "quantity" integer unsigned NOT NULL CHECK ("quantity" >= 0), "unit_price_cents" integer NOT NULL, "order_id" integer NOT NULL REFERENCES "shop_order" ("id") DEFERRABLE INITIALLY DEFERRED, "product_id" integer NOT NULL REFERENCES "shop_product" ("id") DEFERRABLE INITIALLY DEFERRED)',
        'CREATE TABLE "shop_product" ("id" integer NOT NULL PRIMARY KEY AUTOINCREMENT, "sku" varchar(32) NOT NULL UNIQUE, "name" varchar(200) NOT NULL, "description" text NOT NULL, "price_cents" integer NOT NULL)',
        'CREATE INDEX "shop_order_user_id_9f3a1c2e" ON "shop_order" ("user_id")',
        'CREATE INDEX "shop_orderline_order_id_2b7d4a10" ON "shop_orderline" ("order_id")',
        'CREATE INDEX "shop_orderline_product_id_c41e88aa" ON "shop_orderline" ("product_id")',
    ]
    con = sqlite3.connect(":memory:")
    for stmt in ddl:
        con.execute(stmt)
    rows = con.execute("select sql from sqlite_master where sql is not null order by rowid").fetchall()
    con.close()
    return "".join(r[0] + ";\n" for r in rows)


SQLITE_SCHEMA = _sqlite_schema()
MYSQL_SHOW = (
    "*************************** 1. row ***************************\n       Table: orders\nCreate Table: CREATE TABLE `orders` (\n"
    "  `id` bigint NOT NULL AUTO_INCREMENT,\n  `user_id` bigint NOT NULL,\n  `status` varchar(20) NOT NULL DEFAULT 'new',\n"
    "  `total_cents` int NOT NULL,\n  `order` int DEFAULT NULL,\n  `note` text,\n  `created_at` datetime NOT NULL,\n"
    "  PRIMARY KEY (`id`),\n  KEY `idx_orders_user` (`user_id`),\n  KEY `idx_orders_created` (`created_at`),\n"
    "  CONSTRAINT `fk_orders_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE\n"
    ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci\n"
)
SQL_KEEP = 'CREATE TABLE "Order" ("Id" integer NOT NULL, "group" text, "select" text, "user" integer);\n'
SQL_PROSE = 'He said "hello" and then "goodbye".\nCreate "something" with care: the "name" field is required.\n'
SQL_TRICKY = (
    'CREATE TABLE "a" ("id"integer, "x""y" text, "b" text DEFAULT \'say "hi"; "ok"\');\n'
    "-- a comment about \"quoted\" things\nCREATE FUNCTION f() RETURNS text AS $body$ SELECT \"col\" FROM \"t\"; $body$ LANGUAGE sql;\n"
    'CREATE INDEX "idx_a" ON "a" ("id", "b");\n/* block "c" */ CREATE VIEW "v" AS SELECT "id" FROM "a";\nCREATE TABLE "unterminated" ("x\n'
)
SQL_MIXED_QUOTES = 'CREATE TABLE `t` (`id` int, c text DEFAULT "x");\nCREATE TABLE "u" ("id" int);\n'
SQL_BACKSLASH = 'CREATE TABLE "t" ("id" int, "n" text DEFAULT \'a\\\'b\');\n'

case(
    1437,
    claim="Inside CREATE/ALTER ... TABLE/INDEX/VIEW statements, quoted identifiers that are lowercase [a-z_][a-z0-9_]*, not reserved in PostgreSQL/MySQL/SQLite/standard SQL and not glued to a neighbouring word lose their quotes; strings, comments, dollar-quoted bodies, reserved or mixed-case names and text with backslashes are unchanged, and the SQL token stream is otherwise identical.",
    loses="the quote characters around plain lowercase non-reserved identifiers (names, case and token order are unchanged); double quotes are read as identifier quotes only when the text has no backtick",
    encode=unquote_sql,
    equivalent=sql_equivalent,
    fixtures=[
        Fixture("Django sqlite .schema of five tables and three indexes (DDL reconstructed, stored by real sqlite3)", SQLITE_SCHEMA, True),
        Fixture("MySQL SHOW CREATE TABLE \\G (reconstructed from memory)", MYSQL_SHOW, True),
        Fixture("only reserved and mixed-case names", SQL_KEEP, False),
        Fixture("prose with quotes, no DDL statement", SQL_PROSE, False),
        Fixture("backslash in a string: left alone", SQL_BACKSLASH, False),
        Fixture("strings, comments, dollar quotes, doubled quotes, glued names, unterminated quote", SQL_TRICKY, None),
        Fixture("backtick and double-quote dialects in one text", SQL_MIXED_QUOTES, None),
    ],
    min_gain=0.05,
)


# ===============================================================================================================
# #1438 Java .properties: decode \uXXXX escape runs to characters. ResourceBundle files (LibreOffice, Spring, Android-style
# i18n) store every non-ASCII character as \u30C7. Equivalent, not roundtrip: the hex-digit case of decoded escapes is gone.
# An escape is active only after an even number of backslashes; surrogate pairs are combined; only non-ASCII printable
# characters are decoded, so \u0020, \u003d, \u000a, \u00A0 (no-break space) and \u200D (joiner) stay escaped and the
# line structure of the file cannot change.

_UNI = re.compile(r"(\\*)\\u(?:([dD][89abAB][0-9a-fA-F]{2})\\u([dD][c-fC-F][0-9a-fA-F]{2})|([0-9a-fA-F]{4}))")


def _uni_sub(m: re.Match) -> str:
    if len(m.group(1)) % 2:  # the backslash before the u is itself escaped
        return m.group(0)
    if m.group(2):
        ch = chr(0x10000 + ((int(m.group(2), 16) - 0xD800) << 10) + (int(m.group(3), 16) - 0xDC00))
    else:
        ch = chr(int(m.group(4), 16))
    return m.group(1) + ch if ch >= "\x80" and ch.isprintable() else m.group(0)


@_total
def decode_unicode_escapes(text: str) -> str:
    return _UNI.sub(_uni_sub, text)


def _props_scan(s: str):
    """(fully unescaped text, list of active escapes below U+0080) by a character loop, not a regex."""
    out, ascii_esc, i, n = [], [], 0, len(s)
    hexd = "0123456789abcdefABCDEF"
    while i < n:
        c = s[i]
        if c == "\\" and i + 1 < n:
            d = s[i + 1]
            if d == "u" and len(s[i + 2 : i + 6]) == 4 and all(h in hexd for h in s[i + 2 : i + 6]):
                code = int(s[i + 2 : i + 6], 16)
                lo = s[i + 8 : i + 12]
                if 0xD800 <= code <= 0xDBFF and s[i + 6 : i + 8] == "\\u" and len(lo) == 4 and all(h in hexd for h in lo) and 0xDC00 <= int(lo, 16) <= 0xDFFF:
                    out.append(chr(0x10000 + ((code - 0xD800) << 10) + (int(lo, 16) - 0xDC00)))
                    i += 12
                    continue
                if code < 0x80:
                    ascii_esc.append(s[i : i + 6].lower())
                out.append(chr(code))
                i += 6
                continue
            out.append(c + d)  # any other escape, including an escaped backslash, is two characters
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out), ascii_esc


def props_equivalent(a: str, b: str) -> bool:
    if a == b:
        return True
    ua, ea = _props_scan(a)
    ub, eb = _props_scan(b)
    new_chars = set(b) - set(a)
    return (
        ua == ub
        and ea == eb
        and a.count("\n") == b.count("\n")
        and a.count("\r") == b.count("\r")
        and all(ch >= "\x80" and ch.isprintable() for ch in new_chars)
    )


PROPS_ALL = PROPS_JA + PROPS_DE + PROPS_FR
PROPS_LOWER = re.sub(r"\\u([0-9A-F]{4})", lambda m: "\\u" + m.group(1).lower(), PROPS_DE)  # editors that write lowercase digits
PROPS_ASCII = "greeting=Hello\\u0020world\nsep\\u003dvalue=1\nwindows=C:\\\\users\\\\u00e9\nnbsp=a\\u00A0b\njoiner=\\u200D\n"
PROPS_TRICKY = (
    "a=\\\\u00e9 and \\\\\\u00e9\nb=\\uD83D\\uDE00 emoji \\uD83D alone \\uDE00 low first\nc=\\u000a not a newline \\u2028 \\u0085\nd=\\u00e\\u00E9\\uZZZZ\n"
    "e=\\u1F600 \\u0301 combining\n"
)

case(
    1438,
    claim="In .properties text, active \\uXXXX escapes (even number of backslashes before the u, surrogate pairs combined) that denote a non-ASCII printable character become that character; ASCII, control, space-like and invisible escapes, escaped backslashes and malformed escapes stay, and the file's lines are unchanged.",
    loses="the hex-digit case (\\u00E9 versus \\u00e9) of the decoded escapes",
    encode=decode_unicode_escapes,
    equivalent=props_equivalent,
    fixtures=[
        Fixture("real LibreOffice resources_ja.properties (CJK, uppercase digits)", PROPS_JA, True),
        Fixture("real resources_de.properties (umlauts and German quotes)", PROPS_DE, True),
        Fixture("real resources_fr.properties (accents and guillemets)", PROPS_FR, True),
        Fixture("ja, de and fr together", PROPS_ALL, True),
        Fixture("lowercase-digit escapes from another editor", PROPS_LOWER, True),
        Fixture("only ASCII, no-break space and joiner escapes", PROPS_ASCII, False),
        Fixture("plain properties without escapes", "server.port=8080\nspring.application.name=orders\n", False),
        Fixture("odd backslashes, lone and paired surrogates, newline escape, malformed escapes", PROPS_TRICKY, None),
    ],
    min_gain=0.05,
)

# ===============================================================================================================
# #1439 UUIDs without dashes. A canonical 8-4-4-4-12 UUID costs about five more tokens than its 32 hex digits. Every
# bounded UUID loses its dashes behind a one-line legend; decode re-dashes every bounded 32-hex token. Text that already
# holds a bare 32-hex token (an MD5, say) is left alone because decode could not tell it from a stripped UUID.

UUID_LEGEND = "§ids: bare 32-hex tokens are UUIDs with the dashes removed (8-4-4-4-12)\n"
UUID_RAW = "§raw\n"
_UUID_DASHED = re.compile(r"(?<!\w)([0-9a-fA-F]{8})-([0-9a-fA-F]{4})-([0-9a-fA-F]{4})-([0-9a-fA-F]{4})-([0-9a-fA-F]{12})(?!\w)")
_HEX32 = re.compile(r"(?<!\w)[0-9a-fA-F]{32}(?!\w)")


@_failopen
def strip_uuid_dashes(text: str) -> str:
    if text.startswith(SIGIL):
        return UUID_RAW + text
    if _HEX32.search(text):
        return text
    new = _UUID_DASHED.sub(lambda m: "".join(m.groups()), text)
    out = UUID_LEGEND + new
    return out if new != text and len(out) < len(text) else text


def restore_uuid_dashes(text: str) -> str:
    if text.startswith(UUID_RAW):
        return text[len(UUID_RAW):]
    if not text.startswith(UUID_LEGEND):
        return text
    return _HEX32.sub(lambda m: "-".join((m.group(0)[:8], m.group(0)[8:12], m.group(0)[12:16], m.group(0)[16:20], m.group(0)[20:])), text[len(UUID_LEGEND):])


def _uuids(n: int, seed: int) -> list[str]:
    rng = random.Random(seed)
    return [str(uuid.UUID(int=rng.getrandbits(128), version=4)) for _ in range(n)]


def _psql_uuid_table(n: int, seed: int) -> str:
    """psql aligned output of three uuid columns (layout reconstructed from memory)."""
    u = _uuids(3 * n, seed)
    rows = [(u[3 * i], u[3 * i + 1], u[3 * i + 2]) for i in range(n)]
    head = f" {'id':<36} | {'account_id':<36} | {'owner_id':<36}\n{'-' * 38}+{'-' * 38}+{'-' * 37}\n"
    return head + "".join(f" {a} | {b} | {c}\n" for a, b, c in rows) + f"({n} rows)\n"


def _az_roles(n: int, seed: int) -> str:
    u = _uuids(3 * n, seed)
    items = [
        f'  {{\n    "id": "/subscriptions/{u[3 * i]}/providers/Microsoft.Authorization/roleAssignments/{u[3 * i + 1]}",\n'
        f'    "principalId": "{u[3 * i + 2]}",\n    "roleDefinitionName": "Reader"\n  }}'
        for i in range(n)
    ]
    return "[\n" + ",\n".join(items) + "\n]\n"  # shaped like `az role assignment list` (reconstructed from memory)


UUID_TABLE = _psql_uuid_table(24, 11)
UUID_AZ = _az_roles(10, 12)
UUID_FEW = _psql_uuid_table(3, 13)
UUID_WITH_MD5 = UUID_TABLE + "md5: 9e107d9d372bb6826bd81d3542a419d6\n"
UUID_CASE = "{" + "}\n{".join(u.upper() for u in _uuids(60, 14)) + "}\n"  # .NET GUIDs in braces, uppercase

case(
    1439,
    claim="Every word-bounded 8-4-4-4-12 UUID loses its dashes behind a one-line legend and decode re-dashes every bounded 32-hex token, so the text comes back byte for byte; text that already contains a bare 32-hex token, or too few UUIDs to repay the legend, is left alone.",
    encode=strip_uuid_dashes,
    decode=restore_uuid_dashes,
    fixtures=[
        Fixture("psql table of 24 rows by 3 uuid columns (layout reconstructed from memory)", UUID_TABLE, True),
        Fixture("sixty uppercase .NET GUIDs in braces, one per line", UUID_CASE, True),
        Fixture("az role assignment list shaped JSON: three UUIDs per 330 characters (reconstructed from memory)", UUID_AZ, None),
        Fixture("three rows: legend would cost more than it saves", UUID_FEW, False),
        Fixture("an MD5 digest next to the UUIDs", UUID_WITH_MD5, False),
        Fixture("dashed hex that is not a UUID", "build 1234abcd-12-34-56-789012345678 and 12345678-1234-1234-1234-12345678901\n" * 8, False),
        Fixture("input that starts like encoder output", UUID_LEGEND + "0123456789abcdef0123456789abcdef\n", None),
        Fixture("UUIDs glued to words and to each other", "pod-" + _uuids(1, 1)[0] + "-1234\nx" + _uuids(1, 2)[0] + "\n" + _uuids(1, 3)[0] + "_" + _uuids(1, 4)[0] + "\n" + UUID_TABLE, None),
    ],
    min_gain=0.05,
    idempotent=False,  # an encoded text starts with the sigil, so re-encoding escapes it and grows
)

# ===============================================================================================================
# #1440 CSV and TSV rows padded with trailing empty fields. Excel and LibreOffice exports write every row out to the
# widest used column: "1001,SKU-0037,2,3.85,Li Wei,,,,,,,,,". Trailing delimiters come off each record (the physical line
# that ends it), found with the csv module's own record boundaries and re-checked by re-parsing the candidate. Equivalent:
# every row is the same after dropping trailing empty fields, the number of trailing empty fields is what is lost.


def _rstrip_empty(row):
    r = list(row)
    while r and r[-1] == "":
        r.pop()
    return r


def _trim_pad(text: str, delim: str) -> str | None:
    if delim not in text or re.search(r"\r(?!\n)", text):
        return None
    lines = re.findall(r"[^\n]*\n|[^\n]+", text)
    rd = csv.reader(iter(lines), delimiter=delim)
    out: list[str] = []
    used, changed = 0, False
    for row in rd:
        n = rd.line_num - used
        chunk = lines[used : used + n]
        used += n
        last = chunk[-1]
        body = last.rstrip("\r\n")
        eol = last[len(body):]
        core = body.rstrip(delim)
        if core and len(core) < len(body):
            cand = chunk[:-1] + [core + eol]
            again = list(csv.reader(iter(cand), delimiter=delim))
            if len(again) == 1 and _rstrip_empty(again[0]) == _rstrip_empty(row):
                chunk, changed = cand, True
        out.extend(chunk)
    return "".join(out) if changed else None


@_total
def trim_padded_rows(text: str) -> str:
    for d in (",", "\t"):
        res = _trim_pad(text, d)
        if res is not None:
            return res
    return text


def pad_equivalent(a: str, b: str) -> bool:
    if a == b:
        return True
    if a.count("\n") != b.count("\n"):
        return False
    for d in (",", "\t"):
        try:
            ra = [_rstrip_empty(r) for r in csv.reader(io.StringIO(a, newline=""), delimiter=d)]
            rb = [_rstrip_empty(r) for r in csv.reader(io.StringIO(b, newline=""), delimiter=d)]
        except csv.Error:
            continue
        if ra == rb:
            return True
    return False


LO_PAD_CRLF = LO_PAD_CSV.replace("\n", "\r\n")
LO_PAD_TSV = "\n".join("\t".join(r) for r in csv.reader(io.StringIO(LO_PAD_CSV, newline=""))) + "\n"  # Excel 'Text (Tab delimited)' shape
CSV_UNPADDED = "id,name,qty\n1,,3\n2,gadget,5\n3,,7\n"
CSV_QUOTED_TRAILING = 'id,note,x\n1,"line one,,,\nline two,,,",\n2,"a,,",,,\n,,,\n3,"",,\n'

case(
    1440,
    claim="Trailing delimiters (empty trailing fields) are removed from each CSV or TSV record, using the csv module's record boundaries so quoted fields spanning lines are untouched; every row parses to the same fields once trailing empties are ignored, and all-empty rows, quoted content and the line endings are unchanged.",
    loses="how many trailing empty fields each record had (the padded column count)",
    encode=trim_padded_rows,
    equivalent=pad_equivalent,
    fixtures=[
        Fixture("real LibreOffice CSV export padded to column N", LO_PAD_CSV, True),
        Fixture("the same with CRLF line endings", LO_PAD_CRLF, True),
        Fixture("tab-delimited text export of the same sheet", LO_PAD_TSV, True),
        Fixture("CSV without padding: interior empties only", CSV_UNPADDED, False),
        Fixture("prose", "Totals are in the second tab, nothing to trim here.\n", False),
        Fixture("quoted field with trailing commas inside, all-empty row, quoted empty", CSV_QUOTED_TRAILING, None),
        Fixture("unterminated quote at the end", 'a,b,,\n1,"2,,,\n', None),
    ],
    min_gain=0.1,
)

# ===============================================================================================================
# #1441 CSV cells that hold JSON. Postgres COPY ... CSV, BigQuery and Stripe exports put JSON in a cell, so every quote is
# doubled: "{""plan"":""pro""}". When the file has at least six doubled quotes and no cell has a tab, newline or leading
# quote, it is re-emitted as TSV with the raw JSON. Equivalent: the rows parse to the same cells; the CSV quoting is gone.


@_total
def csv_json_to_tsv(text: str) -> str:
    if "\t" in text or re.search(r"\r(?!\n)", text) or text.count('""') < 6:
        return text
    crlf, lf = text.count("\r\n"), text.count("\n")
    if crlf and crlf != lf:
        return text
    nl = "\r\n" if crlf else "\n"
    rows = list(csv.reader(io.StringIO(text, newline="")))
    for r in rows:
        if not r or r == [""] or any(re.search(r"[\t\r\n]", c) or c.startswith('"') for c in r):
            return text
    res = nl.join("\t".join(r) for r in rows) + (nl if text.endswith("\n") else "")
    body = res[: -len(nl)] if res.endswith(nl) else res
    if [l.split("\t") for l in body.split(nl)] != rows:
        return text
    return res


def tsv_equivalent(a: str, b: str) -> bool:
    if a == b:
        return True
    if "\t" in a or a.count("\n") != b.count("\n") or a.endswith("\n") != b.endswith("\n"):
        return False
    try:
        ra = list(csv.reader(io.StringIO(a, newline="")))
    except csv.Error:
        return False
    nl = "\r\n" if "\r\n" in b else "\n"
    body = b[: -len(nl)] if b.endswith(nl) else b
    return ra == [l.split("\t") for l in body.split(nl)]


def _json_csv(n: int, crlf: bool = False) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n" if crlf else "\n")
    w.writerow(["id", "account", "metadata", "created_at"])
    for i in range(n):
        meta = {"plan": ("pro", "free", "team")[i % 3], "seats": 3 + i, "flags": ["a", "b"][: 1 + i % 2], "owner": {"id": i, "email": f"user{i}@example.io"}}
        w.writerow([i, f"acct-{i:03d}", json.dumps(meta, separators=(",", ":")), f"2026-10-{1 + i % 28:02d}T09:{i % 60:02d}:00Z"])
    return buf.getvalue()  # real csv.writer output, the quoting Postgres COPY CSV uses as well


JSON_CSV = _json_csv(12)
JSON_CSV_CRLF = _json_csv(12, crlf=True)
PLAIN_CSV = "id,name,city\n1,Ana,Oslo\n2,\"Li, Wei\",Lima\n3,Sam,Pune\n"
JSON_CSV_TAB = JSON_CSV.replace("acct-003", "acct\t003")
JSON_CSV_NEWLINE = JSON_CSV + '99,"multi\nline",{"x":1},t\n'

case(
    1441,
    claim="A CSV with at least six doubled quotes, whose cells contain no tab, newline or leading quote, is re-emitted as TSV with the raw cell text; the parsed rows are identical, line endings and the final newline are kept, and any file that cannot be TSV-safe comes back unchanged.",
    loses="the CSV quoting (quote characters and doubled quotes); the delimiter changes from comma to tab",
    encode=csv_json_to_tsv,
    equivalent=tsv_equivalent,
    fixtures=[
        Fixture("csv.writer output with a JSON metadata column", JSON_CSV, True),
        Fixture("the same with CRLF line endings", JSON_CSV_CRLF, True),
        Fixture("plain CSV: no doubled quotes", PLAIN_CSV, False),
        Fixture("a cell holds a tab", JSON_CSV_TAB, False),
        Fixture("a cell holds a newline", JSON_CSV_NEWLINE, False),
        Fixture("prose", "The metadata column holds JSON objects.\n", False),
        Fixture("cell starting with a quote, blank line", JSON_CSV + '\n10,"""quoted""",x,y\n', None),
    ],
    min_gain=0.1,
)

# ===============================================================================================================
# #1442 Cargo.lock. Every registry package repeats `source = "registry+https://github.com/rust-lang/crates.io-index"` and
# spells its dependency list one quoted name per line. The source line becomes `source = "crates.io"` (legend comment on
# the first line, valid TOML) and exactly-formatted multi-line dependency arrays become one-line arrays. Roundtrip; the
# extra oracle parses both texts with tomllib and requires the same packages once the alias is expanded.

CARGO_SRC = "registry+https://github.com/rust-lang/crates.io-index"
CARGO_LEGEND = f'# source "crates.io" = {CARGO_SRC}\n'
CARGO_RAW = "# ctxprune: raw\n"
_SRC_LINE = re.compile(r'(?m)^source = "' + re.escape(CARGO_SRC) + r'"$')
_DEPS_MULTI = re.compile(r'(?m)^dependencies = \[\n((?: "[^"\\\n]*",\n)+)\]$')
_DEPS_ONE = re.compile(r'(?m)^dependencies = \["([^"\\\n]*(?:", "[^"\\\n]*)*)"\]$')


@_failopen
def fold_cargo_lock(text: str) -> str:
    if text.startswith(('# source "crates.io"', "# ctxprune: raw")):
        return CARGO_RAW + text
    if 'source = "crates.io"' in text or re.search(r'(?m)^dependencies = \["', text) or "[[package]]" not in text:
        return text
    new = _SRC_LINE.sub('source = "crates.io"', text)
    new = _DEPS_MULTI.sub(lambda m: "dependencies = [" + ", ".join(x.strip().rstrip(",") for x in m.group(1).split("\n") if x) + "]", new)
    out = CARGO_LEGEND + new
    return out if new != text and len(out) < len(text) else text


def unfold_cargo_lock(text: str) -> str:
    if text.startswith(CARGO_RAW):
        return text[len(CARGO_RAW):]
    if not text.startswith(CARGO_LEGEND):
        return text
    body = text[len(CARGO_LEGEND):]
    body = _DEPS_ONE.sub(lambda m: "dependencies = [\n" + "".join(f' "{x}",\n' for x in m.group(1).split('", "')) + "]", body)
    return re.sub(r'(?m)^source = "crates\.io"$', f'source = "{CARGO_SRC}"', body)


def cargo_toml_same(a: str, e: str) -> bool:
    """Extra oracle: the encoded text is still valid TOML and holds the same packages as the original."""
    if e == a or not e.startswith(CARGO_LEGEND):
        return True
    try:
        ta = tomllib.loads(a)
    except tomllib.TOMLDecodeError:
        return True  # the input was not valid TOML to begin with
    try:
        te = tomllib.loads(e)
    except tomllib.TOMLDecodeError:
        return False
    for p in te.get("package", []):
        if p.get("source") == "crates.io":
            p["source"] = CARGO_SRC
    return ta == te


CARGO_PATH_ONLY = '# This file is automatically @generated by Cargo.\n# It is not intended for manual editing.\nversion = 4\n\n[[package]]\nname = "demo"\nversion = "0.1.0"\n'
CARGO_MANIFEST = '[package]\nname = "demo"\nversion = "0.1.0"\n\n[dependencies]\nserde = { version = "1", features = ["derive"] }\nanyhow = "1"\n'

case(
    1442,
    claim="In a Cargo.lock, the crates.io source line becomes `source = \"crates.io\"` behind a one-line TOML comment legend and cargo's exact multi-line `dependencies = [` arrays become one-line arrays; decode restores the original byte for byte, and tomllib parses the encoded text to the same packages.",
    encode=fold_cargo_lock,
    decode=unfold_cargo_lock,
    equivalent=cargo_toml_same,
    fixtures=[
        Fixture("real Cargo.lock of 30 packages from cargo generate-lockfile", CARGO_LOCK, True),
        Fixture("first ten packages of it (truncated file)", "\n[[package]]\n".join(CARGO_LOCK.split("\n[[package]]\n")[:10]) + "\n", True),
        Fixture("lock with only a path package", CARGO_PATH_ONLY, False),
        Fixture("Cargo.toml manifest", CARGO_MANIFEST, False),
        Fixture("prose", "cargo update refreshes the lock file.\n", False),
        Fixture("input that starts like encoder output", CARGO_LEGEND + CARGO_LOCK, None),
        Fixture("already holds the alias", CARGO_LOCK + '\n[[package]]\nname = "x"\nversion = "1"\nsource = "crates.io"\n', None),
        Fixture("already holds a one-line array", CARGO_LOCK + '\n[[package]]\nname = "x"\nversion = "1"\ndependencies = ["a", "b"]\n', None),
    ],
    min_gain=0.1,
    idempotent=False,  # an encoded text starts with the legend comment, so re-encoding escapes it and grows
)

# ===============================================================================================================
# #1443 Spreadsheet Markdown from MarkItDown (xlsx via pandas read_excel) and pandas to_markdown: empty cells print as NaN,
# blank header cells as "Unnamed: 5", and an integer column that has a NaN prints as 120.0. In a pipe table, NaN body cells
# become empty, "Unnamed: N" header cells become empty, and a column whose non-NaN cells all look like 123.0 loses the ".0".
# Equivalent: pandas itself merged blank cells and the strings NaN/N/A on import, so NaN means "no value".

_MD_SEP = re.compile(r"^\|(?:\s*:?-{3,}:?\s*\|)+$")
_MD_INT0 = re.compile(r"-?\d+\.0\Z")
_MD_UNNAMED = re.compile(r"Unnamed: \d+\Z")


def _swap_cell(cell: str, new: str) -> str:
    return cell[: len(cell) - len(cell.lstrip())] + new + cell[len(cell.rstrip()):]


@_total
def clean_sheet_markdown(text: str) -> str:
    lines = text.split("\n")
    out = list(lines)
    i = 0
    while i < len(lines) - 1:
        h, s = lines[i], lines[i + 1]
        if not (h.startswith("|") and h.endswith("|") and _MD_SEP.match(s) and h.count("|") == s.count("|")):
            i += 1
            continue
        j = i + 2
        while j < len(lines) and lines[j].startswith("|") and lines[j].endswith("|") and lines[j].count("|") == h.count("|"):
            j += 1
        if any("\\|" in l or "`" in l for l in lines[i:j]):
            i = j
            continue
        head = h[1:-1].split("|")
        body = [l[1:-1].split("|") for l in lines[i + 2 : j]]
        out[i] = "|" + "|".join(_swap_cell(c, "") if _MD_UNNAMED.match(c.strip()) else c for c in head) + "|"
        for k in range(len(head)):
            real = [r[k].strip() for r in body if r[k].strip() != "NaN"]
            zero = bool(real) and all(_MD_INT0.match(v) for v in real)
            for r in body:
                v = r[k].strip()
                if v == "NaN":
                    r[k] = _swap_cell(r[k], "")
                elif zero:
                    r[k] = _swap_cell(r[k], v[:-2])
        for off, r in enumerate(body):
            out[i + 2 + off] = "|" + "|".join(r) + "|"
        i = j
    return "\n".join(out)


def _md_blocks(lines: list[str]):
    """Greedy left-to-right pipe tables as (header index, separator index, end): header and separator have the same cell
    count, body rows follow while they are pipe lines of that count."""
    res, i = [], 0
    while i < len(lines) - 1:
        h, s = lines[i], lines[i + 1]
        if h.startswith("|") and h.endswith("|") and _MD_SEP.match(s) and h.count("|") == s.count("|"):
            end = i + 2
            while end < len(lines) and lines[end].startswith("|") and lines[end].endswith("|") and lines[end].count("|") == h.count("|"):
                end += 1
            res.append((i, i + 1, end))
            i = end
        else:
            i += 1
    return res


def sheet_md_equivalent(a: str, b: str) -> bool:
    if a == b:
        return True
    la, lb = a.split("\n"), b.split("\n")
    if len(la) != len(lb):
        return False
    blocks = _md_blocks(la)
    for i, (x, y) in enumerate(zip(la, lb)):
        if x == y:
            continue
        blk = next(((hd, sep, end) for hd, sep, end in blocks if hd <= i < end), None)
        if blk is None or not (y.startswith("|") and y.endswith("|")):
            return False
        hd, sep, end = blk
        cx, cy = x[1:-1].split("|"), y[1:-1].split("|")
        if len(cx) != len(cy) or i == sep:
            return False
        for k, (p, q) in enumerate(zip(cx, cy)):
            if p == q:
                continue
            sp, sq = p.strip(), q.strip()
            if _swap_cell(p, sq) != q:  # padding must be kept
                return False
            if i == hd:
                if not (_MD_UNNAMED.match(sp) and sq == ""):
                    return False
            elif sp == "NaN" and sq == "":
                continue
            elif _MD_INT0.match(sp) and sq == sp[:-2]:
                col = [la[r][1:-1].split("|")[k].strip() for r in range(sep + 1, end)]
                if not all(c == "NaN" or _MD_INT0.match(c) for c in col):
                    return False
            else:
                return False
    return True


MD_MIXED_COL = (
    "| name | score | rank |\n| --- | --- | --- |\n| ana | 3.0 | 1.0 |\n| li | 2.5 | NaN |\n| sam | NaN | 3.0 |\n\n"
    "| Unnamed: 0 | total |\n| --- | --- |\n| a | 10.0 |\n| b | 20.0 |\n"
)
MD_PLAIN = "| a | b |\n| --- | --- |\n| 1 | x |\n| 2 | y |\n\nDone.\n"
MD_BACKTICK = "| a | b |\n| --- | --- |\n| `x` | NaN |\n"
MD_NAN_PROSE = "| name | note |\n| --- | --- |\n| NaN | NaN |\n| bob | 2.0 |\n"

case(
    1443,
    claim="In pipe tables, body cells that are exactly NaN become empty, header cells 'Unnamed: N' become empty, and a column whose non-NaN cells all look like 123.0 loses the '.0'; cell padding, every other cell, separators and all non-table text are unchanged.",
    loses="the literal 'NaN' versus an empty cell, the synthetic 'Unnamed: N' header text, and the '.0' suffix on whole-number float columns",
    encode=clean_sheet_markdown,
    equivalent=sheet_md_equivalent,
    fixtures=[
        Fixture("real MarkItDown xlsx output: two sheets, NaN cells, Unnamed: 5, 120.0 floats", MARKITDOWN_MD, True),
        Fixture("float column with a real fraction keeps .0; header and NaN clean up", MD_MIXED_COL, True),
        Fixture("table without NaN, Unnamed or .0", MD_PLAIN, False),
        Fixture("table row with a backtick is skipped", MD_BACKTICK, False),
        Fixture("prose", "The spreadsheet had three sheets.\n", False),
        Fixture("NaN in a text column, all-NaN column", MD_NAN_PROSE, None),
        Fixture("header only table, ragged row", "| a | b |\n| --- | --- |\n| NaN |\n| 1.0 | NaN |\n", None),
    ],
    min_gain=0.05,
)
