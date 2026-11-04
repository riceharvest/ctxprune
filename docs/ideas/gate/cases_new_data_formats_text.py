"""Runnable fidelity cases for ten new lossless data-format ideas (#1434-#1443). See harness.py for what a pass means.

  #1434 byte-array source literals (0x0a, 0x1e, ...) to joined hex lines        roundtrip
  #1435 kubectl get secret -o yaml: printable base64 values to stringData       roundtrip
  #1436 HTML tables (pandas to_html) to TSV                                     equivalent
  #1437 SQL DDL: unquote plain lowercase identifiers                            equivalent
  #1438 Java .properties: decode \\uXXXX runs to characters                      equivalent
  #1439 UUIDs without dashes (one legend line)                                  roundtrip
  #1440 Maven pom.xml dependency blocks to G:A:V coordinate lines               roundtrip
  #1441 CSV cells holding JSON (doubled quotes) to TSV                          equivalent
  #1442 Android values resources: same-tag name/value lines to name=value rows  roundtrip
  #1443 MarkItDown / pandas spreadsheet Markdown: NaN cells, Unnamed: N, .0     equivalent

Conventions: every encoder is wrapped by `_total` (anything that raises, or any result that is not strictly shorter,
hands the input back untouched); in-band markers start with the sigil "§" (or a fixed TOML/YAML comment line) and text
that already starts like an encoding is escaped with a "raw" line, so those roundtrip cases set idempotent=False.
Fixtures marked "real" are captured from the tool in this container (protobuf-go v1.31.0 sources, pandas 3, MarkItDown,
LibreOffice, openssl, csv); the rest are reconstructed from memory and say so.
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
# #1440 Maven pom.xml dependency blocks to coordinate lines. Each <dependency> is five to eight lines; a run of two or more
# blocks in the canonical shape (groupId, artifactId, then optional version, type, classifier, scope, optional, in that
# order, one tag per line, one indent unit, no spaces inside values) becomes a '§deps' header plus one line per block:
#   org.projectlombok:lombok:1.18.30 scope=provided
# Roundtrip: the header carries the indent unit and the block count; decode rebuilds the tags in canonical order.

_DEP_TAGS = ("version", "type", "classifier", "scope", "optional")
_DEPS_HDR = re.compile(r"^([ \t]*)§deps u=(\d+|t) n=(\d+)$")
DEPS_LEGEND = "§deps: lines after a '§deps' header are Maven <dependency> blocks written G:A[:V] [type=] [classifier=] [scope=] [optional]\n"
DEPS_RAW = "§raw\n"
_VAL = r"([^\s<>]+)"


def _dep_block(lines: list[str], i: int, ind: str):
    """(coordinate line, unit, next index) for a canonical <dependency> block at lines[i], else None."""
    if lines[i] != ind + "<dependency>" or i + 3 >= len(lines):
        return None
    m = re.match(r"^(" + re.escape(ind) + r"(?: {1,8}|\t))<groupId>" + _VAL + "</groupId>$", lines[i + 1])
    if not m:
        return None
    inner = m.group(1)
    a = re.match("^" + re.escape(inner) + "<artifactId>" + _VAL + "</artifactId>$", lines[i + 2])
    if not a or ":" in m.group(2) or ":" in a.group(1):
        return None
    coord, extra, j, tagpos = m.group(2) + ":" + a.group(1), [], i + 3, 0
    ver = None
    while j < len(lines):
        t = re.match("^" + re.escape(inner) + r"<(\w+)>" + _VAL + r"</\1>$", lines[j])
        if not t or t.group(1) not in _DEP_TAGS or _DEP_TAGS.index(t.group(1)) < tagpos:
            break
        tagpos = _DEP_TAGS.index(t.group(1)) + 1
        if t.group(1) == "version":
            ver = t.group(2)
        elif t.group(1) == "optional":
            extra.append("optional" if t.group(2) == "true" else f"optional={t.group(2)}")
        else:
            extra.append(f"{t.group(1)}={t.group(2)}")
        j += 1
    if j >= len(lines) or lines[j] != ind + "</dependency>":
        return None
    unit = inner[len(ind):]
    return coord + (":" + ver if ver else "") + "".join(" " + e for e in extra), unit, j + 1


@_failopen
def fold_pom_dependencies(text: str) -> str:
    if text.startswith(SIGIL):
        return DEPS_RAW + text
    if "§deps " in text:
        return text
    lines = text.split("\n")
    out: list[str] = []
    i, changed = 0, False
    while i < len(lines):
        m = re.match(r"^([ \t]*)<dependency>$", lines[i])
        run = []
        if m:
            ind, j, unit = m.group(1), i, None
            while j < len(lines):
                b = _dep_block(lines, j, ind)
                if not b or (unit is not None and b[1] != unit):
                    break
                unit = b[1]
                run.append(b[0])
                j = b[2]
        if m and len(run) >= 2:
            out.append(f"{ind}§deps u={'t' if unit == chr(9) else len(unit)} n={len(run)}")
            out.extend(ind + c for c in run)
            i, changed = j, True
            continue
        out.append(lines[i])
        i += 1
    res = DEPS_LEGEND + "\n".join(out)
    return res if changed and len(res) < len(text) else text


def unfold_pom_dependencies(text: str) -> str:
    if text.startswith(DEPS_RAW):
        return text[len(DEPS_RAW):]
    if not text.startswith(DEPS_LEGEND):
        return text
    lines = text[len(DEPS_LEGEND):].split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        m = _DEPS_HDR.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue
        ind, unit, n = m.group(1), ("\t" if m.group(2) == "t" else " " * int(m.group(2))), int(m.group(3))
        for ln in lines[i + 1 : i + 1 + n]:
            parts = ln[len(ind):].split(" ")
            g, a, *rest = parts[0].split(":", 2)
            v = rest[0] if rest else None
            inner = ind + unit
            out += [ind + "<dependency>", f"{inner}<groupId>{g}</groupId>", f"{inner}<artifactId>{a}</artifactId>"]
            if v is not None:
                out.append(f"{inner}<version>{v}</version>")
            for e in parts[1:]:
                k, _, val = e.partition("=")
                out.append(f"{inner}<{k}>{val if _ else 'true'}</{k}>")
            out.append(ind + "</dependency>")
        i += 1 + n
    return "\n".join(out)


def _pom(indent: str, deps: list[tuple], tail: str = "") -> str:
    """pom.xml shaped like a Spring Boot service (reconstructed from memory)."""
    u = indent
    blocks = []
    for g, a, v, s in deps:
        b = f"{u * 2}<dependency>\n{u * 3}<groupId>{g}</groupId>\n{u * 3}<artifactId>{a}</artifactId>\n"
        if v:
            b += f"{u * 3}<version>{v}</version>\n"
        if s:
            b += f"{u * 3}<scope>{s}</scope>\n"
        blocks.append(b + f"{u * 2}</dependency>\n")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<project xmlns="http://maven.apache.org/POM/4.0.0">\n'
        f"{u}<modelVersion>4.0.0</modelVersion>\n{u}<parent>\n{u * 2}<groupId>org.springframework.boot</groupId>\n{u * 2}<artifactId>spring-boot-starter-parent</artifactId>\n"
        f"{u * 2}<version>3.2.1</version>\n{u}</parent>\n{u}<artifactId>orders</artifactId>\n{u}<properties>\n{u * 2}<java.version>21</java.version>\n{u}</properties>\n"
        f"{u}<dependencies>\n" + "".join(blocks) + f"{u}</dependencies>\n{tail}</project>\n"
    )


_POM_DEPS = [
    ("org.springframework.boot", "spring-boot-starter-web", None, None),
    ("org.springframework.boot", "spring-boot-starter-data-jpa", None, None),
    ("org.springframework.boot", "spring-boot-starter-validation", None, None),
    ("com.fasterxml.jackson.core", "jackson-databind", None, None),
    ("org.postgresql", "postgresql", None, "runtime"),
    ("org.projectlombok", "lombok", "1.18.30", "provided"),
    ("com.google.guava", "guava", "33.0.0-jre", None),
    ("org.springframework.boot", "spring-boot-starter-test", None, "test"),
    ("org.mockito", "mockito-core", "5.8.0", "test"),
]
POM_4 = _pom("    ", _POM_DEPS, "    <build>\n        <plugins>\n            <plugin>\n                <groupId>org.springframework.boot</groupId>\n                <artifactId>spring-boot-maven-plugin</artifactId>\n            </plugin>\n        </plugins>\n    </build>\n")
POM_2 = _pom("  ", _POM_DEPS)
POM_TAB = _pom("\t", _POM_DEPS[:4])
POM_EXCL = _pom("    ", _POM_DEPS[:2]).replace(
    "            <artifactId>spring-boot-starter-data-jpa</artifactId>\n",
    "            <artifactId>spring-boot-starter-data-jpa</artifactId>\n            <exclusions>\n                <exclusion>\n                    <groupId>x</groupId>\n                    <artifactId>y</artifactId>\n                </exclusion>\n            </exclusions>\n",
)
POM_ODD_ORDER = _pom("    ", _POM_DEPS[:3]).replace("<groupId>org.springframework.boot</groupId>\n            <artifactId>spring-boot-starter-data-jpa</artifactId>", "<artifactId>spring-boot-starter-data-jpa</artifactId>\n            <groupId>org.springframework.boot</groupId>")
POM_OPTIONAL = _pom("    ", _POM_DEPS[:2]).replace(
    "            <artifactId>spring-boot-starter-web</artifactId>\n",
    "            <artifactId>spring-boot-starter-web</artifactId>\n            <version>3.2.1</version>\n            <type>pom</type>\n            <classifier>sources</classifier>\n            <scope>import</scope>\n            <optional>true</optional>\n",
)

case(
    1440,
    claim="A run of two or more canonical Maven <dependency> blocks (groupId, artifactId, then version/type/classifier/scope/optional in that order, one tag per line, values without spaces) becomes a '§deps' header and one 'G:A[:V] [type=] [classifier=] [scope=] [optional]' line per block; decode rebuilds the exact XML, and blocks with exclusions or other tag orders stay verbatim.",
    encode=fold_pom_dependencies,
    decode=unfold_pom_dependencies,
    fixtures=[
        Fixture("pom.xml with nine dependencies, 4-space indent, plugin section (reconstructed from memory)", POM_4, True),
        Fixture("the same pom with 2-space indent", POM_2, True),
        Fixture("tab-indented pom, four dependencies", POM_TAB, True),
        Fixture("every optional tag: version, type, classifier, scope, optional", POM_OPTIONAL, None),
        Fixture("dependencies with an exclusions block are not canonical", POM_EXCL, False),
        Fixture("groupId after artifactId", POM_ODD_ORDER, False),
        Fixture("a single dependency", _pom("    ", _POM_DEPS[:1]), False),
        Fixture("prose", "Add the dependency to your pom and rebuild.\n", False),
        Fixture("input that starts like encoder output", DEPS_LEGEND + "§deps u=4 n=1\n    a:b\n", None),
        Fixture("sigil header inside a pom", POM_4.replace("<modelVersion>", "§deps u=4 n=1 <modelVersion>"), None),
    ],
    min_gain=0.15,
    idempotent=False,  # an encoded text starts with the sigil, so re-encoding escapes it and grows
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
# #1442 Android values resources (strings.xml, colors.xml, dimens.xml): a run of three or more one-line resources of the same
# tag, <string name="app_name">Orders</string>, becomes a '§res' header and one 'name=value' line each ('!name=value' for
# translatable="false"). Values stay byte for byte (entities and \' escapes untouched). Roundtrip.

_RES_LINE = re.compile(r'^([ \t]*)<([a-z][a-z-]*) name="([A-Za-z0-9_.]+)"( translatable="false")?>([^<>\n]*)</\2>$')
_RES_HDR = re.compile(r"^([ \t]*)§res tag=([a-z][a-z-]*) n=(\d+)$")
RES_LEGEND = "§res: lines after a '§res' header are <TAG name=\"NAME\">VALUE</TAG> resources written NAME=VALUE ('!NAME' = translatable=\"false\")\n"
RES_RAW = "§raw\n"


@_failopen
def fold_resources(text: str) -> str:
    if text.startswith(SIGIL):
        return RES_RAW + text
    if "§res " in text:
        return text
    lines = text.split("\n")
    out: list[str] = []
    i, changed = 0, False
    while i < len(lines):
        m = _RES_LINE.match(lines[i])
        j = i
        if m:
            ind, tag = m.group(1), m.group(2)
            while j < len(lines):
                mj = _RES_LINE.match(lines[j])
                if not mj or mj.group(1) != ind or mj.group(2) != tag:
                    break
                j += 1
        if m and j - i >= 3:
            out.append(f"{ind}§res tag={tag} n={j - i}")
            for ln in lines[i:j]:
                mj = _RES_LINE.match(ln)
                out.append(f"{ind}{'!' if mj.group(4) else ''}{mj.group(3)}={mj.group(5)}")
            i, changed = j, True
            continue
        out.append(lines[i])
        i += 1
    res = RES_LEGEND + "\n".join(out)
    return res if changed and len(res) < len(text) else text


def unfold_resources(text: str) -> str:
    if text.startswith(RES_RAW):
        return text[len(RES_RAW):]
    if not text.startswith(RES_LEGEND):
        return text
    lines = text[len(RES_LEGEND):].split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        m = _RES_HDR.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue
        ind, tag, n = m.group(1), m.group(2), int(m.group(3))
        for ln in lines[i + 1 : i + 1 + n]:
            body = ln[len(ind):]
            nf = body.startswith("!")
            name, _, val = body[1 if nf else 0 :].partition("=")
            out.append(f'{ind}<{tag} name="{name}"{" translatable=\"false\"" if nf else ""}>{val}</{tag}>')
        i += 1 + n
    return "\n".join(out)


_UI_STRINGS = [
    ("action_save", "Save"), ("action_cancel", "Cancel"), ("action_retry", "Try again"), ("action_delete", "Delete order"), ("action_share", "Share"),
    ("error_network", "Network error, please retry"), ("error_timeout", "The request timed out"), ("error_auth", "Your session has expired"),
    ("hint_search", "Search orders"), ("hint_email", "Email address"), ("hint_password", "Password"), ("title_settings", "Settings"),
    ("title_orders", "Orders"), ("title_details", "Order details"), ("msg_saved", "Saved &amp; synced"), ("msg_deleted", "Order deleted"),
    ("label_total", "Total: %1$s"), ("label_items", "%1$d items"), ("label_status", "Status"), ("label_created", "Created on %1$s"),
    ("quote_it", "It\\'s ready"), ("empty_value", ""), ("eq_sign", "a=b"), ("status_open", "Open"), ("status_paid", "Paid"), ("status_void", "Void"),
    ("nav_home", "Home"), ("nav_profile", "Profile"), ("nav_help", "Help &amp; feedback"), ("dialog_confirm_title", "Are you sure?"),
    ("dialog_confirm_body", "This cannot be undone."), ("permission_camera", "Camera access is needed to scan receipts"),
]
STRINGS_XML = (
    '<?xml version="1.0" encoding="utf-8"?>\n<resources>\n    <string name="app_name" translatable="false">Orders</string>\n'
    + "".join(f'    <string name="{k}">{v}</string>\n' for k, v in _UI_STRINGS)
    + '    <string-array name="sizes">\n        <item>S</item>\n        <item>M</item>\n        <item>L</item>\n    </string-array>\n'
    '    <color name="purple_200">#FFBB86FC</color>\n    <color name="purple_500">#FF6200EE</color>\n    <color name="teal_200">#FF03DAC5</color>\n'
    '    <dimen name="margin_small">8dp</dimen>\n    <dimen name="margin_large">24dp</dimen>\n'
    "</resources>\n"
)  # Android Studio template layout (reconstructed from memory)
STRINGS_TWO = '<resources>\n    <string name="a">A</string>\n    <string name="b">B</string>\n</resources>\n'
STRINGS_CDATA = '<resources>\n    <string name="a"><![CDATA[x < y]]></string>\n    <string name="b" formatted="false">%d</string>\n    <string name="c"><b>bold</b></string>\n    <item name="d" type="id"/>\n</resources>\n'

case(
    1442,
    claim="A run of three or more one-line Android resources of the same tag with only name (and optionally translatable=\"false\") becomes a '§res' header and 'name=value' lines; decode rebuilds the exact XML, and CDATA, nested tags, other attributes and short runs stay verbatim.",
    encode=fold_resources,
    decode=unfold_resources,
    fixtures=[
        Fixture("Android values/strings.xml: 33 strings, a string-array, colors and dimens (reconstructed from memory)", STRINGS_XML, True),
        Fixture("two resources only", STRINGS_TWO, False),
        Fixture("CDATA, formatted attribute, nested tag, self-closing item", STRINGS_CDATA, False),
        Fixture("prose", "Add the string to strings.xml and rebuild the app.\n", False),
        Fixture("input that starts like encoder output", RES_LEGEND + "§res tag=string n=1\na=b\n", None),
        Fixture("sigil header inside the file", STRINGS_XML.replace("<resources>", "<resources> §res tag=string n=1"), None),
        Fixture("tab-indented run with an empty value and an equals sign", '<resources>\n\t<string name="a"></string>\n\t<string name="b">x=y=z</string>\n\t<string name="c" translatable="false">  spaced  </string>\n</resources>\n', None),
    ],
    min_gain=0.1,
    idempotent=False,  # an encoded text starts with the sigil, so re-encoding escapes it and grows
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
