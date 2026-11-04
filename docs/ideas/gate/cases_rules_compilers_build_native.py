"""Rule cases for the compilers / build / native-tooling slice: #118 #121 #214 #216 #220 #221 #223 #224 #427 #498 #508
#604 #609 #701 #703 #1068 #1085 #1151 #1325.

Every case here is a RULE: the transform drops information on purpose and `probes` answers the questions an agent must
still be able to answer (which diagnostics, which file:line:col, which version moved, which module failed). The probes
are written as a different algorithm from the encoder (a parser or a state machine over the data, where the encoder is
a line scanner; `tomllib`/`json` for lockfiles, where the encoder reads lines) so one bug is not mirrored.

Fixtures are real captures wherever the tool exists in the container (ruff, tsc, gcc, clang, ASan, TSan, UBSan, valgrind,
ninja, cmake, make, strace, uv, poetry, pipenv, cargo, npm, yarn, pnpm, go, gradle, maven, java, ruby, pip, node-gyp,
nbconvert, pyspark), with absolute paths shortened to /work/app (and /home/dev/.venv); a few are cut to keep this file small,
and each cut says so next to the fixture. Reconstructed from memory (generated to the tool's documented output format,
marked in the fixture names): xcodebuild (#214), MSBuild/dotnet (#216), pdflatex (#1085), Bazel (#701, alongside a real
ninja capture), macOS/Linux/Android crash reports (#427, alongside a real JVM hs_err and faulthandler), an Android-style
gradle report (#223, alongside a real one) and a Gradle 7.x footer (#224, alongside real Maven and Gradle 8.14 captures).

Conventions: every encoder is total (a bad or foreign input comes back unchanged), self-gating (returns the input unless
strictly shorter) and, where a stub line is added, idempotent because the stub is recognised and never re-folded.
"""
from __future__ import annotations

import json
import re
import tomllib
from collections import Counter, OrderedDict

from harness import Fixture, case

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def plain(s: str) -> str:
    """Remove ANSI escapes (#118 uses it for classification only and emits kept lines verbatim; #1325 strips colour from its output)."""
    return _ANSI.sub("", s)


def lines_nl(text: str) -> tuple[list[str], bool]:
    """'a\\nb\\n' -> (['a','b'], True); keeps the trailing-newline fact so a rewrite can restore it."""
    if text.endswith("\n"):
        return text[:-1].split("\n"), True
    return text.split("\n"), False


def join_nl(lines: list[str], nl: bool) -> str:
    return "\n".join(lines) + ("\n" if nl else "")


def shrink_only(src: str, out: str) -> str:
    """Self-gate: a rule must never grow the text."""
    return out if len(out) < len(src) else src


def cat(*parts: str) -> str:
    return "".join(parts)



# ===============================================================================================================
# #1068 Native-build failure digest for node-gyp, setuptools/pip and Ruby extconf/mkmf. Collapse the runs that every
# failed native install repeats (gyp 'spawn args' blocks, make progress lines, creating/copying, 'checking ... yes',
# mkmf's option list), hoist the flags shared by several compiler lines, drop the node/node-gyp versions that
# 'gyp ERR!' repeats. RULE: every error line (compiler 'fatal error', 'checking ... no', gyp ERR!, pip/Ruby failure
# lines), the names of what is missing, tool versions, the last make progress line (the failing object) and the
# expanded compiler commands must survive. All five fixtures are real captures.

BUILD_FAIL_PSYCOPG2 = (
    'Collecting psycopg2==2.9.9\n'
    '  Downloading psycopg2-2.9.9.tar.gz (384 kB)\n'
    '     ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 384.9/384.9 kB 576.6 kB/s eta 0:00:00\n'
    '  Preparing metadata (setup.py): started\n'
    "  Preparing metadata (setup.py): finished with status 'done'\n"
    'Building wheels for collected packages: psycopg2\n'
    '  Building wheel for psycopg2 (setup.py): started\n'
    "  Building wheel for psycopg2 (setup.py): finished with status 'error'\n"
    '  error: subprocess-exited-with-error\n'
    '  \n'
    '  × python setup.py bdist_wheel did not run successfully.\n'
    '  │ exit code: 1\n'
    '  ╰─> [38 lines of output]\n'
    '      running bdist_wheel\n'
    '      running build\n'
    '      running build_py\n'
    '      creating build\n'
    '      creating build/lib.linux-x86_64-cpython-313\n'
    '      creating build/lib.linux-x86_64-cpython-313/psycopg2\n'
    '      copying lib/_range.py -> build/lib.linux-x86_64-cpython-313/psycopg2\n'
    '      copying lib/pool.py -> build/lib.linux-x86_64-cpython-313/psycopg2\n'
    '      copying lib/errors.py -> build/lib.linux-x86_64-cpython-313/psycopg2\n'
    '      copying lib/errorcodes.py -> build/lib.linux-x86_64-cpython-313/psycopg2\n'
    '      copying lib/extensions.py -> build/lib.linux-x86_64-cpython-313/psycopg2\n'
    '      copying lib/tz.py -> build/lib.linux-x86_64-cpython-313/psycopg2\n'
    '      copying lib/__init__.py -> build/lib.linux-x86_64-cpython-313/psycopg2\n'
    '      copying lib/_json.py -> build/lib.linux-x86_64-cpython-313/psycopg2\n'
    '      copying lib/sql.py -> build/lib.linux-x86_64-cpython-313/psycopg2\n'
    '      copying lib/_ipaddress.py -> build/lib.linux-x86_64-cpython-313/psycopg2\n'
    '      copying lib/extras.py -> build/lib.linux-x86_64-cpython-313/psycopg2\n'
    '      running build_ext\n'
    "      building 'psycopg2._psycopg' extension\n"
    '      creating build/temp.linux-x86_64-cpython-313\n'
    '      creating build/temp.linux-x86_64-cpython-313/psycopg\n'
    '      cc -pthread -fno-strict-overflow -Wsign-compare -Wunreachable-code -DNDEBUG -g -O3 -Wall -O3 -fstack-protector -fstack-clash-protection -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=2 -fno-omit-frame-pointer -mno-omit-leaf-frame-pointer -fPIC -fPIC "-DPSYCOPG_VERSION=2.9.9 (dt dec pq3 ext lo64)" -DPSYCOPG_DEBUG=1 -DPG_VERSION_NUM=160015 -DHAVE_LO64=1 -DPSYCOPG_DEBUG=1 -I/usr/local/include/python3.13 -I. -I/usr/include/postgresql -I/usr/include/postgresql/16/server -I/usr/include/libxml2 -c psycopg/adapter_asis.c -o build/temp.linux-x86_64-cpython-313/psycopg/adapter_asis.o -Wdeclaration-after-statement\n'
    '      In file included from psycopg/adapter_asis.c:28:\n'
    '      ./psycopg/psycopg.h:36:10: fatal error: libpq-fe.h: No such file or directory\n'
    '         36 | #include <libpq-fe.h>\n'
    '            |          ^~~~~~~~~~~~\n'
    '      compilation terminated.\n'
    '      \n'
    '      It appears you are missing some prerequisite to build the package from source.\n'
    '      \n'
    "      You may install a binary package by installing 'psycopg2-binary' from PyPI.\n"
    '      If you want to install psycopg2 from source, please install the packages\n'
    '      required for the build and try again.\n'
    '      \n'
    "      For further information please check the 'doc/src/install.rst' file (also at\n"
    '      <https://www.psycopg.org/docs/install.html>).\n'
    '      \n'
    "      error: command '/usr/bin/cc' failed with exit code 1\n"
    '      [end of output]\n'
    '  \n'
    '  note: This error originates from a subprocess, and is likely not a problem with pip.\n'
    '  ERROR: Failed building wheel for psycopg2\n'
    '  Running setup.py clean for psycopg2\n'
    'Failed to build psycopg2\n'
    'ERROR: Could not build wheels for psycopg2, which is required to install pyproject.toml-based projects\n'
    'exit 1\n'
)
BUILD_FAIL_PGDEMO = (
    'Processing /work/app/pg2\n'
    '  Preparing metadata (setup.py): started\n'
    "  Preparing metadata (setup.py): finished with status 'done'\n"
    'Building wheels for collected packages: pgdemo\n'
    '  Building wheel for pgdemo (setup.py): started\n'
    "  Building wheel for pgdemo (setup.py): finished with status 'error'\n"
    '  error: subprocess-exited-with-error\n'
    '  \n'
    '  × python setup.py bdist_wheel did not run successfully.\n'
    '  │ exit code: 1\n'
    '  ╰─> [14 lines of output]\n'
    '      running bdist_wheel\n'
    '      running build\n'
    '      running build_ext\n'
    "      building 'pgdemo._pgdemo' extension\n"
    '      creating build\n'
    '      creating build/temp.linux-x86_64-cpython-313\n'
    '      creating build/temp.linux-x86_64-cpython-313/src\n'
    '      cc -pthread -fno-strict-overflow -Wsign-compare -Wunreachable-code -DNDEBUG -g -O3 -Wall -O3 -fstack-protector -fstack-clash-protection -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=2 -fno-omit-frame-pointer -mno-omit-leaf-frame-pointer -fPIC -fPIC -DPG_VERSION_NUM=160002 -DPSYCOPG_VERSION=\\"2.9.9\\" -I/usr/include/postgresql -I/usr/local/include/python3.13 -c src/conn.c -o build/temp.linux-x86_64-cpython-313/src/conn.o\n'
    '      cc -pthread -fno-strict-overflow -Wsign-compare -Wunreachable-code -DNDEBUG -g -O3 -Wall -O3 -fstack-protector -fstack-clash-protection -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=2 -fno-omit-frame-pointer -mno-omit-leaf-frame-pointer -fPIC -fPIC -DPG_VERSION_NUM=160002 -DPSYCOPG_VERSION=\\"2.9.9\\" -I/usr/include/postgresql -I/usr/local/include/python3.13 -c src/pgdemo.c -o build/temp.linux-x86_64-cpython-313/src/pgdemo.o\n'
    '      src/pgdemo.c:2:10: fatal error: libpq-fe.h: No such file or directory\n'
    '          2 | #include <libpq-fe.h>\n'
    '            |          ^~~~~~~~~~~~\n'
    '      compilation terminated.\n'
    "      error: command '/usr/bin/cc' failed with exit code 1\n"
    '      [end of output]\n'
    '  \n'
    '  note: This error originates from a subprocess, and is likely not a problem with pip.\n'
    '  ERROR: Failed building wheel for pgdemo\n'
    '  Running setup.py clean for pgdemo\n'
    'Failed to build pgdemo\n'
    'ERROR: Could not build wheels for pgdemo, which is required to install pyproject.toml-based projects\n'
)
BUILD_FAIL_NOKOGIRI = (
    'Successfully installed mini_portile2-2.8.9\n'
    "Building native extensions with: '--use-system-libraries'\n"
    'This could take a while...\n'
    'ERROR:  Error installing nokogiri:\n'
    '\tERROR: Failed to build gem native extension.\n'
    '\n'
    '    current directory: /work/app/n2/gems/gems/nokogiri-1.16.7/ext/nokogiri\n'
    '/opt/rbenv/versions/3.3.6/bin/ruby extconf.rb --use-system-libraries\n'
    'checking for whether -std=c99 is accepted as CFLAGS... yes\n'
    'checking for whether -Wno-declaration-after-statement is accepted as CFLAGS... yes\n'
    'checking for whether -O2 is accepted as CFLAGS... yes\n'
    'checking for whether -g is accepted as CFLAGS... yes\n'
    'checking for whether -Winline is accepted as CFLAGS... yes\n'
    'checking for whether -Wmissing-noreturn is accepted as CFLAGS... yes\n'
    'checking for whether -Wconversion -Wno-sign-conversion is accepted as CFLAGS... yes\n'
    'Building nokogiri using system libraries.\n'
    'checking for gzdopen() in -lz... yes\n'
    'checking for xmlParseDoc() in -lxml2... no\n'
    'checking for xmlParseDoc() in -llibxml2... no\n'
    'checking for libxml-2.0 using `pkg_config`... no\n'
    'Please install either the `pkg-config` utility or the `pkg-config` rubygem.\n'
    'checking for xmlParseDoc() in -lxml2... no\n'
    'checking for xmlParseDoc() in -llibxml2... no\n'
    '-----\n'
    "extconf.rb:295:in `ensure_package_configuration'\n"
    "extconf.rb:704:in `<main>'\n"
    'xml2 is missing. Please locate mkmf.log to investigate how it is failing.\n'
    '-----\n'
    '*** extconf.rb failed ***\n'
    'Could not create Makefile due to some reason, probably lack of necessary\n'
    'libraries and/or headers.  Check the mkmf.log file for more details.  You may\n'
    'need configuration options.\n'
    '\n'
    'Provided configuration options:\n'
    '\t--with-opt-dir\n'
    '\t--without-opt-dir\n'
    '\t--with-opt-include=${opt-dir}/include\n'
    '\t--without-opt-include\n'
    '\t--with-opt-lib=${opt-dir}/lib\n'
    '\t--without-opt-lib\n'
    '\t--with-make-prog\n'
    '\t--without-make-prog\n'
    '\t--srcdir=.\n'
    '\t--curdir\n'
    '\t--ruby=/opt/rbenv/versions/3.3.6/bin/$(RUBY_BASE_NAME)\n'
    '\t--help\n'
    '\t--clean\n'
    '\t--prevent-strip\n'
    '\t--enable-system-libraries\n'
    '\t--disable-system-libraries\n'
    '\t--use-system-libraries\n'
    '\t--enable-system-libraries\n'
    '\t--disable-system-libraries\n'
    '\t--use-system-libraries\n'
    '\t--with-zlib-dir\n'
    '\t--without-zlib-dir\n'
    '\t--with-zlib-include=${zlib-dir}/include\n'
    '\t--without-zlib-include\n'
    '\t--with-zlib-lib=${zlib-dir}/lib\n'
    '\t--without-zlib-lib\n'
    '\t--with-z-dir\n'
    '\t--without-z-dir\n'
    '\t--with-z-include=${z-dir}/include\n'
    '\t--without-z-include\n'
    '\t--with-z-lib=${z-dir}/lib\n'
    '\t--without-z-lib\n'
    '\t--with-zlib\n'
    '\t--without-zlib\n'
    '\t--with-xml2-dir\n'
    '\t--without-xml2-dir\n'
    '\t--with-xml2-include=${xml2-dir}/include\n'
    '\t--without-xml2-include\n'
    '\t--with-xml2-lib=${xml2-dir}/lib\n'
    '\t--without-xml2-lib\n'
    '\t--with-xml2lib\n'
    '\t--without-xml2lib\n'
    '\t--with-libxml2-dir\n'
    '\t--without-libxml2-dir\n'
    '\t--with-libxml2-include=${libxml2-dir}/include\n'
    '\t--without-libxml2-include\n'
    '\t--with-libxml2-lib=${libxml2-dir}/lib\n'
    '\t--without-libxml2-lib\n'
    '\t--with-libxml2lib\n'
    '\t--without-libxml2lib\n'
    '\t--with-libxml-2.0-dir\n'
    '\t--without-libxml-2.0-dir\n'
    '\t--with-libxml-2.0-include=${libxml-2.0-dir}/include\n'
    '\t--without-libxml-2.0-include\n'
    '\t--with-libxml-2.0-lib=${libxml-2.0-dir}/lib\n'
    '\t--without-libxml-2.0-lib\n'
    '\t--with-libxml-2.0-config\n'
    '\t--without-libxml-2.0-config\n'
    '\t--with-pkg-config\n'
    '\t--without-pkg-config\n'
    '\t--with-xml2lib\n'
    '\t--without-xml2lib\n'
    '\t--with-libxml2lib\n'
    '\t--without-libxml2lib\n'
    '\n'
    'To see why this extension failed to compile, please check the mkmf.log which can be found here:\n'
    '\n'
    '  /work/app/n2/gems/extensions/x86_64-linux/3.3.0/nokogiri-1.16.7/mkmf.log\n'
    '\n'
    'extconf failed, exit code 1\n'
    '\n'
    'Gem files will remain installed in /work/app/n2/gems/gems/nokogiri-1.16.7 for inspection.\n'
    'Results logged to /work/app/n2/gems/extensions/x86_64-linux/3.3.0/nokogiri-1.16.7/gem_make.out\n'
    'exit 1\n'
)
BUILD_FAIL_NODE_GYP = (
    'gyp info it worked if it ends with ok\n'
    'gyp info using node-gyp@10.2.0\n'
    'gyp info using node@22.23.4 | linux | x64\n'
    'gyp info find Python using Python version 3.13.18 found at "/usr/local/bin/python3"\n'
    '\n'
    'gyp info spawn /usr/local/bin/python3\n'
    'gyp info spawn args [\n'
    "gyp info spawn args '/root/.npm/_npx/abc/node_modules/node-gyp/gyp/gyp_main.py',\n"
    "gyp info spawn args 'binding.gyp',\n"
    "gyp info spawn args '-f',\n"
    "gyp info spawn args 'make',\n"
    "gyp info spawn args '-I',\n"
    "gyp info spawn args '/work/app/n4/build/config.gypi',\n"
    "gyp info spawn args '-I',\n"
    "gyp info spawn args '/root/.npm/_npx/abc/node_modules/node-gyp/addon.gypi',\n"
    "gyp info spawn args '-I',\n"
    "gyp info spawn args '/root/.cache/node-gyp/22.23.4/include/node/common.gypi',\n"
    "gyp info spawn args '-Dlibrary=shared_library',\n"
    "gyp info spawn args '-Dvisibility=default',\n"
    "gyp info spawn args '-Dnode_root_dir=/root/.cache/node-gyp/22.23.4',\n"
    "gyp info spawn args '-Dnode_gyp_dir=/root/.npm/_npx/abc/node_modules/node-gyp',\n"
    "gyp info spawn args '-Dnode_lib_file=/root/.cache/node-gyp/22.23.4/<(target_arch)/node.lib',\n"
    "gyp info spawn args '-Dmodule_root_dir=/work/app/n4',\n"
    "gyp info spawn args '-Dnode_engine=v8',\n"
    "gyp info spawn args '--depth=.',\n"
    "gyp info spawn args '--no-parallel',\n"
    "gyp info spawn args '--generator-output',\n"
    "gyp info spawn args 'build',\n"
    "gyp info spawn args '-Goutput_dir=.'\n"
    'gyp info spawn args ]\n'
    'gyp info spawn make\n'
    "gyp info spawn args [ 'BUILDTYPE=Release', '-C', 'build' ]\n"
    "make: Entering directory '/work/app/n4/build'\n"
    '  CXX(target) Release/obj.target/imgproc/src/addon.o\n'
    '  CXX(target) Release/obj.target/imgproc/src/resize.o\n'
    '  CXX(target) Release/obj.target/imgproc/src/blur.o\n'
    '../src/blur.cc:1:10: fatal error: vips/vips8: No such file or directory\n'
    '    1 | #include <vips/vips8>\n'
    '      |          ^~~~~~~~~~~~\n'
    'compilation terminated.\n'
    'make: *** [imgproc.target.mk:116: Release/obj.target/imgproc/src/blur.o] Error 1\n'
    "make: Leaving directory '/work/app/n4/build'\n"
    'gyp ERR! build error \n'
    'gyp ERR! stack Error: `make` failed with exit code: 2\n'
    'gyp ERR! stack at ChildProcess.<anonymous> (/root/.npm/_npx/abc/node_modules/node-gyp/lib/build.js:216:23)\n'
    'gyp ERR! System Linux 6.18.44-fc-v233\n'
    'gyp ERR! command "/opt/node22/bin/node" "/root/.npm/_npx/abc/node_modules/.bin/node-gyp" "rebuild"\n'
    'gyp ERR! cwd /work/app/n4\n'
    'gyp ERR! node -v v22.23.4\n'
    'gyp ERR! node-gyp -v v10.2.0\n'
    'gyp ERR! not ok \n'
    'exit 1\n'
)
BUILD_FAIL_MKMF = (
    'checking for xmlParseDoc() in -lxml2... no\n'
    'libxml2 is missing.  please visit https://nokogiri.org/tutorials/installing_nokogiri.html for help with installing dependencies.\n'
    '*** extconf.rb failed ***\n'
    'Could not create Makefile due to some reason, probably lack of necessary\n'
    'libraries and/or headers.  Check the mkmf.log file for more details.  You may\n'
    'need configuration options.\n'
    '\n'
    'Provided configuration options:\n'
    '\t--with-opt-dir\n'
    '\t--without-opt-dir\n'
    '\t--with-opt-include=${opt-dir}/include\n'
    '\t--without-opt-include\n'
    '\t--with-opt-lib=${opt-dir}/lib\n'
    '\t--without-opt-lib\n'
    '\t--with-make-prog\n'
    '\t--without-make-prog\n'
    '\t--srcdir=.\n'
    '\t--curdir\n'
    '\t--ruby=/opt/rbenv/versions/3.3.6/bin/$(RUBY_BASE_NAME)\n'
    '\t--with-xml2-dir\n'
    '\t--without-xml2-dir\n'
    '\t--with-xml2-include=${xml2-dir}/include\n'
    '\t--without-xml2-include\n'
    '\t--with-xml2-lib=${xml2-dir}/lib\n'
    '\t--without-xml2-lib\n'
    '\t--with-xml2lib\n'
    '\t--without-xml2lib\n'
)

_B_SPAWN = re.compile(r"^gyp info spawn args(?: |$)")
_B_PROGRESS = re.compile(r"^\s*(?:CC|CXX|LINK|COPY|TOUCH|SOLINK_MODULE|AR)\((?:target|host)\) ")
_B_COPY = re.compile(r"^\s*(?:creating \S|copying \S.* -> \S)")
_B_YES = re.compile(r"^checking for .*\.\.\. yes$")
_B_OPT_HEAD = "Provided configuration options:"
_B_CC = re.compile(r"^(\s*)((?:/usr/bin/)?(?:cc|gcc|g\+\+|c\+\+|clang(?:\+\+)?))( .+)$")
_B_LEGEND = re.compile(r"^\s*\[ctxprune: @(\d+) = (.*)\]$")
_B_STUB = re.compile(r"^\s*\[ctxprune: \d+ (?:gyp spawn|object|copy|configure check|configuration option)")
_B_VER = [re.compile(r"^gyp ERR! node -v v(\S+)$"), re.compile(r"^gyp ERR! node-gyp -v v(\S+)$")]


def _b_run(lines, i, pred):
    j = i
    while j < len(lines) and pred(lines[j]):
        j += 1
    return j


def digest_native_build(text: str) -> str:
    lines = text.split("\n")
    n = len(lines)
    seen_ver = set()
    for l in lines:
        if m := re.match(r"^gyp info using node-gyp@(\S+)$", l):
            seen_ver.add(("node-gyp", m.group(1)))
        elif m := re.match(r"^gyp info using node@(\S+) \|", l):
            seen_ver.add(("node", m.group(1)))
    out, i = [], 0
    while i < n:
        l = lines[i]
        j = i
        if _B_SPAWN.match(l):
            j = _b_run(lines, i, _B_SPAWN.match)
            if j - i >= 3:
                out.append(f"[ctxprune: {j - i} gyp spawn argument lines elided]")
                i = j
                continue
        elif _B_PROGRESS.match(l):
            j = _b_run(lines, i, _B_PROGRESS.match)
            if j - i >= 3:
                out.append(f"[ctxprune: {j - i - 1} object steps ok elided, the last one is shown]")
                out.append(lines[j - 1])
                i = j
                continue
        elif _B_COPY.match(l):
            j = _b_run(lines, i, _B_COPY.match)
            if j - i >= 3:
                out.append(re.match(r"^\s*", l).group(0) + f"[ctxprune: {j - i} copy/create lines elided]")
                i = j
                continue
        elif _B_YES.match(l):
            j = _b_run(lines, i, _B_YES.match)
            if j - i >= 3:
                out.append(f"[ctxprune: {j - i} configure checks ok elided]")
                i = j
                continue
        elif l == _B_OPT_HEAD:
            j = _b_run(lines, i + 1, lambda x: x.startswith("\t--"))
            if j - i - 1 >= 3:
                out += [l, f"\t[ctxprune: {j - i - 1} configuration option lines elided]"]
                i = j
                continue
        elif (m := _B_VER[0].match(l)) and ("node", m.group(1)) in seen_ver or (m := _B_VER[1].match(l)) and ("node-gyp", m.group(1)) in seen_ver:
            i += 1
            continue
        out.append(l)
        i += 1
    # hoist flags shared by several compiler lines
    cmds = []
    for k, l in enumerate(out):
        if (m := _B_CC.match(l)) and (toks := _tokens(m.group(2) + m.group(3))) and _eligible(toks) and not toks[1].startswith("@"):
            cmds.append((k, m.group(1), toks))
    if len(cmds) >= 2 and len({c[2][0] for c in cmds}) == 1:
        ulists = [_units(c[2][1:]) for c in cmds]
        common = Counter(ulists[0])
        for u in ulists[1:]:
            common &= Counter(u)
        shared, left = [], Counter(common)
        for u in ulists[0]:
            if left[u] > 0:
                shared.append(u)
                left[u] -= 1
        if len(shared) >= 3:
            for (k, ind, toks), units in zip(cmds, ulists):
                rest, left = [], Counter(shared)
                for u in units:
                    if left[u] > 0:
                        left[u] -= 1
                    else:
                        rest.append(u)
                out[k] = ind + " ".join([toks[0], "@1"] + rest)
            out.insert(cmds[0][0], f"{cmds[0][1]}[ctxprune: @1 = {' '.join(shared)}]")
    return shrink_only(text, "\n".join(out))


_B_ERR = [re.compile(p) for p in (r"\b(?:fatal )?error\b", r"gyp ERR!", r"\.\.\. no$", r"\*\*\* extconf\.rb failed", r"is missing", r"not found", r"No such file", r"\bERROR\b", r"Failed to build", r"\bFAILED\b", r"Could not create Makefile")]


def probes_1068(text: str) -> dict:
    lines = text.split("\n")
    legend = {}
    for l in lines:
        if m := _B_LEGEND.match(l):
            legend[m.group(1)] = _units(_tokens(m.group(2)) or [])
    errs, cmds, versions, progress = [], [], set(), None
    for l in lines:
        if _B_STUB.match(l) or _B_LEGEND.match(l):
            continue
        if (m := _B_CC.match(l)) and (toks := _tokens(m.group(2) + m.group(3))) and (_eligible(toks) or (len(toks) > 1 and toks[1].startswith("@") and toks[1][1:] in legend)):
            units = []
            for u in _units(toks[1:]):
                units += legend[u[1:]] if u.startswith("@") and u[1:] in legend else [u]
            cmds.append((toks[0], tuple(sorted(units))))
            continue
        if m := re.match(r"^gyp (?:info using node-gyp@|ERR! node-gyp -v v)(\S+)$", l):
            versions.add(("node-gyp", m.group(1)))
            continue
        if m := re.match(r"^gyp (?:info using node@|ERR! node -v v)(\S+?)(?: \||$)", l):
            versions.add(("node", m.group(1)))
            continue
        if m := re.search(r"Python version (\S+) found", l):
            versions.add(("python", m.group(1)))
        if _B_PROGRESS.match(l):
            progress = l
        if any(rx.search(l) for rx in _B_ERR):
            errs.append(l)
    missing = sorted(set(re.findall(r"fatal error: (\S+): No such file|(\S+) executable not found|(\S+) is missing", "\n".join(errs))))
    return {"error lines": errs, "what is missing": missing, "tool versions": sorted(versions), "compiler commands (head, sorted flags)": cmds,
            "last make progress line (the failing object)": progress}


COMPOSITE_1068 = cat("$ pip install -r requirements.txt\n", BUILD_FAIL_PSYCOPG2.split("exit 1")[0], "$ npm rebuild\n", BUILD_FAIL_NODE_GYP, "$ gem install nokogiri\n", BUILD_FAIL_NOKOGIRI)

case(
    1068,
    claim="Collapses gyp 'spawn args' blocks, make progress runs (the last object stays), setuptools creating/copying runs, mkmf 'checking ... yes' runs and mkmf's option list into count lines, hoists the flags shared by several cc/gcc lines into one '[ctxprune: @1 = ...]', and drops the node and node-gyp versions that 'gyp ERR!' repeats; every error line, the missing header/library/tool, the tool versions, the failing object and each compiler command (expanded) are unchanged.",
    loses="the successful intermediate steps (which objects compiled, which files were copied, which configure probes passed), the order of hoisted compiler flags, and the argument list gyp passes to python and make",
    encode=digest_native_build,
    probes=probes_1068,
    fixtures=lambda: [
        Fixture("pip install psycopg2 from source without libpq-fe.h (real)", BUILD_FAIL_PSYCOPG2, True),
        Fixture("setuptools build_ext through pip: two cc lines sharing 20 flags (real)", BUILD_FAIL_PGDEMO, True),
        Fixture("gem install nokogiri --use-system-libraries without libxml2 (real)", BUILD_FAIL_NOKOGIRI, True),
        Fixture("node-gyp rebuild of an addon that includes vips/vips8 (real)", BUILD_FAIL_NODE_GYP, True),
        Fixture("ruby extconf.rb: have_library xml2 fails (real)", BUILD_FAIL_MKMF, True),
        Fixture("CI log: pip failure first, node-gyp in the middle, nokogiri last", COMPOSITE_1068, True),
        Fixture("a successful, short build log", "running build_ext\nbuilding 'x' extension\ncc -c x.c -o x.o\ncc -shared x.o -o x.so\nOK\n", False),
        Fixture("lines that merely start like the collapsed classes", "creating a plan\ngyp info spawn args\nchecking for tea... yes\n", False),
    ],
    min_gain=0.1,
)



# ===============================================================================================================
# #1085 LaTeX build log pack for pdflatex / latexmk output. Keep '!' errors with their 'l.NN' context, every
# LaTeX/package/class warning (undefined citations and references, rerun hints), an Overfull/Underfull count and the
# page count; drop the nested '(file' open/close lines, 'LaTeX Font Info', 'Package ... Info' and similar chatter.
# RULE: the probe unwraps the 79-column hard wrap of the log first (a warning split across two physical lines must
# still name its citation key), then lists errors with their l.NN line, undefined cites/refs, other warnings, the
# box counts and the output line. No TeX Live here: logs are generated to the pdfTeX log format (reconstructed from
# memory), including a citation key that wraps at column 79 and an error block with the standard help text.

_TX_PKGS = ["article.cls", "size10.clo", "amsmath.sty", "amstext.sty", "amsgen.sty", "amsbsy.sty", "amsopn.sty", "amssymb.sty", "amsfonts.sty", "graphicx.sty", "keyval.sty", "graphics.sty", "trig.sty", "hyperref.sty", "natbib.sty", "geometry.sty", "xcolor.sty", "booktabs.sty", "url.sty", "listings.sty"]


def _wrap79(s: str) -> list[str]:
    return [s[i:i + 79] for i in range(0, len(s), 79)] or [""]


def _latex_log(error: bool = False, long_key: bool = True) -> str:
    L = ["This is pdfTeX, Version 3.141592653-2.6-1.40.25 (TeX Live 2023/Debian) (preloaded format=pdflatex 2024.1.10)  10 JAN 2024 12:00", "entering extended mode", " restricted \\write18 enabled.",
         " %&-line parsing enabled.", "**main.tex", "(./main.tex", "LaTeX2e <2023-11-01> patch level 1", "L3 programming layer <2024-01-05>"]
    for k, p in enumerate(_TX_PKGS):
        L.append(f"(/usr/share/texlive/texmf-dist/tex/latex/{p.split('.')[0]}/{p}")
        if p == "article.cls":
            L.append("Document Class: article 2023/05/17 v1.4n Standard LaTeX document class")
        if p == "amsmath.sty":
            L.append("For additional information on amsmath, use the `?' option.")
        L.append(")")
    L += ["(./main.aux)", "*geometry* driver: auto-detecting", "*geometry* detected driver: pdftex"]
    for k in range(1, 9):
        L += [f"LaTeX Font Info:    Checking defaults for OML/cmm/m/it on input line {k * 3}.", f"LaTeX Font Info:    ... okay on input line {k * 3}.", f"Package hyperref Info: Link coloring OFF on input line {k * 3}."]
    L += ["", "Overfull \\hbox (12.34pt too wide) in paragraph at lines 20--21", "[]\\OT1/cmr/m/n/10 Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod", " []", "",
          "Underfull \\hbox (badness 10000) in paragraph at lines 30--31", "[]\\OT1/cmr/m/n/10 text", " []", "", "Overfull \\hbox (3.1pt too wide) in paragraph at lines 33--34", "[]\\OT1/cmr/m/n/10 Another long line that does not fit", " []", ""]
    L += ["[1{/var/lib/texmf/fonts/map/pdftex/updmap/pdftex.map}]"]
    key = "knuth1984-the-tex-book-computers-and-typesetting-volume-a" if long_key else "knuth84"
    L += _wrap79(f"LaTeX Warning: Citation `{key}' on page 2 undefined on input line 44.") + [""]
    L += ["LaTeX Warning: Reference `fig:plot' on page 2 undefined on input line 51.", "", "Package natbib Warning: Citation `lamport94' on page 2 undefined on input line 52.", ""]
    L += ["Overfull \\hbox (7.7pt too wide) in paragraph at lines 55--56", "[]\\OT1/cmr/m/n/10 yet another overfull box", " []", ""]
    if error:
        L += ["! Undefined control sequence.", "l.60 \\foo", "         bar", "The control sequence at the end of the top line", "of your error message was never \\def'ed.  If you proceed,", "it will be ignored.", "", "[2]"]
    else:
        L += ["[2] [3]"]
    L += ["(./main.bbl) (./main.aux)", "LaTeX Warning: There were undefined references.", "", "LaTeX Warning: Label(s) may have changed. Rerun to get cross-references right.", "", " )"]
    if error:
        L += ["! LaTeX Error: File `missing.sty' not found.", "", "Type X to quit or <RETURN> to proceed,", "or enter new name. (Default extension: sty)", "", "Enter file name: ", "! Emergency stop.", "<read *> ", "         ", "l.3 \\usepackage{missing}", "                        ", "*** (cannot \\read from terminal in nonstop modes)", ""]
        L += ["No pages of output.", "Transcript written on main.log."]
    else:
        L += ["Here is how much of TeX's memory you used:", " 5123 strings out of 476091", " 79890 string characters out of 5793800", "", "Output written on main.pdf (3 pages, 98765 bytes).", "Transcript written on main.log."]
    return "\n".join(L) + "\n"


LATEX_OK = _latex_log(error=False)
LATEX_BROKEN = _latex_log(error=True)

_TX_KEEP_START = re.compile(r"^(?:This is (?:pdfTeX|XeTeX|LuaTeX)|Output written on |No pages of output|Transcript written on |Runaway |Emergency stop)")
_TX_WARN = re.compile(r"^(?:LaTeX|Package \S+|Class \S+) (?:Warning|Error):")
_TX_BOX = re.compile(r"^(Overfull|Underfull) \\[hv]box")
_TX_STUB = re.compile(r"^\[ctxprune: (?:(\d+) Overfull and (\d+) Underfull box warnings elided|(\d+) log lines elided)\]$")


def _tx_start(l: str) -> bool:
    return bool(l.startswith("!") or _TX_WARN.match(l) or _TX_BOX.match(l) or _TX_KEEP_START.match(l) or _TX_STUB.match(l))


def pack_latex_log(text: str) -> str:
    if not re.search(r"(?m)^(?:This is (?:pdfTeX|XeTeX|LuaTeX)|LaTeX2e <|Output written on |No pages of output|Transcript written on )", text):
        return text
    lines = _unwrap(text.split("\n"))
    n = len(lines)
    out, dropped, over, under, i = [], 0, 0, 0, 0

    def flush():
        nonlocal dropped
        if dropped:
            out.append(f"[ctxprune: {dropped} log lines elided]")
            dropped = 0

    while i < n:
        l = lines[i]
        if l.startswith("!"):
            flush()
            k = next((j for j in range(i + 1, min(i + 14, n)) if re.match(r"^l\.\d+ ", lines[j])), None)
            end = min(k + 2, n) if k is not None else i + 1
            out += lines[i:end]
            i = end
        elif _TX_WARN.match(l):
            flush()
            j = i + 1
            while j < n and lines[j].strip() and not _tx_start(lines[j]):
                j += 1
            out += lines[i:j]
            i = j
        elif m := _TX_BOX.match(l):
            over += m.group(1) == "Overfull"
            under += m.group(1) == "Underfull"
            j = i + 1
            while j < n and j < i + 4 and lines[j].strip() and not _tx_start(lines[j]):
                j += 1
            i = j
        elif _TX_KEEP_START.match(l) or _TX_STUB.match(l):
            flush()
            out.append(l)
            i += 1
        else:
            if l.strip():
                dropped += 1
            i += 1
    flush()
    if over or under:
        at = next((k for k, x in enumerate(out) if _TX_WARN.match(x)), len(out))
        out.insert(at, f"[ctxprune: {over} Overfull and {under} Underfull box warnings elided]")
    return shrink_only(text, "\n".join(out) + ("\n" if text.endswith("\n") else ""))


def _unwrap(lines: list[str]) -> list[str]:
    out, i = [], 0
    while i < len(lines):
        cur = lines[i]
        while len(cur) == 79 and i + 1 < len(lines) and lines[i + 1].strip():
            i += 1
            cur += lines[i]
        out.append(cur)
        i += 1
    return out


def probes_1085(text: str) -> dict:
    lines = _unwrap(text.split("\n"))
    errors, cites, refs, warns, over, under, outp, version = [], [], [], [], 0, 0, [], []
    for k, l in enumerate(lines):
        if l.startswith("!"):
            ctx = next((lines[j] for j in range(k + 1, min(k + 14, len(lines))) if re.match(r"^l\.\d+ ", lines[j])), None)
            errors.append((l, ctx))
        if m := re.match(r"^(?:LaTeX|Package \S+) Warning: Citation `(.*?)' on page (\d+) undefined on input line (\d+)", l):
            cites.append((m.group(1), m.group(2), m.group(3)))
        elif m := re.match(r"^(?:LaTeX|Package \S+) Warning: Reference `(.*?)' on page (\d+) undefined on input line (\d+)", l):
            refs.append((m.group(1), m.group(2), m.group(3)))
        elif _TX_WARN.match(l):
            warns.append(l)
        if m := _TX_BOX.match(l):
            over += m.group(1) == "Overfull"
            under += m.group(1) == "Underfull"
        if m := _TX_STUB.match(l):
            over += int(m.group(1) or 0)
            under += int(m.group(2) or 0)
        if re.match(r"^(?:Output written on |No pages of output|Transcript written on )", l):
            outp.append(l)
        if l.startswith("This is "):
            version.append(l)
    return {"errors with their l.NN context": errors, "undefined citations (key, page, line)": cites, "undefined references": refs, "other warnings": warns,
            "Overfull and Underfull box counts": (over, under), "output / page count lines": outp, "TeX version line": version}


case(
    1085,
    claim="Keeps the TeX version line, every '!' error with its 'l.NN' line and continuation, every LaTeX/Package/Class warning block (undefined citations and references, rerun hints, including a key hard-wrapped at column 79) and the 'Output written' / 'No pages of output' lines; the nested '(file' lines, font and package Info lines, memory statistics and page markers become '[ctxprune: N log lines elided]', and Overfull/Underfull boxes become one count line.",
    loses="the 79-column hard wrap of the lines it keeps (they are joined), the names and order of loaded packages and files, the line numbers and text of each Overfull/Underfull box (only the counts survive), font and package Info lines, page markers, and TeX's memory statistics",
    encode=pack_latex_log,
    probes=probes_1085,
    fixtures=lambda: [
        Fixture("pdflatex log of a clean build: 20 packages, 4 boxes, 3 undefined, key wraps at column 79 (generated, reconstructed from memory)", LATEX_OK, True),
        Fixture("the same with an undefined control sequence mid-log and a missing .sty at the end (generated)", LATEX_BROKEN, True),
        Fixture("latexmk summary line only", "Latexmk: All targets (main.pdf) are up-to-date\n", False),
        Fixture("a '!' with no l.NN line (truncated log) and a stub-looking line", "! Undefined control sequence.\nsome text\n[ctxprune: 9 log lines elided]\n", None),
    ],
    min_gain=0.5,
)



# ===============================================================================================================
# #1151 Warnings inside a shared header print once per translation unit that includes it; only the 'In file included
# from' chain differs. Keep one copy per distinct (context line, warning, echoed code) and say how many TUs hit it.
# RULE: probe = every distinct header warning site with the number of translation units that reported it (a Counter
# over the header lines, adding the encoder's '[ctxprune: ... more translation units]' counts), plus every other
# diagnostic and make command, verbatim. Real capture: 30 TUs share noisy.h, 2 of them also have a warning of their own.

MAKE_30_TUS = (
    'g++ -Wall -Wextra -O2 -std=c++17 -c mod1.cpp -o mod1.o\n'
    'In file included from mod1.cpp:1:\n'
    "noisy.h: In function 'int scale(int)':\n"
    "noisy.h:5:9: warning: unused variable 'unused_factor' [-Wunused-variable]\n"
    '    5 |     int unused_factor = 3;\n'
    '      |         ^~~~~~~~~~~~~\n'
    "noisy.h:6:9: warning: unused variable 'shift' [-Wunused-variable]\n"
    '    6 |     int shift;\n'
    '      |         ^~~~~\n'
    'g++ -Wall -Wextra -O2 -std=c++17 -c mod10.cpp -o mod10.o\n'
    'In file included from mod10.cpp:1:\n'
    "noisy.h: In function 'int scale(int)':\n"
    "noisy.h:5:9: warning: unused variable 'unused_factor' [-Wunused-variable]\n"
    '    5 |     int unused_factor = 3;\n'
    '      |         ^~~~~~~~~~~~~\n'
    "noisy.h:6:9: warning: unused variable 'shift' [-Wunused-variable]\n"
    '    6 |     int shift;\n'
    '      |         ^~~~~\n'
    'g++ -Wall -Wextra -O2 -std=c++17 -c mod11.cpp -o mod11.o\n'
    'In file included from mod11.cpp:1:\n'
    "noisy.h: In function 'int scale(int)':\n"
    "noisy.h:5:9: warning: unused variable 'unused_factor' [-Wunused-variable]\n"
    '    5 |     int unused_factor = 3;\n'
    '      |         ^~~~~~~~~~~~~\n'
    "noisy.h:6:9: warning: unused variable 'shift' [-Wunused-variable]\n"
    '    6 |     int shift;\n'
    '      |         ^~~~~\n'
    'g++ -Wall -Wextra -O2 -std=c++17 -c mod12.cpp -o mod12.o\n'
    'In file included from mod12.cpp:1:\n'
    "noisy.h: In function 'int scale(int)':\n"
    "noisy.h:5:9: warning: unused variable 'unused_factor' [-Wunused-variable]\n"
    '    5 |     int unused_factor = 3;\n'
    '      |         ^~~~~~~~~~~~~\n'
    "noisy.h:6:9: warning: unused variable 'shift' [-Wunused-variable]\n"
    '    6 |     int shift;\n'
    '      |         ^~~~~\n'
    'g++ -Wall -Wextra -O2 -std=c++17 -c mod13.cpp -o mod13.o\n'
    'In file included from mod13.cpp:1:\n'
    "noisy.h: In function 'int scale(int)':\n"
    "noisy.h:5:9: warning: unused variable 'unused_factor' [-Wunused-variable]\n"
    '    5 |     int unused_factor = 3;\n'
    '      |         ^~~~~~~~~~~~~\n'
    "noisy.h:6:9: warning: unused variable 'shift' [-Wunused-variable]\n"
    '    6 |     int shift;\n'
    '      |         ^~~~~\n'
    'g++ -Wall -Wextra -O2 -std=c++17 -c mod14.cpp -o mod14.o\n'
    'In file included from mod14.cpp:1:\n'
    "noisy.h: In function 'int scale(int)':\n"
    "noisy.h:5:9: warning: unused variable 'unused_factor' [-Wunused-variable]\n"
    '    5 |     int unused_factor = 3;\n'
    '      |         ^~~~~~~~~~~~~\n'
    "noisy.h:6:9: warning: unused variable 'shift' [-Wunused-variable]\n"
    '    6 |     int shift;\n'
    '      |         ^~~~~\n'
    'g++ -Wall -Wextra -O2 -std=c++17 -c mod15.cpp -o mod15.o\n'
    'In file included from mod15.cpp:1:\n'
    "noisy.h: In function 'int scale(int)':\n"
    "noisy.h:5:9: warning: unused variable 'unused_factor' [-Wunused-variable]\n"
    '    5 |     int unused_factor = 3;\n'
    '      |         ^~~~~~~~~~~~~\n'
    "noisy.h:6:9: warning: unused variable 'shift' [-Wunused-variable]\n"
    '    6 |     int shift;\n'
    '      |         ^~~~~\n'
    'g++ -Wall -Wextra -O2 -std=c++17 -c mod16.cpp -o mod16.o\n'
    'In file included from mod16.cpp:1:\n'
    "noisy.h: In function 'int scale(int)':\n"
    "noisy.h:5:9: warning: unused variable 'unused_factor' [-Wunused-variable]\n"
    '    5 |     int unused_factor = 3;\n'
    '      |         ^~~~~~~~~~~~~\n'
    "noisy.h:6:9: warning: unused variable 'shift' [-Wunused-variable]\n"
    '    6 |     int shift;\n'
    '      |         ^~~~~\n'
    'g++ -Wall -Wextra -O2 -std=c++17 -c mod17.cpp -o mod17.o\n'
    'In file included from mod17.cpp:1:\n'
    "noisy.h: In function 'int scale(int)':\n"
    "noisy.h:5:9: warning: unused variable 'unused_factor' [-Wunused-variable]\n"
    '    5 |     int unused_factor = 3;\n'
    '      |         ^~~~~~~~~~~~~\n'
    "noisy.h:6:9: warning: unused variable 'shift' [-Wunused-variable]\n"
    '    6 |     int shift;\n'
    '      |         ^~~~~\n'
    'g++ -Wall -Wextra -O2 -std=c++17 -c mod18.cpp -o mod18.o\n'
    'In file included from mod18.cpp:1:\n'
    "noisy.h: In function 'int scale(int)':\n"
    "noisy.h:5:9: warning: unused variable 'unused_factor' [-Wunused-variable]\n"
    '    5 |     int unused_factor = 3;\n'
    '      |         ^~~~~~~~~~~~~\n'
    "noisy.h:6:9: warning: unused variable 'shift' [-Wunused-variable]\n"
    '    6 |     int shift;\n'
    '      |         ^~~~~\n'
    'g++ -Wall -Wextra -O2 -std=c++17 -c mod19.cpp -o mod19.o\n'
    'In file included from mod19.cpp:1:\n'
    "noisy.h: In function 'int scale(int)':\n"
    "noisy.h:5:9: warning: unused variable 'unused_factor' [-Wunused-variable]\n"
    '    5 |     int unused_factor = 3;\n'
    '      |         ^~~~~~~~~~~~~\n'
    "noisy.h:6:9: warning: unused variable 'shift' [-Wunused-variable]\n"
    '    6 |     int shift;\n'
    '      |         ^~~~~\n'
    "noisy.h: In function 'const char* name_of(int)':\n"
    'noisy.h:15:1: warning: control reaches end of non-void function [-Wreturn-type]\n'
    '   15 | }\n'
    '      | ^\n'
    'g++ -Wall -Wextra -O2 -std=c++17 -c mod2.cpp -o mod2.o\n'
    'In file included from mod2.cpp:1:\n'
    "noisy.h: In function 'int scale(int)':\n"
    "noisy.h:5:9: warning: unused variable 'unused_factor' [-Wunused-variable]\n"
    '    5 |     int unused_factor = 3;\n'
    '      |         ^~~~~~~~~~~~~\n'
    "noisy.h:6:9: warning: unused variable 'shift' [-Wunused-variable]\n"
    '    6 |     int shift;\n'
    '      |         ^~~~~\n'
    'g++ -Wall -Wextra -O2 -std=c++17 -c mod20.cpp -o mod20.o\n'
    'In file included from mod20.cpp:1:\n'
    "noisy.h: In function 'int scale(int)':\n"
    "noisy.h:5:9: warning: unused variable 'unused_factor' [-Wunused-variable]\n"
    '    5 |     int unused_factor = 3;\n'
    '      |         ^~~~~~~~~~~~~\n'
    "noisy.h:6:9: warning: unused variable 'shift' [-Wunused-variable]\n"
    '    6 |     int shift;\n'
    '      |         ^~~~~\n'
    'g++ -Wall -Wextra -O2 -std=c++17 -c mod21.cpp -o mod21.o\n'
    'In file included from mod21.cpp:1:\n'
    "noisy.h: In function 'int scale(int)':\n"
    "noisy.h:5:9: warning: unused variable 'unused_factor' [-Wunused-variable]\n"
    '    5 |     int unused_factor = 3;\n'
    '      |         ^~~~~~~~~~~~~\n'
    "noisy.h:6:9: warning: unused variable 'shift' [-Wunused-variable]\n"
    '    6 |     int shift;\n'
    '      |         ^~~~~\n'
    'g++ -Wall -Wextra -O2 -std=c++17 -c mod22.cpp -o mod22.o\n'
    'In file included from mod22.cpp:1:\n'
    "noisy.h: In function 'int scale(int)':\n"
    "noisy.h:5:9: warning: unused variable 'unused_factor' [-Wunused-variable]\n"
    '    5 |     int unused_factor = 3;\n'
    '      |         ^~~~~~~~~~~~~\n'
    "noisy.h:6:9: warning: unused variable 'shift' [-Wunused-variable]\n"
    '    6 |     int shift;\n'
    '      |         ^~~~~\n'
    'g++ -Wall -Wextra -O2 -std=c++17 -c mod23.cpp -o mod23.o\n'
    'In file included from mod23.cpp:1:\n'
    "noisy.h: In function 'int scale(int)':\n"
    "noisy.h:5:9: warning: unused variable 'unused_factor' [-Wunused-variable]\n"
    '    5 |     int unused_factor = 3;\n'
    '      |         ^~~~~~~~~~~~~\n'
    "noisy.h:6:9: warning: unused variable 'shift' [-Wunused-variable]\n"
    '    6 |     int shift;\n'
    '      |         ^~~~~\n'
    'g++ -Wall -Wextra -O2 -std=c++17 -c mod24.cpp -o mod24.o\n'
    'In file included from mod24.cpp:1:\n'
    "noisy.h: In function 'int scale(int)':\n"
    "noisy.h:5:9: warning: unused variable 'unused_factor' [-Wunused-variable]\n"
    '    5 |     int unused_factor = 3;\n'
    '      |         ^~~~~~~~~~~~~\n'
    "noisy.h:6:9: warning: unused variable 'shift' [-Wunused-variable]\n"
    '    6 |     int shift;\n'
    '      |         ^~~~~\n'
    'g++ -Wall -Wextra -O2 -std=c++17 -c mod25.cpp -o mod25.o\n'
    'In file included from mod25.cpp:1:\n'
    "noisy.h: In function 'int scale(int)':\n"
    "noisy.h:5:9: warning: unused variable 'unused_factor' [-Wunused-variable]\n"
    '    5 |     int unused_factor = 3;\n'
    '      |         ^~~~~~~~~~~~~\n'
    "noisy.h:6:9: warning: unused variable 'shift' [-Wunused-variable]\n"
    '    6 |     int shift;\n'
    '      |         ^~~~~\n'
    'g++ -Wall -Wextra -O2 -std=c++17 -c mod26.cpp -o mod26.o\n'
    'In file included from mod26.cpp:1:\n'
    "noisy.h: In function 'int scale(int)':\n"
    "noisy.h:5:9: warning: unused variable 'unused_factor' [-Wunused-variable]\n"
    '    5 |     int unused_factor = 3;\n'
    '      |         ^~~~~~~~~~~~~\n'
    "noisy.h:6:9: warning: unused variable 'shift' [-Wunused-variable]\n"
    '    6 |     int shift;\n'
    '      |         ^~~~~\n'
    'g++ -Wall -Wextra -O2 -std=c++17 -c mod27.cpp -o mod27.o\n'
    'In file included from mod27.cpp:1:\n'
    "noisy.h: In function 'int scale(int)':\n"
    "noisy.h:5:9: warning: unused variable 'unused_factor' [-Wunused-variable]\n"
    '    5 |     int unused_factor = 3;\n'
    '      |         ^~~~~~~~~~~~~\n'
    "noisy.h:6:9: warning: unused variable 'shift' [-Wunused-variable]\n"
    '    6 |     int shift;\n'
    '      |         ^~~~~\n'
    'g++ -Wall -Wextra -O2 -std=c++17 -c mod28.cpp -o mod28.o\n'
    'In file included from mod28.cpp:1:\n'
    "noisy.h: In function 'int scale(int)':\n"
    "noisy.h:5:9: warning: unused variable 'unused_factor' [-Wunused-variable]\n"
    '    5 |     int unused_factor = 3;\n'
    '      |         ^~~~~~~~~~~~~\n'
    "noisy.h:6:9: warning: unused variable 'shift' [-Wunused-variable]\n"
    '    6 |     int shift;\n'
    '      |         ^~~~~\n'
    'g++ -Wall -Wextra -O2 -std=c++17 -c mod29.cpp -o mod29.o\n'
    'In file included from mod29.cpp:1:\n'
    "noisy.h: In function 'int scale(int)':\n"
    "noisy.h:5:9: warning: unused variable 'unused_factor' [-Wunused-variable]\n"
    '    5 |     int unused_factor = 3;\n'
    '      |         ^~~~~~~~~~~~~\n'
    "noisy.h:6:9: warning: unused variable 'shift' [-Wunused-variable]\n"
    '    6 |     int shift;\n'
    '      |         ^~~~~\n'
    'g++ -Wall -Wextra -O2 -std=c++17 -c mod3.cpp -o mod3.o\n'
    'In file included from mod3.cpp:1:\n'
    "noisy.h: In function 'int scale(int)':\n"
    "noisy.h:5:9: warning: unused variable 'unused_factor' [-Wunused-variable]\n"
    '    5 |     int unused_factor = 3;\n'
    '      |         ^~~~~~~~~~~~~\n'
    "noisy.h:6:9: warning: unused variable 'shift' [-Wunused-variable]\n"
    '    6 |     int shift;\n'
    '      |         ^~~~~\n'
    'g++ -Wall -Wextra -O2 -std=c++17 -c mod30.cpp -o mod30.o\n'
    'In file included from mod30.cpp:1:\n'
    "noisy.h: In function 'int scale(int)':\n"
    "noisy.h:5:9: warning: unused variable 'unused_factor' [-Wunused-variable]\n"
    '    5 |     int unused_factor = 3;\n'
    '      |         ^~~~~~~~~~~~~\n'
    "noisy.h:6:9: warning: unused variable 'shift' [-Wunused-variable]\n"
    '    6 |     int shift;\n'
    '      |         ^~~~~\n'
    'g++ -Wall -Wextra -O2 -std=c++17 -c mod4.cpp -o mod4.o\n'
    'In file included from mod4.cpp:1:\n'
    "noisy.h: In function 'int scale(int)':\n"
    "noisy.h:5:9: warning: unused variable 'unused_factor' [-Wunused-variable]\n"
    '    5 |     int unused_factor = 3;\n'
    '      |         ^~~~~~~~~~~~~\n'
    "noisy.h:6:9: warning: unused variable 'shift' [-Wunused-variable]\n"
    '    6 |     int shift;\n'
    '      |         ^~~~~\n'
    'g++ -Wall -Wextra -O2 -std=c++17 -c mod5.cpp -o mod5.o\n'
    'In file included from mod5.cpp:1:\n'
    "noisy.h: In function 'int scale(int)':\n"
    "noisy.h:5:9: warning: unused variable 'unused_factor' [-Wunused-variable]\n"
    '    5 |     int unused_factor = 3;\n'
    '      |         ^~~~~~~~~~~~~\n'
    "noisy.h:6:9: warning: unused variable 'shift' [-Wunused-variable]\n"
    '    6 |     int shift;\n'
    '      |         ^~~~~\n'
    'g++ -Wall -Wextra -O2 -std=c++17 -c mod6.cpp -o mod6.o\n'
    'In file included from mod6.cpp:1:\n'
    "noisy.h: In function 'int scale(int)':\n"
    "noisy.h:5:9: warning: unused variable 'unused_factor' [-Wunused-variable]\n"
    '    5 |     int unused_factor = 3;\n'
    '      |         ^~~~~~~~~~~~~\n'
    "noisy.h:6:9: warning: unused variable 'shift' [-Wunused-variable]\n"
    '    6 |     int shift;\n'
    '      |         ^~~~~\n'
    'g++ -Wall -Wextra -O2 -std=c++17 -c mod7.cpp -o mod7.o\n'
    'In file included from mod7.cpp:1:\n'
    "noisy.h: In function 'int scale(int)':\n"
    "noisy.h:5:9: warning: unused variable 'unused_factor' [-Wunused-variable]\n"
    '    5 |     int unused_factor = 3;\n'
    '      |         ^~~~~~~~~~~~~\n'
    "noisy.h:6:9: warning: unused variable 'shift' [-Wunused-variable]\n"
    '    6 |     int shift;\n'
    '      |         ^~~~~\n'
    "mod7.cpp: In function 'int g7(int)':\n"
    "mod7.cpp:2:21: warning: unused variable 'own' [-Wunused-variable]\n"
    '    2 | int g7(int x) { int own = 1; return scale(x); }\n'
    '      |                     ^~~\n'
    'g++ -Wall -Wextra -O2 -std=c++17 -c mod8.cpp -o mod8.o\n'
    'In file included from mod8.cpp:1:\n'
    "noisy.h: In function 'int scale(int)':\n"
    "noisy.h:5:9: warning: unused variable 'unused_factor' [-Wunused-variable]\n"
    '    5 |     int unused_factor = 3;\n'
    '      |         ^~~~~~~~~~~~~\n'
    "noisy.h:6:9: warning: unused variable 'shift' [-Wunused-variable]\n"
    '    6 |     int shift;\n'
    '      |         ^~~~~\n'
    'g++ -Wall -Wextra -O2 -std=c++17 -c mod9.cpp -o mod9.o\n'
    'In file included from mod9.cpp:1:\n'
    "noisy.h: In function 'int scale(int)':\n"
    "noisy.h:5:9: warning: unused variable 'unused_factor' [-Wunused-variable]\n"
    '    5 |     int unused_factor = 3;\n'
    '      |         ^~~~~~~~~~~~~\n'
    "noisy.h:6:9: warning: unused variable 'shift' [-Wunused-variable]\n"
    '    6 |     int shift;\n'
    '      |         ^~~~~\n'
)

_W_HDR = re.compile(r"^(\S[^:]*?):(\d+):(?:(\d+):)? (warning|error|fatal error|note|remark): (.*)$")
_W_CHAIN = re.compile(r"^In file included from |^\s+from \S.*:\d+[,:]$")
_W_CTX = re.compile(r"^\S[^:]*: In .*:$")
_W_HDR_EXT = (".h", ".hh", ".hpp", ".hxx", ".inl", ".ipp", ".tcc", ".inc")
_W_NOTE = re.compile(r"^\[ctxprune: this warning also occurred in (\d+) more translation units?\]$")
_W_GUTTER = re.compile(r"^\d+ \|")


def _w_foldable(m) -> bool:
    return m is not None and m.group(4) == "warning" and m.group(1).endswith(_W_HDR_EXT)


def _w_body(l: str) -> bool:
    return l[:1].isspace() and not _W_CHAIN.match(l) or bool(_W_GUTTER.match(l)) or bool(_W_NOTE.match(l))


def dedupe_header_warnings(text: str) -> str:
    lines = text.split("\n")
    n = len(lines)
    items, i, ctx = [], 0, None  # item: [kind, start, end, key, count]
    while i < n:
        l = lines[i]
        if _W_CHAIN.match(l):
            items.append(["chain", i, i + 1, None, 0])
            ctx = None
            i += 1
        elif _W_CTX.match(l):
            items.append(["ctx", i, i + 1, None, 0])
            ctx = l
            i += 1
        elif (m := _W_HDR.match(l)) and m.group(4) in ("warning", "error", "fatal error"):
            j = i + 1
            while j < n and (_w_body(lines[j]) or ((mm := _W_HDR.match(lines[j])) and mm.group(4) in ("note", "remark"))):
                j += 1
            extra = sum(int(_W_NOTE.match(x).group(1)) for x in lines[i:j] if _W_NOTE.match(x))
            body = tuple(x for x in lines[i:j] if not _W_NOTE.match(x))
            items.append(["unit", i, j, (ctx, body) if _w_foldable(m) else None, 1 + extra])
            i = j
        else:
            items.append(["text", i, i + 1, None, 0])
            ctx = None
            i += 1
    first, total = {}, Counter()
    for k, it in enumerate(items):
        if it[0] == "unit" and it[3] is not None:
            total[it[3]] += it[4]
            first.setdefault(it[3], k)
    dropped = [it[0] == "unit" and it[3] is not None and first[it[3]] != k for k, it in enumerate(items)]
    if not any(dropped):
        return text
    # a prefix line (chain / ctx) goes when every unit it introduces went
    keep = [not d for d in dropped]
    for k, it in enumerate(items):
        if it[0] in ("chain", "ctx"):
            j, units = k + 1, []
            while j < len(items) and items[j][0] in ("unit", "ctx"):
                if items[j][0] == "ctx" and it[0] == "ctx":
                    break
                if items[j][0] == "unit":
                    units.append(j)
                j += 1
            keep[k] = not (units and all(dropped[u] for u in units))
    out = []
    for k, it in enumerate(items):
        if not keep[k]:
            continue
        seg = [x for x in lines[it[1]:it[2]] if not (it[0] == "unit" and _W_NOTE.match(x))]
        out += seg
        if it[0] == "unit" and it[3] is not None and total[it[3]] > 1 and first[it[3]] == k:
            out.append(f"[ctxprune: this warning also occurred in {total[it[3]] - 1} more translation unit{'s' if total[it[3]] > 2 else ''}]")
    return shrink_only(text, "\n".join(out))


def probes_1151(text: str) -> dict:
    sites, others, texts = Counter(), [], []
    last = None
    for l in text.split("\n"):
        if (m := _W_NOTE.match(l)):
            if last is not None:
                sites[last] += int(m.group(1))
            continue
        if _W_CHAIN.match(l) or _W_CTX.match(l) or _w_body(l):
            continue
        if (m := _W_HDR.match(l)):
            if _w_foldable(m):
                sites[l] += 1
                last = l
            elif m.group(4) in ("warning", "error", "fatal error"):
                others.append(l)
                last = None
            continue
        texts.append(l)
        last = None
    return {"header warning site -> number of translation units": sorted(sites.items()), "errors and own-file warnings, verbatim": others,
            "other output lines (make commands, summaries)": texts}


_MAKE_BLOCKS = [b for b in re.split(r"(?m)^(?=g\+\+ )", MAKE_30_TUS) if b]  # one block per 'g++ -c modN.cpp' command

case(
    1151,
    claim="A warning in a header (.h/.hpp/...) that repeats with the same context line and echoed code in several translation units is printed once, followed by '[ctxprune: this warning also occurred in N more translation units]'; the include-chain and context lines of fully duplicated blocks go with them. Every distinct header warning site with its translation-unit count, every error and every warning in a non-header file, and every make command line are unchanged.",
    loses="which translation units reported a header warning (only how many) and the include chains of the dropped repeats",
    encode=dedupe_header_warnings,
    probes=probes_1151,
    fixtures=lambda: [
        Fixture("make -k over 30 .cpp files sharing noisy.h; two have an extra warning (real, gcc 13)", MAKE_30_TUS, True),
        Fixture("two TUs only", cat(*_MAKE_BLOCKS[1:3]), True),
        Fixture("one TU: nothing repeats", _MAKE_BLOCKS[1], False),
        Fixture("header ERROR repeated in three TUs is not folded", "g++ -c a.cpp\nIn file included from a.cpp:1:\nbad.h:3:1: error: unknown type name 'Foo'\n    3 | Foo f;\n      | ^~~\ng++ -c b.cpp\nIn file included from b.cpp:1:\nbad.h:3:1: error: unknown type name 'Foo'\n    3 | Foo f;\n      | ^~~\ng++ -c c.cpp\nIn file included from c.cpp:1:\nbad.h:3:1: error: unknown type name 'Foo'\n    3 | Foo f;\n      | ^~~\n", False),
        Fixture("identical warnings in a .cpp (not a header) repeat on purpose", "a.cpp:2:5: warning: unused variable 'x' [-Wunused-variable]\n    2 |     int x;\n      |         ^\na.cpp:2:5: warning: unused variable 'x' [-Wunused-variable]\n    2 |     int x;\n      |         ^\n", False),
        Fixture("text that looks like the annotation", "[ctxprune: this warning also occurred in 4 more translation units]\nIn file included from a.cpp:1:\nx.h:1:1: warning: w [-Wall]\n", None),
    ],
    min_gain=0.2,
)



# ===============================================================================================================
# #118 Strip echoed source lines and caret/squiggle markers from diagnostics (ruff full, tsc --pretty, gcc, clang,
# Python 3.11+ tracebacks, difflib '?' lines). RULE: file:line:col and the message of every diagnostic must survive.

RUFF_FULL = (
    'F401 [*] `os` imported but unused\n'
    ' --> a.py:1:8\n'
    '  |\n'
    '1 | import os\n'
    '  |        ^^\n'
    '2 | import sys\n'
    '3 | from typing import List\n'
    '  |\n'
    'help: Remove unused import: `os`\n'
    '  |\n'
    '  - import os\n'
    '1 | import sys\n'
    '  |\n'
    '\n'
    'F401 [*] `sys` imported but unused\n'
    ' --> a.py:2:8\n'
    '  |\n'
    '1 | import os\n'
    '2 | import sys\n'
    '  |        ^^^\n'
    '3 | from typing import List\n'
    '  |\n'
    'help: Remove unused import: `sys`\n'
    '  |\n'
    '1 | import os\n'
    '  - import sys\n'
    '2 | from typing import List\n'
    '  |\n'
    '\n'
    'UP035 `typing.List` is deprecated, use `list` instead\n'
    ' --> a.py:3:1\n'
    '  |\n'
    '1 | import os\n'
    '2 | import sys\n'
    '3 | from typing import List\n'
    '  | ^^^^^^^^^^^^^^^^^^^^^^^\n'
    '\n'
    'F841 Local variable `y` is assigned to but never used\n'
    ' --> a.py:7:5\n'
    '  |\n'
    '6 | def f(x):\n'
    '7 |     y = 1\n'
    '  |     ^\n'
    '8 |     return x\n'
    '  |\n'
    'help: Remove assignment to unused variable `y`\n'
    '\n'
    'UP006 [*] Use `list` instead of `List` for type annotation\n'
    '  --> a.py:11:14\n'
    '   |\n'
    '11 | def g(items: List[int]) -> int:\n'
    '   |              ^^^^\n'
    '12 |     total = 0\n'
    '13 |     for i in range(len(items)):\n'
    '   |\n'
    'help: Replace with `list`\n'
    '   |\n'
    '10 |\n'
    '   - def g(items: List[int]) -> int:\n'
    '11 + def g(items: list[int]) -> int:\n'
    '12 |     total = 0\n'
    '   |\n'
    '\n'
    'E711 Comparison to `None` should be `cond is None`\n'
    '  --> a.py:15:17\n'
    '   |\n'
    '13 |     for i in range(len(items)):\n'
    '14 |         total += items[i]\n'
    '15 |     if total == None:\n'
    '   |                 ^^^^\n'
    '16 |         return 0\n'
    '17 |     return total\n'
    '   |\n'
    'help: Replace with `cond is None`\n'
    '\n'
    'Found 6 errors.\n'
    '[*] 3 fixable with the `--fix` option (2 hidden fixes can be enabled with the `--unsafe-fixes` option).\n'
)
RUFF_CONCISE = (
    'a.py:1:8: F401 [*] `os` imported but unused\n'
    'a.py:2:8: F401 [*] `sys` imported but unused\n'
    'a.py:3:1: UP035 `typing.List` is deprecated, use `list` instead\n'
    'a.py:7:5: F841 Local variable `y` is assigned to but never used\n'
    'a.py:11:14: UP006 [*] Use `list` instead of `List` for type annotation\n'
    'a.py:15:17: E711 Comparison to `None` should be `cond is None`\n'
    'Found 6 errors.\n'
    '[*] 3 fixable with the `--fix` option (2 hidden fixes can be enabled with the `--unsafe-fixes` option).\n'
)
TSC_PRETTY = (
    "\x1b[96mt.ts\x1b[0m:\x1b[93m1\x1b[0m:\x1b[93m7\x1b[0m - \x1b[91merror\x1b[0m\x1b[90m TS2322: \x1b[0mType 'string' is not assignable to type 'number'.\n"
    '\n'
    '\x1b[7m1\x1b[0m const x: number = "hello";\n'
    '\x1b[7m \x1b[0m \x1b[91m      ~\x1b[0m\n'
    '\n'
    "\x1b[96mt.ts\x1b[0m:\x1b[93m5\x1b[0m:\x1b[93m8\x1b[0m - \x1b[91merror\x1b[0m\x1b[90m TS2345: \x1b[0mArgument of type 'string' is not assignable to parameter of type 'number'.\n"
    '\n'
    '\x1b[7m5\x1b[0m add(1, "2");\n'
    '\x1b[7m \x1b[0m \x1b[91m       ~~~\x1b[0m\n'
    '\n'
    "\x1b[96mt.ts\x1b[0m:\x1b[93m7\x1b[0m:\x1b[93m7\x1b[0m - \x1b[91merror\x1b[0m\x1b[90m TS2741: \x1b[0mProperty 'age' is missing in type '{ name: string; }' but required in type 'P'.\n"
    '\n'
    '\x1b[7m7\x1b[0m const p: P = { name: "a" };\n'
    '\x1b[7m \x1b[0m \x1b[91m      ~\x1b[0m\n'
    '\n'
    '  \x1b[96mt.ts\x1b[0m:\x1b[93m6\x1b[0m:\x1b[93m29\x1b[0m\n'
    '    \x1b[7m6\x1b[0m interface P { name: string; age: number }\n'
    '    \x1b[7m \x1b[0m \x1b[96m                            ~~~\x1b[0m\n'
    "    'age' is declared here.\n"
    '\n'
    "\x1b[96mt.ts\x1b[0m:\x1b[93m8\x1b[0m:\x1b[93m13\x1b[0m - \x1b[91merror\x1b[0m\x1b[90m TS2552: \x1b[0mCannot find name 'undefinedVar'. Did you mean 'undefined'?\n"
    '\n'
    '\x1b[7m8\x1b[0m console.log(undefinedVar);\n'
    '\x1b[7m \x1b[0m \x1b[91m            ~~~~~~~~~~~~\x1b[0m\n'
    '\n'
    '\n'
    'Found 4 errors in the same file, starting at: t.ts\x1b[90m:1\x1b[0m\n'
    '\n'
)
TSC_PRETTY_NOANSI = (
    "t.ts:1:7 - error TS2322: Type 'string' is not assignable to type 'number'.\n"
    '\n'
    '1 const x: number = "hello";\n'
    '        ~\n'
    '\n'
    "t.ts:5:8 - error TS2345: Argument of type 'string' is not assignable to parameter of type 'number'.\n"
    '\n'
    '5 add(1, "2");\n'
    '         ~~~\n'
    '\n'
    "t.ts:7:7 - error TS2741: Property 'age' is missing in type '{ name: string; }' but required in type 'P'.\n"
    '\n'
    '7 const p: P = { name: "a" };\n'
    '        ~\n'
    '\n'
    '  t.ts:6:29\n'
    '    6 interface P { name: string; age: number }\n'
    '                                  ~~~\n'
    "    'age' is declared here.\n"
    '\n'
    "t.ts:8:13 - error TS2552: Cannot find name 'undefinedVar'. Did you mean 'undefined'?\n"
    '\n'
    '8 console.log(undefinedVar);\n'
    '              ~~~~~~~~~~~~\n'
    '\n'
    '\n'
    'Found 4 errors in the same file, starting at: t.ts:1\n'
    '\n'
)
TSC_PLAIN = (
    "t.ts(1,7): error TS2322: Type 'string' is not assignable to type 'number'.\n"
    "t.ts(5,8): error TS2345: Argument of type 'string' is not assignable to parameter of type 'number'.\n"
    "t.ts(7,7): error TS2741: Property 'age' is missing in type '{ name: string; }' but required in type 'P'.\n"
    "t.ts(8,13): error TS2552: Cannot find name 'undefinedVar'. Did you mean 'undefined'?\n"
)
GCC_DIAG = (
    'e.c: In function ‘main’:\n'
    'e.c:4:20: error: ‘cnt’ undeclared (first use in this function); did you mean ‘int’?\n'
    '    4 |     printf("%d\\n", cnt);\n'
    '      |                    ^~~\n'
    '      |                    int\n'
    'e.c:4:20: note: each undeclared identifier is reported only once for each function it appears in\n'
    'e.c:5:15: warning: initialization of ‘char *’ from ‘int’ makes pointer from integer without a cast [-Wint-conversion]\n'
    '    5 |     char *s = 5;\n'
    '      |               ^\n'
    'e.c:6:12: warning: implicit declaration of function ‘undefined_fn’ [-Wimplicit-function-declaration]\n'
    '    6 |     return undefined_fn(count);\n'
    '      |            ^~~~~~~~~~~~\n'
    'e.c:5:11: warning: unused variable ‘s’ [-Wunused-variable]\n'
    '    5 |     char *s = 5;\n'
    '      |           ^\n'
)
CLANG_DIAG = (
    "e.c:4:20: error: use of undeclared identifier 'cnt'\n"
    '    4 |     printf("%d\\n", cnt);\n'
    '      |                    ^\n'
    "e.c:5:11: error: incompatible integer to pointer conversion initializing 'char *' with an expression of type 'int' [-Wint-conversion]\n"
    '    5 |     char *s = 5;\n'
    '      |           ^   ~\n'
    "e.c:6:12: error: call to undeclared function 'undefined_fn'; ISO C99 and later do not support implicit function declarations [-Wimplicit-function-declaration]\n"
    '    6 |     return undefined_fn(count);\n'
    '      |            ^\n'
    '3 errors generated.\n'
)
PY_TRACEBACK = (
    'Traceback (most recent call last):\n'
    '  File "/work/app/tb.py", line 10, in <module>\n'
    '    run({"items": [{"price": 2}]})\n'
    '    ~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^\n'
    '  File "/work/app/tb.py", line 8, in run\n'
    '    return sum(i["price"] * i["qty"] for i in items)\n'
    '  File "/work/app/tb.py", line 8, in <genexpr>\n'
    '    return sum(i["price"] * i["qty"] for i in items)\n'
    '                            ~^^^^^^^\n'
    "KeyError: 'qty'\n"
)
UNITTEST_NDIFF = (
    'F\n'
    '======================================================================\n'
    'FAIL: test_total (__main__.T.test_total)\n'
    '----------------------------------------------------------------------\n'
    'Traceback (most recent call last):\n'
    '  File "/work/app/ut.py", line 6, in test_total\n'
    '    self.assertEqual(a, b)\n'
    '    ~~~~~~~~~~~~~~~~^^^^^^\n'
    "AssertionError: 'def [19 chars]s = 0\\n    for i in items:\\n        s += i.pri[14 chars] s\\n' != 'def [19 chars]s = 0.0\\n    for it in items:\\n        s += it[37 chars]2)\\n'\n"
    '  def total(items):\n'
    '-     s = 0\n'
    '+     s = 0.0\n'
    '?         ++\n'
    '-     for i in items:\n'
    '+     for it in items:\n'
    '?          +\n'
    '-         s += i.price\n'
    '+         s += it.price * it.qty\n'
    '?               +      +++++++++\n'
    '-     return s\n'
    '+     return round(s, 2)\n'
    '\n'
    '\n'
    '----------------------------------------------------------------------\n'
    'Ran 1 test in 0.001s\n'
    '\n'
    'FAILED (failures=1)\n'
)

_GCC_HDR = re.compile(r"^[^\s:]\S*?:\d+:(?:\d+:)? (?:fatal error|error|warning|note|remark)\b")
_RUFF_ARROW = re.compile(r"^\s*--> \S.*:\d+:\d+$")
_TSC_HDR = re.compile(r"^\s*\S+:\d+:\d+(?: - (?:error|warning|message) TS\d+:.*)?$")
_GUT_SRC = re.compile(r"^\s*\d+ \|")  # '    4 |     printf(...)'
_GUT_MARK = re.compile(r"^\s*\|[\s~^+-]*$")  # '      |     ^~~', and the empty gutter line '  |'
_RUFF_DIFF = re.compile(r"^\s*(?:\d+\s+)?[-+] ")  # fix preview inside a ruff block: '  - import os', '11 + def g()'
_TSC_SRC = re.compile(r"^\s*\d+ ")
_TSC_SQ = re.compile(r"^\s+~[~\s]*$")
_TB_MARK = re.compile(r"^ {4,}[ ~^]*[~^][ ~^]*$")
_NDIFF_HINT = re.compile(r"^\? [ +^~-]*[+^~-][ +^~-]*$")


def strip_echo(text: str) -> str:
    lines = text.split("\n")
    P = [plain(l) for l in lines]
    n = len(lines)
    drop = [False] * n
    state = None
    i = 0
    while i < n:
        p = P[i]
        if _GCC_HDR.match(p):
            state = "gcc"
        elif _RUFF_ARROW.match(p):
            state = "ruff"
        elif p.startswith("Traceback (most recent call last):"):
            state = "tb"
        elif _TSC_HDR.match(p):
            state = "tsc"
        elif state in ("gcc", "ruff") and (_GUT_SRC.match(p) or _GUT_MARK.match(p)):
            drop[i] = True
        elif state == "ruff" and _RUFF_DIFF.match(p):
            drop[i] = True
        elif state == "ruff" and not p.strip():
            state = None
        elif state == "gcc":
            state = None
        elif state == "tsc" and _TSC_SRC.match(p) and i + 1 < n and _TSC_SQ.match(P[i + 1]):
            drop[i] = drop[i + 1] = True
            if i + 2 < n and not P[i + 2].strip() and i > 0 and not P[i - 1].strip():
                drop[i + 2] = True  # keep a single blank line between diagnostics
            i += 1
        elif state == "tb" and _TB_MARK.match(p):
            drop[i] = True
        elif state == "tb" and p and not p[0].isspace():
            state = None
        elif i > 0 and _NDIFF_HINT.match(p) and (P[i - 1][:2] in ("- ", "+ ") or (drop[i - 1] and _NDIFF_HINT.match(P[i - 1]))):
            drop[i] = True
        i += 1
    out = "\n".join(l for l, d in zip(lines, drop) if not d)
    return shrink_only(text, out)


def probes_118(text: str) -> dict:
    P = [plain(l) for l in text.split("\n")]
    diags, frames, excs, notes, summary, ndiff = [], [], [], [], [], []
    in_tb = False
    for i, p in enumerate(P):
        nxt = P[i + 1] if i + 1 < len(P) else ""
        if m := re.match(r"^(\S+?):(\d+):(\d+): (fatal error|error|warning|note|remark|[A-Z]+\d+)\b\s*(.*)$", p):
            diags.append((m[1], int(m[2]), int(m[3]), m[4], m[5]))
        elif m := re.match(r"^(\S+?):(\d+):(\d+) - (error|warning) (TS\d+): (.*)$", p):
            diags.append((m[1], int(m[2]), int(m[3]), m[5], m[6]))
        elif m := re.match(r"^(\S+?)\((\d+),(\d+)\): (error|warning) (TS\d+): (.*)$", p):
            diags.append((m[1], int(m[2]), int(m[3]), m[5], m[6]))
        elif (m := re.match(r"^([A-Z]+\d+) (.*)$", p)) and (a := re.match(r"^\s*--> (\S+):(\d+):(\d+)$", nxt)):
            diags.append((a[1], int(a[2]), int(a[3]), m[1], m[2]))
        if m := re.match(r'^\s*File "(.+)", line (\d+), in (.+)$', p):
            src = nxt.strip() if re.match(r"^ {4}\S", nxt) and not re.match(r"^ {4,}[ ~^]*[~^][ ~^]*$", nxt) else None
            frames.append((m[1], int(m[2]), m[3], src))
        if p.startswith("Traceback (most recent call last):"):
            in_tb = True
        elif in_tb and p and not p[0].isspace():
            excs.append(p)
            in_tb = False
        if re.match(r"^\s*(help|note): ", p):
            notes.append(p.strip())
        if re.match(r"^(Found \d+ errors?\b.*|\d+ errors? generated\.|\d+ warnings? generated\.|\[\*\] .*)$", p):
            summary.append(p)
        if p[:2] in ("- ", "+ "):
            ndiff.append(p)
    return {"diagnostics (file, line, col, code/severity, message)": diags, "traceback frames with source line": frames,
            "exception lines": excs, "help/note lines": notes, "summary lines": summary, "ndiff -/+ lines": ndiff}


def _noise(tag: str, n: int) -> str:
    return "".join(f"[{tag}] step {i}/{n} ok: compiled unit_{i}.o in 0.{i % 10}s\n" for i in range(1, n + 1))


COMPOSITE_118 = cat(
    _noise("build", 6), GCC_DIAG, _noise("lint", 5), RUFF_FULL, _noise("types", 4), TSC_PRETTY, _noise("test", 3), PY_TRACEBACK,
)

case(
    118,
    claim="Drops the echoed source line, gutter and ~~~/^^^ marker lines under ruff, gcc, clang and tsc --pretty diagnostics, the ^^^^ marker lines in Python 3.11+ tracebacks and difflib '?' hint lines; every diagnostic header (file:line:col, code, message), traceback frame with its source line, exception line, help/note line and summary line is unchanged.",
    loses="the code excerpt and the exact column span shown under each diagnostic (the start column stays in the header), ruff's fix-preview diff under 'help:', gcc/clang caret underline widths, and difflib's character-level change hints; a gutter line carrying a label or fix-it text is kept",
    encode=strip_echo,
    probes=probes_118,
    fixtures=lambda: [
        Fixture("ruff full format (6 findings, real)", RUFF_FULL, True),
        Fixture("tsc --pretty with ANSI colour (real)", TSC_PRETTY, True),
        Fixture("tsc --pretty, colour stripped", TSC_PRETTY_NOANSI, True),
        Fixture("gcc -Wall (real, UTF-8 quotes)", GCC_DIAG, True),
        Fixture("clang (real)", CLANG_DIAG, True),
        Fixture("python 3.13 traceback with ~~~^^^ markers (real)", PY_TRACEBACK, True),
        Fixture("unittest assertEqual with ndiff '?' lines (real)", UNITTEST_NDIFF, True),
        Fixture("build log: gcc first, ruff middle, tsc and traceback at the end", COMPOSITE_118, True),
        Fixture("ruff concise (nothing to strip)", RUFF_CONCISE, False),
        Fixture("tsc --pretty false (nothing to strip)", TSC_PLAIN, False),
        Fixture("unified diff and table: '- ' / '|' lines that are not gutters", "--- a/x\n+++ b/x\n@@ -1,2 +1,2 @@\n-old = 1\n+new = 1\n | a | b |\n | - | - |\n  - bullet\n", False),
    ],
    min_gain=0.08,
)



# ===============================================================================================================
# #121 Lockfile-diff digest: hunks of package-lock.json, yarn.lock, pnpm-lock.yaml, Cargo.lock, uv.lock and go.sum
# become 'name old->new' lines; hashes, URLs, sizes and dependency lists go. Non-lockfile diffs stay verbatim.
# RULE: every name/version pair that left or entered the lockfile must survive. The probe reads the ORIGINAL diff
# independently: it rebuilds the old and new side of each hunk as text and runs a multi-line regex per format; the
# encoder is a line scanner over the same sides.

LOCK_COMMIT_DIFF = (
    'diff --git a/web/package-lock.json b/web/package-lock.json\n'
    'index 3567e6c..115e4a1 100644\n'
    '--- a/web/package-lock.json\n'
    '+++ b/web/package-lock.json\n'
    '@@ -8,18 +8,19 @@\n'
    '       "name": "demo",\n'
    '       "version": "1.0.0",\n'
    '       "dependencies": {\n'
    '-        "debug": "4.3.4",\n'
    '-        "lodash": "4.17.20",\n'
    '-        "semver": "7.5.4"\n'
    '+        "debug": "4.3.7",\n'
    '+        "lodash": "4.17.21",\n'
    '+        "ms": "2.1.3",\n'
    '+        "semver": "7.6.3"\n'
    '       }\n'
    '     },\n'
    '     "node_modules/debug": {\n'
    '-      "version": "4.3.4",\n'
    '-      "resolved": "https://registry.npmjs.org/debug/-/debug-4.3.4.tgz",\n'
    '-      "integrity": "sha512-PRWFHuSU3eDtQJPvnNY7Jcket1j0t5OuOsFzPPzsekD52Zl8qUfFIPEiswXqIvHWGVHOgX+7G/vCNNhehwxfkQ==",\n'
    '+      "version": "4.3.7",\n'
    '+      "resolved": "https://registry.npmjs.org/debug/-/debug-4.3.7.tgz",\n'
    '+      "integrity": "sha512-Er2nc/H7RrMXZBFCEim6TCmMk02Z8vLC2Rbi1KEBggpo0fS6l0S1nnapwmIi3yW/+GOJap1Krg4w0Hg80oCqgQ==",\n'
    '       "license": "MIT",\n'
    '       "dependencies": {\n'
    '-        "ms": "2.1.2"\n'
    '+        "ms": "^2.1.3"\n'
    '       },\n'
    '       "engines": {\n'
    '         "node": ">=6.0"\n'
    '@@ -31,49 +32,28 @@\n'
    '       }\n'
    '     },\n'
    '     "node_modules/lodash": {\n'
    '-      "version": "4.17.20",\n'
    '-      "resolved": "https://registry.npmjs.org/lodash/-/lodash-4.17.20.tgz",\n'
    '-      "integrity": "sha512-PlhdFcillOINfeV7Ni6oF1TAEayyZBoZ8bcshTHqOYJYlrqzRK5hagpagky5o4HfCzzd1TRkXPMFq6cKk9rGmA==",\n'
    '+      "version": "4.17.21",\n'
    '+      "resolved": "https://registry.npmjs.org/lodash/-/lodash-4.17.21.tgz",\n'
    '+      "integrity": "sha512-v2kDEe57lecTulaDIuNTPy3Ry4gLGJ6Z1O3vE1krgXZNrsQ+LFTGHVxVjcXPs17LhbZVGedAJv8XZ1tvj5FvSg==",\n'
    '       "license": "MIT"\n'
    '     },\n'
    '-    "node_modules/lru-cache": {\n'
    '-      "version": "6.0.0",\n'
    '-      "resolved": "https://registry.npmjs.org/lru-cache/-/lru-cache-6.0.0.tgz",\n'
    '-      "integrity": "sha512-Jo6dJ04CmSjuznwJSS3pUeWmd/H0ffTlkXXgwZi+eq1UCmqQwCh+eLsYOYCwY991i2Fah4h1BEMCx4qThGbsiA==",\n'
    '-      "license": "ISC",\n'
    '-      "dependencies": {\n'
    '-        "yallist": "^4.0.0"\n'
    '-      },\n'
    '-      "engines": {\n'
    '-        "node": ">=10"\n'
    '-      }\n'
    '-    },\n'
    '     "node_modules/ms": {\n'
    '-      "version": "2.1.2",\n'
    '-      "resolved": "https://registry.npmjs.org/ms/-/ms-2.1.2.tgz",\n'
    '-      "integrity": "sha512-sGkPx+VjMtmA6MX27oA4FBFELFCZZ4S4XqeGOXCv68tT+jb3vk/RyaKWP0PTKyWtmLSM0b+adUTEvbs1PEaH2w==",\n'
    '+      "version": "2.1.3",\n'
    '+      "resolved": "https://registry.npmjs.org/ms/-/ms-2.1.3.tgz",\n'
    '+      "integrity": "sha512-6FlzubTLZG3J2a/NVCAleEhjzq5oxgHyaCU9yYXvcLsvoVaHJq/s5xXI6/XXP6tz7R9xAOtHnSO/tXtF3WRTlA==",\n'
    '       "license": "MIT"\n'
    '     },\n'
    '     "node_modules/semver": {\n'
    '-      "version": "7.5.4",\n'
    '-      "resolved": "https://registry.npmjs.org/semver/-/semver-7.5.4.tgz",\n'
    '-      "integrity": "sha512-1bCSESV6Pv+i21Hvpxp3Dx+pSD8lIPt8uVjRrxAUt/nbswYc+tK6Y2btiULjd4+fnq15PX+nqQDC7Oft7WkwcA==",\n'
    '+      "version": "7.6.3",\n'
    '+      "resolved": "https://registry.npmjs.org/semver/-/semver-7.6.3.tgz",\n'
    '+      "integrity": "sha512-oVekP1cKtI+CTDvHWYFUcMtsK/00wmAEfyqKfNdARm8u1wNVhSgaX7A8d4UuIlUI5e84iEwOhs7ZPYRmzU9U6A==",\n'
    '       "license": "ISC",\n'
    '-      "dependencies": {\n'
    '-        "lru-cache": "^6.0.0"\n'
    '-      },\n'
    '       "bin": {\n'
    '         "semver": "bin/semver.js"\n'
    '       },\n'
    '       "engines": {\n'
    '         "node": ">=10"\n'
    '       }\n'
    '-    },\n'
    '-    "node_modules/yallist": {\n'
    '-      "version": "4.0.0",\n'
    '-      "resolved": "https://registry.npmjs.org/yallist/-/yallist-4.0.0.tgz",\n'
    '-      "integrity": "sha512-3wdGidZyq5PB084XLES5TpOSRA3wjXAlIWMhum2kRcv/41Sn2emQ0dycQW4uZXLejwKvg6EsvbdlVL+FYEct7A==",\n'
    '-      "license": "ISC"\n'
    '     }\n'
    '   }\n'
    ' }\n'
    'diff --git a/web2/pnpm-lock.yaml b/web2/pnpm-lock.yaml\n'
    'index fe0f6a8..eeb1f85 100644\n'
    '--- a/web2/pnpm-lock.yaml\n'
    '+++ b/web2/pnpm-lock.yaml\n'
    '@@ -9,19 +9,22 @@ importers:\n'
    '   .:\n'
    '     dependencies:\n'
    '       debug:\n'
    '-        specifier: 4.3.4\n'
    '-        version: 4.3.4\n'
    '+        specifier: 4.3.7\n'
    '+        version: 4.3.7\n'
    '       lodash:\n'
    '-        specifier: 4.17.20\n'
    '-        version: 4.17.20\n'
    '+        specifier: 4.17.21\n'
    '+        version: 4.17.21\n'
    '+      ms:\n'
    '+        specifier: 2.1.3\n'
    '+        version: 2.1.3\n'
    '       semver:\n'
    '-        specifier: 7.5.4\n'
    '-        version: 7.5.4\n'
    '+        specifier: 7.6.3\n'
    '+        version: 7.6.3\n'
    ' \n'
    ' packages:\n'
    ' \n'
    '-  debug@4.3.4:\n'
    '-    resolution: {integrity: sha512-PRWFHuSU3eDtQJPvnNY7Jcket1j0t5OuOsFzPPzsekD52Zl8qUfFIPEiswXqIvHWGVHOgX+7G/vCNNhehwxfkQ==}\n'
    '+  debug@4.3.7:\n'
    '+    resolution: {integrity: sha512-Er2nc/H7RrMXZBFCEim6TCmMk02Z8vLC2Rbi1KEBggpo0fS6l0S1nnapwmIi3yW/+GOJap1Krg4w0Hg80oCqgQ==}\n'
    "     engines: {node: '>=6.0'}\n"
    '     peerDependencies:\n'
    "       supports-color: '*'\n"
    '@@ -29,40 +32,25 @@ packages:\n'
    '       supports-color:\n'
    '         optional: true\n'
    ' \n'
    '-  lodash@4.17.20:\n'
    '-    resolution: {integrity: sha512-PlhdFcillOINfeV7Ni6oF1TAEayyZBoZ8bcshTHqOYJYlrqzRK5hagpagky5o4HfCzzd1TRkXPMFq6cKk9rGmA==}\n'
    '+  lodash@4.17.21:\n'
    '+    resolution: {integrity: sha512-v2kDEe57lecTulaDIuNTPy3Ry4gLGJ6Z1O3vE1krgXZNrsQ+LFTGHVxVjcXPs17LhbZVGedAJv8XZ1tvj5FvSg==}\n'
    ' \n'
    '-  lru-cache@6.0.0:\n'
    '-    resolution: {integrity: sha512-Jo6dJ04CmSjuznwJSS3pUeWmd/H0ffTlkXXgwZi+eq1UCmqQwCh+eLsYOYCwY991i2Fah4h1BEMCx4qThGbsiA==}\n'
    "-    engines: {node: '>=10'}\n"
    '-\n'
    '-  ms@2.1.2:\n'
    '-    resolution: {integrity: sha512-sGkPx+VjMtmA6MX27oA4FBFELFCZZ4S4XqeGOXCv68tT+jb3vk/RyaKWP0PTKyWtmLSM0b+adUTEvbs1PEaH2w==}\n'
    '+  ms@2.1.3:\n'
    '+    resolution: {integrity: sha512-6FlzubTLZG3J2a/NVCAleEhjzq5oxgHyaCU9yYXvcLsvoVaHJq/s5xXI6/XXP6tz7R9xAOtHnSO/tXtF3WRTlA==}\n'
    ' \n'
    '-  semver@7.5.4:\n'
    '-    resolution: {integrity: sha512-1bCSESV6Pv+i21Hvpxp3Dx+pSD8lIPt8uVjRrxAUt/nbswYc+tK6Y2btiULjd4+fnq15PX+nqQDC7Oft7WkwcA==}\n'
    '+  semver@7.6.3:\n'
    '+    resolution: {integrity: sha512-oVekP1cKtI+CTDvHWYFUcMtsK/00wmAEfyqKfNdARm8u1wNVhSgaX7A8d4UuIlUI5e84iEwOhs7ZPYRmzU9U6A==}\n'
    "     engines: {node: '>=10'}\n"
    '     hasBin: true\n'
    ' \n'
    '-  yallist@4.0.0:\n'
    '-    resolution: {integrity: sha512-3wdGidZyq5PB084XLES5TpOSRA3wjXAlIWMhum2kRcv/41Sn2emQ0dycQW4uZXLejwKvg6EsvbdlVL+FYEct7A==}\n'
    '-\n'
    ' snapshots:\n'
    ' \n'
    '-  debug@4.3.4:\n'
    '+  debug@4.3.7:\n'
    '     dependencies:\n'
    '-      ms: 2.1.2\n'
    '+      ms: 2.1.3\n'
    ' \n'
    '-  lodash@4.17.20: {}\n'
    '+  lodash@4.17.21: {}\n'
    ' \n'
    '-  lru-cache@6.0.0:\n'
    '-    dependencies:\n'
    '-      yallist: 4.0.0\n'
    '-\n'
    '-  ms@2.1.2: {}\n'
    '-\n'
    '-  semver@7.5.4:\n'
    '-    dependencies:\n'
    '-      lru-cache: 6.0.0\n'
    '+  ms@2.1.3: {}\n'
    ' \n'
    '-  yallist@4.0.0: {}\n'
    '+  semver@7.6.3: {}\n'
    'diff --git a/python/src/app.py b/python/src/app.py\n'
    'index faf33ee..6bd30ab 100644\n'
    '--- a/python/src/app.py\n'
    '+++ b/python/src/app.py\n'
    '@@ -3,10 +3,10 @@ import requests\n'
    ' API = "https://api.example.com"\n'
    ' \n'
    ' \n'
    '-def fetch(path, retries=2):\n'
    '+def fetch(path, retries=3):\n'
    '     url = f"{API}/{path}"\n'
    '     for attempt in range(retries):\n'
    '-        r = requests.get(url, timeout=5)\n'
    '+        r = requests.get(url, timeout=10)\n'
    '         if r.ok:\n'
    '             return r.json()\n'
    '-    raise RuntimeError(f"failed: {url}")\n'
    '+    raise RuntimeError(f"failed after {retries} attempts: {url}")\n'
    'diff --git a/python/uv.lock b/python/uv.lock\n'
    'index 901d02c..cb9c2b1 100644\n'
    '--- a/python/uv.lock\n'
    '+++ b/python/uv.lock\n'
    '@@ -4,20 +4,20 @@ requires-python = ">=3.10"\n'
    ' \n'
    ' [[package]]\n'
    ' name = "attrs"\n'
    '-version = "23.1.0"\n'
    '+version = "24.2.0"\n'
    ' source = { registry = "https://pypi.org/simple" }\n'
    '-sdist = { url = "https://files.pythonhosted.org/packages/97/90/81f95d5f705be17872843536b1868f351805acf6971251ff07c1b8334dbb/attrs-23.1.0.tar.gz", hash = "sha256:6279836d581513a26f1bf235f9acd333bc9115683f14f7e8fae46c98fc50e015", size = 212878, upload-time = "2023-04-16T10:48:18.214Z" }\n'
    '+sdist = { url = "https://files.pythonhosted.org/packages/fc/0f/aafca9af9315aee06a89ffde799a10a582fe8de76c563ee80bbcdc08b3fb/attrs-24.2.0.tar.gz", hash = "sha256:5cfb1b9148b5b086569baec03f20d7b6bf3bcacc9a42bebf87ffaaca362f6346", size = 792678, upload-time = "2024-08-06T14:37:38.364Z" }\n'
    ' wheels = [\n'
    '-    { url = "https://files.pythonhosted.org/packages/f0/eb/fcb708c7bf5056045e9e98f62b93bd7467eb718b0202e7698eb11d66416c/attrs-23.1.0-py3-none-any.whl", hash = "sha256:1f28b4522cdc2fb4256ac1a020c78acf9cba2c6b461ccd2c126f3aa8e8335d04", size = 61160, upload-time = "2023-04-16T10:48:16.358Z" },\n'
    '+    { url = "https://files.pythonhosted.org/packages/6a/21/5b6702a7f963e95456c0de2d495f67bf5fd62840ac655dc451586d23d39a/attrs-24.2.0-py3-none-any.whl", hash = "sha256:81921eb96de3191c8258c199618104dd27ac608d9366f5e35d011eae1867ede2", size = 63001, upload-time = "2024-08-06T14:37:36.958Z" },\n'
    ' ]\n'
    ' \n'
    ' [[package]]\n'
    ' name = "certifi"\n'
    '-version = "2023.7.22"\n'
    '+version = "2024.8.30"\n'
    ' source = { registry = "https://pypi.org/simple" }\n'
    '-sdist = { url = "https://files.pythonhosted.org/packages/98/98/c2ff18671db109c9f10ed27f5ef610ae05b73bd876664139cf95bd1429aa/certifi-2023.7.22.tar.gz", hash = "sha256:539cc1d13202e33ca466e88b2807e29f4c13049d6d87031a3c110744495cb082", size = 159517, upload-time = "2023-07-22T08:39:27.482Z" }\n'
    '+sdist = { url = "https://files.pythonhosted.org/packages/b0/ee/9b19140fe824b367c04c5e1b369942dd754c4c5462d5674002f75c4dedc1/certifi-2024.8.30.tar.gz", hash = "sha256:bec941d2aa8195e248a60b31ff9f0558284cf01a52591ceda73ea9afffd69fd9", size = 168507, upload-time = "2024-08-30T01:55:04.365Z" }\n'
    ' wheels = [\n'
    '-    { url = "https://files.pythonhosted.org/packages/4c/dd/2234eab22353ffc7d94e8d13177aaa050113286e93e7b40eae01fbf7c3d9/certifi-2023.7.22-py3-none-any.whl", hash = "sha256:92d6037539857d8206b8f6ae472e8b77db8058fec5937a1ef3f54304089edbb9", size = 158334, upload-time = "2023-07-22T08:39:25.345Z" },\n'
    '+    { url = "https://files.pythonhosted.org/packages/12/90/3c9ff0512038035f59d279fddeb79f5f1eccd8859f06d6163c58798b9487/certifi-2024.8.30-py3-none-any.whl", hash = "sha256:922820b53db7a7257ffbda3f597266d435245903d80737e34f8a45ff3e3230d8", size = 167321, upload-time = "2024-08-30T01:55:02.591Z" },\n'
    ' ]\n'
    ' \n'
    ' [[package]]\n'
    '@@ -191,33 +191,42 @@ dependencies = [\n'
    '     { name = "attrs" },\n'
    '     { name = "certifi" },\n'
    '     { name = "idna" },\n'
    '+    { name = "packaging" },\n'
    '     { name = "requests" },\n'
    '-    { name = "tomli" },\n'
    '     { name = "urllib3" },\n'
    ' ]\n'
    ' \n'
    ' [package.metadata]\n'
    ' requires-dist = [\n'
    '-    { name = "attrs", specifier = "==23.1.0" },\n'
    '-    { name = "certifi", specifier = "==2023.7.22" },\n'
    '-    { name = "idna", specifier = "==3.4" },\n'
    '-    { name = "requests", specifier = "==2.31.0" },\n'
    '-    { name = "tomli", specifier = "==2.0.1" },\n'
    '-    { name = "urllib3", specifier = "==2.0.7" },\n'
    '+    { name = "attrs", specifier = "==24.2.0" },\n'
    '+    { name = "certifi", specifier = "==2024.8.30" },\n'
    '+    { name = "idna", specifier = "==3.10" },\n'
    '+    { name = "packaging", specifier = "==24.1" },\n'
    '+    { name = "requests", specifier = "==2.32.3" },\n'
    '+    { name = "urllib3", specifier = "==2.2.3" },\n'
    ' ]\n'
    ' \n'
    ' [[package]]\n'
    ' name = "idna"\n'
    '-version = "3.4"\n'
    '+version = "3.10"\n'
    ' source = { registry = "https://pypi.org/simple" }\n'
    '-sdist = { url = "https://files.pythonhosted.org/packages/8b/e1/43beb3d38dba6cb420cefa297822eac205a277ab43e5ba5d5c46faf96438/idna-3.4.tar.gz", hash = "sha256:814f528e8dead7d329833b91c5faa87d60bf71824cd12a7530b5526063d02cb4", size = 183077, upload-time = "2022-09-14T00:24:27.719Z" }\n'
    '+sdist = { url = "https://files.pythonhosted.org/packages/f1/70/7703c29685631f5a7590aa73f1f1d3fa9a380e654b86af429e0934a32f7d/idna-3.10.tar.gz", hash = "sha256:12f65c9b470abda6dc35cf8e63cc574b1c52b11df2c86030af0ac09b01b13ea9", size = 190490, upload-time = "2024-09-15T18:07:39.745Z" }\n'
    ' wheels = [\n'
    '-    { url = "https://files.pythonhosted.org/packages/fc/34/3030de6f1370931b9dbb4dad48f6ab1015ab1d32447850b9fc94e60097be/idna-3.4-py3-none-any.whl", hash = "sha256:90b77e79eaa3eba6de819a0c442c0b4ceefc341a7a2ab77d7562bf49f425c5c2", size = 61538, upload-time = "2022-09-14T00:24:23.22Z" },\n'
    '+    { url = "https://files.pythonhosted.org/packages/76/c6/c88e154df9c4e1a2a66ccf0005a88dfb2650c1dffb6f5ce603dfbd452ce3/idna-3.10-py3-none-any.whl", hash = "sha256:946d195a0d259cbba61165e88e65941f16e9b36ea6ddb97f00452bae8b1287d3", size = 70442, upload-time = "2024-09-15T18:07:37.964Z" },\n'
    '+]\n'
    '+\n'
    '+[[package]]\n'
    '+name = "packaging"\n'
    '+version = "24.1"\n'
    '+source = { registry = "https://pypi.org/simple" }\n'
    '+sdist = { url = "https://files.pythonhosted.org/packages/51/65/50db4dda066951078f0a96cf12f4b9ada6e4b811516bf0262c0f4f7064d4/packaging-24.1.tar.gz", hash = "sha256:026ed72c8ed3fcce5bf8950572258698927fd1dbda10a5e981cdf0ac37f4f002", size = 148788, upload-time = "2024-06-09T23:19:24.956Z" }\n'
    '+wheels = [\n'
    '+    { url = "https://files.pythonhosted.org/packages/08/aa/cc0199a5f0ad350994d660967a8efb233fe0416e4639146c089643407ce6/packaging-24.1-py3-none-any.whl", hash = "sha256:5b8f2217dbdbd2f7f384c41c628544e6d52f2d0f53c6d0c3ea61aa5d1d7ff124", size = 53985, upload-time = "2024-06-09T23:19:21.909Z" },\n'
    ' ]\n'
    ' \n'
    ' [[package]]\n'
    ' name = "requests"\n'
    '-version = "2.31.0"\n'
    '+version = "2.32.3"\n'
    ' source = { registry = "https://pypi.org/simple" }\n'
    ' dependencies = [\n'
    '     { name = "certifi" },\n'
    '@@ -225,25 +234,16 @@ dependencies = [\n'
    '     { name = "idna" },\n'
    '     { name = "urllib3" },\n'
    ' ]\n'
    '-sdist = { url = "https://files.pythonhosted.org/packages/9d/be/10918a2eac4ae9f02f6cfe6414b7a155ccd8f7f9d4380d62fd5b955065c3/requests-2.31.0.tar.gz", hash = "sha256:942c5a758f98d790eaed1a29cb6eefc7ffb0d1cf7af05c3d2791656dbd6ad1e1", size = 110794, upload-time = "2023-05-22T15:12:44.175Z" }\n'
    '-wheels = [\n'
    '-    { url = "https://files.pythonhosted.org/packages/70/8e/0e2d847013cb52cd35b38c009bb167a1a26b2ce6cd6965bf26b47bc0bf44/requests-2.31.0-py3-none-any.whl", hash = "sha256:58cd2187c01e70e6e26505bca751777aa9f2ee0b7f4300988b709f44e013003f", size = 62574, upload-time = "2023-05-22T15:12:42.313Z" },\n'
    '-]\n'
    '-\n'
    '-[[package]]\n'
    '-name = "tomli"\n'
    '-version = "2.0.1"\n'
    '-source = { registry = "https://pypi.org/simple" }\n'
    '-sdist = { url = "https://files.pythonhosted.org/packages/c0/3f/d7af728f075fb08564c5949a9c95e44352e23dee646869fa104a3b2060a3/tomli-2.0.1.tar.gz", hash = "sha256:de526c12914f0c550d15924c62d72abc48d6fe7364aa87328337a31007fe8a4f", size = 15164, upload-time = "2022-02-08T10:54:04.006Z" }\n'
    '+sdist = { url = "https://files.pythonhosted.org/packages/63/70/2bf7780ad2d390a8d301ad0b550f1581eadbd9a20f896afe06353c2a2913/requests-2.32.3.tar.gz", hash = "sha256:55365417734eb18255590a9ff9eb97e9e1da868d4ccd6402399eaf68af20a760", size = 131218, upload-time = "2024-05-29T15:37:49.536Z" }\n'
    ' wheels = [\n'
    '-    { url = "https://files.pythonhosted.org/packages/97/75/10a9ebee3fd790d20926a90a2547f0bf78f371b2f13aa822c759680ca7b9/tomli-2.0.1-py3-none-any.whl", hash = "sha256:939de3e7a6161af0c887ef91b7d41a53e7c5a1ca976325f429cb46ea9bc30ecc", size = 12757, upload-time = "2022-02-08T10:54:02.017Z" },\n'
    '+    { url = "https://files.pythonhosted.org/packages/f9/9b/335f9764261e915ed497fcdeb11df5dfd6f7bf257d4a6a2a686d80da4d54/requests-2.32.3-py3-none-any.whl", hash = "sha256:70761cfe03c773ceb22aa2f671b4757976145175cdfca038c02654d061d6dcc6", size = 64928, upload-time = "2024-05-29T15:37:47.027Z" },\n'
    ' ]\n'
    ' \n'
    ' [[package]]\n'
    ' name = "urllib3"\n'
    '-version = "2.0.7"\n'
    '+version = "2.2.3"\n'
    ' source = { registry = "https://pypi.org/simple" }\n'
    '-sdist = { url = "https://files.pythonhosted.org/packages/af/47/b215df9f71b4fdba1025fc05a77db2ad243fa0926755a52c5e71659f4e3c/urllib3-2.0.7.tar.gz", hash = "sha256:c97dfde1f7bd43a71c8d2a58e369e9b2bf692d1334ea9f9cae55add7d0dd0f84", size = 282546, upload-time = "2023-10-17T17:46:50.542Z" }\n'
    '+sdist = { url = "https://files.pythonhosted.org/packages/ed/63/22ba4ebfe7430b76388e7cd448d5478814d3032121827c12a2cc287e2260/urllib3-2.2.3.tar.gz", hash = "sha256:e7d814a81dad81e6caf2ec9fdedb284ecc9c73076b62654547cc64ccdcae26e9", size = 300677, upload-time = "2024-09-12T10:52:18.401Z" }\n'
    ' wheels = [\n'
    '-    { url = "https://files.pythonhosted.org/packages/d2/b2/b157855192a68541a91ba7b2bbcb91f1b4faa51f8bae38d8005c034be524/urllib3-2.0.7-py3-none-any.whl", hash = "sha256:fdb6d215c776278489906c2f8916e6e7d4f5a9b602ccbcfdf7f016fc8da0596e", size = 124213, upload-time = "2023-10-17T17:46:48.538Z" },\n'
    '+    { url = "https://files.pythonhosted.org/packages/ce/d9/5f4c13cecde62396b0d3fe530a50ccea91e7dfc1ccf0e09c228841bb5ba8/urllib3-2.2.3-py3-none-any.whl", hash = "sha256:ca899ca043dcb1bafa3e262d73aa25c465bfb49e0bd9dd5d59f1d0acba2f8fac", size = 126338, upload-time = "2024-09-12T10:52:16.589Z" },\n'
    ' ]\n'
    'diff --git a/README.md b/README.md\n'
    'index cd49312..d9f0227 100644\n'
    '--- a/README.md\n'
    '+++ b/README.md\n'
    '@@ -2,4 +2,4 @@\n'
    ' \n'
    ' Run `make test`.\n'
    ' \n'
    '-Supports requests 2.31.\n'
    '+Supports requests 2.32.\n'
    'diff --git a/svc/go.sum b/svc/go.sum\n'
    'index d9fabc0..207cc30 100644\n'
    '--- a/svc/go.sum\n'
    '+++ b/svc/go.sum\n'
    '@@ -1,8 +1,8 @@\n'
    '-github.com/pkg/errors v0.9.0 h1:J8lpUdobwIeCI7OiSxHqEwJUKvJwicL5+3v1oe2Yb4k=\n'
    '-github.com/pkg/errors v0.9.0/go.mod h1:bwawxfHBFNV+L2hUp1rHADufV3IMtnDRdf1r5NINEl0=\n'
    '-golang.org/x/text v0.13.0 h1:ablQoSUd0tRdKxZewP80B+BaqeKJuVhuRxj/dkrun3k=\n'
    '-golang.org/x/text v0.13.0/go.mod h1:TvPlkZtksWOMsz7fbANvkp4WM8x/WCo/om8BMLbz+aE=\n'
    '+github.com/pkg/errors v0.9.1 h1:FEBLx1zS214owpjy7qsBeixbURkuhQAwrK5UwLGTwt4=\n'
    '+github.com/pkg/errors v0.9.1/go.mod h1:bwawxfHBFNV+L2hUp1rHADufV3IMtnDRdf1r5NINEl0=\n'
    '+golang.org/x/text v0.19.0 h1:kTxAhCbGbxhK0IwgSKiMO5awPoDQ0RpfiVYBfK860YM=\n'
    '+golang.org/x/text v0.19.0/go.mod h1:BuEKDfySbSR4drPmRPG/7iBdf8hvFMuRexcpahXilzY=\n'
    ' gopkg.in/check.v1 v0.0.0-20161208181325-20d25e280405 h1:yhCVgyC4o1eVCa2tZl7eS0r+SDo693bJlVdllGtEeKM=\n'
    ' gopkg.in/check.v1 v0.0.0-20161208181325-20d25e280405/go.mod h1:Co6ibVJAznAaIkqp8huTwlJQCZ016jof/cbN4VW5Yz0=\n'
    '-gopkg.in/yaml.v3 v3.0.0 h1:hjy8E9ON/egN1tAYqKb61G10WtihqetD4sz2H+8nIeA=\n'
    '-gopkg.in/yaml.v3 v3.0.0/go.mod h1:K4uyk7z7BCEPqu6E+C64Yfv1cQ7kz7rIZviUmN+EgEM=\n'
    '+gopkg.in/yaml.v3 v3.0.1 h1:fxVm/GzAzEWqLHuvctI91KS9hhNmmWOoWu0XTYJS7CA=\n'
    '+gopkg.in/yaml.v3 v3.0.1/go.mod h1:K4uyk7z7BCEPqu6E+C64Yfv1cQ7kz7rIZviUmN+EgEM=\n'
)
LOCK_CARGO_DIFF = (
    'diff --git a/Cargo.lock b/Cargo.lock\n'
    'index 593706a..36eaf49 100644\n'
    '--- a/Cargo.lock\n'
    '+++ b/Cargo.lock\n'
    '@@ -2,26 +2,26 @@\n'
    ' # It is not intended for manual editing.\n'
    ' version = 4\n'
    ' \n'
    '-[[package]]\n'
    '-name = "anyhow"\n'
    '-version = "1.0.75"\n'
    '-source = "registry+https://github.com/rust-lang/crates.io-index"\n'
    '-checksum = "a4668cab20f66d8d020e1fbc0ebe47217433c1b6c8f2040faf858554e394ace6"\n'
    '-\n'
    ' [[package]]\n'
    ' name = "demo"\n'
    ' version = "0.1.0"\n'
    ' dependencies = [\n'
    '- "anyhow",\n'
    '  "itoa",\n'
    '+ "memchr",\n'
    '  "serde",\n'
    ' ]\n'
    ' \n'
    ' [[package]]\n'
    ' name = "itoa"\n'
    '-version = "1.0.9"\n'
    '+version = "1.0.14"\n'
    '+source = "registry+https://github.com/rust-lang/crates.io-index"\n'
    '+checksum = "d75a2a4b1b190afb6f5425f10f6a8f959d2ea0b9c2b1d79553551850539e4674"\n'
    '+\n'
    '+[[package]]\n'
    '+name = "memchr"\n'
    '+version = "2.7.4"\n'
    ' source = "registry+https://github.com/rust-lang/crates.io-index"\n'
    '-checksum = "af150ab688ff2122fcef229be89cb50dd66af9e01a4ff320cc137eecc9bacc38"\n'
    '+checksum = "78ca9ab1a0babb1e7d5695e3530886289c18cf2f87ec19a575a0abdce112e3a3"\n'
    ' \n'
    ' [[package]]\n'
    ' name = "proc-macro2"\n'
    '@@ -43,18 +43,18 @@ dependencies = [\n'
    ' \n'
    ' [[package]]\n'
    ' name = "serde"\n'
    '-version = "1.0.190"\n'
    '+version = "1.0.215"\n'
    ' source = "registry+https://github.com/rust-lang/crates.io-index"\n'
    '-checksum = "91d3c334ca1ee894a2c6f6ad698fe8c435b76d504b13d436f0685d648d6d96f7"\n'
    '+checksum = "6513c1ad0b11a9376da888e3e0baa0077f1aed55c17f50e7b2397136129fb88f"\n'
    ' dependencies = [\n'
    '  "serde_derive",\n'
    ' ]\n'
    ' \n'
    ' [[package]]\n'
    ' name = "serde_derive"\n'
    '-version = "1.0.190"\n'
    '+version = "1.0.215"\n'
    ' source = "registry+https://github.com/rust-lang/crates.io-index"\n'
    '-checksum = "67c5609f394e5c2bd7fc51efda478004ea80ef42fee983d5c67a65e34f32c0e3"\n'
    '+checksum = "ad1e866f866923f252f05c889987993144fb74e722403468a4ebd70c3cd756c0"\n'
    ' dependencies = [\n'
    '  "proc-macro2",\n'
    '  "quote",\n'
)
LOCK_YARN_DIFF = (
    'diff --git a/yarn.lock b/yarn.lock\n'
    'index 6ca8b83..a26de53 100644\n'
    '--- a/yarn.lock\n'
    '+++ b/yarn.lock\n'
    '@@ -2,38 +2,24 @@\n'
    ' # yarn lockfile v1\n'
    ' \n'
    ' \n'
    '-debug@4.3.4:\n'
    '-  version "4.3.4"\n'
    '-  resolved "https://registry.yarnpkg.com/debug/-/debug-4.3.4.tgz#1319f6579357f2338d3337d2cdd4914bb5dcc865"\n'
    '-  integrity sha512-PRWFHuSU3eDtQJPvnNY7Jcket1j0t5OuOsFzPPzsekD52Zl8qUfFIPEiswXqIvHWGVHOgX+7G/vCNNhehwxfkQ==\n'
    '+debug@4.3.7:\n'
    '+  version "4.3.7"\n'
    '+  resolved "https://registry.yarnpkg.com/debug/-/debug-4.3.7.tgz#87945b4151a011d76d95a198d7111c865c360a52"\n'
    '+  integrity sha512-Er2nc/H7RrMXZBFCEim6TCmMk02Z8vLC2Rbi1KEBggpo0fS6l0S1nnapwmIi3yW/+GOJap1Krg4w0Hg80oCqgQ==\n'
    '   dependencies:\n'
    '-    ms "2.1.2"\n'
    '+    ms "^2.1.3"\n'
    ' \n'
    '-lodash@4.17.20:\n'
    '-  version "4.17.20"\n'
    '-  resolved "https://registry.yarnpkg.com/lodash/-/lodash-4.17.20.tgz#b44a9b6297bcb698f1c51a3545a2b3b368d59c52"\n'
    '-  integrity sha512-PlhdFcillOINfeV7Ni6oF1TAEayyZBoZ8bcshTHqOYJYlrqzRK5hagpagky5o4HfCzzd1TRkXPMFq6cKk9rGmA==\n'
    '+lodash@4.17.21:\n'
    '+  version "4.17.21"\n'
    '+  resolved "https://registry.yarnpkg.com/lodash/-/lodash-4.17.21.tgz#679591c564c3bffaae8454cf0b3df370c3d6911c"\n'
    '+  integrity sha512-v2kDEe57lecTulaDIuNTPy3Ry4gLGJ6Z1O3vE1krgXZNrsQ+LFTGHVxVjcXPs17LhbZVGedAJv8XZ1tvj5FvSg==\n'
    ' \n'
    '-lru-cache@^6.0.0:\n'
    '-  version "6.0.0"\n'
    '-  resolved "https://registry.yarnpkg.com/lru-cache/-/lru-cache-6.0.0.tgz#6d6fe6570ebd96aaf90fcad1dafa3b2566db3a94"\n'
    '-  integrity sha512-Jo6dJ04CmSjuznwJSS3pUeWmd/H0ffTlkXXgwZi+eq1UCmqQwCh+eLsYOYCwY991i2Fah4h1BEMCx4qThGbsiA==\n'
    '-  dependencies:\n'
    '-    yallist "^4.0.0"\n'
    '-\n'
    '-ms@2.1.2:\n'
    '-  version "2.1.2"\n'
    '-  resolved "https://registry.yarnpkg.com/ms/-/ms-2.1.2.tgz#d09d1f357b443f493382a8eb3ccd183872ae6009"\n'
    '-  integrity sha512-sGkPx+VjMtmA6MX27oA4FBFELFCZZ4S4XqeGOXCv68tT+jb3vk/RyaKWP0PTKyWtmLSM0b+adUTEvbs1PEaH2w==\n'
    '-\n'
    '-semver@7.5.4:\n'
    '-  version "7.5.4"\n'
    '-  resolved "https://registry.yarnpkg.com/semver/-/semver-7.5.4.tgz#483986ec4ed38e1c6c48c34894a9182dbff68a6e"\n'
    '-  integrity sha512-1bCSESV6Pv+i21Hvpxp3Dx+pSD8lIPt8uVjRrxAUt/nbswYc+tK6Y2btiULjd4+fnq15PX+nqQDC7Oft7WkwcA==\n'
    '-  dependencies:\n'
    '-    lru-cache "^6.0.0"\n'
    '+ms@2.1.3, ms@^2.1.3:\n'
    '+  version "2.1.3"\n'
    '+  resolved "https://registry.yarnpkg.com/ms/-/ms-2.1.3.tgz#574c8138ce1d2b5861f0b44579dbadd60c6615b2"\n'
    '+  integrity sha512-6FlzubTLZG3J2a/NVCAleEhjzq5oxgHyaCU9yYXvcLsvoVaHJq/s5xXI6/XXP6tz7R9xAOtHnSO/tXtF3WRTlA==\n'
    ' \n'
    '-yallist@^4.0.0:\n'
    '-  version "4.0.0"\n'
    '-  resolved "https://registry.yarnpkg.com/yallist/-/yallist-4.0.0.tgz#9bb92790d9c0effec63be73519e11a35019a3a72"\n'
    '-  integrity sha512-3wdGidZyq5PB084XLES5TpOSRA3wjXAlIWMhum2kRcv/41Sn2emQ0dycQW4uZXLejwKvg6EsvbdlVL+FYEct7A==\n'
    '+semver@7.6.3:\n'
    '+  version "7.6.3"\n'
    '+  resolved "https://registry.yarnpkg.com/semver/-/semver-7.6.3.tgz#980f7b5550bc175fb4dc09403085627f9eb33143"\n'
    '+  integrity sha512-oVekP1cKtI+CTDvHWYFUcMtsK/00wmAEfyqKfNdARm8u1wNVhSgaX7A8d4UuIlUI5e84iEwOhs7ZPYRmzU9U6A==\n'
)
SRC_ONLY_DIFF = (
    'diff --git a/README.md b/README.md\n'
    'index cd49312..d9f0227 100644\n'
    '--- a/README.md\n'
    '+++ b/README.md\n'
    '@@ -2,4 +2,4 @@\n'
    ' \n'
    ' Run `make test`.\n'
    ' \n'
    '-Supports requests 2.31.\n'
    '+Supports requests 2.32.\n'
    'diff --git a/src/app.py b/src/app.py\n'
    'index faf33ee..6bd30ab 100644\n'
    '--- a/src/app.py\n'
    '+++ b/src/app.py\n'
    '@@ -3,10 +3,10 @@ import requests\n'
    ' API = "https://api.example.com"\n'
    ' \n'
    ' \n'
    '-def fetch(path, retries=2):\n'
    '+def fetch(path, retries=3):\n'
    '     url = f"{API}/{path}"\n'
    '     for attempt in range(retries):\n'
    '-        r = requests.get(url, timeout=5)\n'
    '+        r = requests.get(url, timeout=10)\n'
    '         if r.ok:\n'
    '             return r.json()\n'
    '-    raise RuntimeError(f"failed: {url}")\n'
    '+    raise RuntimeError(f"failed after {retries} attempts: {url}")\n'
)

LOCKFILE_KIND = {"package-lock.json": "npm", "yarn.lock": "yarn", "pnpm-lock.yaml": "pnpm", "Cargo.lock": "toml", "uv.lock": "toml", "go.sum": "gosum"}
_SEC_HDR = re.compile(r"^diff --git a/(.+?) b/(.+)$")
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@")


def _lock_kind(header: str):
    m = _SEC_HDR.match(header)
    return LOCKFILE_KIND.get(m.group(2).rsplit("/", 1)[-1]) if m else None


def _split_sections(text: str) -> list[list[str]]:
    secs: list[list[str]] = [[]]
    for l in text.split("\n"):
        if l.startswith("diff --git ") and secs[-1]:
            secs.append([])
        secs[-1].append(l)
    return secs


def _first_hunk(sec: list[str]):
    return next((i for i, l in enumerate(sec) if _HUNK.match(l)), None)


def _sides(hunk_lines: list[str]):
    old: list = []
    new: list = []
    for l in hunk_lines:
        if _HUNK.match(l):
            old.append(None)  # None breaks adjacency between hunks
            new.append(None)
        elif l == "" or l[0] == " ":
            old.append(l[1:])
            new.append(l[1:])
        elif l[0] == "-":
            old.append(l[1:])
        elif l[0] == "+":
            new.append(l[1:])
    return old, new


def _pairs(kind: str, side: list) -> set:
    """Line scanner: (name, version) pairs of one side of the diff."""
    out = set()
    prev = None  # the previous line's parsed name, if it was a name line
    for l in side:
        if l is None:
            prev = None
            continue
        if kind == "toml":
            if prev is not None and (m := re.fullmatch(r'version = "([^"]*)"', l)):
                out.add((prev, m.group(1)))
            m = re.fullmatch(r'name = "([^"]*)"', l)
            prev = m.group(1) if m else None
        elif kind == "npm":
            if prev is not None and (m := re.fullmatch(r'\s*"version": "([^"]*)",?', l)):
                out.add((prev, m.group(1)))
            m = re.fullmatch(r'\s*"([^"]*)": \{', l)
            prev = m.group(1).rsplit("node_modules/", 1)[-1] if m and m.group(1) else None
        elif kind == "yarn":
            if prev is not None and (m := re.fullmatch(r'  version "([^"]*)"', l)):
                out.add((prev, m.group(1)))
            m = re.fullmatch(r'"?(@?[^@"\s,]+)@[^\s,]*"?(?:, [^\n]*)?:', l)
            prev = m.group(1) if m else None
        elif kind == "pnpm":
            if m := re.fullmatch(r"  '?(@?[^@\s']+)@([^:'()\s]+)[^:']*'?:", l):
                out.add((m.group(1), m.group(2)))
        elif kind == "gosum":
            if m := re.fullmatch(r"(\S+) (v\S+?)(?:/go\.mod)? h1:\S+", l):
                out.add((m.group(1), m.group(2)))
    return out


def _digest_lines(removed: list, added: list) -> list[str]:
    rn, an = {}, {}
    for n, v in removed:
        rn.setdefault(n, []).append(v)
    for n, v in added:
        an.setdefault(n, []).append(v)
    out, changed, plus, minus = [], 0, 0, 0
    for n in sorted(set(rn) | set(an)):
        ro, ad = sorted(rn.get(n, [])), sorted(an.get(n, []))
        for o, w in zip(ro, ad):
            out.append(f"{n} {o}->{w}")
            changed += 1
        for o in ro[len(ad):]:
            out.append(f"- {n} {o}")
            minus += 1
        for w in ad[len(ro):]:
            out.append(f"+ {n} {w}")
            plus += 1
    return [f"lockfile digest ({changed} changed, {plus} added, {minus} removed; hashes, URLs and dependency lists elided)"] + out


def lock_digest(text: str) -> str:
    secs = _split_sections(text)
    for k, sec in enumerate(secs):
        kind = _lock_kind(sec[0])
        h = _first_hunk(sec)
        if kind is None or h is None:
            continue
        old, new = _sides(sec[h:])
        po, pn = _pairs(kind, old), _pairs(kind, new)
        digest = [sec[0]] + _digest_lines(sorted(po - pn), sorted(pn - po))
        if len("\n".join(digest)) < len("\n".join(sec)) - 1:
            secs[k] = digest + ([""] if sec[-1] == "" else [])
    return shrink_only(text, "\n".join(l for sec in secs for l in sec))


def probes_121(text: str) -> dict:
    """Independent reader: per section, rebuild both sides as text and apply a multi-line regex per format."""
    pairs_by_file, headers, other = {}, [], []
    for sec in _split_sections(text):
        m = _SEC_HDR.match(sec[0])
        if m:
            headers.append(sec[0])
        kind = _lock_kind(sec[0])
        h = _first_hunk(sec)
        if kind and h is not None:
            blob = {"-": "", "+": ""}
            for l in sec[h:]:
                if _HUNK.match(l):
                    for s in blob:
                        blob[s] += "\n\x00\n"
                elif l == "" or l[0] == " ":
                    for s in blob:
                        blob[s] += l[1:] + "\n"
                elif l[0] in blob:
                    blob[l[0]] += l[1:] + "\n"
            rx = {
                "toml": r'(?m)^name = "([^"]*)"\nversion = "([^"]*)"$',
                "npm": r'(?m)^\s*"([^"]*)": \{\n\s*"version": "([^"]*)",?$',
                "yarn": r'(?m)^"?(@?[^@"\s,]+)@[^\s,]*"?(?:, [^\n]*)?:\n  version "([^"]*)"$',
                "pnpm": r"(?m)^  '?(@?[^@\s']+)@([^:'()\s]+)[^:'\n]*'?:$",
                "gosum": r"(?m)^(\S+) (v\S+?)(?:/go\.mod)? h1:\S+$",
            }[kind]
            def found(s):
                out = set()
                for a, b in re.findall(rx, blob[s]):
                    if kind == "npm":
                        a = a.rsplit("node_modules/", 1)[-1]
                        if not a:
                            continue
                    out.add((a, b))
                return out
            old, new = found("-"), found("+")
            pairs_by_file[sec[0]] = (sorted(old - new), sorted(new - old))
        elif m and len(sec) > 1 and sec[1].startswith("lockfile digest ("):
            rem, add = [], []
            for l in sec[2:]:
                if a := re.fullmatch(r"(\S+) (\S+)->(\S+)", l):
                    rem.append((a[1], a[2]))
                    add.append((a[1], a[3]))
                elif a := re.fullmatch(r"\+ (\S+) (\S+)", l):
                    add.append((a[1], a[2]))
                elif a := re.fullmatch(r"- (\S+) (\S+)", l):
                    rem.append((a[1], a[2]))
            pairs_by_file[sec[0]] = (sorted(rem), sorted(add))
        else:
            other.append("\n".join(sec))
    return {"name/version pairs removed and added per lockfile": sorted(pairs_by_file.items()), "files in the diff": headers,
            "all other diff text, verbatim": other}


case(
    121,
    claim="In a git diff, the hunks of package-lock.json, yarn.lock, pnpm-lock.yaml, Cargo.lock, uv.lock and go.sum become a header plus 'name old->new' / '+ name ver' / '- name ver' lines; every (name, version) pair that left or entered the lockfile survives, and every non-lockfile diff stays byte-identical.",
    loses="integrity hashes, resolved URLs, file sizes and upload times, dependency lists and specifier ranges (including the 'requires-dist' / root 'dependencies' lines that record a changed constraint), engines/license/bin fields, and the context lines of the hunk",
    encode=lock_digest,
    probes=probes_121,
    fixtures=lambda: [
        Fixture("monorepo commit: npm first, pnpm, source, uv.lock, README, go.sum last (real diffs)", LOCK_COMMIT_DIFF, True),
        Fixture("Cargo.lock: bump, add and remove in one hunk (real)", LOCK_CARGO_DIFF, True),
        Fixture("yarn.lock v1 with multi-key headers 'ms@2.1.3, ms@^2.1.3:' (real)", LOCK_YARN_DIFF, True),
        Fixture("source-only diff must stay verbatim", SRC_ONLY_DIFF, False),
        Fixture("lockfile content, not a diff", "[[package]]\nname = \"serde\"\nversion = \"1.0.215\"\nchecksum = \"6513c1ad\"\n", False),
        Fixture("lockfile diff with only a checksum change and no version move is still digested",
                'diff --git a/Cargo.lock b/Cargo.lock\nindex 1..2 100644\n--- a/Cargo.lock\n+++ b/Cargo.lock\n@@ -5,5 +5,5 @@\n [[package]]\n name = "itoa"\n version = "1.0.14"\n source = "registry+https://github.com/rust-lang/crates.io-index"\n-checksum = "d75a2a4b1b190afb6f5425f10f6a8f959d2ea0b9c2b1d79553551850539e4674"\n+checksum = "d75a2a4b1b190afb6f5425f10f6a8f959d2ea0b9c2b1d79553551850539e4675"\n', None),
        Fixture("source diff that merely contains digest-looking lines", "diff --git a/NOTES.md b/NOTES.md\n--- a/NOTES.md\n+++ b/NOTES.md\n@@ -1,2 +1,3 @@\n lockfile digest (1 changed, 0 added, 0 removed)\n+attrs 23.1.0->24.2.0\n", False),
    ],
    min_gain=0.5,
)



# ===============================================================================================================
# #1325 IPython / nbconvert / PySpark failures: strip ANSI, fold runs of library frames (site-packages, Cython .pyx,
# pyspark/py4j) into one count line, fold repeated Python tracebacks and runs of Java 'at' frames. Keep the failing
# cell source, user frames (Cell In[N], project files) with their pointer line, and every exception line.
# RULE: the probe reads (after stripping ANSI) the distinct exception lines, the user frames with their source
# pointer, the failing cell's source, and that library frames and Java frames are accounted for (kept + folded).
# Both fixtures are real: nbconvert --execute on a notebook with a pandas KeyError, and a local PySpark job whose
# UDF raises ValueError (pyspark 4.3, pandas 3.1, ipykernel 7.4).

NBCONVERT_KEYERROR = (
    '[NbConvertApp] Converting notebook nb.ipynb to notebook\n'
    '[IPKernelApp] WARNING | Kernel is running over TCP without encryption. All communication (including code and outputs) is sent in plain text and is susceptible to eavesdropping. Use IPC transport or launch with kernel manager-provisioned CurveZMQ keys to enable transport encryption.\n'
    'Traceback (most recent call last):\n'
    '  File "/home/dev/.venv/bin/jupyter-nbconvert", line 8, in <module>\n'
    '    sys.exit(main())\n'
    '             ~~~~^^\n'
    '  File "/home/dev/.venv/lib/python3.13/site-packages/jupyter_core/application.py", line 284, in launch_instance\n'
    '    super().launch_instance(argv=argv, **kwargs)\n'
    '    ~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^\n'
    '  File "/home/dev/.venv/lib/python3.13/site-packages/traitlets/config/application.py", line 1080, in launch_instance\n'
    '    app.start()\n'
    '    ~~~~~~~~~^^\n'
    '  File "/home/dev/.venv/lib/python3.13/site-packages/nbconvert/nbconvertapp.py", line 420, in start\n'
    '    self.convert_notebooks()\n'
    '    ~~~~~~~~~~~~~~~~~~~~~~^^\n'
    '  File "/home/dev/.venv/lib/python3.13/site-packages/nbconvert/nbconvertapp.py", line 597, in convert_notebooks\n'
    '    self.convert_single_notebook(notebook_filename)\n'
    '    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^\n'
    '  File "/home/dev/.venv/lib/python3.13/site-packages/nbconvert/nbconvertapp.py", line 563, in convert_single_notebook\n'
    '    output, resources = self.export_single_notebook(\n'
    '                        ~~~~~~~~~~~~~~~~~~~~~~~~~~~^\n'
    '        notebook_filename, resources, input_buffer=input_buffer\n'
    '        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n'
    '    )\n'
    '    ^\n'
    '  File "/home/dev/.venv/lib/python3.13/site-packages/nbconvert/nbconvertapp.py", line 487, in export_single_notebook\n'
    '    output, resources = self.exporter.from_filename(\n'
    '                        ~~~~~~~~~~~~~~~~~~~~~~~~~~~^\n'
    '        notebook_filename, resources=resources\n'
    '        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n'
    '    )\n'
    '    ^\n'
    '  File "/home/dev/.venv/lib/python3.13/site-packages/nbconvert/exporters/exporter.py", line 201, in from_filename\n'
    '    return self.from_file(f, resources=resources, **kw)\n'
    '           ~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n'
    '  File "/home/dev/.venv/lib/python3.13/site-packages/nbconvert/exporters/exporter.py", line 220, in from_file\n'
    '    return self.from_notebook_node(\n'
    '           ~~~~~~~~~~~~~~~~~~~~~~~^\n'
    '        nbformat.read(file_stream, as_version=4), resources=resources, **kw\n'
    '        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n'
    '    )\n'
    '    ^\n'
    '  File "/home/dev/.venv/lib/python3.13/site-packages/nbconvert/exporters/notebook.py", line 36, in from_notebook_node\n'
    '    nb_copy, resources = super().from_notebook_node(nb, resources, **kw)\n'
    '                         ~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^\n'
    '  File "/home/dev/.venv/lib/python3.13/site-packages/nbconvert/exporters/exporter.py", line 154, in from_notebook_node\n'
    '    nb_copy, resources = self._preprocess(nb_copy, resources)\n'
    '                         ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^\n'
    '  File "/home/dev/.venv/lib/python3.13/site-packages/nbconvert/exporters/exporter.py", line 353, in _preprocess\n'
    '    nbc, resc = preprocessor(nbc, resc)\n'
    '                ~~~~~~~~~~~~^^^^^^^^^^^\n'
    '  File "/home/dev/.venv/lib/python3.13/site-packages/nbconvert/preprocessors/base.py", line 48, in __call__\n'
    '    return self.preprocess(nb, resources)\n'
    '           ~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^\n'
    '  File "/home/dev/.venv/lib/python3.13/site-packages/nbconvert/preprocessors/execute.py", line 103, in preprocess\n'
    '    self.preprocess_cell(cell, resources, index)\n'
    '    ~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^\n'
    '  File "/home/dev/.venv/lib/python3.13/site-packages/nbconvert/preprocessors/execute.py", line 124, in preprocess_cell\n'
    '    cell = self.execute_cell(cell, index, store_history=True)\n'
    '  File "/home/dev/.venv/lib/python3.13/site-packages/jupyter_core/utils/__init__.py", line 165, in wrapped\n'
    '    return loop.run_until_complete(inner)\n'
    '           ~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^\n'
    '  File "/usr/local/lib/python3.13/asyncio/base_events.py", line 726, in run_until_complete\n'
    '    return future.result()\n'
    '           ~~~~~~~~~~~~~^^\n'
    '  File "/home/dev/.venv/lib/python3.13/site-packages/nbclient/client.py", line 1062, in async_execute_cell\n'
    '    await self._check_raise_for_error(cell, cell_index, exec_reply)\n'
    '  File "/home/dev/.venv/lib/python3.13/site-packages/nbclient/client.py", line 918, in _check_raise_for_error\n'
    '    raise CellExecutionError.from_cell_and_msg(cell, exec_reply_content)\n'
    'nbclient.exceptions.CellExecutionError: An error occurred while executing the following cell:\n'
    '------------------\n'
    'def revenue(frame):\n'
    "    return frame['price'] * frame['amount']\n"
    '\n'
    'total = revenue(df).sum()\n'
    'total\n'
    '------------------\n'
    '\n'
    '\n'
    '\x1b[31m---------------------------------------------------------------------------\x1b[39m\n'
    '\x1b[31mKeyError\x1b[39m                                  Traceback (most recent call last)\n'
    '\x1b[36mFile \x1b[39m\x1b[32m/home/dev/.venv/lib/python3.13/site-packages/pandas/core/indexes/base.py:3816\x1b[39m, in \x1b[36mIndex.get_loc\x1b[39m\x1b[34m(self, key)\x1b[39m\n'
    '\x1b[32m   3815\x1b[39m \x1b[38;5;28;01mtry\x1b[39;00m:\n'
    '\x1b[32m-> \x1b[39m\x1b[32m3816\x1b[39m     \x1b[38;5;28;01mreturn\x1b[39;00m \x1b[30;43mself\x1b[39;49m\x1b[30;43m.\x1b[39;49m\x1b[30;43m_engine\x1b[39;49m\x1b[30;43m.\x1b[39;49m\x1b[30;43mget_loc\x1b[39;49m\x1b[30;43m(\x1b[39;49m\x1b[30;43mcasted_key\x1b[39;49m\x1b[30;43m)\x1b[39;49m\n'
    '\x1b[32m   3817\x1b[39m \x1b[38;5;28;01mexcept\x1b[39;00m \x1b[38;5;167;01mKeyError\x1b[39;00m \x1b[38;5;28;01mas\x1b[39;00m err:\n'
    '\n'
    '\x1b[36mFile \x1b[39m\x1b[32mpandas/_libs/index.pyx:172\x1b[39m, in \x1b[36mpandas._libs.index.IndexEngine.get_loc\x1b[39m\x1b[34m()\x1b[39m\n'
    "\x1b[32m--> \x1b[39m\x1b[32m172\x1b[39m \x1b[33m'Could not get source, probably due dynamically evaluated source code.'\x1b[39m\n"
    '\n'
    '\x1b[36mFile \x1b[39m\x1b[32mpandas/_libs/index.pyx:201\x1b[39m, in \x1b[36mpandas._libs.index.IndexEngine.get_loc\x1b[39m\x1b[34m()\x1b[39m\n'
    "\x1b[32m--> \x1b[39m\x1b[32m201\x1b[39m \x1b[33m'Could not get source, probably due dynamically evaluated source code.'\x1b[39m\n"
    '\n'
    '\x1b[36mFile \x1b[39m\x1b[32mpandas/_libs/hashtable_class_helper.pxi:7726\x1b[39m, in \x1b[36mpandas._libs.hashtable.PyObjectHashTable.get_item\x1b[39m\x1b[34m()\x1b[39m\n'
    "\x1b[32m-> \x1b[39m\x1b[32m7726\x1b[39m \x1b[33m'Could not get source, probably due dynamically evaluated source code.'\x1b[39m\n"
    '\n'
    '\x1b[36mFile \x1b[39m\x1b[32mpandas/_libs/hashtable_class_helper.pxi:7734\x1b[39m, in \x1b[36mpandas._libs.hashtable.PyObjectHashTable.get_item\x1b[39m\x1b[34m()\x1b[39m\n'
    "\x1b[32m-> \x1b[39m\x1b[32m7734\x1b[39m \x1b[33m'Could not get source, probably due dynamically evaluated source code.'\x1b[39m\n"
    '\n'
    "\x1b[31mKeyError\x1b[39m: 'amount'\n"
    '\n'
    'The above exception was the direct cause of the following exception:\n'
    '\n'
    '\x1b[31mKeyError\x1b[39m                                  Traceback (most recent call last)\n'
    '\x1b[36mCell\x1b[39m\x1b[36m \x1b[39m\x1b[32mIn[3]\x1b[39m\x1b[32m, line 4\x1b[39m\n'
    '\x1b[32m      1\x1b[39m \x1b[38;5;28;01mdef\x1b[39;00m revenue(frame):\n'
    "\x1b[32m      2\x1b[39m     \x1b[38;5;28;01mreturn\x1b[39;00m frame[\x1b[33m'price'\x1b[39m] * frame[\x1b[33m'amount'\x1b[39m]\n"
    '\x1b[32m      3\x1b[39m \n'
    '\x1b[32m----> \x1b[39m\x1b[32m4\x1b[39m total = revenue(df).sum()\n'
    '\x1b[32m      5\x1b[39m total\n'
    '\n'
    '\x1b[36mCell\x1b[39m\x1b[36m \x1b[39m\x1b[32mIn[3]\x1b[39m\x1b[32m, line 2\x1b[39m, in \x1b[36mrevenue\x1b[39m\x1b[34m(frame)\x1b[39m\n'
    '\x1b[32m      1\x1b[39m \x1b[38;5;28;01mdef\x1b[39;00m revenue(frame):\n'
    "\x1b[32m----> \x1b[39m\x1b[32m2\x1b[39m     \x1b[38;5;28;01mreturn\x1b[39;00m frame[\x1b[33m'price'\x1b[39m] * frame[\x1b[33m'amount'\x1b[39m]\n"
    '\n'
    '\x1b[36mFile \x1b[39m\x1b[32m/home/dev/.venv/lib/python3.13/site-packages/pandas/core/frame.py:4330\x1b[39m, in \x1b[36mDataFrame.__getitem__\x1b[39m\x1b[34m(self, key)\x1b[39m\n'
    '\x1b[32m   4326\x1b[39m \n'
    '\x1b[32m   4327\x1b[39m         \x1b[38;5;28;01mif\x1b[39;00m is_single_key:\n'
    '\x1b[32m   4328\x1b[39m             \x1b[38;5;28;01mif\x1b[39;00m self.columns.nlevels > \x1b[32m1\x1b[39m:\n'
    '\x1b[32m   4329\x1b[39m                 \x1b[38;5;28;01mreturn\x1b[39;00m self._getitem_multilevel(key)\n'
    '\x1b[32m-> \x1b[39m\x1b[32m4330\x1b[39m             indexer = self.columns.get_loc(key)\n'
    '\x1b[32m   4331\x1b[39m             \x1b[38;5;28;01mif\x1b[39;00m is_integer(indexer):\n'
    '\x1b[32m   4332\x1b[39m                 indexer = [indexer]  \x1b[38;5;66;03m# type: ignore[assignment]\x1b[39;00m\n'
    '\x1b[32m   4333\x1b[39m         \x1b[38;5;28;01melse\x1b[39;00m:\n'
    '\n'
    '\x1b[36mFile \x1b[39m\x1b[32m/home/dev/.venv/lib/python3.13/site-packages/pandas/core/indexes/base.py:3823\x1b[39m, in \x1b[36mIndex.get_loc\x1b[39m\x1b[34m(self, key)\x1b[39m\n'
    '\x1b[32m   3818\x1b[39m     \x1b[38;5;28;01mif\x1b[39;00m \x1b[38;5;28misinstance\x1b[39m(casted_key, \x1b[38;5;28mslice\x1b[39m) \x1b[38;5;129;01mor\x1b[39;00m (\n'
    '\x1b[32m   3819\x1b[39m         \x1b[38;5;28misinstance\x1b[39m(casted_key, abc.Iterable)\n'
    '\x1b[32m   3820\x1b[39m         \x1b[38;5;129;01mand\x1b[39;00m \x1b[38;5;28many\x1b[39m(\x1b[38;5;28misinstance\x1b[39m(x, \x1b[38;5;28mslice\x1b[39m) \x1b[38;5;28;01mfor\x1b[39;00m x \x1b[38;5;129;01min\x1b[39;00m casted_key)\n'
    '\x1b[32m   3821\x1b[39m     ):\n'
    '\x1b[32m   3822\x1b[39m         \x1b[38;5;28;01mraise\x1b[39;00m InvalidIndexError(key) \x1b[38;5;28;01mfrom\x1b[39;00m\x1b[38;5;250m \x1b[39m\x1b[34;01merr\x1b[39;00m\n'
    '\x1b[32m-> \x1b[39m\x1b[32m3823\x1b[39m     \x1b[38;5;28;01mraise\x1b[39;00m \x1b[38;5;167;01mKeyError\x1b[39;00m(key) \x1b[38;5;28;01mfrom\x1b[39;00m\x1b[38;5;250m \x1b[39m\x1b[34;01merr\x1b[39;00m\n'
    '\x1b[32m   3824\x1b[39m \x1b[38;5;28;01mexcept\x1b[39;00m \x1b[38;5;167;01mTypeError\x1b[39;00m:\n'
    '\x1b[32m   3825\x1b[39m     \x1b[38;5;66;03m# If we have a listlike key, _check_indexing_error will raise\x1b[39;00m\n'
    '\x1b[32m   3826\x1b[39m     \x1b[38;5;66;03m#  InvalidIndexError. Otherwise we fall through and re-raise\x1b[39;00m\n'
    '\x1b[32m   3827\x1b[39m     \x1b[38;5;66;03m#  the TypeError.\x1b[39;00m\n'
    '\x1b[32m   3828\x1b[39m     \x1b[38;5;28mself\x1b[39m._check_indexing_error(key)\n'
    '\n'
    "\x1b[31mKeyError\x1b[39m: 'amount'\n"
    '\n'
)
PYSPARK_UDF_ERROR = (
    'WARNING: Using incubator modules: jdk.incubator.vector\n'
    "Using Spark's default log4j profile: org/apache/spark/log4j2-defaults.properties\n"
    '26/11/04 17:40:50 WARN Utils: Your hostname, vm, resolves to a loopback address: 127.0.0.1; using 192.0.2.2 instead (on interface eth0)\n'
    '26/11/04 17:40:50 WARN Utils: Set SPARK_LOCAL_IP if you need to bind to another address\n'
    "Using Spark's default log4j profile: org/apache/spark/log4j2-defaults.properties\n"
    'Setting default log level to "WARN".\n'
    'To adjust logging level use sc.setLogLevel(newLevel). For SparkR, use setLogLevel(newLevel).\n'
    '/home/dev/.venv/lib/python3.13/site-packages/pyspark/testing/utils.py:127: FutureWarning: PySpark does not yet fully support pandas >= 3.0.0. Some features may not work correctly. It is recommended to use pandas < 3.0.0 for now.\n'
    '  require_minimum_pandas_version()\n'
    '26/11/04 17:40:51 WARN NativeCodeLoader: Unable to load native-hadoop library for your platform... using builtin-java classes where applicable\n'
    '/home/dev/.venv/lib/python3.13/site-packages/pyspark/sql/udf.py:116: FutureWarning: PySpark does not yet fully support pandas >= 3.0.0. Some features may not work correctly. It is recommended to use pandas < 3.0.0 for now.\n'
    '  require_minimum_pandas_version()\n'
    '/home/dev/.venv/lib/python3.13/site-packages/pyspark/sql/udf.py:120: RuntimeWarning: Arrow optimization failed to enable because PyArrow or Pandas is not installed. Falling back to a non-Arrow-optimized UDF.\n'
    '  warnings.warn(\n'
    '\r'
    '[Stage 0:>                                                          (0 + 1) / 1]\r'
    '26/11/04 17:41:00 ERROR Executor: Exception in task 0.0 in stage 0.0 (TID 0)\n'
    'org.apache.spark.api.python.PythonException: [PYTHON_EXCEPTION] An exception was thrown from the Python worker: Traceback (most recent call last):\n'
    '  File "/work/app/job.py", line 9, in parse_qty\n'
    '    return int(raw)\n'
    "ValueError: invalid literal for int() with base 10: 'x3'\n"
    ' SQLSTATE: 38000\n'
    '\tat org.apache.spark.api.python.BasePythonRunner$ReaderIterator.handlePythonException(PythonRunner.scala:1097)\n'
    '\tat org.apache.spark.sql.execution.python.BasePythonUDFRunner$$anon$2.read(PythonUDFRunner.scala:133)\n'
    '\tat org.apache.spark.sql.execution.python.BasePythonUDFRunner$$anon$2.read(PythonUDFRunner.scala:114)\n'
    '\tat org.apache.spark.api.python.BasePythonRunner$ReaderIterator.hasNext(PythonRunner.scala:1034)\n'
    '\tat org.apache.spark.InterruptibleIterator.hasNext(InterruptibleIterator.scala:37)\n'
    '\tat scala.collection.Iterator$$anon$10.hasNext(Iterator.scala:611)\n'
    '\tat scala.collection.Iterator$$anon$9.hasNext(Iterator.scala:593)\n'
    '\tat scala.collection.Iterator$$anon$9.hasNext(Iterator.scala:593)\n'
    '\tat org.apache.spark.sql.catalyst.expressions.GeneratedClass$GeneratedIteratorForCodegenStage2.processNext(Unknown Source)\n'
    '\tat org.apache.spark.sql.execution.BufferedRowIterator.hasNext(BufferedRowIterator.java:44)\n'
    '\tat org.apache.spark.sql.execution.WholeStageCodegenEvaluatorFactory$WholeStageCodegenPartitionEvaluator$$anon$1.hasNext(WholeStageCodegenEvaluatorFactory.scala:50)\n'
    '\tat org.apache.spark.sql.execution.SparkPlan.$anonfun$getByteArrayRdd$2(SparkPlan.scala:418)\n'
    '\tat org.apache.spark.rdd.RDD.$anonfun$mapPartitionsInternal$2(RDD.scala:960)\n'
    '\tat org.apache.spark.rdd.RDD.$anonfun$mapPartitionsInternal$2$adapted(RDD.scala:960)\n'
    '\tat org.apache.spark.rdd.MapPartitionsRDD.compute(MapPartitionsRDD.scala:57)\n'
    '\tat org.apache.spark.rdd.RDD.computeOrReadCheckpoint(RDD.scala:374)\n'
    '\tat org.apache.spark.rdd.RDD.iterator(RDD.scala:338)\n'
    '\tat org.apache.spark.scheduler.ResultTask.runTask(ResultTask.scala:93)\n'
    '\tat org.apache.spark.TaskContext.runTaskWithListeners(TaskContext.scala:240)\n'
    '\tat org.apache.spark.scheduler.Task.run(Task.scala:147)\n'
    '\tat org.apache.spark.executor.Executor$TaskRunner.$anonfun$run$4(Executor.scala:903)\n'
    '\tat org.apache.spark.util.SparkErrorUtils.tryWithSafeFinally(SparkErrorUtils.scala:86)\n'
    '\tat org.apache.spark.util.SparkErrorUtils.tryWithSafeFinally$(SparkErrorUtils.scala:83)\n'
    '\tat org.apache.spark.util.Utils$.tryWithSafeFinally(Utils.scala:97)\n'
    '\tat org.apache.spark.executor.Executor$TaskRunner.run(Executor.scala:906)\n'
    '\tat java.base/java.util.concurrent.ThreadPoolExecutor.runWorker(ThreadPoolExecutor.java:1144)\n'
    '\tat java.base/java.util.concurrent.ThreadPoolExecutor$Worker.run(ThreadPoolExecutor.java:642)\n'
    '\tat java.base/java.lang.Thread.run(Thread.java:1583)\n'
    '26/11/04 17:41:00 WARN TaskSetManager: Lost task 0.0 in stage 0.0 (TID 0) (192.0.2.2 executor driver): org.apache.spark.api.python.PythonException: [PYTHON_EXCEPTION] An exception was thrown from the Python worker: Traceback (most recent call last):\n'
    '  File "/work/app/job.py", line 9, in parse_qty\n'
    '    return int(raw)\n'
    "ValueError: invalid literal for int() with base 10: 'x3'\n"
    ' SQLSTATE: 38000\n'
    '\tat org.apache.spark.api.python.BasePythonRunner$ReaderIterator.handlePythonException(PythonRunner.scala:1097)\n'
    '\tat org.apache.spark.sql.execution.python.BasePythonUDFRunner$$anon$2.read(PythonUDFRunner.scala:133)\n'
    '\tat org.apache.spark.sql.execution.python.BasePythonUDFRunner$$anon$2.read(PythonUDFRunner.scala:114)\n'
    '\tat org.apache.spark.api.python.BasePythonRunner$ReaderIterator.hasNext(PythonRunner.scala:1034)\n'
    '\tat org.apache.spark.InterruptibleIterator.hasNext(InterruptibleIterator.scala:37)\n'
    '\tat scala.collection.Iterator$$anon$10.hasNext(Iterator.scala:611)\n'
    '\tat scala.collection.Iterator$$anon$9.hasNext(Iterator.scala:593)\n'
    '\tat scala.collection.Iterator$$anon$9.hasNext(Iterator.scala:593)\n'
    '\tat org.apache.spark.sql.catalyst.expressions.GeneratedClass$GeneratedIteratorForCodegenStage2.processNext(Unknown Source)\n'
    '\tat org.apache.spark.sql.execution.BufferedRowIterator.hasNext(BufferedRowIterator.java:44)\n'
    '\tat org.apache.spark.sql.execution.WholeStageCodegenEvaluatorFactory$WholeStageCodegenPartitionEvaluator$$anon$1.hasNext(WholeStageCodegenEvaluatorFactory.scala:50)\n'
    '\tat org.apache.spark.sql.execution.SparkPlan.$anonfun$getByteArrayRdd$2(SparkPlan.scala:418)\n'
    '\tat org.apache.spark.rdd.RDD.$anonfun$mapPartitionsInternal$2(RDD.scala:960)\n'
    '\tat org.apache.spark.rdd.RDD.$anonfun$mapPartitionsInternal$2$adapted(RDD.scala:960)\n'
    '\tat org.apache.spark.rdd.MapPartitionsRDD.compute(MapPartitionsRDD.scala:57)\n'
    '\tat org.apache.spark.rdd.RDD.computeOrReadCheckpoint(RDD.scala:374)\n'
    '\tat org.apache.spark.rdd.RDD.iterator(RDD.scala:338)\n'
    '\tat org.apache.spark.scheduler.ResultTask.runTask(ResultTask.scala:93)\n'
    '\tat org.apache.spark.TaskContext.runTaskWithListeners(TaskContext.scala:240)\n'
    '\tat org.apache.spark.scheduler.Task.run(Task.scala:147)\n'
    '\tat org.apache.spark.executor.Executor$TaskRunner.$anonfun$run$4(Executor.scala:903)\n'
    '\tat org.apache.spark.util.SparkErrorUtils.tryWithSafeFinally(SparkErrorUtils.scala:86)\n'
    '\tat org.apache.spark.util.SparkErrorUtils.tryWithSafeFinally$(SparkErrorUtils.scala:83)\n'
    '\tat org.apache.spark.util.Utils$.tryWithSafeFinally(Utils.scala:97)\n'
    '\tat org.apache.spark.executor.Executor$TaskRunner.run(Executor.scala:906)\n'
    '\tat java.base/java.util.concurrent.ThreadPoolExecutor.runWorker(ThreadPoolExecutor.java:1144)\n'
    '\tat java.base/java.util.concurrent.ThreadPoolExecutor$Worker.run(ThreadPoolExecutor.java:642)\n'
    '\tat java.base/java.lang.Thread.run(Thread.java:1583)\n'
    '\n'
    '26/11/04 17:41:00 ERROR TaskSetManager: Task 0 in stage 0.0 failed 1 times; aborting job\n'
    '\r'
    '[Stage 0:>                                                          (0 + 0) / 1]\r'
    'Traceback (most recent call last):\n'
    '  File "/work/app/job.py", line 15, in <module>\n'
    '    out.show()\n'
    '    ~~~~~~~~^^\n'
    '  File "/home/dev/.venv/lib/python3.13/site-packages/pyspark/sql/classic/dataframe.py", line 302, in show\n'
    '    print(self._show_string(n, truncate, vertical))\n'
    '          ~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^\n'
    '  File "/home/dev/.venv/lib/python3.13/site-packages/pyspark/sql/classic/dataframe.py", line 328, in _show_string\n'
    '    return self._jdf.showString(n, 20, vertical)\n'
    '           ~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^\n'
    '  File "/home/dev/.venv/lib/python3.13/site-packages/py4j/java_gateway.py", line 1362, in __call__\n'
    '    return_value = get_return_value(\n'
    '        answer, self.gateway_client, self.target_id, self.name)\n'
    '  File "/home/dev/.venv/lib/python3.13/site-packages/pyspark/errors/exceptions/captured.py", line 262, in deco\n'
    '    raise converted from None\n'
    '  File "/work/app/job.py", line 9, in parse_qty\n'
    '    return int(raw)\n'
    '      ^^^^^^^^^^^^^\n'
    'pyspark.errors.exceptions.captured.PythonException: An exception was thrown from the Python worker:\n'
    'Traceback (most recent call last):\n'
    '  File "/work/app/job.py", line 9, in parse_qty\n'
    '    return int(raw)\n'
    "ValueError: invalid literal for int() with base 10: 'x3'\n"
)

_P_LIB = re.compile(r"site-packages|dist-packages|\.pyx|\.pxi|\.pxd|/lib/python3|pyspark\.zip|py4j|<frozen")
_P_IPY = re.compile(r"^File (\S+?):(\d+), in (.*)$")
_P_CELL = re.compile(r"^(?:Cell In\[\d+\]|Input In \[\d+\]|<ipython-input-[^>]*>),? ?(?:line \d+)?.*$")
_P_PY = re.compile(r'^(\s*)File "(.+?)", line (\d+), in (.*)$')
_P_STUB = re.compile(r"^\s*\[ctxprune: (\d+) (library frames folded|Java stack frames elided)\]$")
_P_SAME = re.compile(r"^\s*\[ctxprune: same Python traceback as above \(\d+ lines\)\]$")
_P_JAVA = re.compile(r"^\tat \S+\(.*\)$")
_P_TB = "Traceback (most recent call last):"
_P_NUMBERED = re.compile(r"^\s*(?:-+> )?\d+(?: .*)?$")  # the numbered source lines IPython prints under a frame header


def _frame_items(lines, P):
    """[(kind, start, end, is_library)] for IPython-style ('ipy') and classic ('py') frames."""
    items, i, n = [], 0, len(lines)
    while i < n:
        p = P[i]
        if m := _P_IPY.match(p):
            j = i + 1
            while j < n and _P_NUMBERED.match(P[j]):
                j += 1
            items.append(("ipy", i, j, bool(_P_LIB.search(m.group(1)))))
            i = j
        elif _P_CELL.match(p) and ("line" in p or p.startswith(("Input", "<ipython"))):
            j = i + 1
            while j < n and _P_NUMBERED.match(P[j]):
                j += 1
            items.append(("ipy", i, j, False))
            i = j
        elif m := _P_PY.match(p):
            ind = m.group(1)
            j = i + 1
            while j < n and P[j].startswith(ind + "  ") and not _P_PY.match(P[j]):
                j += 1
            items.append(("py", i, j, bool(_P_LIB.search(m.group(2)))))
            i = j
        else:
            i += 1
    return items


def fold_runtime(text: str) -> str:
    lines = [plain(l) for l in text.split("\n")]
    P = lines
    n = len(lines)
    items = _frame_items(lines, P)
    drop, stubs = set(), {}
    k = 0
    while k < len(items):
        if not items[k][3]:
            k += 1
            continue
        run = [items[k]]
        def adjacent(prev, nxt):  # no stray line between them (IPython frames are separated by exactly one blank line)
            return nxt[1] == prev[2] or (nxt[1] == prev[2] + 1 and nxt[0] == "ipy" and not lines[prev[2]].strip())

        while k + 1 < len(items) and items[k + 1][3] and items[k + 1][0] == run[0][0] and adjacent(run[-1], items[k + 1]):
            k += 1
            run.append(items[k])
        k += 1
        start, end = run[0][1], run[-1][2]
        ind = re.match(r"^\s*", lines[start]).group(0)
        stub = f"{ind}[ctxprune: {len(run)} library frames folded]"
        if len(stub) < sum(len(lines[x]) + 1 for x in range(start, end)):
            stubs[start] = stub
            drop.update(range(start, end))
    out = []
    for i, l in enumerate(lines):
        if i in stubs:
            out.append(stubs[i])
        if i not in drop:
            out.append(l)
    # repeated Python tracebacks: identical frames+exception block after a header containing 'Traceback (most recent call last):'
    lines, out, i, seen, res = out, [], 0, set(), []
    n = len(lines)
    while i < n:
        l = lines[i]
        res.append(l)
        i += 1
        if _P_TB in l:
            j = i
            while j < n and lines[j].startswith("  ") and not _P_SAME.match(lines[j]):
                j += 1
            if j < n and lines[j] and not lines[j].startswith((" ", "\t")):
                j += 1  # the exception line belongs to the block
            block = tuple(lines[i:j])
            if len(block) >= 3 and block in seen:
                res.append(f"  [ctxprune: same Python traceback as above ({len(block)} lines)]")
                i = j
            else:
                seen.add(block)
    # runs of Java stack frames
    lines, res, i, n = res, [], 0, len(res)
    while i < n:
        if _P_JAVA.match(lines[i]):
            j = i
            while j < n and _P_JAVA.match(lines[j]):
                j += 1
            if j - i >= 3:
                res.append(f"\t[ctxprune: {j - i} Java stack frames elided]")
                i = j
                continue
        res.append(lines[i])
        i += 1
    return shrink_only(text, "\n".join(res))


def probes_1325(text: str) -> dict:
    lines = [plain(l) for l in text.split("\n")]
    exc, user_frames, cell, libs, java = set(), [], [], 0, 0
    for i, l in enumerate(lines):
        if re.match(r"^(?:[\w.$]+(?:Error|Exception|Exit|Interrupt)\b.*|Caused by: .*)$", l) and not l.startswith("File"):
            exc.add(l)
        if (m := _P_IPY.match(l)):
            if _P_LIB.search(m.group(1)):
                libs += 1
            else:
                user_frames.append((l, next((x.strip() for x in lines[i + 1:i + 12] if re.match(r"^\s*-+> \d+", x)), None)))
        elif _P_CELL.match(l) and ("line" in l or l.startswith(("Input", "<ipython"))):
            user_frames.append((l, next((x.strip() for x in lines[i + 1:i + 12] if re.match(r"^\s*-+> \d+", x)), None)))
        elif (m := _P_PY.match(l)):
            if _P_LIB.search(m.group(2)):
                libs += 1
            else:
                user_frames.append((l.strip(), lines[i + 1].strip() if i + 1 < len(lines) and lines[i + 1].startswith(m.group(1) + "    ") else None))
        if (s := _P_STUB.match(l)):
            if s.group(2).startswith("library"):
                libs += int(s.group(1))
            else:
                java += int(s.group(1))
        elif _P_JAVA.match(l):
            java += 1
    if "------------------" in lines:
        a = lines.index("------------------")
        b = lines.index("------------------", a + 1) if "------------------" in lines[a + 1:] else a
        block = lines[a + 1:b]
        frame_like = lambda l: bool(_P_IPY.match(l) or _P_PY.match(l) or _P_STUB.match(l) or (_P_CELL.match(l) and "line" in l) or l.startswith("-" * 20))
        cell = [] if any(frame_like(l) for l in block) or b - a > 60 else block
    return {"distinct exception lines": sorted(exc), "user frames with source pointer": sorted(set(user_frames), key=str), "failing cell source": cell,
            "library frames accounted for": libs, "java frames accounted for": java}


COMPOSITE_1325 = cat("$ make notebooks\nexecuting nb.ipynb ...\n", NBCONVERT_KEYERROR, "make: *** [Makefile:12: notebooks] Error 1\n$ spark-submit job.py\n", PYSPARK_UDF_ERROR, "$ echo $?\n1\n")

case(
    1325,
    claim="Strips ANSI colour, folds each run of library frames (site-packages, Cython .pyx/.pxi, pyspark/py4j, stdlib) of an IPython or classic traceback into '[ctxprune: N library frames folded]', replaces a Python traceback that repeats an earlier one by a 'same as above' line, and replaces runs of 3+ Java 'at' frames by a count; the failing cell's source, every user frame (Cell In[N], project files) with its pointer line, every distinct exception line and the counts of library and Java frames are unchanged.",
    loses="ANSI colour, the names and source excerpts of library frames, the second and later identical copies of a Python traceback, and the individual Java frames (the saved original keeps them)",
    encode=fold_runtime,
    probes=probes_1325,
    fixtures=lambda: [
        Fixture("jupyter nbconvert --execute: pandas KeyError, ANSI colour, nbconvert's own traceback first (real)", NBCONVERT_KEYERROR, True),
        Fixture("pyspark: UDF raises ValueError, traceback repeated 3x with Java frames (real)", PYSPARK_UDF_ERROR, True),
        Fixture("CI log: notebook failure first, make error, spark failure last", COMPOSITE_1325, True),
        Fixture("a short traceback with only user frames", 'Traceback (most recent call last):\n  File "/work/app/job.py", line 12, in <module>\n    main()\n  File "/work/app/job.py", line 8, in main\n    raise ValueError("bad")\nValueError: bad\n', False),
        Fixture("a library frame between two user frames of a short classic traceback is folded too, not hidden", 'Traceback (most recent call last):\n  File "/work/app/a.py", line 3, in <module>\n    run()\n  File "/a/site-packages/x.py", line 1, in f\n    g()\n  File "/work/app/a.py", line 9, in run\n    boom()\nKeyError: 1\n', None),
        Fixture("text with a Traceback header inside a log line and no frames", "ERROR: job failed: Traceback (most recent call last):\n[ctxprune: same Python traceback as above (9 lines)]\n", None),
    ],
    min_gain=0.3,
)



# ===============================================================================================================
# #214 xcodebuild: drop the per-step 'cd', 'export' and tool command lines under each step header (SwiftCompile, Ld,
# CodeSign, ProcessInfoPlistFile, CompileC, ...). RULE: keep every header, every diagnostic with its echoed source,
# the BUILD result and the failed-command list. No Mac here: the fixtures are generated to the xcodebuild step
# format as documented in public CI logs, with realistic multi-hundred-character swift-frontend/clang command lines
# (reconstructed from memory; the idea itself says 'from memory, unmeasured'). The trap the generator includes: an
# echoed Swift/ObjC source line that starts with '/' (a doc comment) must not be mistaken for a command line.

_XC_DD = "/Users/runner/Library/Developer/Xcode/DerivedData/App-bhqkdmfyxqrlsvbhvjtfhzmmqjoc"
_XC_TC = "/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin"
_XC_SDK = "/Applications/Xcode.app/Contents/Developer/Platforms/iPhoneSimulator.platform/Developer/SDKs/iPhoneSimulator17.2.sdk"


def _xc_swift_cmd(src: str) -> str:
    flags = " ".join(f"-Xcc -I{_XC_DD}/Build/Intermediates.noindex/App.build/Debug-iphonesimulator/App.build/{d}" for d in ("swift-overrides.hmap", "DerivedSources-normal/arm64", "DerivedSources/arm64", "DerivedSources"))
    return (f"{_XC_TC}/swift-frontend -frontend -c -primary-file /Users/runner/work/App/App/App/{src} -emit-module-path {_XC_DD}/Build/Intermediates.noindex/App.build/Debug-iphonesimulator/App.build/Objects-normal/arm64/{src[:-6]}.swiftmodule "
            f"-target arm64-apple-ios17.0-simulator -sdk {_XC_SDK} -enable-objc-interop -enable-testing -g -module-cache-path {_XC_DD}/ModuleCache.noindex -swift-version 5 -Onone {flags} -module-name App -parse-as-library")


def _xc_steps(files, fail_at=None):
    out = ["Command line invocation:", "    /Applications/Xcode.app/Contents/Developer/usr/bin/xcodebuild -scheme App -destination \"platform=iOS Simulator,name=iPhone 15\" build", "",
           "Prepare packages", "", "Computing target dependency graph and provisioning inputs", "", "Create build description", "Build description signature: 0d9c6a44f1a3bb7e4c3d5e2a1b9f8c77", "", f"CreateBuildDirectory {_XC_DD}/Build/Products",
           "    cd /Users/runner/work/App/App/App.xcodeproj", f"    builtin-create-build-directory {_XC_DD}/Build/Products", "",
           f"ProcessInfoPlistFile {_XC_DD}/Build/Products/Debug-iphonesimulator/App.app/Info.plist /Users/runner/work/App/App/App/Info.plist (in target 'App' from project 'App')", "    cd /Users/runner/work/App/App",
           f"    builtin-infoPlistUtility /Users/runner/work/App/App/App/Info.plist -producttype com.apple.product-type.application -genpkginfo {_XC_DD}/Build/Products/Debug-iphonesimulator/App.app/PkgInfo -expandbuildsettings -format binary -platform iphonesimulator -o {_XC_DD}/Build/Products/Debug-iphonesimulator/App.app/Info.plist", ""]
    for k, f in enumerate(files):
        out += [f"SwiftCompile normal arm64 Compiling\\ {f} /Users/runner/work/App/App/App/{f} (in target 'App' from project 'App')", "    cd /Users/runner/work/App/App", "    " + _xc_swift_cmd(f), ""]
        if k == 1:
            out += [f"/Users/runner/work/App/App/App/{f}:41:17: warning: variable 'tmp' was never used; consider replacing with '_' or removing it", "        let tmp = compute()", "            ~~~~^~~", ""]
        if fail_at == k:
            out += [f"/Users/runner/work/App/App/App/{f}:12:9: error: cannot find 'undefinedHelper' in scope", "        undefinedHelper()", "        ^~~~~~~~~~~~~~~", f"/Users/runner/work/App/App/App/{f}:20:5: error: cannot find 'fmt' in scope",
                    "    /// docs mention fmt(...) here", "    fmt(3)", "    ^~~", ""]
    if fail_at is None:
        out += [f"Ld {_XC_DD}/Build/Products/Debug-iphonesimulator/App.app/App normal (in target 'App' from project 'App')", "    cd /Users/runner/work/App/App",
                f"    {_XC_TC}/clang -target arm64-apple-ios17.0-simulator -isysroot {_XC_SDK} -L{_XC_DD}/Build/Products/Debug-iphonesimulator -F{_XC_DD}/Build/Products/Debug-iphonesimulator -filelist {_XC_DD}/Build/Intermediates.noindex/App.build/Debug-iphonesimulator/App.build/Objects-normal/arm64/App.LinkFileList -Xlinker -rpath -Xlinker @executable_path/Frameworks -o {_XC_DD}/Build/Products/Debug-iphonesimulator/App.app/App", "",
                f"CodeSign {_XC_DD}/Build/Products/Debug-iphonesimulator/App.app (in target 'App' from project 'App')", "    cd /Users/runner/work/App/App", f"    export CODESIGN_ALLOCATE={_XC_TC}/codesign_allocate", "", "    Signing Identity:     \"-\"", "",
                f"    /usr/bin/codesign --force --sign - --timestamp\\=none --generate-entitlement-der {_XC_DD}/Build/Products/Debug-iphonesimulator/App.app", "", "** BUILD SUCCEEDED **", ""]
    else:
        out += ["** BUILD FAILED **", "", "", "The following build commands failed:", f"\tSwiftCompile normal arm64 Compiling\\ {files[fail_at]} /Users/runner/work/App/App/App/{files[fail_at]} (in target 'App' from project 'App')", "(1 failure)", ""]
    return "\n".join(out)


_XC_FILES = ["AppDelegate.swift", "ContentView.swift", "Model.swift", "NetworkClient.swift", "Store.swift", "Theme.swift", "Router.swift", "Settings.swift"]
XCODE_GREEN = _xc_steps(_XC_FILES)
XCODE_RED = _xc_steps(_XC_FILES[:5], fail_at=3)

_X_STEPS = ("CreateBuildDirectory|MkDir|Touch|WriteAuxiliaryFile|RegisterExecutionPolicyException|SetMode|SetOwnerAndGroup|Copy|CpResource|CpHeader|Ditto|Strip|Validate|Libtool|Ld|CodeSign|"
            "ProcessInfoPlistFile|ProcessProductPackaging|SwiftCompile|SwiftDriver|CompileC|CompileSwift|CompileAssetCatalog|CompileStoryboard|PhaseScriptExecution|GenerateDSYMFile|LinkStoryboards|CopySwiftLibs")
_X_HEAD = re.compile(r"^(?:[A-Z][A-Za-z]+ .*\(in target '[^']*' from project '[^']*'\)|(?:" + _X_STEPS + r") \S.*)$")
_X_CMD = re.compile(r"^    (?:cd \S+|export \S+=.*|setenv \S+ .*|unsetenv \S+|builtin-\S+.*|/(?:Applications|usr|bin|opt)/\S+.*|write-file .*)$")
_X_DIAG = re.compile(r"^\S[^:]*:\d+:\d+: (?:error|warning|note): ")


def drop_step_commands(text: str) -> str:
    lines = text.split("\n")
    out, state = [], None
    for i, l in enumerate(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if l and not l[0].isspace():
            if _X_DIAG.match(l):
                state = "diag"
            elif _X_HEAD.match(l) and re.match(r"^    cd \S+$", nxt):
                state = "step"
            else:
                state = None
        elif state == "step" and _X_CMD.match(l):
            continue
        out.append(l)
    return shrink_only(text, "\n".join(out))


def probes_214(text: str) -> dict:
    lines = text.split("\n")
    steps, diags, results, echo = [], [], [], []
    for i, l in enumerate(lines):
        if l and not l[0].isspace():
            if _X_HEAD.match(l):
                steps.append(l)
            elif m := re.match(r"^(\S[^:]*):(\d+):(\d+): (error|warning|note): (.*)$", l):
                diags.append((m[1], int(m[2]), int(m[3]), m[4], m[5]))
                echo.append([x for x in lines[i + 1:i + 4] if x.startswith("    ") and not re.match(r"^    (cd |export |builtin-|/Applications/)", x)])
            elif re.match(r"^(\*\* BUILD |The following build commands failed|\(\d+ failures?\)|Testing failed)", l) or l.startswith("\t"):
                results.append(l)
        elif l.startswith("\t"):
            results.append(l)
    return {"step headers in order": steps, "diagnostics (file, line, col, severity, message)": diags, "echoed source under each diagnostic": echo, "build result and failed-command list": results}


case(
    214,
    claim="Under each xcodebuild step header, drops the indented 'cd', 'export', 'builtin-*' and absolute-path tool command lines; every step header, every file:line:col diagnostic with its echoed source (even one starting with '/'), the BUILD SUCCEEDED/FAILED line and the failed-command list are unchanged.",
    loses="the exact swift-frontend/clang/ld/codesign command lines, flag lists and working directories of every step (the saved original keeps them)",
    encode=drop_step_commands,
    probes=probes_214,
    fixtures=lambda: [
        Fixture("8 Swift files, link and codesign, one warning, BUILD SUCCEEDED (generated, reconstructed from memory)", XCODE_GREEN, True),
        Fixture("5 files, errors in the 4th including an echoed '///' line, BUILD FAILED (generated)", XCODE_RED, True),
        Fixture("xcodebuild -quiet: only diagnostics", "/Users/runner/work/App/App/App/A.swift:3:5: error: x\n    foo()\n    ^\n** BUILD FAILED **\n", False),
        Fixture("not xcodebuild: a shell script with indented cd lines", "build:\n    cd src\n    make all\nSetup step\n    cd /tmp\n    /usr/bin/true\n", None),
    ],
    min_gain=0.5,
)



# ===============================================================================================================
# #216 MSBuild / dotnet build: every warning and error is printed inline, again in the end summary, and once per
# target framework, each line ending in [/abs/path/proj.csproj(::TargetFramework=tfm)]. Fold repeats to one line with
# a count, hoist the project path into a legend. RULE: the probe checks, per distinct (file, position, severity,
# code, message), the project it belongs to, how many times it was printed and for which target frameworks, plus
# every non-diagnostic line (restore, '-> dll', 'Build succeeded.', 'N Warning(s)', 'Time Elapsed'). No .NET SDK
# here: the logs are generated to the documented dotnet 8 format (and the older '[proj.csproj]' suffix without the
# framework) with 12 distinct warnings, 2 target frameworks, and the summary repeating them (reconstructed from memory).

_MS_DIAG = re.compile(r"^(?P<file>.+?)(?:\((?P<l>\d+),(?P<c>\d+)(?:,\d+,\d+)?\))? ?: (?P<sev>warning|error) (?P<code>[A-Z]+\d+): (?P<msg>.*?) \[(?P<proj>[^\]\[]+?\.\w*proj)(?:::TargetFramework=(?P<tfm>[^\]\[]+))?\]$")
_MS_FOLDED = re.compile(r"^(?P<head>.+: (?:warning|error) [A-Z]+\d+: .*?) \[@(?P<id>\d+)\](?: x(?P<n>\d+))?(?: tfm=(?P<tfm>\S+))?$")
_MS_LEGEND = re.compile(r"^\[ctxprune: @(\d+) = (.+)\]$")


def _ms_warnings() -> list[tuple[str, int, int, str, str, str]]:
    base = [("Program.cs", 10, 13, "warning", "CS0168", "The variable 'x' is declared but never used"), ("Program.cs", 21, 9, "warning", "CS8602", "Dereference of a possibly null reference."),
            ("Services/UserService.cs", 44, 20, "warning", "CS8618", "Non-nullable property 'Name' must contain a non-null value when exiting constructor. Consider adding the 'required' modifier or declaring the property as nullable."),
            ("Services/UserService.cs", 61, 17, "warning", "CS1998", "This async method lacks 'await' operators and will run synchronously. Consider using the 'await' operator to await non-blocking API calls."),
            ("Models/Order.cs", 12, 23, "warning", "CS8625", "Cannot convert null literal to non-nullable reference type."), ("Models/Order.cs", 30, 5, "warning", "CS0649", "Field 'Order._id' is never assigned to, and will always have its default value 0"),
            ("Controllers/HomeController.cs", 18, 33, "warning", "CS8600", "Converting null literal or possible null value to non-nullable type."), ("Controllers/HomeController.cs", 25, 13, "warning", "CS0219", "The variable 'count' is assigned but its value is never used"),
            ("Data/Repo.cs", 77, 40, "warning", "CS8604", "Possible null reference argument for parameter 'id' in 'Task<User> Repo.Find(string id)'."), ("Data/Repo.cs", 90, 9, "warning", "CS0414", "The private field 'Repo._retries' is assigned but its value is never used"),
            ("Util/Json.cs", 5, 1, "warning", "CS8019", "Unnecessary using directive."), ("App.csproj", 0, 0, "warning", "NU1903", "Package 'System.Text.Json' 6.0.0 has a known high severity vulnerability, https://github.com/advisories/GHSA-hh2w-p6rv-4g7w")]
    return base


def _msbuild_log(tfms, old_suffix=False, error=False) -> str:
    proj = "/src/App/App.csproj"
    suf = lambda t: f"[{proj}]" if old_suffix else f"[{proj}::TargetFramework={t}]"
    diag = lambda w, t: (f"/src/App/{w[0]}({w[1]},{w[2]}): {w[3]} {w[4]}: {w[5]} {suf(t)}" if w[1] else f"{proj} : {w[3]} {w[4]}: {w[5]} {suf(t)}")
    out = ["  Determining projects to restore...", f"  Restored {proj} (in 312 ms)."]
    ws = _ms_warnings()
    if error:
        ws = ws[:6] + [("Services/UserService.cs", 70, 12, "error", "CS1002", "; expected")] + ws[6:]
    for w in ws:
        for t in tfms:
            out.append(diag(w, t))
    out += [f"  App -> /src/App/bin/Debug/{t}/App.dll" for t in tfms]
    out += ["", "Build FAILED." if error else "Build succeeded.", ""]
    for w in ws:
        for t in tfms:
            out.append(diag(w, t))
    n_err = len(tfms) if error else 0
    out += [f"    {len(ws) * len(tfms) - n_err} Warning(s)", f"    {n_err} Error(s)", "", "Time Elapsed 00:00:04.21", ""]
    return "\n".join(out)


MSBUILD_TWO_TFMS = _msbuild_log(["net8.0", "net6.0"])
MSBUILD_OLD_SUFFIX = _msbuild_log(["net6.0", "netstandard2.0"], old_suffix=True)
MSBUILD_ERROR = _msbuild_log(["net8.0", "net6.0"], error=True)


def fold_msbuild(text: str) -> str:
    lines = text.split("\n")
    diags = [(i, m) for i, l in enumerate(lines) if (m := _MS_DIAG.match(l))]
    if len(diags) < 2:
        return text
    projs: dict = {}
    keys: OrderedDict = OrderedDict()
    for i, m in diags:
        pid = projs.setdefault(m["proj"], len(projs) + 1)
        k = (m["file"], m["l"], m["c"], m["sev"], m["code"], m["msg"], pid)
        e = keys.setdefault(k, {"n": 0, "tfms": [], "first": i})
        e["n"] += 1
        if m["tfm"] and m["tfm"] not in e["tfms"]:
            e["tfms"].append(m["tfm"])
    first_of = {e["first"]: (k, e) for k, e in keys.items()}
    skip = {i for i, _ in diags} - set(first_of)
    out = [f"[ctxprune: @{pid} = {p}]" for p, pid in projs.items()]
    for i, l in enumerate(lines):
        if i in skip:
            continue
        if i in first_of:
            k, e = first_of[i]
            pos = f"({k[1]},{k[2]})" if k[1] else ""
            out.append(f"{k[0]}{pos}: {k[3]} {k[4]}: {k[5]} [@{k[6]}]" + (f" x{e['n']}" if e["n"] > 1 else "") + (f" tfm={','.join(e['tfms'])}" if e["tfms"] else ""))
        else:
            out.append(l)
    return shrink_only(text, "\n".join(out))


def probes_msbuild(text: str) -> dict:
    lines = text.split("\n")
    legend = {m.group(1): m.group(2) for l in lines if (m := _MS_LEGEND.match(l))}
    facts: dict = {}
    other = []
    for l in lines:
        if _MS_LEGEND.match(l):
            continue
        if m := _MS_DIAG.match(l):
            k = (m["file"], m["l"], m["c"], m["sev"], m["code"], m["msg"], m["proj"])
            e = facts.setdefault(k, [0, set()])
            e[0] += 1
            if m["tfm"]:
                e[1].add(m["tfm"])
        elif (m := _MS_FOLDED.match(l)) and m["id"] in legend:
            hm = re.match(r"^(?P<file>.+?)(?:\((?P<l>\d+),(?P<c>\d+)\))?: (?P<sev>warning|error) (?P<code>[A-Z]+\d+): (?P<msg>.*)$", m["head"])
            if hm:
                k = (hm["file"], hm["l"], hm["c"], hm["sev"], hm["code"], hm["msg"], legend[m["id"]])
                e = facts.setdefault(k, [0, set()])
                e[0] += int(m["n"] or 1)
                if m["tfm"]:
                    e[1].update(m["tfm"].split(","))
            else:
                other.append(l)
        else:
            other.append(l)
    return {"per distinct diagnostic: project, times printed, target frameworks": sorted(((k, v[0], sorted(v[1])) for k, v in facts.items()), key=str), "other lines (restore, outputs, results, totals)": other}


case(
    216,
    claim="Repeats of the same MSBuild diagnostic (same file, position, severity, code and message) across the inline list, the end summary and target frameworks become one line 'file(l,c): warning CODE: msg [@1] xN tfm=a,b' with the project path hoisted to one '[ctxprune: @1 = /path/App.csproj]' line; per distinct diagnostic the project, the number of times printed and the frameworks, and every other line ('Build succeeded.', 'N Warning(s)', ...) are unchanged.",
    loses="the interleaved order of repeats (they collapse onto the first occurrence), which of the repeats came from the end summary rather than the inline list, and the project path text on each line (it is in the legend)",
    encode=fold_msbuild,
    probes=probes_msbuild,
    fixtures=lambda: [
        Fixture("dotnet build, 2 target frameworks, 12 warnings printed 4 times each (generated, dotnet 8 format, reconstructed from memory)", MSBUILD_TWO_TFMS, True),
        Fixture("older SDK: '[proj.csproj]' suffix without ::TargetFramework (generated)", MSBUILD_OLD_SUFFIX, True),
        Fixture("Build FAILED with an error CS1002 repeated like the warnings (generated)", MSBUILD_ERROR, True),
        Fixture("a single warning (printed once): nothing to fold", "  Determining projects to restore...\n/src/App/Program.cs(10,13): warning CS0168: The variable 'x' is declared but never used [/src/App/App.csproj::TargetFramework=net8.0]\n    1 Warning(s)\n", False),
        Fixture("dotnet test output", "Passed!  - Failed:     0, Passed:    42, Skipped:     0, Total:    42, Duration: 1 s\n", False),
        Fixture("a diagnostic with no position and a bracket inside the message", "/src/App/App.csproj : warning NU1603: App depends on Foo (>= 1.0) [x] [/src/App/App.csproj]\n/src/App/App.csproj : warning NU1603: App depends on Foo (>= 1.0) [x] [/src/App/App.csproj]\n", None),
    ],
    min_gain=0.4,
)



# ===============================================================================================================
# #220 g++/clang overload errors: keep the error plus the first three 'note: candidate' records, then '+N more'.
# RULE: every error line, the first three candidates (with their 'no known conversion' sub-notes), the number of
# candidates per run and every non-candidate note/warning must survive.

CLANG_OPERATOR_BIG = (
    "o.cpp:16:28: error: invalid operands to binary expression ('basic_ostream<char, char_traits<char>>' and 'Point')\n"
    '   16 |     std::cout << "point: " << p << std::endl;\n'
    '      |     ~~~~~~~~~~~~~~~~~~~~~~ ^  ~\n'
    "/usr/include/c++/13/system_error:339:5: note: candidate function template not viable: no known conversion from 'Point' to 'const error_code' for 2nd argument\n"
    '  339 |     operator<<(basic_ostream<_CharT, _Traits>& __os, const error_code& __e)\n'
    '      |     ^                                                ~~~~~~~~~~~~~~~~~~~~~\n'
    "/usr/include/c++/13/ostream:564:5: note: candidate function template not viable: no known conversion from 'Point' to 'char' for 2nd argument\n"
    '  564 |     operator<<(basic_ostream<_CharT, _Traits>& __out, char __c)\n'
    '      |     ^                                                 ~~~~~~~~\n'
    "/usr/include/c++/13/ostream:570:5: note: candidate function template not viable: no known conversion from 'Point' to 'char' for 2nd argument\n"
    '  570 |     operator<<(basic_ostream<char, _Traits>& __out, char __c)\n'
    '      |     ^                                               ~~~~~~~~\n'
    "/usr/include/c++/13/ostream:581:5: note: candidate function template not viable: no known conversion from 'Point' to 'signed char' for 2nd argument\n"
    '  581 |     operator<<(basic_ostream<char, _Traits>& __out, signed char __c)\n'
    '      |     ^                                               ~~~~~~~~~~~~~~~\n'
    "/usr/include/c++/13/ostream:586:5: note: candidate function template not viable: no known conversion from 'Point' to 'unsigned char' for 2nd argument\n"
    '  586 |     operator<<(basic_ostream<char, _Traits>& __out, unsigned char __c)\n'
    '      |     ^                                               ~~~~~~~~~~~~~~~~~\n'
    "/usr/include/c++/13/ostream:662:5: note: candidate function template not viable: no known conversion from 'Point' to 'const char *' for 2nd argument\n"
    '  662 |     operator<<(basic_ostream<char, _Traits>& __out, const char* __s)\n'
    '      |     ^                                               ~~~~~~~~~~~~~~~\n'
    "/usr/include/c++/13/ostream:675:5: note: candidate function template not viable: no known conversion from 'Point' to 'const signed char *' for 2nd argument\n"
    '  675 |     operator<<(basic_ostream<char, _Traits>& __out, const signed char* __s)\n'
    '      |     ^                                               ~~~~~~~~~~~~~~~~~~~~~~\n'
    "/usr/include/c++/13/ostream:680:5: note: candidate function template not viable: no known conversion from 'Point' to 'const unsigned char *' for 2nd argument\n"
    '  680 |     operator<<(basic_ostream<char, _Traits>& __out, const unsigned char* __s)\n'
    '      |     ^                                               ~~~~~~~~~~~~~~~~~~~~~~~~\n'
    "/usr/include/c++/13/bits/ostream.tcc:307:5: note: candidate function template not viable: no known conversion from 'Point' to 'const char *' for 2nd argument\n"
    '  307 |     operator<<(basic_ostream<_CharT, _Traits>& __out, const char* __s)\n'
    '      |     ^                                                 ~~~~~~~~~~~~~~~\n'
    "/usr/include/c++/13/cstddef:124:5: note: candidate function template not viable: no known conversion from 'basic_ostream<char, char_traits<char>>' to 'byte' for 1st argument\n"
    '  124 |     operator<<(byte __b, _IntegerType __shift) noexcept\n'
    '      |     ^          ~~~~~~~~\n'
    "/usr/include/c++/13/ostream:554:5: note: candidate template ignored: deduced conflicting types for parameter '_CharT' ('char' vs. 'Point')\n"
    '  554 |     operator<<(basic_ostream<_CharT, _Traits>& __out, _CharT __c)\n'
    '      |     ^\n'
    "/usr/include/c++/13/string_view:761:5: note: candidate template ignored: could not match 'basic_string_view<_CharT, _Traits>' against 'Point'\n"
    '  761 |     operator<<(basic_ostream<_CharT, _Traits>& __os,\n'
    '      |     ^\n'
    "/usr/include/c++/13/bits/basic_string.h:4032:5: note: candidate template ignored: could not match 'basic_string<_CharT, _Traits, _Alloc>' against 'Point'\n"
    ' 4032 |     operator<<(basic_ostream<_CharT, _Traits>& __os,\n'
    '      |     ^\n'
    "/usr/include/c++/13/ostream:645:5: note: candidate template ignored: could not match 'const _CharT *' against 'Point'\n"
    '  645 |     operator<<(basic_ostream<_CharT, _Traits>& __out, const _CharT* __s)\n'
    '      |     ^\n'
    "/usr/include/c++/13/ostream:801:5: note: candidate template ignored: requirement '__and_<std::is_class<std::basic_ostream<char, std::char_traits<char>> &>, std::__not_<std::is_same<std::basic_ostream<char, std::char_traits<char>> &, std::ios_base>>, std::is_convertible<std::basic_ostream<char, std::char_traits<char>> *, std::ios_base *>>::value' was not satisfied [with _Ostream = basic_ostream<char, char_traits<char>> &, _Tp = Point]\n"
    '  801 |     operator<<(_Ostream&& __os, const _Tp& __x)\n'
    '      |     ^\n'
    "/usr/include/c++/13/ostream:110:7: note: candidate function not viable: no known conversion from 'Point' to '__ostream_type &(*)(__ostream_type &)' (aka 'basic_ostream<char, std::char_traits<char>> &(*)(basic_ostream<char, std::char_traits<char>> &)') for 1st argument\n"
    '  110 |       operator<<(__ostream_type& (*__pf)(__ostream_type&))\n'
    '      |       ^          ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~\n'
    "/usr/include/c++/13/ostream:119:7: note: candidate function not viable: no known conversion from 'Point' to '__ios_type &(*)(__ios_type &)' (aka 'basic_ios<char, std::char_traits<char>> &(*)(basic_ios<char, std::char_traits<char>> &)') for 1st argument\n"
    '  119 |       operator<<(__ios_type& (*__pf)(__ios_type&))\n'
    '      |       ^          ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~\n'
    "/usr/include/c++/13/ostream:129:7: note: candidate function not viable: no known conversion from 'Point' to 'ios_base &(*)(ios_base &)' for 1st argument\n"
    '  129 |       operator<<(ios_base& (*__pf) (ios_base&))\n'
    '      |       ^          ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~\n'
    "/usr/include/c++/13/ostream:168:7: note: candidate function not viable: no known conversion from 'Point' to 'long' for 1st argument\n"
    '  168 |       operator<<(long __n)\n'
    '      |       ^          ~~~~~~~~\n'
    "/usr/include/c++/13/ostream:172:7: note: candidate function not viable: no known conversion from 'Point' to 'unsigned long' for 1st argument\n"
    '  172 |       operator<<(unsigned long __n)\n'
    '      |       ^          ~~~~~~~~~~~~~~~~~\n'
    "/usr/include/c++/13/ostream:176:7: note: candidate function not viable: no known conversion from 'Point' to 'bool' for 1st argument\n"
    '  176 |       operator<<(bool __n)\n'
    '      |       ^          ~~~~~~~~\n'
    "/usr/include/c++/13/ostream:180:7: note: candidate function not viable: no known conversion from 'Point' to 'short' for 1st argument\n"
    '  180 |       operator<<(short __n);\n'
    '      |       ^          ~~~~~~~~~\n'
    "/usr/include/c++/13/ostream:183:7: note: candidate function not viable: no known conversion from 'Point' to 'unsigned short' for 1st argument\n"
    '  183 |       operator<<(unsigned short __n)\n'
    '      |       ^          ~~~~~~~~~~~~~~~~~~\n'
    "/usr/include/c++/13/ostream:191:7: note: candidate function not viable: no known conversion from 'Point' to 'int' for 1st argument\n"
    '  191 |       operator<<(int __n);\n'
    '      |       ^          ~~~~~~~\n'
    "/usr/include/c++/13/ostream:194:7: note: candidate function not viable: no known conversion from 'Point' to 'unsigned int' for 1st argument\n"
    '  194 |       operator<<(unsigned int __n)\n'
    '      |       ^          ~~~~~~~~~~~~~~~~\n'
    "/usr/include/c++/13/ostream:203:7: note: candidate function not viable: no known conversion from 'Point' to 'long long' for 1st argument\n"
    '  203 |       operator<<(long long __n)\n'
    '      |       ^          ~~~~~~~~~~~~~\n'
    "/usr/include/c++/13/ostream:207:7: note: candidate function not viable: no known conversion from 'Point' to 'unsigned long long' for 1st argument\n"
    '  207 |       operator<<(unsigned long long __n)\n'
    '      |       ^          ~~~~~~~~~~~~~~~~~~~~~~\n'
    "/usr/include/c++/13/ostream:222:7: note: candidate function not viable: no known conversion from 'Point' to 'double' for 1st argument\n"
    '  222 |       operator<<(double __f)\n'
    '      |       ^          ~~~~~~~~~~\n'
    "/usr/include/c++/13/ostream:226:7: note: candidate function not viable: no known conversion from 'Point' to 'float' for 1st argument\n"
    '  226 |       operator<<(float __f)\n'
    '      |       ^          ~~~~~~~~~\n'
    "/usr/include/c++/13/ostream:234:7: note: candidate function not viable: no known conversion from 'Point' to 'long double' for 1st argument\n"
    '  234 |       operator<<(long double __f)\n'
    '      |       ^          ~~~~~~~~~~~~~~~\n'
    "/usr/include/c++/13/ostream:292:7: note: candidate function not viable: no known conversion from 'Point' to 'const void *' for 1st argument; take the address of the argument with &\n"
    '  292 |       operator<<(const void* __p)\n'
    '      |       ^          ~~~~~~~~~~~~~~~\n'
    "/usr/include/c++/13/ostream:297:7: note: candidate function not viable: no known conversion from 'Point' to 'nullptr_t' (aka 'std::nullptr_t') for 1st argument\n"
    '  297 |       operator<<(nullptr_t)\n'
    '      |       ^          ~~~~~~~~~\n'
    "/usr/include/c++/13/ostream:330:7: note: candidate function not viable: no known conversion from 'Point' to '__streambuf_type *' (aka 'basic_streambuf<char, std::char_traits<char>> *') for 1st argument\n"
    '  330 |       operator<<(__streambuf_type* __sb);\n'
    '      |       ^          ~~~~~~~~~~~~~~~~~~~~~~\n'
    "o.cpp:11:39: error: invalid operands to binary expression ('ostream' (aka 'basic_ostream<char>') and 'Point const')\n"
    '   11 |     for (const auto& e : v) std::cout << e << "\\n";\n'
    '      |                             ~~~~~~~~~ ^  ~\n'
    "o.cpp:18:5: note: in instantiation of function template specialization 'show<Point>' requested here\n"
    '   18 |     show(pts);\n'
    '      |     ^\n'
    "/usr/include/c++/13/system_error:339:5: note: candidate function template not viable: no known conversion from 'Point const' to 'const error_code' for 2nd argument\n"
    '  339 |     operator<<(basic_ostream<_CharT, _Traits>& __os, const error_code& __e)\n'
    '      |     ^                                                ~~~~~~~~~~~~~~~~~~~~~\n'
    "/usr/include/c++/13/ostream:564:5: note: candidate function template not viable: no known conversion from 'Point const' to 'char' for 2nd argument\n"
    '  564 |     operator<<(basic_ostream<_CharT, _Traits>& __out, char __c)\n'
    '      |     ^                                                 ~~~~~~~~\n'
    "/usr/include/c++/13/ostream:570:5: note: candidate function template not viable: no known conversion from 'Point const' to 'char' for 2nd argument\n"
    '  570 |     operator<<(basic_ostream<char, _Traits>& __out, char __c)\n'
    '      |     ^                                               ~~~~~~~~\n'
    "/usr/include/c++/13/ostream:581:5: note: candidate function template not viable: no known conversion from 'Point const' to 'signed char' for 2nd argument\n"
    '  581 |     operator<<(basic_ostream<char, _Traits>& __out, signed char __c)\n'
    '      |     ^                                               ~~~~~~~~~~~~~~~\n'
    "/usr/include/c++/13/ostream:586:5: note: candidate function template not viable: no known conversion from 'Point const' to 'unsigned char' for 2nd argument\n"
    '  586 |     operator<<(basic_ostream<char, _Traits>& __out, unsigned char __c)\n'
    '      |     ^                                               ~~~~~~~~~~~~~~~~~\n'
    "/usr/include/c++/13/ostream:662:5: note: candidate function template not viable: no known conversion from 'Point const' to 'const char *' for 2nd argument\n"
    '  662 |     operator<<(basic_ostream<char, _Traits>& __out, const char* __s)\n'
    '      |     ^                                               ~~~~~~~~~~~~~~~\n'
    "/usr/include/c++/13/ostream:675:5: note: candidate function template not viable: no known conversion from 'Point const' to 'const signed char *' for 2nd argument\n"
    '  675 |     operator<<(basic_ostream<char, _Traits>& __out, const signed char* __s)\n'
    '      |     ^                                               ~~~~~~~~~~~~~~~~~~~~~~\n'
    "/usr/include/c++/13/ostream:680:5: note: candidate function template not viable: no known conversion from 'Point const' to 'const unsigned char *' for 2nd argument\n"
    '  680 |     operator<<(basic_ostream<char, _Traits>& __out, const unsigned char* __s)\n'
    '      |     ^                                               ~~~~~~~~~~~~~~~~~~~~~~~~\n'
    "/usr/include/c++/13/bits/ostream.tcc:307:5: note: candidate function template not viable: no known conversion from 'Point const' to 'const char *' for 2nd argument\n"
    '  307 |     operator<<(basic_ostream<_CharT, _Traits>& __out, const char* __s)\n'
    '      |     ^                                                 ~~~~~~~~~~~~~~~\n'
    "/usr/include/c++/13/cstddef:124:5: note: candidate function template not viable: no known conversion from 'ostream' (aka 'basic_ostream<char>') to 'byte' for 1st argument\n"
    '  124 |     operator<<(byte __b, _IntegerType __shift) noexcept\n'
    '      |     ^          ~~~~~~~~\n'
    "/usr/include/c++/13/ostream:554:5: note: candidate template ignored: deduced conflicting types for parameter '_CharT' ('char' vs. 'Point')\n"
    '  554 |     operator<<(basic_ostream<_CharT, _Traits>& __out, _CharT __c)\n'
    '      |     ^\n'
    "/usr/include/c++/13/string_view:761:5: note: candidate template ignored: could not match 'basic_string_view<_CharT, _Traits>' against 'Point'\n"
    '  761 |     operator<<(basic_ostream<_CharT, _Traits>& __os,\n'
    '      |     ^\n'
    "/usr/include/c++/13/bits/basic_string.h:4032:5: note: candidate template ignored: could not match 'const basic_string<_CharT, _Traits, _Alloc>' against 'Point const'\n"
    ' 4032 |     operator<<(basic_ostream<_CharT, _Traits>& __os,\n'
    '      |     ^\n'
    "/usr/include/c++/13/ostream:645:5: note: candidate template ignored: could not match 'const _CharT *' against 'Point'\n"
    '  645 |     operator<<(basic_ostream<_CharT, _Traits>& __out, const _CharT* __s)\n'
    '      |     ^\n'
    "/usr/include/c++/13/ostream:801:5: note: candidate template ignored: requirement '__and_<std::is_class<std::basic_ostream<char, std::char_traits<char>> &>, std::__not_<std::is_same<std::basic_ostream<char, std::char_traits<char>> &, std::ios_base>>, std::is_convertible<std::basic_ostream<char, std::char_traits<char>> *, std::ios_base *>>::value' was not satisfied [with _Ostream = ostream &, _Tp = Point]\n"
    '  801 |     operator<<(_Ostream&& __os, const _Tp& __x)\n'
    '      |     ^\n'
    "/usr/include/c++/13/ostream:110:7: note: candidate function not viable: no known conversion from 'Point const' to '__ostream_type &(*)(__ostream_type &)' (aka 'basic_ostream<char, std::char_traits<char>> &(*)(basic_ostream<char, std::char_traits<char>> &)') for 1st argument\n"
    '  110 |       operator<<(__ostream_type& (*__pf)(__ostream_type&))\n'
    '      |       ^          ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~\n'
    "/usr/include/c++/13/ostream:119:7: note: candidate function not viable: no known conversion from 'Point const' to '__ios_type &(*)(__ios_type &)' (aka 'basic_ios<char, std::char_traits<char>> &(*)(basic_ios<char, std::char_traits<char>> &)') for 1st argument\n"
    '  119 |       operator<<(__ios_type& (*__pf)(__ios_type&))\n'
    '      |       ^          ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~\n'
    "/usr/include/c++/13/ostream:129:7: note: candidate function not viable: no known conversion from 'Point const' to 'ios_base &(*)(ios_base &)' for 1st argument\n"
    '  129 |       operator<<(ios_base& (*__pf) (ios_base&))\n'
    '      |       ^          ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~\n'
    "/usr/include/c++/13/ostream:168:7: note: candidate function not viable: no known conversion from 'Point const' to 'long' for 1st argument\n"
    '  168 |       operator<<(long __n)\n'
    '      |       ^          ~~~~~~~~\n'
    "/usr/include/c++/13/ostream:172:7: note: candidate function not viable: no known conversion from 'Point const' to 'unsigned long' for 1st argument\n"
    '  172 |       operator<<(unsigned long __n)\n'
    '      |       ^          ~~~~~~~~~~~~~~~~~\n'
    "/usr/include/c++/13/ostream:176:7: note: candidate function not viable: no known conversion from 'Point const' to 'bool' for 1st argument\n"
    '  176 |       operator<<(bool __n)\n'
    '      |       ^          ~~~~~~~~\n'
    "/usr/include/c++/13/ostream:180:7: note: candidate function not viable: no known conversion from 'Point const' to 'short' for 1st argument\n"
    '  180 |       operator<<(short __n);\n'
    '      |       ^          ~~~~~~~~~\n'
    "/usr/include/c++/13/ostream:183:7: note: candidate function not viable: no known conversion from 'Point const' to 'unsigned short' for 1st argument\n"
    '  183 |       operator<<(unsigned short __n)\n'
    '      |       ^          ~~~~~~~~~~~~~~~~~~\n'
    "/usr/include/c++/13/ostream:191:7: note: candidate function not viable: no known conversion from 'Point const' to 'int' for 1st argument\n"
    '  191 |       operator<<(int __n);\n'
    '      |       ^          ~~~~~~~\n'
    "/usr/include/c++/13/ostream:194:7: note: candidate function not viable: no known conversion from 'Point const' to 'unsigned int' for 1st argument\n"
    '  194 |       operator<<(unsigned int __n)\n'
    '      |       ^          ~~~~~~~~~~~~~~~~\n'
    "/usr/include/c++/13/ostream:203:7: note: candidate function not viable: no known conversion from 'Point const' to 'long long' for 1st argument\n"
    '  203 |       operator<<(long long __n)\n'
    '      |       ^          ~~~~~~~~~~~~~\n'
    "/usr/include/c++/13/ostream:207:7: note: candidate function not viable: no known conversion from 'Point const' to 'unsigned long long' for 1st argument\n"
    '  207 |       operator<<(unsigned long long __n)\n'
    '      |       ^          ~~~~~~~~~~~~~~~~~~~~~~\n'
    "/usr/include/c++/13/ostream:222:7: note: candidate function not viable: no known conversion from 'Point const' to 'double' for 1st argument\n"
    '  222 |       operator<<(double __f)\n'
    '      |       ^          ~~~~~~~~~~\n'
    "/usr/include/c++/13/ostream:226:7: note: candidate function not viable: no known conversion from 'Point const' to 'float' for 1st argument\n"
    '  226 |       operator<<(float __f)\n'
    '      |       ^          ~~~~~~~~~\n'
    "/usr/include/c++/13/ostream:234:7: note: candidate function not viable: no known conversion from 'Point const' to 'long double' for 1st argument\n"
    '  234 |       operator<<(long double __f)\n'
    '      |       ^          ~~~~~~~~~~~~~~~\n'
    "/usr/include/c++/13/ostream:292:7: note: candidate function not viable: no known conversion from 'Point const' to 'const void *' for 1st argument; take the address of the argument with &\n"
    '  292 |       operator<<(const void* __p)\n'
    '      |       ^          ~~~~~~~~~~~~~~~\n'
    "/usr/include/c++/13/ostream:297:7: note: candidate function not viable: no known conversion from 'Point const' to 'nullptr_t' (aka 'std::nullptr_t') for 1st argument\n"
    '  297 |       operator<<(nullptr_t)\n'
    '      |       ^          ~~~~~~~~~\n'
    "/usr/include/c++/13/ostream:330:7: note: candidate function not viable: no known conversion from 'Point const' to '__streambuf_type *' (aka 'basic_streambuf<char, std::char_traits<char>> *') for 1st argument\n"
    '  330 |       operator<<(__streambuf_type* __sb);\n'
    '      |       ^          ~~~~~~~~~~~~~~~~~~~~~~\n'
    '2 errors generated.\n'
)
GPP_OVERLOADS = (
    "ov.cpp: In function 'int main()':\n"
    "ov.cpp:20:9: error: no matching function for call to 'emit(Money&)'\n"
    '   20 |     emit(m);\n'
    '      |     ~~~~^~~\n'
    "ov.cpp:15:28: note: candidate: 'template<class T> void emit(const std::vector<T>&, T)'\n"
    '   15 | template <typename T> void emit(const std::vector<T>& v, T sep);\n'
    '      |                            ^~~~\n'
    'ov.cpp:15:28: note:   template argument deduction/substitution failed:\n'
    "ov.cpp:20:9: note:   'Money' is not derived from 'const std::vector<T>'\n"
    '   20 |     emit(m);\n'
    '      |     ~~~~^~~\n'
    "ov.cpp:7:6: note: candidate: 'void emit(int)'\n"
    '    7 | void emit(int v);\n'
    '      |      ^~~~\n'
    "ov.cpp:7:15: note:   no known conversion for argument 1 from 'Money' to 'int'\n"
    '    7 | void emit(int v);\n'
    '      |           ~~~~^\n'
    "ov.cpp:8:6: note: candidate: 'void emit(double)'\n"
    '    8 | void emit(double v);\n'
    '      |      ^~~~\n'
    "ov.cpp:8:18: note:   no known conversion for argument 1 from 'Money' to 'double'\n"
    '    8 | void emit(double v);\n'
    '      |           ~~~~~~~^\n'
    "ov.cpp:9:6: note: candidate: 'void emit(const char*)'\n"
    '    9 | void emit(const char* s);\n'
    '      |      ^~~~\n'
    "ov.cpp:9:23: note:   no known conversion for argument 1 from 'Money' to 'const char*'\n"
    '    9 | void emit(const char* s);\n'
    '      |           ~~~~~~~~~~~~^\n'
    "ov.cpp:10:6: note: candidate: 'void emit(const std::string&)'\n"
    '   10 | void emit(const std::string& s);\n'
    '      |      ^~~~\n'
    "ov.cpp:10:30: note:   no known conversion for argument 1 from 'Money' to 'const std::string&' {aka 'const std::__cxx11::basic_string<char>&'}\n"
    '   10 | void emit(const std::string& s);\n'
    '      |           ~~~~~~~~~~~~~~~~~~~^\n'
    "ov.cpp:11:6: note: candidate: 'void emit(const std::vector<int>&)'\n"
    '   11 | void emit(const std::vector<int>& v);\n'
    '      |      ^~~~\n'
    "ov.cpp:11:35: note:   no known conversion for argument 1 from 'Money' to 'const std::vector<int>&'\n"
    '   11 | void emit(const std::vector<int>& v);\n'
    '      |           ~~~~~~~~~~~~~~~~~~~~~~~~^\n'
    "ov.cpp:12:6: note: candidate: 'void emit(char)'\n"
    '   12 | void emit(char c);\n'
    '      |      ^~~~\n'
    "ov.cpp:12:16: note:   no known conversion for argument 1 from 'Money' to 'char'\n"
    '   12 | void emit(char c);\n'
    '      |           ~~~~~^\n'
    "ov.cpp:13:6: note: candidate: 'void emit(bool)'\n"
    '   13 | void emit(bool b);\n'
    '      |      ^~~~\n'
    "ov.cpp:13:16: note:   no known conversion for argument 1 from 'Money' to 'bool'\n"
    '   13 | void emit(bool b);\n'
    '      |           ~~~~~^\n'
    "ov.cpp:14:6: note: candidate: 'void emit(long int, int)'\n"
    '   14 | void emit(long v, int base);\n'
    '      |      ^~~~\n'
    'ov.cpp:14:6: note:   candidate expects 2 arguments, 1 provided\n'
    "ov.cpp:21:9: error: no matching function for call to 'emit(Tag&, int)'\n"
    '   21 |     emit(t, 3);\n'
    '      |     ~~~~^~~~~~\n'
    "ov.cpp:15:28: note: candidate: 'template<class T> void emit(const std::vector<T>&, T)'\n"
    '   15 | template <typename T> void emit(const std::vector<T>& v, T sep);\n'
    '      |                            ^~~~\n'
    'ov.cpp:15:28: note:   template argument deduction/substitution failed:\n'
    "ov.cpp:21:9: note:   'Tag' is not derived from 'const std::vector<T>'\n"
    '   21 |     emit(t, 3);\n'
    '      |     ~~~~^~~~~~\n'
    "ov.cpp:7:6: note: candidate: 'void emit(int)'\n"
    '    7 | void emit(int v);\n'
    '      |      ^~~~\n'
    'ov.cpp:7:6: note:   candidate expects 1 argument, 2 provided\n'
    "ov.cpp:8:6: note: candidate: 'void emit(double)'\n"
    '    8 | void emit(double v);\n'
    '      |      ^~~~\n'
    'ov.cpp:8:6: note:   candidate expects 1 argument, 2 provided\n'
    "ov.cpp:9:6: note: candidate: 'void emit(const char*)'\n"
    '    9 | void emit(const char* s);\n'
    '      |      ^~~~\n'
    'ov.cpp:9:6: note:   candidate expects 1 argument, 2 provided\n'
    "ov.cpp:10:6: note: candidate: 'void emit(const std::string&)'\n"
    '   10 | void emit(const std::string& s);\n'
    '      |      ^~~~\n'
    'ov.cpp:10:6: note:   candidate expects 1 argument, 2 provided\n'
    "ov.cpp:11:6: note: candidate: 'void emit(const std::vector<int>&)'\n"
    '   11 | void emit(const std::vector<int>& v);\n'
    '      |      ^~~~\n'
    'ov.cpp:11:6: note:   candidate expects 1 argument, 2 provided\n'
    "ov.cpp:12:6: note: candidate: 'void emit(char)'\n"
    '   12 | void emit(char c);\n'
    '      |      ^~~~\n'
    'ov.cpp:12:6: note:   candidate expects 1 argument, 2 provided\n'
    "ov.cpp:13:6: note: candidate: 'void emit(bool)'\n"
    '   13 | void emit(bool b);\n'
    '      |      ^~~~\n'
    'ov.cpp:13:6: note:   candidate expects 1 argument, 2 provided\n'
    "ov.cpp:14:6: note: candidate: 'void emit(long int, int)'\n"
    '   14 | void emit(long v, int base);\n'
    '      |      ^~~~\n'
    "ov.cpp:14:16: note:   no known conversion for argument 1 from 'Tag' to 'long int'\n"
    '   14 | void emit(long v, int base);\n'
    '      |           ~~~~~^\n'
)
CLANG_OVERLOADS = (
    "ov.cpp:20:5: error: no matching function for call to 'emit'\n"
    '   20 |     emit(m);\n'
    '      |     ^~~~\n'
    "ov.cpp:7:6: note: candidate function not viable: no known conversion from 'Money' to 'int' for 1st argument\n"
    '    7 | void emit(int v);\n'
    '      |      ^    ~~~~~\n'
    "ov.cpp:8:6: note: candidate function not viable: no known conversion from 'Money' to 'double' for 1st argument\n"
    '    8 | void emit(double v);\n'
    '      |      ^    ~~~~~~~~\n'
    "ov.cpp:9:6: note: candidate function not viable: no known conversion from 'Money' to 'const char *' for 1st argument\n"
    '    9 | void emit(const char* s);\n'
    '      |      ^    ~~~~~~~~~~~~~\n'
    "ov.cpp:10:6: note: candidate function not viable: no known conversion from 'Money' to 'const std::string' (aka 'const basic_string<char>') for 1st argument\n"
    '   10 | void emit(const std::string& s);\n'
    '      |      ^    ~~~~~~~~~~~~~~~~~~~~\n'
    "ov.cpp:11:6: note: candidate function not viable: no known conversion from 'Money' to 'const std::vector<int>' for 1st argument\n"
    '   11 | void emit(const std::vector<int>& v);\n'
    '      |      ^    ~~~~~~~~~~~~~~~~~~~~~~~~~\n'
    "ov.cpp:12:6: note: candidate function not viable: no known conversion from 'Money' to 'char' for 1st argument\n"
    '   12 | void emit(char c);\n'
    '      |      ^    ~~~~~~\n'
    "ov.cpp:13:6: note: candidate function not viable: no known conversion from 'Money' to 'bool' for 1st argument\n"
    '   13 | void emit(bool b);\n'
    '      |      ^    ~~~~~~\n'
    'ov.cpp:15:28: note: candidate function template not viable: requires 2 arguments, but 1 was provided\n'
    '   15 | template <typename T> void emit(const std::vector<T>& v, T sep);\n'
    '      |                            ^    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~\n'
    'ov.cpp:14:6: note: candidate function not viable: requires 2 arguments, but 1 was provided\n'
    '   14 | void emit(long v, int base);\n'
    '      |      ^    ~~~~~~~~~~~~~~~~\n'
    "ov.cpp:21:5: error: no matching function for call to 'emit'\n"
    '   21 |     emit(t, 3);\n'
    '      |     ^~~~\n'
    "ov.cpp:14:6: note: candidate function not viable: no known conversion from 'Tag' to 'long' for 1st argument\n"
    '   14 | void emit(long v, int base);\n'
    '      |      ^    ~~~~~~\n'
    "ov.cpp:15:28: note: candidate template ignored: could not match 'std::vector<T>' against 'Tag'\n"
    '   15 | template <typename T> void emit(const std::vector<T>& v, T sep);\n'
    '      |                            ^\n'
    "ov.cpp:13:6: note: candidate function not viable: requires single argument 'b', but 2 arguments were provided\n"
    '   13 | void emit(bool b);\n'
    '      |      ^    ~~~~~~\n'
    "ov.cpp:12:6: note: candidate function not viable: requires single argument 'c', but 2 arguments were provided\n"
    '   12 | void emit(char c);\n'
    '      |      ^    ~~~~~~\n'
    "ov.cpp:11:6: note: candidate function not viable: requires single argument 'v', but 2 arguments were provided\n"
    '   11 | void emit(const std::vector<int>& v);\n'
    '      |      ^    ~~~~~~~~~~~~~~~~~~~~~~~~~\n'
    "ov.cpp:10:6: note: candidate function not viable: requires single argument 's', but 2 arguments were provided\n"
    '   10 | void emit(const std::string& s);\n'
    '      |      ^    ~~~~~~~~~~~~~~~~~~~~\n'
    "ov.cpp:9:6: note: candidate function not viable: requires single argument 's', but 2 arguments were provided\n"
    '    9 | void emit(const char* s);\n'
    '      |      ^    ~~~~~~~~~~~~~\n'
    "ov.cpp:8:6: note: candidate function not viable: requires single argument 'v', but 2 arguments were provided\n"
    '    8 | void emit(double v);\n'
    '      |      ^    ~~~~~~~~\n'
    "ov.cpp:7:6: note: candidate function not viable: requires single argument 'v', but 2 arguments were provided\n"
    '    7 | void emit(int v);\n'
    '      |      ^    ~~~~~\n'
    '2 errors generated.\n'
)
GPP_TWO_CANDIDATES = (
    "ov2.cpp: In function 'int main()':\n"
    "ov2.cpp:4:32: error: no matching function for call to 'emit(Money&)'\n"
    '    4 | int main() { Money m{250}; emit(m); return 0; }\n'
    '      |                            ~~~~^~~\n'
    "ov2.cpp:2:6: note: candidate: 'void emit(int)'\n"
    '    2 | void emit(int v);\n'
    '      |      ^~~~\n'
    "ov2.cpp:2:15: note:   no known conversion for argument 1 from 'Money' to 'int'\n"
    '    2 | void emit(int v);\n'
    '      |           ~~~~^\n'
    "ov2.cpp:3:6: note: candidate: 'void emit(const char*)'\n"
    '    3 | void emit(const char* s);\n'
    '      |      ^~~~\n'
    "ov2.cpp:3:23: note:   no known conversion for argument 1 from 'Money' to 'const char*'\n"
    '    3 | void emit(const char* s);\n'
    '      |           ~~~~~~~~~~~~^\n'
)
CLANG_TWO_CANDIDATES = (
    "ov2.cpp:4:28: error: no matching function for call to 'emit'\n"
    '    4 | int main() { Money m{250}; emit(m); return 0; }\n'
    '      |                            ^~~~\n'
    "ov2.cpp:2:6: note: candidate function not viable: no known conversion from 'Money' to 'int' for 1st argument\n"
    '    2 | void emit(int v);\n'
    '      |      ^    ~~~~~\n'
    "ov2.cpp:3:6: note: candidate function not viable: no known conversion from 'Money' to 'const char *' for 1st argument\n"
    '    3 | void emit(const char* s);\n'
    '      |      ^    ~~~~~~~~~~~~~\n'
    '1 error generated.\n'
)

KEEP_CANDIDATES = 3
_PRE = r"^\S[^:]*?:\d+:(?:\d+:)? "
_O_HDR = re.compile(_PRE + r"(?:fatal error|error|warning|note|remark): ?")
_O_CAND = re.compile(_PRE + r"note: candidate")
_O_SUB = re.compile(_PRE + r"note:   \S")
_O_PREFIX = re.compile(r"^In file included from |^\s+from \S.*:\d+[,:]$|^\S[^:]*: In .*:$|^\S[^:]*:\d+:(?:\d+:)?   required from")
_O_STUB = re.compile(r"\[ctxprune: \+(\d+) more candidate notes omitted\]")
_O_GUTTER = re.compile(r"^\d+ \|")


def _o_body(l: str) -> bool:
    return (l[:1].isspace() and not _O_PREFIX.match(l)) or bool(_O_GUTTER.match(l))


def _o_records(lines: list[str]):
    """[(kind, start, end)] with kind cand | sub | other | line. Prefix lines (include chains, 'In function', 'required
    from') are glued to the record of the header that follows them; a sub-note is merged into the previous record."""
    recs, n, i, pending = [], len(lines), 0, None
    while i < n:
        l = lines[i]
        if _O_PREFIX.match(l):
            pending = i if pending is None else pending
            i += 1
        elif _O_HDR.match(l):
            start = i if pending is None else pending
            pending = None
            j = i + 1
            while j < n and _o_body(lines[j]):
                j += 1
            kind = "cand" if _O_CAND.match(l) else "sub" if _O_SUB.match(l) else "other"
            recs.append([kind, start, j])
            i = j
        else:
            if pending is not None:
                recs.append(["other", pending, i])
                pending = None
            recs.append(["line", i, i + 1])
            i += 1
    if pending is not None:
        recs.append(["other", pending, n])
    merged = []
    for r in recs:
        if r[0] == "sub" and merged:
            merged[-1][2] = r[2]
        else:
            merged.append(r)
    return merged


def cap_candidates(text: str) -> str:
    lines = text.split("\n")
    recs = _o_records(lines)
    out, k = [], 0
    while k < len(recs):
        if recs[k][0] != "cand":
            out += lines[recs[k][1]:recs[k][2]]
            k += 1
            continue
        run = []
        while k < len(recs) and recs[k][0] == "cand":
            run.append(recs[k])
            k += 1
        keep, drop = run[:KEEP_CANDIDATES], run[KEEP_CANDIDATES:]
        stub = f"[ctxprune: +{len(drop)} more candidate notes omitted]"
        if drop and sum(r[2] - r[1] for r in drop) > 1:
            for r in keep:
                out += lines[r[1]:r[2]]
            out.append(stub)
        else:
            for r in run:
                out += lines[r[1]:r[2]]
    return shrink_only(text, "\n".join(out))


def probes_220(text: str) -> dict:
    others, runs, plain_lines = [], [], []
    cur = None
    for l in text.split("\n"):
        if _O_CAND.match(l):
            if cur is None:
                cur = {"heads": [], "subs": [], "n": 0}
                runs.append(cur)
            cur["n"] += 1
            if cur["n"] <= 3:  # the claim says three, whatever the encoder's constant is
                cur["heads"].append(l)
        elif _O_SUB.match(l):
            if cur is not None and cur["n"] <= 3:
                cur["subs"].append(l)
        elif m := _O_STUB.fullmatch(l):
            if cur is None:
                cur = {"heads": [], "subs": [], "n": 0}
                runs.append(cur)
            cur["n"] += int(m.group(1))
        elif _O_HDR.match(l):
            others.append(l)
            cur = None
        elif _O_PREFIX.match(l) or _o_body(l):
            continue
        else:
            plain_lines.append(l)
            cur = None
    errors = [l for l in others if re.match(_PRE + r"(?:fatal )?error:", l)]
    return {"error lines": errors, "other notes and warnings": [l for l in others if l not in errors],
            "candidate runs: first three headers, their sub-notes, total count": [(r["heads"], r["subs"], r["n"]) for r in runs],
            "other output lines": plain_lines}


COMPOSITE_220 = cat(GPP_OVERLOADS, "[ 40%] Building CXX object CMakeFiles/app.dir/src/util.cpp.o\n", CLANG_OVERLOADS, "make[2]: *** [CMakeFiles/app.dir/build.make:76: CMakeFiles/app.dir/src/ov.cpp.o] Error 1\n", CLANG_OPERATOR_BIG)

case(
    220,
    claim="After each error, the first three 'note: candidate' records (with their sub-notes and echoed code) are kept and the rest of an unbroken candidate run is replaced by one '[ctxprune: +N more candidate notes omitted]' line; error lines, non-candidate notes, warnings and the total candidate count per run are unchanged.",
    loses="the signatures and 'no known conversion' reasons of every candidate beyond the third (the saved original keeps them), and the include-chain lines that preceded those candidates",
    encode=cap_candidates,
    probes=probes_220,
    fixtures=lambda: [
        Fixture("clang: missing operator<< for std::cout, two errors, 66 candidates (real)", CLANG_OPERATOR_BIG, True),
        Fixture("g++: 9 user overloads, two failing calls (real)", GPP_OVERLOADS, True),
        Fixture("clang: the same 9 overloads (real)", CLANG_OVERLOADS, True),
        Fixture("g++ then make noise then clang: errors at start, middle and end", COMPOSITE_220, True),
        Fixture("g++ with two candidates: nothing to cap", GPP_TWO_CANDIDATES, False),
        Fixture("clang with two candidates: nothing to cap", CLANG_TWO_CANDIDATES, False),
        Fixture("a plain compile error with no candidates", "e.c:4:20: error: use of undeclared identifier 'cnt'\n    4 |     printf(\"%d\\n\", cnt);\n      |                    ^\n1 error generated.\n", False),
        Fixture("text that looks like the stub, and a candidate-looking line with no echo", "[ctxprune: +5 more candidate notes omitted]\nx.cpp:1:1: note: candidate: foo\n", None),
    ],
    min_gain=0.3,
)



# ===============================================================================================================
# #221 ASan/TSan/UBSan/Valgrind: drop the shadow-byte dump and legend, UBSan's hex dump, BuildId noise, and hoist the
# ==PID== prefix. RULE: the error line, every stack frame (function and file:line), allocation/free sites and the
# summaries must survive; the probe reads headings and frames with the prefix and BuildId normalised away.

ASAN_HEAP_OVERFLOW = (
    '=================================================================\n'
    '==6477==ERROR: AddressSanitizer: heap-buffer-overflow on address 0x502000000020 at pc 0x55c9434b92da bp 0x7ffdfb03da60 sp 0x7ffdfb03da50\n'
    'READ of size 4 at 0x502000000020 thread T0\n'
    '    #0 0x55c9434b92d9 in sum /work/app/heap.c:12\n'
    '    #1 0x55c9434b937e in main /work/app/heap.c:19\n'
    '    #2 0x7f47e4a2a1c9 in __libc_start_call_main ../sysdeps/nptl/libc_start_call_main.h:58\n'
    '    #3 0x7f47e4a2a28a in __libc_start_main_impl ../csu/libc-start.c:360\n'
    '    #4 0x55c9434b9184 in _start (/work/app/heap_asan+0x1184) (BuildId: 0c28665f60ef2192cf36c16d45db04add9ec8625)\n'
    '\n'
    '0x502000000020 is located 0 bytes after 16-byte region [0x502000000010,0x502000000020)\n'
    'allocated by thread T0 here:\n'
    '    #0 0x7f47e4efd9c7 in malloc ../../../../src/libsanitizer/asan/asan_malloc_linux.cpp:69\n'
    '    #1 0x55c9434b9268 in make /work/app/heap.c:6\n'
    '    #2 0x55c9434b9305 in main /work/app/heap.c:17\n'
    '    #3 0x7f47e4a2a1c9 in __libc_start_call_main ../sysdeps/nptl/libc_start_call_main.h:58\n'
    '    #4 0x7f47e4a2a28a in __libc_start_main_impl ../csu/libc-start.c:360\n'
    '    #5 0x55c9434b9184 in _start (/work/app/heap_asan+0x1184) (BuildId: 0c28665f60ef2192cf36c16d45db04add9ec8625)\n'
    '\n'
    'SUMMARY: AddressSanitizer: heap-buffer-overflow /work/app/heap.c:12 in sum\n'
    'Shadow bytes around the buggy address:\n'
    '  0x501ffffffd80: 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00\n'
    '  0x501ffffffe00: 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00\n'
    '  0x501ffffffe80: 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00\n'
    '  0x501fffffff00: 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00\n'
    '  0x501fffffff80: 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00\n'
    '=>0x502000000000: fa fa 00 00[fa]fa fa fa fa fa fa fa fa fa fa fa\n'
    '  0x502000000080: fa fa fa fa fa fa fa fa fa fa fa fa fa fa fa fa\n'
    '  0x502000000100: fa fa fa fa fa fa fa fa fa fa fa fa fa fa fa fa\n'
    '  0x502000000180: fa fa fa fa fa fa fa fa fa fa fa fa fa fa fa fa\n'
    '  0x502000000200: fa fa fa fa fa fa fa fa fa fa fa fa fa fa fa fa\n'
    '  0x502000000280: fa fa fa fa fa fa fa fa fa fa fa fa fa fa fa fa\n'
    'Shadow byte legend (one shadow byte represents 8 application bytes):\n'
    '  Addressable:           00\n'
    '  Partially addressable: 01 02 03 04 05 06 07 \n'
    '  Heap left redzone:       fa\n'
    '  Freed heap region:       fd\n'
    '  Stack left redzone:      f1\n'
    '  Stack mid redzone:       f2\n'
    '  Stack right redzone:     f3\n'
    '  Stack after return:      f5\n'
    '  Stack use after scope:   f8\n'
    '  Global redzone:          f9\n'
    '  Global init order:       f6\n'
    '  Poisoned by user:        f7\n'
    '  Container overflow:      fc\n'
    '  Array cookie:            ac\n'
    '  Intra object redzone:    bb\n'
    '  ASan internal:           fe\n'
    '  Left alloca redzone:     ca\n'
    '  Right alloca redzone:    cb\n'
    '==6477==ABORTING\n'
)
ASAN_USE_AFTER_FREE = (
    '=================================================================\n'
    '==6518==ERROR: AddressSanitizer: heap-use-after-free on address 0x502000000030 at pc 0x7fb4a706cf85 bp 0x7ffda534db50 sp 0x7ffda534d2f8\n'
    'READ of size 2 at 0x502000000030 thread T0\n'
    '    #0 0x7fb4a706cf84 in puts ../../../../src/libsanitizer/sanitizer_common/sanitizer_common_interceptors.inc:1236\n'
    '    #1 0x55de1699d38e in main /work/app/uaf.c:21\n'
    '    #2 0x7fb4a6c2a1c9 in __libc_start_call_main ../sysdeps/nptl/libc_start_call_main.h:58\n'
    '    #3 0x7fb4a6c2a28a in __libc_start_main_impl ../csu/libc-start.c:360\n'
    '    #4 0x55de1699d1a4 in _start (/work/app/uaf_asan+0x11a4) (BuildId: d960d994e74becdbd7fc8c785be04b47ed2c566a)\n'
    '\n'
    '0x502000000030 is located 0 bytes inside of 16-byte region [0x502000000030,0x502000000040)\n'
    'freed by thread T0 here:\n'
    '    #0 0x7fb4a70fc4d8 in free ../../../../src/libsanitizer/asan/asan_malloc_linux.cpp:52\n'
    '    #1 0x55de1699d335 in drop /work/app/uaf.c:15\n'
    '    #2 0x55de1699d382 in main /work/app/uaf.c:20\n'
    '    #3 0x7fb4a6c2a1c9 in __libc_start_call_main ../sysdeps/nptl/libc_start_call_main.h:58\n'
    '    #4 0x7fb4a6c2a28a in __libc_start_main_impl ../csu/libc-start.c:360\n'
    '    #5 0x55de1699d1a4 in _start (/work/app/uaf_asan+0x11a4) (BuildId: d960d994e74becdbd7fc8c785be04b47ed2c566a)\n'
    '\n'
    'previously allocated by thread T0 here:\n'
    '    #0 0x7fb4a70fd9c7 in malloc ../../../../src/libsanitizer/asan/asan_malloc_linux.cpp:69\n'
    '    #1 0x55de1699d286 in push /work/app/uaf.c:8\n'
    '    #2 0x55de1699d372 in main /work/app/uaf.c:19\n'
    '    #3 0x7fb4a6c2a1c9 in __libc_start_call_main ../sysdeps/nptl/libc_start_call_main.h:58\n'
    '    #4 0x7fb4a6c2a28a in __libc_start_main_impl ../csu/libc-start.c:360\n'
    '    #5 0x55de1699d1a4 in _start (/work/app/uaf_asan+0x11a4) (BuildId: d960d994e74becdbd7fc8c785be04b47ed2c566a)\n'
    '\n'
    'SUMMARY: AddressSanitizer: heap-use-after-free ../../../../src/libsanitizer/sanitizer_common/sanitizer_common_interceptors.inc:1236 in puts\n'
    'Shadow bytes around the buggy address:\n'
    '  0x501ffffffd80: 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00\n'
    '  0x501ffffffe00: 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00\n'
    '  0x501ffffffe80: 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00\n'
    '  0x501fffffff00: 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00\n'
    '  0x501fffffff80: 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00\n'
    '=>0x502000000000: fa fa 00 00 fa fa[fd]fd fa fa fa fa fa fa fa fa\n'
    '  0x502000000080: fa fa fa fa fa fa fa fa fa fa fa fa fa fa fa fa\n'
    '  0x502000000100: fa fa fa fa fa fa fa fa fa fa fa fa fa fa fa fa\n'
    '  0x502000000180: fa fa fa fa fa fa fa fa fa fa fa fa fa fa fa fa\n'
    '  0x502000000200: fa fa fa fa fa fa fa fa fa fa fa fa fa fa fa fa\n'
    '  0x502000000280: fa fa fa fa fa fa fa fa fa fa fa fa fa fa fa fa\n'
    'Shadow byte legend (one shadow byte represents 8 application bytes):\n'
    '  Addressable:           00\n'
    '  Partially addressable: 01 02 03 04 05 06 07 \n'
    '  Heap left redzone:       fa\n'
    '  Freed heap region:       fd\n'
    '  Stack left redzone:      f1\n'
    '  Stack mid redzone:       f2\n'
    '  Stack right redzone:     f3\n'
    '  Stack after return:      f5\n'
    '  Stack use after scope:   f8\n'
    '  Global redzone:          f9\n'
    '  Global init order:       f6\n'
    '  Poisoned by user:        f7\n'
    '  Container overflow:      fc\n'
    '  Array cookie:            ac\n'
    '  Intra object redzone:    bb\n'
    '  ASan internal:           fe\n'
    '  Left alloca redzone:     ca\n'
    '  Right alloca redzone:    cb\n'
    '==6518==ABORTING\n'
)
TSAN_DATA_RACE = (
    '==================\n'
    'WARNING: ThreadSanitizer: data race (pid=6529)\n'
    '  Read of size 4 at 0x56279fe94014 by thread T2:\n'
    '    #0 work /work/app/race.c:4 (race_tsan+0x1283) (BuildId: ef765c0882b14aad671edb4ace7238328fb21b33)\n'
    '\n'
    '  Previous write of size 4 at 0x56279fe94014 by thread T1:\n'
    '    #0 work /work/app/race.c:4 (race_tsan+0x12a7) (BuildId: ef765c0882b14aad671edb4ace7238328fb21b33)\n'
    '\n'
    "  Location is global 'counter' of size 4 at 0x56279fe94014 (race_tsan+0x4014)\n"
    '\n'
    '  Thread T2 (tid=6532, running) created by main thread at:\n'
    '    #0 pthread_create ../../../../src/libsanitizer/tsan/tsan_interceptors_posix.cpp:1022 (libtsan.so.2+0x5ac1a) (BuildId: 2a13a7710e361d06f7babbea53065ca2be93f738)\n'
    '    #1 main /work/app/race.c:8 (race_tsan+0x131a) (BuildId: ef765c0882b14aad671edb4ace7238328fb21b33)\n'
    '\n'
    '  Thread T1 (tid=6531, finished) created by main thread at:\n'
    '    #0 pthread_create ../../../../src/libsanitizer/tsan/tsan_interceptors_posix.cpp:1022 (libtsan.so.2+0x5ac1a) (BuildId: 2a13a7710e361d06f7babbea53065ca2be93f738)\n'
    '    #1 main /work/app/race.c:7 (race_tsan+0x1300) (BuildId: ef765c0882b14aad671edb4ace7238328fb21b33)\n'
    '\n'
    'SUMMARY: ThreadSanitizer: data race /work/app/race.c:4 in work\n'
    '==================\n'
    'ThreadSanitizer: reported 1 warnings\n'
    '2000\n'
)
UBSAN_RUNTIME = (
    "ub.c:5:7: runtime error: signed integer overflow: 2147483647 + 1 cannot be represented in type 'int'\n"
    "ub.c:8:29: runtime error: index 6 out of bounds for type 'int [4]'\n"
    "ub.c:8:5: runtime error: load of address 0x7ffd1d65b8f8 with insufficient space for an object of type 'int'\n"
    '0x7ffd1d65b8f8: note: pointer points here\n'
    ' fd 7f 00 00  00 ab 92 73 02 38 b8 b1  00 00 00 00 00 00 00 00  38 ba 65 1d fd 7f 00 00  b0 b9 65 1d\n'
    '              ^ \n'
    '-2147483648 1938991872\n'
)
VALGRIND_OVERFLOW = (
    '==6486== Memcheck, a memory error detector\n'
    "==6486== Copyright (C) 2002-2022, and GNU GPL'd, by Julian Seward et al.\n"
    '==6486== Using Valgrind-3.22.0 and LibVEX; rerun with -h for copyright info\n'
    '==6486== Command: ./heap_plain\n'
    '==6486== \n'
    '==6486== Invalid read of size 4\n'
    '==6486==    at 0x1091DE: sum (heap.c:12)\n'
    '==6486==    by 0x10924A: main (heap.c:19)\n'
    "==6486==  Address 0x4a7b050 is 0 bytes after a block of size 16 alloc'd\n"
    '==6486==    at 0x4846828: malloc (in /usr/libexec/valgrind/vgpreload_memcheck-amd64-linux.so)\n'
    '==6486==    by 0x1091A8: make (heap.c:6)\n'
    '==6486==    by 0x109209: main (heap.c:17)\n'
    '==6486== \n'
    '6\n'
    '==6486== \n'
    '==6486== HEAP SUMMARY:\n'
    '==6486==     in use at exit: 0 bytes in 0 blocks\n'
    '==6486==   total heap usage: 2 allocs, 2 frees, 4,112 bytes allocated\n'
    '==6486== \n'
    '==6486== All heap blocks were freed -- no leaks are possible\n'
    '==6486== \n'
    '==6486== For lists of detected and suppressed errors, rerun with: -s\n'
    '==6486== ERROR SUMMARY: 1 errors from 1 contexts (suppressed: 0 from 0)\n'
)
VALGRIND_LEAKS = (
    '==6610== Memcheck, a memory error detector\n'
    "==6610== Copyright (C) 2002-2022, and GNU GPL'd, by Julian Seward et al.\n"
    '==6610== Using Valgrind-3.22.0 and LibVEX; rerun with -h for copyright info\n'
    '==6610== Command: ./leak_plain\n'
    '==6610== \n'
    '==6610== Invalid write of size 1\n'
    '==6610==    at 0x1091D7: dup_upper (leak.c:7)\n'
    '==6610==    by 0x109210: main (leak.c:12)\n'
    "==6610==  Address 0x4a7b045 is 0 bytes after a block of size 5 alloc'd\n"
    '==6610==    at 0x4846828: malloc (in /usr/libexec/valgrind/vgpreload_memcheck-amd64-linux.so)\n'
    '==6610==    by 0x1091AC: dup_upper (leak.c:6)\n'
    '==6610==    by 0x109210: main (leak.c:12)\n'
    '==6610== \n'
    '==6610== Conditional jump or move depends on uninitialised value(s)\n'
    '==6610==    at 0x109227: main (leak.c:15)\n'
    '==6610== \n'
    '==6610== Invalid read of size 1\n'
    '==6610==    at 0x484F234: strlen (in /usr/libexec/valgrind/vgpreload_memcheck-amd64-linux.so)\n'
    '==6610==    by 0x48ECCDC: puts (ioputs.c:35)\n'
    '==6610==    by 0x109243: main (leak.c:16)\n'
    "==6610==  Address 0x4a7b045 is 0 bytes after a block of size 5 alloc'd\n"
    '==6610==    at 0x4846828: malloc (in /usr/libexec/valgrind/vgpreload_memcheck-amd64-linux.so)\n'
    '==6610==    by 0x1091AC: dup_upper (leak.c:6)\n'
    '==6610==    by 0x109210: main (leak.c:12)\n'
    '==6610== \n'
    'HELLO\n'
    '==6610== \n'
    '==6610== HEAP SUMMARY:\n'
    '==6610==     in use at exit: 105 bytes in 2 blocks\n'
    '==6610==   total heap usage: 3 allocs, 1 frees, 4,201 bytes allocated\n'
    '==6610== \n'
    '==6610== 5 bytes in 1 blocks are definitely lost in loss record 1 of 2\n'
    '==6610==    at 0x4846828: malloc (in /usr/libexec/valgrind/vgpreload_memcheck-amd64-linux.so)\n'
    '==6610==    by 0x1091AC: dup_upper (leak.c:6)\n'
    '==6610==    by 0x109210: main (leak.c:12)\n'
    '==6610== \n'
    '==6610== 100 bytes in 1 blocks are definitely lost in loss record 2 of 2\n'
    '==6610==    at 0x4846828: malloc (in /usr/libexec/valgrind/vgpreload_memcheck-amd64-linux.so)\n'
    '==6610==    by 0x10921E: main (leak.c:13)\n'
    '==6610== \n'
    '==6610== LEAK SUMMARY:\n'
    '==6610==    definitely lost: 105 bytes in 2 blocks\n'
    '==6610==    indirectly lost: 0 bytes in 0 blocks\n'
    '==6610==      possibly lost: 0 bytes in 0 blocks\n'
    '==6610==    still reachable: 0 bytes in 0 blocks\n'
    '==6610==         suppressed: 0 bytes in 0 blocks\n'
    '==6610== \n'
    '==6610== Use --track-origins=yes to see where uninitialised values come from\n'
    '==6610== For lists of detected and suppressed errors, rerun with: -s\n'
    '==6610== ERROR SUMMARY: 5 errors from 5 contexts (suppressed: 0 from 0)\n'
)

_S_PID = re.compile(r"^==(\d+)==(?: |$)")
_S_SH_START = re.compile(r"^(?:==\d+== ?)?Shadow bytes around the buggy address:$")
_S_SH_ROW = re.compile(r"^(?:=>|  )0x[0-9a-f]+:(?: |\[)[0-9a-f]{2}")
_S_LEG_HEAD = re.compile(r"^Shadow byte legend \(")
_S_LEG = re.compile(r"^  [A-Z][A-Za-z ]+:\s+[0-9a-f]{2}")
_S_UB_NOTE = re.compile(r"^0x[0-9a-f]+: note: pointer points here$")
_S_HEXDUMP = re.compile(r"^ ?<?[0-9a-f]{2}>?(?: {1,2}<?[0-9a-f]{2}>?)* *$")
_S_CARET = re.compile(r"^ +\^ *$")
_S_BUILDID = re.compile(r" \(BuildId: [0-9a-f]+\)")
_S_STUB = re.compile(r"^\[ctxprune: shadow-byte dump and legend omitted \((\d+) lines\)\]$")
_S_HDR = re.compile(r"^\[pid (\d+)\]$")


def trim_sanitizer(text: str) -> str:
    lines = [_S_BUILDID.sub("", l) for l in text.split("\n")]
    out, i, n = [], 0, len(lines)
    while i < n:
        l = lines[i]
        if _S_SH_START.match(l):
            j = i + 1
            while j < n and _S_SH_ROW.match(lines[j]):
                j += 1
            if j > i + 1:
                if j < n and _S_LEG_HEAD.match(lines[j]):
                    j += 1
                    while j < n and _S_LEG.match(lines[j]):
                        j += 1
                out.append(f"[ctxprune: shadow-byte dump and legend omitted ({j - i} lines)]")
                i = j
                continue
        out.append(l)
        i += 1
        if _S_UB_NOTE.match(l):
            k = i
            while k < n and _S_HEXDUMP.match(lines[k]):
                k += 1
            if k > i:
                i = k + 1 if k < n and _S_CARET.match(lines[k]) else k
    pids = [m.group(1) for l in out if (m := _S_PID.match(l))]
    if len(pids) >= 4 and len(set(pids)) == 1 and not any(_S_HDR.match(l) for l in out):
        pid = pids[0]
        out = [f"[pid {pid}]"] + [l[len(m.group(0)):] if (m := _S_PID.match(l)) else l for l in out]
    return shrink_only(text, "\n".join(out))


_S_FRAME = [
    re.compile(r"^\s*#(\d+) 0x[0-9a-f]+ in (\S+)(?: (.*))?$"),  # ASan
    re.compile(r"^\s*#(\d+) (\S+) (\S+) \((\S+)\)$"),  # TSan
    re.compile(r"^\s*(?:at|by) 0x[0-9A-F]+: (\S+) \((.*)\)$"),  # valgrind
]


def probes_221(text: str) -> dict:
    pids, headings, cur = set(), [], None
    state = None  # 'rows' after the shadow header, 'legend' after the legend header
    for raw in text.split("\n"):
        l = _S_BUILDID.sub("", raw)
        while m := _S_PID.match(l):
            pids.add(m.group(1))
            l = l[len(m.group(0)):]
        if m := _S_HDR.match(l):
            pids.add(m.group(1))
            continue
        if _S_STUB.match(l):
            state = None
            continue
        if l.startswith("Shadow bytes around the buggy address:"):
            state = "rows"
            continue
        if l.startswith("Shadow byte legend ("):
            state = "legend"
            continue
        if state == "rows" and re.match(r"^(?:=>|  )0x[0-9a-f]+:(?: |\[)[0-9a-f]{2}", l):
            continue
        if state == "legend" and re.match(r"^  [A-Z][A-Za-z ]+:\s+[0-9a-f]{2}", l):
            continue
        state = None
        if re.match(r"^ ?<?[0-9a-f]{2}>?(?: {1,2}<?[0-9a-f]{2}>?)* *$", l) or re.match(r"^ +\^ *$", l):
            continue  # UBSan's raw bytes under 'pointer points here'
        for k, rx in enumerate(_S_FRAME):
            if m := rx.match(l):
                if cur is not None:
                    cur[1].append((m.group(2), m.group(3)) if k == 0 else (m.group(2), m.group(3), m.group(4)) if k == 1 else (m.group(1), m.group(2)))
                break
        else:
            if l.strip():
                cur = (l.strip(), [])
                headings.append(cur)
    return {"pid": sorted(pids), "headings with their stack frames": [(h, tuple(f)) for h, f in headings]}


COMPOSITE_221 = cat(
    "$ ./run_tests.sh\ntest_parse ... ok\ntest_sum ... FAILED\n", ASAN_HEAP_OVERFLOW, "test_free ... ok\n", ASAN_USE_AFTER_FREE, "exit status 1\n"
)

case(
    221,
    claim="Removes ASan's shadow-byte rows and legend, UBSan's raw-byte dump under 'pointer points here' and every '(BuildId: ...)' suffix, and hoists a single ==PID== valgrind prefix into one '[pid N]' line; every error headline, stack frame (function, file:line), allocation/free site, leak summary and SUMMARY line is unchanged.",
    loses="the shadow byte values around the faulting address (including the byte at the fault; the error type already names the class), the raw memory bytes UBSan prints, and binary build ids",
    encode=trim_sanitizer,
    probes=probes_221,
    fixtures=lambda: [
        Fixture("ASan 4-int heap overflow (real, paths shortened)", ASAN_HEAP_OVERFLOW, True),
        Fixture("ASan use-after-free with freed-by and allocated-by stacks (real)", ASAN_USE_AFTER_FREE, True),
        Fixture("TSan data race (real)", TSAN_DATA_RACE, True),
        Fixture("UBSan: overflow, out of bounds, pointer-points-here dump (real)", UBSAN_RUNTIME, True),
        Fixture("valgrind: invalid read, one ==PID== on every line (real)", VALGRIND_OVERFLOW, True),
        Fixture("valgrind --leak-check=full with program output interleaved (real)", VALGRIND_LEAKS, True),
        Fixture("test log: failing ASan run in the middle, another at the end", COMPOSITE_221, True),
        Fixture("program output that merely mentions ==5==", "run: ==5== is the marker\nok\nSHADOW is a word\n", False),
        Fixture("two pids (trace-children) are not hoisted, a truncated shadow section", "==1== Memcheck\n==2== Memcheck\n==1== Invalid read of size 4\n==2== Invalid write of size 1\nShadow bytes around the buggy address:\n  0x501ffffffd80: 00 00 00 00\n", None),
    ],
    min_gain=0.15,
)



# ===============================================================================================================
# #223 gradle dependencies: print identical configuration trees once, headed by every configuration name that
# shares them. RULE (weaker than claimed on a plain Java project, see the real fixture): the probe maps
# (project, configuration) -> its exact tree lines, so 'which version of X is on runtimeClasspath' is answerable from
# the folded form; descriptions of the folded-away configurations are the only loss.

GRADLE_DEPS_REAL = (
    '\n'
    '------------------------------------------------------------\n'
    "Project ':app'\n"
    '------------------------------------------------------------\n'
    '\n'
    "annotationProcessor - Annotation processors and their dependencies for source set 'main'.\n"
    'No dependencies\n'
    '\n'
    "compileClasspath - Compile classpath for source set 'main'.\n"
    '+--- project :lib\n'
    '|    \\--- org.apache.commons:commons-lang3:3.13.0\n'
    '+--- org.slf4j:slf4j-api:2.0.9\n'
    '\\--- com.google.guava:guava:31.0-jre\n'
    '     +--- com.google.guava:failureaccess:1.0.1 FAILED\n'
    '     +--- com.google.guava:listenablefuture:9999.0-empty-to-avoid-conflict-with-guava FAILED\n'
    '     +--- com.google.code.findbugs:jsr305:3.0.2 FAILED\n'
    '     +--- org.checkerframework:checker-qual:3.12.0\n'
    '     +--- com.google.errorprone:error_prone_annotations:2.7.1\n'
    '     \\--- com.google.j2objc:j2objc-annotations:1.3\n'
    '\n'
    "compileOnly - Compile-only dependencies for the 'main' feature. (n)\n"
    'No dependencies\n'
    '\n'
    'default - Configuration for default artifacts. (n)\n'
    'No dependencies\n'
    '\n'
    "implementation - Implementation dependencies for the 'main' feature. (n)\n"
    '+--- project lib (n)\n'
    '+--- org.slf4j:slf4j-api:2.0.9 (n)\n'
    '\\--- com.google.guava:guava:31.0-jre (n)\n'
    '\n'
    'mainSourceElements - List of source directories contained in the Main SourceSet. (n)\n'
    'No dependencies\n'
    '\n'
    "runtimeClasspath - Runtime classpath of source set 'main'.\n"
    '+--- project :lib\n'
    '|    +--- org.apache.commons:commons-lang3:3.13.0\n'
    '|    \\--- com.google.guava:guava:32.1.3-jre FAILED\n'
    '+--- org.slf4j:slf4j-api:2.0.9\n'
    '+--- com.google.guava:guava:31.0-jre -> 32.1.3-jre FAILED\n'
    '\\--- ch.qos.logback:logback-classic:1.4.11 FAILED\n'
    '\n'
    "runtimeElements - Runtime elements for the 'main' feature. (n)\n"
    'No dependencies\n'
    '\n'
    "runtimeOnly - Runtime-only dependencies for the 'main' feature. (n)\n"
    '\\--- ch.qos.logback:logback-classic:1.4.11 (n)\n'
    '\n'
    "testAnnotationProcessor - Annotation processors and their dependencies for source set 'test'.\n"
    'No dependencies\n'
    '\n'
    "testCompileClasspath - Compile classpath for source set 'test'.\n"
    '+--- project :lib\n'
    '|    \\--- org.apache.commons:commons-lang3:3.13.0\n'
    '+--- org.slf4j:slf4j-api:2.0.9\n'
    '+--- com.google.guava:guava:31.0-jre\n'
    '|    +--- com.google.guava:failureaccess:1.0.1 FAILED\n'
    '|    +--- com.google.guava:listenablefuture:9999.0-empty-to-avoid-conflict-with-guava FAILED\n'
    '|    +--- com.google.code.findbugs:jsr305:3.0.2 FAILED\n'
    '|    +--- org.checkerframework:checker-qual:3.12.0\n'
    '|    +--- com.google.errorprone:error_prone_annotations:2.7.1\n'
    '|    \\--- com.google.j2objc:j2objc-annotations:1.3\n'
    '\\--- org.junit.jupiter:junit-jupiter:5.10.0 FAILED\n'
    '\n'
    "testCompileOnly - Compile only dependencies for source set 'test'. (n)\n"
    'No dependencies\n'
    '\n'
    "testImplementation - Implementation only dependencies for source set 'test'. (n)\n"
    '\\--- org.junit.jupiter:junit-jupiter:5.10.0 (n)\n'
    '\n'
    "testRuntimeClasspath - Runtime classpath of source set 'test'.\n"
    '+--- project :lib\n'
    '|    +--- org.apache.commons:commons-lang3:3.13.0\n'
    '|    \\--- com.google.guava:guava:32.1.3-jre FAILED\n'
    '+--- org.slf4j:slf4j-api:2.0.9\n'
    '+--- com.google.guava:guava:31.0-jre -> 32.1.3-jre FAILED\n'
    '+--- ch.qos.logback:logback-classic:1.4.11 FAILED\n'
    '\\--- org.junit.jupiter:junit-jupiter:5.10.0 FAILED\n'
    '\n'
    "testRuntimeOnly - Runtime only dependencies for source set 'test'. (n)\n"
    'No dependencies\n'
    '\n'
    '(*) - Indicates repeated occurrences of a transitive dependency subtree. Gradle expands transitive dependency subtrees only once per project; repeat occurrences only display the root of the subtree, followed by this annotation.\n'
    '\n'
    '(n) - A dependency or dependency configuration that cannot be resolved.\n'
    '\n'
    'A web-based, searchable dependency report is available by adding the --scan option.\n'
)

_D_PROJECT = re.compile(r"^(?:Root project|Project) '")
_D_HEADER = re.compile(r"^([A-Za-z][\w.-]*(?:, [A-Za-z][\w.-]*)*)(?: - (.*))?$")
_D_BODY_FIRST = re.compile(r"^(?:No dependencies|[+\\]--- .*)$")


def _d_blocks(lines):
    """[(header index, end index exclusive, names, description, body tuple)] for each configuration block."""
    out, i, n = [], 0, len(lines)
    while i < n:
        m = _D_HEADER.match(lines[i])
        if m and i + 1 < n and _D_BODY_FIRST.match(lines[i + 1]) and not _D_PROJECT.match(lines[i]):
            j = i + 1
            while j < n and lines[j].strip():
                j += 1
            out.append((i, j, m.group(1).split(", "), m.group(2), tuple(lines[i + 1:j])))
            i = j
        else:
            i += 1
    return out


def fold_configurations(text: str) -> str:
    lines = text.split("\n")
    blocks = _d_blocks(lines)
    project_of = {}
    proj = None
    starts = {b[0]: b for b in blocks}
    for i, l in enumerate(lines):
        if _D_PROJECT.match(l):
            proj = l
        if i in starts:
            project_of[i] = proj
    groups: dict = {}
    for b in blocks:
        groups.setdefault((project_of[b[0]], b[4]), []).append(b)
    out, i, n = [], 0, len(lines)
    while i < n:
        b = starts.get(i)
        if b is None:
            out.append(lines[i])
            i += 1
            continue
        grp = groups[(project_of[i], b[4])]
        if grp[0] is not b:  # a repeat: drop it together with the blank line after it
            i = b[1] + 1 if b[1] < n and not lines[b[1]].strip() else b[1]
            continue
        names = [nm for g in grp for nm in g[2]]
        head = ", ".join(names) + (f" - {b[3]}" if b[3] is not None else "")
        out += [head] + list(b[4])
        i = b[1]
    return shrink_only(text, "\n".join(out))


def probes_223(text: str) -> dict:
    lines = text.split("\n")
    trees, others, proj = {}, [], None
    skip = set()
    for h, e, names, _desc, body in _d_blocks(lines):
        skip.update(range(h, e))
    cur_proj_at = {}
    for i, l in enumerate(lines):
        if _D_PROJECT.match(l):
            proj = l
        cur_proj_at[i] = proj
    for h, e, names, _desc, body in _d_blocks(lines):
        for nm in names:
            trees[(cur_proj_at[h], nm)] = body
    others = [l for i, l in enumerate(lines) if i not in skip and l.strip()]
    return {"tree per (project, configuration)": sorted(trees.items(), key=lambda kv: (str(kv[0][0]), kv[0][1])),
            "non-tree lines": others}


def _android_like() -> str:
    """Generated to the shape of `gradlew :app:dependencies` for an Android app module (reconstructed from memory):
    debug/release x compile/runtime x main/unitTest/androidTest configurations, most of them with the same tree."""
    core = ["+--- org.jetbrains.kotlin:kotlin-stdlib:1.9.22", "|    +--- org.jetbrains:annotations:13.0", "|    \\--- org.jetbrains.kotlin:kotlin-stdlib-common:1.9.22 (c)",
            "+--- androidx.core:core-ktx:1.12.0", "|    +--- androidx.annotation:annotation:1.6.0", "|    \\--- androidx.core:core:1.12.0 (*)",
            "+--- androidx.appcompat:appcompat:1.6.1", "|    +--- androidx.activity:activity:1.6.0 -> 1.8.0", "|    \\--- androidx.core:core:1.9.0 -> 1.12.0 (*)",
            "\\--- com.squareup.okhttp3:okhttp:4.12.0", "     +--- com.squareup.okio:okio:3.6.0", "     \\--- org.jetbrains.kotlin:kotlin-stdlib:1.8.21 -> 1.9.22 (*)"]
    test = core + ["+--- junit:junit:4.13.2", "|    \\--- org.hamcrest:hamcrest-core:1.3", "\\--- org.mockito:mockito-core:5.7.0"]
    android = core + ["+--- androidx.test.ext:junit:1.1.5", "\\--- androidx.test.espresso:espresso-core:3.5.1"]
    spec = [
        ("debugCompileClasspath", "Resolved configuration for compilation for variant: debug", core),
        ("debugRuntimeClasspath", "Resolved configuration for runtime for variant: debug", core),
        ("debugUnitTestCompileClasspath", "Resolved configuration for compilation for variant: debugUnitTest", test),
        ("debugUnitTestRuntimeClasspath", "Resolved configuration for runtime for variant: debugUnitTest", test),
        ("debugAndroidTestCompileClasspath", "Resolved configuration for compilation for variant: debugAndroidTest", android),
        ("debugAndroidTestRuntimeClasspath", "Resolved configuration for runtime for variant: debugAndroidTest", android),
        ("releaseCompileClasspath", "Resolved configuration for compilation for variant: release", core),
        ("releaseRuntimeClasspath", "Resolved configuration for runtime for variant: release", core),
        ("releaseUnitTestCompileClasspath", "Resolved configuration for compilation for variant: releaseUnitTest", test),
        ("releaseUnitTestRuntimeClasspath", "Resolved configuration for runtime for variant: releaseUnitTest", test),
        ("debugAnnotationProcessorClasspath", "Resolved configuration for annotation-processor for variant: debug", None),
        ("releaseAnnotationProcessorClasspath", "Resolved configuration for annotation-processor for variant: release", None),
        ("debugUnitTestAnnotationProcessorClasspath", "Resolved configuration for annotation-processor for variant: debugUnitTest", None),
        ("debugAndroidTestAnnotationProcessorClasspath", "Resolved configuration for annotation-processor for variant: debugAndroidTest", None),
        ("kotlinCompilerClasspath", None, ["\\--- org.jetbrains.kotlin:kotlin-compiler-embeddable:1.9.22", "     +--- org.jetbrains.kotlin:kotlin-stdlib:1.9.22", "     \\--- org.jetbrains.kotlin:kotlin-script-runtime:1.9.22"]),
    ]
    out = ["------------------------------------------------------------", "Project ':app'", "------------------------------------------------------------", ""]
    for name, desc, body in spec:
        out.append(name + (f" - {desc}" if desc else ""))
        out += body if body else ["No dependencies"]
        out.append("")
    out += ["(c) - A dependency constraint, not a dependency. The dependency affected by the constraint occurs elsewhere in the tree.",
            "(*) - Indicates repeated occurrences of a transitive dependency subtree. Dependencies are omitted (listed previously)", "",
            "A web-based, searchable dependency report is available by adding the --scan option.", "", "BUILD SUCCESSFUL in 2s", "1 actionable task: 1 executed", ""]
    return "\n".join(out)


ANDROID_DEPS = _android_like()

case(
    223,
    claim="Configuration blocks of 'gradle dependencies' whose dependency trees are line-for-line identical within a project are printed once under 'name1, name2, ... - first description'; the exact tree of every (project, configuration), including version arrows, (*), (c) and FAILED markers, and every line outside the trees are unchanged.",
    loses="the description text of every configuration after the first in a folded group, and the position of the later configurations in the report",
    encode=fold_configurations,
    probes=probes_223,
    fixtures=lambda: [
        Fixture("Android-style :app report, 15 configurations, 5 distinct trees (generated, reconstructed from memory)", ANDROID_DEPS, True),
        Fixture("plain Java app + lib project: only the 'No dependencies' configurations repeat (real, Gradle 8.14)", GRADLE_DEPS_REAL, True),
        Fixture("two projects: identical trees in different projects must not merge", "------------------------------------------------------------\nProject ':a'\n------------------------------------------------------------\n\nx - X\n+--- g:h:1\n|    \\--- g:i:2\n\\--- g:j:3\n\ny - Y\n+--- g:h:1\n|    \\--- g:i:2\n\\--- g:j:3\n\nz - Z\n+--- g:h:1\n|    \\--- g:i:2\n\\--- g:j:3\n\n------------------------------------------------------------\nProject ':b'\n------------------------------------------------------------\n\nx - X\n+--- g:h:1\n|    \\--- g:i:2\n\\--- g:j:3\n\n", True),
        Fixture("every configuration distinct", "compileClasspath - c\n\\--- a:b:1\n\nruntimeClasspath - r\n\\--- a:b:2\n\n", False),
        Fixture("not a dependency report", "BUILD SUCCESSFUL in 2s\n1 actionable task: 1 executed\n", False),
        Fixture("a header that already lists several names", "a, b - folded earlier\n\\--- g:h:1\n\nc - other\n\\--- g:h:2\n", None),
    ],
    min_gain=0.1,
)



# ===============================================================================================================
# #224 Maven reactor stanzas and Maven/Gradle failure footers. Per module Maven prints a banner, 'Building', a
# '--- plugin:version:goal (id) @ module ---' header per goal and no-op results ('Nothing to compile'); failures end
# with constant -e/-X/help advice. Gradle ends with a constant '* Try:' list and a deprecation advisory.
# RULE: modules in order, the goals each ran, every WARNING/ERROR/test line, the Reactor Summary, the failing module
# and goal, the Gradle task and error text must survive. Real captures: mvn verify over a 10-module reactor (all
# green, and with mod-7 broken) and a failing gradle build.

MVN_REACTOR_OK = (
    '[INFO] Scanning for projects...\n'
    '[INFO] ------------------------------------------------------------------------\n'
    '[INFO] Reactor Build Order:\n'
    '[INFO] \n'
    '[INFO] parent                                                             [pom]\n'
    '[INFO] mod-1                                                              [jar]\n'
    '[INFO] mod-2                                                              [jar]\n'
    '[INFO] mod-3                                                              [jar]\n'
    '[INFO] mod-4                                                              [jar]\n'
    '[INFO] mod-5                                                              [jar]\n'
    '[INFO] mod-6                                                              [jar]\n'
    '[INFO] mod-7                                                              [jar]\n'
    '[INFO] mod-8                                                              [jar]\n'
    '[INFO] mod-9                                                              [jar]\n'
    '[INFO] mod-10                                                             [jar]\n'
    '[INFO] \n'
    '[INFO] -------------------------< com.example:parent >-------------------------\n'
    '[INFO] Building parent 1.0.0                                             [1/11]\n'
    '[INFO]   from pom.xml\n'
    '[INFO] --------------------------------[ pom ]---------------------------------\n'
    '[INFO] \n'
    '[INFO] -------------------------< com.example:mod-1 >--------------------------\n'
    '[INFO] Building mod-1 1.0.0                                              [2/11]\n'
    '[INFO]   from mod-1/pom.xml\n'
    '[INFO] --------------------------------[ jar ]---------------------------------\n'
    '[INFO] \n'
    '[INFO] --- resources:3.3.1:resources (default-resources) @ mod-1 ---\n'
    '[INFO] skip non existing resourceDirectory /work/app/mod-1/src/main/resources\n'
    '[INFO] \n'
    '[INFO] --- compiler:3.13.0:compile (default-compile) @ mod-1 ---\n'
    '[INFO] Nothing to compile - all classes are up to date.\n'
    '[INFO] \n'
    '[INFO] --- resources:3.3.1:testResources (default-testResources) @ mod-1 ---\n'
    '[INFO] skip non existing resourceDirectory /work/app/mod-1/src/test/resources\n'
    '[INFO] \n'
    '[INFO] --- compiler:3.13.0:testCompile (default-testCompile) @ mod-1 ---\n'
    '[INFO] No sources to compile\n'
    '[INFO] \n'
    '[INFO] --- surefire:3.2.5:test (default-test) @ mod-1 ---\n'
    '[INFO] No tests to run.\n'
    '[INFO] \n'
    '[INFO] --- jar:3.4.1:jar (default-jar) @ mod-1 ---\n'
    '[INFO] \n'
    '[INFO] -------------------------< com.example:mod-2 >--------------------------\n'
    '[INFO] Building mod-2 1.0.0                                              [3/11]\n'
    '[INFO]   from mod-2/pom.xml\n'
    '[INFO] --------------------------------[ jar ]---------------------------------\n'
    '[INFO] \n'
    '[INFO] --- resources:3.3.1:resources (default-resources) @ mod-2 ---\n'
    '[INFO] skip non existing resourceDirectory /work/app/mod-2/src/main/resources\n'
    '[INFO] \n'
    '[INFO] --- compiler:3.13.0:compile (default-compile) @ mod-2 ---\n'
    '[INFO] Nothing to compile - all classes are up to date.\n'
    '[INFO] \n'
    '[INFO] --- resources:3.3.1:testResources (default-testResources) @ mod-2 ---\n'
    '[INFO] skip non existing resourceDirectory /work/app/mod-2/src/test/resources\n'
    '[INFO] \n'
    '[INFO] --- compiler:3.13.0:testCompile (default-testCompile) @ mod-2 ---\n'
    '[INFO] No sources to compile\n'
    '[INFO] \n'
    '[INFO] --- surefire:3.2.5:test (default-test) @ mod-2 ---\n'
    '[INFO] No tests to run.\n'
    '[INFO] \n'
    '[INFO] --- jar:3.4.1:jar (default-jar) @ mod-2 ---\n'
    '[INFO] \n'
    '[INFO] -------------------------< com.example:mod-3 >--------------------------\n'
    '[INFO] Building mod-3 1.0.0                                              [4/11]\n'
    '[INFO]   from mod-3/pom.xml\n'
    '[INFO] --------------------------------[ jar ]---------------------------------\n'
    '[INFO] \n'
    '[INFO] --- resources:3.3.1:resources (default-resources) @ mod-3 ---\n'
    '[INFO] skip non existing resourceDirectory /work/app/mod-3/src/main/resources\n'
    '[INFO] \n'
    '[INFO] --- compiler:3.13.0:compile (default-compile) @ mod-3 ---\n'
    '[INFO] Nothing to compile - all classes are up to date.\n'
    '[INFO] \n'
    '[INFO] --- resources:3.3.1:testResources (default-testResources) @ mod-3 ---\n'
    '[INFO] skip non existing resourceDirectory /work/app/mod-3/src/test/resources\n'
    '[INFO] \n'
    '[INFO] --- compiler:3.13.0:testCompile (default-testCompile) @ mod-3 ---\n'
    '[INFO] No sources to compile\n'
    '[INFO] \n'
    '[INFO] --- surefire:3.2.5:test (default-test) @ mod-3 ---\n'
    '[INFO] No tests to run.\n'
    '[INFO] \n'
    '[INFO] --- jar:3.4.1:jar (default-jar) @ mod-3 ---\n'
    '[INFO] \n'
    '[INFO] -------------------------< com.example:mod-4 >--------------------------\n'
    '[INFO] Building mod-4 1.0.0                                              [5/11]\n'
    '[INFO]   from mod-4/pom.xml\n'
    '[INFO] --------------------------------[ jar ]---------------------------------\n'
    '[INFO] \n'
    '[INFO] --- resources:3.3.1:resources (default-resources) @ mod-4 ---\n'
    '[INFO] skip non existing resourceDirectory /work/app/mod-4/src/main/resources\n'
    '[INFO] \n'
    '[INFO] --- compiler:3.13.0:compile (default-compile) @ mod-4 ---\n'
    '[INFO] Nothing to compile - all classes are up to date.\n'
    '[INFO] \n'
    '[INFO] --- resources:3.3.1:testResources (default-testResources) @ mod-4 ---\n'
    '[INFO] skip non existing resourceDirectory /work/app/mod-4/src/test/resources\n'
    '[INFO] \n'
    '[INFO] --- compiler:3.13.0:testCompile (default-testCompile) @ mod-4 ---\n'
    '[INFO] No sources to compile\n'
    '[INFO] \n'
    '[INFO] --- surefire:3.2.5:test (default-test) @ mod-4 ---\n'
    '[INFO] No tests to run.\n'
    '[INFO] \n'
    '[INFO] --- jar:3.4.1:jar (default-jar) @ mod-4 ---\n'
    '[INFO] \n'
    '[INFO] -------------------------< com.example:mod-5 >--------------------------\n'
    '[INFO] Building mod-5 1.0.0                                              [6/11]\n'
    '[INFO]   from mod-5/pom.xml\n'
    '[INFO] --------------------------------[ jar ]---------------------------------\n'
    '[INFO] \n'
    '[INFO] --- resources:3.3.1:resources (default-resources) @ mod-5 ---\n'
    '[INFO] skip non existing resourceDirectory /work/app/mod-5/src/main/resources\n'
    '[INFO] \n'
    '[INFO] --- compiler:3.13.0:compile (default-compile) @ mod-5 ---\n'
    '[INFO] Nothing to compile - all classes are up to date.\n'
    '[INFO] \n'
    '[INFO] --- resources:3.3.1:testResources (default-testResources) @ mod-5 ---\n'
    '[INFO] skip non existing resourceDirectory /work/app/mod-5/src/test/resources\n'
    '[INFO] \n'
    '[INFO] --- compiler:3.13.0:testCompile (default-testCompile) @ mod-5 ---\n'
    '[INFO] No sources to compile\n'
    '[INFO] \n'
    '[INFO] --- surefire:3.2.5:test (default-test) @ mod-5 ---\n'
    '[INFO] No tests to run.\n'
    '[INFO] \n'
    '[INFO] --- jar:3.4.1:jar (default-jar) @ mod-5 ---\n'
    '[INFO] \n'
    '[INFO] -------------------------< com.example:mod-6 >--------------------------\n'
    '[INFO] Building mod-6 1.0.0                                              [7/11]\n'
    '[INFO]   from mod-6/pom.xml\n'
    '[INFO] --------------------------------[ jar ]---------------------------------\n'
    '[INFO] \n'
    '[INFO] --- resources:3.3.1:resources (default-resources) @ mod-6 ---\n'
    '[INFO] skip non existing resourceDirectory /work/app/mod-6/src/main/resources\n'
    '[INFO] \n'
    '[INFO] --- compiler:3.13.0:compile (default-compile) @ mod-6 ---\n'
    '[INFO] Nothing to compile - all classes are up to date.\n'
    '[INFO] \n'
    '[INFO] --- resources:3.3.1:testResources (default-testResources) @ mod-6 ---\n'
    '[INFO] skip non existing resourceDirectory /work/app/mod-6/src/test/resources\n'
    '[INFO] \n'
    '[INFO] --- compiler:3.13.0:testCompile (default-testCompile) @ mod-6 ---\n'
    '[INFO] No sources to compile\n'
    '[INFO] \n'
    '[INFO] --- surefire:3.2.5:test (default-test) @ mod-6 ---\n'
    '[INFO] No tests to run.\n'
    '[INFO] \n'
    '[INFO] --- jar:3.4.1:jar (default-jar) @ mod-6 ---\n'
    '[INFO] \n'
    '[INFO] -------------------------< com.example:mod-7 >--------------------------\n'
    '[INFO] Building mod-7 1.0.0                                              [8/11]\n'
    '[INFO]   from mod-7/pom.xml\n'
    '[INFO] --------------------------------[ jar ]---------------------------------\n'
    '[INFO] \n'
    '[INFO] --- resources:3.3.1:resources (default-resources) @ mod-7 ---\n'
    '[INFO] skip non existing resourceDirectory /work/app/mod-7/src/main/resources\n'
    '[INFO] \n'
    '[INFO] --- compiler:3.13.0:compile (default-compile) @ mod-7 ---\n'
    '[INFO] Nothing to compile - all classes are up to date.\n'
    '[INFO] \n'
    '[INFO] --- resources:3.3.1:testResources (default-testResources) @ mod-7 ---\n'
    '[INFO] skip non existing resourceDirectory /work/app/mod-7/src/test/resources\n'
    '[INFO] \n'
    '[INFO] --- compiler:3.13.0:testCompile (default-testCompile) @ mod-7 ---\n'
    '[INFO] No sources to compile\n'
    '[INFO] \n'
    '[INFO] --- surefire:3.2.5:test (default-test) @ mod-7 ---\n'
    '[INFO] No tests to run.\n'
    '[INFO] \n'
    '[INFO] --- jar:3.4.1:jar (default-jar) @ mod-7 ---\n'
    '[INFO] \n'
    '[INFO] -------------------------< com.example:mod-8 >--------------------------\n'
    '[INFO] Building mod-8 1.0.0                                              [9/11]\n'
    '[INFO]   from mod-8/pom.xml\n'
    '[INFO] --------------------------------[ jar ]---------------------------------\n'
    '[INFO] \n'
    '[INFO] --- resources:3.3.1:resources (default-resources) @ mod-8 ---\n'
    '[INFO] skip non existing resourceDirectory /work/app/mod-8/src/main/resources\n'
    '[INFO] \n'
    '[INFO] --- compiler:3.13.0:compile (default-compile) @ mod-8 ---\n'
    '[INFO] Nothing to compile - all classes are up to date.\n'
    '[INFO] \n'
    '[INFO] --- resources:3.3.1:testResources (default-testResources) @ mod-8 ---\n'
    '[INFO] skip non existing resourceDirectory /work/app/mod-8/src/test/resources\n'
    '[INFO] \n'
    '[INFO] --- compiler:3.13.0:testCompile (default-testCompile) @ mod-8 ---\n'
    '[INFO] No sources to compile\n'
    '[INFO] \n'
    '[INFO] --- surefire:3.2.5:test (default-test) @ mod-8 ---\n'
    '[INFO] No tests to run.\n'
    '[INFO] \n'
    '[INFO] --- jar:3.4.1:jar (default-jar) @ mod-8 ---\n'
    '[INFO] \n'
    '[INFO] -------------------------< com.example:mod-9 >--------------------------\n'
    '[INFO] Building mod-9 1.0.0                                             [10/11]\n'
    '[INFO]   from mod-9/pom.xml\n'
    '[INFO] --------------------------------[ jar ]---------------------------------\n'
    '[INFO] \n'
    '[INFO] --- resources:3.3.1:resources (default-resources) @ mod-9 ---\n'
    '[INFO] skip non existing resourceDirectory /work/app/mod-9/src/main/resources\n'
    '[INFO] \n'
    '[INFO] --- compiler:3.13.0:compile (default-compile) @ mod-9 ---\n'
    '[INFO] Nothing to compile - all classes are up to date.\n'
    '[INFO] \n'
    '[INFO] --- resources:3.3.1:testResources (default-testResources) @ mod-9 ---\n'
    '[INFO] skip non existing resourceDirectory /work/app/mod-9/src/test/resources\n'
    '[INFO] \n'
    '[INFO] --- compiler:3.13.0:testCompile (default-testCompile) @ mod-9 ---\n'
    '[INFO] No sources to compile\n'
    '[INFO] \n'
    '[INFO] --- surefire:3.2.5:test (default-test) @ mod-9 ---\n'
    '[INFO] No tests to run.\n'
    '[INFO] \n'
    '[INFO] --- jar:3.4.1:jar (default-jar) @ mod-9 ---\n'
    '[INFO] \n'
    '[INFO] -------------------------< com.example:mod-10 >-------------------------\n'
    '[INFO] Building mod-10 1.0.0                                            [11/11]\n'
    '[INFO]   from mod-10/pom.xml\n'
    '[INFO] --------------------------------[ jar ]---------------------------------\n'
    '[INFO] \n'
    '[INFO] --- resources:3.3.1:resources (default-resources) @ mod-10 ---\n'
    '[INFO] skip non existing resourceDirectory /work/app/mod-10/src/main/resources\n'
    '[INFO] \n'
    '[INFO] --- compiler:3.13.0:compile (default-compile) @ mod-10 ---\n'
    '[INFO] Nothing to compile - all classes are up to date.\n'
    '[INFO] \n'
    '[INFO] --- resources:3.3.1:testResources (default-testResources) @ mod-10 ---\n'
    '[INFO] skip non existing resourceDirectory /work/app/mod-10/src/test/resources\n'
    '[INFO] \n'
    '[INFO] --- compiler:3.13.0:testCompile (default-testCompile) @ mod-10 ---\n'
    '[INFO] No sources to compile\n'
    '[INFO] \n'
    '[INFO] --- surefire:3.2.5:test (default-test) @ mod-10 ---\n'
    '[INFO] No tests to run.\n'
    '[INFO] \n'
    '[INFO] --- jar:3.4.1:jar (default-jar) @ mod-10 ---\n'
    '[INFO] ------------------------------------------------------------------------\n'
    '[INFO] Reactor Summary for parent 1.0.0:\n'
    '[INFO] \n'
    '[INFO] parent ............................................. SUCCESS [  0.001 s]\n'
    '[INFO] mod-1 .............................................. SUCCESS [  0.794 s]\n'
    '[INFO] mod-2 .............................................. SUCCESS [  0.022 s]\n'
    '[INFO] mod-3 .............................................. SUCCESS [  0.018 s]\n'
    '[INFO] mod-4 .............................................. SUCCESS [  0.019 s]\n'
    '[INFO] mod-5 .............................................. SUCCESS [  0.016 s]\n'
    '[INFO] mod-6 .............................................. SUCCESS [  0.017 s]\n'
    '[INFO] mod-7 .............................................. SUCCESS [  0.035 s]\n'
    '[INFO] mod-8 .............................................. SUCCESS [  0.019 s]\n'
    '[INFO] mod-9 .............................................. SUCCESS [  0.014 s]\n'
    '[INFO] mod-10 ............................................. SUCCESS [  0.016 s]\n'
    '[INFO] ------------------------------------------------------------------------\n'
    '[INFO] BUILD SUCCESS\n'
    '[INFO] ------------------------------------------------------------------------\n'
    '[INFO] Total time:  1.126 s\n'
    '[INFO] Finished at: 2026-11-04T17:40:17Z\n'
    '[INFO] ------------------------------------------------------------------------\n'
)
MVN_REACTOR_FAIL = (
    '[INFO] Scanning for projects...\n'
    '[INFO] ------------------------------------------------------------------------\n'
    '[INFO] Reactor Build Order:\n'
    '[INFO] \n'
    '[INFO] parent                                                             [pom]\n'
    '[INFO] mod-1                                                              [jar]\n'
    '[INFO] mod-2                                                              [jar]\n'
    '[INFO] mod-3                                                              [jar]\n'
    '[INFO] mod-4                                                              [jar]\n'
    '[INFO] mod-5                                                              [jar]\n'
    '[INFO] mod-6                                                              [jar]\n'
    '[INFO] mod-7                                                              [jar]\n'
    '[INFO] mod-8                                                              [jar]\n'
    '[INFO] mod-9                                                              [jar]\n'
    '[INFO] mod-10                                                             [jar]\n'
    '[INFO] \n'
    '[INFO] -------------------------< com.example:parent >-------------------------\n'
    '[INFO] Building parent 1.0.0                                             [1/11]\n'
    '[INFO]   from pom.xml\n'
    '[INFO] --------------------------------[ pom ]---------------------------------\n'
    '[INFO] \n'
    '[INFO] -------------------------< com.example:mod-1 >--------------------------\n'
    '[INFO] Building mod-1 1.0.0                                              [2/11]\n'
    '[INFO]   from mod-1/pom.xml\n'
    '[INFO] --------------------------------[ jar ]---------------------------------\n'
    '[INFO] \n'
    '[INFO] --- resources:3.3.1:resources (default-resources) @ mod-1 ---\n'
    '[INFO] skip non existing resourceDirectory /work/app/mod-1/src/main/resources\n'
    '[INFO] \n'
    '[INFO] --- compiler:3.13.0:compile (default-compile) @ mod-1 ---\n'
    '[INFO] Nothing to compile - all classes are up to date.\n'
    '[INFO] \n'
    '[INFO] --- resources:3.3.1:testResources (default-testResources) @ mod-1 ---\n'
    '[INFO] skip non existing resourceDirectory /work/app/mod-1/src/test/resources\n'
    '[INFO] \n'
    '[INFO] --- compiler:3.13.0:testCompile (default-testCompile) @ mod-1 ---\n'
    '[INFO] No sources to compile\n'
    '[INFO] \n'
    '[INFO] --- surefire:3.2.5:test (default-test) @ mod-1 ---\n'
    '[INFO] No tests to run.\n'
    '[INFO] \n'
    '[INFO] --- jar:3.4.1:jar (default-jar) @ mod-1 ---\n'
    '[INFO] \n'
    '[INFO] -------------------------< com.example:mod-2 >--------------------------\n'
    '[INFO] Building mod-2 1.0.0                                              [3/11]\n'
    '[INFO]   from mod-2/pom.xml\n'
    '[INFO] --------------------------------[ jar ]---------------------------------\n'
    '[INFO] \n'
    '[INFO] --- resources:3.3.1:resources (default-resources) @ mod-2 ---\n'
    '[INFO] skip non existing resourceDirectory /work/app/mod-2/src/main/resources\n'
    '[INFO] \n'
    '[INFO] --- compiler:3.13.0:compile (default-compile) @ mod-2 ---\n'
    '[INFO] Nothing to compile - all classes are up to date.\n'
    '[INFO] \n'
    '[INFO] --- resources:3.3.1:testResources (default-testResources) @ mod-2 ---\n'
    '[INFO] skip non existing resourceDirectory /work/app/mod-2/src/test/resources\n'
    '[INFO] \n'
    '[INFO] --- compiler:3.13.0:testCompile (default-testCompile) @ mod-2 ---\n'
    '[INFO] No sources to compile\n'
    '[INFO] \n'
    '[INFO] --- surefire:3.2.5:test (default-test) @ mod-2 ---\n'
    '[INFO] No tests to run.\n'
    '[INFO] \n'
    '[INFO] --- jar:3.4.1:jar (default-jar) @ mod-2 ---\n'
    '[INFO] \n'
    '[INFO] -------------------------< com.example:mod-3 >--------------------------\n'
    '[INFO] Building mod-3 1.0.0                                              [4/11]\n'
    '[INFO]   from mod-3/pom.xml\n'
    '[INFO] --------------------------------[ jar ]---------------------------------\n'
    '[INFO] \n'
    '[INFO] --- resources:3.3.1:resources (default-resources) @ mod-3 ---\n'
    '[INFO] skip non existing resourceDirectory /work/app/mod-3/src/main/resources\n'
    '[INFO] \n'
    '[INFO] --- compiler:3.13.0:compile (default-compile) @ mod-3 ---\n'
    '[INFO] Nothing to compile - all classes are up to date.\n'
    '[INFO] \n'
    '[INFO] --- resources:3.3.1:testResources (default-testResources) @ mod-3 ---\n'
    '[INFO] skip non existing resourceDirectory /work/app/mod-3/src/test/resources\n'
    '[INFO] \n'
    '[INFO] --- compiler:3.13.0:testCompile (default-testCompile) @ mod-3 ---\n'
    '[INFO] No sources to compile\n'
    '[INFO] \n'
    '[INFO] --- surefire:3.2.5:test (default-test) @ mod-3 ---\n'
    '[INFO] No tests to run.\n'
    '[INFO] \n'
    '[INFO] --- jar:3.4.1:jar (default-jar) @ mod-3 ---\n'
    '[INFO] \n'
    '[INFO] -------------------------< com.example:mod-4 >--------------------------\n'
    '[INFO] Building mod-4 1.0.0                                              [5/11]\n'
    '[INFO]   from mod-4/pom.xml\n'
    '[INFO] --------------------------------[ jar ]---------------------------------\n'
    '[INFO] \n'
    '[INFO] --- resources:3.3.1:resources (default-resources) @ mod-4 ---\n'
    '[INFO] skip non existing resourceDirectory /work/app/mod-4/src/main/resources\n'
    '[INFO] \n'
    '[INFO] --- compiler:3.13.0:compile (default-compile) @ mod-4 ---\n'
    '[INFO] Nothing to compile - all classes are up to date.\n'
    '[INFO] \n'
    '[INFO] --- resources:3.3.1:testResources (default-testResources) @ mod-4 ---\n'
    '[INFO] skip non existing resourceDirectory /work/app/mod-4/src/test/resources\n'
    '[INFO] \n'
    '[INFO] --- compiler:3.13.0:testCompile (default-testCompile) @ mod-4 ---\n'
    '[INFO] No sources to compile\n'
    '[INFO] \n'
    '[INFO] --- surefire:3.2.5:test (default-test) @ mod-4 ---\n'
    '[INFO] No tests to run.\n'
    '[INFO] \n'
    '[INFO] --- jar:3.4.1:jar (default-jar) @ mod-4 ---\n'
    '[INFO] \n'
    '[INFO] -------------------------< com.example:mod-5 >--------------------------\n'
    '[INFO] Building mod-5 1.0.0                                              [6/11]\n'
    '[INFO]   from mod-5/pom.xml\n'
    '[INFO] --------------------------------[ jar ]---------------------------------\n'
    '[INFO] \n'
    '[INFO] --- resources:3.3.1:resources (default-resources) @ mod-5 ---\n'
    '[INFO] skip non existing resourceDirectory /work/app/mod-5/src/main/resources\n'
    '[INFO] \n'
    '[INFO] --- compiler:3.13.0:compile (default-compile) @ mod-5 ---\n'
    '[INFO] Nothing to compile - all classes are up to date.\n'
    '[INFO] \n'
    '[INFO] --- resources:3.3.1:testResources (default-testResources) @ mod-5 ---\n'
    '[INFO] skip non existing resourceDirectory /work/app/mod-5/src/test/resources\n'
    '[INFO] \n'
    '[INFO] --- compiler:3.13.0:testCompile (default-testCompile) @ mod-5 ---\n'
    '[INFO] No sources to compile\n'
    '[INFO] \n'
    '[INFO] --- surefire:3.2.5:test (default-test) @ mod-5 ---\n'
    '[INFO] No tests to run.\n'
    '[INFO] \n'
    '[INFO] --- jar:3.4.1:jar (default-jar) @ mod-5 ---\n'
    '[INFO] \n'
    '[INFO] -------------------------< com.example:mod-6 >--------------------------\n'
    '[INFO] Building mod-6 1.0.0                                              [7/11]\n'
    '[INFO]   from mod-6/pom.xml\n'
    '[INFO] --------------------------------[ jar ]---------------------------------\n'
    '[INFO] \n'
    '[INFO] --- resources:3.3.1:resources (default-resources) @ mod-6 ---\n'
    '[INFO] skip non existing resourceDirectory /work/app/mod-6/src/main/resources\n'
    '[INFO] \n'
    '[INFO] --- compiler:3.13.0:compile (default-compile) @ mod-6 ---\n'
    '[INFO] Nothing to compile - all classes are up to date.\n'
    '[INFO] \n'
    '[INFO] --- resources:3.3.1:testResources (default-testResources) @ mod-6 ---\n'
    '[INFO] skip non existing resourceDirectory /work/app/mod-6/src/test/resources\n'
    '[INFO] \n'
    '[INFO] --- compiler:3.13.0:testCompile (default-testCompile) @ mod-6 ---\n'
    '[INFO] No sources to compile\n'
    '[INFO] \n'
    '[INFO] --- surefire:3.2.5:test (default-test) @ mod-6 ---\n'
    '[INFO] No tests to run.\n'
    '[INFO] \n'
    '[INFO] --- jar:3.4.1:jar (default-jar) @ mod-6 ---\n'
    '[INFO] \n'
    '[INFO] -------------------------< com.example:mod-7 >--------------------------\n'
    '[INFO] Building mod-7 1.0.0                                              [8/11]\n'
    '[INFO]   from mod-7/pom.xml\n'
    '[INFO] --------------------------------[ jar ]---------------------------------\n'
    '[INFO] \n'
    '[INFO] --- resources:3.3.1:resources (default-resources) @ mod-7 ---\n'
    '[INFO] skip non existing resourceDirectory /work/app/mod-7/src/main/resources\n'
    '[INFO] \n'
    '[INFO] --- compiler:3.13.0:compile (default-compile) @ mod-7 ---\n'
    '[INFO] Recompiling the module because of changed source code.\n'
    '[INFO] Compiling 1 source file with javac [debug target 17] to target/classes\n'
    '[INFO] -------------------------------------------------------------\n'
    '[WARNING] COMPILATION WARNING : \n'
    '[INFO] -------------------------------------------------------------\n'
    '[WARNING] system modules path not set in conjunction with -source 17\n'
    '[INFO] 1 warning\n'
    '[INFO] -------------------------------------------------------------\n'
    '[INFO] -------------------------------------------------------------\n'
    '[ERROR] COMPILATION ERROR : \n'
    '[INFO] -------------------------------------------------------------\n'
    '[ERROR] /work/app/mod-7/src/main/java/com/example/m7/Svc.java:[2,44] incompatible types: java.lang.String cannot be converted to int\n'
    '[INFO] 1 error\n'
    '[INFO] -------------------------------------------------------------\n'
    '[INFO] ------------------------------------------------------------------------\n'
    '[INFO] Reactor Summary for parent 1.0.0:\n'
    '[INFO] \n'
    '[INFO] parent ............................................. SUCCESS [  0.002 s]\n'
    '[INFO] mod-1 .............................................. SUCCESS [  0.858 s]\n'
    '[INFO] mod-2 .............................................. SUCCESS [  0.019 s]\n'
    '[INFO] mod-3 .............................................. SUCCESS [  0.018 s]\n'
    '[INFO] mod-4 .............................................. SUCCESS [  0.018 s]\n'
    '[INFO] mod-5 .............................................. SUCCESS [  0.031 s]\n'
    '[INFO] mod-6 .............................................. SUCCESS [  0.024 s]\n'
    '[INFO] mod-7 .............................................. FAILURE [  0.343 s]\n'
    '[INFO] mod-8 .............................................. SKIPPED\n'
    '[INFO] mod-9 .............................................. SKIPPED\n'
    '[INFO] mod-10 ............................................. SKIPPED\n'
    '[INFO] ------------------------------------------------------------------------\n'
    '[INFO] BUILD FAILURE\n'
    '[INFO] ------------------------------------------------------------------------\n'
    '[INFO] Total time:  1.426 s\n'
    '[INFO] Finished at: 2026-11-04T17:40:20Z\n'
    '[INFO] ------------------------------------------------------------------------\n'
    '[ERROR] Failed to execute goal org.apache.maven.plugins:maven-compiler-plugin:3.13.0:compile (default-compile) on project mod-7: Compilation failure\n'
    '[ERROR] /work/app/mod-7/src/main/java/com/example/m7/Svc.java:[2,44] incompatible types: java.lang.String cannot be converted to int\n'
    '[ERROR] -> [Help 1]\n'
    '[ERROR] \n'
    '[ERROR] To see the full stack trace of the errors, re-run Maven with the -e switch.\n'
    '[ERROR] Re-run Maven using the -X switch to enable full debug logging.\n'
    '[ERROR] \n'
    '[ERROR] For more information about the errors and possible solutions, please read the following articles:\n'
    '[ERROR] [Help 1] http://cwiki.apache.org/confluence/display/MAVEN/MojoFailureException\n'
    '[ERROR] \n'
    '[ERROR] After correcting the problems, you can resume the build with the command\n'
    '[ERROR]   mvn <args> -rf :mod-7\n'
)
GRADLE_COMPILE_FAIL = (
    '\n'
    '> Task :compileJava FAILED\n'
    '/work/app/src/main/java/demo/Main.java:4: error: incompatible types: String cannot be converted to int\n'
    '        int x = "not an int";\n'
    '                ^\n'
    '/work/app/src/main/java/demo/Main.java:5: error: cannot find symbol\n'
    '        System.out.println(undefinedVar);\n'
    '                           ^\n'
    '  symbol:   variable undefinedVar\n'
    '  location: class Main\n'
    '2 errors\n'
    '\n'
    '[Incubating] Problems report is available at: file:///work/app/build/reports/problems/problems-report.html\n'
    '\n'
    'FAILURE: Build failed with an exception.\n'
    '\n'
    '* What went wrong:\n'
    "Execution failed for task ':compileJava'.\n"
    '> Compilation failed; see the compiler output below.\n'
    '  /work/app/src/main/java/demo/Main.java:4: error: incompatible types: String cannot be converted to int\n'
    '          int x = "not an int";\n'
    '                  ^\n'
    '  /work/app/src/main/java/demo/Main.java:5: error: cannot find symbol\n'
    '          System.out.println(undefinedVar);\n'
    '                             ^\n'
    '    symbol:   variable undefinedVar\n'
    '    location: class Main\n'
    '  2 errors\n'
    '\n'
    '* Try:\n'
    '> Check your code and dependencies to fix the compilation error(s)\n'
    '> Run with --scan to get full insights.\n'
    '\n'
    'BUILD FAILED in 1s\n'
    '1 actionable task: 1 executed\n'
)

_M_BANNER = re.compile(r"^\[INFO\] -+< (\S+):(\S+) >-+$")
_M_BUILDING = re.compile(r"^\[INFO\] Building (.*?) (\S+)\s+\[(\d+)/(\d+)\]$")
_M_HEAD = re.compile(r"^\[INFO\] \[(\d+)/(\d+)\] (.*?) (\S+)$")
_M_FROM = re.compile(r"^\[INFO\]   from \S+$")
_M_PKG = re.compile(r"^\[INFO\] -+\[ \S+ \]-+$")
_M_GOAL = re.compile(r"^\[INFO\] --- (\S+:\S+:\S+) \(([^)]*)\) @ (\S+) ---$")
_M_GOALS = "[INFO]   goals: "
_M_NOOP = [re.compile(p) for p in (r"^\[INFO\] skip non existing resourceDirectory .*$", r"^\[INFO\] Nothing to compile - all classes are up to date\.?$",
                                   r"^\[INFO\] No sources to compile$", r"^\[INFO\] No tests to run\.$", r"^\[INFO\] Using '.*' encoding to copy filtered resources\.$",
                                   r"^\[INFO\] Copying \d+ resources?( from .* to .*)?$")]
_M_END = re.compile(r"^\[INFO\] Reactor Summary for ")
_M_RULE = re.compile(r"^\[INFO\] -{40,}$")
_M_ADVICE = [re.compile(p) for p in (r"^\[ERROR\] To see the full stack trace of the errors, re-run Maven with the -e switch\.$", r"^\[ERROR\] Re-run Maven using the -X switch to enable full debug logging\.$",
                                     r"^\[ERROR\] For more information about the errors and possible solutions, please read the following articles:$")]
_M_ADV_TAG = "[ERROR] [ctxprune: -e/-X/help advice elided]"
_G_ADVICE = [re.compile(p) for p in (r"^> Run with --stacktrace option to get the stack trace\.$", r"^> Run with --info or --debug option to get more log output\.$", r"^> Run with --scan to get full insights\.$",
                                     r"^> Get more help at https://help\.gradle\.org\.?$", r"^\* Get more help at https://help\.gradle\.org$")]
_G_ADV_TAG = "> [ctxprune: --stacktrace/--info/--scan/help elided]"
_G_DEPR = [re.compile(p) for p in (r"^You can use '--warning-mode all' to show the individual deprecation warnings and determine if they come from your own scripts or plugins\.$",
                                   r"^For more on this, please refer to https://docs\.gradle\.org/\S+ in the Gradle documentation\.$")]
_G_DEPR_TAG = "[ctxprune: deprecation advisory elided]"


def _m_noop(l: str) -> bool:
    return any(rx.match(l) for rx in _M_NOOP)


def _m_stanza(lines: list[str]) -> list[str]:
    if not any(_M_BUILDING.match(l) for l in lines):
        return lines
    out: list[str] = []
    goals: list = []  # [header, kept lines]
    bare: list[str] = []

    def flush_goals():
        nonlocal goals, bare
        for header, kept in goals:
            if kept:
                if bare:
                    out.append(_M_GOALS + ", ".join(bare))
                    bare = []
                out.extend([header] + kept)
            else:
                bare.append(_M_GOAL.match(header).group(1))
        goals = []

    for l in lines:
        if m := _M_BUILDING.match(l):
            flush_goals()
            if bare:
                out.append(_M_GOALS + ", ".join(bare))
                bare = []
            out.append(f"[INFO] [{m.group(3)}/{m.group(4)}] {m.group(1)} {m.group(2)}")
        elif _M_BANNER.match(l) or _M_FROM.match(l) or _M_PKG.match(l) or l == "[INFO] " or _m_noop(l):
            continue
        elif _M_GOAL.match(l):
            goals.append([l, []])
        elif goals:
            goals[-1][1].append(l)
        else:
            flush_goals()
            if bare:
                out.append(_M_GOALS + ", ".join(bare))
                bare = []
            out.append(l)
    flush_goals()
    if bare:
        out.append(_M_GOALS + ", ".join(bare))
    return out


def trim_build_tool(text: str) -> str:
    lines = text.split("\n")
    n = len(lines)
    out, i = [], 0
    while i < n:
        if _M_BANNER.match(lines[i]):
            j = i + 1
            while j < n and not _M_BANNER.match(lines[j]) and not _M_END.match(lines[j]):
                j += 1
            if j < n and _M_END.match(lines[j]) and _M_RULE.match(lines[j - 1]):
                j -= 1
            out += _m_stanza(lines[i:j])
            i = j
        else:
            out.append(lines[i])
            i += 1
    # constant advice lines -> one tag per family, only when that is shorter than what it replaces
    families = [(_M_ADVICE, _M_ADV_TAG), (_G_ADVICE, _G_ADV_TAG), (_G_DEPR, _G_DEPR_TAG)]
    active = []
    for rxs, tag in families:
        total = sum(len(l) + 1 for l in out if any(rx.match(l) for rx in rxs))
        if total > len(tag) + 1:
            active.append((rxs, tag))
    res, done = [], set()
    k = 0
    while k < len(out):
        l = out[k]
        fam = next((f for f in active if any(rx.match(l) for rx in f[0])), None)
        if fam is None:
            res.append(l)
        else:
            if fam[1] not in done:
                res.append(fam[1])
                done.add(fam[1])
            if fam[0] is _M_ADVICE and k + 1 < len(out) and out[k + 1] == "[ERROR] ":
                k += 1
        k += 1
    return shrink_only(text, "\n".join(res))


def probes_224(text: str) -> dict:
    modules, goals, wl, summary, gradle, other = [], [], [], [], [], []
    cur = None
    for l in text.split("\n"):
        if m := _M_BUILDING.match(l):
            modules.append((m.group(1), m.group(2), int(m.group(3)), int(m.group(4))))
            cur = m.group(1)
        elif m := _M_HEAD.match(l):
            modules.append((m.group(3), m.group(4), int(m.group(1)), int(m.group(2))))
            cur = m.group(3)
        elif m := _M_GOAL.match(l):
            goals.append((cur, m.group(1)))
        elif l.startswith(_M_GOALS):
            goals += [(cur, g) for g in l[len(_M_GOALS):].split(", ")]
        elif l in (_M_ADV_TAG, _G_ADV_TAG, _G_DEPR_TAG) or any(rx.match(l) for rx in _M_ADVICE + _G_ADVICE + _G_DEPR):
            continue
        elif l.strip() != "[ERROR]" and re.match(r"^\[(?:WARNING|ERROR)\]", l) or re.search(r"Tests run:", l) or re.match(r"^\[INFO\] BUILD |^\[INFO\] (?:Total time|Finished at)", l):
            wl.append(l)
        elif re.match(r"^\[INFO\] (?:Reactor Summary|\S+ \.+ (?:SUCCESS|FAILURE|SKIPPED))", l):
            summary.append(l)
        elif re.match(r"^(?:> Task |FAILURE: |BUILD (?:FAILED|SUCCESSFUL)|\d+ actionable tasks?|\* What went wrong|Execution failed|> |  \S|Deprecated Gradle)", l) or l.startswith("* Try:"):
            gradle.append(l)
        elif l.startswith("[INFO]") and not (_M_BANNER.match(l) or _M_FROM.match(l) or _M_PKG.match(l) or l == "[INFO] " or _m_noop(l) or _M_RULE.match(l)):
            other.append(l)
    return {"modules in order (name, version, i, n)": modules, "goals run per module": goals, "warnings, errors, test and build result lines": wl,
            "reactor summary lines": summary, "gradle tasks, error text and result": gradle, "other INFO lines": other}


case(
    224,
    claim="Each Maven module stanza loses its banner, 'from pom', packaging rule, blank INFO lines and no-op results ('Nothing to compile', 'No tests to run', 'skip non existing resourceDirectory') and becomes '[INFO] [i/n] name version' plus one 'goals:' line (a goal with output keeps its full header and lines); the constant -e/-X/help paragraph of a Maven failure and the constant --stacktrace/--info/--scan/help and deprecation lines of a Gradle failure become one tag each. Module order, goals run, WARNING/ERROR/test lines, the Reactor Summary, the failing module and goal, and Gradle's task and error text are unchanged.",
    loses="the execution ids ('(default-compile)'), the '@ module' repeat and decorative rules of each goal header, the no-op result lines, and the constant advice text (which --stacktrace/-e/-X/--scan do; the saved original keeps it)",
    encode=trim_build_tool,
    probes=probes_224,
    fixtures=lambda: [
        Fixture("mvn verify, 11-module reactor, all green (real, Maven 3.9.11)", MVN_REACTOR_OK, True),
        Fixture("mvn verify, mod-7 fails to compile; reactor summary and failure footer last (real)", MVN_REACTOR_FAIL, True),
        Fixture("gradle build, compileJava fails: Gradle 8.14 prints a single constant Try line, so the tag is not worth it (real)", GRADLE_COMPILE_FAIL, None),
        Fixture("mvn -q: a single line of output", "[ERROR] Failed to execute goal org.apache.maven.plugins:maven-surefire-plugin:3.2.5:test (default-test) on project api: There are test failures.\n", False),
        Fixture("not a build log", "hello\n[INFO] this is a plain log line\n> Task is not a gradle line\n", False),
        Fixture("gradle 7.x style footer: a task-specific Try line next to four constant ones (reconstructed from memory)", "FAILURE: Build failed with an exception.\n\n* What went wrong:\nTask 'foo' not found in root project 'demo'.\n\n* Try:\n> Run gradle tasks to get a list of available tasks.\n> Run with --stacktrace option to get the stack trace.\n> Run with --info or --debug option to get more log output.\n> Run with --scan to get full insights.\n> Get more help at https://help.gradle.org.\n\nDeprecated Gradle features were used in this build, making it incompatible with Gradle 9.0.\n\nYou can use '--warning-mode all' to show the individual deprecation warnings and determine if they come from your own scripts or plugins.\n\nFor more on this, please refer to https://docs.gradle.org/8.5/userguide/command_line_interface.html#sec:command_line_warnings in the Gradle documentation.\n\nBUILD FAILED in 520ms\n", True),
        Fixture("a banner with no 'Building' line and a text that looks like the tags", "[INFO] --------------------< a:b >---------------------\n[INFO] something else\n[ERROR] [ctxprune: standard Maven -e/-X/help advice elided]\n", None),
    ],
    min_gain=0.3,
)



# ===============================================================================================================
# #427 Crash-report section triage: JVM hs_err, macOS crash report, Linux oops, Android tombstone. Keep the header,
# signal/exception, crashing thread and its frames; replace register dumps, memory maps, library lists, other
# threads and environment sections by '[ctxprune: N lines elided (Section)]'. RULE: the probe checks the signal
# and exception lines, the crashing thread's frames, per-format identity lines, and that the line counts add up
# (kept lines + sum of stub counts == original line count) so a stub can never hide a miscount.
# hs_err and faulthandler are real captures (JVM 21 SIGSEGV through Unsafe.putAddress; Python faulthandler); the
# other three formats are reconstructed from memory, with generated bulk sections.

HS_ERR_CUT = (
    '#\n'
    '# A fatal error has been detected by the Java Runtime Environment:\n'
    '#\n'
    '#  SIGSEGV (0xb) at pc=0x00007f019d5c91d4, pid=9680, tid=9681\n'
    '#\n'
    '# JRE version: OpenJDK Runtime Environment (21.0.12.1+1) (build 21.0.12.1+1-1-24.04.4-Ubuntu)\n'
    '# Java VM: OpenJDK 64-Bit Server VM (21.0.12.1+1-1-24.04.4-Ubuntu, mixed mode, sharing, tiered, compressed oops, compressed class ptrs, g1 gc, linux-amd64)\n'
    '# Problematic frame:\n'
    '# V  [libjvm.so+0xfc91d4]  Unsafe_PutLong+0xa4\n'
    '#\n'
    '# No core dump will be written. Core dumps have been disabled. To enable core dumping, try "ulimit -c unlimited" before starting Java again\n'
    '#\n'
    '# If you would like to submit a bug report, please visit:\n'
    '#   https://bugs.launchpad.net/ubuntu/+source/openjdk-21\n'
    '#\n'
    '\n'
    '---------------  S U M M A R Y ------------\n'
    '\n'
    'Command Line: -Djavax.net.ssl.trustStore=/etc/ssl/certs/java/cacerts -Djavax.net.ssl.trustStoreType=JKS Crash\n'
    '\n'
    'Host: Intel(R) Xeon(R) Processor @ 2.10GHz, 4 cores, 13G, Ubuntu 24.04.5 LTS\n'
    'Time: Wed Nov  4 17:38:18 2026 UTC elapsed time: 0.182525 seconds (0d 0h 0m 0s)\n'
    '\n'
    '---------------  T H R E A D  ---------------\n'
    '\n'
    'Current thread (0x00007f019801a960):  JavaThread "main"             [_thread_in_vm, id=9681, stack(0x00007f019c500000,0x00007f019c600000) (1024K)]\n'
    '\n'
    'Stack: [0x00007f019c500000,0x00007f019c600000],  sp=0x00007f019c5fe670,  free space=1017k\n'
    'Native frames: (J=compiled Java code, j=interpreted, Vv=VM code, C=native code)\n'
    'V  [libjvm.so+0xfc91d4]  Unsafe_PutLong+0xa4\n'
    'j  jdk.internal.misc.Unsafe.putLong(Ljava/lang/Object;JJ)V+0 java.base@21.0.12.1\n'
    'j  jdk.internal.misc.Unsafe.putAddress(Ljava/lang/Object;JJ)V+24 java.base@21.0.12.1\n'
    'j  jdk.internal.misc.Unsafe.putAddress(JJ)V+4 java.base@21.0.12.1\n'
    'j  sun.misc.Unsafe.putAddress(JJ)V+5 jdk.unsupported@21.0.12.1\n'
    'j  Crash.poke(Lsun/misc/Unsafe;)V+5\n'
    'j  Crash.main([Ljava/lang/String;)V+23\n'
    'v  ~StubRoutines::call_stub 0x00007f0187d37cc6\n'
    'V  [libjvm.so+0x9199ea]  JavaCalls::call_helper(JavaValue*, methodHandle const&, JavaCallArguments*, JavaThread*)+0x2da\n'
    'V  [libjvm.so+0x9be9bd]  jni_invoke_static(JNIEnv_*, JavaValue*, _jobject*, JNICallType, _jmethodID*, JNI_ArgumentPusher*, JavaThread*) [clone .constprop.1]+0x35d\n'
    'V  [libjvm.so+0x9d1f22]  jni_CallStaticVoidMethod+0x252\n'
    'C  [libjli.so+0x55ce]  JavaMain+0xfee\n'
    'C  [libjli.so+0x849d]  ThreadJavaMain+0xd\n'
    'C  [libc.so.6+0x9cb84]\n'
    'Java frames: (J=compiled Java code, j=interpreted, Vv=VM code)\n'
    'j  jdk.internal.misc.Unsafe.putLong(Ljava/lang/Object;JJ)V+0 java.base@21.0.12.1\n'
    'j  jdk.internal.misc.Unsafe.putAddress(Ljava/lang/Object;JJ)V+24 java.base@21.0.12.1\n'
    'j  jdk.internal.misc.Unsafe.putAddress(JJ)V+4 java.base@21.0.12.1\n'
    'j  sun.misc.Unsafe.putAddress(JJ)V+5 jdk.unsupported@21.0.12.1\n'
    'j  Crash.poke(Lsun/misc/Unsafe;)V+5\n'
    'j  Crash.main([Ljava/lang/String;)V+23\n'
    'v  ~StubRoutines::call_stub 0x00007f0187d37cc6\n'
    '\n'
    'siginfo: si_signo: 11 (SIGSEGV), si_code: 1 (SEGV_MAPERR), si_addr: 0x0000000000000000\n'
    '\n'
    'Registers:\n'
    'RAX=0x0000000000000000, RBX=0x00007f019801a960, RCX=0x0000000000000000, RDX=0x0000000000000000\n'
    'RSP=0x00007f019c5fe670, RBP=0x00007f019c5fe690, RSI=0x00007f019c5fe760, RDI=0x00007f019801ad20\n'
    'R8 =0x000000000000002a, R9 =0x0000000000000000, R10=0x00007f0187d43a91, R11=0x0000000000000101\n'
    'R12=0x0000000000000000, R13=0x0000000000000000, R14=0x000000000000002a, R15=0x00007f019801a960\n'
    'RIP=0x00007f019d5c91d4, EFLAGS=0x0000000000010246, CSGSFS=0x002b000000000033, ERR=0x0000000000000006\n'
    '  TRAPNO=0x000000000000000e\n'
    '\n'
    'XMM[0]=0xffffffffffffffff 0xffffffff0000000e\n'
    'XMM[1]=0x00007f019801b080 0x00007f019801b070\n'
    'XMM[2]=0x00007f019801b080 0x00007f019801b070\n'
    'XMM[3]=0x0000000000000000 0x00007f0187d3ea90\n'
    'XMM[4]=0x00007f019c5fe7b0 0x00007f0000000000\n'
    'XMM[5]=0x0000000000000000 0x00007f019801a960\n'
    'XMM[6]=0x0000000000000000 0x00007f019801a960\n'
    'XMM[7]=0xffffffffffffffff 0xffffffff0000000f\n'
    'XMM[8]=0xcafebabecafebabe 0xcafebabecafebabe\n'
    'XMM[9]=0x0000000000000000 0x00007f019d7c9080\n'
    'XMM[10]=0x0000000000000000 0x00007f019d7c93e0\n'
    'XMM[11]=0x0000000000000000 0x00007f019d7c91a0\n'
    'XMM[12]=0x0000000000000000 0x0000000000000000\n'
    'XMM[13]=0x0000000000000000 0x0000000000000000\n'
    'XMM[14]=0x0000000000000000 0x0000000000000000\n'
    'XMM[15]=0x0000000000000000 0x0000000000000000\n'
    '  MXCSR=0x00001fa2\n'
    '\n'
    '\n'
    'Register to memory mapping:\n'
    '\n'
    'RAX=0x0 is null\n'
    'RBX=0x00007f019801a960 is a thread\n'
    'RCX=0x0 is null\n'
    'RDX=0x0 is null\n'
    'RSP=0x00007f019c5fe670 is pointing into the stack for thread: 0x00007f019801a960\n'
    'RBP=0x00007f019c5fe690 is pointing into the stack for thread: 0x00007f019801a960\n'
    'RSI=0x00007f019c5fe760 is pointing into the stack for thread: 0x00007f019801a960\n'
    'RDI=0x00007f019801ad20 points into unknown readable memory: 0x00007f019dad5060 | 60 50 ad 9d 01 7f 00 00\n'
    'R8 =0x000000000000002a is an unknown value\n'
    'R9 =0x0 is null\n'
    'R10=0x00007f0187d43a91 is at code_begin+1009 in an Interpreter codelet\n'
    'native method entry point (kind = native)  [0x00007f0187d436a0, 0x00007f0187d44210]  2928 bytes\n'
    'R11=0x0000000000000101 is an unknown value\n'
    'R12=0x0 is null\n'
    'R13=0x0 is null\n'
    'R14=0x000000000000002a is an unknown value\n'
    'R15=0x00007f019801a960 is a thread\n'
    '\n'
    'Top of Stack: (sp=0x00007f019c5fe670)\n'
    '0x00007f019c5fe670:   00007f0123442168 0000000000000000   h!D#............\n'
    '0x00007f019c5fe680:   00007f0123be0598 00007f019c5fe760   ...#....`._.....\n'
    '0x00007f019c5fe690:   00007f019c5fe718 00007f0187d43ac0   .._......:......\n'
    '0x00007f019c5fe6a0:   0000000000000000 00007f0123be0598   ...........#....\n'
    '0x00007f019c5fe6b0:   00007f019c5fe718 00007f0187d43797   .._......7......\n'
    '0x00007f019c5fe6c0:   0000000000000000 00007f0187d4375f   ........_7......\n'
    '0x00007f019c5fe6d0:   00007f019c5fe6d0 00007f0123be0598   .._........#....\n'
    '0x00007f019c5fe6e0:   0000000000000009 00007f0123442fc0   ........./D#....\n'
    '0x00007f019c5fe6f0:   0000000000000000 00000007ffe859c0   .........Y......\n'
    '0x00007f019c5fe700:   00007f0123442168 0000000000000000   h!D#............\n'
    '0x00007f019c5fe710:   00007f019c5fe738 00007f019c5fe7b0   8._......._.....\n'
    '0x00007f019c5fe720:   00007f0187d3f180 0000000000000000   ................\n'
    '0x00007f019c5fe730:   00007f0187d40fd7 000000000000002a   ........*.......\n'
    '0x00007f019c5fe740:   0000000000000000 0000000000000000   ................\n'
    '0x00007f019c5fe750:   0000000000000000 0000000000000000   ................\n'
    '0x00007f019c5fe760:   00000007378028a8 00007f019c5fe768   .(.7....h._.....\n'
    '0x00007f019c5fe770:   00007f0123bdb220 0000000000000007    ..#............\n'
    '0x00007f019c5fe780:   00007f0123442fc0 0000000000000000   ./D#............\n'
    '0x00007f019c5fe790:   00000007ffe859c0 00007f012343eb70   .Y......p.C#....\n'
    '0x00007f019c5fe7a0:   00007f019c5fe738 00007f019c5fe7c0   8._......._.....\n'
    '0x00007f019c5fe7b0:   00007f019c5fe838 00007f0187d3f180   8._.............\n'
    '0x00007f019c5fe7c0:   000000000000002a 0000000000000000   *...............\n'
    '0x00007f019c5fe7d0:   0000000000000000 0000000000000000   ................\n'
    '0x00007f019c5fe7e0:   0000000000000000 00000007378028a8   .........(.7....\n'
    '0x00007f019c5fe7f0:   00007f019c5fe7f0 00007f0123bdb184   .._........#....\n'
    '0x00007f019c5fe800:   0000000000000006 00007f0123442fc0   ........./D#....\n'
    '0x00007f019c5fe810:   0000000000000000 00000007ffe859c0   .........Y......\n'
    '0x00007f019c5fe820:   00007f012343eb18 00007f019c5fe7c0   ..C#......_.....\n'
    '0x00007f019c5fe830:   00007f019c5fe848 00007f019c5fe8b8   H._......._.....\n'
    '0x00007f019c5fe840:   00007f0187d3f180 000000000000002a   ........*.......\n'
    '0x00007f019c5fe850:   0000000000000000 0000000000000000   ................\n'
    '0x00007f019c5fe860:   0000000000000000 00000007378028a8   .........(.7....\n'
    '\n'
    'Instructions: (pc=0x00007f019d5c91d4)\n'
    '0x00007f019d5c90d4:   72 ff ff ff 0f 1f 84 00 00 00 00 00 48 89 df e8\n'
    '0x00007f019d5c90e4:   e8 92 96 ff e9 2e ff ff ff 0f 1f 00 31 d2 be 01\n'
    '0x00007f019d5c90f4:   00 00 00 48 89 df e8 b1 a4 df ff e9 09 ff ff ff\n'
    '0x00007f019d5c9104:   0f 1f 40 00 48 89 df e8 10 83 96 ff e9 ce fe ff\n'
    '0x00007f019d5c9114:   ff 0f 1f 00 49 8d 7c 24 fe ff 15 ed 4e 50 00 e9\n'
    '0x00007f019d5c9124:   22 ff ff ff 0f 1f 84 00 00 00 00 00 f3 0f 1e fa\n'
    '0x00007f019d5c9134:   55 48 89 e5 41 56 4d 89 c6 41 55 49 89 cd 41 54\n'
    '0x00007f019d5c9144:   49 89 d4 53 48 8d 9f 40 fc ff ff 8b 83 70 04 00\n'
    '0x00007f019d5c9154:   00 2d ad de 00 00 83 f8 01 0f 86 25 01 00 00 48\n'
    '0x00007f019d5c9164:   8d 05 9e af 54 00 80 38 00 c7 83 4c 04 00 00 06\n'
    '0x00007f019d5c9174:   00 00 00 75 05 f0 83 04 24 00 48 8b 83 50 04 00\n'
    '0x00007f019d5c9184:   00 a8 01 0f 85 e3 00 00 00 8b 83 48 04 00 00 a8\n'
    '0x00007f019d5c9194:   0c 0f 85 c5 00 00 00 c7 83 4c 04 00 00 06 00 00\n'
    '0x00007f019d5c91a4:   00 31 c0 4d 85 e4 74 1e 4c 89 e0 83 e0 03 48 83\n'
    '0x00007f019d5c91b4:   f8 01 0f 84 8c 00 00 00 48 83 f8 02 0f 84 d2 00\n'
    '0x00007f019d5c91c4:   00 00 49 8b 04 24 49 01 c5 c6 83 78 04 00 00 01\n'
    '0x00007f019d5c91d4:   4d 89 75 00 4c 8b a3 98 01 00 00 c6 83 78 04 00\n'
    '0x00007f019d5c91e4:   00 00 49 8b 44 24 10 48 83 38 00 74 0d 4c 89 e7\n'
    '0x00007f019d5c91f4:   e8 77 c6 8d ff 49 8b 44 24 10 49 8b 54 24 08 48\n'
    '0x00007f019d5c9204:   8d bb a0 03 00 00 48 89 42 10 49 8b 44 24 08 49\n'
    '0x00007f019d5c9214:   8b 54 24 18 48 89 50 18 49 8b 44 24 08 49 8b 54\n'
    '0x00007f019d5c9224:   24 20 48 89 50 20 e8 81 99 7d ff c7 83 4c 04 00\n'
    '0x00007f019d5c9234:   00 04 00 00 00 5b 41 5c 41 5d 41 5e 5d c3 66 0f\n'
    '0x00007f019d5c9244:   1f 44 00 00 49 8d 7c 24 ff ff 15 dd 9a 50 00 e9\n'
    '0x00007f019d5c9254:   72 ff ff ff 0f 1f 84 00 00 00 00 00 48 89 df e8\n'
    '0x00007f019d5c9264:   68 91 96 ff e9 2e ff ff ff 0f 1f 00 31 d2 be 01\n'
    '0x00007f019d5c9274:   00 00 00 48 89 df e8 31 a3 df ff e9 09 ff ff ff\n'
    '0x00007f019d5c9284:   0f 1f 40 00 48 89 df e8 90 81 96 ff e9 ce fe ff\n'
    '0x00007f019d5c9294:   ff 0f 1f 00 49 8d 7c 24 fe ff 15 6d 4d 50 00 e9\n'
    '0x00007f019d5c92a4:   22 ff ff ff 0f 1f 84 00 00 00 00 00 f3 0f 1e fa\n'
    '0x00007f019d5c92b4:   55 48 89 e5 41 56 45 89 c6 41 55 49 89 cd 41 54\n'
    '0x00007f019d5c92c4:   49 89 d4 53 48 8d 9f 40 fc ff ff 8b 83 70 04 00\n'
    '\n'
    '\n'
    'Stack slot to memory mapping:\n'
    '\n'
    "stack at sp + 0 slots: {method} {0x00007f0123442168} 'putLong' '(Ljava/lang/Object;JJ)V' in 'jdk/internal/misc/Unsafe'\n"
    'stack at sp + 1 slots: 0x0 is null\n'
    'stack at sp + 2 slots: 0x00007f0123be0598 is pointing into metadata\n'
    'stack at sp + 3 slots: 0x00007f019c5fe760 is pointing into the stack for thread: 0x00007f019801a960\n'
    'stack at sp + 4 slots: 0x00007f019c5fe718 is pointing into the stack for thread: 0x00007f019801a960\n'
    'stack at sp + 5 slots: 0x00007f0187d43ac0 is at code_begin+1056 in an Interpreter codelet\n'
    'native method entry point (kind = native)  [0x00007f0187d436a0, 0x00007f0187d44210]  2928 bytes\n'
    'stack at sp + 6 slots: 0x0 is null\n'
    'stack at sp + 7 slots: 0x00007f0123be0598 is pointing into metadata\n'
    '\n'
    '\n'
    '---------------  P R O C E S S  ---------------\n'
    '\n'
    'Threads class SMR info:\n'
    '_java_thread_list=0x00007f0198176e20, length=10, elements={\n'
    '0x00007f019801a960, 0x00007f019812e330, 0x00007f019812f8b0, 0x00007f0198131060,\n'
    '0x00007f01981326b0, 0x00007f0198133c60, 0x00007f01981357b0, 0x00007f0198136e80,\n'
    '0x00007f0198172d10, 0x00007f0198175c90\n'
    '}\n'
    '\n'
    'Java Threads: ( => current thread )\n'
    '=>0x00007f019801a960 JavaThread "main"                              [_thread_in_vm, id=9681, stack(0x00007f019c500000,0x00007f019c600000) (1024K)]\n'
    '  0x00007f019812e330 JavaThread "Reference Handler"          daemon [_thread_blocked, id=9689, stack(0x00007f01714f8000,0x00007f01715f8000) (1024K)]\n'
    '  0x00007f019812f8b0 JavaThread "Finalizer"                  daemon [_thread_blocked, id=9690, stack(0x00007f01713f8000,0x00007f01714f8000) (1024K)]\n'
    '  0x00007f0198131060 JavaThread "Signal Dispatcher"          daemon [_thread_blocked, id=9691, stack(0x00007f01712f8000,0x00007f01713f8000) (1024K)]\n'
    '  0x00007f01981326b0 JavaThread "Service Thread"             daemon [_thread_blocked, id=9692, stack(0x00007f01711f8000,0x00007f01712f8000) (1024K)]\n'
    '  0x00007f0198133c60 JavaThread "Monitor Deflation Thread"   daemon [_thread_blocked, id=9693, stack(0x00007f01710f8000,0x00007f01711f8000) (1024K)]\n'
    '  0x00007f01981357b0 JavaThread "C2 CompilerThread0"         daemon [_thread_blocked, id=9694, stack(0x00007f0170ff8000,0x00007f01710f8000) (1024K)]\n'
    '  0x00007f0198136e80 JavaThread "C1 CompilerThread0"         daemon [_thread_blocked, id=9695, stack(0x00007f0170ef8000,0x00007f0170ff8000) (1024K)]\n'
    '  0x00007f0198172d10 JavaThread "Notification Thread"        daemon [_thread_blocked, id=9696, stack(0x00007f0170df8000,0x00007f0170ef8000) (1024K)]\n'
    '  0x00007f0198175c90 JavaThread "Common-Cleaner"             daemon [_thread_blocked, id=9697, stack(0x00007f0170cf8000,0x00007f0170df8000) (1024K)]\n'
    'Total: 10\n'
    '\n'
    'Other Threads:\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '---------------  S Y S T E M  ---------------\n'
    '\n'
    'OS:\n'
    'DISTRIB_ID=Ubuntu\n'
    'DISTRIB_RELEASE=24.04\n'
    'DISTRIB_CODENAME=noble\n'
    'DISTRIB_DESCRIPTION="Ubuntu 24.04.5 LTS"\n'
    'uname: Linux 6.18.44-fc-v233 #1 SMP PREEMPT_DYNAMIC @0 x86_64\n'
    'OS uptime: 0 days 0:21 hours\n'
    'libc: glibc 2.39 NPTL 2.39 \n'
    'rlimit (soft/hard): STACK 8192k/infinity , CORE 0k/infinity , NPROC 64276/64276 , NOFILE 20000/20000 , AS infinity/infinity , CPU infinity/infinity , DATA infinity/infinity , FSIZE infinity/infinity , MEMLOCK 8192k/8192k\n'
    'load average: 1.51 0.87 0.42\n'
    '\n'
    '/proc/meminfo:\n'
    'MemTotal:       16477348 kB\n'
    'MemFree:        10753472 kB\n'
    'MemAvailable:   15651816 kB\n'
    'Buffers:          197828 kB\n'
    'Cached:          4803428 kB\n'
    'SwapCached:            0 kB\n'
    'Active:          1789820 kB\n'
    'Inactive:        3573816 kB\n'
    'Active(anon):        112 kB\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    'vm_info: OpenJDK 64-Bit Server VM (21.0.12.1+1-1-24.04.4-Ubuntu) for linux-amd64 JRE (21.0.12.1+1-1-24.04.4-Ubuntu), built on 2026-09-04T06:14:02Z by "buildd" with gcc 13.3.0\n'
    '\n'
    'END.\n'
)
FAULTHANDLER_REPORT = (
    'Fatal Python error: Segmentation fault\n'
    '\n'
    'Thread 0x00007fe2d789d6c0 (most recent call first):\n'
    '  File "/work/app/fh.py", line 5 in worker\n'
    '  File "/usr/local/lib/python3.13/threading.py", line 995 in run\n'
    '  File "/usr/local/lib/python3.13/threading.py", line 1044 in _bootstrap_inner\n'
    '  File "/usr/local/lib/python3.13/threading.py", line 1015 in _bootstrap\n'
    '\n'
    'Current thread 0x00007fe2d7e6e740 (most recent call first):\n'
    '  File "/usr/local/lib/python3.13/ctypes/__init__.py", line 551 in string_at\n'
    '  File "/work/app/fh.py", line 8 in deref\n'
    '  File "/work/app/fh.py", line 12 in process\n'
    '  File "/work/app/fh.py", line 15 in <module>\n'
)

_C_STUB = re.compile(r"^\s*(?:\[\s*\d+\.\d+\] )?\[ctxprune: (\d+) lines elided \((.*)\)\]$")
_HS_SEC = re.compile(r"^-{15}  ((?:[A-Z] )*[A-Z]) +-+$")
_HS_THREAD_ELIDE = ("Registers:", "Register to memory mapping:", "Top of Stack:", "Instructions:", "Stack slot to memory mapping:")


_KEEP_ALWAYS = re.compile(r"\b(?:SIG[A-Z]+|EXC_[A-Z_]+|EXCEPTION_[A-Z_]+|Oops:|BUG:|Kernel panic|Fatal Python error|Abort message|Problematic frame|Exception Type|Exception Codes|Termination Reason|Crashed Thread|Cause:|Call Trace:)|^(?:# JRE version|# Java VM|# Problematic|# V |# C |Process:|Identifier:|pid: |Cmdline:|vm_info:|Native frames:|Java frames:|siginfo:|Current thread|backtrace:|Thread \d+ Crashed|Thread 0x)")


def _stub(n: int, what: str, prefix: str = "") -> str:
    return f"{prefix}[ctxprune: {n} lines elided ({what})]"


_FRAME_LIKE = re.compile(r"^[VvjJC]  \S|^\s+#\d+ pc |^\[\s*\d+\.\d+\]\s+(?:\? )?[\w.]+\+0x[0-9a-f]+/0x")


def _elide(block: list[str], what: str, prefix: str = "", frames: bool = True) -> list[str]:
    """One stub for a block, but a line that names the signal, exception or cause (or, where frames never belong, a
    stack frame) is never swallowed: a damaged or foreign block title must not hide the cause."""
    keep = [l for l in block if _KEEP_ALWAYS.search(l) or (frames and _FRAME_LIKE.match(l))]
    return [_stub(len(block) - len(keep), what, prefix)] + keep if len(block) - len(keep) >= 1 else list(block)


def _hs_err(lines):
    out, i, n, sec = [], 0, len(lines), None
    while i < n:
        l = lines[i]
        if m := _HS_SEC.match(l):
            sec = m.group(1).replace(" ", "")
            out.append(l)
            i += 1
            continue
        if sec == "THREAD" and l.startswith(_HS_THREAD_ELIDE):
            j = i + 1
            while j < n and lines[j].strip() and not _HS_SEC.match(lines[j]):
                j += 1
            if j - i - 1 >= 2:
                out += [l] + _elide(lines[i + 1:j], l.split(":")[0])
            else:
                out += lines[i:j]
            i = j
        elif sec in ("PROCESS", "SYSTEM"):
            j = i
            while j < n and not _HS_SEC.match(lines[j]) and not lines[j].startswith(("vm_info:", "END.")):
                j += 1
            if len([x for x in lines[i:j] if x.strip()]) >= 3:
                out += _elide(lines[i:j], f"{sec.title()} section")
            else:
                out += lines[i:j]
            if j < n and not _HS_SEC.match(lines[j]):
                out.append(lines[j])  # the vm_info: / END. line itself
                j += 1
            i = j
        else:
            out.append(l)
            i += 1
    return out


_MAC_TITLE = re.compile(r"^(Thread \d+( Crashed)?::?.*|Thread \d+ crashed with .*|Binary Images:|VM Region Summary:|External Modification Summary:|Model: .*)$")


def _mac(lines):
    out, i, n = [], 0, len(lines)
    while i < n:
        l = lines[i]
        if _MAC_TITLE.match(l) and not re.match(r"^Thread \d+ Crashed", l):
            j = i + 1
            while j < n and lines[j].strip():
                j += 1
            if j - i - 1 >= 3:
                out += [l] + _elide(lines[i + 1:j], l.split(":")[0].split(" crashed")[0] if not l.startswith("Thread") else l.split(":")[0], frames=False)
            else:
                out += lines[i:j]
            i = j
        else:
            out.append(l)
            i += 1
    return out


_OOPS_PREFIX = re.compile(r"^(\[\s*\d+\.\d+\] )")
_OOPS_REG = re.compile(r"^\[\s*\d+\.\d+\] (?:RSP|RAX|RDX|RBP|R10|R13|FS|CS|CR2|CR0|DR0|DR3|R08|RBX|RSI|R11|R14|ES):")


def _oops(lines):
    out, i, n = [], 0, len(lines)
    while i < n:
        l = lines[i]
        pre = (_OOPS_PREFIX.match(l) or [None, ""])[1]
        if _OOPS_REG.match(l) or l[len(pre):].startswith("Code:") or l[len(pre):].startswith("Modules linked in:"):
            j = i + 1
            if _OOPS_REG.match(l):
                while j < n and _OOPS_REG.match(lines[j]):
                    j += 1
            what = "registers" if _OOPS_REG.match(l) else l[len(pre):].split(":")[0]
            if _OOPS_REG.match(l) and j - i < 3:
                out += lines[i:j]
            else:
                out += _elide(lines[i:j], what, pre)
            i = j
        else:
            out.append(l)
            i += 1
    return out


_TOMB_REGBLOCK = re.compile(r"^    (?:x\d+|sp|pc|lr|fp)\s+[0-9a-f]{16}")
_TOMB_SEC = re.compile(r"^(memory near .*:|memory map \(\d+ entries?\):.*|open files:|stack:|backtrace:|other threads?:|--- --- ---.*|\S.*:)$")


def _tomb(lines):
    out, i, n = [], 0, len(lines)
    while i < n:
        l = lines[i]
        if _TOMB_REGBLOCK.match(l):
            j = i
            while j < n and _TOMB_REGBLOCK.match(lines[j]):
                j += 1
            if j - i >= 3:
                out += _elide(lines[i:j], "registers", "    ")
                i = j
            else:
                out.append(lines[i])
                i += 1
        elif re.match(r"^(memory near .*:|memory map \(\d+ entries?\):.*|open files:|stack:)$", l):
            j = i + 1
            while j < n and lines[j].startswith(("    ", "\t")) and lines[j].strip():
                j += 1
            if j - i - 1 >= 3:
                out += [l] + _elide(lines[i + 1:j], l.split(" (")[0].rstrip(":"), "    ")
            else:
                out += lines[i:j]
            i = j
        else:
            out.append(l)
            i += 1
    return out


def triage_crash(text: str) -> str:
    lines = text.split("\n")
    if any(_C_STUB.match(l) for l in lines):
        return text
    if lines[0:3] and any(l.startswith("# A fatal error has been detected by the Java Runtime Environment") for l in lines[:3]):
        out = _hs_err(lines)
    elif any(l == "Binary Images:" for l in lines) and any(l.startswith("Exception Type:") for l in lines):
        out = _mac(lines)
    elif any(re.match(r"^\[\s*\d+\.\d+\] (BUG:|Oops:)", l) for l in lines):
        out = _oops(lines)
    elif lines and lines[0].startswith("*** *** ***") and "backtrace:" in lines:
        out = _tomb(lines)
    else:
        return text
    return shrink_only(text, "\n".join(out))


def probes_427(text: str) -> dict:
    lines = text.split("\n")
    kept = [l for l in lines if not _C_STUB.match(l)]
    accounted = len(kept) + sum(int(m.group(1)) for l in lines if (m := _C_STUB.match(l)))
    sig = [l.strip() for l in kept if re.search(r"\b(?:SIG[A-Z]+|EXC_[A-Z_]+|EXCEPTION_[A-Z_]+|Oops:|BUG:|Kernel panic|Fatal Python error|Abort message|Problematic frame|Exception Type|Exception Codes|Termination Reason|Crashed Thread|Cause:)", l)]
    frames: list = []
    starts = re.compile(r"^(Native frames:|Java frames:|backtrace:|Thread \d+ Crashed|Current thread|Thread 0x)|^\[\s*\d+\.\d+\] Call Trace:")
    oops_frame = re.compile(r"^\[\s*\d+\.\d+\]\s+(?:</?TASK>|\??\s*[\w.]+\+0x[0-9a-f]+/0x[0-9a-f]+.*)$")
    k = 0
    while k < len(kept):
        l = kept[k]
        if starts.match(l):
            frames.append((l.split("(")[0].strip(),))
            is_oops = "Call Trace:" in l
            k += 1
            while k < len(kept) and kept[k].strip() and not starts.match(kept[k]) and (not is_oops or oops_frame.match(kept[k])):
                if re.match(r"^(?:[VvjJC]  \S|\s+#\d+ pc |\d+\s+\S+\s+0x[0-9a-f]+ |\s+File \"|\[\s*\d+\.\d+\])", kept[k]):
                    frames.append(kept[k].strip())
                k += 1
        else:
            k += 1
    ident = [l.strip() for l in kept if re.match(r"^(# JRE version|# Java VM|# Problematic|# V |# C |Process:|Identifier:|Version:|pid: |Cmdline:|Build fingerprint|Timestamp:|\[\s*\d+\.\d+\] (?:CPU:|RIP:|Hardware name)|vm_info:|Current thread|siginfo:)", l)]
    return {"lines accounted for (kept + elided)": accounted, "signal / exception / cause lines": sig, "crashing-thread frames": frames, "identity lines": ident}


def _mac_report() -> str:  # reconstructed from memory: macOS (arm64) .crash text format, EXC_BAD_ACCESS in the main thread
    head = ["Process:               Notes [4217]", "Path:                  /Applications/Notes.app/Contents/MacOS/Notes", "Identifier:            com.example.Notes", "Version:               3.2 (320)",
            "Code Type:             ARM-64 (Native)", "Parent Process:        launchd [1]", "", "Date/Time:             2024-01-10 12:00:01.123 +0000", "OS Version:            macOS 14.2.1 (23C71)", "",
            "Exception Type:        EXC_BAD_ACCESS (SIGSEGV)", "Exception Codes:       KERN_INVALID_ADDRESS at 0x0000000000000010", "Termination Reason:    Namespace SIGNAL, Code 11 Segmentation fault: 11", "Triggered by Thread:   0", ""]
    crashed = ["Thread 0 Crashed::  Dispatch queue: com.apple.main-thread", "0   Notes                         \t0x0000000102a1c3f4 -[NoteStore saveNote:] + 52", "1   Notes                         \t0x0000000102a18a10 -[EditorController commit] + 124",
               "2   AppKit                        \t0x00000001a2b3c4d8 -[NSApplication sendAction:to:from:] + 108", "3   AppKit                        \t0x00000001a2b3c000 -[NSControl sendAction:to:] + 96", "4   dyld                          \t0x00000001a0f2d0e0 start + 2360", ""]
    others = []
    for t in range(1, 6):
        others += [f"Thread {t}::  com.apple.network.worker-{t}", f"0   libsystem_kernel.dylib        \t0x00000001a0f6a{t}00 mach_msg2_trap + 8", f"1   libsystem_kernel.dylib        \t0x00000001a0f7c{t}10 mach_msg2_internal + 76",
                   f"2   libsystem_kernel.dylib        \t0x00000001a0f7b{t}28 mach_msg_overwrite + 540", f"3   CoreFoundation                \t0x00000001a1022{t}34 __CFRunLoopServiceMachPort + 160", f"4   libsystem_pthread.dylib       \t0x00000001a0fa8{t}18 thread_start + 8", ""]
    regs = ["Thread 0 crashed with ARM Thread State (64-bit):"] + [f"    x{i}: 0x{(i * 0x1111111) % 2**64:016x}   x{i + 1}: 0x{((i + 1) * 0x2222222) % 2**64:016x}   x{i + 2}: 0x{((i + 2) * 0x3333333) % 2**64:016x}" for i in range(0, 27, 3)] + ["     fp: 0x000000016fdff420   lr: 0x0000000102a18a10", "     sp: 0x000000016fdff3f0   pc: 0x0000000102a1c3f4 cpsr: 0x80001000", ""]
    imgs = ["Binary Images:"] + [f"       0x{0x102a10000 + k * 0x40000:x} -        0x{0x102a10000 + k * 0x40000 + 0x3ffff:x} {'com.apple.framework' + str(k)} (1.0 - {k}) <{k:08x}-1111-2222-3333-444455556666> /System/Library/Frameworks/Framework{k}.framework/Versions/A/Framework{k}" for k in range(60)] + [""]
    tail = ["VM Region Summary:", "ReadOnly portion of Libraries: Total=1.2G resident=0K(0%) swapped_out_or_unallocated=1.2G(100%)", "Writable regions: Total=352.6M written=1088K(0%) resident=1088K(0%) swapped_out=0K(0%) unallocated=351.6M(100%)",
            "                                VIRTUAL   REGION ", "REGION TYPE                        SIZE    COUNT (non-coalesced) ", "===========                     =======  ======= ", "Kernel Alloc Once                   32K        1 ", "MALLOC                           176.2M       19 ", "STACK GUARD                       56.0M        7 ", ""]
    return "\n".join(head + crashed + others + regs + imgs + tail)


def _oops_report() -> str:  # reconstructed from memory: x86_64 kernel NULL dereference in a module init
    t = [f"[ {1234 + k // 100}.{k % 100:02d}0000] " for k in range(60)]
    L = ["BUG: kernel NULL pointer dereference, address: 0000000000000008", "#PF: supervisor read access in kernel mode", "#PF: error_code(0x0000) - not-present page", "PGD 0 P4D 0 ",
         "Oops: 0000 [#1] PREEMPT SMP NOPTI", "CPU: 2 PID: 3141 Comm: insmod Tainted: G           OE      6.5.0-14-generic #14-Ubuntu", "Hardware name: QEMU Standard PC (Q35 + ICH9, 2009), BIOS 1.16.2-debian-1.16.2-1 04/01/2014",
         "RIP: 0010:demo_init+0x1a/0x1000 [demo]", "Code: 48 8b 04 25 08 00 00 00 48 89 c7 e8 00 00 00 00 31 c0 c3 cc cc cc cc cc cc cc cc cc cc cc cc cc 55 48 89 e5 53 48 83 ec 08 <48> 8b 40 08 48 85 c0",
         "RSP: 0018:ffffb3a2c1a3fd48 EFLAGS: 00010246", "RAX: 0000000000000000 RBX: ffffffffc0a52000 RCX: 0000000000000000", "RDX: 0000000000000000 RSI: ffffb3a2c1a3fd48 RDI: ffffffffc0a52000",
         "RBP: ffffb3a2c1a3fd58 R08: 0000000000000000 R09: 0000000000000000", "R10: 0000000000000000 R11: 0000000000000000 R12: ffffffffc0a52000", "R13: 0000000000000000 R14: 0000000000000000 R15: ffff9c1e8a5c4000",
         "FS:  00007f7a3b4c0740(0000) GS:ffff9c1ebbd00000(0000) knlGS:0000000000000000", "CS:  0010 DS: 0000 ES: 0000 CR0: 0000000080050033", "CR2: 0000000000000008 CR3: 0000000102f3a000 CR4: 0000000000750ee0",
         "Call Trace:", " <TASK>", " ? __die+0x23/0x70", " ? page_fault_oops+0x171/0x4e0", " ? do_user_addr_fault+0x30f/0x6c0", " ? exc_page_fault+0x7f/0x180", " ? asm_exc_page_fault+0x27/0x30",
         " ? demo_init+0x1a/0x1000 [demo]", " do_one_initcall+0x5b/0x340", " do_init_module+0x68/0x260", " load_module+0xb8e/0xc20", " init_module_from_file+0x96/0x100", " __x64_sys_finit_module+0x18/0x30",
         " </TASK>", "Modules linked in: demo(OE+) " + " ".join(f"mod{k}_{k * 7}" for k in range(70)), "CR2: 0000000000000008", "---[ end trace 0000000000000000 ]---"]
    return "\n".join(t[i] + l for i, l in enumerate(L)) + "\n"


def _tomb_report() -> str:  # reconstructed from memory: Android 13 tombstone, SIGSEGV in a native render thread
    h = ["*** *** *** *** *** *** *** *** *** *** *** *** *** *** *** ***", "Build fingerprint: 'google/raven/raven:13/TQ3A.230901.001/10750268:user/release-keys'", "Revision: 'MP1.0'", "ABI: 'arm64'",
         "Timestamp: 2024-01-10 12:00:01.123456789+0000", "Process uptime: 12s", "Cmdline: com.example.app", "pid: 12345, tid: 12378, name: RenderThread  >>> com.example.app <<<", "uid: 10123",
         "signal 11 (SIGSEGV), code 1 (SEGV_MAPERR), fault addr 0x0000000000000010", "Cause: null pointer dereference"]
    regs = [f"    x{i}  {(i * 0x1010101) % 2**64:016x}  x{i + 1}  {((i + 1) * 0x2020202) % 2**64:016x}  x{i + 2}  {((i + 2) * 0x3030303) % 2**64:016x}" for i in range(0, 27, 3)] + ["    lr   0000007b2c4e1a30  sp   0000007b27af7f40  pc   0000007b2c4e19f0  pst  0000000060001000", ""]
    bt = ["backtrace:", "      #00 pc 000000000004a1f0  /system/lib64/libhwui.so (android::uirenderer::RenderNode::prepareTree+32) (BuildId: 5a2b1c3d4e5f60718293a4b5c6d7e8f9)",
          "      #01 pc 00000000000491a8  /system/lib64/libhwui.so (android::uirenderer::renderthread::CanvasContext::prepareTree+144) (BuildId: 5a2b1c3d4e5f60718293a4b5c6d7e8f9)",
          "      #02 pc 000000000003d7c4  /system/lib64/libhwui.so (android::uirenderer::renderthread::DrawFrameTask::run+196) (BuildId: 5a2b1c3d4e5f60718293a4b5c6d7e8f9)",
          "      #03 pc 00000000000b1fe4  /apex/com.android.runtime/lib64/bionic/libc.so (__pthread_start+264) (BuildId: aa11bb22cc33dd44ee55ff6600778899)", ""]
    mem = []
    for r in ("x0", "x1", "x20"):
        mem += [f"memory near {r}:"] + [f"    0000007b27af{k:04x}0 {k * 0x101:016x} {k * 0x202:016x}  ................" for k in range(1, 10)] + [""]
    mmap = ["memory map (96 entries):"] + [f"    {0x12c00000 + k * 0x100000:08x}-{0x12c00000 + k * 0x100000 + 0xfffff:08x} rw-  {k * 0x1000:x}  100000  [anon:dalvik-main space {k}]" for k in range(96)] + [""]
    files = ["open files:", "    fd 0: /dev/null (unowned)", "    fd 1: /dev/null (unowned)", "    fd 2: /dev/null (unowned)", "    fd 3: /data/app/com.example.app/base.apk (owned by FILE 0x7b2d1a2000)", "    fd 4: /dev/binder (unowned)", ""]
    return "\n".join(h + regs + bt + mem + mmap + files)


MAC_CRASH = _mac_report()
LINUX_OOPS = _oops_report()
ANDROID_TOMBSTONE = _tomb_report()

case(
    427,
    claim="In a JVM hs_err log, macOS crash report, Linux oops or Android tombstone, register dumps, memory-to-register maps, stack dumps, the whole Process/System sections, other threads' stacks, binary-image lists, memory maps, open files, the module list and 'Code:' bytes become '[ctxprune: N lines elided (Section)]'; the header, signal/exception/cause lines, the crashing thread's frames and the identity lines are unchanged, and kept lines plus the stubs' counts add up to the original line count.",
    loses="register values, memory contents around registers, the loaded-library, module and memory-map lists, the other threads' stacks and heap/GC/environment/OS details (all in the saved original)",
    encode=triage_crash,
    probes=probes_427,
    fixtures=lambda: [
        Fixture("JVM 21 hs_err: SIGSEGV in Unsafe_PutLong (real, long sections cut to 10-22 lines)", HS_ERR_CUT, True),
        Fixture("macOS .crash with EXC_BAD_ACCESS, 5 other threads, 60 binary images (reconstructed from memory)", MAC_CRASH, True),
        Fixture("Linux oops: NULL dereference in a module init, registers, Code:, 70 modules (reconstructed from memory)", LINUX_OOPS, True),
        Fixture("Android tombstone: SIGSEGV, registers, memory near x0/x1/x20, 96-entry map (reconstructed from memory)", ANDROID_TOMBSTONE, True),
        Fixture("Python faulthandler report: nothing to elide (real)", FAULTHANDLER_REPORT, False),
        Fixture("an ordinary stack trace", "Exception in thread \"main\" java.lang.NullPointerException\n\tat Foo.bar(Foo.java:12)\n\tat Foo.main(Foo.java:5)\n", False),
        Fixture("a report that already has a stub", "# A fatal error has been detected by the Java Runtime Environment:\n#\n#  SIGSEGV (0xb) at pc=0x1, pid=1, tid=1\n[ctxprune: 4 lines elided (Registers)]\n", None),
    ],
    min_gain=0.4,
)



# ===============================================================================================================
# #498 Show lint and typecheck findings only in git-diff-touched files. Modelled as a transform over the (diff,
# findings) bundle a hook would assemble: '=== git diff -U0 ===' followed by one '=== <tool> ===' section per
# linter. Keeps findings in touched files, then a count and the first three of the rest, plus (the addition this
# case needed) every outside finding that names a def/class the diff changed. The real capture shows why: a
# signature change in api.py breaks src/zz_caller.py, and mypy lists that error 17th of 17, not in the first three.
# The diff section itself is replaced by one '[498] diff touches ...' line (it was the filter's input, the agent can
# read `git diff` itself). Real repo: 40 files, 200 ruff findings, 17 mypy errors, 3 files edited.

LINT_DIFF_BUNDLE = (
    '=== git diff -U0 ===\n'
    'diff --git a/src/api.py b/src/api.py\n'
    'index 18a0b42..d46294e 100644\n'
    '--- a/src/api.py\n'
    '+++ b/src/api.py\n'
    '@@ -1,2 +1,2 @@\n'
    '-def parse(x: int) -> int:\n'
    '-    return x + 1\n'
    '+def parse(x: int, strict: bool) -> int:\n'
    '+    return x + (2 if strict else 1)\n'
    'diff --git a/src/m03.py b/src/m03.py\n'
    'index 53972a5..39f6db2 100644\n'
    '--- a/src/m03.py\n'
    '+++ b/src/m03.py\n'
    '@@ -8,0 +9 @@ def work_m03(items: List[int]) -> int:\n'
    '+    extra = 2\n'
    'diff --git a/src/util.py b/src/util.py\n'
    'index 9877444..0cf3cb3 100644\n'
    '--- a/src/util.py\n'
    '+++ b/src/util.py\n'
    '@@ -1,0 +2 @@ import re\n'
    '+import os\n'
    '=== ruff check src --select F,E --output-format concise ===\n'
    'src/m00.py:1:8: F401 [*] `os` imported but unused\n'
    'src/m00.py:2:8: F401 [*] `sys` imported but unused\n'
    'src/m00.py:3:8: F401 [*] `json` imported but unused\n'
    'src/m00.py:8:5: F841 Local variable `unused` is assigned to but never used\n'
    'src/m00.py:12:17: E711 Comparison to `None` should be `cond is None`\n'
    'src/m00.py:17:5: F841 Local variable `c` is assigned to but never used\n'
    'src/m01.py:1:8: F401 [*] `os` imported but unused\n'
    'src/m01.py:2:8: F401 [*] `sys` imported but unused\n'
    'src/m01.py:3:8: F401 [*] `json` imported but unused\n'
    'src/m01.py:8:5: F841 Local variable `unused` is assigned to but never used\n'
    'src/m01.py:12:17: E711 Comparison to `None` should be `cond is None`\n'
    'src/m02.py:1:8: F401 [*] `os` imported but unused\n'
    'src/m02.py:2:8: F401 [*] `sys` imported but unused\n'
    'src/m02.py:3:8: F401 [*] `json` imported but unused\n'
    'src/m02.py:8:5: F841 Local variable `unused` is assigned to but never used\n'
    'src/m02.py:12:17: E711 Comparison to `None` should be `cond is None`\n'
    'src/m03.py:1:8: F401 [*] `os` imported but unused\n'
    'src/m03.py:2:8: F401 [*] `sys` imported but unused\n'
    'src/m03.py:3:8: F401 [*] `json` imported but unused\n'
    'src/m03.py:8:5: F841 Local variable `unused` is assigned to but never used\n'
    'src/m03.py:9:5: F841 Local variable `extra` is assigned to but never used\n'
    'src/m03.py:13:17: E711 Comparison to `None` should be `cond is None`\n'
    'src/m03.py:18:5: F841 Local variable `c` is assigned to but never used\n'
    'src/m04.py:1:8: F401 [*] `os` imported but unused\n'
    'src/m04.py:2:8: F401 [*] `sys` imported but unused\n'
    'src/m04.py:3:8: F401 [*] `json` imported but unused\n'
    'src/m04.py:8:5: F841 Local variable `unused` is assigned to but never used\n'
    'src/m04.py:12:17: E711 Comparison to `None` should be `cond is None`\n'
    'src/m04.py:17:5: F841 Local variable `c` is assigned to but never used\n'
    'src/m05.py:1:8: F401 [*] `os` imported but unused\n'
    'src/m05.py:2:8: F401 [*] `sys` imported but unused\n'
    'src/m05.py:3:8: F401 [*] `json` imported but unused\n'
    'src/m05.py:8:5: F841 Local variable `unused` is assigned to but never used\n'
    'src/m05.py:12:17: E711 Comparison to `None` should be `cond is None`\n'
    'src/m05.py:17:5: F841 Local variable `c` is assigned to but never used\n'
    'src/m06.py:1:8: F401 [*] `os` imported but unused\n'
    'src/m06.py:2:8: F401 [*] `sys` imported but unused\n'
    'src/m06.py:3:8: F401 [*] `json` imported but unused\n'
    'src/m06.py:8:5: F841 Local variable `unused` is assigned to but never used\n'
    'src/m06.py:12:17: E711 Comparison to `None` should be `cond is None`\n'
    'src/m06.py:17:5: F841 Local variable `c` is assigned to but never used\n'
    'src/m07.py:1:8: F401 [*] `os` imported but unused\n'
    'src/m07.py:2:8: F401 [*] `sys` imported but unused\n'
    'src/m07.py:3:8: F401 [*] `json` imported but unused\n'
    'src/m07.py:8:5: F841 Local variable `unused` is assigned to but never used\n'
    'src/m07.py:12:17: E711 Comparison to `None` should be `cond is None`\n'
    'src/m07.py:17:5: F841 Local variable `c` is assigned to but never used\n'
    'src/m08.py:1:8: F401 [*] `os` imported but unused\n'
    'src/m08.py:2:8: F401 [*] `sys` imported but unused\n'
    'src/m08.py:3:8: F401 [*] `json` imported but unused\n'
    'src/m08.py:8:5: F841 Local variable `unused` is assigned to but never used\n'
    'src/m08.py:12:17: E711 Comparison to `None` should be `cond is None`\n'
    'src/m09.py:1:8: F401 [*] `os` imported but unused\n'
    'src/m09.py:2:8: F401 [*] `sys` imported but unused\n'
    'src/m09.py:3:8: F401 [*] `json` imported but unused\n'
    'src/m09.py:8:5: F841 Local variable `unused` is assigned to but never used\n'
    'src/m09.py:12:17: E711 Comparison to `None` should be `cond is None`\n'
    'src/m10.py:1:8: F401 [*] `os` imported but unused\n'
    'src/m10.py:2:8: F401 [*] `sys` imported but unused\n'
    'src/m10.py:3:8: F401 [*] `json` imported but unused\n'
    'src/m10.py:8:5: F841 Local variable `unused` is assigned to but never used\n'
    'src/m10.py:12:17: E711 Comparison to `None` should be `cond is None`\n'
    'src/m11.py:1:8: F401 [*] `os` imported but unused\n'
    'src/m11.py:2:8: F401 [*] `sys` imported but unused\n'
    'src/m11.py:3:8: F401 [*] `json` imported but unused\n'
    'src/m11.py:8:5: F841 Local variable `unused` is assigned to but never used\n'
    'src/m11.py:12:17: E711 Comparison to `None` should be `cond is None`\n'
    'src/m12.py:1:8: F401 [*] `os` imported but unused\n'
    'src/m12.py:2:8: F401 [*] `sys` imported but unused\n'
    'src/m12.py:3:8: F401 [*] `json` imported but unused\n'
    'src/m12.py:8:5: F841 Local variable `unused` is assigned to but never used\n'
    'src/m12.py:12:17: E711 Comparison to `None` should be `cond is None`\n'
    'src/m12.py:17:5: F841 Local variable `c` is assigned to but never used\n'
    'src/m13.py:1:8: F401 [*] `os` imported but unused\n'
    'src/m13.py:2:8: F401 [*] `sys` imported but unused\n'
    'src/m13.py:3:8: F401 [*] `json` imported but unused\n'
    'src/m13.py:8:5: F841 Local variable `unused` is assigned to but never used\n'
    'src/m13.py:12:17: E711 Comparison to `None` should be `cond is None`\n'
    'src/m13.py:17:5: F841 Local variable `c` is assigned to but never used\n'
    'src/m14.py:1:8: F401 [*] `os` imported but unused\n'
    'src/m14.py:2:8: F401 [*] `sys` imported but unused\n'
    'src/m14.py:3:8: F401 [*] `json` imported but unused\n'
    'src/m14.py:8:5: F841 Local variable `unused` is assigned to but never used\n'
    'src/m14.py:12:17: E711 Comparison to `None` should be `cond is None`\n'
    'src/m14.py:17:5: F841 Local variable `c` is assigned to but never used\n'
    'src/m15.py:1:8: F401 [*] `os` imported but unused\n'
    'src/m15.py:2:8: F401 [*] `sys` imported but unused\n'
    'src/m15.py:3:8: F401 [*] `json` imported but unused\n'
    'src/m15.py:8:5: F841 Local variable `unused` is assigned to but never used\n'
    'src/m15.py:12:17: E711 Comparison to `None` should be `cond is None`\n'
    'src/m16.py:1:8: F401 [*] `os` imported but unused\n'
    'src/m16.py:2:8: F401 [*] `sys` imported but unused\n'
    'src/m16.py:3:8: F401 [*] `json` imported but unused\n'
    'src/m16.py:8:5: F841 Local variable `unused` is assigned to but never used\n'
    'src/m16.py:12:17: E711 Comparison to `None` should be `cond is None`\n'
    'src/m17.py:1:8: F401 [*] `os` imported but unused\n'
    'src/m17.py:2:8: F401 [*] `sys` imported but unused\n'
    'src/m17.py:3:8: F401 [*] `json` imported but unused\n'
    'src/m17.py:8:5: F841 Local variable `unused` is assigned to but never used\n'
    'src/m17.py:12:17: E711 Comparison to `None` should be `cond is None`\n'
    'src/m17.py:17:5: F841 Local variable `c` is assigned to but never used\n'
    'src/m18.py:1:8: F401 [*] `os` imported but unused\n'
    'src/m18.py:2:8: F401 [*] `sys` imported but unused\n'
    'src/m18.py:3:8: F401 [*] `json` imported but unused\n'
    'src/m18.py:8:5: F841 Local variable `unused` is assigned to but never used\n'
    'src/m18.py:12:17: E711 Comparison to `None` should be `cond is None`\n'
    'src/m19.py:1:8: F401 [*] `os` imported but unused\n'
    'src/m19.py:2:8: F401 [*] `sys` imported but unused\n'
    'src/m19.py:3:8: F401 [*] `json` imported but unused\n'
    'src/m19.py:8:5: F841 Local variable `unused` is assigned to but never used\n'
    'src/m19.py:12:17: E711 Comparison to `None` should be `cond is None`\n'
    'src/m19.py:17:5: F841 Local variable `c` is assigned to but never used\n'
    'src/m20.py:1:8: F401 [*] `os` imported but unused\n'
    'src/m20.py:2:8: F401 [*] `sys` imported but unused\n'
    'src/m20.py:3:8: F401 [*] `json` imported but unused\n'
    'src/m20.py:8:5: F841 Local variable `unused` is assigned to but never used\n'
    'src/m20.py:12:17: E711 Comparison to `None` should be `cond is None`\n'
    'src/m20.py:17:5: F841 Local variable `c` is assigned to but never used\n'
    'src/m21.py:1:8: F401 [*] `os` imported but unused\n'
    'src/m21.py:2:8: F401 [*] `sys` imported but unused\n'
    'src/m21.py:3:8: F401 [*] `json` imported but unused\n'
    'src/m21.py:8:5: F841 Local variable `unused` is assigned to but never used\n'
    'src/m21.py:12:17: E711 Comparison to `None` should be `cond is None`\n'
    'src/m22.py:1:8: F401 [*] `os` imported but unused\n'
    'src/m22.py:2:8: F401 [*] `sys` imported but unused\n'
    'src/m22.py:3:8: F401 [*] `json` imported but unused\n'
    'src/m22.py:8:5: F841 Local variable `unused` is assigned to but never used\n'
    'src/m22.py:12:17: E711 Comparison to `None` should be `cond is None`\n'
    'src/m22.py:17:5: F841 Local variable `c` is assigned to but never used\n'
    'src/m23.py:1:8: F401 [*] `os` imported but unused\n'
    'src/m23.py:2:8: F401 [*] `sys` imported but unused\n'
    'src/m23.py:3:8: F401 [*] `json` imported but unused\n'
    'src/m23.py:8:5: F841 Local variable `unused` is assigned to but never used\n'
    'src/m23.py:12:17: E711 Comparison to `None` should be `cond is None`\n'
    'src/m24.py:1:8: F401 [*] `os` imported but unused\n'
    'src/m24.py:2:8: F401 [*] `sys` imported but unused\n'
    'src/m24.py:3:8: F401 [*] `json` imported but unused\n'
    'src/m24.py:8:5: F841 Local variable `unused` is assigned to but never used\n'
    'src/m24.py:12:17: E711 Comparison to `None` should be `cond is None`\n'
    'src/m25.py:1:8: F401 [*] `os` imported but unused\n'
    'src/m25.py:2:8: F401 [*] `sys` imported but unused\n'
    'src/m25.py:3:8: F401 [*] `json` imported but unused\n'
    'src/m25.py:8:5: F841 Local variable `unused` is assigned to but never used\n'
    'src/m25.py:12:17: E711 Comparison to `None` should be `cond is None`\n'
    'src/m26.py:1:8: F401 [*] `os` imported but unused\n'
    'src/m26.py:2:8: F401 [*] `sys` imported but unused\n'
    'src/m26.py:3:8: F401 [*] `json` imported but unused\n'
    'src/m26.py:8:5: F841 Local variable `unused` is assigned to but never used\n'
    'src/m26.py:12:17: E711 Comparison to `None` should be `cond is None`\n'
    'src/m26.py:17:5: F841 Local variable `c` is assigned to but never used\n'
    'src/m27.py:1:8: F401 [*] `os` imported but unused\n'
    'src/m27.py:2:8: F401 [*] `sys` imported but unused\n'
    'src/m27.py:3:8: F401 [*] `json` imported but unused\n'
    'src/m27.py:8:5: F841 Local variable `unused` is assigned to but never used\n'
    'src/m27.py:12:17: E711 Comparison to `None` should be `cond is None`\n'
    'src/m27.py:17:5: F841 Local variable `c` is assigned to but never used\n'
    'src/m28.py:1:8: F401 [*] `os` imported but unused\n'
    'src/m28.py:2:8: F401 [*] `sys` imported but unused\n'
    'src/m28.py:3:8: F401 [*] `json` imported but unused\n'
    'src/m28.py:8:5: F841 Local variable `unused` is assigned to but never used\n'
    'src/m28.py:12:17: E711 Comparison to `None` should be `cond is None`\n'
    'src/m28.py:17:5: F841 Local variable `c` is assigned to but never used\n'
    'src/m29.py:1:8: F401 [*] `os` imported but unused\n'
    'src/m29.py:2:8: F401 [*] `sys` imported but unused\n'
    'src/m29.py:3:8: F401 [*] `json` imported but unused\n'
    'src/m29.py:8:5: F841 Local variable `unused` is assigned to but never used\n'
    'src/m29.py:12:17: E711 Comparison to `None` should be `cond is None`\n'
    'src/m30.py:1:8: F401 [*] `os` imported but unused\n'
    'src/m30.py:2:8: F401 [*] `sys` imported but unused\n'
    'src/m30.py:3:8: F401 [*] `json` imported but unused\n'
    'src/m30.py:8:5: F841 Local variable `unused` is assigned to but never used\n'
    'src/m30.py:12:17: E711 Comparison to `None` should be `cond is None`\n'
    'src/m31.py:1:8: F401 [*] `os` imported but unused\n'
    'src/m31.py:2:8: F401 [*] `sys` imported but unused\n'
    'src/m31.py:3:8: F401 [*] `json` imported but unused\n'
    'src/m31.py:8:5: F841 Local variable `unused` is assigned to but never used\n'
    'src/m31.py:12:17: E711 Comparison to `None` should be `cond is None`\n'
    'src/m32.py:1:8: F401 [*] `os` imported but unused\n'
    'src/m32.py:2:8: F401 [*] `sys` imported but unused\n'
    'src/m32.py:3:8: F401 [*] `json` imported but unused\n'
    'src/m32.py:8:5: F841 Local variable `unused` is assigned to but never used\n'
    'src/m32.py:12:17: E711 Comparison to `None` should be `cond is None`\n'
    'src/m33.py:1:8: F401 [*] `os` imported but unused\n'
    'src/m33.py:2:8: F401 [*] `sys` imported but unused\n'
    'src/m33.py:3:8: F401 [*] `json` imported but unused\n'
    'src/m33.py:8:5: F841 Local variable `unused` is assigned to but never used\n'
    'src/m33.py:12:17: E711 Comparison to `None` should be `cond is None`\n'
    'src/m34.py:1:8: F401 [*] `os` imported but unused\n'
    'src/m34.py:2:8: F401 [*] `sys` imported but unused\n'
    'src/m34.py:3:8: F401 [*] `json` imported but unused\n'
    'src/m34.py:8:5: F841 Local variable `unused` is assigned to but never used\n'
    'src/m34.py:12:17: E711 Comparison to `None` should be `cond is None`\n'
    'src/m34.py:17:5: F841 Local variable `c` is assigned to but never used\n'
    'src/m35.py:1:8: F401 [*] `os` imported but unused\n'
    'src/m35.py:2:8: F401 [*] `sys` imported but unused\n'
    'src/m35.py:3:8: F401 [*] `json` imported but unused\n'
    'src/m35.py:8:5: F841 Local variable `unused` is assigned to but never used\n'
    'src/m35.py:12:17: E711 Comparison to `None` should be `cond is None`\n'
    'src/m35.py:17:5: F841 Local variable `c` is assigned to but never used\n'
    'src/util.py:2:8: F401 [*] `os` imported but unused\n'
    'Found 200 errors.\n'
    '[*] 109 fixable with the `--fix` option (91 hidden fixes can be enabled with the `--unsafe-fixes` option).\n'
    '=== mypy src ===\n'
    'src/m30.py:17: error: Incompatible return value type (got "int", expected "str")  [return-value]\n'
    'src/m25.py:17: error: Incompatible return value type (got "int", expected "str")  [return-value]\n'
    'src/m24.py:17: error: Incompatible return value type (got "int", expected "str")  [return-value]\n'
    'src/m20.py:21: error: Incompatible return value type (got "int", expected "str")  [return-value]\n'
    'src/m17.py:21: error: Incompatible return value type (got "int", expected "str")  [return-value]\n'
    'src/m16.py:17: error: Incompatible return value type (got "int", expected "str")  [return-value]\n'
    'src/m15.py:17: error: Incompatible return value type (got "int", expected "str")  [return-value]\n'
    'src/m12.py:21: error: Incompatible return value type (got "int", expected "str")  [return-value]\n'
    'src/m11.py:17: error: Incompatible return value type (got "int", expected "str")  [return-value]\n'
    'src/m10.py:17: error: Incompatible return value type (got "int", expected "str")  [return-value]\n'
    'src/m09.py:17: error: Incompatible return value type (got "int", expected "str")  [return-value]\n'
    'src/m07.py:21: error: Incompatible return value type (got "int", expected "str")  [return-value]\n'
    'src/m05.py:21: error: Incompatible return value type (got "int", expected "str")  [return-value]\n'
    'src/m02.py:17: error: Incompatible return value type (got "int", expected "str")  [return-value]\n'
    'src/m01.py:17: error: Incompatible return value type (got "int", expected "str")  [return-value]\n'
    'src/m00.py:21: error: Incompatible return value type (got "int", expected "str")  [return-value]\n'
    'src/zz_caller.py:5: error: Missing positional argument "strict" in call to "parse"  [call-arg]\n'
    'Found 17 errors in 17 files (checked 40 source files)\n'
)

_L_SEP = re.compile(r"^=== (.+) ===$")
_L_FINDING = re.compile(r"^([^\s:]+):(\d+):(?:\d+:)? (.*)$")
_L_TOUCH = re.compile(r"^\[498\] diff touches \d+ files?: (.*)$")
_L_OUT = re.compile(r"^\[498\] (\d+) findings outside the diff in \d+ files?; first 3:$")
_L_LINK = re.compile(r"^\[498\] outside findings that mention names the diff changed \(\d+\):$")


def _l_diff_facts(lines: list[str]):
    touched = []
    for l in lines:
        if m := re.match(r"^diff --git a/(.+?) b/(.+)$", l):
            if m.group(2) not in touched:
                touched.append(m.group(2))
    ids = sorted({m.group(1) for l in lines if (m := re.match(r"^[-+]\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)", l))})
    return touched, ids


def _l_sections(lines: list[str]):
    secs, cur = [], (None, [])
    for l in lines:
        if m := _L_SEP.match(l):
            secs.append(cur)
            cur = (m.group(1), [])
        else:
            cur[1].append(l)
    secs.append(cur)
    return secs  # first entry is the preamble (name None)


def filter_findings(text: str) -> str:
    lines = text.split("\n")
    secs = _l_sections(lines)
    diff = next((s for s in secs if s[0] and s[0].startswith("git diff")), None)
    if diff is None:
        return text
    touched, ids = _l_diff_facts(diff[1])
    if not touched:
        return text
    id_rx = re.compile(r"\b(?:" + "|".join(map(re.escape, ids)) + r")\b") if ids else None
    if not any(sum(1 for l in body if (m := _L_FINDING.match(l)) and m.group(1) not in touched) > 4 for name, body in secs[1:] if not name.startswith("git diff")):
        return text  # nothing outside the diff worth hiding: leave the whole bundle, diff included
    out = list(secs[0][1])
    for name, body in secs[1:]:
        if name.startswith("git diff"):
            out.append(f"[498] diff touches {len(touched)} file{'s' if len(touched) != 1 else ''}: {', '.join(touched)}")
            out += [l for l in body if l.strip() and not re.match(r"^(?:diff --git|index |--- |\+\+\+ |@@ |[-+ ])", l)]
            continue
        out.append(f"=== {name} ===")
        found = [(l, _L_FINDING.match(l)) for l in body]
        mine = [l for l, m in found if m and m.group(1) in touched]
        outside = [(l, m) for l, m in found if m and m.group(1) not in touched]
        other = [l for l, m in found if not m]
        if len(outside) <= 4:
            out += body
            continue
        out += mine
        files = {m.group(1) for _, m in outside}
        out += [f"[498] {len(outside)} findings outside the diff in {len(files)} file{'s' if len(files) != 1 else ''}; first 3:"] + [l for l, _ in outside[:3]]
        linked = [l for l, m in outside if id_rx and id_rx.search(m.group(3))]
        if linked:
            out += [f"[498] outside findings that mention names the diff changed ({len(linked)}):"] + linked
        out += other
    return shrink_only(text, "\n".join(out))


def probes_498(text: str) -> dict:
    lines = text.split("\n")
    secs = _l_sections(lines)
    touched, ids = [], []
    for name, body in secs:
        if name and name.startswith("git diff"):
            touched, ids = _l_diff_facts(body)
    for l in lines:
        if m := _L_TOUCH.match(l):
            touched = m.group(1).split(", ")
    per_tool = {}
    for name, body in secs[1:]:
        if name.startswith("git diff"):
            continue
        mine, outside, linked_lines, other, in_link = [], 0, [], [], False
        id_rx = re.compile(r"\b(?:" + "|".join(map(re.escape, ids)) + r")\b") if ids else None
        encoded = any(_L_OUT.match(l) for l in body)
        for l in body:
            if m := _L_OUT.match(l):
                outside += int(m.group(1))
                in_link = False
            elif _L_LINK.match(l):
                in_link = True
            elif fm := _L_FINDING.match(l):
                if fm.group(1) in touched:
                    mine.append(l)
                elif encoded:
                    if in_link:
                        linked_lines.append(l)
                else:
                    outside += 1
                    if id_rx and id_rx.search(fm.group(3)):
                        linked_lines.append(l)
            else:
                other.append(l)
                in_link = False
        per_tool[name] = (mine, outside, linked_lines, other)
    return {"files the diff touches": touched, "per tool: findings in touched files, outside count, outside findings naming a changed def/class, summary lines": sorted(per_tool.items())}


case(
    498,
    claim="Replaces the git diff section by a one-line list of touched files and, per lint/typecheck section, keeps every finding in a touched file, then states how many findings lie outside the diff (with the first three) and lists every outside finding whose message names a def/class the diff added or removed (the cross-file breaks); summary lines are unchanged.",
    loses="the diff text itself, the line-by-line detail of old findings outside the diff beyond the first three, and linked findings are matched by name only (a break that does not mention the changed name is shown only if it is among the first three)",
    encode=filter_findings,
    probes=probes_498,
    fixtures=lambda: [
        Fixture("40-file repo, 3 edited files, 200 ruff findings and 17 mypy errors; the caller break is mypy's 17th line (real)", LINT_DIFF_BUNDLE, True),
        Fixture("only a diff, no findings sections", LINT_DIFF_BUNDLE.split("=== ruff")[0], False),
        Fixture("findings with no diff section", "=== ruff check ===\n" + "".join(f"src/m{i:02d}.py:1:8: F401 `os` imported but unused\n" for i in range(12)) + "Found 12 errors.\n", False),
        Fixture("few findings outside the diff: nothing to hide", "=== git diff -U0 ===\ndiff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-x=1\n+x = 1\n=== ruff ===\nsrc/a.py:1:1: E225 missing whitespace\nsrc/b.py:2:1: F401 `os` unused\nFound 2 errors.\n", False),
        Fixture("a diff header with no hunks and a stray separator", "=== git diff -U0 ===\ndiff --git a/x b/x\n=== ===\n=== ruff ===\n" + "".join(f"y{i}.py:1:1: E1 x\n" for i in range(8)), None),
    ],
    min_gain=0.5,
)



# ===============================================================================================================
# #508 strace: collapse the successful dynamic-loader preamble of each process to one line, join <unfinished ...> /
# <... resumed> pairs. RULE: every failed call (= -1 E...), every socket/connect, every non-library open, every write,
# fork/exec/wait, signal and exit must survive. Encoder: a line scanner with per-pid preamble state. Probe: joins
# unfinished/resumed with a dict keyed on (pid, syscall) and classifies the resulting events.

STRACE_LS_MISSING_DIR = (
    'execve("/usr/bin/ls", ["ls", "/nonexistent_dir", "/tmp"], 0x7fff9757b3c8 /* 174 vars */) = 0\n'
    'brk(NULL)                               = 0x555e4aae6000\n'
    'mmap(NULL, 8192, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANONYMOUS, -1, 0) = 0x7f9798811000\n'
    'access("/etc/ld.so.preload", R_OK)      = -1 ENOENT (No such file or directory)\n'
    'openat(AT_FDCWD, "/etc/ld.so.cache", O_RDONLY|O_CLOEXEC) = 3\n'
    'fstat(3, {st_mode=S_IFREG|0644, st_size=44439, ...}) = 0\n'
    'mmap(NULL, 44439, PROT_READ, MAP_PRIVATE, 3, 0) = 0x7f9798806000\n'
    'close(3)                                = 0\n'
    'openat(AT_FDCWD, "/lib/x86_64-linux-gnu/libselinux.so.1", O_RDONLY|O_CLOEXEC) = 3\n'
    'read(3, "\\177ELF\\2\\1\\1\\0\\0\\0\\0\\0\\0\\0\\0\\0\\3\\0>\\0\\1\\0\\0\\0\\0\\0\\0\\0\\0\\0\\0\\0"..., 832) = 832\n'
    'fstat(3, {st_mode=S_IFREG|0644, st_size=174472, ...}) = 0\n'
    'mmap(NULL, 181960, PROT_READ, MAP_PRIVATE|MAP_DENYWRITE, 3, 0) = 0x7f97987d9000\n'
    'mmap(0x7f97987df000, 118784, PROT_READ|PROT_EXEC, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0x6000) = 0x7f97987df000\n'
    'mmap(0x7f97987fc000, 24576, PROT_READ, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0x23000) = 0x7f97987fc000\n'
    'mmap(0x7f9798802000, 8192, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0x29000) = 0x7f9798802000\n'
    'mmap(0x7f9798804000, 5832, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_FIXED|MAP_ANONYMOUS, -1, 0) = 0x7f9798804000\n'
    'close(3)                                = 0\n'
    'openat(AT_FDCWD, "/lib/x86_64-linux-gnu/libc.so.6", O_RDONLY|O_CLOEXEC) = 3\n'
    'read(3, "\\177ELF\\2\\1\\1\\3\\0\\0\\0\\0\\0\\0\\0\\0\\3\\0>\\0\\1\\0\\0\\0\\220\\243\\2\\0\\0\\0\\0\\0"..., 832) = 832\n'
    'pread64(3, "\\6\\0\\0\\0\\4\\0\\0\\0@\\0\\0\\0\\0\\0\\0\\0@\\0\\0\\0\\0\\0\\0\\0@\\0\\0\\0\\0\\0\\0\\0"..., 784, 64) = 784\n'
    'fstat(3, {st_mode=S_IFREG|0755, st_size=2129424, ...}) = 0\n'
    'pread64(3, "\\6\\0\\0\\0\\4\\0\\0\\0@\\0\\0\\0\\0\\0\\0\\0@\\0\\0\\0\\0\\0\\0\\0@\\0\\0\\0\\0\\0\\0\\0"..., 784, 64) = 784\n'
    'mmap(NULL, 2174352, PROT_READ, MAP_PRIVATE|MAP_DENYWRITE, 3, 0) = 0x7f9798400000\n'
    'mmap(0x7f9798428000, 1609728, PROT_READ|PROT_EXEC, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0x28000) = 0x7f9798428000\n'
    'mmap(0x7f97985b1000, 323584, PROT_READ, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0x1b1000) = 0x7f97985b1000\n'
    'mmap(0x7f9798600000, 24576, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0x1ff000) = 0x7f9798600000\n'
    'mmap(0x7f9798606000, 52624, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_FIXED|MAP_ANONYMOUS, -1, 0) = 0x7f9798606000\n'
    'close(3)                                = 0\n'
    'openat(AT_FDCWD, "/lib/x86_64-linux-gnu/libpcre2-8.so.0", O_RDONLY|O_CLOEXEC) = 3\n'
    'read(3, "\\177ELF\\2\\1\\1\\0\\0\\0\\0\\0\\0\\0\\0\\0\\3\\0>\\0\\1\\0\\0\\0\\0\\0\\0\\0\\0\\0\\0\\0"..., 832) = 832\n'
    'fstat(3, {st_mode=S_IFREG|0644, st_size=625344, ...}) = 0\n'
    'mmap(NULL, 627472, PROT_READ, MAP_PRIVATE|MAP_DENYWRITE, 3, 0) = 0x7f979873f000\n'
    'mmap(0x7f9798741000, 450560, PROT_READ|PROT_EXEC, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0x2000) = 0x7f9798741000\n'
    'mmap(0x7f97987af000, 163840, PROT_READ, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0x70000) = 0x7f97987af000\n'
    'mmap(0x7f97987d7000, 8192, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0x97000) = 0x7f97987d7000\n'
    'close(3)                                = 0\n'
    'mmap(NULL, 12288, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANONYMOUS, -1, 0) = 0x7f979873c000\n'
    'arch_prctl(ARCH_SET_FS, 0x7f979873c800) = 0\n'
    'set_tid_address(0x7f979873cad0)         = 9268\n'
    'set_robust_list(0x7f979873cae0, 24)     = 0\n'
    'rseq(0x7f979873d120, 0x20, 0, 0x53053053) = 0\n'
    'mprotect(0x7f9798600000, 16384, PROT_READ) = 0\n'
    'mprotect(0x7f97987d7000, 4096, PROT_READ) = 0\n'
    'mprotect(0x7f9798802000, 4096, PROT_READ) = 0\n'
    'mprotect(0x555e1daa5000, 8192, PROT_READ) = 0\n'
    'mprotect(0x7f9798851000, 8192, PROT_READ) = 0\n'
    'prlimit64(0, RLIMIT_STACK, NULL, {rlim_cur=8192*1024, rlim_max=RLIM64_INFINITY}) = 0\n'
    'munmap(0x7f9798806000, 44439)           = 0\n'
    'statfs("/sys/fs/selinux", {f_type=SYSFS_MAGIC, f_bsize=4096, f_blocks=0, f_bfree=0, f_bavail=0, f_files=0, f_ffree=0, f_fsid={val=[0xa15c917a, 0x5d89b42]}, f_namelen=255, f_frsize=4096, f_flags=ST_VALID|ST_RELATIME}) = 0\n'
    'statfs("/selinux", 0x7ffee8544c00)      = -1 ENOENT (No such file or directory)\n'
    'getrandom("\\xa0\\xfb\\xa6\\x08\\x78\\xcc\\x75\\x22", 8, GRND_NONBLOCK) = 8\n'
    'brk(NULL)                               = 0x555e4aae6000\n'
    'brk(0x555e4ab07000)                     = 0x555e4ab07000\n'
    'openat(AT_FDCWD, "/proc/filesystems", O_RDONLY|O_CLOEXEC) = 3\n'
    'fstat(3, {st_mode=S_IFREG|0444, st_size=0, ...}) = 0\n'
    'read(3, "nodev\\tsysfs\\nnodev\\ttmpfs\\nnodev\\tpr"..., 1024) = 380\n'
    'close(3)                                = 0\n'
    'openat(AT_FDCWD, "/proc/mounts", O_RDONLY|O_CLOEXEC) = 3\n'
    'fstat(3, {st_mode=S_IFREG|0444, st_size=0, ...}) = 0\n'
    'read(3, "proc /proc proc rw,relatime 0 0\\n"..., 1024) = 1024\n'
    'read(3, "relatime,cpu 0 0\\ncgroup /sys/fs/"..., 1024) = 662\n'
    'read(3, "", 1024)                       = 0\n'
    'close(3)                                = 0\n'
    'access("/etc/selinux/config", F_OK)     = -1 ENOENT (No such file or directory)\n'
    'ioctl(1, TCGETS, 0x7ffee8544aa0)        = -1 ENOTTY (Inappropriate ioctl for device)\n'
    'statx(AT_FDCWD, "/nonexistent_dir", AT_STATX_SYNC_AS_STAT|AT_NO_AUTOMOUNT, STATX_MODE, 0x7ffee85446e0) = -1 ENOENT (No such file or directory)\n'
    'statx(AT_FDCWD, "/nonexistent_dir", AT_STATX_SYNC_AS_STAT|AT_SYMLINK_NOFOLLOW|AT_NO_AUTOMOUNT, STATX_MODE, 0x7ffee85446e0) = -1 ENOENT (No such file or directory)\n'
    'write(2, "ls: ", 4ls: )                     = 4\n'
    'write(2, "cannot access \'/nonexistent_dir\'", 32cannot access \'/nonexistent_dir\') = 32\n'
    'write(2, ": No such file or directory", 27: No such file or directory) = 27\n'
    'write(2, "\\n", 1\n'
    ')                       = 1\n'
    'statx(AT_FDCWD, "/tmp", AT_STATX_SYNC_AS_STAT|AT_NO_AUTOMOUNT, STATX_MODE, {stx_mask=STATX_TYPE|STATX_MODE|STATX_NLINK|STATX_UID|STATX_GID|STATX_ATIME|STATX_INO|STATX_SIZE|STATX_BLOCKS|STATX_MNT_ID, stx_attributes=0, stx_mode=S_IFDIR|S_ISVTX|0777, stx_size=4096, ...}) = 0\n'
    'openat(AT_FDCWD, "/tmp", O_RDONLY|O_NONBLOCK|O_CLOEXEC|O_DIRECTORY) = 3\n'
    'fstat(3, {st_mode=S_IFDIR|S_ISVTX|0777, st_size=4096, ...}) = 0\n'
    'fstat(1, {st_mode=S_IFCHR|0666, st_rdev=makedev(0x1, 0x3), ...}) = 0\n'
    'ioctl(1, TCGETS, 0x7ffee8542520)        = -1 ENOTTY (Inappropriate ioctl for device)\n'
    'getdents64(3, 0x555e4aaebc80 /* 113 entries */, 32768) = 5120\n'
    'getdents64(3, 0x555e4aaebc80 /* 0 entries */, 32768) = 0\n'
    'close(3)                                = 0\n'
    'write(1, "/tmp:\\ncc-socks\\nclaude-0\\nclaude-a"..., 2546) = 2546\n'
    'close(1)                                = 0\n'
    'close(2)                                = 0\n'
    'exit_group(2)                           = ?\n'
    '+++ exited with 2 +++\n'
)
STRACE_SH_PIPELINE = (
    'execve("/usr/bin/sh", ["sh", "-c", "ls /tmp | wc -l"], 0x7fff934995d8 /* 174 vars */) = 0\n'
    'brk(NULL)                               = 0x558b55e35000\n'
    'mmap(NULL, 8192, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANONYMOUS, -1, 0) = 0x7f290bccc000\n'
    'access("/etc/ld.so.preload", R_OK)      = -1 ENOENT (No such file or directory)\n'
    'openat(AT_FDCWD, "/etc/ld.so.cache", O_RDONLY|O_CLOEXEC) = 3\n'
    'fstat(3, {st_mode=S_IFREG|0644, st_size=44439, ...}) = 0\n'
    'mmap(NULL, 44439, PROT_READ, MAP_PRIVATE, 3, 0) = 0x7f290bcc1000\n'
    'close(3)                                = 0\n'
    'openat(AT_FDCWD, "/lib/x86_64-linux-gnu/libc.so.6", O_RDONLY|O_CLOEXEC) = 3\n'
    'read(3, "\\177ELF\\2\\1\\1\\3\\0\\0\\0\\0\\0\\0\\0\\0\\3\\0>\\0\\1\\0\\0\\0\\220\\243\\2\\0\\0\\0\\0\\0"..., 832) = 832\n'
    'pread64(3, "\\6\\0\\0\\0\\4\\0\\0\\0@\\0\\0\\0\\0\\0\\0\\0@\\0\\0\\0\\0\\0\\0\\0@\\0\\0\\0\\0\\0\\0\\0"..., 784, 64) = 784\n'
    'fstat(3, {st_mode=S_IFREG|0755, st_size=2129424, ...}) = 0\n'
    'pread64(3, "\\6\\0\\0\\0\\4\\0\\0\\0@\\0\\0\\0\\0\\0\\0\\0@\\0\\0\\0\\0\\0\\0\\0@\\0\\0\\0\\0\\0\\0\\0"..., 784, 64) = 784\n'
    'mmap(NULL, 2174352, PROT_READ, MAP_PRIVATE|MAP_DENYWRITE, 3, 0) = 0x7f290ba00000\n'
    'mmap(0x7f290ba28000, 1609728, PROT_READ|PROT_EXEC, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0x28000) = 0x7f290ba28000\n'
    'mmap(0x7f290bbb1000, 323584, PROT_READ, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0x1b1000) = 0x7f290bbb1000\n'
    'mmap(0x7f290bc00000, 24576, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0x1ff000) = 0x7f290bc00000\n'
    'mmap(0x7f290bc06000, 52624, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_FIXED|MAP_ANONYMOUS, -1, 0) = 0x7f290bc06000\n'
    'close(3)                                = 0\n'
    'mmap(NULL, 12288, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANONYMOUS, -1, 0) = 0x7f290bcbe000\n'
    'arch_prctl(ARCH_SET_FS, 0x7f290bcbe740) = 0\n'
    'set_tid_address(0x7f290bcbea10)         = 9273\n'
    'set_robust_list(0x7f290bcbea20, 24)     = 0\n'
    'rseq(0x7f290bcbf060, 0x20, 0, 0x53053053) = 0\n'
    'mprotect(0x7f290bc00000, 16384, PROT_READ) = 0\n'
    'mprotect(0x558b5310e000, 8192, PROT_READ) = 0\n'
    'mprotect(0x7f290bd0c000, 8192, PROT_READ) = 0\n'
    'prlimit64(0, RLIMIT_STACK, NULL, {rlim_cur=8192*1024, rlim_max=RLIM64_INFINITY}) = 0\n'
    'munmap(0x7f290bcc1000, 44439)           = 0\n'
    'getuid()                                = 0\n'
    'getgid()                                = 0\n'
    'getpid()                                = 9273\n'
    'rt_sigaction(SIGCHLD, {sa_handler=0x558b53103cd0, sa_mask=~[RTMIN RT_1], sa_flags=SA_RESTORER, sa_restorer=0x7f290ba45330}, NULL, 8) = 0\n'
    'geteuid()                               = 0\n'
    'getrandom("\\xca\\x06\\xe7\\xdd\\x4d\\xb4\\xd0\\x24", 8, GRND_NONBLOCK) = 8\n'
    'brk(NULL)                               = 0x558b55e35000\n'
    'brk(0x558b55e56000)                     = 0x558b55e56000\n'
    'getppid()                               = 9270\n'
    'newfstatat(AT_FDCWD, "/work/app", {st_mode=S_IFDIR|0755, st_size=4096, ...}, 0) = 0\n'
    'newfstatat(AT_FDCWD, ".", {st_mode=S_IFDIR|0755, st_size=4096, ...}, 0) = 0\n'
    'geteuid()                               = 0\n'
    'getegid()                               = 0\n'
    'rt_sigaction(SIGINT, NULL, {sa_handler=SIG_DFL, sa_mask=[], sa_flags=0}, 8) = 0\n'
    'rt_sigaction(SIGINT, {sa_handler=0x558b53103cd0, sa_mask=~[RTMIN RT_1], sa_flags=SA_RESTORER, sa_restorer=0x7f290ba45330}, NULL, 8) = 0\n'
    'rt_sigaction(SIGQUIT, NULL, {sa_handler=SIG_DFL, sa_mask=[], sa_flags=0}, 8) = 0\n'
    'rt_sigaction(SIGQUIT, {sa_handler=SIG_DFL, sa_mask=~[RTMIN RT_1], sa_flags=SA_RESTORER, sa_restorer=0x7f290ba45330}, NULL, 8) = 0\n'
    'rt_sigaction(SIGTERM, NULL, {sa_handler=SIG_DFL, sa_mask=[], sa_flags=0}, 8) = 0\n'
    'rt_sigaction(SIGTERM, {sa_handler=SIG_DFL, sa_mask=~[RTMIN RT_1], sa_flags=SA_RESTORER, sa_restorer=0x7f290ba45330}, NULL, 8) = 0\n'
    'newfstatat(AT_FDCWD, "/nix/var/nix/profiles/default/bin/ls", 0x7ffec880f430, 0) = -1 ENOENT (No such file or directory)\n'
    'newfstatat(AT_FDCWD, "/root/.local/bin/ls", 0x7ffec880f430, 0) = -1 ENOENT (No such file or directory)\n'
    'newfstatat(AT_FDCWD, "/root/.cargo/bin/ls", 0x7ffec880f430, 0) = -1 ENOENT (No such file or directory)\n'
    'newfstatat(AT_FDCWD, "/usr/local/go/bin/ls", 0x7ffec880f430, 0) = -1 ENOENT (No such file or directory)\n'
    'newfstatat(AT_FDCWD, "/opt/node22/bin/ls", 0x7ffec880f430, 0) = -1 ENOENT (No such file or directory)\n'
    'newfstatat(AT_FDCWD, "/opt/maven/bin/ls", 0x7ffec880f430, 0) = -1 ENOENT (No such file or directory)\n'
    'newfstatat(AT_FDCWD, "/opt/gradle/bin/ls", 0x7ffec880f430, 0) = -1 ENOENT (No such file or directory)\n'
    'newfstatat(AT_FDCWD, "/opt/rbenv/bin/ls", 0x7ffec880f430, 0) = -1 ENOENT (No such file or directory)\n'
    'newfstatat(AT_FDCWD, "/root/.bun/bin/ls", 0x7ffec880f430, 0) = -1 ENOENT (No such file or directory)\n'
    'newfstatat(AT_FDCWD, "/usr/local/sbin/ls", 0x7ffec880f430, 0) = -1 ENOENT (No such file or directory)\n'
    'newfstatat(AT_FDCWD, "/usr/local/bin/ls", 0x7ffec880f430, 0) = -1 ENOENT (No such file or directory)\n'
    'newfstatat(AT_FDCWD, "/usr/sbin/ls", 0x7ffec880f430, 0) = -1 ENOENT (No such file or directory)\n'
    'newfstatat(AT_FDCWD, "/usr/bin/ls", {st_mode=S_IFREG|0755, st_size=142312, ...}, 0) = 0\n'
    'pipe2([3, 4], 0)                        = 0\n'
    'clone(child_stack=NULL, flags=CLONE_CHILD_CLEARTID|CLONE_CHILD_SETTID|SIGCHLD, child_tidptr=0x7f290bcbea10) = 9274\n'
    'strace: Process 9274 attached\n'
    '[pid  9273] close(4)                    = 0\n'
    '[pid  9274] set_robust_list(0x7f290bcbea20, 24 <unfinished ...>\n'
    '[pid  9273] newfstatat(AT_FDCWD, "/nix/var/nix/profiles/default/bin/wc",  <unfinished ...>\n'
    '[pid  9274] <... set_robust_list resumed>) = 0\n'
    '[pid  9273] <... newfstatat resumed>0x7ffec880f430, 0) = -1 ENOENT (No such file or directory)\n'
    '[pid  9273] newfstatat(AT_FDCWD, "/root/.local/bin/wc",  <unfinished ...>\n'
    '[pid  9274] close(3 <unfinished ...>\n'
    '[pid  9273] <... newfstatat resumed>0x7ffec880f430, 0) = -1 ENOENT (No such file or directory)\n'
    '[pid  9274] <... close resumed>)        = 0\n'
    '[pid  9273] newfstatat(AT_FDCWD, "/root/.cargo/bin/wc",  <unfinished ...>\n'
    '[pid  9274] dup2(4, 1 <unfinished ...>\n'
    '[pid  9273] <... newfstatat resumed>0x7ffec880f430, 0) = -1 ENOENT (No such file or directory)\n'
    '[pid  9274] <... dup2 resumed>)         = 1\n'
    '[pid  9273] newfstatat(AT_FDCWD, "/usr/local/go/bin/wc",  <unfinished ...>\n'
    '[pid  9274] close(4 <unfinished ...>\n'
    '[pid  9273] <... newfstatat resumed>0x7ffec880f430, 0) = -1 ENOENT (No such file or directory)\n'
    '[pid  9274] <... close resumed>)        = 0\n'
    '[pid  9273] newfstatat(AT_FDCWD, "/opt/node22/bin/wc", 0x7ffec880f430, 0) = -1 ENOENT (No such file or directory)\n'
    '[pid  9274] execve("/usr/bin/ls", ["ls", "/tmp"], 0x558b55e37608 /* 174 vars */ <unfinished ...>\n'
    '[pid  9273] newfstatat(AT_FDCWD, "/opt/maven/bin/wc", 0x7ffec880f430, 0) = -1 ENOENT (No such file or directory)\n'
    '[pid  9273] newfstatat(AT_FDCWD, "/opt/gradle/bin/wc", 0x7ffec880f430, 0) = -1 ENOENT (No such file or directory)\n'
    '[pid  9273] newfstatat(AT_FDCWD, "/opt/rbenv/bin/wc", 0x7ffec880f430, 0) = -1 ENOENT (No such file or directory)\n'
    '[pid  9273] newfstatat(AT_FDCWD, "/root/.bun/bin/wc", 0x7ffec880f430, 0) = -1 ENOENT (No such file or directory)\n'
    '[pid  9274] <... execve resumed>)       = 0\n'
    '[pid  9273] newfstatat(AT_FDCWD, "/usr/local/sbin/wc", 0x7ffec880f430, 0) = -1 ENOENT (No such file or directory)\n'
    '[pid  9274] brk(NULL <unfinished ...>\n'
    '[pid  9273] newfstatat(AT_FDCWD, "/usr/local/bin/wc",  <unfinished ...>\n'
    '[pid  9274] <... brk resumed>)          = 0x55dfe65e2000\n'
    '[pid  9273] <... newfstatat resumed>0x7ffec880f430, 0) = -1 ENOENT (No such file or directory)\n'
    '[pid  9273] newfstatat(AT_FDCWD, "/usr/sbin/wc", 0x7ffec880f430, 0) = -1 ENOENT (No such file or directory)\n'
    '[pid  9273] newfstatat(AT_FDCWD, "/usr/bin/wc", {st_mode=S_IFREG|0755, st_size=55824, ...}, 0) = 0\n'
    '[pid  9273] clone(child_stack=NULL, flags=CLONE_CHILD_CLEARTID|CLONE_CHILD_SETTID|SIGCHLDstrace: Process 9275 attached\n'
    ', child_tidptr=0x7f290bcbea10) = 9275\n'
    '[pid  9275] set_robust_list(0x7f290bcbea20, 24 <unfinished ...>\n'
    '[pid  9273] close(3 <unfinished ...>\n'
    '[pid  9275] <... set_robust_list resumed>) = 0\n'
    '[pid  9273] <... close resumed>)        = 0\n'
    '[pid  9273] close(-1 <unfinished ...>\n'
    '[pid  9275] dup2(3, 0 <unfinished ...>\n'
    '[pid  9273] <... close resumed>)        = -1 EBADF (Bad file descriptor)\n'
    '[pid  9275] <... dup2 resumed>)         = 0\n'
    '[pid  9273] wait4(-1,  <unfinished ...>\n'
    '[pid  9275] close(3)                    = 0\n'
    '[pid  9274] mmap(NULL, 8192, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANONYMOUS, -1, 0 <unfinished ...>\n'
    '[pid  9275] execve("/usr/bin/wc", ["wc", "-l"], 0x558b55e37638 /* 174 vars */ <unfinished ...>\n'
    '[pid  9274] <... mmap resumed>)         = 0x7f4eac79d000\n'
    '[pid  9274] access("/etc/ld.so.preload", R_OK) = -1 ENOENT (No such file or directory)\n'
    '[pid  9274] openat(AT_FDCWD, "/etc/ld.so.cache", O_RDONLY|O_CLOEXEC) = 3\n'
    '[pid  9274] fstat(3,  <unfinished ...>\n'
    '[pid  9275] <... execve resumed>)       = 0\n'
    '[pid  9274] <... fstat resumed>{st_mode=S_IFREG|0644, st_size=44439, ...}) = 0\n'
    '[pid  9274] mmap(NULL, 44439, PROT_READ, MAP_PRIVATE, 3, 0 <unfinished ...>\n'
    '[pid  9275] brk(NULL <unfinished ...>\n'
    '[pid  9274] <... mmap resumed>)         = 0x7f4eac792000\n'
    '[pid  9275] <... brk resumed>)          = 0x55be8d5d0000\n'
    '[pid  9274] close(3)                    = 0\n'
    '[pid  9274] openat(AT_FDCWD, "/lib/x86_64-linux-gnu/libselinux.so.1", O_RDONLY|O_CLOEXEC) = 3\n'
    '[pid  9274] read(3, "\\177ELF\\2\\1\\1\\0\\0\\0\\0\\0\\0\\0\\0\\0\\3\\0>\\0\\1\\0\\0\\0\\0\\0\\0\\0\\0\\0\\0\\0"..., 832) = 832\n'
    '[pid  9274] fstat(3, {st_mode=S_IFREG|0644, st_size=174472, ...}) = 0\n'
    '[pid  9274] mmap(NULL, 181960, PROT_READ, MAP_PRIVATE|MAP_DENYWRITE, 3, 0) = 0x7f4eac765000\n'
    '[pid  9274] mmap(0x7f4eac76b000, 118784, PROT_READ|PROT_EXEC, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0x6000) = 0x7f4eac76b000\n'
    '[pid  9274] mmap(0x7f4eac788000, 24576, PROT_READ, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0x23000) = 0x7f4eac788000\n'
    '[pid  9274] mmap(0x7f4eac78e000, 8192, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0x29000) = 0x7f4eac78e000\n'
    '[pid  9274] mmap(0x7f4eac790000, 5832, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_FIXED|MAP_ANONYMOUS, -1, 0 <unfinished ...>\n'
    '[pid  9275] mmap(NULL, 8192, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANONYMOUS, -1, 0 <unfinished ...>\n'
    '[pid  9274] <... mmap resumed>)         = 0x7f4eac790000\n'
    '[pid  9275] <... mmap resumed>)         = 0x7f35e2387000\n'
    '[pid  9275] access("/etc/ld.so.preload", R_OK <unfinished ...>\n'
    '[pid  9274] close(3 <unfinished ...>\n'
    '[pid  9275] <... access resumed>)       = -1 ENOENT (No such file or directory)\n'
    '[pid  9274] <... close resumed>)        = 0\n'
    '[pid  9275] openat(AT_FDCWD, "/etc/ld.so.cache", O_RDONLY|O_CLOEXEC <unfinished ...>\n'
    '[pid  9274] openat(AT_FDCWD, "/lib/x86_64-linux-gnu/libc.so.6", O_RDONLY|O_CLOEXEC <unfinished ...>\n'
    '[pid  9275] <... openat resumed>)       = 3\n'
    '[pid  9274] <... openat resumed>)       = 3\n'
    '[pid  9275] fstat(3,  <unfinished ...>\n'
    '[pid  9274] read(3,  <unfinished ...>\n'
    '[pid  9275] <... fstat resumed>{st_mode=S_IFREG|0644, st_size=44439, ...}) = 0\n'
    '[pid  9274] <... read resumed>"\\177ELF\\2\\1\\1\\3\\0\\0\\0\\0\\0\\0\\0\\0\\3\\0>\\0\\1\\0\\0\\0\\220\\243\\2\\0\\0\\0\\0\\0"..., 832) = 832\n'
    '[pid  9275] mmap(NULL, 44439, PROT_READ, MAP_PRIVATE, 3, 0 <unfinished ...>\n'
    '[pid  9274] pread64(3,  <unfinished ...>\n'
    '[pid  9275] <... mmap resumed>)         = 0x7f35e237c000\n'
    '[pid  9274] <... pread64 resumed>"\\6\\0\\0\\0\\4\\0\\0\\0@\\0\\0\\0\\0\\0\\0\\0@\\0\\0\\0\\0\\0\\0\\0@\\0\\0\\0\\0\\0\\0\\0"..., 784, 64) = 784\n'
    '[pid  9275] close(3 <unfinished ...>\n'
    '[pid  9274] fstat(3,  <unfinished ...>\n'
    '[pid  9275] <... close resumed>)        = 0\n'
    '[pid  9274] <... fstat resumed>{st_mode=S_IFREG|0755, st_size=2129424, ...}) = 0\n'
    '[pid  9275] openat(AT_FDCWD, "/lib/x86_64-linux-gnu/libc.so.6", O_RDONLY|O_CLOEXEC <unfinished ...>\n'
    '[pid  9274] pread64(3,  <unfinished ...>\n'
    '[pid  9275] <... openat resumed>)       = 3\n'
    '[pid  9274] <... pread64 resumed>"\\6\\0\\0\\0\\4\\0\\0\\0@\\0\\0\\0\\0\\0\\0\\0@\\0\\0\\0\\0\\0\\0\\0@\\0\\0\\0\\0\\0\\0\\0"..., 784, 64) = 784\n'
    '[pid  9275] read(3,  <unfinished ...>\n'
    '[pid  9274] mmap(NULL, 2174352, PROT_READ, MAP_PRIVATE|MAP_DENYWRITE, 3, 0 <unfinished ...>\n'
    '[pid  9275] <... read resumed>"\\177ELF\\2\\1\\1\\3\\0\\0\\0\\0\\0\\0\\0\\0\\3\\0>\\0\\1\\0\\0\\0\\220\\243\\2\\0\\0\\0\\0\\0"..., 832) = 832\n'
    '[pid  9274] <... mmap resumed>)         = 0x7f4eac400000\n'
    '[pid  9275] pread64(3,  <unfinished ...>\n'
    '[pid  9274] mmap(0x7f4eac428000, 1609728, PROT_READ|PROT_EXEC, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0x28000 <unfinished ...>\n'
    '[pid  9275] <... pread64 resumed>"\\6\\0\\0\\0\\4\\0\\0\\0@\\0\\0\\0\\0\\0\\0\\0@\\0\\0\\0\\0\\0\\0\\0@\\0\\0\\0\\0\\0\\0\\0"..., 784, 64) = 784\n'
    '[pid  9274] <... mmap resumed>)         = 0x7f4eac428000\n'
    '[pid  9275] fstat(3,  <unfinished ...>\n'
    '[pid  9274] mmap(0x7f4eac5b1000, 323584, PROT_READ, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0x1b1000 <unfinished ...>\n'
    '[pid  9275] <... fstat resumed>{st_mode=S_IFREG|0755, st_size=2129424, ...}) = 0\n'
    '[pid  9274] <... mmap resumed>)         = 0x7f4eac5b1000\n'
    '[pid  9275] pread64(3,  <unfinished ...>\n'
    '[pid  9274] mmap(0x7f4eac600000, 24576, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0x1ff000 <unfinished ...>\n'
    '[pid  9275] <... pread64 resumed>"\\6\\0\\0\\0\\4\\0\\0\\0@\\0\\0\\0\\0\\0\\0\\0@\\0\\0\\0\\0\\0\\0\\0@\\0\\0\\0\\0\\0\\0\\0"..., 784, 64) = 784\n'
    '[pid  9274] <... mmap resumed>)         = 0x7f4eac600000\n'
    '[pid  9275] mmap(NULL, 2174352, PROT_READ, MAP_PRIVATE|MAP_DENYWRITE, 3, 0 <unfinished ...>\n'
    '[pid  9274] mmap(0x7f4eac606000, 52624, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_FIXED|MAP_ANONYMOUS, -1, 0 <unfinished ...>\n'
    '[pid  9275] <... mmap resumed>)         = 0x7f35e2000000\n'
    '[pid  9274] <... mmap resumed>)         = 0x7f4eac606000\n'
    '[pid  9275] mmap(0x7f35e2028000, 1609728, PROT_READ|PROT_EXEC, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0x28000) = 0x7f35e2028000\n'
    '[pid  9274] close(3 <unfinished ...>\n'
    '[pid  9275] mmap(0x7f35e21b1000, 323584, PROT_READ, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0x1b1000 <unfinished ...>\n'
    '[pid  9274] <... close resumed>)        = 0\n'
    '[pid  9275] <... mmap resumed>)         = 0x7f35e21b1000\n'
    '[pid  9274] openat(AT_FDCWD, "/lib/x86_64-linux-gnu/libpcre2-8.so.0", O_RDONLY|O_CLOEXEC <unfinished ...>\n'
    '[pid  9275] mmap(0x7f35e2200000, 24576, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0x1ff000 <unfinished ...>\n'
    '[pid  9274] <... openat resumed>)       = 3\n'
    '[pid  9275] <... mmap resumed>)         = 0x7f35e2200000\n'
    '[pid  9274] read(3,  <unfinished ...>\n'
    '[pid  9275] mmap(0x7f35e2206000, 52624, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_FIXED|MAP_ANONYMOUS, -1, 0 <unfinished ...>\n'
    '[pid  9274] <... read resumed>"\\177ELF\\2\\1\\1\\0\\0\\0\\0\\0\\0\\0\\0\\0\\3\\0>\\0\\1\\0\\0\\0\\0\\0\\0\\0\\0\\0\\0\\0"..., 832) = 832\n'
    '[pid  9275] <... mmap resumed>)         = 0x7f35e2206000\n'
    '[pid  9274] fstat(3,  <unfinished ...>\n'
    '[pid  9275] close(3 <unfinished ...>\n'
    '[pid  9274] <... fstat resumed>{st_mode=S_IFREG|0644, st_size=625344, ...}) = 0\n'
    '[pid  9275] <... close resumed>)        = 0\n'
    '[pid  9275] mmap(NULL, 12288, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANONYMOUS, -1, 0) = 0x7f35e2379000\n'
    '[pid  9274] mmap(NULL, 627472, PROT_READ, MAP_PRIVATE|MAP_DENYWRITE, 3, 0 <unfinished ...>\n'
    '[pid  9275] arch_prctl(ARCH_SET_FS, 0x7f35e2379740 <unfinished ...>\n'
    '[pid  9274] <... mmap resumed>)         = 0x7f4eac6cb000\n'
    '[pid  9275] <... arch_prctl resumed>)   = 0\n'
    '[pid  9274] mmap(0x7f4eac6cd000, 450560, PROT_READ|PROT_EXEC, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0x2000 <unfinished ...>\n'
    '[pid  9275] set_tid_address(0x7f35e2379a10) = 9275\n'
    '[pid  9274] <... mmap resumed>)         = 0x7f4eac6cd000\n'
    '[pid  9275] set_robust_list(0x7f35e2379a20, 24 <unfinished ...>\n'
    '[pid  9274] mmap(0x7f4eac73b000, 163840, PROT_READ, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0x70000 <unfinished ...>\n'
    '[pid  9275] <... set_robust_list resumed>) = 0\n'
    '[pid  9274] <... mmap resumed>)         = 0x7f4eac73b000\n'
    '[pid  9275] rseq(0x7f35e237a060, 0x20, 0, 0x53053053 <unfinished ...>\n'
    '[pid  9274] mmap(0x7f4eac763000, 8192, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0x97000 <unfinished ...>\n'
    '[pid  9275] <... rseq resumed>)         = 0\n'
    '[pid  9274] <... mmap resumed>)         = 0x7f4eac763000\n'
    '[pid  9274] close(3 <unfinished ...>\n'
    '[pid  9275] mprotect(0x7f35e2200000, 16384, PROT_READ <unfinished ...>\n'
    '[pid  9274] <... close resumed>)        = 0\n'
    '[pid  9275] <... mprotect resumed>)     = 0\n'
    '[pid  9274] mmap(NULL, 12288, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANONYMOUS, -1, 0 <unfinished ...>\n'
    '[pid  9275] mprotect(0x55be6266f000, 4096, PROT_READ <unfinished ...>\n'
    '[pid  9274] <... mmap resumed>)         = 0x7f4eac6c8000\n'
    '[pid  9275] <... mprotect resumed>)     = 0\n'
    '[pid  9274] arch_prctl(ARCH_SET_FS, 0x7f4eac6c8800 <unfinished ...>\n'
    '[pid  9275] mprotect(0x7f35e23c7000, 8192, PROT_READ <unfinished ...>\n'
    '[pid  9274] <... arch_prctl resumed>)   = 0\n'
    '[pid  9275] <... mprotect resumed>)     = 0\n'
    '[pid  9274] set_tid_address(0x7f4eac6c8ad0 <unfinished ...>\n'
    '[pid  9275] prlimit64(0, RLIMIT_STACK, NULL,  <unfinished ...>\n'
    '[pid  9274] <... set_tid_address resumed>) = 9274\n'
    '[pid  9275] <... prlimit64 resumed>{rlim_cur=8192*1024, rlim_max=RLIM64_INFINITY}) = 0\n'
    '[pid  9274] set_robust_list(0x7f4eac6c8ae0, 24 <unfinished ...>\n'
    '[pid  9275] munmap(0x7f35e237c000, 44439 <unfinished ...>\n'
    '[pid  9274] <... set_robust_list resumed>) = 0\n'
    '[pid  9275] <... munmap resumed>)       = 0\n'
    '[pid  9274] rseq(0x7f4eac6c9120, 0x20, 0, 0x53053053) = 0\n'
    '[pid  9275] getrandom( <unfinished ...>\n'
    '[pid  9274] mprotect(0x7f4eac600000, 16384, PROT_READ <unfinished ...>\n'
    '[pid  9275] <... getrandom resumed>"\\x01\\xd0\\x09\\x74\\x93\\x74\\x86\\x8c", 8, GRND_NONBLOCK) = 8\n'
    '[pid  9274] <... mprotect resumed>)     = 0\n'
    '[pid  9275] brk(NULL <unfinished ...>\n'
    '[pid  9274] mprotect(0x7f4eac763000, 4096, PROT_READ <unfinished ...>\n'
    '[pid  9275] <... brk resumed>)          = 0x55be8d5d0000\n'
    '[pid  9274] <... mprotect resumed>)     = 0\n'
    '[pid  9275] brk(0x55be8d5f1000)         = 0x55be8d5f1000\n'
    '[pid  9274] mprotect(0x7f4eac78e000, 4096, PROT_READ <unfinished ...>\n'
    '[pid  9275] fadvise64(0, 0, 0, POSIX_FADV_SEQUENTIAL <unfinished ...>\n'
    '[pid  9274] <... mprotect resumed>)     = 0\n'
    '[pid  9275] <... fadvise64 resumed>)    = -1 ESPIPE (Illegal seek)\n'
    '[pid  9275] read(0,  <unfinished ...>\n'
    '[pid  9274] mprotect(0x55dfcd16c000, 8192, PROT_READ) = 0\n'
    '[pid  9274] mprotect(0x7f4eac7dd000, 8192, PROT_READ) = 0\n'
    '[pid  9274] prlimit64(0, RLIMIT_STACK, NULL, {rlim_cur=8192*1024, rlim_max=RLIM64_INFINITY}) = 0\n'
    '[pid  9274] munmap(0x7f4eac792000, 44439) = 0\n'
    '[pid  9274] statfs("/sys/fs/selinux", {f_type=SYSFS_MAGIC, f_bsize=4096, f_blocks=0, f_bfree=0, f_bavail=0, f_files=0, f_ffree=0, f_fsid={val=[0xa15c917a, 0x5d89b42]}, f_namelen=255, f_frsize=4096, f_flags=ST_VALID|ST_RELATIME}) = 0\n'
    '[pid  9274] statfs("/selinux", 0x7ffeb30ef600) = -1 ENOENT (No such file or directory)\n'
    '[pid  9274] getrandom("\\xbe\\xf7\\x10\\xb7\\x90\\xab\\x01\\x70", 8, GRND_NONBLOCK) = 8\n'
    '[pid  9274] brk(NULL)                   = 0x55dfe65e2000\n'
    '[pid  9274] brk(0x55dfe6603000)         = 0x55dfe6603000\n'
    '[pid  9274] openat(AT_FDCWD, "/proc/filesystems", O_RDONLY|O_CLOEXEC) = 3\n'
    '[pid  9274] fstat(3, {st_mode=S_IFREG|0444, st_size=0, ...}) = 0\n'
    '[pid  9274] read(3, "nodev\\tsysfs\\nnodev\\ttmpfs\\nnodev\\tpr"..., 1024) = 380\n'
    '[pid  9274] close(3)                    = 0\n'
    '[pid  9274] openat(AT_FDCWD, "/proc/mounts", O_RDONLY|O_CLOEXEC) = 3\n'
    '[pid  9274] fstat(3, {st_mode=S_IFREG|0444, st_size=0, ...}) = 0\n'
    '[pid  9274] read(3, "proc /proc proc rw,relatime 0 0\\n"..., 1024) = 1024\n'
    '[pid  9274] read(3, "relatime,cpu 0 0\\ncgroup /sys/fs/"..., 1024) = 662\n'
    '[pid  9274] read(3, "", 1024)           = 0\n'
    '[pid  9274] close(3)                    = 0\n'
    '[pid  9274] access("/etc/selinux/config", F_OK) = -1 ENOENT (No such file or directory)\n'
    '[pid  9274] ioctl(1, TCGETS, 0x7ffeb30ef4a0) = -1 ENOTTY (Inappropriate ioctl for device)\n'
    '[pid  9274] statx(AT_FDCWD, "/tmp", AT_STATX_SYNC_AS_STAT|AT_NO_AUTOMOUNT, STATX_MODE, {stx_mask=STATX_TYPE|STATX_MODE|STATX_NLINK|STATX_UID|STATX_GID|STATX_ATIME|STATX_INO|STATX_SIZE|STATX_BLOCKS|STATX_MNT_ID, stx_attributes=0, stx_mode=S_IFDIR|S_ISVTX|0777, stx_size=4096, ...}) = 0\n'
    '[pid  9274] openat(AT_FDCWD, "/tmp", O_RDONLY|O_NONBLOCK|O_CLOEXEC|O_DIRECTORY) = 3\n'
    '[pid  9274] fstat(3, {st_mode=S_IFDIR|S_ISVTX|0777, st_size=4096, ...}) = 0\n'
    '[pid  9274] getdents64(3, 0x55dfe65e7c80 /* 113 entries */, 32768) = 5120\n'
    '[pid  9274] getdents64(3, 0x55dfe65e7c80 /* 0 entries */, 32768) = 0\n'
    '[pid  9274] close(3)                    = 0\n'
    '[pid  9274] fstat(1, {st_mode=S_IFIFO|0600, st_size=0, ...}) = 0\n'
    '[pid  9274] write(1, "cc-socks\\nclaude-0\\nclaude-append-"..., 2540) = 2540\n'
    '[pid  9275] <... read resumed>"cc-socks\\nclaude-0\\nclaude-append-"..., 16320) = 2540\n'
    '[pid  9274] close(1 <unfinished ...>\n'
    '[pid  9275] read(0,  <unfinished ...>\n'
    '[pid  9274] <... close resumed>)        = 0\n'
    '[pid  9275] <... read resumed>"", 16320) = 0\n'
    '[pid  9274] close(2 <unfinished ...>\n'
    '[pid  9275] fstat(1,  <unfinished ...>\n'
    '[pid  9274] <... close resumed>)        = 0\n'
    '[pid  9275] <... fstat resumed>{st_mode=S_IFCHR|0666, st_rdev=makedev(0x1, 0x3), ...}) = 0\n'
    '[pid  9275] ioctl(1, TCGETS <unfinished ...>\n'
    '[pid  9274] exit_group(0 <unfinished ...>\n'
    '[pid  9275] <... ioctl resumed>, 0x7ffc6cb17ab0) = -1 ENOTTY (Inappropriate ioctl for device)\n'
    '[pid  9274] <... exit_group resumed>)   = ?\n'
    '[pid  9275] write(1, "110\\n", 4)        = 4\n'
    '[pid  9275] close(0)                    = 0\n'
    '[pid  9275] close(1)                    = 0\n'
    '[pid  9274] +++ exited with 0 +++\n'
    '[pid  9273] <... wait4 resumed>[{WIFEXITED(s) && WEXITSTATUS(s) == 0}], 0, NULL) = 9274\n'
    '[pid  9273] --- SIGCHLD {si_signo=SIGCHLD, si_code=CLD_EXITED, si_pid=9274, si_uid=0, si_status=0, si_utime=0, si_stime=0} ---\n'
    '[pid  9275] close(2 <unfinished ...>\n'
    '[pid  9273] rt_sigreturn({mask=[]} <unfinished ...>\n'
    '[pid  9275] <... close resumed>)        = 0\n'
    '[pid  9273] <... rt_sigreturn resumed>) = 9274\n'
    '[pid  9273] wait4(-1,  <unfinished ...>\n'
    '[pid  9275] exit_group(0)               = ?\n'
    '[pid  9275] +++ exited with 0 +++\n'
    '<... wait4 resumed>[{WIFEXITED(s) && WEXITSTATUS(s) == 0}], 0, NULL) = 9275\n'
    '--- SIGCHLD {si_signo=SIGCHLD, si_code=CLD_EXITED, si_pid=9275, si_uid=0, si_status=0, si_utime=0, si_stime=0} ---\n'
    'rt_sigreturn({mask=[]})                 = 9275\n'
    'wait4(-1, 0x7ffec880f38c, WNOHANG, NULL) = -1 ECHILD (No child processes)\n'
    'exit_group(0)                           = ?\n'
    '+++ exited with 0 +++\n'
)
STRACE_CURL_REFUSED = (
    'execve("/usr/bin/curl", ["curl", "-s", "http://127.0.0.1:9/"], 0x7ffd520accc8 /* 174 vars */) = 0\n'
    'brk(NULL)                               = 0x55c1c066b000\n'
    'mmap(NULL, 8192, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANONYMOUS, -1, 0) = 0x7f6cd8b7b000\n'
    'access("/etc/ld.so.preload", R_OK)      = -1 ENOENT (No such file or directory)\n'
    'openat(AT_FDCWD, "/etc/ld.so.cache", O_RDONLY|O_CLOEXEC) = 3\n'
    'fstat(3, {st_mode=S_IFREG|0644, st_size=44439, ...}) = 0\n'
    'mmap(NULL, 44439, PROT_READ, MAP_PRIVATE, 3, 0) = 0x7f6cd8b70000\n'
    'close(3)                                = 0\n'
    'openat(AT_FDCWD, "/lib/x86_64-linux-gnu/libcurl.so.4", O_RDONLY|O_CLOEXEC) = 3\n'
    'read(3, "\\177ELF\\2\\1\\1\\0\\0\\0\\0\\0\\0\\0\\0\\0\\3\\0>\\0\\1\\0\\0\\0\\0\\0\\0\\0\\0\\0\\0\\0"..., 832) = 832\n'
    'fstat(3, {st_mode=S_IFREG|0644, st_size=786336, ...}) = 0\n'
    'mmap(NULL, 789456, PROT_READ, MAP_PRIVATE|MAP_DENYWRITE, 3, 0) = 0x7f6cd8aaf000\n'
    'mmap(0x7f6cd8ac0000, 573440, PROT_READ|PROT_EXEC, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0x11000) = 0x7f6cd8ac0000\n'
    'mmap(0x7f6cd8b4c000, 118784, PROT_READ, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0x9d000) = 0x7f6cd8b4c000\n'
    'mmap(0x7f6cd8b69000, 28672, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0xb9000) = 0x7f6cd8b69000\n'
    'close(3)                                = 0\n'
    'openat(AT_FDCWD, "/lib/x86_64-linux-gnu/libz.so.1", O_RDONLY|O_CLOEXEC) = 3\n'
    'read(3, "\\177ELF\\2\\1\\1\\0\\0\\0\\0\\0\\0\\0\\0\\0\\3\\0>\\0\\1\\0\\0\\0\\0\\0\\0\\0\\0\\0\\0\\0"..., 832) = 832\n'
    'fstat(3, {st_mode=S_IFREG|0644, st_size=113000, ...}) = 0\n'
    'mmap(NULL, 110744, PROT_READ, MAP_PRIVATE|MAP_DENYWRITE, 3, 0) = 0x7f6cd8a93000\n'
    'mmap(0x7f6cd8a95000, 73728, PROT_READ|PROT_EXEC, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0x2000) = 0x7f6cd8a95000\n'
    'mmap(0x7f6cd8aa7000, 24576, PROT_READ, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0x14000) = 0x7f6cd8aa7000\n'
    'mmap(0x7f6cd8aad000, 8192, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0x1a000) = 0x7f6cd8aad000\n'
    'close(3)                                = 0\n'
    'openat(AT_FDCWD, "/lib/x86_64-linux-gnu/libc.so.6", O_RDONLY|O_CLOEXEC) = 3\n'
    'read(3, "\\177ELF\\2\\1\\1\\3\\0\\0\\0\\0\\0\\0\\0\\0\\3\\0>\\0\\1\\0\\0\\0\\220\\243\\2\\0\\0\\0\\0\\0"..., 832) = 832\n'
    'pread64(3, "\\6\\0\\0\\0\\4\\0\\0\\0@\\0\\0\\0\\0\\0\\0\\0@\\0\\0\\0\\0\\0\\0\\0@\\0\\0\\0\\0\\0\\0\\0"..., 784, 64) = 784\n'
    'fstat(3, {st_mode=S_IFREG|0755, st_size=2129424, ...}) = 0\n'
    'pread64(3, "\\6\\0\\0\\0\\4\\0\\0\\0@\\0\\0\\0\\0\\0\\0\\0@\\0\\0\\0\\0\\0\\0\\0@\\0\\0\\0\\0\\0\\0\\0"..., 784, 64) = 784\n'
    'mmap(NULL, 2174352, PROT_READ, MAP_PRIVATE|MAP_DENYWRITE, 3, 0) = 0x7f6cd8800000\n'
    'mmap(0x7f6cd8828000, 1609728, PROT_READ|PROT_EXEC, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0x28000) = 0x7f6cd8828000\n'
    'mmap(0x7f6cd89b1000, 323584, PROT_READ, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0x1b1000) = 0x7f6cd89b1000\n'
    'mmap(0x7f6cd8a00000, 24576, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0x1ff000) = 0x7f6cd8a00000\n'
    'mmap(0x7f6cd8a06000, 52624, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_FIXED|MAP_ANONYMOUS, -1, 0) = 0x7f6cd8a06000\n'
    'close(3)                                = 0\n'
    'openat(AT_FDCWD, "/lib/x86_64-linux-gnu/libnghttp2.so.14", O_RDONLY|O_CLOEXEC) = 3\n'
    'read(3, "\\177ELF\\2\\1\\1\\0\\0\\0\\0\\0\\0\\0\\0\\0\\3\\0>\\0\\1\\0\\0\\0\\0\\0\\0\\0\\0\\0\\0\\0"..., 832) = 832\n'
    'fstat(3, {st_mode=S_IFREG|0644, st_size=170352, ...}) = 0\n'
    'mmap(NULL, 172272, PROT_READ, MAP_PRIVATE|MAP_DENYWRITE, 3, 0) = 0x7f6cd8a68000\n'
    'mmap(0x7f6cd8a6d000, 90112, PROT_READ|PROT_EXEC, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0x5000) = 0x7f6cd8a6d000\n'
    'mmap(0x7f6cd8a83000, 49152, PROT_READ, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0x1b000) = 0x7f6cd8a83000\n'
    'mmap(0x7f6cd8a8f000, 16384, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0x26000) = 0x7f6cd8a8f000\n'
    'close(3)                                = 0\n'
    'openat(AT_FDCWD, "/lib/x86_64-linux-gnu/libidn2.so.0", O_RDONLY|O_CLOEXEC) = 3\n'
    'read(3, "\\177ELF\\2\\1\\1\\0\\0\\0\\0\\0\\0\\0\\0\\0\\3\\0>\\0\\1\\0\\0\\0\\0\\0\\0\\0\\0\\0\\0\\0"..., 832) = 832\n'
    'fstat(3, {st_mode=S_IFREG|0644, st_size=133192, ...}) = 0\n'
    'mmap(NULL, 135184, PROT_READ, MAP_PRIVATE|MAP_DENYWRITE, 3, 0) = 0x7f6cd8a46000\n'
    'mmap(0x7f6cd8a48000, 16384, PROT_READ|PROT_EXEC, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0x2000) = 0x7f6cd8a48000\n'
    'mmap(0x7f6cd8a4c000, 106496, PROT_READ, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0x6000) = 0x7f6cd8a4c000\n'
    'mmap(0x7f6cd8a66000, 8192, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0x1f000) = 0x7f6cd8a66000\n'
    'close(3)                                = 0\n'
    'mmap(NULL, 8192, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANONYMOUS, -1, 0) = 0x7f6cd8515000\n'
    'mmap(NULL, 8192, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANONYMOUS, -1, 0) = 0x7f6cd784a000\n'
    'mmap(NULL, 12288, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANONYMOUS, -1, 0) = 0x7f6cd7847000\n'
    'arch_prctl(ARCH_SET_FS, 0x7f6cd7847940) = 0\n'
    'set_tid_address(0x7f6cd7847c10)         = 9281\n'
    'set_robust_list(0x7f6cd7847c20, 24)     = 0\n'
    'rseq(0x7f6cd7848260, 0x20, 0, 0x53053053) = 0\n'
    'mprotect(0x7f6cd8a00000, 16384, PROT_READ) = 0\n'
    'mprotect(0x7f6cd7856000, 4096, PROT_READ) = 0\n'
    'mprotect(0x7f6cd7866000, 4096, PROT_READ) = 0\n'
    'mprotect(0x7f6cd851c000, 4096, PROT_READ) = 0\n'
    'mprotect(0x7f6cd787e000, 4096, PROT_READ) = 0\n'
    'mprotect(0x7f6cd7a09000, 53248, PROT_READ) = 0\n'
    'mprotect(0x7f6cd7a45000, 4096, PROT_READ) = 0\n'
    'mprotect(0x7f6cd84b2000, 376832, PROT_READ) = 0\n'
    'mprotect(0x7f6cd7a5f000, 4096, PROT_READ) = 0\n'
    'mprotect(0x7f6cd7a6c000, 4096, PROT_READ) = 0\n'
    'mprotect(0x7f6cd8524000, 4096, PROT_READ) = 0\n'
    'mprotect(0x7f6cd8550000, 4096, PROT_READ) = 0\n'
    'mprotect(0x7f6cd7b28000, 53248, PROT_READ) = 0\n'
    'mprotect(0x7f6cd7bb9000, 4096, PROT_READ) = 0\n'
    'mprotect(0x7f6cd7c0d000, 8192, PROT_READ) = 0\n'
    'mprotect(0x7f6cd7c55000, 8192, PROT_READ) = 0\n'
    'mprotect(0x7f6cd7ffb000, 16384, PROT_READ) = 0\n'
    'mprotect(0x7f6cd8a66000, 4096, PROT_READ) = 0\n'
    'mprotect(0x7f6cd7e3e000, 65536, PROT_READ) = 0\n'
    'mprotect(0x7f6cd8562000, 4096, PROT_READ) = 0\n'
    'mprotect(0x7f6cd861c000, 4096, PROT_READ) = 0\n'
    'mprotect(0x7f6cd8a22000, 4096, PROT_READ) = 0\n'
    'mprotect(0x7f6cd8677000, 8192, PROT_READ) = 0\n'
    'mprotect(0x7f6cd86cc000, 8192, PROT_READ) = 0\n'
    'mprotect(0x7f6cd876c000, 40960, PROT_READ) = 0\n'
    'mprotect(0x7f6cd878c000, 4096, PROT_READ) = 0\n'
    'mprotect(0x7f6cd8aad000, 4096, PROT_READ) = 0\n'
    'mprotect(0x7f6cd87fc000, 8192, PROT_READ) = 0\n'
    'mprotect(0x7f6cd8a42000, 4096, PROT_READ) = 0\n'
    'mprotect(0x7f6cd8a8f000, 12288, PROT_READ) = 0\n'
    'mprotect(0x7f6cd8b69000, 16384, PROT_READ) = 0\n'
    'mprotect(0x55c1c050b000, 20480, PROT_READ) = 0\n'
    'mprotect(0x7f6cd8bbb000, 8192, PROT_READ) = 0\n'
    'prlimit64(0, RLIMIT_STACK, NULL, {rlim_cur=8192*1024, rlim_max=RLIM64_INFINITY}) = 0\n'
    'munmap(0x7f6cd8b70000, 44439)           = 0\n'
    'getrandom("\\xaf\\xfa\\x83\\x69\\x7d\\x46\\x2f\\x98", 8, GRND_NONBLOCK) = 8\n'
    'brk(NULL)                               = 0x55c1c066b000\n'
    'brk(0x55c1c068c000)                     = 0x55c1c068c000\n'
    'getrandom("\\x90", 1, GRND_NONBLOCK)     = 1\n'
    'newfstatat(AT_FDCWD, "/etc/gnutls/config", {st_mode=S_IFREG|0644, st_size=119, ...}, 0) = 0\n'
    'openat(AT_FDCWD, "/etc/gnutls/config", O_RDONLY|O_CLOEXEC) = 3\n'
    'fstat(3, {st_mode=S_IFREG|0644, st_size=119, ...}) = 0\n'
    'brk(0x55c1c06ad000)                     = 0x55c1c06ad000\n'
    'read(3, "[overrides]\\ndisabled-version = t"..., 4096) = 119\n'
    'read(3, "", 4096)                       = 0\n'
    'lseek(3, 0, SEEK_SET)                   = 0\n'
    'read(3, "[overrides]\\ndisabled-version = t"..., 4096) = 119\n'
    'read(3, "", 4096)                       = 0\n'
    'close(3)                                = 0\n'
    'brk(0x55c1c06ac000)                     = 0x55c1c06ac000\n'
    'fcntl(0, F_GETFD)                       = 0\n'
    'fcntl(1, F_GETFD)                       = 0\n'
    'fcntl(2, F_GETFD)                       = 0\n'
    'rt_sigaction(SIGPIPE, {sa_handler=SIG_IGN, sa_mask=[PIPE], sa_flags=SA_RESTORER|SA_RESTART, sa_restorer=0x7f6cd8845330}, {sa_handler=SIG_DFL, sa_mask=[], sa_flags=0}, 8) = 0\n'
    'futex(0x7f6cd85113ec, FUTEX_WAKE_PRIVATE, 2147483647) = 0\n'
    'futex(0x7f6cd85113e0, FUTEX_WAKE_PRIVATE, 2147483647) = 0\n'
    'futex(0x7f6cd85113d8, FUTEX_WAKE_PRIVATE, 2147483647) = 0\n'
    'futex(0x7f6cd85114e0, FUTEX_WAKE_PRIVATE, 2147483647) = 0\n'
    'futex(0x7f6cd85113c8, FUTEX_WAKE_PRIVATE, 2147483647) = 0\n'
    'futex(0x7f6cd85113c0, FUTEX_WAKE_PRIVATE, 2147483647) = 0\n'
    'openat(AT_FDCWD, "/proc/sys/crypto/fips_enabled", O_RDONLY) = -1 ENOENT (No such file or directory)\n'
    'futex(0x7f6cd85110ec, FUTEX_WAKE_PRIVATE, 2147483647) = 0\n'
    'futex(0x7f6cd8510f08, FUTEX_WAKE_PRIVATE, 2147483647) = 0\n'
    'futex(0x7f6cd8510e44, FUTEX_WAKE_PRIVATE, 2147483647) = 0\n'
    'futex(0x7f6cd8511424, FUTEX_WAKE_PRIVATE, 2147483647) = 0\n'
    'futex(0x7f6cd8510e38, FUTEX_WAKE_PRIVATE, 2147483647) = 0\n'
    'futex(0x7f6cd85113d0, FUTEX_WAKE_PRIVATE, 2147483647) = 0\n'
    'openat(AT_FDCWD, "/usr/lib/ssl/openssl.cnf", O_RDONLY) = 3\n'
    'fstat(3, {st_mode=S_IFREG|0644, st_size=12324, ...}) = 0\n'
    'read(3, "#\\n# OpenSSL example configuratio"..., 4096) = 4096\n'
    'read(3, "d attributes must be the same, a"..., 4096) = 4096\n'
    'read(3, "coding of an extension: beware e"..., 4096) = 4096\n'
    'read(3, " = $insta::certout # insta.cert."..., 4096) = 36\n'
    'read(3, "", 4096)                       = 0\n'
    'close(3)                                = 0\n'
    'futex(0x7f6cd8510bf8, FUTEX_WAKE_PRIVATE, 2147483647) = 0\n'
    'futex(0x7f6cd8510c54, FUTEX_WAKE_PRIVATE, 2147483647) = 0\n'
    'futex(0x7f6cd8511394, FUTEX_WAKE_PRIVATE, 2147483647) = 0\n'
    'futex(0x7f6cd8510bd8, FUTEX_WAKE_PRIVATE, 2147483647) = 0\n'
    'futex(0x7f6cd85113b8, FUTEX_WAKE_PRIVATE, 2147483647) = 0\n'
    'futex(0x7f6cd8779844, FUTEX_WAKE_PRIVATE, 2147483647) = 0\n'
    'futex(0x7f6cd8779864, FUTEX_WAKE_PRIVATE, 2147483647) = 0\n'
    'openat(AT_FDCWD, "/root/.curlrc", O_RDONLY) = -1 ENOENT (No such file or directory)\n'
    'openat(AT_FDCWD, "/root/.config/curlrc", O_RDONLY) = -1 ENOENT (No such file or directory)\n'
    'geteuid()                               = 0\n'
    'socket(AF_UNIX, SOCK_STREAM|SOCK_CLOEXEC|SOCK_NONBLOCK, 0) = 3\n'
    'connect(3, {sa_family=AF_UNIX, sun_path="/var/run/nscd/socket"}, 110) = -1 ENOENT (No such file or directory)\n'
    'close(3)                                = 0\n'
    'socket(AF_UNIX, SOCK_STREAM|SOCK_CLOEXEC|SOCK_NONBLOCK, 0) = 3\n'
    'connect(3, {sa_family=AF_UNIX, sun_path="/var/run/nscd/socket"}, 110) = -1 ENOENT (No such file or directory)\n'
    'close(3)                                = 0\n'
    'newfstatat(AT_FDCWD, "/etc/nsswitch.conf", {st_mode=S_IFREG|0644, st_size=494, ...}, 0) = 0\n'
    'newfstatat(AT_FDCWD, "/", {st_mode=S_IFDIR|0755, st_size=4096, ...}, 0) = 0\n'
    'openat(AT_FDCWD, "/etc/nsswitch.conf", O_RDONLY|O_CLOEXEC) = 3\n'
    'fstat(3, {st_mode=S_IFREG|0644, st_size=494, ...}) = 0\n'
    'read(3, "# /etc/nsswitch.conf\\n#\\n# Example"..., 4096) = 494\n'
    'read(3, "", 4096)                       = 0\n'
    'fstat(3, {st_mode=S_IFREG|0644, st_size=494, ...}) = 0\n'
    'close(3)                                = 0\n'
    'openat(AT_FDCWD, "/etc/passwd", O_RDONLY|O_CLOEXEC) = 3\n'
    'fstat(3, {st_mode=S_IFREG|0644, st_size=3333, ...}) = 0\n'
    'lseek(3, 0, SEEK_SET)                   = 0\n'
    'read(3, "root:x:0:0:root:/root:/bin/bash\\n"..., 4096) = 3333\n'
    'close(3)                                = 0\n'
    'openat(AT_FDCWD, "/root/.curlrc", O_RDONLY) = -1 ENOENT (No such file or directory)\n'
    'rt_sigaction(SIGPIPE, NULL, {sa_handler=SIG_IGN, sa_mask=[PIPE], sa_flags=SA_RESTORER|SA_RESTART, sa_restorer=0x7f6cd8845330}, 8) = 0\n'
    'rt_sigaction(SIGPIPE, {sa_handler=SIG_IGN, sa_mask=[PIPE], sa_flags=SA_RESTORER|SA_RESTART, sa_restorer=0x7f6cd8845330}, NULL, 8) = 0\n'
    'rt_sigaction(SIGPIPE, {sa_handler=SIG_IGN, sa_mask=[PIPE], sa_flags=SA_RESTORER|SA_RESTART, sa_restorer=0x7f6cd8845330}, NULL, 8) = 0\n'
    'ioctl(1, TCGETS, 0x7fff70757d60)        = -1 ENOTTY (Inappropriate ioctl for device)\n'
    'ioctl(0, TIOCGWINSZ, 0x7fff70757e90)    = -1 ENOTTY (Inappropriate ioctl for device)\n'
    'pipe2([3, 4], 0)                        = 0\n'
    'fcntl(3, F_GETFL)                       = 0 (flags O_RDONLY)\n'
    'fcntl(3, F_SETFL, O_RDONLY|O_NONBLOCK)  = 0\n'
    'fcntl(4, F_GETFL)                       = 0x1 (flags O_WRONLY)\n'
    'fcntl(4, F_SETFL, O_WRONLY|O_NONBLOCK)  = 0\n'
    'rt_sigaction(SIGPIPE, NULL, {sa_handler=SIG_IGN, sa_mask=[PIPE], sa_flags=SA_RESTORER|SA_RESTART, sa_restorer=0x7f6cd8845330}, 8) = 0\n'
    'rt_sigaction(SIGPIPE, {sa_handler=SIG_IGN, sa_mask=[PIPE], sa_flags=SA_RESTORER|SA_RESTART, sa_restorer=0x7f6cd8845330}, NULL, 8) = 0\n'
    'poll([{fd=3, events=POLLIN}], 1, 0)     = 0 (Timeout)\n'
    'rt_sigaction(SIGPIPE, NULL, {sa_handler=SIG_IGN, sa_mask=[PIPE], sa_flags=SA_RESTORER|SA_RESTART, sa_restorer=0x7f6cd8845330}, 8) = 0\n'
    'rt_sigaction(SIGPIPE, {sa_handler=SIG_IGN, sa_mask=[PIPE], sa_flags=SA_RESTORER|SA_RESTART, sa_restorer=0x7f6cd8845330}, NULL, 8) = 0\n'
    'brk(0x55c1c06e1000)                     = 0x55c1c06e1000\n'
    'socket(AF_INET, SOCK_STREAM, IPPROTO_TCP) = 5\n'
    'setsockopt(5, SOL_TCP, TCP_NODELAY, [1], 4) = 0\n'
    'setsockopt(5, SOL_SOCKET, SO_KEEPALIVE, [1], 4) = 0\n'
    'setsockopt(5, SOL_TCP, TCP_KEEPIDLE, [60], 4) = 0\n'
    'setsockopt(5, SOL_TCP, TCP_KEEPINTVL, [60], 4) = 0\n'
    'fcntl(5, F_GETFL)                       = 0x2 (flags O_RDWR)\n'
    'fcntl(5, F_SETFL, O_RDWR|O_NONBLOCK)    = 0\n'
    'connect(5, {sa_family=AF_INET, sin_port=htons(9), sin_addr=inet_addr("127.0.0.1")}, 16) = -1 EINPROGRESS (Operation now in progress)\n'
    'getsockname(5, {sa_family=AF_INET, sin_port=htons(59696), sin_addr=inet_addr("127.0.0.1")}, [128 => 16]) = 0\n'
    'rt_sigaction(SIGPIPE, {sa_handler=SIG_IGN, sa_mask=[PIPE], sa_flags=SA_RESTORER|SA_RESTART, sa_restorer=0x7f6cd8845330}, NULL, 8) = 0\n'
    'poll([{fd=5, events=POLLOUT}, {fd=3, events=POLLIN}], 2, 1000) = 1 ([{fd=5, revents=POLLOUT|POLLERR|POLLHUP}])\n'
    'rt_sigaction(SIGPIPE, NULL, {sa_handler=SIG_IGN, sa_mask=[PIPE], sa_flags=SA_RESTORER|SA_RESTART, sa_restorer=0x7f6cd8845330}, 8) = 0\n'
    'rt_sigaction(SIGPIPE, {sa_handler=SIG_IGN, sa_mask=[PIPE], sa_flags=SA_RESTORER|SA_RESTART, sa_restorer=0x7f6cd8845330}, NULL, 8) = 0\n'
    'poll([{fd=5, events=POLLPRI|POLLOUT|POLLWRNORM}], 1, 0) = 1 ([{fd=5, revents=POLLOUT|POLLERR|POLLHUP|POLLWRNORM}])\n'
    'getsockopt(5, SOL_SOCKET, SO_ERROR, [ECONNREFUSED], [4]) = 0\n'
    'getsockname(5, {sa_family=AF_INET, sin_port=htons(59696), sin_addr=inet_addr("127.0.0.1")}, [128 => 16]) = 0\n'
    'close(5)                                = 0\n'
    'brk(0x55c1c06c8000)                     = 0x55c1c06c8000\n'
    'rt_sigaction(SIGPIPE, {sa_handler=SIG_IGN, sa_mask=[PIPE], sa_flags=SA_RESTORER|SA_RESTART, sa_restorer=0x7f6cd8845330}, NULL, 8) = 0\n'
    'rt_sigaction(SIGPIPE, {sa_handler=SIG_IGN, sa_mask=[PIPE], sa_flags=SA_RESTORER|SA_RESTART, sa_restorer=0x7f6cd8845330}, NULL, 8) = 0\n'
    'rt_sigaction(SIGPIPE, NULL, {sa_handler=SIG_IGN, sa_mask=[PIPE], sa_flags=SA_RESTORER|SA_RESTART, sa_restorer=0x7f6cd8845330}, 8) = 0\n'
    'rt_sigaction(SIGPIPE, {sa_handler=SIG_IGN, sa_mask=[PIPE], sa_flags=SA_RESTORER|SA_RESTART, sa_restorer=0x7f6cd8845330}, NULL, 8) = 0\n'
    'rt_sigaction(SIGPIPE, NULL, {sa_handler=SIG_IGN, sa_mask=[PIPE], sa_flags=SA_RESTORER|SA_RESTART, sa_restorer=0x7f6cd8845330}, 8) = 0\n'
    'rt_sigaction(SIGPIPE, {sa_handler=SIG_IGN, sa_mask=[PIPE], sa_flags=SA_RESTORER|SA_RESTART, sa_restorer=0x7f6cd8845330}, NULL, 8) = 0\n'
    'brk(0x55c1c06c7000)                     = 0x55c1c06c7000\n'
    'rt_sigaction(SIGPIPE, {sa_handler=SIG_IGN, sa_mask=[PIPE], sa_flags=SA_RESTORER|SA_RESTART, sa_restorer=0x7f6cd8845330}, NULL, 8) = 0\n'
    'close(3)                                = 0\n'
    'close(4)                                = 0\n'
    'brk(0x55c1c06c5000)                     = 0x55c1c06c5000\n'
    'rt_sigaction(SIGPIPE, {sa_handler=SIG_IGN, sa_mask=[PIPE], sa_flags=SA_RESTORER|SA_RESTART, sa_restorer=0x7f6cd8845330}, NULL, 8) = 0\n'
    'rt_sigaction(SIGPIPE, NULL, {sa_handler=SIG_IGN, sa_mask=[PIPE], sa_flags=SA_RESTORER|SA_RESTART, sa_restorer=0x7f6cd8845330}, 8) = 0\n'
    'rt_sigaction(SIGPIPE, {sa_handler=SIG_IGN, sa_mask=[PIPE], sa_flags=SA_RESTORER|SA_RESTART, sa_restorer=0x7f6cd8845330}, NULL, 8) = 0\n'
    'rt_sigaction(SIGPIPE, {sa_handler=SIG_IGN, sa_mask=[PIPE], sa_flags=SA_RESTORER|SA_RESTART, sa_restorer=0x7f6cd8845330}, NULL, 8) = 0\n'
    'brk(0x55c1c06c3000)                     = 0x55c1c06c3000\n'
    'exit_group(7)                           = ?\n'
    '+++ exited with 7 +++\n'
)
STRACE_LS_O_FILE = (
    '9125  execve("/usr/bin/ls", ["ls", "/tmp"], 0x7ffdb1137ef0 /* 174 vars */) = 0\n'
    '9125  brk(NULL)                         = 0x56352fac4000\n'
    '9125  mmap(NULL, 8192, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANONYMOUS, -1, 0) = 0x7f8dcf7a6000\n'
    '9125  access("/etc/ld.so.preload", R_OK) = -1 ENOENT (No such file or directory)\n'
    '9125  openat(AT_FDCWD, "/etc/ld.so.cache", O_RDONLY|O_CLOEXEC) = 3\n'
    '9125  fstat(3, {st_mode=S_IFREG|0644, st_size=44439, ...}) = 0\n'
    '9125  mmap(NULL, 44439, PROT_READ, MAP_PRIVATE, 3, 0) = 0x7f8dcf79b000\n'
    '9125  close(3)                          = 0\n'
    '9125  openat(AT_FDCWD, "/lib/x86_64-linux-gnu/libselinux.so.1", O_RDONLY|O_CLOEXEC) = 3\n'
    '9125  read(3, "\\177ELF\\2\\1\\1\\0\\0\\0\\0\\0\\0\\0\\0\\0\\3\\0>\\0\\1\\0\\0\\0\\0\\0\\0\\0\\0\\0\\0\\0"..., 832) = 832\n'
    '9125  fstat(3, {st_mode=S_IFREG|0644, st_size=174472, ...}) = 0\n'
    '9125  mmap(NULL, 181960, PROT_READ, MAP_PRIVATE|MAP_DENYWRITE, 3, 0) = 0x7f8dcf76e000\n'
    '9125  mmap(0x7f8dcf774000, 118784, PROT_READ|PROT_EXEC, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0x6000) = 0x7f8dcf774000\n'
    '9125  mmap(0x7f8dcf791000, 24576, PROT_READ, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0x23000) = 0x7f8dcf791000\n'
    '9125  mmap(0x7f8dcf797000, 8192, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0x29000) = 0x7f8dcf797000\n'
    '9125  mmap(0x7f8dcf799000, 5832, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_FIXED|MAP_ANONYMOUS, -1, 0) = 0x7f8dcf799000\n'
    '9125  close(3)                          = 0\n'
    '9125  openat(AT_FDCWD, "/lib/x86_64-linux-gnu/libc.so.6", O_RDONLY|O_CLOEXEC) = 3\n'
    '9125  read(3, "\\177ELF\\2\\1\\1\\3\\0\\0\\0\\0\\0\\0\\0\\0\\3\\0>\\0\\1\\0\\0\\0\\220\\243\\2\\0\\0\\0\\0\\0"..., 832) = 832\n'
    '9125  pread64(3, "\\6\\0\\0\\0\\4\\0\\0\\0@\\0\\0\\0\\0\\0\\0\\0@\\0\\0\\0\\0\\0\\0\\0@\\0\\0\\0\\0\\0\\0\\0"..., 784, 64) = 784\n'
    '9125  fstat(3, {st_mode=S_IFREG|0755, st_size=2129424, ...}) = 0\n'
    '9125  pread64(3, "\\6\\0\\0\\0\\4\\0\\0\\0@\\0\\0\\0\\0\\0\\0\\0@\\0\\0\\0\\0\\0\\0\\0@\\0\\0\\0\\0\\0\\0\\0"..., 784, 64) = 784\n'
    '9125  mmap(NULL, 2174352, PROT_READ, MAP_PRIVATE|MAP_DENYWRITE, 3, 0) = 0x7f8dcf400000\n'
    '9125  mmap(0x7f8dcf428000, 1609728, PROT_READ|PROT_EXEC, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0x28000) = 0x7f8dcf428000\n'
    '9125  mmap(0x7f8dcf5b1000, 323584, PROT_READ, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0x1b1000) = 0x7f8dcf5b1000\n'
    '9125  mmap(0x7f8dcf600000, 24576, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0x1ff000) = 0x7f8dcf600000\n'
    '9125  mmap(0x7f8dcf606000, 52624, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_FIXED|MAP_ANONYMOUS, -1, 0) = 0x7f8dcf606000\n'
    '9125  close(3)                          = 0\n'
    '9125  openat(AT_FDCWD, "/lib/x86_64-linux-gnu/libpcre2-8.so.0", O_RDONLY|O_CLOEXEC) = 3\n'
    '9125  read(3, "\\177ELF\\2\\1\\1\\0\\0\\0\\0\\0\\0\\0\\0\\0\\3\\0>\\0\\1\\0\\0\\0\\0\\0\\0\\0\\0\\0\\0\\0"..., 832) = 832\n'
    '9125  fstat(3, {st_mode=S_IFREG|0644, st_size=625344, ...}) = 0\n'
    '9125  mmap(NULL, 627472, PROT_READ, MAP_PRIVATE|MAP_DENYWRITE, 3, 0) = 0x7f8dcf6d4000\n'
    '9125  mmap(0x7f8dcf6d6000, 450560, PROT_READ|PROT_EXEC, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0x2000) = 0x7f8dcf6d6000\n'
    '9125  mmap(0x7f8dcf744000, 163840, PROT_READ, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0x70000) = 0x7f8dcf744000\n'
    '9125  mmap(0x7f8dcf76c000, 8192, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, 0x97000) = 0x7f8dcf76c000\n'
    '9125  close(3)                          = 0\n'
    '9125  mmap(NULL, 12288, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANONYMOUS, -1, 0) = 0x7f8dcf6d1000\n'
    '9125  arch_prctl(ARCH_SET_FS, 0x7f8dcf6d1800) = 0\n'
    '9125  set_tid_address(0x7f8dcf6d1ad0)   = 9125\n'
    '9125  set_robust_list(0x7f8dcf6d1ae0, 24) = 0\n'
    '9125  rseq(0x7f8dcf6d2120, 0x20, 0, 0x53053053) = 0\n'
    '9125  mprotect(0x7f8dcf600000, 16384, PROT_READ) = 0\n'
    '9125  mprotect(0x7f8dcf76c000, 4096, PROT_READ) = 0\n'
    '9125  mprotect(0x7f8dcf797000, 4096, PROT_READ) = 0\n'
    '9125  mprotect(0x563511ba7000, 8192, PROT_READ) = 0\n'
    '9125  mprotect(0x7f8dcf7e6000, 8192, PROT_READ) = 0\n'
    '9125  prlimit64(0, RLIMIT_STACK, NULL, {rlim_cur=8192*1024, rlim_max=RLIM64_INFINITY}) = 0\n'
    '9125  munmap(0x7f8dcf79b000, 44439)     = 0\n'
    '9125  statfs("/sys/fs/selinux", {f_type=SYSFS_MAGIC, f_bsize=4096, f_blocks=0, f_bfree=0, f_bavail=0, f_files=0, f_ffree=0, f_fsid={val=[0xa15c917a, 0x5d89b42]}, f_namelen=255, f_frsize=4096, f_flags=ST_VALID|ST_RELATIME}) = 0\n'
    '9125  statfs("/selinux", 0x7fff2dd41a10) = -1 ENOENT (No such file or directory)\n'
    '9125  getrandom("\\xb8\\xfc\\xe8\\x3f\\xca\\x02\\x65\\xd9", 8, GRND_NONBLOCK) = 8\n'
    '9125  brk(NULL)                         = 0x56352fac4000\n'
    '9125  brk(0x56352fae5000)               = 0x56352fae5000\n'
    '9125  openat(AT_FDCWD, "/proc/filesystems", O_RDONLY|O_CLOEXEC) = 3\n'
    '9125  fstat(3, {st_mode=S_IFREG|0444, st_size=0, ...}) = 0\n'
    '9125  read(3, "nodev\\tsysfs\\nnodev\\ttmpfs\\nnodev\\tpr"..., 1024) = 380\n'
    '9125  close(3)                          = 0\n'
    '9125  openat(AT_FDCWD, "/proc/mounts", O_RDONLY|O_CLOEXEC) = 3\n'
    '9125  fstat(3, {st_mode=S_IFREG|0444, st_size=0, ...}) = 0\n'
    '9125  read(3, "proc /proc proc rw,relatime 0 0\\n"..., 1024) = 1024\n'
    '9125  read(3, "relatime,cpu 0 0\\ncgroup /sys/fs/"..., 1024) = 662\n'
    '9125  read(3, "", 1024)                 = 0\n'
    '9125  close(3)                          = 0\n'
    '9125  access("/etc/selinux/config", F_OK) = -1 ENOENT (No such file or directory)\n'
    '9125  ioctl(1, TCGETS, 0x7fff2dd418b0)  = -1 ENOTTY (Inappropriate ioctl for device)\n'
    '9125  statx(AT_FDCWD, "/tmp", AT_STATX_SYNC_AS_STAT|AT_NO_AUTOMOUNT, STATX_MODE, {stx_mask=STATX_TYPE|STATX_MODE|STATX_NLINK|STATX_UID|STATX_GID|STATX_ATIME|STATX_INO|STATX_SIZE|STATX_BLOCKS|STATX_MNT_ID, stx_attributes=0, stx_mode=S_IFDIR|S_ISVTX|0777, stx_size=4096, ...}) = 0\n'
    '9125  openat(AT_FDCWD, "/tmp", O_RDONLY|O_NONBLOCK|O_CLOEXEC|O_DIRECTORY) = 3\n'
    '9125  fstat(3, {st_mode=S_IFDIR|S_ISVTX|0777, st_size=4096, ...}) = 0\n'
    '9125  getdents64(3, 0x56352fac9c80 /* 56 entries */, 32768) = 2600\n'
    '9125  getdents64(3, 0x56352fac9c80 /* 0 entries */, 32768) = 0\n'
    '9125  close(3)                          = 0\n'
    '9125  fstat(1, {st_mode=S_IFCHR|0666, st_rdev=makedev(0x1, 0x3), ...}) = 0\n'
    '9125  ioctl(1, TCGETS, 0x7fff2dd3f2d0)  = -1 ENOTTY (Inappropriate ioctl for device)\n'
    '9125  write(1, "cc-socks\\nclaude-0\\nclaude-append-"..., 1319) = 1319\n'
    '9125  close(1)                          = 0\n'
    '9125  close(2)                          = 0\n'
    '9125  exit_group(0)                     = ?\n'
    '9125  +++ exited with 0 +++\n'
)

_ST_LINE = re.compile(r"^(?:(\[pid +(\d+)\] )|((\d+) +))?(.*)$", re.S)
_ST_CALL = re.compile(r"^(\w+)\((.*)$", re.S)
_ST_UNF = " <unfinished ...>"
_ST_RES = re.compile(r"^<\.\.\. (\w+) resumed>(.*)$", re.S)
_ST_FAIL = re.compile(r" = -1 E[A-Z0-9]+ \(")
_ST_RET = re.compile(r" = (-?\d+|0x[0-9a-f]+|\?)(?: E[A-Z0-9]+ \(.*\))?(?: <[\d.]+>)?$")
_ST_LIBPATH = re.compile(r'"(?:/etc/ld\.so\.cache|[^"]*\.so(?:\.\d+)*)"')
_ST_ALWAYS = {"brk", "arch_prctl", "set_tid_address", "set_robust_list", "rseq", "mprotect", "munmap", "getrandom", "prlimit64", "mmap", "statfs"}
_ST_FDCALLS = {"read", "pread64", "fstat", "newfstatat", "lseek", "close"}
_ST_STUB = re.compile(r"^(?:\[pid +\d+\] |\d+ +)?\[ctxprune: \d+ startup syscalls ok")


def _st_split(line: str):
    m = _ST_LINE.match(line)
    return (m.group(1) or m.group(3) or ""), (m.group(2) or m.group(4)), m.group(5)


def _st_join(lines: list[str]) -> list[str]:
    """Replace each <unfinished ...> line + its <... resumed> line by one joined line at the resumed position. A pid has
    at most one call in flight, so its next line is either the resumption or proves the earlier call never resumed."""
    pending: dict = {}
    out: list = list(lines)
    for i, l in enumerate(lines):
        pre, pid, body = _st_split(l)
        p = pending.pop(pid, None)
        if p is not None and (m := _ST_RES.match(body)) and m.group(1) == p[3]:
            out[i] = p[1] + p[2] + m.group(2)
            out[p[0]] = None
        elif body.endswith(_ST_UNF) and (m := _ST_CALL.match(body)):
            pending[pid] = (i, pre, body[: -len(_ST_UNF)], m.group(1))
    return [l for l in out if l is not None]


class _Stub:
    def __init__(self, pre: str):
        self.pre, self.lines, self.names, self.libs = pre, [], Counter(), []

    def render(self) -> list[str]:
        top = ", ".join(f"{n} x{c}" for n, c in sorted(self.names.items(), key=lambda kv: (-kv[1], kv[0]))[:4])
        libs = f"; libraries: {', '.join(self.libs)}" if self.libs else ""
        stub = f"{self.pre}[ctxprune: {len(self.lines)} startup syscalls ok ({top}){libs}]"
        return [stub] if len(stub) < sum(len(l) + 1 for l in self.lines) else self.lines


def collapse_strace(text: str) -> str:
    lines = _st_join(text.split("\n"))
    out: list = []
    state: dict = {}
    for l in lines:
        pre, pid, body = _st_split(l)
        st = state.setdefault(pid, {"on": False, "fds": set(), "cur": None})
        if _ST_STUB.match(l):
            out.append(l)
            continue
        m = _ST_CALL.match(body)
        name = m.group(1) if m else None
        fail = bool(_ST_FAIL.search(body))
        if name == "execve" and not fail:
            st.update(on=True, fds=set(), cur=None)
            out.append(l)
            continue
        if st["on"] and name and not fail:
            ok = name in _ST_ALWAYS
            lib = None
            if name in ("openat", "open") and (lm := _ST_LIBPATH.search(body)) and (r := _ST_RET.search(body)) and r.group(1).isdigit():
                ok, lib = True, lm.group(0).strip('"').rsplit("/", 1)[-1]
                st["fds"].add(r.group(1))
            elif name in _ST_FDCALLS:
                fd = re.match(r"(\d+)\b", m.group(2))
                ok = bool(fd and fd.group(1) in st["fds"])
                if ok and name == "close":
                    st["fds"].discard(fd.group(1))
            if ok:
                if st["cur"] is None:
                    st["cur"] = _Stub(pre)
                    out.append(st["cur"])
                st["cur"].lines.append(l)
                st["cur"].names[name] += 1
                if lib:
                    st["cur"].libs.append(lib)
                continue
        if st["on"] and name and fail:
            st["cur"] = None  # a kept failure splits the run
            out.append(l)
            continue
        st.update(on=False, cur=None)  # first successful non-startup call ends this pid's preamble
        out.append(l)
    flat = []
    for item in out:
        flat += item.render() if isinstance(item, _Stub) else [item]
    return shrink_only(text, "\n".join(flat))


def probes_508(text: str) -> dict:
    pending, events = {}, []
    for raw in text.split("\n"):
        if _ST_STUB.match(raw):
            continue
        m = re.match(r"^(?:\[pid +(\d+)\] |(\d+) +)?(.*)$", raw, re.S)
        pid, body = m.group(1) or m.group(2), m.group(3)
        held = pending.pop(pid, None)
        if held and (r := re.match(r"^<\.\.\. (\w+) resumed>(.*)$", body, re.S)) and r.group(1) == held[0]:
            events.append((pid, held[1] + r.group(2)))
            continue
        if held:
            events.append((pid, held[1] + " <unfinished ...>"))
        if body.endswith(" <unfinished ...>") and (c := re.match(r"^(\w+)\(", body)):
            pending[pid] = (c.group(1), body[: -len(" <unfinished ...>")])
        else:
            events.append((pid, body))
    for pid, held in pending.items():
        events.append((pid, held[1] + " <unfinished ...>"))
    failures, net, opens, writes, procs, marks = [], [], [], [], [], []
    for pid, ev in events:
        c = re.match(r"^(\w+)\(", ev)
        name = c.group(1) if c else None
        if re.search(r" = -1 E[A-Z0-9]+ \(", ev):
            failures.append((pid, ev))
        if name in ("socket", "connect", "bind", "listen", "accept", "accept4", "sendto", "recvfrom"):
            net.append((pid, ev))
        if name in ("open", "openat", "execve", "access", "readlink", "newfstatat", "stat", "lstat", "chdir", "unlink", "mkdir", "rename"):
            q = re.search(r'"([^"]+)"', ev)
            if q and not re.search(r"\.so(\.\d+)*$|^/etc/ld\.so\.cache$", q.group(1)):
                opens.append((pid, ev))
        if name in ("write", "writev", "pwrite64"):
            writes.append((pid, ev))
        if name in ("clone", "clone3", "fork", "vfork", "execve", "wait4", "waitid", "exit", "exit_group", "kill", "tgkill", "pipe", "pipe2", "dup2", "dup3"):
            procs.append((pid, ev))
        if ev.startswith("--- ") or ev.startswith("+++ "):
            marks.append((pid, ev))
    return {"failed calls (= -1 E...)": failures, "network calls": net, "non-library path calls": opens, "writes": writes,
            "process control and exit calls": procs, "signals and exit markers": marks}


case(
    508,
    claim="Joins <unfinished ...>/<... resumed> pairs into one call at the resumed position and replaces each process's run of successful loader syscalls (mmap, mprotect, brk, library openat/read/fstat/close, rseq, ...) between its execve and its first other call by one '[ctxprune: N startup syscalls ok (...); libraries: ...]' line; every failed call, socket/connect/bind, non-library open, write, fork/exec/wait, signal and exit marker is unchanged.",
    loses="the arguments and addresses of the collapsed successful startup calls (including statfs probes and which fds the libraries used), and the position of a blocking call's start relative to other pids' calls (it now appears where it completed)",
    encode=collapse_strace,
    probes=probes_508,
    fixtures=lambda: [
        Fixture("strace -f ls /nonexistent_dir /tmp: ENOENT, write to stderr, exit (real)", STRACE_LS_MISSING_DIR, True),
        Fixture("strace -f sh -c 'ls /tmp | wc -l': three processes, 86 unfinished/resumed pairs (real)", STRACE_SH_PIPELINE, True),
        Fixture("strace -f curl http://127.0.0.1:9/: ECONNREFUSED, exit 7; 6 of the library loads kept (real, trimmed)", STRACE_CURL_REFUSED, True),
        Fixture("strace -f -o file form with 'PID  call' prefixes (real)", STRACE_LS_O_FILE, True),
        Fixture("a trace attached mid-run: no execve, nothing to collapse", 'read(3, "abc", 4096) = 3\nwrite(1, "abc", 3) = 3\nmmap(NULL, 4096, PROT_READ, MAP_PRIVATE, 3, 0) = 0x7f0000\nclose(3) = 0\n', False),
        Fixture("execve that failed, then nothing", 'execve("/nope", ["nope"], 0x7ffd /* 3 vars */) = -1 ENOENT (No such file or directory)\n+++ exited with 1 +++\n', False),
        Fixture("unfinished call that never resumes (killed) and a stray resumed line", '[pid 5] read(3,  <unfinished ...>\n[pid 6] <... futex resumed>) = 0\n+++ killed by SIGKILL +++\n', None),
    ],
    min_gain=0.3,
)



# ===============================================================================================================
# #604 tsc --traceResolution and --listFiles: fold each successfully resolved block to one line, keep unresolved
# blocks whole, collapse lib.*.d.ts and node_modules/@types entries of --listFiles to counts. RULE: every
# (specifier, importing file) -> result, every unresolved block verbatim, every user file and the lib/@types counts.
# Real finding kept in the fixtures: in a project with @types/node, 291 of 421 blocks (70% of 391k chars in the
# full capture) are 'not resolved' blocks for ambient 'node:*' modules, which this rule keeps whole by design.

TSC_TRACE_LISTFILES = (
    "======== Resolving module './b' from '/work/app/src/a.ts'. ========\n"
    "Explicitly specified module resolution kind: 'Bundler'.\n"
    "Resolving in CJS mode with conditions 'import', 'types'.\n"
    "Loading module as file / folder, candidate module location '/work/app/src/b', target file types: TypeScript, JavaScript, Declaration, JSON.\n"
    "File '/work/app/src/b.ts' exists - use it as a name resolution result.\n"
    "======== Module name './b' was successfully resolved to '/work/app/src/b.ts'. ========\n"
    "======== Resolving module './util/fmt' from '/work/app/src/a.ts'. ========\n"
    "Explicitly specified module resolution kind: 'Bundler'.\n"
    "Resolving in CJS mode with conditions 'import', 'types'.\n"
    "Loading module as file / folder, candidate module location '/work/app/src/util/fmt', target file types: TypeScript, JavaScript, Declaration, JSON.\n"
    "File '/work/app/src/util/fmt.ts' exists - use it as a name resolution result.\n"
    "======== Module name './util/fmt' was successfully resolved to '/work/app/src/util/fmt.ts'. ========\n"
    "======== Resolving module './does-not-exist' from '/work/app/src/a.ts'. ========\n"
    "Explicitly specified module resolution kind: 'Bundler'.\n"
    "Resolving in CJS mode with conditions 'import', 'types'.\n"
    "Loading module as file / folder, candidate module location '/work/app/src/does-not-exist', target file types: TypeScript, JavaScript, Declaration, JSON.\n"
    "File '/work/app/src/does-not-exist.ts' does not exist.\n"
    "File '/work/app/src/does-not-exist.tsx' does not exist.\n"
    "File '/work/app/src/does-not-exist.d.ts' does not exist.\n"
    "File '/work/app/src/does-not-exist.js' does not exist.\n"
    "File '/work/app/src/does-not-exist.jsx' does not exist.\n"
    "Directory '/work/app/src/does-not-exist' does not exist, skipping all lookups in it.\n"
    "======== Module name './does-not-exist' was not resolved. ========\n"
    "======== Resolving module 'left-pad' from '/work/app/src/a.ts'. ========\n"
    "Explicitly specified module resolution kind: 'Bundler'.\n"
    "Resolving in CJS mode with conditions 'import', 'types'.\n"
    "File '/work/app/src/package.json' does not exist.\n"
    "Found 'package.json' at '/work/app/package.json'.\n"
    "Loading module 'left-pad' from 'node_modules' folder, target file types: TypeScript, JavaScript, Declaration, JSON.\n"
    'Searching all ancestor node_modules directories for preferred extensions: TypeScript, Declaration.\n'
    "Directory '/work/app/src/node_modules' does not exist, skipping all lookups in it.\n"
    "File '/work/app/node_modules/left-pad.ts' does not exist.\n"
    "File '/work/app/node_modules/left-pad.tsx' does not exist.\n"
    "File '/work/app/node_modules/left-pad.d.ts' does not exist.\n"
    "File '/work/app/node_modules/@types/left-pad.d.ts' does not exist.\n"
    "Directory '/tmp/claude-0/-home-user-ctxprune/d7bbec31-4626-52ed-8422-7b4860cedd2e/scratchpad/ext/compilers_build_native/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/tmp/claude-0/-home-user-ctxprune/d7bbec31-4626-52ed-8422-7b4860cedd2e/scratchpad/ext/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/tmp/claude-0/-home-user-ctxprune/d7bbec31-4626-52ed-8422-7b4860cedd2e/scratchpad/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/tmp/claude-0/-home-user-ctxprune/d7bbec31-4626-52ed-8422-7b4860cedd2e/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/tmp/claude-0/-home-user-ctxprune/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/tmp/claude-0/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/tmp/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/node_modules' does not exist, skipping all lookups in it.\n"
    'Searching all ancestor node_modules directories for fallback extensions: JavaScript, JSON.\n'
    "Directory '/work/app/src/node_modules' does not exist, skipping all lookups in it.\n"
    "File '/work/app/node_modules/left-pad.js' does not exist.\n"
    "File '/work/app/node_modules/left-pad.jsx' does not exist.\n"
    "Directory '/tmp/claude-0/-home-user-ctxprune/d7bbec31-4626-52ed-8422-7b4860cedd2e/scratchpad/ext/compilers_build_native/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/tmp/claude-0/-home-user-ctxprune/d7bbec31-4626-52ed-8422-7b4860cedd2e/scratchpad/ext/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/tmp/claude-0/-home-user-ctxprune/d7bbec31-4626-52ed-8422-7b4860cedd2e/scratchpad/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/tmp/claude-0/-home-user-ctxprune/d7bbec31-4626-52ed-8422-7b4860cedd2e/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/tmp/claude-0/-home-user-ctxprune/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/tmp/claude-0/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/tmp/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/node_modules' does not exist, skipping all lookups in it.\n"
    "======== Module name 'left-pad' was not resolved. ========\n"
    "======== Resolving module '../b' from '/work/app/src/util/fmt.ts'. ========\n"
    "Explicitly specified module resolution kind: 'Bundler'.\n"
    "Resolving in CJS mode with conditions 'import', 'types'.\n"
    "Loading module as file / folder, candidate module location '/work/app/src/b', target file types: TypeScript, JavaScript, Declaration, JSON.\n"
    "File '/work/app/src/b.ts' exists - use it as a name resolution result.\n"
    "======== Module name '../b' was successfully resolved to '/work/app/src/b.ts'. ========\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist.\n"
    "Found 'package.json' at '/usr/lib/node_modules/typescript/package.json'.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/lib/package.json' does not exist according to earlier cached lookups.\n"
    "File '/usr/lib/node_modules/typescript/package.json' exists according to earlier cached lookups.\n"
    "src/a.ts(3,26): error TS2307: Cannot find module './does-not-exist' or its corresponding type declarations.\n"
    "src/a.ts(4,17): error TS2307: Cannot find module 'left-pad' or its corresponding type declarations.\n"
    '/usr/lib/node_modules/typescript/lib/lib.es5.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.es2015.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.es2016.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.es2017.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.es2018.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.es2019.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.es2020.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.dom.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.dom.iterable.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.dom.asynciterable.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.webworker.importscripts.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.scripthost.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.es2015.core.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.es2015.collection.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.es2015.generator.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.es2015.iterable.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.es2015.promise.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.es2015.proxy.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.es2015.reflect.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.es2015.symbol.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.es2015.symbol.wellknown.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.es2016.array.include.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.es2016.intl.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.es2017.arraybuffer.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.es2017.date.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.es2017.object.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.es2017.sharedmemory.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.es2017.string.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.es2017.intl.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.es2017.typedarrays.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.es2018.asyncgenerator.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.es2018.asynciterable.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.es2018.intl.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.es2018.promise.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.es2018.regexp.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.es2019.array.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.es2019.object.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.es2019.string.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.es2019.symbol.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.es2019.intl.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.es2020.bigint.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.es2020.date.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.es2020.promise.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.es2020.sharedmemory.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.es2020.string.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.es2020.symbol.wellknown.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.es2020.intl.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.es2020.number.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.decorators.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.decorators.legacy.d.ts\n'
    '/usr/lib/node_modules/typescript/lib/lib.es2020.full.d.ts\n'
    '/work/app/src/b.ts\n'
    '/work/app/src/util/fmt.ts\n'
    '/work/app/src/a.ts\n'
)
TSC_TRACE_NODE_HEAD = (
    "======== Resolving module './b' from '/work/proj/src/a.ts'. ========\n"
    "Explicitly specified module resolution kind: 'Bundler'.\n"
    "Resolving in CJS mode with conditions 'import', 'types'.\n"
    "Loading module as file / folder, candidate module location '/work/proj/src/b', target file types: TypeScript, JavaScript, Declaration, JSON.\n"
    "File '/work/proj/src/b.ts' exists - use it as a name resolution result.\n"
    "======== Module name './b' was successfully resolved to '/work/proj/src/b.ts'. ========\n"
    "======== Resolving module './util/fmt' from '/work/proj/src/a.ts'. ========\n"
    "Explicitly specified module resolution kind: 'Bundler'.\n"
    "Resolving in CJS mode with conditions 'import', 'types'.\n"
    "Loading module as file / folder, candidate module location '/work/proj/src/util/fmt', target file types: TypeScript, JavaScript, Declaration, JSON.\n"
    "File '/work/proj/src/util/fmt.ts' exists - use it as a name resolution result.\n"
    "======== Module name './util/fmt' was successfully resolved to '/work/proj/src/util/fmt.ts'. ========\n"
    "======== Resolving module './does-not-exist' from '/work/proj/src/a.ts'. ========\n"
    "Explicitly specified module resolution kind: 'Bundler'.\n"
    "Resolving in CJS mode with conditions 'import', 'types'.\n"
    "Loading module as file / folder, candidate module location '/work/proj/src/does-not-exist', target file types: TypeScript, JavaScript, Declaration, JSON.\n"
    "File '/work/proj/src/does-not-exist.ts' does not exist.\n"
    "File '/work/proj/src/does-not-exist.tsx' does not exist.\n"
    "File '/work/proj/src/does-not-exist.d.ts' does not exist.\n"
    "File '/work/proj/src/does-not-exist.js' does not exist.\n"
    "File '/work/proj/src/does-not-exist.jsx' does not exist.\n"
    "Directory '/work/proj/src/does-not-exist' does not exist, skipping all lookups in it.\n"
    "======== Module name './does-not-exist' was not resolved. ========\n"
    "======== Resolving module 'left-pad' from '/work/proj/src/a.ts'. ========\n"
    "Explicitly specified module resolution kind: 'Bundler'.\n"
    "Resolving in CJS mode with conditions 'import', 'types'.\n"
    "File '/work/proj/src/package.json' does not exist.\n"
    "Found 'package.json' at '/work/proj/package.json'.\n"
    "Loading module 'left-pad' from 'node_modules' folder, target file types: TypeScript, JavaScript, Declaration, JSON.\n"
    'Searching all ancestor node_modules directories for preferred extensions: TypeScript, Declaration.\n'
    "Directory '/work/proj/src/node_modules' does not exist, skipping all lookups in it.\n"
    "File '/work/proj/node_modules/left-pad.ts' does not exist.\n"
    "File '/work/proj/node_modules/left-pad.tsx' does not exist.\n"
    "File '/work/proj/node_modules/left-pad.d.ts' does not exist.\n"
    "File '/work/proj/node_modules/@types/left-pad.d.ts' does not exist.\n"
    "Directory '/tmp/claude-0/-home-user-ctxprune/d7bbec31-4626-52ed-8422-7b4860cedd2e/scratchpad/ext/compilers_build_native/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/tmp/claude-0/-home-user-ctxprune/d7bbec31-4626-52ed-8422-7b4860cedd2e/scratchpad/ext/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/tmp/claude-0/-home-user-ctxprune/d7bbec31-4626-52ed-8422-7b4860cedd2e/scratchpad/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/tmp/claude-0/-home-user-ctxprune/d7bbec31-4626-52ed-8422-7b4860cedd2e/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/tmp/claude-0/-home-user-ctxprune/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/tmp/claude-0/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/tmp/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/node_modules' does not exist, skipping all lookups in it.\n"
    'Searching all ancestor node_modules directories for fallback extensions: JavaScript, JSON.\n'
    "Directory '/work/proj/src/node_modules' does not exist, skipping all lookups in it.\n"
    "File '/work/proj/node_modules/left-pad.js' does not exist.\n"
    "File '/work/proj/node_modules/left-pad.jsx' does not exist.\n"
    "Directory '/tmp/claude-0/-home-user-ctxprune/d7bbec31-4626-52ed-8422-7b4860cedd2e/scratchpad/ext/compilers_build_native/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/tmp/claude-0/-home-user-ctxprune/d7bbec31-4626-52ed-8422-7b4860cedd2e/scratchpad/ext/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/tmp/claude-0/-home-user-ctxprune/d7bbec31-4626-52ed-8422-7b4860cedd2e/scratchpad/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/tmp/claude-0/-home-user-ctxprune/d7bbec31-4626-52ed-8422-7b4860cedd2e/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/tmp/claude-0/-home-user-ctxprune/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/tmp/claude-0/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/tmp/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/node_modules' does not exist, skipping all lookups in it.\n"
    "======== Module name 'left-pad' was not resolved. ========\n"
    "======== Resolving module '../b' from '/work/proj/src/util/fmt.ts'. ========\n"
    "Explicitly specified module resolution kind: 'Bundler'.\n"
    "Resolving in CJS mode with conditions 'import', 'types'.\n"
    "Loading module as file / folder, candidate module location '/work/proj/src/b', target file types: TypeScript, JavaScript, Declaration, JSON.\n"
    "File '/work/proj/src/b.ts' exists - use it as a name resolution result.\n"
    "======== Module name '../b' was successfully resolved to '/work/proj/src/b.ts'. ========\n"
    "======== Resolving type reference directive 'node', containing file '/work/proj/__inferred type names__.ts', root directory '/work/proj/node_modules/@types,/tmp/claude-0/-home-user-ctxprune/d7bbec31-4626-52ed-8422-7b4860cedd2e/scratchpad/ext/compilers_build_native/node_modules/@types,/tmp/claude-0/-home-user-ctxprune/d7bbec31-4626-52ed-8422-7b4860cedd2e/scratchpad/ext/node_modules/@types,/tmp/claude-0/-home-user-ctxprune/d7bbec31-4626-52ed-8422-7b4860cedd2e/scratchpad/node_modules/@types,/tmp/claude-0/-home-user-ctxprune/d7bbec31-4626-52ed-8422-7b4860cedd2e/node_modules/@types,/tmp/claude-0/-home-user-ctxprune/node_modules/@types,/tmp/claude-0/node_modules/@types,/tmp/node_modules/@types,/node_modules/@types'. ========\n"
    "Resolving with primary search path '/work/proj/node_modules/@types, /tmp/claude-0/-home-user-ctxprune/d7bbec31-4626-52ed-8422-7b4860cedd2e/scratchpad/ext/compilers_build_native/node_modules/@types, /tmp/claude-0/-home-user-ctxprune/d7bbec31-4626-52ed-8422-7b4860cedd2e/scratchpad/ext/node_modules/@types, /tmp/claude-0/-home-user-ctxprune/d7bbec31-4626-52ed-8422-7b4860cedd2e/scratchpad/node_modules/@types, /tmp/claude-0/-home-user-ctxprune/d7bbec31-4626-52ed-8422-7b4860cedd2e/node_modules/@types, /tmp/claude-0/-home-user-ctxprune/node_modules/@types, /tmp/claude-0/node_modules/@types, /tmp/node_modules/@types, /node_modules/@types'.\n"
    "Found 'package.json' at '/work/proj/node_modules/@types/node/package.json'.\n"
    "'package.json' has a 'typesVersions' field with version-specific path mappings.\n"
    "'package.json' does not have a 'typesVersions' entry that matches version '6.0'.\n"
    "'package.json' does not have a 'typings' field.\n"
    "'package.json' has 'types' field 'index.d.ts' that references '/work/proj/node_modules/@types/node/index.d.ts'.\n"
    "File '/work/proj/node_modules/@types/node/index.d.ts' exists - use it as a name resolution result.\n"
    "'package.json' has a 'peerDependencies' field.\n"
    "Resolving real path for '/work/proj/node_modules/@types/node', result '/work/proj/node_modules/@types/node'.\n"
    "Resolving real path for '/work/proj/node_modules/@types/node/index.d.ts', result '/work/proj/node_modules/@types/node/index.d.ts'.\n"
    "======== Type reference directive 'node' was successfully resolved to '/work/proj/node_modules/@types/node/index.d.ts' with Package ID '@types/node/index.d.ts@26.6.6', primary: true. ========\n"
    "File '/work/proj/node_modules/@types/node/package.json' exists according to earlier cached lookups.\n"
    "File '/work/proj/node_modules/@types/node/package.json' exists according to earlier cached lookups.\n"
    "File '/work/proj/node_modules/@types/node/package.json' exists according to earlier cached lookups.\n"
    "File '/work/proj/node_modules/@types/node/package.json' exists according to earlier cached lookups.\n"
    "File '/work/proj/node_modules/@types/node/web-globals/package.json' does not exist.\n"
    "File '/work/proj/node_modules/@types/node/package.json' exists according to earlier cached lookups.\n"
    "======== Resolving module 'node:events' from '/work/proj/node_modules/@types/node/web-globals/abortcontroller.d.ts'. ========\n"
    "Explicitly specified module resolution kind: 'Bundler'.\n"
    "Resolving in CJS mode with conditions 'import', 'types'.\n"
    "File '/work/proj/node_modules/@types/node/web-globals/package.json' does not exist according to earlier cached lookups.\n"
    "File '/work/proj/node_modules/@types/node/package.json' exists according to earlier cached lookups.\n"
    "Skipping module 'node:events' that looks like an absolute URI, target file types: TypeScript, JavaScript, Declaration, JSON.\n"
    "======== Module name 'node:events' was not resolved. ========\n"
    "File '/work/proj/node_modules/@types/node/web-globals/package.json' does not exist according to earlier cached lookups.\n"
    "File '/work/proj/node_modules/@types/node/package.json' exists according to earlier cached lookups.\n"
    "======== Resolving module 'node:buffer' from '/work/proj/node_modules/@types/node/web-globals/blob.d.ts'. ========\n"
    "Explicitly specified module resolution kind: 'Bundler'.\n"
    "Resolving in CJS mode with conditions 'import', 'types'.\n"
    "File '/work/proj/node_modules/@types/node/web-globals/package.json' does not exist according to earlier cached lookups.\n"
    "File '/work/proj/node_modules/@types/node/package.json' exists according to earlier cached lookups.\n"
    "Skipping module 'node:buffer' that looks like an absolute URI, target file types: TypeScript, JavaScript, Declaration, JSON.\n"
    "======== Module name 'node:buffer' was not resolved. ========\n"
    "File '/work/proj/node_modules/@types/node/web-globals/package.json' does not exist according to earlier cached lookups.\n"
    "File '/work/proj/node_modules/@types/node/package.json' exists according to earlier cached lookups.\n"
    "======== Resolving module 'node:console' from '/work/proj/node_modules/@types/node/web-globals/console.d.ts'. ========\n"
    "Explicitly specified module resolution kind: 'Bundler'.\n"
    "Resolving in CJS mode with conditions 'import', 'types'.\n"
    "File '/work/proj/node_modules/@types/node/web-globals/package.json' does not exist according to earlier cached lookups.\n"
    "File '/work/proj/node_modules/@types/node/package.json' exists according to earlier cached lookups.\n"
    "Skipping module 'node:console' that looks like an absolute URI, target file types: TypeScript, JavaScript, Declaration, JSON.\n"
    "======== Module name 'node:console' was not resolved. ========\n"
    "File '/work/proj/node_modules/@types/node/web-globals/package.json' does not exist according to earlier cached lookups.\n"
    "File '/work/proj/node_modules/@types/node/package.json' exists according to earlier cached lookups.\n"
    "======== Resolving module 'crypto' from '/work/proj/node_modules/@types/node/web-globals/crypto.d.ts'. ========\n"
    "Explicitly specified module resolution kind: 'Bundler'.\n"
    "Resolving in CJS mode with conditions 'import', 'types'.\n"
    "File '/work/proj/node_modules/@types/node/web-globals/package.json' does not exist according to earlier cached lookups.\n"
    "File '/work/proj/node_modules/@types/node/package.json' exists according to earlier cached lookups.\n"
    "Loading module 'crypto' from 'node_modules' folder, target file types: TypeScript, JavaScript, Declaration, JSON.\n"
    'Searching all ancestor node_modules directories for preferred extensions: TypeScript, Declaration.\n'
    "Directory '/work/proj/node_modules/@types/node/web-globals/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/work/proj/node_modules/@types/node/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/work/proj/node_modules/@types/node_modules' does not exist, skipping all lookups in it.\n"
    "File '/work/proj/node_modules/crypto.ts' does not exist.\n"
    "File '/work/proj/node_modules/crypto.tsx' does not exist.\n"
    "File '/work/proj/node_modules/crypto.d.ts' does not exist.\n"
    "File '/work/proj/node_modules/@types/crypto.d.ts' does not exist.\n"
    "Directory '/tmp/claude-0/-home-user-ctxprune/d7bbec31-4626-52ed-8422-7b4860cedd2e/scratchpad/ext/compilers_build_native/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/tmp/claude-0/-home-user-ctxprune/d7bbec31-4626-52ed-8422-7b4860cedd2e/scratchpad/ext/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/tmp/claude-0/-home-user-ctxprune/d7bbec31-4626-52ed-8422-7b4860cedd2e/scratchpad/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/tmp/claude-0/-home-user-ctxprune/d7bbec31-4626-52ed-8422-7b4860cedd2e/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/tmp/claude-0/-home-user-ctxprune/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/tmp/claude-0/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/tmp/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/node_modules' does not exist, skipping all lookups in it.\n"
    'Searching all ancestor node_modules directories for fallback extensions: JavaScript, JSON.\n'
    "Directory '/work/proj/node_modules/@types/node/web-globals/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/work/proj/node_modules/@types/node/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/work/proj/node_modules/@types/node_modules' does not exist, skipping all lookups in it.\n"
    "File '/work/proj/node_modules/crypto.js' does not exist.\n"
    "File '/work/proj/node_modules/crypto.jsx' does not exist.\n"
    "Directory '/tmp/claude-0/-home-user-ctxprune/d7bbec31-4626-52ed-8422-7b4860cedd2e/scratchpad/ext/compilers_build_native/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/tmp/claude-0/-home-user-ctxprune/d7bbec31-4626-52ed-8422-7b4860cedd2e/scratchpad/ext/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/tmp/claude-0/-home-user-ctxprune/d7bbec31-4626-52ed-8422-7b4860cedd2e/scratchpad/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/tmp/claude-0/-home-user-ctxprune/d7bbec31-4626-52ed-8422-7b4860cedd2e/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/tmp/claude-0/-home-user-ctxprune/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/tmp/claude-0/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/tmp/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/node_modules' does not exist, skipping all lookups in it.\n"
    "======== Module name 'crypto' was not resolved. ========\n"
    "File '/work/proj/node_modules/@types/node/web-globals/package.json' does not exist according to earlier cached lookups.\n"
    "File '/work/proj/node_modules/@types/node/package.json' exists according to earlier cached lookups.\n"
    "File '/work/proj/node_modules/@types/node/web-globals/package.json' does not exist according to earlier cached lookups.\n"
    "File '/work/proj/node_modules/@types/node/package.json' exists according to earlier cached lookups.\n"
    "======== Resolving module 'node:util' from '/work/proj/node_modules/@types/node/web-globals/encoding.d.ts'. ========\n"
    "Explicitly specified module resolution kind: 'Bundler'.\n"
    "Resolving in CJS mode with conditions 'import', 'types'.\n"
    "File '/work/proj/node_modules/@types/node/web-globals/package.json' does not exist according to earlier cached lookups.\n"
    "File '/work/proj/node_modules/@types/node/package.json' exists according to earlier cached lookups.\n"
    "Skipping module 'node:util' that looks like an absolute URI, target file types: TypeScript, JavaScript, Declaration, JSON.\n"
    "======== Module name 'node:util' was not resolved. ========\n"
    "File '/work/proj/node_modules/@types/node/web-globals/package.json' does not exist according to earlier cached lookups.\n"
    "File '/work/proj/node_modules/@types/node/package.json' exists according to earlier cached lookups.\n"
    "File '/work/proj/node_modules/@types/node/web-globals/package.json' does not exist according to earlier cached lookups.\n"
    "File '/work/proj/node_modules/@types/node/package.json' exists according to earlier cached lookups.\n"
    "======== Resolving module 'undici-types' from '/work/proj/node_modules/@types/node/web-globals/fetch.d.ts'. ========\n"
    "Explicitly specified module resolution kind: 'Bundler'.\n"
    "Resolving in CJS mode with conditions 'import', 'types'.\n"
    "File '/work/proj/node_modules/@types/node/web-globals/package.json' does not exist according to earlier cached lookups.\n"
    "File '/work/proj/node_modules/@types/node/package.json' exists according to earlier cached lookups.\n"
    "Loading module 'undici-types' from 'node_modules' folder, target file types: TypeScript, JavaScript, Declaration, JSON.\n"
    'Searching all ancestor node_modules directories for preferred extensions: TypeScript, Declaration.\n'
    "Directory '/work/proj/node_modules/@types/node/web-globals/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/work/proj/node_modules/@types/node/node_modules' does not exist, skipping all lookups in it.\n"
    "Directory '/work/proj/node_modules/@types/node_modules' does not exist, skipping all lookups in it.\n"
    "Found 'package.json' at '/work/proj/node_modules/undici-types/package.json'.\n"
    "File '/work/proj/node_modules/undici-types.ts' does not exist.\n"
    "File '/work/proj/node_modules/undici-types.tsx' does not exist.\n"
    "File '/work/proj/node_modules/undici-types.d.ts' does not exist.\n"
    "'package.json' does not have a 'typesVersions' field.\n"
    "'package.json' does not have a 'typings' field.\n"
    "'package.json' has 'types' field 'index.d.ts' that references '/work/proj/node_modules/undici-types/index.d.ts'.\n"
    "File '/work/proj/node_modules/undici-types/index.d.ts' exists - use it as a name resolution result.\n"
    "'package.json' does not have a 'peerDependencies' field.\n"
    "Resolving real path for '/work/proj/node_modules/undici-types/index.d.ts', result '/work/proj/node_modules/undici-types/index.d.ts'.\n"
    "======== Module name 'undici-types' was successfully resolved to '/work/proj/node_modules/undici-types/index.d.ts' with Package ID 'undici-types/index.d.ts@8.9.0'. ========\n"
    "File '/work/proj/node_modules/undici-types/package.json' exists according to earlier cached lookups.\n"
    "======== Resolving module './dispatcher' from '/work/proj/node_modules/undici-types/index.d.ts'. ========\n"
    "Explicitly specified module resolution kind: 'Bundler'.\n"
    "Resolving in CJS mode with conditions 'import', 'types'.\n"
    "Loading module as file / folder, candidate module location '/work/proj/node_modules/undici-types/dispatcher', target file types: TypeScript, JavaScript, Declaration, JSON.\n"
    "File '/work/proj/node_modules/undici-types/dispatcher.ts' does not exist.\n"
    "File '/work/proj/node_modules/undici-types/dispatcher.tsx' does not exist.\n"
    "File '/work/proj/node_modules/undici-types/dispatcher.d.ts' exists - use it as a name resolution result.\n"
    "File '/work/proj/node_modules/undici-types/package.json' exists according to earlier cached lookups.\n"
    "======== Module name './dispatcher' was successfully resolved to '/work/proj/node_modules/undici-types/dispatcher.d.ts' with Package ID 'undici-types/dispatcher.d.ts@8.9.0'. ========\n"
    "======== Resolving module './global-dispatcher' from '/work/proj/node_modules/undici-types/index.d.ts'. ========\n"
    "Explicitly specified module resolution kind: 'Bundler'.\n"
    "Resolving in CJS mode with conditions 'import', 'types'.\n"
    "Loading module as file / folder, candidate module location '/work/proj/node_modules/undici-types/global-dispatcher', target file types: TypeScript, JavaScript, Declaration, JSON.\n"
    "File '/work/proj/node_modules/undici-types/global-dispatcher.ts' does not exist.\n"
    "File '/work/proj/node_modules/undici-types/global-dispatcher.tsx' does not exist.\n"
    "File '/work/proj/node_modules/undici-types/global-dispatcher.d.ts' exists - use it as a name resolution result.\n"
    "File '/work/proj/node_modules/undici-types/package.json' exists according to earlier cached lookups.\n"
    "======== Module name './global-dispatcher' was successfully resolved to '/work/proj/node_modules/undici-types/global-dispatcher.d.ts' with Package ID 'undici-types/global-dispatcher.d.ts@8.9.0'. ========\n"
    "======== Resolving module './global-origin' from '/work/proj/node_modules/undici-types/index.d.ts'. ========\n"
    "Explicitly specified module resolution kind: 'Bundler'.\n"
    "Resolving in CJS mode with conditions 'import', 'types'.\n"
    "Loading module as file / folder, candidate module location '/work/proj/node_modules/undici-types/global-origin', target file types: TypeScript, JavaScript, Declaration, JSON.\n"
    "File '/work/proj/node_modules/undici-types/global-origin.ts' does not exist.\n"
    "File '/work/proj/node_modules/undici-types/global-origin.tsx' does not exist.\n"
    "File '/work/proj/node_modules/undici-types/global-origin.d.ts' exists - use it as a name resolution result.\n"
    "File '/work/proj/node_modules/undici-types/package.json' exists according to earlier cached lookups.\n"
    "======== Module name './global-origin' was successfully resolved to '/work/proj/node_modules/undici-types/global-origin.d.ts' with Package ID 'undici-types/global-origin.d.ts@8.9.0'. ========\n"
    "======== Resolving module './pool' from '/work/proj/node_modules/undici-types/index.d.ts'. ========\n"
    "Explicitly specified module resolution kind: 'Bundler'.\n"
    "Resolving in CJS mode with conditions 'import', 'types'.\n"
    "Loading module as file / folder, candidate module location '/work/proj/node_modules/undici-types/pool', target file types: TypeScript, JavaScript, Declaration, JSON.\n"
    "File '/work/proj/node_modules/undici-types/pool.ts' does not exist.\n"
    "File '/work/proj/node_modules/undici-types/pool.tsx' does not exist.\n"
    "File '/work/proj/node_modules/undici-types/pool.d.ts' exists - use it as a name resolution result.\n"
)

_T_START = re.compile(r"^======== Resolving (module|type reference directive) '(.*)'(?:, containing file| from) '(.*?)'(?:, root directory '.*')?\. ========$")
_T_OK_MOD = re.compile(r"^======== Module name '(.*)' was successfully resolved to '(.*?)'(?: with Package ID '(.*)')?\. ========$")
_T_OK_REF = re.compile(r"^======== Type reference directive '(.*)' was successfully resolved to '(.*?)'(?: with Package ID '(.*?)')?, primary: (true|false)\. ========$")
_T_BAD = re.compile(r"^======== (?:Module name|Type reference directive) '(.*)' was not resolved\. ========$")
_T_FOLDED = re.compile(r"^\[ctxprune ok\] (module|type reference) '(.*)' from '(.*?)' -> '(.*?)'(?: package '(.*?)')?(?: primary (true|false))?$")
_T_LIBFILE = re.compile(r"^/.*/typescript/lib/lib\.[A-Za-z0-9.]*\.d\.ts$")
_T_TYPES = re.compile(r"^/.*/node_modules/@types/.*\.d\.ts$")
_T_STUB = re.compile(r"^\[ctxprune: (\d+) (TypeScript lib|@types) files omitted\]$")


def fold_resolution(text: str) -> str:
    lines = text.split("\n")
    out, i, n = [], 0, len(lines)
    stub_at: dict = {}
    counts: Counter = Counter()
    while i < n:
        l = lines[i]
        if (m := _T_START.match(l)):
            j = i + 1
            while j < n and not (_T_OK_MOD.match(lines[j]) or _T_OK_REF.match(lines[j]) or _T_BAD.match(lines[j]) or _T_START.match(lines[j])):
                j += 1
            if j < n and (ok := _T_OK_MOD.match(lines[j]) or _T_OK_REF.match(lines[j])):
                kind = "module" if m.group(1) == "module" else "type reference"
                tail = f" package '{ok.group(3)}'" if ok.group(3) else ""
                tail += f" primary {ok.group(4)}" if ok.re is _T_OK_REF else ""
                out.append(f"[ctxprune ok] {kind} '{m.group(2)}' from '{m.group(3)}' -> '{ok.group(2)}'{tail}")
                i = j + 1
            else:  # unresolved, abandoned or cut off: keep every line of it, and count nothing inside it
                end = j + 1 if j < n and _T_BAD.match(lines[j]) else j
                out += lines[i:end]
                i = end
            continue
        cls = "TypeScript lib" if _T_LIBFILE.match(l) else "@types" if _T_TYPES.match(l) else None
        if cls:
            counts[cls] += 1
            if cls not in stub_at:
                stub_at[cls] = len(out)
                out.append(None)
            i += 1
            continue
        out.append(l)
        i += 1
    for cls, at in stub_at.items():
        out[at] = f"[ctxprune: {counts[cls]} {cls} files omitted]"
    return shrink_only(text, "\n".join(out))


def probes_604(text: str) -> dict:
    resolutions, bad_blocks, user_files, n_lib, n_types = [], [], [], 0, 0
    cur = None
    for l in text.split("\n"):
        if (m := _T_STUB.match(l)):
            if m.group(2) == "@types":
                n_types += int(m.group(1))
            else:
                n_lib += int(m.group(1))
            continue
        if (m := _T_FOLDED.match(l)):
            resolutions.append((m.group(1), m.group(2), m.group(3), m.group(4), m.group(5), m.group(6)))
            continue
        if (m := _T_START.match(l)):
            cur = ([l], m)
            continue
        if cur is not None:
            cur[0].append(l)
            if (ok := _T_OK_MOD.match(l)) or (ok := _T_OK_REF.match(l)):
                st = cur[1]
                is_mod = st.group(1) == "module"
                resolutions.append(("module" if is_mod else "type reference", st.group(2), st.group(3), ok.group(2), ok.group(3), None if is_mod else ok.group(4)))
                cur = None
            elif _T_BAD.match(l):
                bad_blocks.append("\n".join(cur[0]))
                cur = None
            continue
        if _T_LIBFILE.match(l):
            n_lib += 1
        elif _T_TYPES.match(l):
            n_types += 1
        elif re.match(r"^/\S+\.(?:d\.)?tsx?$", l):
            user_files.append(l)
    return {"resolutions (kind, specifier, from, result, package id, primary)": resolutions, "unresolved blocks, verbatim": bad_blocks,
            "listFiles: user files": user_files, "listFiles: lib and @types counts": (n_lib, n_types)}


case(
    604,
    claim="Each '======== Resolving ... ========' block that ends 'successfully resolved' becomes one '[ctxprune ok] module X from Y -> Z' line, 'not resolved' blocks stay whole, and --listFiles entries under typescript/lib and node_modules/@types become two count lines; every resolution result, every unresolved block, every user file and the lib/@types counts are unchanged.",
    loses="the candidate paths, extensions and 'directory does not exist' lines tried inside each successful resolution, and the individual lib/@types file names",
    encode=fold_resolution,
    probes=probes_604,
    fixtures=lambda: [
        Fixture("tsc --traceResolution --listFiles: 2 broken imports, 3 resolved, 56 listed files (real)", TSC_TRACE_LISTFILES, True),
        Fixture("with @types/node: first 215 lines of a 3,883-line real trace, ambient node:* blocks stay (real, cut)", TSC_TRACE_NODE_HEAD, True),
        Fixture("plain tsc output with one error", "src/a.ts(3,26): error TS2307: Cannot find module './does-not-exist' or its corresponding type declarations.\n", False),
        Fixture("only unresolved blocks", "======== Resolving module 'left-pad' from '/work/proj/src/a.ts'. ========\nExplicitly specified module resolution kind: 'Bundler'.\n======== Module name 'left-pad' was not resolved. ========\n", False),
    ],
    min_gain=0.2,
)



# ===============================================================================================================
# #609 Python lock hashes: requirements.txt --hash continuation lines (+ '# via' blocks), poetry.lock and Pipfile.lock
# collapse to 'name==version'. RULE: the name==version pairs must equal what a real parser reads from the original.
# Encoder: line scanners. Probe: logical-line / block regexes; fixtures() additionally proves the probe against
# tomllib (poetry.lock) and json (Pipfile.lock) on the real captures, so the probe itself is not a mirror.

PIP_COMPILE_HASHES = (
    '# This file was autogenerated by uv via the following command:\n'
    '#    uv pip compile --generate-hashes --python-version 3.12 requirements.in -o requirements.txt\n'
    'attrs==24.2.0 \\\n'
    '    --hash=sha256:5cfb1b9148b5b086569baec03f20d7b6bf3bcacc9a42bebf87ffaaca362f6346 \\\n'
    '    --hash=sha256:81921eb96de3191c8258c199618104dd27ac608d9366f5e35d011eae1867ede2\n'
    '    # via -r requirements.in\n'
    'blinker==1.8.2 \\\n'
    '    --hash=sha256:1779309f71bf239144b9399d06ae925637cf6634cf6bd131104184531bf67c01 \\\n'
    '    --hash=sha256:8f77b09d3bf7c795e969e9486f39c2c5e9c39d4ee07424be2bc594ece9642d83\n'
    '    # via -r requirements.in\n'
    'idna==3.10 \\\n'
    '    --hash=sha256:12f65c9b470abda6dc35cf8e63cc574b1c52b11df2c86030af0ac09b01b13ea9 \\\n'
    '    --hash=sha256:946d195a0d259cbba61165e88e65941f16e9b36ea6ddb97f00452bae8b1287d3\n'
    '    # via -r requirements.in\n'
    'itsdangerous==2.2.0 \\\n'
    '    --hash=sha256:c6242fc49e35958c8b15141343aa660db5fc54d4f13a1db01a3f5891b98700ef \\\n'
    '    --hash=sha256:e0050c0b7da1eea53ffaf149c0cfbb5c6e2e2b69c4bef22c81fa6eb73e5f6173\n'
    '    # via -r requirements.in\n'
    'markupsafe==2.1.5 \\\n'
    '    --hash=sha256:00e046b6dd71aa03a41079792f8473dc494d564611a8f89bbbd7cb93295ebdcf \\\n'
    '    --hash=sha256:075202fa5b72c86ad32dc7d0b56024ebdbcf2048c0ba09f1cde31bfdd57bcfff \\\n'
    '    --hash=sha256:0e397ac966fdf721b2c528cf028494e86172b4feba51d65f81ffd65c63798f3f \\\n'
    '    --hash=sha256:17b950fccb810b3293638215058e432159d2b71005c74371d784862b7e4683f3 \\\n'
    '    --hash=sha256:1f3fbcb7ef1f16e48246f704ab79d79da8a46891e2da03f8783a5b6fa41a9532 \\\n'
    '    --hash=sha256:2174c595a0d73a3080ca3257b40096db99799265e1c27cc5a610743acd86d62f \\\n'
    '    --hash=sha256:2b7c57a4dfc4f16f7142221afe5ba4e093e09e728ca65c51f5620c9aaeb9a617 \\\n'
    '    --hash=sha256:2d2d793e36e230fd32babe143b04cec8a8b3eb8a3122d2aceb4a371e6b09b8df \\\n'
    '    --hash=sha256:30b600cf0a7ac9234b2638fbc0fb6158ba5bdcdf46aeb631ead21248b9affbc4 \\\n'
    '    --hash=sha256:397081c1a0bfb5124355710fe79478cdbeb39626492b15d399526ae53422b906 \\\n'
    '    --hash=sha256:3a57fdd7ce31c7ff06cdfbf31dafa96cc533c21e443d57f5b1ecc6cdc668ec7f \\\n'
    '    --hash=sha256:3c6b973f22eb18a789b1460b4b91bf04ae3f0c4234a0a6aa6b0a92f6f7b951d4 \\\n'
    '    --hash=sha256:3e53af139f8579a6d5f7b76549125f0d94d7e630761a2111bc431fd820e163b8 \\\n'
    '    --hash=sha256:4096e9de5c6fdf43fb4f04c26fb114f61ef0bf2e5604b6ee3019d51b69e8c371 \\\n'
    '    --hash=sha256:4275d846e41ecefa46e2015117a9f491e57a71ddd59bbead77e904dc02b1bed2 \\\n'
    '    --hash=sha256:4c31f53cdae6ecfa91a77820e8b151dba54ab528ba65dfd235c80b086d68a465 \\\n'
    '    --hash=sha256:4f11aa001c540f62c6166c7726f71f7573b52c68c31f014c25cc7901deea0b52 \\\n'
    '    --hash=sha256:5049256f536511ee3f7e1b3f87d1d1209d327e818e6ae1365e8653d7e3abb6a6 \\\n'
    '    --hash=sha256:58c98fee265677f63a4385256a6d7683ab1832f3ddd1e66fe948d5880c21a169 \\\n'
    '    --hash=sha256:598e3276b64aff0e7b3451b72e94fa3c238d452e7ddcd893c3ab324717456bad \\\n'
    '    --hash=sha256:5b7b716f97b52c5a14bffdf688f971b2d5ef4029127f1ad7a513973cfd818df2 \\\n'
    '    --hash=sha256:5dedb4db619ba5a2787a94d877bc8ffc0566f92a01c0ef214865e54ecc9ee5e0 \\\n'
    '    --hash=sha256:619bc166c4f2de5caa5a633b8b7326fbe98e0ccbfacabd87268a2b15ff73a029 \\\n'
    '    --hash=sha256:629ddd2ca402ae6dbedfceeba9c46d5f7b2a61d9749597d4307f943ef198fc1f \\\n'
    '    --hash=sha256:656f7526c69fac7f600bd1f400991cc282b417d17539a1b228617081106feb4a \\\n'
    '    --hash=sha256:6ec585f69cec0aa07d945b20805be741395e28ac1627333b1c5b0105962ffced \\\n'
    '    --hash=sha256:72b6be590cc35924b02c78ef34b467da4ba07e4e0f0454a2c5907f473fc50ce5 \\\n'
    '    --hash=sha256:7502934a33b54030eaf1194c21c692a534196063db72176b0c4028e140f8f32c \\\n'
    '    --hash=sha256:7a68b554d356a91cce1236aa7682dc01df0edba8d043fd1ce607c49dd3c1edcf \\\n'
    '    --hash=sha256:7b2e5a267c855eea6b4283940daa6e88a285f5f2a67f2220203786dfa59b37e9 \\\n'
    '    --hash=sha256:823b65d8706e32ad2df51ed89496147a42a2a6e01c13cfb6ffb8b1e92bc910bb \\\n'
    '    --hash=sha256:8590b4ae07a35970728874632fed7bd57b26b0102df2d2b233b6d9d82f6c62ad \\\n'
    '    --hash=sha256:8dd717634f5a044f860435c1d8c16a270ddf0ef8588d4887037c5028b859b0c3 \\\n'
    '    --hash=sha256:8dec4936e9c3100156f8a2dc89c4b88d5c435175ff03413b443469c7c8c5f4d1 \\\n'
    '    --hash=sha256:97cafb1f3cbcd3fd2b6fbfb99ae11cdb14deea0736fc2b0952ee177f2b813a46 \\\n'
    '    --hash=sha256:a17a92de5231666cfbe003f0e4b9b3a7ae3afb1ec2845aadc2bacc93ff85febc \\\n'
    '    --hash=sha256:a549b9c31bec33820e885335b451286e2969a2d9e24879f83fe904a5ce59d70a \\\n'
    '    --hash=sha256:ac07bad82163452a6884fe8fa0963fb98c2346ba78d779ec06bd7a6262132aee \\\n'
    '    --hash=sha256:ae2ad8ae6ebee9d2d94b17fb62763125f3f374c25618198f40cbb8b525411900 \\\n'
    '    --hash=sha256:b91c037585eba9095565a3556f611e3cbfaa42ca1e865f7b8015fe5c7336d5a5 \\\n'
    '    --hash=sha256:bc1667f8b83f48511b94671e0e441401371dfd0f0a795c7daa4a3cd1dde55bea \\\n'
    '    --hash=sha256:bec0a414d016ac1a18862a519e54b2fd0fc8bbfd6890376898a6c0891dd82e9f \\\n'
    '    --hash=sha256:bf50cd79a75d181c9181df03572cdce0fbb75cc353bc350712073108cba98de5 \\\n'
    '    --hash=sha256:bff1b4290a66b490a2f4719358c0cdcd9bafb6b8f061e45c7a2460866bf50c2e \\\n'
    '    --hash=sha256:c061bb86a71b42465156a3ee7bd58c8c2ceacdbeb95d05a99893e08b8467359a \\\n'
    '    --hash=sha256:c8b29db45f8fe46ad280a7294f5c3ec36dbac9491f2d1c17345be8e69cc5928f \\\n'
    '    --hash=sha256:ce409136744f6521e39fd8e2a24c53fa18ad67aa5bc7c2cf83645cce5b5c4e50 \\\n'
    '    --hash=sha256:d050b3361367a06d752db6ead6e7edeb0009be66bc3bae0ee9d97fb326badc2a \\\n'
    '    --hash=sha256:d283d37a890ba4c1ae73ffadf8046435c76e7bc2247bbb63c00bd1a709c6544b \\\n'
    '    --hash=sha256:d9fad5155d72433c921b782e58892377c44bd6252b5af2f67f16b194987338a4 \\\n'
    '    --hash=sha256:daa4ee5a243f0f20d528d939d06670a298dd39b1ad5f8a72a4275124a7819eff \\\n'
    '    --hash=sha256:db0b55e0f3cc0be60c1f19efdde9a637c32740486004f20d1cff53c3c0ece4d2 \\\n'
    '    --hash=sha256:e61659ba32cf2cf1481e575d0462554625196a1f2fc06a1c777d3f48e8865d46 \\\n'
    '    --hash=sha256:ea3d8a3d18833cf4304cd2fc9cbb1efe188ca9b5efef2bdac7adc20594a0e46b \\\n'
    '    --hash=sha256:ec6a563cff360b50eed26f13adc43e61bc0c04d94b8be985e6fb24b81f6dcfdf \\\n'
    '    --hash=sha256:f5dfb42c4604dddc8e4305050aa6deb084540643ed5804d7455b5df8fe16f5e5 \\\n'
    '    --hash=sha256:fa173ec60341d6bb97a89f5ea19c85c5643c1e7dedebc22f5181eb73573142c5 \\\n'
    '    --hash=sha256:fa9db3f79de01457b03d4f01b34cf91bc0048eb2c3846ff26f66687c2f6d16ab \\\n'
    '    --hash=sha256:fce659a462a1be54d2ffcacea5e3ba2d74daa74f30f5f143fe0c58636e355fdd \\\n'
    '    --hash=sha256:ffee1f21e5ef0d712f9033568f8344d5da8cc2869dbd08d87c84656e6a2d2f68\n'
    '    # via -r requirements.in\n'
    'pluggy==1.5.0 \\\n'
    '    --hash=sha256:2cffa88e94fdc978c4c574f15f9e59b7f4201d439195c3715ca9e2486f1d0cf1 \\\n'
    '    --hash=sha256:44e1ad92c8ca002de6377e165f3e0f1be63266ab4d554740532335b9d75ea669\n'
    '    # via -r requirements.in\n'
)
POETRY_LOCK = (
    '# This file is automatically @generated by Poetry 2.4.3 and should not be changed by hand.\n'
    '\n'
    '[[package]]\n'
    'name = "attrs"\n'
    'version = "24.2.0"\n'
    'description = "Classes Without Boilerplate"\n'
    'optional = false\n'
    'python-versions = ">=3.7"\n'
    'groups = ["main"]\n'
    'files = [\n'
    '    {file = "attrs-24.2.0-py3-none-any.whl", hash = "sha256:81921eb96de3191c8258c199618104dd27ac608d9366f5e35d011eae1867ede2"},\n'
    '    {file = "attrs-24.2.0.tar.gz", hash = "sha256:5cfb1b9148b5b086569baec03f20d7b6bf3bcacc9a42bebf87ffaaca362f6346"},\n'
    ']\n'
    '\n'
    '[package.extras]\n'
    'benchmark = ["cloudpickle ; platform_python_implementation == \\"CPython\\"", "hypothesis", "mypy (>=1.11.1) ; platform_python_implementation == \\"CPython\\" and python_version >= \\"3.9\\"", "pympler", "pytest (>=4.3.0)", "pytest-codspeed", "pytest-mypy-plugins ; platform_python_implementation == \\"CPython\\" and python_version >= \\"3.9\\" and python_version < \\"3.13\\"", "pytest-xdist[psutil]"]\n'
    'cov = ["cloudpickle ; platform_python_implementation == \\"CPython\\"", "coverage[toml] (>=5.3)", "hypothesis", "mypy (>=1.11.1) ; platform_python_implementation == \\"CPython\\" and python_version >= \\"3.9\\"", "pympler", "pytest (>=4.3.0)", "pytest-mypy-plugins ; platform_python_implementation == \\"CPython\\" and python_version >= \\"3.9\\" and python_version < \\"3.13\\"", "pytest-xdist[psutil]"]\n'
    'dev = ["cloudpickle ; platform_python_implementation == \\"CPython\\"", "hypothesis", "mypy (>=1.11.1) ; platform_python_implementation == \\"CPython\\" and python_version >= \\"3.9\\"", "pre-commit", "pympler", "pytest (>=4.3.0)", "pytest-mypy-plugins ; platform_python_implementation == \\"CPython\\" and python_version >= \\"3.9\\" and python_version < \\"3.13\\"", "pytest-xdist[psutil]"]\n'
    'docs = ["cogapp", "furo", "myst-parser", "sphinx", "sphinx-notfound-page", "sphinxcontrib-towncrier", "towncrier (<24.7)"]\n'
    'tests = ["cloudpickle ; platform_python_implementation == \\"CPython\\"", "hypothesis", "mypy (>=1.11.1) ; platform_python_implementation == \\"CPython\\" and python_version >= \\"3.9\\"", "pympler", "pytest (>=4.3.0)", "pytest-mypy-plugins ; platform_python_implementation == \\"CPython\\" and python_version >= \\"3.9\\" and python_version < \\"3.13\\"", "pytest-xdist[psutil]"]\n'
    'tests-mypy = ["mypy (>=1.11.1) ; platform_python_implementation == \\"CPython\\" and python_version >= \\"3.9\\"", "pytest-mypy-plugins ; platform_python_implementation == \\"CPython\\" and python_version >= \\"3.9\\" and python_version < \\"3.13\\""]\n'
    '\n'
    '[[package]]\n'
    'name = "click"\n'
    'version = "8.1.7"\n'
    'description = "Composable command line interface toolkit"\n'
    'optional = false\n'
    'python-versions = ">=3.7"\n'
    'groups = ["main"]\n'
    'files = [\n'
    '    {file = "click-8.1.7-py3-none-any.whl", hash = "sha256:ae74fb96c20a0277a1d615f1e4d73c8414f5a98db8b799a7931d1582f3390c28"},\n'
    '    {file = "click-8.1.7.tar.gz", hash = "sha256:ca9853ad459e787e2192211578cc907e7594e294c7ccc834310722b41b9ca6de"},\n'
    ']\n'
    '\n'
    '[package.dependencies]\n'
    'colorama = {version = "*", markers = "platform_system == \\"Windows\\""}\n'
    '\n'
    '[[package]]\n'
    'name = "colorama"\n'
    'version = "0.4.6"\n'
    'description = "Cross-platform colored terminal text."\n'
    'optional = false\n'
    'python-versions = "!=3.0.*,!=3.1.*,!=3.2.*,!=3.3.*,!=3.4.*,!=3.5.*,!=3.6.*,>=2.7"\n'
    'groups = ["main"]\n'
    'markers = "platform_system == \\"Windows\\""\n'
    'files = [\n'
    '    {file = "colorama-0.4.6-py2.py3-none-any.whl", hash = "sha256:4f1d9991f5acc0ca119f9d443620b77f9d6b33703e51011c16baf57afb285fc6"},\n'
    '    {file = "colorama-0.4.6.tar.gz", hash = "sha256:08695f5cb7ed6e0531a20572697297273c47b8cae5a63ffc6d6ed5c201be6e44"},\n'
    ']\n'
    '\n'
    '[[package]]\n'
    'name = "idna"\n'
    'version = "3.10"\n'
    'description = "Internationalized Domain Names in Applications (IDNA)"\n'
    'optional = false\n'
    'python-versions = ">=3.6"\n'
    'groups = ["main"]\n'
    'files = [\n'
    '    {file = "idna-3.10-py3-none-any.whl", hash = "sha256:946d195a0d259cbba61165e88e65941f16e9b36ea6ddb97f00452bae8b1287d3"},\n'
    '    {file = "idna-3.10.tar.gz", hash = "sha256:12f65c9b470abda6dc35cf8e63cc574b1c52b11df2c86030af0ac09b01b13ea9"},\n'
    ']\n'
    '\n'
    '[package.extras]\n'
    'all = ["flake8 (>=7.1.1)", "mypy (>=1.11.2)", "pytest (>=8.3.2)", "ruff (>=0.6.2)"]\n'
    '\n'
    '[[package]]\n'
    'name = "markupsafe"\n'
    'version = "2.1.5"\n'
    'description = "Safely add untrusted strings to HTML/XML markup."\n'
    'optional = false\n'
    'python-versions = ">=3.7"\n'
    'groups = ["main"]\n'
    'files = [\n'
    '    {file = "MarkupSafe-2.1.5-cp310-cp310-macosx_10_9_universal2.whl", hash = "sha256:a17a92de5231666cfbe003f0e4b9b3a7ae3afb1ec2845aadc2bacc93ff85febc"},\n'
    '    {file = "MarkupSafe-2.1.5-cp310-cp310-macosx_10_9_x86_64.whl", hash = "sha256:72b6be590cc35924b02c78ef34b467da4ba07e4e0f0454a2c5907f473fc50ce5"},\n'
    '    {file = "MarkupSafe-2.1.5-cp310-cp310-manylinux_2_17_aarch64.manylinux2014_aarch64.whl", hash = "sha256:e61659ba32cf2cf1481e575d0462554625196a1f2fc06a1c777d3f48e8865d46"},\n'
    '    {file = "MarkupSafe-2.1.5-cp310-cp310-manylinux_2_17_x86_64.manylinux2014_x86_64.whl", hash = "sha256:2174c595a0d73a3080ca3257b40096db99799265e1c27cc5a610743acd86d62f"},\n'
    '    {file = "MarkupSafe-2.1.5-cp310-cp310-manylinux_2_5_i686.manylinux1_i686.manylinux_2_17_i686.manylinux2014_i686.whl", hash = "sha256:ae2ad8ae6ebee9d2d94b17fb62763125f3f374c25618198f40cbb8b525411900"},\n'
    '    {file = "MarkupSafe-2.1.5-cp310-cp310-musllinux_1_1_aarch64.whl", hash = "sha256:075202fa5b72c86ad32dc7d0b56024ebdbcf2048c0ba09f1cde31bfdd57bcfff"},\n'
    '    {file = "MarkupSafe-2.1.5-cp310-cp310-musllinux_1_1_i686.whl", hash = "sha256:598e3276b64aff0e7b3451b72e94fa3c238d452e7ddcd893c3ab324717456bad"},\n'
    '    {file = "MarkupSafe-2.1.5-cp310-cp310-musllinux_1_1_x86_64.whl", hash = "sha256:fce659a462a1be54d2ffcacea5e3ba2d74daa74f30f5f143fe0c58636e355fdd"},\n'
    '    {file = "MarkupSafe-2.1.5-cp310-cp310-win32.whl", hash = "sha256:d9fad5155d72433c921b782e58892377c44bd6252b5af2f67f16b194987338a4"},\n'
    '    {file = "MarkupSafe-2.1.5-cp310-cp310-win_amd64.whl", hash = "sha256:bf50cd79a75d181c9181df03572cdce0fbb75cc353bc350712073108cba98de5"},\n'
    '    {file = "MarkupSafe-2.1.5-cp311-cp311-macosx_10_9_universal2.whl", hash = "sha256:629ddd2ca402ae6dbedfceeba9c46d5f7b2a61d9749597d4307f943ef198fc1f"},\n'
    '    {file = "MarkupSafe-2.1.5-cp311-cp311-macosx_10_9_x86_64.whl", hash = "sha256:5b7b716f97b52c5a14bffdf688f971b2d5ef4029127f1ad7a513973cfd818df2"},\n'
    '    {file = "MarkupSafe-2.1.5-cp311-cp311-manylinux_2_17_aarch64.manylinux2014_aarch64.whl", hash = "sha256:6ec585f69cec0aa07d945b20805be741395e28ac1627333b1c5b0105962ffced"},\n'
    '    {file = "MarkupSafe-2.1.5-cp311-cp311-manylinux_2_17_x86_64.manylinux2014_x86_64.whl", hash = "sha256:b91c037585eba9095565a3556f611e3cbfaa42ca1e865f7b8015fe5c7336d5a5"},\n'
    '    {file = "MarkupSafe-2.1.5-cp311-cp311-manylinux_2_5_i686.manylinux1_i686.manylinux_2_17_i686.manylinux2014_i686.whl", hash = "sha256:7502934a33b54030eaf1194c21c692a534196063db72176b0c4028e140f8f32c"},\n'
    '    {file = "MarkupSafe-2.1.5-cp311-cp311-musllinux_1_1_aarch64.whl", hash = "sha256:0e397ac966fdf721b2c528cf028494e86172b4feba51d65f81ffd65c63798f3f"},\n'
    '    {file = "MarkupSafe-2.1.5-cp311-cp311-musllinux_1_1_i686.whl", hash = "sha256:c061bb86a71b42465156a3ee7bd58c8c2ceacdbeb95d05a99893e08b8467359a"},\n'
    '    {file = "MarkupSafe-2.1.5-cp311-cp311-musllinux_1_1_x86_64.whl", hash = "sha256:3a57fdd7ce31c7ff06cdfbf31dafa96cc533c21e443d57f5b1ecc6cdc668ec7f"},\n'
    '    {file = "MarkupSafe-2.1.5-cp311-cp311-win32.whl", hash = "sha256:397081c1a0bfb5124355710fe79478cdbeb39626492b15d399526ae53422b906"},\n'
    '    {file = "MarkupSafe-2.1.5-cp311-cp311-win_amd64.whl", hash = "sha256:2b7c57a4dfc4f16f7142221afe5ba4e093e09e728ca65c51f5620c9aaeb9a617"},\n'
    '    {file = "MarkupSafe-2.1.5-cp312-cp312-macosx_10_9_universal2.whl", hash = "sha256:8dec4936e9c3100156f8a2dc89c4b88d5c435175ff03413b443469c7c8c5f4d1"},\n'
    '    {file = "MarkupSafe-2.1.5-cp312-cp312-macosx_10_9_x86_64.whl", hash = "sha256:3c6b973f22eb18a789b1460b4b91bf04ae3f0c4234a0a6aa6b0a92f6f7b951d4"},\n'
    '    {file = "MarkupSafe-2.1.5-cp312-cp312-manylinux_2_17_aarch64.manylinux2014_aarch64.whl", hash = "sha256:ac07bad82163452a6884fe8fa0963fb98c2346ba78d779ec06bd7a6262132aee"},\n'
    '    {file = "MarkupSafe-2.1.5-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl", hash = "sha256:f5dfb42c4604dddc8e4305050aa6deb084540643ed5804d7455b5df8fe16f5e5"},\n'
    '    {file = "MarkupSafe-2.1.5-cp312-cp312-manylinux_2_5_i686.manylinux1_i686.manylinux_2_17_i686.manylinux2014_i686.whl", hash = "sha256:ea3d8a3d18833cf4304cd2fc9cbb1efe188ca9b5efef2bdac7adc20594a0e46b"},\n'
    '    {file = "MarkupSafe-2.1.5-cp312-cp312-musllinux_1_1_aarch64.whl", hash = "sha256:d050b3361367a06d752db6ead6e7edeb0009be66bc3bae0ee9d97fb326badc2a"},\n'
    '    {file = "MarkupSafe-2.1.5-cp312-cp312-musllinux_1_1_i686.whl", hash = "sha256:bec0a414d016ac1a18862a519e54b2fd0fc8bbfd6890376898a6c0891dd82e9f"},\n'
    '    {file = "MarkupSafe-2.1.5-cp312-cp312-musllinux_1_1_x86_64.whl", hash = "sha256:58c98fee265677f63a4385256a6d7683ab1832f3ddd1e66fe948d5880c21a169"},\n'
    '    {file = "MarkupSafe-2.1.5-cp312-cp312-win32.whl", hash = "sha256:8590b4ae07a35970728874632fed7bd57b26b0102df2d2b233b6d9d82f6c62ad"},\n'
    '    {file = "MarkupSafe-2.1.5-cp312-cp312-win_amd64.whl", hash = "sha256:823b65d8706e32ad2df51ed89496147a42a2a6e01c13cfb6ffb8b1e92bc910bb"},\n'
    '    {file = "MarkupSafe-2.1.5-cp37-cp37m-macosx_10_9_x86_64.whl", hash = "sha256:c8b29db45f8fe46ad280a7294f5c3ec36dbac9491f2d1c17345be8e69cc5928f"},\n'
    '    {file = "MarkupSafe-2.1.5-cp37-cp37m-manylinux_2_17_aarch64.manylinux2014_aarch64.whl", hash = "sha256:ec6a563cff360b50eed26f13adc43e61bc0c04d94b8be985e6fb24b81f6dcfdf"},\n'
    '    {file = "MarkupSafe-2.1.5-cp37-cp37m-manylinux_2_17_x86_64.manylinux2014_x86_64.whl", hash = "sha256:a549b9c31bec33820e885335b451286e2969a2d9e24879f83fe904a5ce59d70a"},\n'
    '    {file = "MarkupSafe-2.1.5-cp37-cp37m-manylinux_2_5_i686.manylinux1_i686.manylinux_2_17_i686.manylinux2014_i686.whl", hash = "sha256:4f11aa001c540f62c6166c7726f71f7573b52c68c31f014c25cc7901deea0b52"},\n'
    '    {file = "MarkupSafe-2.1.5-cp37-cp37m-musllinux_1_1_aarch64.whl", hash = "sha256:7b2e5a267c855eea6b4283940daa6e88a285f5f2a67f2220203786dfa59b37e9"},\n'
    '    {file = "MarkupSafe-2.1.5-cp37-cp37m-musllinux_1_1_i686.whl", hash = "sha256:2d2d793e36e230fd32babe143b04cec8a8b3eb8a3122d2aceb4a371e6b09b8df"},\n'
    '    {file = "MarkupSafe-2.1.5-cp37-cp37m-musllinux_1_1_x86_64.whl", hash = "sha256:ce409136744f6521e39fd8e2a24c53fa18ad67aa5bc7c2cf83645cce5b5c4e50"},\n'
    '    {file = "MarkupSafe-2.1.5-cp37-cp37m-win32.whl", hash = "sha256:4096e9de5c6fdf43fb4f04c26fb114f61ef0bf2e5604b6ee3019d51b69e8c371"},\n'
    '    {file = "MarkupSafe-2.1.5-cp37-cp37m-win_amd64.whl", hash = "sha256:4275d846e41ecefa46e2015117a9f491e57a71ddd59bbead77e904dc02b1bed2"},\n'
    '    {file = "MarkupSafe-2.1.5-cp38-cp38-macosx_10_9_universal2.whl", hash = "sha256:656f7526c69fac7f600bd1f400991cc282b417d17539a1b228617081106feb4a"},\n'
    '    {file = "MarkupSafe-2.1.5-cp38-cp38-macosx_10_9_x86_64.whl", hash = "sha256:97cafb1f3cbcd3fd2b6fbfb99ae11cdb14deea0736fc2b0952ee177f2b813a46"},\n'
    '    {file = "MarkupSafe-2.1.5-cp38-cp38-manylinux_2_17_aarch64.manylinux2014_aarch64.whl", hash = "sha256:1f3fbcb7ef1f16e48246f704ab79d79da8a46891e2da03f8783a5b6fa41a9532"},\n'
    '    {file = "MarkupSafe-2.1.5-cp38-cp38-manylinux_2_17_x86_64.manylinux2014_x86_64.whl", hash = "sha256:fa9db3f79de01457b03d4f01b34cf91bc0048eb2c3846ff26f66687c2f6d16ab"},\n'
    '    {file = "MarkupSafe-2.1.5-cp38-cp38-manylinux_2_5_i686.manylinux1_i686.manylinux_2_17_i686.manylinux2014_i686.whl", hash = "sha256:ffee1f21e5ef0d712f9033568f8344d5da8cc2869dbd08d87c84656e6a2d2f68"},\n'
    '    {file = "MarkupSafe-2.1.5-cp38-cp38-musllinux_1_1_aarch64.whl", hash = "sha256:5dedb4db619ba5a2787a94d877bc8ffc0566f92a01c0ef214865e54ecc9ee5e0"},\n'
    '    {file = "MarkupSafe-2.1.5-cp38-cp38-musllinux_1_1_i686.whl", hash = "sha256:30b600cf0a7ac9234b2638fbc0fb6158ba5bdcdf46aeb631ead21248b9affbc4"},\n'
    '    {file = "MarkupSafe-2.1.5-cp38-cp38-musllinux_1_1_x86_64.whl", hash = "sha256:8dd717634f5a044f860435c1d8c16a270ddf0ef8588d4887037c5028b859b0c3"},\n'
    '    {file = "MarkupSafe-2.1.5-cp38-cp38-win32.whl", hash = "sha256:daa4ee5a243f0f20d528d939d06670a298dd39b1ad5f8a72a4275124a7819eff"},\n'
    '    {file = "MarkupSafe-2.1.5-cp38-cp38-win_amd64.whl", hash = "sha256:619bc166c4f2de5caa5a633b8b7326fbe98e0ccbfacabd87268a2b15ff73a029"},\n'
    '    {file = "MarkupSafe-2.1.5-cp39-cp39-macosx_10_9_universal2.whl", hash = "sha256:7a68b554d356a91cce1236aa7682dc01df0edba8d043fd1ce607c49dd3c1edcf"},\n'
    '    {file = "MarkupSafe-2.1.5-cp39-cp39-macosx_10_9_x86_64.whl", hash = "sha256:db0b55e0f3cc0be60c1f19efdde9a637c32740486004f20d1cff53c3c0ece4d2"},\n'
    '    {file = "MarkupSafe-2.1.5-cp39-cp39-manylinux_2_17_aarch64.manylinux2014_aarch64.whl", hash = "sha256:3e53af139f8579a6d5f7b76549125f0d94d7e630761a2111bc431fd820e163b8"},\n'
    '    {file = "MarkupSafe-2.1.5-cp39-cp39-manylinux_2_17_x86_64.manylinux2014_x86_64.whl", hash = "sha256:17b950fccb810b3293638215058e432159d2b71005c74371d784862b7e4683f3"},\n'
    '    {file = "MarkupSafe-2.1.5-cp39-cp39-manylinux_2_5_i686.manylinux1_i686.manylinux_2_17_i686.manylinux2014_i686.whl", hash = "sha256:4c31f53cdae6ecfa91a77820e8b151dba54ab528ba65dfd235c80b086d68a465"},\n'
    '    {file = "MarkupSafe-2.1.5-cp39-cp39-musllinux_1_1_aarch64.whl", hash = "sha256:bff1b4290a66b490a2f4719358c0cdcd9bafb6b8f061e45c7a2460866bf50c2e"},\n'
    '    {file = "MarkupSafe-2.1.5-cp39-cp39-musllinux_1_1_i686.whl", hash = "sha256:bc1667f8b83f48511b94671e0e441401371dfd0f0a795c7daa4a3cd1dde55bea"},\n'
    '    {file = "MarkupSafe-2.1.5-cp39-cp39-musllinux_1_1_x86_64.whl", hash = "sha256:5049256f536511ee3f7e1b3f87d1d1209d327e818e6ae1365e8653d7e3abb6a6"},\n'
    '    {file = "MarkupSafe-2.1.5-cp39-cp39-win32.whl", hash = "sha256:00e046b6dd71aa03a41079792f8473dc494d564611a8f89bbbd7cb93295ebdcf"},\n'
    '    {file = "MarkupSafe-2.1.5-cp39-cp39-win_amd64.whl", hash = "sha256:fa173ec60341d6bb97a89f5ea19c85c5643c1e7dedebc22f5181eb73573142c5"},\n'
    '    {file = "MarkupSafe-2.1.5.tar.gz", hash = "sha256:d283d37a890ba4c1ae73ffadf8046435c76e7bc2247bbb63c00bd1a709c6544b"},\n'
    ']\n'
    '\n'
    '[metadata]\n'
    'lock-version = "2.1"\n'
    'python-versions = "^3.10"\n'
    'content-hash = "eb136f9c3392bb6076a58c96351586eac98dd64f79283ccae378273a7cb9ef19"\n'
)
PIPFILE_LOCK = (
    '{\n'
    '    "_meta": {\n'
    '        "hash": {\n'
    '            "sha256": "36a0fba6ae3391f7f55aaef65ba532c10f7848538355711fe2043d707d5eba0f"\n'
    '        },\n'
    '        "pipfile-spec": 6,\n'
    '        "requires": {\n'
    '            "python_version": "3.13"\n'
    '        },\n'
    '        "sources": [\n'
    '            {\n'
    '                "name": "pypi",\n'
    '                "url": "https://pypi.org/simple",\n'
    '                "verify_ssl": true\n'
    '            }\n'
    '        ]\n'
    '    },\n'
    '    "default": {\n'
    '        "attrs": {\n'
    '            "hashes": [\n'
    '                "sha256:5cfb1b9148b5b086569baec03f20d7b6bf3bcacc9a42bebf87ffaaca362f6346",\n'
    '                "sha256:81921eb96de3191c8258c199618104dd27ac608d9366f5e35d011eae1867ede2"\n'
    '            ],\n'
    '            "index": "pypi",\n'
    '            "markers": "python_version >= \'3.7\'",\n'
    '            "version": "==24.2.0"\n'
    '        },\n'
    '        "idna": {\n'
    '            "hashes": [\n'
    '                "sha256:12f65c9b470abda6dc35cf8e63cc574b1c52b11df2c86030af0ac09b01b13ea9",\n'
    '                "sha256:946d195a0d259cbba61165e88e65941f16e9b36ea6ddb97f00452bae8b1287d3"\n'
    '            ],\n'
    '            "index": "pypi",\n'
    '            "markers": "python_version >= \'3.6\'",\n'
    '            "version": "==3.10"\n'
    '        },\n'
    '        "markupsafe": {\n'
    '            "hashes": [\n'
    '                "sha256:00e046b6dd71aa03a41079792f8473dc494d564611a8f89bbbd7cb93295ebdcf",\n'
    '                "sha256:075202fa5b72c86ad32dc7d0b56024ebdbcf2048c0ba09f1cde31bfdd57bcfff",\n'
    '                "sha256:0e397ac966fdf721b2c528cf028494e86172b4feba51d65f81ffd65c63798f3f",\n'
    '                "sha256:17b950fccb810b3293638215058e432159d2b71005c74371d784862b7e4683f3",\n'
    '                "sha256:1f3fbcb7ef1f16e48246f704ab79d79da8a46891e2da03f8783a5b6fa41a9532",\n'
    '                "sha256:2174c595a0d73a3080ca3257b40096db99799265e1c27cc5a610743acd86d62f",\n'
    '                "sha256:2b7c57a4dfc4f16f7142221afe5ba4e093e09e728ca65c51f5620c9aaeb9a617",\n'
    '                "sha256:2d2d793e36e230fd32babe143b04cec8a8b3eb8a3122d2aceb4a371e6b09b8df",\n'
    '                "sha256:30b600cf0a7ac9234b2638fbc0fb6158ba5bdcdf46aeb631ead21248b9affbc4",\n'
    '                "sha256:397081c1a0bfb5124355710fe79478cdbeb39626492b15d399526ae53422b906",\n'
    '                "sha256:3a57fdd7ce31c7ff06cdfbf31dafa96cc533c21e443d57f5b1ecc6cdc668ec7f",\n'
    '                "sha256:3c6b973f22eb18a789b1460b4b91bf04ae3f0c4234a0a6aa6b0a92f6f7b951d4",\n'
    '                "sha256:3e53af139f8579a6d5f7b76549125f0d94d7e630761a2111bc431fd820e163b8",\n'
    '                "sha256:4096e9de5c6fdf43fb4f04c26fb114f61ef0bf2e5604b6ee3019d51b69e8c371",\n'
    '                "sha256:4275d846e41ecefa46e2015117a9f491e57a71ddd59bbead77e904dc02b1bed2",\n'
    '                "sha256:4c31f53cdae6ecfa91a77820e8b151dba54ab528ba65dfd235c80b086d68a465",\n'
    '                "sha256:4f11aa001c540f62c6166c7726f71f7573b52c68c31f014c25cc7901deea0b52",\n'
    '                "sha256:5049256f536511ee3f7e1b3f87d1d1209d327e818e6ae1365e8653d7e3abb6a6",\n'
    '                "sha256:58c98fee265677f63a4385256a6d7683ab1832f3ddd1e66fe948d5880c21a169",\n'
    '                "sha256:598e3276b64aff0e7b3451b72e94fa3c238d452e7ddcd893c3ab324717456bad",\n'
    '                "sha256:5b7b716f97b52c5a14bffdf688f971b2d5ef4029127f1ad7a513973cfd818df2",\n'
    '                "sha256:5dedb4db619ba5a2787a94d877bc8ffc0566f92a01c0ef214865e54ecc9ee5e0",\n'
    '                "sha256:619bc166c4f2de5caa5a633b8b7326fbe98e0ccbfacabd87268a2b15ff73a029",\n'
    '                "sha256:629ddd2ca402ae6dbedfceeba9c46d5f7b2a61d9749597d4307f943ef198fc1f",\n'
    '                "sha256:656f7526c69fac7f600bd1f400991cc282b417d17539a1b228617081106feb4a",\n'
    '                "sha256:6ec585f69cec0aa07d945b20805be741395e28ac1627333b1c5b0105962ffced",\n'
    '                "sha256:72b6be590cc35924b02c78ef34b467da4ba07e4e0f0454a2c5907f473fc50ce5",\n'
    '                "sha256:7502934a33b54030eaf1194c21c692a534196063db72176b0c4028e140f8f32c",\n'
    '                "sha256:7a68b554d356a91cce1236aa7682dc01df0edba8d043fd1ce607c49dd3c1edcf",\n'
    '                "sha256:7b2e5a267c855eea6b4283940daa6e88a285f5f2a67f2220203786dfa59b37e9",\n'
    '                "sha256:823b65d8706e32ad2df51ed89496147a42a2a6e01c13cfb6ffb8b1e92bc910bb",\n'
    '                "sha256:8590b4ae07a35970728874632fed7bd57b26b0102df2d2b233b6d9d82f6c62ad",\n'
    '                "sha256:8dd717634f5a044f860435c1d8c16a270ddf0ef8588d4887037c5028b859b0c3",\n'
    '                "sha256:8dec4936e9c3100156f8a2dc89c4b88d5c435175ff03413b443469c7c8c5f4d1",\n'
    '                "sha256:97cafb1f3cbcd3fd2b6fbfb99ae11cdb14deea0736fc2b0952ee177f2b813a46",\n'
    '                "sha256:a17a92de5231666cfbe003f0e4b9b3a7ae3afb1ec2845aadc2bacc93ff85febc",\n'
    '                "sha256:a549b9c31bec33820e885335b451286e2969a2d9e24879f83fe904a5ce59d70a",\n'
    '                "sha256:ac07bad82163452a6884fe8fa0963fb98c2346ba78d779ec06bd7a6262132aee",\n'
    '                "sha256:ae2ad8ae6ebee9d2d94b17fb62763125f3f374c25618198f40cbb8b525411900",\n'
    '                "sha256:b91c037585eba9095565a3556f611e3cbfaa42ca1e865f7b8015fe5c7336d5a5",\n'
    '                "sha256:bc1667f8b83f48511b94671e0e441401371dfd0f0a795c7daa4a3cd1dde55bea",\n'
    '                "sha256:bec0a414d016ac1a18862a519e54b2fd0fc8bbfd6890376898a6c0891dd82e9f",\n'
    '                "sha256:bf50cd79a75d181c9181df03572cdce0fbb75cc353bc350712073108cba98de5",\n'
    '                "sha256:bff1b4290a66b490a2f4719358c0cdcd9bafb6b8f061e45c7a2460866bf50c2e",\n'
    '                "sha256:c061bb86a71b42465156a3ee7bd58c8c2ceacdbeb95d05a99893e08b8467359a",\n'
    '                "sha256:c8b29db45f8fe46ad280a7294f5c3ec36dbac9491f2d1c17345be8e69cc5928f",\n'
    '                "sha256:ce409136744f6521e39fd8e2a24c53fa18ad67aa5bc7c2cf83645cce5b5c4e50",\n'
    '                "sha256:d050b3361367a06d752db6ead6e7edeb0009be66bc3bae0ee9d97fb326badc2a",\n'
    '                "sha256:d283d37a890ba4c1ae73ffadf8046435c76e7bc2247bbb63c00bd1a709c6544b",\n'
    '                "sha256:d9fad5155d72433c921b782e58892377c44bd6252b5af2f67f16b194987338a4",\n'
    '                "sha256:daa4ee5a243f0f20d528d939d06670a298dd39b1ad5f8a72a4275124a7819eff",\n'
    '                "sha256:db0b55e0f3cc0be60c1f19efdde9a637c32740486004f20d1cff53c3c0ece4d2",\n'
    '                "sha256:e61659ba32cf2cf1481e575d0462554625196a1f2fc06a1c777d3f48e8865d46",\n'
    '                "sha256:ea3d8a3d18833cf4304cd2fc9cbb1efe188ca9b5efef2bdac7adc20594a0e46b",\n'
    '                "sha256:ec6a563cff360b50eed26f13adc43e61bc0c04d94b8be985e6fb24b81f6dcfdf",\n'
    '                "sha256:f5dfb42c4604dddc8e4305050aa6deb084540643ed5804d7455b5df8fe16f5e5",\n'
    '                "sha256:fa173ec60341d6bb97a89f5ea19c85c5643c1e7dedebc22f5181eb73573142c5",\n'
    '                "sha256:fa9db3f79de01457b03d4f01b34cf91bc0048eb2c3846ff26f66687c2f6d16ab",\n'
    '                "sha256:fce659a462a1be54d2ffcacea5e3ba2d74daa74f30f5f143fe0c58636e355fdd",\n'
    '                "sha256:ffee1f21e5ef0d712f9033568f8344d5da8cc2869dbd08d87c84656e6a2d2f68"\n'
    '            ],\n'
    '            "index": "pypi",\n'
    '            "markers": "python_version >= \'3.7\'",\n'
    '            "version": "==2.1.5"\n'
    '        }\n'
    '    },\n'
    '    "develop": {\n'
    '        "pluggy": {\n'
    '            "hashes": [\n'
    '                "sha256:2cffa88e94fdc978c4c574f15f9e59b7f4201d439195c3715ca9e2486f1d0cf1",\n'
    '                "sha256:44e1ad92c8ca002de6377e165f3e0f1be63266ab4d554740532335b9d75ea669"\n'
    '            ],\n'
    '            "index": "pypi",\n'
    '            "markers": "python_version >= \'3.8\'",\n'
    '            "version": "==1.5.0"\n'
    '        }\n'
    '    }\n'
    '}\n'
)

PIP_COMPILE_MARKERS = (  # reconstructed from memory: pip-compile --generate-hashes output with a marker and a multi-line 'via'
    "#\n# This file is autogenerated by pip-compile with Python 3.12\n# by the following command:\n#\n#    pip-compile --generate-hashes requirements.in\n#\n"
    "attrs==24.2.0 \\\n    --hash=sha256:5cfb1b9148b5b086569baec03f20d7b6bf3bcacc9a42bebf87ffaaca362f6346 \\\n"
    "    --hash=sha256:81921eb96de3191c8258c199618104dd27ac608d9366f5e35d011eae1867ede2\n    # via\n    #   -r requirements.in\n    #   pytest\n"
    "colorama==0.4.6 ; sys_platform == \"win32\" \\\n    --hash=sha256:08695f5cb7ed6e0531a20572697297273c47b8cae5a63ffc6d6ed5c201be6e44 \\\n"
    "    --hash=sha256:4f1d9991f5acc0ca119f9d443620b77f9d971b7a1a2f2bb2d4f2d0a2e0a8f5d6\n    # via pytest\n"
    "pytest[testing]==8.3.3 \\\n    --hash=sha256:a6853c7375b2663155079443d2e45de913a911a11d669810f3109c4b5b8fd3d4\n    # via -r requirements.in\n"
)

_REQ_PIN = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*(?:\[[^\]\s]*\])?)==([^\s;\\]+)(?: *; *([^\\]*?))? *(\\?)")
_REQ_HASH = re.compile(r"\s+--hash=\S+(?: *\\)?")


def _fmt(name, ver, marker, group=None):
    return f"{name}=={ver}" + (f" ; {marker}" if marker else "")


def lock_pins(text: str) -> str:
    """Line scanners for the three formats; anything unrecognised comes back unchanged."""
    lines, nl = lines_nl(text)
    if text.startswith("{") and '"_meta"' in text and '"default"' in text:
        return shrink_only(text, _pipfile_lines(lines, nl))
    if "[[package]]" in text and re.search(r'(?m)^name = "', text):
        return shrink_only(text, _poetry_lines(lines, nl))
    out, in_pkg, after_pin = [], False, False
    for l in lines:
        if m := _REQ_PIN.fullmatch(l):
            out.append(_fmt(m.group(1), m.group(2), m.group(3)))
            in_pkg, after_pin = bool(m.group(4)), True
        elif in_pkg and _REQ_HASH.fullmatch(l):
            in_pkg = l.rstrip().endswith("\\")
        elif after_pin and re.fullmatch(r"\s+#.*", l):
            continue  # '# via ...' and its '#   name' continuation lines
        else:
            out.append(l)
            in_pkg = after_pin = False
    return shrink_only(text, join_nl(out, nl))


def _poetry_lines(lines, nl):
    """A package is the first name/version line of the table body between '[[package]]' and the next '[' header."""
    out, k = [], 0
    while k < len(lines):
        if lines[k] == "[[package]]":
            k += 1
            name = ver = None
            while k < len(lines) and not lines[k].startswith("["):
                if name is None and (m := re.fullmatch(r'name = "([^"]*)"', lines[k])):
                    name = m.group(1)
                elif ver is None and (m := re.fullmatch(r'version = "([^"]*)"', lines[k])):
                    ver = m.group(1)
                k += 1
            if name is not None and ver is not None:
                out.append(f"{name}=={ver}")
        else:
            k += 1
    return join_nl(out, nl) if out else join_nl(lines, nl)


def _close(lines, start, end, prefix):
    """Index of the first line in (start, end) that begins with prefix + '}' (the closing brace of a JSON object)."""
    return next((j for j in range(start + 1, end) if lines[j].startswith(prefix + "}")), None)


def _pipfile_lines(lines, nl):
    out, k = [], 0
    while k < len(lines):
        m = re.fullmatch(r'    "(default|develop)": \{', lines[k])
        end = _close(lines, k, len(lines), "    ") if m else None
        if end is None:
            k += 1
            continue
        out.append(f"# {m.group(1)}")
        j = k + 1
        while j < end:
            pm = re.fullmatch(r'        "([^"]+)": \{', lines[j])
            pend = _close(lines, j, end, "        ") if pm else None
            if pend is None:
                j += 1
                continue
            ver = marker = None
            for body in lines[j + 1:pend]:
                if ver is None and (v := re.fullmatch(r'            "version": "==([^"]*)",?', body)):
                    ver = v.group(1)
                elif marker is None and (v := re.fullmatch(r'            "markers": "([^"]*)",?', body)):
                    marker = v.group(1)
            if ver is not None:
                out.append(_fmt(pm.group(1), ver, marker))
            j = pend + 1
        k = end + 1
    return join_nl(out, nl) if out else join_nl(lines, nl)


def probes_609(text: str) -> dict:
    """Reads the original independently: logical lines for requirements, block regexes for the two lockfiles."""
    pins = []
    if text.startswith("{") and '"_meta"' in text and '"default"' in text:
        for sec in re.finditer(r'(?ms)^    "(default|develop)": \{\n(.*?)^    \}', text):
            for pk in re.finditer(r'(?ms)^        "([^"]+)": \{\n(.*?)^        \}', sec.group(2)):
                v = re.search(r'(?m)^            "version": "==([^"]*)",?$', pk.group(2))
                mk = re.search(r'(?m)^            "markers": "([^"]*)",?$', pk.group(2))
                if v:
                    pins.append((sec.group(1), pk.group(1), v.group(1), mk.group(1) if mk else None))
    elif "[[package]]" in text and re.search(r'(?m)^name = "', text):
        for block in re.split(r"(?m)^\[\[package\]\]$", text)[1:]:
            head = re.split(r"(?m)^\[", block)[0]
            n = re.search(r'(?m)^name = "([^"]*)"$', head)
            v = re.search(r'(?m)^version = "([^"]*)"$', head)
            if n and v:
                pins.append((None, n.group(1), v.group(1), None))
    else:
        group = None
        joined = re.sub(r"\\\n[ \t]+(?=--)", " ", text)  # fold continuation lines that carry options, then read logical lines
        for l in joined.split("\n"):
            if m := re.fullmatch(r"# (default|develop)", l):
                group = m.group(1)
            elif m := re.match(r"([A-Za-z0-9][A-Za-z0-9._-]*(?:\[[^\]\s]*\])?)==([^\s;\\]+)(?: *; *([^\\]*?))?(?= +(?:--hash=|\\)|\s*$)", l):
                pins.append((group, m.group(1), m.group(2), m.group(3) or None))
    return {"name==version pins (section, name, version, marker)": pins, "pin count": len(pins),
            "distinct names": sorted({p[1] for p in pins})}


def _fixtures_609():
    # Prove the probe's readers against real parsers on the real captures (a failure here fails the case).
    toml_pins = sorted((p["name"], p["version"]) for p in tomllib.loads(POETRY_LOCK)["package"])
    assert toml_pins == sorted((n, v) for _, n, v, _ in probes_609(POETRY_LOCK)["name==version pins (section, name, version, marker)"]), "probe disagrees with tomllib"
    pf = json.loads(PIPFILE_LOCK)
    want = sorted((s, n, d["version"][2:], d.get("markers")) for s in ("default", "develop") for n, d in pf[s].items())
    assert want == sorted(probes_609(PIPFILE_LOCK)["name==version pins (section, name, version, marker)"]), "probe disagrees with json"
    return [
        Fixture("uv pip compile --generate-hashes + '# via' (real)", PIP_COMPILE_HASHES, True),
        Fixture("poetry.lock with files/hash per wheel, extras and metadata (real)", POETRY_LOCK, True),
        Fixture("Pipfile.lock with default and develop sections (real)", PIPFILE_LOCK, True),
        Fixture("pip-compile with an environment marker, extras and a multi-line via (reconstructed from memory)", PIP_COMPILE_MARKERS, True),
        Fixture("pip freeze (nothing to collapse)", "attrs==24.2.0\nblinker==1.8.2\nidna==3.10\nmarkupsafe==2.1.5\n", False),
        Fixture("requirements.in", "requests>=2.31\nflask~=3.0  # web\n-r base.txt\n", False),
        Fixture("a hash line with no pin above it, and a pin with trailing prose", "    --hash=sha256:abcd\nsomething==1 trailing words\n", None),
    ]


case(
    609,
    claim="requirements.txt with --hash and '# via' lines, poetry.lock and Pipfile.lock collapse to one 'name==version' line per package (environment markers and the Pipfile default/develop section kept); the set of pins equals what tomllib/json read from the original.",
    loses="every hash and wheel file name, who requires each package ('# via'), poetry's dependency graph, extras, python-versions, groups, optional flags and content-hash, Pipfile index and the _meta block, and the original file order of unrecognised comment lines is kept but their relation to a pin is not",
    encode=lock_pins,
    probes=probes_609,
    fixtures=_fixtures_609,
    min_gain=0.5,
)



# ===============================================================================================================
# #701 Ninja and Bazel failures: after each 'FAILED:' the full compiler command repeats the same flag list. Hoist
# the flags shared by every failed command into one '[ctxprune: @1 = ...]' line, drop dependency-tracking flags that
# the FAILED target already names, print 'compiler @1 <rest>'. RULE: the probe expands @N and checks, per failed
# target, the compiler, the source and the multiset of compilation flags, plus every diagnostic.

NINJA_FAILED_BUILD = (
    "ninja: Entering directory `build'\n"
    '[1/6] Building CXX object CMakeFiles/app.dir/src/ui.cpp.o\n'
    'FAILED: CMakeFiles/app.dir/src/ui.cpp.o \n'
    '/usr/bin/c++ -DAPP_VERSION=\\"1.4.2\\" -DENABLE_TELEMETRY=0 -DUSE_FAST_PATH=1 -D_GNU_SOURCE -I/work/app/include -I/work/app/third_party/fmt/include -I/work/app/third_party/json/include -std=gnu++17 -Wall -Wextra -Wpedantic -fno-omit-frame-pointer -O2 -g -fno-rtti -Wshadow -MD -MT CMakeFiles/app.dir/src/ui.cpp.o -MF CMakeFiles/app.dir/src/ui.cpp.o.d -o CMakeFiles/app.dir/src/ui.cpp.o -c /work/app/src/ui.cpp\n'
    "/work/app/src/ui.cpp: In function 'void draw()':\n"
    "/work/app/src/ui.cpp:2:25: error: expected ',' or ';' before '}' token\n"
    '    2 | void draw() { int x = 1 }\n'
    '      |                         ^\n'
    "/work/app/src/ui.cpp:2:19: warning: unused variable 'x' [-Wunused-variable]\n"
    '    2 | void draw() { int x = 1 }\n'
    '      |                   ^\n'
    '[2/6] Building CXX object CMakeFiles/app.dir/src/store.cpp.o\n'
    '[3/6] Building CXX object CMakeFiles/app.dir/src/main.cpp.o\n'
    '[4/6] Building CXX object CMakeFiles/app.dir/src/net.cpp.o\n'
    'FAILED: CMakeFiles/app.dir/src/net.cpp.o \n'
    '/usr/bin/c++ -DAPP_VERSION=\\"1.4.2\\" -DENABLE_TELEMETRY=0 -DUSE_FAST_PATH=1 -D_GNU_SOURCE -DNET_RETRIES=3 -I/work/app/include -I/work/app/third_party/fmt/include -I/work/app/third_party/json/include -std=gnu++17 -Wall -Wextra -Wpedantic -fno-omit-frame-pointer -O2 -g -MD -MT CMakeFiles/app.dir/src/net.cpp.o -MF CMakeFiles/app.dir/src/net.cpp.o.d -o CMakeFiles/app.dir/src/net.cpp.o -c /work/app/src/net.cpp\n'
    "/work/app/src/net.cpp: In function 'int connect_to(const std::string&)':\n"
    "/work/app/src/net.cpp:3:12: error: 'hst' was not declared in this scope; did you mean 'host'?\n"
    '    3 |     return hst.size();\n'
    '      |            ^~~\n'
    '      |            host\n'
    "/work/app/src/net.cpp:2:35: warning: unused parameter 'host' [-Wunused-parameter]\n"
    '    2 | int connect_to(const std::string& host) {\n'
    '      |                ~~~~~~~~~~~~~~~~~~~^~~~\n'
    '[5/6] Building CXX object CMakeFiles/app.dir/src/parse.cpp.o\n'
    'FAILED: CMakeFiles/app.dir/src/parse.cpp.o \n'
    '/usr/bin/c++ -DAPP_VERSION=\\"1.4.2\\" -DENABLE_TELEMETRY=0 -DUSE_FAST_PATH=1 -D_GNU_SOURCE -I/work/app/include -I/work/app/third_party/fmt/include -I/work/app/third_party/json/include -std=gnu++17 -Wall -Wextra -Wpedantic -fno-omit-frame-pointer -O2 -g -fno-rtti -Wshadow -MD -MT CMakeFiles/app.dir/src/parse.cpp.o -MF CMakeFiles/app.dir/src/parse.cpp.o.d -o CMakeFiles/app.dir/src/parse.cpp.o -c /work/app/src/parse.cpp\n'
    "/work/app/src/parse.cpp: In function 'int parse_int(const std::string&)':\n"
    "/work/app/src/parse.cpp:2:61: error: 'missing_helper' was not declared in this scope\n"
    '    2 | int parse_int(const std::string& s) { return std::stoi(s) + missing_helper(s); }\n'
    '      |                                                             ^~~~~~~~~~~~~~\n'
    'ninja: build stopped: cannot make progress due to previous errors.\n'
)

def _bazel_failure(src: str, diag: str, echo: str) -> str:
    obj = "bazel-out/k8-fastbuild/bin/src/_objs/app/" + src.split("/")[-1].replace(".cc", ".pic")
    return (f"ERROR: /work/app/src/BUILD:5:11: Compiling {src} failed: (Exit 1): gcc failed: error executing CppCompile command (from target //src:app) "
            f"/usr/bin/gcc -U_FORTIFY_SOURCE -fstack-protector -Wall -Wunused-but-set-parameter -Wno-free-nonheap-object -fno-omit-frame-pointer -MD -MF {obj}.d "
            f"'-frandom-seed={obj}.o' -fPIC -iquote . -iquote bazel-out/k8-fastbuild/bin -iquote external/bazel_tools -iquote bazel-out/k8-fastbuild/bin/external/bazel_tools "
            f"-std=c++17 -c {src} -o {obj}.o\n{src}:{diag}\n{echo}"
            "Use --sandbox_debug to see verbose messages from the sandbox and retain the sandbox build root for debugging\n")


BAZEL_FAILED_BUILD = (  # reconstructed from memory: bazel build //src:app with four CppCompile failures (Bazel 7 format)
    "INFO: Analyzed target //src:app (14 packages loaded, 212 targets configured).\nINFO: Found 1 target...\n"
    + _bazel_failure("src/net.cc", "3:12: error: 'hst' was not declared in this scope; did you mean 'host'?", "    3 |     return hst.size();\n      |            ^~~\n      |            host\n")
    + _bazel_failure("src/ui.cc", "2:25: error: expected ',' or ';' before '}' token", "    2 | void draw() { int x = 1 }\n      |                         ^\n")
    + _bazel_failure("src/parse.cc", "2:61: error: 'missing_helper' was not declared in this scope", "    2 | int parse_int(const std::string& s) { return std::stoi(s) + missing_helper(s); }\n      |                                                             ^~~~~~~~~~~~~~\n")
    + _bazel_failure("src/store.cc", "7:5: error: 'Rec' does not name a type", "    7 |     Rec r;\n      |     ^~~\n")
    + "INFO: Elapsed time: 2.114s, Critical Path: 0.41s\nINFO: 5 processes: 5 internal.\nFAILED: Build did NOT complete successfully\n"
)

_ARG_FLAGS = {"-o", "-MT", "-MF", "-MQ", "-c", "-include", "-isystem", "-iquote", "-I", "-D", "-U", "-x", "-L", "-l", "-imacros", "-idirafter", "-isysroot", "--sysroot", "-Xclang", "-Xlinker", "-target"}
_TOKEN = re.compile(r"""(?:[^\s"'\\]|\\.|"(?:\\.|[^"\\])*"|'[^']*')+""")
_NOISE_FLAGS = {"-MD", "-MMD"}
_NOISE_PAIR = {"-MT", "-MF", "-MQ"}
_LEGEND = re.compile(r"^\[ctxprune: @(\d+) = (.*)\]$")
_BAZEL = re.compile(r"^(ERROR: .*? error executing \w+ command \(from target \S+\)) (\S.*)$")


def _tokens(s: str):
    toks = _TOKEN.findall(s)
    return toks if " ".join(toks) == re.sub(r"\s+", " ", s.strip()) else None


def _units(toks):
    out, i = [], 0
    while i < len(toks):
        if toks[i] in _ARG_FLAGS and i + 1 < len(toks):
            out.append(toks[i] + " " + toks[i + 1])
            i += 2
        else:
            out.append(toks[i])
            i += 1
    return out


def _eligible(toks):
    return bool(toks) and len(toks) >= 6 and toks[0] not in (":", "cd") and not any(t in ("&&", "||", ";", "|", ">", "2>&1") for t in toks) and "-c" in toks


def _find_commands(lines):
    """[(line index, kind, lead, tokens, target)] for ninja (line after 'FAILED:') and bazel ERROR lines."""
    found = []
    for i, l in enumerate(lines):
        if l.startswith("FAILED: ") and i + 1 < len(lines):
            toks = _tokens(lines[i + 1])
            if toks and _eligible(toks):
                found.append((i + 1, "ninja", "", toks, l[len("FAILED: "):].strip()))
        elif (m := _BAZEL.match(l)) and (toks := _tokens(m.group(2))) and _eligible(toks):
            found.append((i, "bazel", m.group(1) + " ", toks, None))
    return found


def hoist_flags(text: str) -> str:
    lines = text.split("\n")
    cmds = _find_commands(lines)
    legends, repl, nid = {}, {}, 0
    for kind in ("ninja", "bazel"):
        grp = [c for c in cmds if c[1] == kind and not (len(c[3]) > 1 and c[3][1].startswith("@"))]  # a command already using @N is not hoisted again
        if len(grp) < 2 or len({c[3][0] for c in grp}) != 1:
            continue
        ulists = [_units(c[3][1:]) for c in grp]
        common = Counter(ulists[0])
        for u in ulists[1:]:
            common &= Counter(u)
        shared = []
        left = Counter(common)
        for u in ulists[0]:
            if left[u] > 0:
                shared.append(u)
                left[u] -= 1
        shared = [u for u in shared if u not in _NOISE_FLAGS and u.split(" ")[0] not in _NOISE_PAIR]
        if len(shared) < 3:
            continue
        nid += 1
        legends[grp[0][0] - (1 if kind == "ninja" else 0)] = f"[ctxprune: @{nid} = {' '.join(shared)}]"
        for c, units in zip(grp, ulists):
            rest, left = [], Counter(shared)
            for u in units:
                if left[u] > 0:
                    left[u] -= 1
                elif u in _NOISE_FLAGS or (c[1] == "ninja" and u.split(" ")[0] in _NOISE_PAIR):
                    continue
                elif c[1] == "ninja" and u.startswith("-o ") and u[3:] == c[4]:
                    continue
                else:
                    rest.append(u)
            repl[c[0]] = c[2] + " ".join([c[3][0], f"@{nid}"] + rest)
    out = []
    for i, l in enumerate(lines):
        for at, leg in legends.items():
            if at == i:
                out.append(leg)
        out.append(repl.get(i, l))
    return shrink_only(text, "\n".join(out))


def probes_701(text: str) -> dict:
    lines = text.split("\n")
    legend = {}
    for l in lines:
        if m := _LEGEND.match(l):
            legend[m.group(1)] = _units(_tokens(m.group(2)) or [])

    def has_ref(toks):
        return any(t.startswith("@") and t[1:] in legend for t in toks[1:2])

    def canon(toks, target):
        units = []
        for u in _units(toks[1:]):
            units += legend[u[1:]] if u.startswith("@") and u[1:] in legend else [u]
        keep = [u for u in units if u not in _NOISE_FLAGS and u.split(" ")[0] not in _NOISE_PAIR and not (target and u == f"-o {target}")]
        return (toks[0], tuple(sorted(keep)))

    failed, commands, diags, other = [], [], [], []
    for i, l in enumerate(lines):
        if l.startswith("FAILED: "):
            failed.append(l[len("FAILED: "):].strip())
            t = _tokens(lines[i + 1]) if i + 1 < len(lines) else None
            if t and (_eligible(t) or has_ref(t)):
                commands.append((failed[-1], canon(t, failed[-1])))
        elif m := _BAZEL.match(l):
            t = _tokens(m.group(2))
            if t and (_eligible(t) or has_ref(t)):
                commands.append((m.group(1), canon(t, None)))
        if re.match(r"^\S[^:]*:\d+:\d+: (?:fatal error|error|warning|note): ", l):
            diags.append(l)
        elif re.match(r"^(\[\d+/\d+\] |ninja: |INFO: |FAILED: Build|Use --sandbox)", l):
            other.append(l)
    return {"failed targets": failed, "per failed command: target, compiler, sorted compilation flags": sorted(commands),
            "diagnostic lines": diags, "ninja/bazel progress and status lines": other}


COMPOSITE_701 = cat("-- Configuring done\n-- Build files have been written to: /work/app/build\n", NINJA_FAILED_BUILD)

case(
    701,
    claim="Flags shared by every failed compiler command are printed once as '[ctxprune: @1 = ...]', each command becomes 'compiler @1 <its own flags>' without the -MD/-MT/-MF/-o dependency flags the FAILED target already names; per failed target the compiler, source and multiset of compilation flags, every diagnostic and every ninja/bazel status line are unchanged.",
    loses="the original order of flags (shared flags move to the front), the -MD -MT -MF and -o arguments of ninja compile commands (the FAILED line names the object; the saved original keeps the exact rerun command), and whitespace inside commands",
    encode=hoist_flags,
    probes=probes_701,
    fixtures=lambda: [
        Fixture("ninja -k 0, 3 of 5 compiles fail in a CMake project with many -I/-D flags (real)", NINJA_FAILED_BUILD, True),
        Fixture("cmake configure noise first, then the same failing build", COMPOSITE_701, True),
        Fixture("bazel CppCompile failures (reconstructed from memory)", BAZEL_FAILED_BUILD, True),
        Fixture("a single failed compile: nothing to share", "FAILED: CMakeFiles/app.dir/src/ui.cpp.o \n/usr/bin/c++ -DA=1 -I/work/include -std=gnu++17 -Wall -O2 -g -MD -MT x.o -MF x.o.d -o x.o -c /work/app/src/ui.cpp\n/work/app/src/ui.cpp:2:25: error: expected ';'\nninja: build stopped: subcommand failed.\n", False),
        Fixture("link failures are not compile commands", "FAILED: app \n: && /usr/bin/c++ -g a.o b.o -o app && :\n/usr/bin/ld: a.o: undefined reference to `foo'\nFAILED: tool \n: && /usr/bin/c++ -g c.o d.o -o tool && :\n/usr/bin/ld: c.o: undefined reference to `bar'\n", False),
        Fixture("a stray legend-looking line and an @ argument in the source name", "[ctxprune: @1 = -Wall -O2 -g]\nFAILED: a.o \n/usr/bin/c++ @1 -c a.cc\nFAILED: b.o \n/usr/bin/c++ @1 -c b.cc\n", None),
    ],
    min_gain=0.2,
)



# ===============================================================================================================
# #703 gcc -E / clang -E: drop the regions that linemarkers flag as system headers, keep the user's own regions
# (macro-expanded code, in order, with its line numbers). RULE. The trap the real capture shows: a macro that expands
# to system text inside the user file is marked '# 9 "u.c" 3 4' (flag 3 on the USER file), so "drop every flag-3
# region" would delete `stderr` from `fprintf(stderr, ...)`. The rule drops only files that were ENTERED as system
# headers (flag 1 together with flag 3). The probe classifies by directory instead, a different test.

GCC_E_TRIMMED = (
    '# 0 "u.c"\n'
    '# 0 "<built-in>"\n'
    '# 0 "<command-line>"\n'
    '# 1 "/usr/include/stdc-predef.h" 1 3 4\n'
    '# 0 "<command-line>" 2\n'
    '# 1 "u.c"\n'
    '# 1 "/usr/include/stdio.h" 1 3 4\n'
    '# 28 "/usr/include/stdio.h" 3 4\n'
    '# 1 "/usr/include/x86_64-linux-gnu/bits/libc-header-start.h" 1 3 4\n'
    '# 33 "/usr/include/x86_64-linux-gnu/bits/libc-header-start.h" 3 4\n'
    '# 1 "/usr/include/features.h" 1 3 4\n'
    '# 394 "/usr/include/features.h" 3 4\n'
    '# 1 "/usr/include/features-time64.h" 1 3 4\n'
    '# 20 "/usr/include/features-time64.h" 3 4\n'
    '# 1 "/usr/include/x86_64-linux-gnu/bits/wordsize.h" 1 3 4\n'
    '# 21 "/usr/include/features-time64.h" 2 3 4\n'
    '# 1 "/usr/include/x86_64-linux-gnu/bits/timesize.h" 1 3 4\n'
    '# 19 "/usr/include/x86_64-linux-gnu/bits/timesize.h" 3 4\n'
    '# 1 "/usr/include/x86_64-linux-gnu/bits/wordsize.h" 1 3 4\n'
    '# 20 "/usr/include/x86_64-linux-gnu/bits/timesize.h" 2 3 4\n'
    '# 22 "/usr/include/features-time64.h" 2 3 4\n'
    '# 395 "/usr/include/features.h" 2 3 4\n'
    '# 502 "/usr/include/features.h" 3 4\n'
    '# 1 "/usr/include/x86_64-linux-gnu/sys/cdefs.h" 1 3 4\n'
    '# 576 "/usr/include/x86_64-linux-gnu/sys/cdefs.h" 3 4\n'
    '# 1 "/usr/include/x86_64-linux-gnu/bits/wordsize.h" 1 3 4\n'
    '# 577 "/usr/include/x86_64-linux-gnu/sys/cdefs.h" 2 3 4\n'
    '# 1 "/usr/include/x86_64-linux-gnu/bits/long-double.h" 1 3 4\n'
    '# 578 "/usr/include/x86_64-linux-gnu/sys/cdefs.h" 2 3 4\n'
    '# 503 "/usr/include/features.h" 2 3 4\n'
    '# 526 "/usr/include/features.h" 3 4\n'
    '# 1 "/usr/include/x86_64-linux-gnu/gnu/stubs.h" 1 3 4\n'
    '# 10 "/usr/include/x86_64-linux-gnu/gnu/stubs.h" 3 4\n'
    '# 1 "/usr/include/x86_64-linux-gnu/gnu/stubs-64.h" 1 3 4\n'
    '# 11 "/usr/include/x86_64-linux-gnu/gnu/stubs.h" 2 3 4\n'
    '# 527 "/usr/include/features.h" 2 3 4\n'
    '# 34 "/usr/include/x86_64-linux-gnu/bits/libc-header-start.h" 2 3 4\n'
    '# 29 "/usr/include/stdio.h" 2 3 4\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '# 1 "/usr/lib/gcc/x86_64-linux-gnu/13/include/stddef.h" 1 3 4\n'
    '# 214 "/usr/lib/gcc/x86_64-linux-gnu/13/include/stddef.h" 3 4\n'
    '\n'
    '# 214 "/usr/lib/gcc/x86_64-linux-gnu/13/include/stddef.h" 3 4\n'
    'typedef long unsigned int size_t;\n'
    '# 35 "/usr/include/stdio.h" 2 3 4\n'
    '\n'
    '\n'
    '# 1 "/usr/lib/gcc/x86_64-linux-gnu/13/include/stdarg.h" 1 3 4\n'
    '# 40 "/usr/lib/gcc/x86_64-linux-gnu/13/include/stdarg.h" 3 4\n'
    'typedef __builtin_va_list __gnuc_va_list;\n'
    '# 38 "/usr/include/stdio.h" 2 3 4\n'
    '\n'
    '# 1 "/usr/include/x86_64-linux-gnu/bits/types.h" 1 3 4\n'
    '# 27 "/usr/include/x86_64-linux-gnu/bits/types.h" 3 4\n'
    '# 1 "/usr/include/x86_64-linux-gnu/bits/wordsize.h" 1 3 4\n'
    '# 28 "/usr/include/x86_64-linux-gnu/bits/types.h" 2 3 4\n'
    '# 1 "/usr/include/x86_64-linux-gnu/bits/timesize.h" 1 3 4\n'
    '# 19 "/usr/include/x86_64-linux-gnu/bits/timesize.h" 3 4\n'
    '# 1 "/usr/include/x86_64-linux-gnu/bits/wordsize.h" 1 3 4\n'
    '# 20 "/usr/include/x86_64-linux-gnu/bits/timesize.h" 2 3 4\n'
    '# 29 "/usr/include/x86_64-linux-gnu/bits/types.h" 2 3 4\n'
    '\n'
    '\n'
    'typedef unsigned char __u_char;\n'
    'typedef unsigned short int __u_short;\n'
    '# 141 "/usr/include/x86_64-linux-gnu/bits/types.h" 3 4\n'
    '# 1 "/usr/include/x86_64-linux-gnu/bits/typesizes.h" 1 3 4\n'
    '# 142 "/usr/include/x86_64-linux-gnu/bits/types.h" 2 3 4\n'
    '# 1 "/usr/include/x86_64-linux-gnu/bits/time64.h" 1 3 4\n'
    '# 143 "/usr/include/x86_64-linux-gnu/bits/types.h" 2 3 4\n'
    '\n'
    '\n'
    'typedef unsigned long int __dev_t;\n'
    'typedef unsigned int __uid_t;\n'
    '# 40 "/usr/include/stdio.h" 2 3 4\n'
    '# 1 "/usr/include/x86_64-linux-gnu/bits/types/__fpos_t.h" 1 3 4\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '# 1 "/usr/include/x86_64-linux-gnu/bits/types/__mbstate_t.h" 1 3 4\n'
    '# 13 "/usr/include/x86_64-linux-gnu/bits/types/__mbstate_t.h" 3 4\n'
    'typedef struct\n'
    '{\n'
    '  int __count;\n'
    '  union\n'
    '# 6 "/usr/include/x86_64-linux-gnu/bits/types/__fpos_t.h" 2 3 4\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '# 41 "/usr/include/stdio.h" 2 3 4\n'
    '# 1 "/usr/include/x86_64-linux-gnu/bits/types/__fpos64_t.h" 1 3 4\n'
    '# 10 "/usr/include/x86_64-linux-gnu/bits/types/__fpos64_t.h" 3 4\n'
    'typedef struct _G_fpos64_t\n'
    '{\n'
    '  __off64_t __pos;\n'
    '  __mbstate_t __state;\n'
    '# 42 "/usr/include/stdio.h" 2 3 4\n'
    '# 1 "/usr/include/x86_64-linux-gnu/bits/types/__FILE.h" 1 3 4\n'
    '\n'
    '\n'
    '\n'
    'struct _IO_FILE;\n'
    '# 43 "/usr/include/stdio.h" 2 3 4\n'
    '# 1 "/usr/include/x86_64-linux-gnu/bits/types/FILE.h" 1 3 4\n'
    '\n'
    '\n'
    '\n'
    'struct _IO_FILE;\n'
    '# 44 "/usr/include/stdio.h" 2 3 4\n'
    '# 1 "/usr/include/x86_64-linux-gnu/bits/types/struct_FILE.h" 1 3 4\n'
    '# 35 "/usr/include/x86_64-linux-gnu/bits/types/struct_FILE.h" 3 4\n'
    'struct _IO_FILE;\n'
    'struct _IO_marker;\n'
    'struct _IO_codecvt;\n'
    'struct _IO_wide_data;\n'
    '# 45 "/usr/include/stdio.h" 2 3 4\n'
    '\n'
    '\n'
    '# 1 "/usr/include/x86_64-linux-gnu/bits/types/cookie_io_functions_t.h" 1 3 4\n'
    '# 27 "/usr/include/x86_64-linux-gnu/bits/types/cookie_io_functions_t.h" 3 4\n'
    'typedef __ssize_t cookie_read_function_t (void *__cookie, char *__buf,\n'
    '                                          size_t __nbytes);\n'
    '\n'
    '\n'
    '# 48 "/usr/include/stdio.h" 2 3 4\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '# 64 "/usr/include/stdio.h" 3 4\n'
    'typedef __off_t off_t;\n'
    '# 78 "/usr/include/stdio.h" 3 4\n'
    'typedef __ssize_t ssize_t;\n'
    '\n'
    '\n'
    '\n'
    '# 129 "/usr/include/stdio.h" 3 4\n'
    '# 1 "/usr/include/x86_64-linux-gnu/bits/stdio_lim.h" 1 3 4\n'
    '# 130 "/usr/include/stdio.h" 2 3 4\n'
    '# 149 "/usr/include/stdio.h" 3 4\n'
    'extern FILE *stdin;\n'
    'extern FILE *stdout;\n'
    'extern FILE *stderr;\n'
    '\n'
    '# 184 "/usr/include/stdio.h" 3 4\n'
    'extern int fclose (FILE *__stream) __attribute__ ((__nonnull__ (1)));\n'
    '# 194 "/usr/include/stdio.h" 3 4\n'
    'extern FILE *tmpfile (void)\n'
    '  __attribute__ ((__malloc__)) __attribute__ ((__malloc__ (fclose, 1))) ;\n'
    '# 211 "/usr/include/stdio.h" 3 4\n'
    'extern char *tmpnam (char[20]) __attribute__ ((__nothrow__ , __leaf__)) ;\n'
    '\n'
    '\n'
    '\n'
    '# 228 "/usr/include/stdio.h" 3 4\n'
    'extern char *tempnam (const char *__dir, const char *__pfx)\n'
    '   __attribute__ ((__nothrow__ , __leaf__)) __attribute__ ((__malloc__)) __attribute__ ((__malloc__ (__builtin_free, 1)));\n'
    '\n'
    '\n'
    '# 245 "/usr/include/stdio.h" 3 4\n'
    'extern int fflush_unlocked (FILE *__stream);\n'
    '# 264 "/usr/include/stdio.h" 3 4\n'
    'extern FILE *fopen (const char *__restrict __filename,\n'
    '      const char *__restrict __modes)\n'
    '  __attribute__ ((__malloc__)) __attribute__ ((__malloc__ (fclose, 1))) ;\n'
    '\n'
    '# 299 "/usr/include/stdio.h" 3 4\n'
    'extern FILE *fdopen (int __fd, const char *__modes) __attribute__ ((__nothrow__ , __leaf__))\n'
    '  __attribute__ ((__malloc__)) __attribute__ ((__malloc__ (fclose, 1))) ;\n'
    '\n'
    '\n'
    '# 334 "/usr/include/stdio.h" 3 4\n'
    'extern void setbuf (FILE *__restrict __stream, char *__restrict __buf) __attribute__ ((__nothrow__ , __leaf__))\n'
    '  __attribute__ ((__nonnull__ (1)));\n'
    '\n'
    '\n'
    '# 1 "/usr/include/x86_64-linux-gnu/bits/floatn.h" 1 3 4\n'
    '# 119 "/usr/include/x86_64-linux-gnu/bits/floatn.h" 3 4\n'
    '# 1 "/usr/include/x86_64-linux-gnu/bits/floatn-common.h" 1 3 4\n'
    '# 24 "/usr/include/x86_64-linux-gnu/bits/floatn-common.h" 3 4\n'
    '# 1 "/usr/include/x86_64-linux-gnu/bits/long-double.h" 1 3 4\n'
    '# 25 "/usr/include/x86_64-linux-gnu/bits/floatn-common.h" 2 3 4\n'
    '# 120 "/usr/include/x86_64-linux-gnu/bits/floatn.h" 2 3 4\n'
    '# 438 "/usr/include/stdio.h" 2 3 4\n'
    '# 463 "/usr/include/stdio.h" 3 4\n'
    'extern int fscanf (FILE *__restrict __stream, const char *__restrict __format, ...) __asm__ ("" "__isoc99_fscanf")\n'
    '\n'
    '                                __attribute__ ((__nonnull__ (1)));\n'
    'extern int scanf (const char *__restrict __format, ...) __asm__ ("" "__isoc99_scanf")\n'
    '# 490 "/usr/include/stdio.h" 3 4\n'
    'extern int vfscanf (FILE *__restrict __s, const char *__restrict __format,\n'
    '      __gnuc_va_list __arg)\n'
    '     __attribute__ ((__format__ (__scanf__, 2, 0))) __attribute__ ((__nonnull__ (1)));\n'
    '\n'
    '# 540 "/usr/include/stdio.h" 3 4\n'
    'extern int vfscanf (FILE *__restrict __s, const char *__restrict __format, __gnuc_va_list __arg) __asm__ ("" "__isoc99_vfscanf")\n'
    '\n'
    '\n'
    '\n'
    '# 575 "/usr/include/stdio.h" 3 4\n'
    'extern int fgetc (FILE *__stream) __attribute__ ((__nonnull__ (1)));\n'
    'extern int getc (FILE *__stream) __attribute__ ((__nonnull__ (1)));\n'
    '\n'
    '\n'
    '# 600 "/usr/include/stdio.h" 3 4\n'
    'extern int fgetc_unlocked (FILE *__stream) __attribute__ ((__nonnull__ (1)));\n'
    '# 611 "/usr/include/stdio.h" 3 4\n'
    'extern int fputc (int __c, FILE *__stream) __attribute__ ((__nonnull__ (2)));\n'
    'extern int putc (int __c, FILE *__stream) __attribute__ ((__nonnull__ (2)));\n'
    '\n'
    '\n'
    '# 627 "/usr/include/stdio.h" 3 4\n'
    'extern int fputc_unlocked (int __c, FILE *__stream) __attribute__ ((__nonnull__ (2)));\n'
    '\n'
    '\n'
    '\n'
    '# 694 "/usr/include/stdio.h" 3 4\n'
    'extern __ssize_t __getdelim (char **__restrict __lineptr,\n'
    '                             size_t *__restrict __n, int __delimiter,\n'
    '                             FILE *__restrict __stream) __attribute__ ((__nonnull__ (4)));\n'
    'extern __ssize_t getdelim (char **__restrict __lineptr,\n'
    '# 766 "/usr/include/stdio.h" 3 4\n'
    'extern size_t fread_unlocked (void *__restrict __ptr, size_t __size,\n'
    '         size_t __n, FILE *__restrict __stream)\n'
    '  __attribute__ ((__nonnull__ (4)));\n'
    'extern size_t fwrite_unlocked (const void *__restrict __ptr, size_t __size,\n'
    '# 803 "/usr/include/stdio.h" 3 4\n'
    'extern int fseeko (FILE *__stream, __off_t __off, int __whence)\n'
    '  __attribute__ ((__nonnull__ (1)));\n'
    '\n'
    '\n'
    '# 829 "/usr/include/stdio.h" 3 4\n'
    'extern int fgetpos (FILE *__restrict __stream, fpos_t *__restrict __pos)\n'
    '  __attribute__ ((__nonnull__ (1)));\n'
    '\n'
    '\n'
    '# 860 "/usr/include/stdio.h" 3 4\n'
    'extern void clearerr (FILE *__stream) __attribute__ ((__nothrow__ , __leaf__)) __attribute__ ((__nonnull__ (1)));\n'
    '\n'
    'extern int feof (FILE *__stream) __attribute__ ((__nothrow__ , __leaf__)) __attribute__ ((__nonnull__ (1)));\n'
    '\n'
    '# 897 "/usr/include/stdio.h" 3 4\n'
    'extern int pclose (FILE *__stream) __attribute__ ((__nonnull__ (1)));\n'
    '\n'
    '\n'
    '\n'
    '# 941 "/usr/include/stdio.h" 3 4\n'
    'extern void flockfile (FILE *__stream) __attribute__ ((__nothrow__ , __leaf__)) __attribute__ ((__nonnull__ (1)));\n'
    '\n'
    '\n'
    '\n'
    '# 959 "/usr/include/stdio.h" 3 4\n'
    'extern int __uflow (FILE *);\n'
    'extern int __overflow (FILE *, int);\n'
    '# 983 "/usr/include/stdio.h" 3 4\n'
    '\n'
    '# 2 "u.c" 2\n'
    '# 1 "/usr/include/string.h" 1 3 4\n'
    '# 26 "/usr/include/string.h" 3 4\n'
    '# 1 "/usr/include/x86_64-linux-gnu/bits/libc-header-start.h" 1 3 4\n'
    '# 27 "/usr/include/string.h" 2 3 4\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '# 1 "/usr/lib/gcc/x86_64-linux-gnu/13/include/stddef.h" 1 3 4\n'
    '# 34 "/usr/include/string.h" 2 3 4\n'
    '# 43 "/usr/include/string.h" 3 4\n'
    'extern void *memcpy (void *__restrict __dest, const void *__restrict __src,\n'
    '       size_t __n) __attribute__ ((__nothrow__ , __leaf__)) __attribute__ ((__nonnull__ (1, 2)));\n'
    '\n'
    '\n'
    '# 80 "/usr/include/string.h" 3 4\n'
    'extern int __memcmpeq (const void *__s1, const void *__s2, size_t __n)\n'
    '     __attribute__ ((__nothrow__ , __leaf__)) __attribute__ ((__pure__)) __attribute__ ((__nonnull__ (1, 2)));\n'
    '# 107 "/usr/include/string.h" 3 4\n'
    'extern void *memchr (const void *__s, int __c, size_t __n)\n'
    '      __attribute__ ((__nothrow__ , __leaf__)) __attribute__ ((__pure__)) __attribute__ ((__nonnull__ (1)));\n'
    '# 141 "/usr/include/string.h" 3 4\n'
    'extern char *strcpy (char *__restrict __dest, const char *__restrict __src)\n'
    '     __attribute__ ((__nothrow__ , __leaf__)) __attribute__ ((__nonnull__ (1, 2)));\n'
    '\n'
    'extern char *strncpy (char *__restrict __dest,\n'
    '# 1 "/usr/include/x86_64-linux-gnu/bits/types/locale_t.h" 1 3 4\n'
    '# 22 "/usr/include/x86_64-linux-gnu/bits/types/locale_t.h" 3 4\n'
    '# 1 "/usr/include/x86_64-linux-gnu/bits/types/__locale_t.h" 1 3 4\n'
    '# 27 "/usr/include/x86_64-linux-gnu/bits/types/__locale_t.h" 3 4\n'
    'struct __locale_struct\n'
    '{\n'
    '\n'
    '  struct __locale_data *__locales[13];\n'
    '# 23 "/usr/include/x86_64-linux-gnu/bits/types/locale_t.h" 2 3 4\n'
    '\n'
    'typedef __locale_t locale_t;\n'
    '# 173 "/usr/include/string.h" 2 3 4\n'
    '\n'
    '\n'
    'extern int strcoll_l (const char *__s1, const char *__s2, locale_t __l)\n'
    '     __attribute__ ((__nothrow__ , __leaf__)) __attribute__ ((__pure__)) __attribute__ ((__nonnull__ (1, 2, 3)));\n'
    '# 246 "/usr/include/string.h" 3 4\n'
    'extern char *strchr (const char *__s, int __c)\n'
    '     __attribute__ ((__nothrow__ , __leaf__)) __attribute__ ((__pure__)) __attribute__ ((__nonnull__ (1)));\n'
    '# 273 "/usr/include/string.h" 3 4\n'
    'extern char *strrchr (const char *__s, int __c)\n'
    '     __attribute__ ((__nothrow__ , __leaf__)) __attribute__ ((__pure__)) __attribute__ ((__nonnull__ (1)));\n'
    '# 286 "/usr/include/string.h" 3 4\n'
    'extern char *strchrnul (const char *__s, int __c)\n'
    '     __attribute__ ((__nothrow__ , __leaf__)) __attribute__ ((__pure__)) __attribute__ ((__nonnull__ (1)));\n'
    '\n'
    '\n'
    '# 323 "/usr/include/string.h" 3 4\n'
    'extern char *strpbrk (const char *__s, const char *__accept)\n'
    '     __attribute__ ((__nothrow__ , __leaf__)) __attribute__ ((__pure__)) __attribute__ ((__nonnull__ (1, 2)));\n'
    '# 350 "/usr/include/string.h" 3 4\n'
    'extern char *strstr (const char *__haystack, const char *__needle)\n'
    '     __attribute__ ((__nothrow__ , __leaf__)) __attribute__ ((__pure__)) __attribute__ ((__nonnull__ (1, 2)));\n'
    '\n'
    '\n'
    '# 380 "/usr/include/string.h" 3 4\n'
    'extern char *strcasestr (const char *__haystack, const char *__needle)\n'
    '     __attribute__ ((__nothrow__ , __leaf__)) __attribute__ ((__pure__)) __attribute__ ((__nonnull__ (1, 2)));\n'
    '\n'
    '\n'
    '# 432 "/usr/include/string.h" 3 4\n'
    'extern int strerror_r (int __errnum, char *__buf, size_t __buflen) __asm__ ("" "__xpg_strerror_r") __attribute__ ((__nothrow__ , __leaf__))\n'
    '\n'
    '                        __attribute__ ((__nonnull__ (2)))\n'
    '    __attribute__ ((__access__ (__write_only__, 2, 3)));\n'
    '# 458 "/usr/include/string.h" 3 4\n'
    'extern char *strerror_l (int __errnum, locale_t __l) __attribute__ ((__nothrow__ , __leaf__));\n'
    '\n'
    '\n'
    '\n'
    '# 1 "/usr/include/strings.h" 1 3 4\n'
    '# 23 "/usr/include/strings.h" 3 4\n'
    '# 1 "/usr/lib/gcc/x86_64-linux-gnu/13/include/stddef.h" 1 3 4\n'
    '# 24 "/usr/include/strings.h" 2 3 4\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '# 68 "/usr/include/strings.h" 3 4\n'
    'extern char *index (const char *__s, int __c)\n'
    '     __attribute__ ((__nothrow__ , __leaf__)) __attribute__ ((__pure__)) __attribute__ ((__nonnull__ (1)));\n'
    '# 96 "/usr/include/strings.h" 3 4\n'
    'extern char *rindex (const char *__s, int __c)\n'
    '     __attribute__ ((__nothrow__ , __leaf__)) __attribute__ ((__pure__)) __attribute__ ((__nonnull__ (1)));\n'
    '\n'
    '\n'
    '# 463 "/usr/include/string.h" 2 3 4\n'
    '\n'
    '\n'
    '\n'
    'extern void explicit_bzero (void *__s, size_t __n) __attribute__ ((__nothrow__ , __leaf__)) __attribute__ ((__nonnull__ (1)))\n'
    '# 489 "/usr/include/string.h" 3 4\n'
    'extern char *__stpcpy (char *__restrict __dest, const char *__restrict __src)\n'
    '     __attribute__ ((__nothrow__ , __leaf__)) __attribute__ ((__nonnull__ (1, 2)));\n'
    'extern char *stpcpy (char *__restrict __dest, const char *__restrict __src)\n'
    '     __attribute__ ((__nothrow__ , __leaf__)) __attribute__ ((__nonnull__ (1, 2)));\n'
    '# 552 "/usr/include/string.h" 3 4\n'
    '\n'
    '# 3 "u.c" 2\n'
    '# 1 "util.h" 1\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '# 4 "util.h"\n'
    'int helper(int);\n'
    '# 4 "u.c" 2\n'
    '\n'
    '\n'
    '\n'
    '\n'
    'int area(int w) {\n'
    '    fprintf(\n'
    '# 9 "u.c" 3 4\n'
    '   stderr\n'
    '# 9 "u.c"\n'
    '   , "[%s:%d] " "area w=%d" "\\n", "u.c", 9, w);\n'
    '    return ((w) * (w)) + 64;\n'
    '}\n'
    '\n'
    'int main(int argc, char **argv) {\n'
    '    char buf[64];\n'
    '    strncpy(buf, argv[0], sizeof buf);\n'
    '    printf("%d\\n", area(argc));\n'
    '    return 0;\n'
    '}\n'
)

CLANG_E_HEAD = (  # reconstructed from memory: clang -E marks the <built-in> pseudo files with flag 3 too
    '# 1 "u.c"\n# 1 "<built-in>" 1\n# 1 "<built-in>" 3\n# 389 "<built-in>" 3\n# 1 "<command line>" 1\n# 1 "<built-in>" 2\n# 1 "u.c" 2\n'
    '# 1 "/usr/include/stdio.h" 1 3 4\n# 27 "/usr/include/stdio.h" 3 4\ntypedef struct _IO_FILE FILE;\nextern FILE *stderr;\nextern int printf(const char *, ...);\n'
    '# 1 "/usr/include/x86_64-linux-gnu/bits/types.h" 1 3 4\n# 28 "/usr/include/x86_64-linux-gnu/bits/types.h" 3 4\ntypedef unsigned char __u_char;\n'
    '# 2 "/usr/include/stdio.h" 2 3 4\ntypedef long ssize_t;\n'
    '# 2 "u.c" 2\n\nint area(int w) {\n    fprintf(stderr, "area %d\\n", w);\n    return ((w) * (w)) + 64;\n}\n'
)

_LMARK = re.compile(r'^# (\d+) "([^"]*)"((?: [1-4])*)$')
_E_STUB = re.compile(r"^// \[ctxprune: \d+ lines? of system headers elided.*\]$")


def drop_system_regions(text: str) -> str:
    lines = text.split("\n")
    marks = [(i, _LMARK.match(l)) for i, l in enumerate(lines)]
    sysfiles = {m.group(2) for _, m in marks if m and "1" in m.group(3).split() and "3" in m.group(3).split()}
    if not sysfiles:
        return text
    out, i, n = [], 0, len(lines)
    while i < n:
        m = _LMARK.match(lines[i])
        if m and m.group(2) in sysfiles:
            j, files = i, []
            while j < n:
                mj = _LMARK.match(lines[j])
                if mj and mj.group(2) not in sysfiles:
                    break
                if mj and mj.group(2) not in files:
                    files.append(mj.group(2))
                j += 1
            shown = ", ".join(f.rsplit("/", 1)[-1] for f in files[:4]) + (f", +{len(files) - 4} more" if len(files) > 4 else "")
            out.append(f"// [ctxprune: {j - i} lines of system headers elided ({shown})]")
            i = j
        else:
            out.append(lines[i])
            i += 1
    return shrink_only(text, "\n".join(out))


def _sys_dir(path: str) -> bool:
    return bool(re.search(r"/usr/(?:include|lib/(?:gcc|clang)|local/include)/", path))


def probes_703(text: str) -> dict:
    """Walk the linemarkers keeping a (file, line) cursor; report every non-marker line of a user file with its position.
    A file is a system file if it sits in a system include directory (the classification a human would use) or was
    entered with flags 1 and 3 (what the encoder keys on); the first test is independent of the encoder, the second
    keeps the probe from disagreeing about files whose marker was damaged."""
    entered = set()
    for l in text.split("\n"):
        if (m := _LMARK.match(l)) and {"1", "3"} <= set(m.group(3).split()):
            entered.add(m.group(2))
    cur_file, cur_line, user_lines, files = None, 1, [], []
    for l in text.split("\n"):
        if _E_STUB.match(l):
            continue
        m = _LMARK.match(l)
        if m:
            cur_file, cur_line = m.group(2), int(m.group(1))
            if not _sys_dir(cur_file) and cur_file not in entered and cur_file not in files:
                files.append(cur_file)
            continue
        if cur_file is not None and not _sys_dir(cur_file) and cur_file not in entered and l.strip():
            user_lines.append((cur_file, cur_line, l.strip()))
        cur_line += 1
    return {"non-blank lines of user files with (file, line number)": user_lines, "user files visited, in order": files,
            "text in order": [t for _, _, t in user_lines]}


CLANG_E_FULL = CLANG_E_HEAD

case(
    703,
    claim="Replaces every region of gcc/clang -E output that belongs to a file entered as a system header (linemarker flags 1 and 3) by one '// [ctxprune: N lines of system headers elided (...)]' line; user regions, their linemarkers and the macro-expanded text in them, including flag-3 markers on the user's own file, are unchanged.",
    loses="all text from system headers (declarations, inline functions and macros from <stdio.h>, <vector>, ...), and the linemarkers inside them",
    encode=drop_system_regions,
    probes=probes_703,
    fixtures=lambda: [
        Fixture("gcc -E of a C file with stdio.h and string.h, system regions cut to 4 lines each (real structure)", GCC_E_TRIMMED, True),
        Fixture("clang -E head: <built-in> is flag 3 but is not a system header (markers real, body shortened)", CLANG_E_HEAD, True),
        Fixture("gcc -E -P: no linemarkers at all", "int area(int w) {\n    return ((w) * (w)) + 64;\n}\n", False),
        Fixture("preprocessor output of a user header only", '# 1 "util.h" 1\n# 1 "util.h"\nint helper(int);\n# 2 "u.c" 2\nint main(void) { return helper(1); }\n', False),
        Fixture("a system file mentioned with flag 3 but never entered (marker for it was cut)", '# 1 "u.c"\n# 28 "/usr/include/stdio.h" 3 4\nextern int puts(const char *);\n# 3 "u.c" 2\nint main(void) { return puts("x"); }\n', False),
    ],
    min_gain=0.4,
)
