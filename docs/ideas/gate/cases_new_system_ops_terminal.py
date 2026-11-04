"""New lossless ideas for OS and system tools and for terminal rendering: #1454-#1463.

  #1454 replay cursor-up / erase-line redraws to the final screen   (equivalent: a character-grid replay)
  #1455 /proc/PID/maps: ditto the dev+inode+path tail                (roundtrip)
  #1456 /proc/PID/status: alias the hex Mems_allowed/Cpus_allowed masks to their *_list twins   (roundtrip)
  #1457 ps -eLf: keep a process's command line once, thread rows as LWP/C/TIME   (equivalent)
  #1458 ip addr: drop "valid_lft forever" lines, mark the derivable brd   (equivalent)
  #1459 pstree -ap: merge sibling thread leaves into one pid-range line   (roundtrip)
  #1460 ss: fold the IPv4/IPv6 twin listener rows                    (equivalent)
  #1461 ipconfig /all: drop the alignment dot leaders                (equivalent)
  #1462 strace -y: ditto repeated fd and AT_FDCWD path annotations   (roundtrip, with a header line)
  #1463 conntrack: replace the reply tuple that mirrors the original with '<mirror>'   (roundtrip)

Fixtures marked "real" were captured in this container (Ubuntu 24.04, kernel 6.18, procps, util-linux, strace 6.x,
pip's vendored rich, strace -y); the rest are "reconstructed from memory" or synthesised from a stated model and say so. Every
in-band marker has an escape for text that already looks like it (see each case), which is why most of the roundtrip
cases are not idempotent: re-encoding an encoded text escapes the markers it contains, so it grows by design.
"""
import ipaddress
import re
import unicodedata

from harness import Fixture, case


def _txt(*lines, end="\n"):
    """Join fixture lines; `end` is the final terminator (pass end="" for a text with no trailing newline)."""
    return "\n".join(lines) + end


# ---------------------------------------------------------------------------------------------------------------
# real captures (generated from files captured in this container; see the comment above each)

# real: cat /proc/self/maps (Ubuntu 24.04, Firecracker VM, kernel 6.18)
MAPS_CAT = _txt(
    '55c850f4a000-55c850f4c000 r--p 00000000 fe:00 208997                     /usr/bin/cat',
    '55c850f4c000-55c850f51000 r-xp 00002000 fe:00 208997                     /usr/bin/cat',
    '55c850f51000-55c850f53000 r--p 00007000 fe:00 208997                     /usr/bin/cat',
    '55c850f53000-55c850f54000 r--p 00008000 fe:00 208997                     /usr/bin/cat',
    '55c850f54000-55c850f55000 rw-p 00009000 fe:00 208997                     /usr/bin/cat',
    '55c87c8f3000-55c87c914000 rw-p 00000000 00:00 0                          [heap]',
    '7fe670e00000-7fe670e28000 r--p 00000000 fe:00 209637                     /usr/lib/x86_64-linux-gnu/libc.so.6',
    '7fe670e28000-7fe670fb1000 r-xp 00028000 fe:00 209637                     /usr/lib/x86_64-linux-gnu/libc.so.6',
    '7fe670fb1000-7fe671000000 r--p 001b1000 fe:00 209637                     /usr/lib/x86_64-linux-gnu/libc.so.6',
    '7fe671000000-7fe671004000 r--p 001ff000 fe:00 209637                     /usr/lib/x86_64-linux-gnu/libc.so.6',
    '7fe671004000-7fe671006000 rw-p 00203000 fe:00 209637                     /usr/lib/x86_64-linux-gnu/libc.so.6',
    '7fe671006000-7fe671013000 rw-p 00000000 00:00 0 ',
    '7fe67112a000-7fe67114f000 rw-p 00000000 00:00 0 ',
    '7fe67115a000-7fe67115c000 rw-p 00000000 00:00 0 ',
    '7fe67115c000-7fe671160000 r--p 00000000 00:00 0                          [vvar]',
    '7fe671160000-7fe671162000 r--p 00000000 00:00 0                          [vvar_vclock]',
    '7fe671162000-7fe671164000 r-xp 00000000 00:00 0                          [vdso]',
    '7fe671164000-7fe671165000 r--p 00000000 fe:00 209617                     /usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2',
    '7fe671165000-7fe671190000 r-xp 00001000 fe:00 209617                     /usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2',
    '7fe671190000-7fe67119a000 r--p 0002c000 fe:00 209617                     /usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2',
    '7fe67119a000-7fe67119c000 r--p 00036000 fe:00 209617                     /usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2',
    '7fe67119c000-7fe67119e000 rw-p 00038000 fe:00 209617                     /usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2',
    '7fff383e9000-7fff3840c000 rw-p 00000000 00:00 0                          [stack]',
    'ffffffffff600000-ffffffffff601000 --xp 00000000 00:00 0                  [vsyscall]',
)

# real: /proc/PID/maps of a python3.13 process with 34 threads, excerpt (lines 1-12, 120-130, 143-192 of 192)
MAPS_PY = _txt(
    '001ff000-00200000 rw-p 00000000 fe:00 60                                 /usr/local/bin/python3.13',
    '00200000-00767000 r--p 00001000 fe:00 60                                 /usr/local/bin/python3.13',
    '00768000-01244000 r-xp 00568000 fe:00 60                                 /usr/local/bin/python3.13',
    '01244000-012c5000 r--p 01043000 fe:00 60                                 /usr/local/bin/python3.13',
    '012c5000-0136e000 rw-p 010c3000 fe:00 60                                 /usr/local/bin/python3.13',
    '0136e000-013f1000 rw-p 00000000 00:00 0 ',
    '01400000-01da9000 r-xp 0116c000 fe:00 60                                 /usr/local/bin/python3.13',
    '3414e000-34299000 rw-p 00000000 00:00 0                                  [heap]',
    '7fec98000000-7fec98021000 rw-p 00000000 00:00 0 ',
    '7fec98021000-7fec9c000000 ---p 00000000 00:00 0 ',
    '7feca0000000-7feca0021000 rw-p 00000000 00:00 0 ',
    '7feca0021000-7feca4000000 ---p 00000000 00:00 0 ',
    '7fed20021000-7fed24000000 ---p 00000000 00:00 0 ',
    '7fed24000000-7fed24021000 rw-p 00000000 00:00 0 ',
    '7fed24021000-7fed28000000 ---p 00000000 00:00 0 ',
    '7fed28145000-7fed281b1000 rw-p 00000000 00:00 0 ',
    '7fed281b1000-7fed281b2000 ---p 00000000 00:00 0 ',
    '7fed281b2000-7fed289b2000 rw-p 00000000 00:00 0 ',
    '7fed289b2000-7fed289b6000 rw-p 00000000 00:00 0 ',
    '7fed289b6000-7fed289b7000 ---p 00000000 00:00 0 ',
    '7fed289b7000-7fed291b7000 rw-p 00000000 00:00 0 ',
    '7fed291b7000-7fed291bb000 rw-p 00000000 00:00 0 ',
    '7fed291bb000-7fed291bc000 ---p 00000000 00:00 0 ',
    '7fed2b9cc000-7fed2b9cd000 ---p 00000000 00:00 0 ',
    '7fed2b9cd000-7fed2c1cd000 rw-p 00000000 00:00 0 ',
    '7fed2c1cd000-7fed2c600000 rw-p 00000000 00:00 0 ',
    '7fed2c600000-7fed2c628000 r--p 00000000 fe:00 209637                     /usr/lib/x86_64-linux-gnu/libc.so.6',
    '7fed2c628000-7fed2c7b1000 r-xp 00028000 fe:00 209637                     /usr/lib/x86_64-linux-gnu/libc.so.6',
    '7fed2c7b1000-7fed2c800000 r--p 001b1000 fe:00 209637                     /usr/lib/x86_64-linux-gnu/libc.so.6',
    '7fed2c800000-7fed2c804000 r--p 001ff000 fe:00 209637                     /usr/lib/x86_64-linux-gnu/libc.so.6',
    '7fed2c804000-7fed2c806000 rw-p 00203000 fe:00 209637                     /usr/lib/x86_64-linux-gnu/libc.so.6',
    '7fed2c806000-7fed2c813000 rw-p 00000000 00:00 0 ',
    '7fed2c816000-7fed2c880000 rw-p 00000000 00:00 0 ',
    '7fed2c880000-7fed2c8d9000 r--p 00000000 fe:00 209314                     /usr/lib/locale/C.utf8/LC_CTYPE',
    '7fed2c8d9000-7fed2c8de000 rw-p 00000000 00:00 0 ',
    '7fed2c8de000-7fed2c8df000 r--p 00000000 fe:00 209718                     /usr/lib/x86_64-linux-gnu/librt.so.1',
    '7fed2c8df000-7fed2c8e0000 r-xp 00001000 fe:00 209718                     /usr/lib/x86_64-linux-gnu/librt.so.1',
    '7fed2c8e0000-7fed2c8e1000 r--p 00002000 fe:00 209718                     /usr/lib/x86_64-linux-gnu/librt.so.1',
    '7fed2c8e1000-7fed2c8e2000 r--p 00002000 fe:00 209718                     /usr/lib/x86_64-linux-gnu/librt.so.1',
    '7fed2c8e2000-7fed2c8e3000 rw-p 00003000 fe:00 209718                     /usr/lib/x86_64-linux-gnu/librt.so.1',
    '7fed2c8e3000-7fed2c8f3000 r--p 00000000 fe:00 209679                     /usr/lib/x86_64-linux-gnu/libm.so.6',
    '7fed2c8f3000-7fed2c972000 r-xp 00010000 fe:00 209679                     /usr/lib/x86_64-linux-gnu/libm.so.6',
    '7fed2c972000-7fed2c9ca000 r--p 0008f000 fe:00 209679                     /usr/lib/x86_64-linux-gnu/libm.so.6',
    '7fed2c9ca000-7fed2c9cb000 r--p 000e7000 fe:00 209679                     /usr/lib/x86_64-linux-gnu/libm.so.6',
    '7fed2c9cb000-7fed2c9cc000 rw-p 000e8000 fe:00 209679                     /usr/lib/x86_64-linux-gnu/libm.so.6',
    '7fed2c9cc000-7fed2c9cd000 r--p 00000000 fe:00 209744                     /usr/lib/x86_64-linux-gnu/libutil.so.1',
    '7fed2c9cd000-7fed2c9ce000 r-xp 00001000 fe:00 209744                     /usr/lib/x86_64-linux-gnu/libutil.so.1',
    '7fed2c9ce000-7fed2c9cf000 r--p 00002000 fe:00 209744                     /usr/lib/x86_64-linux-gnu/libutil.so.1',
    '7fed2c9cf000-7fed2c9d0000 r--p 00002000 fe:00 209744                     /usr/lib/x86_64-linux-gnu/libutil.so.1',
    '7fed2c9d0000-7fed2c9d1000 rw-p 00003000 fe:00 209744                     /usr/lib/x86_64-linux-gnu/libutil.so.1',
    '7fed2c9d1000-7fed2c9d2000 r--p 00000000 fe:00 209651                     /usr/lib/x86_64-linux-gnu/libdl.so.2',
    '7fed2c9d2000-7fed2c9d3000 r-xp 00001000 fe:00 209651                     /usr/lib/x86_64-linux-gnu/libdl.so.2',
    '7fed2c9d3000-7fed2c9d4000 r--p 00002000 fe:00 209651                     /usr/lib/x86_64-linux-gnu/libdl.so.2',
    '7fed2c9d4000-7fed2c9d5000 r--p 00002000 fe:00 209651                     /usr/lib/x86_64-linux-gnu/libdl.so.2',
    '7fed2c9d5000-7fed2c9d6000 rw-p 00003000 fe:00 209651                     /usr/lib/x86_64-linux-gnu/libdl.so.2',
    '7fed2c9d6000-7fed2c9d7000 r--p 00000000 fe:00 209716                     /usr/lib/x86_64-linux-gnu/libpthread.so.0',
    '7fed2c9d7000-7fed2c9d8000 r-xp 00001000 fe:00 209716                     /usr/lib/x86_64-linux-gnu/libpthread.so.0',
    '7fed2c9d8000-7fed2c9d9000 r--p 00002000 fe:00 209716                     /usr/lib/x86_64-linux-gnu/libpthread.so.0',
    '7fed2c9d9000-7fed2c9da000 r--p 00002000 fe:00 209716                     /usr/lib/x86_64-linux-gnu/libpthread.so.0',
    '7fed2c9da000-7fed2c9db000 rw-p 00003000 fe:00 209716                     /usr/lib/x86_64-linux-gnu/libpthread.so.0',
    '7fed2c9db000-7fed2c9df000 rw-p 00000000 00:00 0 ',
    '7fed2c9df000-7fed2c9e6000 r--s 00000000 fe:00 209608                     /usr/lib/x86_64-linux-gnu/gconv/gconv-modules.cache',
    '7fed2c9e6000-7fed2c9e8000 rw-p 00000000 00:00 0 ',
    '7fed2c9e8000-7fed2c9ec000 r--p 00000000 00:00 0                          [vvar]',
    '7fed2c9ec000-7fed2c9ee000 r--p 00000000 00:00 0                          [vvar_vclock]',
    '7fed2c9ee000-7fed2c9f0000 r-xp 00000000 00:00 0                          [vdso]',
    '7fed2c9f0000-7fed2c9f1000 r--p 00000000 fe:00 209617                     /usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2',
    '7fed2c9f1000-7fed2ca1c000 r-xp 00001000 fe:00 209617                     /usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2',
    '7fed2ca1c000-7fed2ca26000 r--p 0002c000 fe:00 209617                     /usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2',
    '7fed2ca26000-7fed2ca28000 r--p 00036000 fe:00 209617                     /usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2',
    '7fed2ca28000-7fed2ca2a000 rw-p 00038000 fe:00 209617                     /usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2',
    '7ffe4d941000-7ffe4d964000 rw-p 00000000 00:00 0                          [stack]',
    'ffffffffff600000-ffffffffff601000 --xp 00000000 00:00 0                  [vsyscall]',
)

# real: cat /proc/self/status
STATUS_CAT = _txt(
    'Name:\tcat',
    'Umask:\t0022',
    'State:\tR (running)',
    'Tgid:\t18407',
    'Ngid:\t0',
    'Pid:\t18407',
    'PPid:\t18405',
    'TracerPid:\t0',
    'Uid:\t0\t0\t0\t0',
    'Gid:\t0\t0\t0\t0',
    'FDSize:\t64',
    'Groups:\t ',
    'NStgid:\t18407',
    'NSpid:\t18407',
    'NSpgid:\t18407',
    'NSsid:\t18405',
    'Kthread:\t0',
    'VmPeak:\t    2860 kB',
    'VmSize:\t    2860 kB',
    'VmLck:\t       0 kB',
    'VmPin:\t       0 kB',
    'VmHWM:\t    1640 kB',
    'VmRSS:\t    1640 kB',
    'RssAnon:\t     108 kB',
    'RssFile:\t    1532 kB',
    'RssShmem:\t       0 kB',
    'VmData:\t     360 kB',
    'VmStk:\t     140 kB',
    'VmExe:\t      20 kB',
    'VmLib:\t    1752 kB',
    'VmPTE:\t      44 kB',
    'VmSwap:\t       0 kB',
    'HugetlbPages:\t       0 kB',
    'CoreDumping:\t0',
    'THP_enabled:\t1',
    'untag_mask:\t0xffffffffffffffff',
    'Threads:\t1',
    'SigQ:\t0/64276',
    'SigPnd:\t0000000000000000',
    'ShdPnd:\t0000000000000000',
    'SigBlk:\t0000000000000000',
    'SigIgn:\t0000000000000000',
    'SigCgt:\t0000000000000000',
    'CapInh:\t0000000000000000',
    'CapPrm:\t000001fffeffffff',
    'CapEff:\t000001fffeffffff',
    'CapBnd:\t000001fffeffffff',
    'CapAmb:\t0000000000000000',
    'NoNewPrivs:\t0',
    'Seccomp:\t0',
    'Seccomp_filters:\t0',
    'Speculation_Store_Bypass:\tthread vulnerable',
    'SpeculationIndirectBranch:\tconditional enabled',
    'Cpus_allowed:\tf',
    'Cpus_allowed_list:\t0-3',
    'Mems_allowed:\t00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000001',
    'Mems_allowed_list:\t0',
    'voluntary_ctxt_switches:\t0',
    'nonvoluntary_ctxt_switches:\t0',
)

# real: /proc/PID/status of a python3.13 process
STATUS_PY = _txt(
    'Name:\tpython3',
    'Umask:\t0022',
    'State:\tS (sleeping)',
    'Tgid:\t17412',
    'Ngid:\t0',
    'Pid:\t17412',
    'PPid:\t17409',
    'TracerPid:\t0',
    'Uid:\t0\t0\t0\t0',
    'Gid:\t0\t0\t0\t0',
    'FDSize:\t64',
    'Groups:\t ',
    'NStgid:\t17412',
    'NSpid:\t17412',
    'NSpgid:\t17412',
    'NSsid:\t17409',
    'Kthread:\t0',
    'VmPeak:\t 2389956 kB',
    'VmSize:\t 2349072 kB',
    'VmLck:\t       0 kB',
    'VmPin:\t       0 kB',
    'VmHWM:\t   14272 kB',
    'VmRSS:\t   14272 kB',
    'RssAnon:\t    5632 kB',
    'RssFile:\t    8640 kB',
    'RssShmem:\t       0 kB',
    'VmData:\t  290516 kB',
    'VmStk:\t     140 kB',
    'VmExe:\t   22788 kB',
    'VmLib:\t     500 kB',
    'VmPTE:\t     364 kB',
    'VmSwap:\t       0 kB',
    'HugetlbPages:\t       0 kB',
    'CoreDumping:\t0',
    'THP_enabled:\t1',
    'untag_mask:\t0xffffffffffffffff',
    'Threads:\t35',
    'SigQ:\t0/64276',
    'SigPnd:\t0000000000000000',
    'ShdPnd:\t0000000000000000',
    'SigBlk:\t0000000000000000',
    'SigIgn:\t0000000001001000',
    'SigCgt:\t0000000100000002',
    'CapInh:\t0000000000000000',
    'CapPrm:\t000001fffeffffff',
    'CapEff:\t000001fffeffffff',
    'CapBnd:\t000001fffeffffff',
    'CapAmb:\t0000000000000000',
    'NoNewPrivs:\t0',
    'Seccomp:\t0',
    'Seccomp_filters:\t0',
    'Speculation_Store_Bypass:\tthread vulnerable',
    'SpeculationIndirectBranch:\tconditional enabled',
    'Cpus_allowed:\tf',
    'Cpus_allowed_list:\t0-3',
    'Mems_allowed:\t00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000000,00000001',
    'Mems_allowed_list:\t0',
    'voluntary_ctxt_switches:\t39',
    'nonvoluntary_ctxt_switches:\t0',
)

# real: pstree -ap PID of a python3.13 process with 34 threads and one child
PSTREE_AP = _txt(
    'python3,17412 thr.py',
    '  |-sleep,17448 8',
    '  |-{python3},17414',
    '  |-{python3},17415',
    '  |-{python3},17416',
    '  |-{python3},17417',
    '  |-{python3},17418',
    '  |-{python3},17419',
    '  |-{python3},17420',
    '  |-{python3},17421',
    '  |-{python3},17422',
    '  |-{python3},17423',
    '  |-{python3},17424',
    '  |-{python3},17425',
    '  |-{python3},17426',
    '  |-{python3},17427',
    '  |-{python3},17428',
    '  |-{python3},17429',
    '  |-{python3},17430',
    '  |-{python3},17431',
    '  |-{python3},17432',
    '  |-{python3},17433',
    '  |-{python3},17434',
    '  |-{python3},17435',
    '  |-{python3},17436',
    '  |-{python3},17437',
    '  |-{python3},17438',
    '  |-{python3},17439',
    '  |-{python3},17440',
    '  |-{python3},17441',
    '  |-{python3},17442',
    '  |-{python3},17443',
    '  |-{python3},17444',
    '  |-{python3},17445',
    '  |-{python3},17446',
    '  `-{python3},17447',
)

# real: strace -f -y sh -c 'ls /etc | head -3; cat /etc/hostname' 2>file, first 100 lines, cwd rewritten to /home/dev/ctxprune/docs/ideas
STRACE_STDOUT = _txt(
    'execve("/usr/bin/sh", ["sh", "-c", "ls /etc | head -3; cat /etc/host"...], 0x7ffd11066cb0 /* 174 vars */) = 0',
    'brk(NULL)                               = 0x560374041000',
    'mmap(NULL, 8192, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANONYMOUS, -1, 0) = 0x7fda275af000',
    'access("/etc/ld.so.preload", R_OK)      = -1 ENOENT (No such file or directory)',
    'openat(AT_FDCWD</home/dev/ctxprune/docs/ideas>, "/etc/ld.so.cache", O_RDONLY|O_CLOEXEC) = 3</etc/ld.so.cache>',
    'fstat(3</etc/ld.so.cache>, {st_mode=S_IFREG|0644, st_size=44439, ...}) = 0',
    'mmap(NULL, 44439, PROT_READ, MAP_PRIVATE, 3</etc/ld.so.cache>, 0) = 0x7fda275a4000',
    'close(3</etc/ld.so.cache>)              = 0',
    'openat(AT_FDCWD</home/dev/ctxprune/docs/ideas>, "/lib/x86_64-linux-gnu/libc.so.6", O_RDONLY|O_CLOEXEC) = 3</usr/lib/x86_64-linux-gnu/libc.so.6>',
    'read(3</usr/lib/x86_64-linux-gnu/libc.so.6>, "\\177ELF\\2\\1\\1\\3\\0\\0\\0\\0\\0\\0\\0\\0\\3\\0>\\0\\1\\0\\0\\0\\220\\243\\2\\0\\0\\0\\0\\0"..., 832) = 832',
    'pread64(3</usr/lib/x86_64-linux-gnu/libc.so.6>, "\\6\\0\\0\\0\\4\\0\\0\\0@\\0\\0\\0\\0\\0\\0\\0@\\0\\0\\0\\0\\0\\0\\0@\\0\\0\\0\\0\\0\\0\\0"..., 784, 64) = 784',
    'fstat(3</usr/lib/x86_64-linux-gnu/libc.so.6>, {st_mode=S_IFREG|0755, st_size=2129424, ...}) = 0',
    'pread64(3</usr/lib/x86_64-linux-gnu/libc.so.6>, "\\6\\0\\0\\0\\4\\0\\0\\0@\\0\\0\\0\\0\\0\\0\\0@\\0\\0\\0\\0\\0\\0\\0@\\0\\0\\0\\0\\0\\0\\0"..., 784, 64) = 784',
    'mmap(NULL, 2174352, PROT_READ, MAP_PRIVATE|MAP_DENYWRITE, 3</usr/lib/x86_64-linux-gnu/libc.so.6>, 0) = 0x7fda27200000',
    'mmap(0x7fda27228000, 1609728, PROT_READ|PROT_EXEC, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3</usr/lib/x86_64-linux-gnu/libc.so.6>, 0x28000) = 0x7fda27228000',
    'mmap(0x7fda273b1000, 323584, PROT_READ, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3</usr/lib/x86_64-linux-gnu/libc.so.6>, 0x1b1000) = 0x7fda273b1000',
    'mmap(0x7fda27400000, 24576, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3</usr/lib/x86_64-linux-gnu/libc.so.6>, 0x1ff000) = 0x7fda27400000',
    'mmap(0x7fda27406000, 52624, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_FIXED|MAP_ANONYMOUS, -1, 0) = 0x7fda27406000',
    'close(3</usr/lib/x86_64-linux-gnu/libc.so.6>) = 0',
    'mmap(NULL, 12288, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANONYMOUS, -1, 0) = 0x7fda275a1000',
    'arch_prctl(ARCH_SET_FS, 0x7fda275a1740) = 0',
    'set_tid_address(0x7fda275a1a10)         = 18156',
    'set_robust_list(0x7fda275a1a20, 24)     = 0',
    'rseq(0x7fda275a2060, 0x20, 0, 0x53053053) = 0',
    'mprotect(0x7fda27400000, 16384, PROT_READ) = 0',
    'mprotect(0x5603446c3000, 8192, PROT_READ) = 0',
    'mprotect(0x7fda275ef000, 8192, PROT_READ) = 0',
    'prlimit64(0, RLIMIT_STACK, NULL, {rlim_cur=8192*1024, rlim_max=RLIM64_INFINITY}) = 0',
    'munmap(0x7fda275a4000, 44439)           = 0',
    'getuid()                                = 0',
    'getgid()                                = 0',
    'getpid()                                = 18156',
    'rt_sigaction(SIGCHLD, {sa_handler=0x5603446b8cd0, sa_mask=~[RTMIN RT_1], sa_flags=SA_RESTORER, sa_restorer=0x7fda27245330}, NULL, 8) = 0',
    'geteuid()                               = 0',
    'getrandom("\\xdd\\x8d\\x84\\xfe\\xfd\\xb1\\xb2\\x6e", 8, GRND_NONBLOCK) = 8',
    'brk(NULL)                               = 0x560374041000',
    'brk(0x560374062000)                     = 0x560374062000',
    'getppid()                               = 18153',
    'newfstatat(AT_FDCWD</home/dev/ctxprune/docs/ideas>, "/home/dev/ctxprune/docs/ideas", {st_mode=S_IFDIR|0755, st_size=4096, ...}, 0) = 0',
    'newfstatat(AT_FDCWD</home/dev/ctxprune/docs/ideas>, ".", {st_mode=S_IFDIR|0755, st_size=4096, ...}, 0) = 0',
    'geteuid()                               = 0',
    'getegid()                               = 0',
    'rt_sigaction(SIGINT, NULL, {sa_handler=SIG_DFL, sa_mask=[], sa_flags=0}, 8) = 0',
    'rt_sigaction(SIGINT, {sa_handler=0x5603446b8cd0, sa_mask=~[RTMIN RT_1], sa_flags=SA_RESTORER, sa_restorer=0x7fda27245330}, NULL, 8) = 0',
    'rt_sigaction(SIGQUIT, NULL, {sa_handler=SIG_DFL, sa_mask=[], sa_flags=0}, 8) = 0',
    'rt_sigaction(SIGQUIT, {sa_handler=SIG_DFL, sa_mask=~[RTMIN RT_1], sa_flags=SA_RESTORER, sa_restorer=0x7fda27245330}, NULL, 8) = 0',
    'rt_sigaction(SIGTERM, NULL, {sa_handler=SIG_DFL, sa_mask=[], sa_flags=0}, 8) = 0',
    'rt_sigaction(SIGTERM, {sa_handler=SIG_DFL, sa_mask=~[RTMIN RT_1], sa_flags=SA_RESTORER, sa_restorer=0x7fda27245330}, NULL, 8) = 0',
    'newfstatat(AT_FDCWD</home/dev/ctxprune/docs/ideas>, "/nix/var/nix/profiles/default/bin/ls", 0x7ffcfe97b6f0, 0) = -1 ENOENT (No such file or directory)',
    'newfstatat(AT_FDCWD</home/dev/ctxprune/docs/ideas>, "/root/.local/bin/ls", 0x7ffcfe97b6f0, 0) = -1 ENOENT (No such file or directory)',
    'newfstatat(AT_FDCWD</home/dev/ctxprune/docs/ideas>, "/root/.cargo/bin/ls", 0x7ffcfe97b6f0, 0) = -1 ENOENT (No such file or directory)',
    'newfstatat(AT_FDCWD</home/dev/ctxprune/docs/ideas>, "/usr/local/go/bin/ls", 0x7ffcfe97b6f0, 0) = -1 ENOENT (No such file or directory)',
    'newfstatat(AT_FDCWD</home/dev/ctxprune/docs/ideas>, "/opt/node22/bin/ls", 0x7ffcfe97b6f0, 0) = -1 ENOENT (No such file or directory)',
    'newfstatat(AT_FDCWD</home/dev/ctxprune/docs/ideas>, "/opt/maven/bin/ls", 0x7ffcfe97b6f0, 0) = -1 ENOENT (No such file or directory)',
    'newfstatat(AT_FDCWD</home/dev/ctxprune/docs/ideas>, "/opt/gradle/bin/ls", 0x7ffcfe97b6f0, 0) = -1 ENOENT (No such file or directory)',
    'newfstatat(AT_FDCWD</home/dev/ctxprune/docs/ideas>, "/opt/rbenv/bin/ls", 0x7ffcfe97b6f0, 0) = -1 ENOENT (No such file or directory)',
    'newfstatat(AT_FDCWD</home/dev/ctxprune/docs/ideas>, "/root/.bun/bin/ls", 0x7ffcfe97b6f0, 0) = -1 ENOENT (No such file or directory)',
    'newfstatat(AT_FDCWD</home/dev/ctxprune/docs/ideas>, "/usr/local/sbin/ls", 0x7ffcfe97b6f0, 0) = -1 ENOENT (No such file or directory)',
    'newfstatat(AT_FDCWD</home/dev/ctxprune/docs/ideas>, "/usr/local/bin/ls", 0x7ffcfe97b6f0, 0) = -1 ENOENT (No such file or directory)',
    'newfstatat(AT_FDCWD</home/dev/ctxprune/docs/ideas>, "/usr/sbin/ls", 0x7ffcfe97b6f0, 0) = -1 ENOENT (No such file or directory)',
    'newfstatat(AT_FDCWD</home/dev/ctxprune/docs/ideas>, "/usr/bin/ls", {st_mode=S_IFREG|0755, st_size=142312, ...}, 0) = 0',
    'pipe2([3<pipe:[63993]>, 4<pipe:[63993]>], 0) = 0',
    'clone(child_stack=NULL, flags=CLONE_CHILD_CLEARTID|CLONE_CHILD_SETTID|SIGCHLD, child_tidptr=0x7fda275a1a10) = 18157',
    'strace: Process 18157 attached',
    '[pid 18156] close(4<pipe:[63993]> <unfinished ...>',
    '[pid 18157] set_robust_list(0x7fda275a1a20, 24 <unfinished ...>',
    '[pid 18156] <... close resumed>)        = 0',
    '[pid 18156] newfstatat(AT_FDCWD</home/dev/ctxprune/docs/ideas>, "/nix/var/nix/profiles/default/bin/head",  <unfinished ...>',
    '[pid 18157] <... set_robust_list resumed>) = 0',
    '[pid 18156] <... newfstatat resumed>0x7ffcfe97b6f0, 0) = -1 ENOENT (No such file or directory)',
    '[pid 18156] newfstatat(AT_FDCWD</home/dev/ctxprune/docs/ideas>, "/root/.local/bin/head", 0x7ffcfe97b6f0, 0) = -1 ENOENT (No such file or directory)',
    '[pid 18157] close(3<pipe:[63993]> <unfinished ...>',
    '[pid 18156] newfstatat(AT_FDCWD</home/dev/ctxprune/docs/ideas>, "/root/.cargo/bin/head",  <unfinished ...>',
    '[pid 18157] <... close resumed>)        = 0',
    '[pid 18156] <... newfstatat resumed>0x7ffcfe97b6f0, 0) = -1 ENOENT (No such file or directory)',
    '[pid 18157] dup2(4<pipe:[63993]>, 1</dev/null> <unfinished ...>',
    '[pid 18156] newfstatat(AT_FDCWD</home/dev/ctxprune/docs/ideas>, "/usr/local/go/bin/head",  <unfinished ...>',
    '[pid 18157] <... dup2 resumed>)         = 1<pipe:[63993]>',
    '[pid 18156] <... newfstatat resumed>0x7ffcfe97b6f0, 0) = -1 ENOENT (No such file or directory)',
    '[pid 18157] close(4<pipe:[63993]> <unfinished ...>',
    '[pid 18156] newfstatat(AT_FDCWD</home/dev/ctxprune/docs/ideas>, "/opt/node22/bin/head",  <unfinished ...>',
    '[pid 18157] <... close resumed>)        = 0',
    '[pid 18156] <... newfstatat resumed>0x7ffcfe97b6f0, 0) = -1 ENOENT (No such file or directory)',
    '[pid 18156] newfstatat(AT_FDCWD</home/dev/ctxprune/docs/ideas>, "/opt/maven/bin/head",  <unfinished ...>',
    '[pid 18157] execve("/usr/bin/ls", ["ls", "/etc"], 0x560374043818 /* 174 vars */ <unfinished ...>',
    '[pid 18156] <... newfstatat resumed>0x7ffcfe97b6f0, 0) = -1 ENOENT (No such file or directory)',
    '[pid 18156] newfstatat(AT_FDCWD</home/dev/ctxprune/docs/ideas>, "/opt/gradle/bin/head", 0x7ffcfe97b6f0, 0) = -1 ENOENT (No such file or directory)',
    '[pid 18156] newfstatat(AT_FDCWD</home/dev/ctxprune/docs/ideas>, "/opt/rbenv/bin/head", 0x7ffcfe97b6f0, 0) = -1 ENOENT (No such file or directory)',
    '[pid 18156] newfstatat(AT_FDCWD</home/dev/ctxprune/docs/ideas>, "/root/.bun/bin/head", 0x7ffcfe97b6f0, 0) = -1 ENOENT (No such file or directory)',
    '[pid 18156] newfstatat(AT_FDCWD</home/dev/ctxprune/docs/ideas>, "/usr/local/sbin/head",  <unfinished ...>',
    '[pid 18157] <... execve resumed>)       = 0',
    '[pid 18156] <... newfstatat resumed>0x7ffcfe97b6f0, 0) = -1 ENOENT (No such file or directory)',
    '[pid 18156] newfstatat(AT_FDCWD</home/dev/ctxprune/docs/ideas>, "/usr/local/bin/head",  <unfinished ...>',
    '[pid 18157] brk(NULL <unfinished ...>',
    '[pid 18156] <... newfstatat resumed>0x7ffcfe97b6f0, 0) = -1 ENOENT (No such file or directory)',
    '[pid 18157] <... brk resumed>)          = 0x5579e783c000',
    '[pid 18156] newfstatat(AT_FDCWD</home/dev/ctxprune/docs/ideas>, "/usr/sbin/head", 0x7ffcfe97b6f0, 0) = -1 ENOENT (No such file or directory)',
    '[pid 18156] newfstatat(AT_FDCWD</home/dev/ctxprune/docs/ideas>, "/usr/bin/head", {st_mode=S_IFREG|0755, st_size=43528, ...}, 0) = 0',
    '[pid 18156] clone(child_stack=NULL, flags=CLONE_CHILD_CLEARTID|CLONE_CHILD_SETTID|SIGCHLD, child_tidptr=0x7fda275a1a10) = 18158',
    '[pid 18156] close(3<pipe:[63993]>strace: Process 18158 attached',
)

# real: strace -f -y -o file python3 -c ..., lines 1-36 and 195-215 (pid prefix without brackets), cwd rewritten to /home/dev/ctxprune/docs/ideas
STRACE_FILE = _txt(
    '15243 execve("/usr/local/bin/python3", ["python3", "-c", "import json,os;print(open(\'/etc/"...], 0x7ffe1700ac10 /* 174 vars */) = 0',
    '15243 brk(NULL)                         = 0x2944b000',
    '15243 mmap(NULL, 8192, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANONYMOUS, -1, 0) = 0x7f5138d39000',
    '15243 readlinkat(AT_FDCWD</home/dev/ctxprune/docs/ideas>, "/proc/self/exe", "/usr/local/bin/python3.13", 4096) = 25',
    '15243 access("/etc/ld.so.preload", R_OK) = -1 ENOENT (No such file or directory)',
    '15243 openat(AT_FDCWD</home/dev/ctxprune/docs/ideas>, "/usr/local/bin/../lib/glibc-hwcaps/x86-64-v4/libpthread.so.0", O_RDONLY|O_CLOEXEC) = -1 ENOENT (No such file or directory)',
    '15243 newfstatat(AT_FDCWD</home/dev/ctxprune/docs/ideas>, "/usr/local/bin/../lib/glibc-hwcaps/x86-64-v4/", 0x7fffeccdc330, 0) = -1 ENOENT (No such file or directory)',
    '15243 openat(AT_FDCWD</home/dev/ctxprune/docs/ideas>, "/usr/local/bin/../lib/glibc-hwcaps/x86-64-v3/libpthread.so.0", O_RDONLY|O_CLOEXEC) = -1 ENOENT (No such file or directory)',
    '15243 newfstatat(AT_FDCWD</home/dev/ctxprune/docs/ideas>, "/usr/local/bin/../lib/glibc-hwcaps/x86-64-v3/", 0x7fffeccdc330, 0) = -1 ENOENT (No such file or directory)',
    '15243 openat(AT_FDCWD</home/dev/ctxprune/docs/ideas>, "/usr/local/bin/../lib/glibc-hwcaps/x86-64-v2/libpthread.so.0", O_RDONLY|O_CLOEXEC) = -1 ENOENT (No such file or directory)',
    '15243 newfstatat(AT_FDCWD</home/dev/ctxprune/docs/ideas>, "/usr/local/bin/../lib/glibc-hwcaps/x86-64-v2/", 0x7fffeccdc330, 0) = -1 ENOENT (No such file or directory)',
    '15243 openat(AT_FDCWD</home/dev/ctxprune/docs/ideas>, "/usr/local/bin/../lib/libpthread.so.0", O_RDONLY|O_CLOEXEC) = -1 ENOENT (No such file or directory)',
    '15243 newfstatat(AT_FDCWD</home/dev/ctxprune/docs/ideas>, "/usr/local/bin/../lib/", {st_mode=S_IFDIR|0755, st_size=4096, ...}, 0) = 0',
    '15243 openat(AT_FDCWD</home/dev/ctxprune/docs/ideas>, "/etc/ld.so.cache", O_RDONLY|O_CLOEXEC) = 3</etc/ld.so.cache>',
    '15243 fstat(3</etc/ld.so.cache>, {st_mode=S_IFREG|0644, st_size=44439, ...}) = 0',
    '15243 mmap(NULL, 44439, PROT_READ, MAP_PRIVATE, 3</etc/ld.so.cache>, 0) = 0x7f5138d2e000',
    '15243 close(3</etc/ld.so.cache>)        = 0',
    '15243 openat(AT_FDCWD</home/dev/ctxprune/docs/ideas>, "/lib/x86_64-linux-gnu/libpthread.so.0", O_RDONLY|O_CLOEXEC) = 3</usr/lib/x86_64-linux-gnu/libpthread.so.0>',
    '15243 read(3</usr/lib/x86_64-linux-gnu/libpthread.so.0>, "\\177ELF\\2\\1\\1\\0\\0\\0\\0\\0\\0\\0\\0\\0\\3\\0>\\0\\1\\0\\0\\0\\0\\0\\0\\0\\0\\0\\0\\0"..., 832) = 832',
    '15243 fstat(3</usr/lib/x86_64-linux-gnu/libpthread.so.0>, {st_mode=S_IFREG|0644, st_size=14408, ...}) = 0',
    '15243 mmap(NULL, 16400, PROT_READ, MAP_PRIVATE|MAP_DENYWRITE, 3</usr/lib/x86_64-linux-gnu/libpthread.so.0>, 0) = 0x7f5138d29000',
    '15243 mmap(0x7f5138d2a000, 4096, PROT_READ|PROT_EXEC, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3</usr/lib/x86_64-linux-gnu/libpthread.so.0>, 0x1000) = 0x7f5138d2a000',
    '15243 mmap(0x7f5138d2b000, 4096, PROT_READ, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3</usr/lib/x86_64-linux-gnu/libpthread.so.0>, 0x2000) = 0x7f5138d2b000',
    '15243 mmap(0x7f5138d2c000, 8192, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3</usr/lib/x86_64-linux-gnu/libpthread.so.0>, 0x2000) = 0x7f5138d2c000',
    '15243 close(3</usr/lib/x86_64-linux-gnu/libpthread.so.0>) = 0',
    '15243 openat(AT_FDCWD</home/dev/ctxprune/docs/ideas>, "/usr/local/bin/../lib/libdl.so.2", O_RDONLY|O_CLOEXEC) = -1 ENOENT (No such file or directory)',
    '15243 openat(AT_FDCWD</home/dev/ctxprune/docs/ideas>, "/lib/x86_64-linux-gnu/libdl.so.2", O_RDONLY|O_CLOEXEC) = 3</usr/lib/x86_64-linux-gnu/libdl.so.2>',
    '15243 read(3</usr/lib/x86_64-linux-gnu/libdl.so.2>, "\\177ELF\\2\\1\\1\\0\\0\\0\\0\\0\\0\\0\\0\\0\\3\\0>\\0\\1\\0\\0\\0\\0\\0\\0\\0\\0\\0\\0\\0"..., 832) = 832',
    '15243 fstat(3</usr/lib/x86_64-linux-gnu/libdl.so.2>, {st_mode=S_IFREG|0644, st_size=14408, ...}) = 0',
    '15243 mmap(NULL, 16400, PROT_READ, MAP_PRIVATE|MAP_DENYWRITE, 3</usr/lib/x86_64-linux-gnu/libdl.so.2>, 0) = 0x7f5138d24000',
    '15243 mmap(0x7f5138d25000, 4096, PROT_READ|PROT_EXEC, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3</usr/lib/x86_64-linux-gnu/libdl.so.2>, 0x1000) = 0x7f5138d25000',
    '15243 mmap(0x7f5138d26000, 4096, PROT_READ, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3</usr/lib/x86_64-linux-gnu/libdl.so.2>, 0x2000) = 0x7f5138d26000',
    '15243 mmap(0x7f5138d27000, 8192, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3</usr/lib/x86_64-linux-gnu/libdl.so.2>, 0x2000) = 0x7f5138d27000',
    '15243 close(3</usr/lib/x86_64-linux-gnu/libdl.so.2>) = 0',
    '15243 openat(AT_FDCWD</home/dev/ctxprune/docs/ideas>, "/usr/local/bin/../lib/libutil.so.1", O_RDONLY|O_CLOEXEC) = -1 ENOENT (No such file or directory)',
    '15243 openat(AT_FDCWD</home/dev/ctxprune/docs/ideas>, "/lib/x86_64-linux-gnu/libutil.so.1", O_RDONLY|O_CLOEXEC) = 3</usr/lib/x86_64-linux-gnu/libutil.so.1>',
    '15243 close(3</usr/local/lib/python3.13/encodings>) = 0',
    '15243 stat("/usr/local/lib/python3.13/encodings/aliases.py", {st_mode=S_IFREG|0644, st_size=16011, ...}) = 0',
    '15243 stat("/usr/local/lib/python3.13/encodings/aliases.py", {st_mode=S_IFREG|0644, st_size=16011, ...}) = 0',
    '15243 openat(AT_FDCWD</home/dev/ctxprune/docs/ideas>, "/usr/local/lib/python3.13/encodings/__pycache__/aliases.cpython-313.pyc", O_RDONLY|O_CLOEXEC) = 3</usr/local/lib/python3.13/encodings/__pycache__/aliases.cpython-313.pyc>',
    '15243 fstat(3</usr/local/lib/python3.13/encodings/__pycache__/aliases.cpython-313.pyc>, {st_mode=S_IFREG|0644, st_size=12548, ...}) = 0',
    '15243 ioctl(3</usr/local/lib/python3.13/encodings/__pycache__/aliases.cpython-313.pyc>, TCGETS, 0x7fffeccdac00) = -1 ENOTTY (Inappropriate ioctl for device)',
    '15243 lseek(3</usr/local/lib/python3.13/encodings/__pycache__/aliases.cpython-313.pyc>, 0, SEEK_CUR) = 0',
    '15243 lseek(3</usr/local/lib/python3.13/encodings/__pycache__/aliases.cpython-313.pyc>, 0, SEEK_CUR) = 0',
    '15243 fstat(3</usr/local/lib/python3.13/encodings/__pycache__/aliases.cpython-313.pyc>, {st_mode=S_IFREG|0644, st_size=12548, ...}) = 0',
    '15243 read(3</usr/local/lib/python3.13/encodings/__pycache__/aliases.cpython-313.pyc>, "\\363\\r\\r\\n\\0\\0\\0\\0\\200\\0\\222e\\213>\\0\\0\\343\\0\\0\\0\\0\\0\\0\\0\\0\\0\\0\\0\\0\\16\\0\\0"..., 12549) = 12548',
    '15243 read(3</usr/local/lib/python3.13/encodings/__pycache__/aliases.cpython-313.pyc>, "", 1) = 0',
    '15243 close(3</usr/local/lib/python3.13/encodings/__pycache__/aliases.cpython-313.pyc>) = 0',
    '15243 stat("/usr/local/lib/python3.13/encodings", {st_mode=S_IFDIR|0755, st_size=4096, ...}) = 0',
    '15243 stat("/usr/local/lib/python3.13/encodings/utf_8.py", {st_mode=S_IFREG|0644, st_size=1005, ...}) = 0',
    '15243 stat("/usr/local/lib/python3.13/encodings/utf_8.py", {st_mode=S_IFREG|0644, st_size=1005, ...}) = 0',
    '15243 openat(AT_FDCWD</home/dev/ctxprune/docs/ideas>, "/usr/local/lib/python3.13/encodings/__pycache__/utf_8.cpython-313.pyc", O_RDONLY|O_CLOEXEC) = 3</usr/local/lib/python3.13/encodings/__pycache__/utf_8.cpython-313.pyc>',
    '15243 fstat(3</usr/local/lib/python3.13/encodings/__pycache__/utf_8.cpython-313.pyc>, {st_mode=S_IFREG|0644, st_size=2290, ...}) = 0',
    '15243 ioctl(3</usr/local/lib/python3.13/encodings/__pycache__/utf_8.cpython-313.pyc>, TCGETS, 0x7fffeccdba50) = -1 ENOTTY (Inappropriate ioctl for device)',
    '15243 lseek(3</usr/local/lib/python3.13/encodings/__pycache__/utf_8.cpython-313.pyc>, 0, SEEK_CUR) = 0',
    '15243 lseek(3</usr/local/lib/python3.13/encodings/__pycache__/utf_8.cpython-313.pyc>, 0, SEEK_CUR) = 0',
    '15243 fstat(3</usr/local/lib/python3.13/encodings/__pycache__/utf_8.cpython-313.pyc>, {st_mode=S_IFREG|0644, st_size=2290, ...}) = 0',
)

# real: pip's vendored rich 3-task Progress, Console(force_terminal=True, width=72) captured to a string
RICH_RAW = _txt(
    '\x1b[?25l\r\x1b[2K⠋ layer a3f1c2d9e8b7                        0%\r\x1b[2K⠋ layer a3f1c2d9e8b7                        0%',
    '⠋ layer 5c6d7e8f9a0b                        0%\r\x1b[2K\x1b[1A\x1b[2K⠋ layer a3f1c2d9e8b7                        0%',
    '⠋ layer 5c6d7e8f9a0b                        0%',
    '⠋ layer 11223344aabb                        0%\r\x1b[2K\x1b[1A\x1b[2K\x1b[1A\x1b[2K⠋ layer a3f1c2d9e8b7                        0%',
    '⠋ layer 5c6d7e8f9a0b                        0%',
    '⠋ layer 11223344aabb                        0%\r\x1b[2K\x1b[1A\x1b[2K\x1b[1A\x1b[2K⠋ layer a3f1c2d9e8b7 ━━━━                  20%',
    '⠋ layer 5c6d7e8f9a0b ━━━                   15%',
    '⠋ layer 11223344aabb ━━                    10%\r\x1b[2K\x1b[1A\x1b[2K\x1b[1A\x1b[2K⠋ layer a3f1c2d9e8b7 ━━━━━━━━              40%',
    '⠋ layer 5c6d7e8f9a0b ━━━━━━━               35%',
    '⠋ layer 11223344aabb ━━━━━━                30%\r\x1b[2K\x1b[1A\x1b[2K\x1b[1A\x1b[2K⠋ layer a3f1c2d9e8b7 ━━━━━━━━━━━━          60%',
    '⠋ layer 5c6d7e8f9a0b ━━━━━━━━━━━           55%',
    '⠋ layer 11223344aabb ━━━━━━━━━━            50%\r\x1b[2K\x1b[1A\x1b[2K\x1b[1A\x1b[2K⠋ layer a3f1c2d9e8b7 ━━━━━━━━━━━━━━━━      80%',
    '⠋ layer 5c6d7e8f9a0b ━━━━━━━━━━━━━━━       75%',
    '⠋ layer 11223344aabb ━━━━━━━━━━━━━━        70%\r\x1b[2K\x1b[1A\x1b[2K\x1b[1A\x1b[2K  layer a3f1c2d9e8b7 ━━━━━━━━━━━━━━━━━━━━ 100%',
    '⠋ layer 5c6d7e8f9a0b ━━━━━━━━━━━━━━━━━━━   95%',
    '⠋ layer 11223344aabb ━━━━━━━━━━━━━━━━━━    90%\r\x1b[2K\x1b[1A\x1b[2K\x1b[1A\x1b[2K  layer a3f1c2d9e8b7 ━━━━━━━━━━━━━━━━━━━━ 100%',
    '⠋ layer 5c6d7e8f9a0b ━━━━━━━━━━━━━━━━━━━   95%',
    '⠋ layer 11223344aabb ━━━━━━━━━━━━━━━━━━    90%',
    '\x1b[?25hdone',
)

# real: ps -eLf, header + PID 1 (9 threads) + kernel threads + a python3.13 process with 35 threads and a long argv + its child
PS_ELF = _txt(
    'UID        PID  PPID   LWP  C NLWP STIME TTY          TIME CMD',
    'root         1     0     1  0    9 17:16 ?        00:00:00 /process_api --firecracker-init --addr 0.0.0.0:2024 --max-ws-buffer-size 32768 --block-local-connections --control-vsock-port 5001 --listen-vsock-port 2024 --log-vsock-port 5002',
    'root         1     0    69  0    9 17:16 ?        00:00:00 /process_api --firecracker-init --addr 0.0.0.0:2024 --max-ws-buffer-size 32768 --block-local-connections --control-vsock-port 5001 --listen-vsock-port 2024 --log-vsock-port 5002',
    'root         1     0    70  0    9 17:16 ?        00:00:00 /process_api --firecracker-init --addr 0.0.0.0:2024 --max-ws-buffer-size 32768 --block-local-connections --control-vsock-port 5001 --listen-vsock-port 2024 --log-vsock-port 5002',
    'root         1     0    71  0    9 17:16 ?        00:00:00 /process_api --firecracker-init --addr 0.0.0.0:2024 --max-ws-buffer-size 32768 --block-local-connections --control-vsock-port 5001 --listen-vsock-port 2024 --log-vsock-port 5002',
    'root         1     0    72  0    9 17:16 ?        00:00:00 /process_api --firecracker-init --addr 0.0.0.0:2024 --max-ws-buffer-size 32768 --block-local-connections --control-vsock-port 5001 --listen-vsock-port 2024 --log-vsock-port 5002',
    'root         1     0    73  0    9 17:16 ?        00:00:00 /process_api --firecracker-init --addr 0.0.0.0:2024 --max-ws-buffer-size 32768 --block-local-connections --control-vsock-port 5001 --listen-vsock-port 2024 --log-vsock-port 5002',
    'root         1     0    74  0    9 17:16 ?        00:00:00 /process_api --firecracker-init --addr 0.0.0.0:2024 --max-ws-buffer-size 32768 --block-local-connections --control-vsock-port 5001 --listen-vsock-port 2024 --log-vsock-port 5002',
    'root         1     0    78  0    9 17:16 ?        00:00:00 /process_api --firecracker-init --addr 0.0.0.0:2024 --max-ws-buffer-size 32768 --block-local-connections --control-vsock-port 5001 --listen-vsock-port 2024 --log-vsock-port 5002',
    'root         1     0    81  0    9 17:16 ?        00:00:00 /process_api --firecracker-init --addr 0.0.0.0:2024 --max-ws-buffer-size 32768 --block-local-connections --control-vsock-port 5001 --listen-vsock-port 2024 --log-vsock-port 5002',
    'root         2     0     2  0    1 17:16 ?        00:00:00 [kthreadd]',
    'root         3     2     3  0    1 17:16 ?        00:00:00 [pool_workqueue_release]',
    'root         4     2     4  0    1 17:16 ?        00:00:00 [kworker/R-rcu_gp]',
    'root         5     2     5  0    1 17:16 ?        00:00:00 [kworker/R-sync_wq]',
    'root     31238 31237 31238  1   35 18:08 ?        00:00:00 python3 thr.py -Xms512m -Xmx2g -XX:+UseG1GC -XX:MaxGCPauseMillis=200 -Dspring.profiles.active=prod -Dfile.encoding=UTF-8 -cp /opt/shop/lib/shop-api-3.4.1.jar:/opt/shop/lib/spring-boot-3.3.2.jar:/opt/shop/lib/spring-web-6.1.11.jar:/opt/shop/lib/jackson-databind-2.17.2.jar:/opt/shop/lib/netty-handler-4.1.112.Final.jar com.example.shop.ShopApplication --server.port=8080',
    'root     31238 31237 31240  0   35 18:08 ?        00:00:00 python3 thr.py -Xms512m -Xmx2g -XX:+UseG1GC -XX:MaxGCPauseMillis=200 -Dspring.profiles.active=prod -Dfile.encoding=UTF-8 -cp /opt/shop/lib/shop-api-3.4.1.jar:/opt/shop/lib/spring-boot-3.3.2.jar:/opt/shop/lib/spring-web-6.1.11.jar:/opt/shop/lib/jackson-databind-2.17.2.jar:/opt/shop/lib/netty-handler-4.1.112.Final.jar com.example.shop.ShopApplication --server.port=8080',
    'root     31238 31237 31241  0   35 18:08 ?        00:00:00 python3 thr.py -Xms512m -Xmx2g -XX:+UseG1GC -XX:MaxGCPauseMillis=200 -Dspring.profiles.active=prod -Dfile.encoding=UTF-8 -cp /opt/shop/lib/shop-api-3.4.1.jar:/opt/shop/lib/spring-boot-3.3.2.jar:/opt/shop/lib/spring-web-6.1.11.jar:/opt/shop/lib/jackson-databind-2.17.2.jar:/opt/shop/lib/netty-handler-4.1.112.Final.jar com.example.shop.ShopApplication --server.port=8080',
    'root     31238 31237 31242  0   35 18:08 ?        00:00:00 python3 thr.py -Xms512m -Xmx2g -XX:+UseG1GC -XX:MaxGCPauseMillis=200 -Dspring.profiles.active=prod -Dfile.encoding=UTF-8 -cp /opt/shop/lib/shop-api-3.4.1.jar:/opt/shop/lib/spring-boot-3.3.2.jar:/opt/shop/lib/spring-web-6.1.11.jar:/opt/shop/lib/jackson-databind-2.17.2.jar:/opt/shop/lib/netty-handler-4.1.112.Final.jar com.example.shop.ShopApplication --server.port=8080',
    'root     31238 31237 31243  0   35 18:08 ?        00:00:00 python3 thr.py -Xms512m -Xmx2g -XX:+UseG1GC -XX:MaxGCPauseMillis=200 -Dspring.profiles.active=prod -Dfile.encoding=UTF-8 -cp /opt/shop/lib/shop-api-3.4.1.jar:/opt/shop/lib/spring-boot-3.3.2.jar:/opt/shop/lib/spring-web-6.1.11.jar:/opt/shop/lib/jackson-databind-2.17.2.jar:/opt/shop/lib/netty-handler-4.1.112.Final.jar com.example.shop.ShopApplication --server.port=8080',
    'root     31238 31237 31244  0   35 18:08 ?        00:00:00 python3 thr.py -Xms512m -Xmx2g -XX:+UseG1GC -XX:MaxGCPauseMillis=200 -Dspring.profiles.active=prod -Dfile.encoding=UTF-8 -cp /opt/shop/lib/shop-api-3.4.1.jar:/opt/shop/lib/spring-boot-3.3.2.jar:/opt/shop/lib/spring-web-6.1.11.jar:/opt/shop/lib/jackson-databind-2.17.2.jar:/opt/shop/lib/netty-handler-4.1.112.Final.jar com.example.shop.ShopApplication --server.port=8080',
    'root     31238 31237 31245  0   35 18:08 ?        00:00:00 python3 thr.py -Xms512m -Xmx2g -XX:+UseG1GC -XX:MaxGCPauseMillis=200 -Dspring.profiles.active=prod -Dfile.encoding=UTF-8 -cp /opt/shop/lib/shop-api-3.4.1.jar:/opt/shop/lib/spring-boot-3.3.2.jar:/opt/shop/lib/spring-web-6.1.11.jar:/opt/shop/lib/jackson-databind-2.17.2.jar:/opt/shop/lib/netty-handler-4.1.112.Final.jar com.example.shop.ShopApplication --server.port=8080',
    'root     31238 31237 31246  0   35 18:08 ?        00:00:00 python3 thr.py -Xms512m -Xmx2g -XX:+UseG1GC -XX:MaxGCPauseMillis=200 -Dspring.profiles.active=prod -Dfile.encoding=UTF-8 -cp /opt/shop/lib/shop-api-3.4.1.jar:/opt/shop/lib/spring-boot-3.3.2.jar:/opt/shop/lib/spring-web-6.1.11.jar:/opt/shop/lib/jackson-databind-2.17.2.jar:/opt/shop/lib/netty-handler-4.1.112.Final.jar com.example.shop.ShopApplication --server.port=8080',
    'root     31238 31237 31247  0   35 18:08 ?        00:00:00 python3 thr.py -Xms512m -Xmx2g -XX:+UseG1GC -XX:MaxGCPauseMillis=200 -Dspring.profiles.active=prod -Dfile.encoding=UTF-8 -cp /opt/shop/lib/shop-api-3.4.1.jar:/opt/shop/lib/spring-boot-3.3.2.jar:/opt/shop/lib/spring-web-6.1.11.jar:/opt/shop/lib/jackson-databind-2.17.2.jar:/opt/shop/lib/netty-handler-4.1.112.Final.jar com.example.shop.ShopApplication --server.port=8080',
    'root     31238 31237 31248  0   35 18:08 ?        00:00:00 python3 thr.py -Xms512m -Xmx2g -XX:+UseG1GC -XX:MaxGCPauseMillis=200 -Dspring.profiles.active=prod -Dfile.encoding=UTF-8 -cp /opt/shop/lib/shop-api-3.4.1.jar:/opt/shop/lib/spring-boot-3.3.2.jar:/opt/shop/lib/spring-web-6.1.11.jar:/opt/shop/lib/jackson-databind-2.17.2.jar:/opt/shop/lib/netty-handler-4.1.112.Final.jar com.example.shop.ShopApplication --server.port=8080',
    'root     31238 31237 31249  0   35 18:08 ?        00:00:00 python3 thr.py -Xms512m -Xmx2g -XX:+UseG1GC -XX:MaxGCPauseMillis=200 -Dspring.profiles.active=prod -Dfile.encoding=UTF-8 -cp /opt/shop/lib/shop-api-3.4.1.jar:/opt/shop/lib/spring-boot-3.3.2.jar:/opt/shop/lib/spring-web-6.1.11.jar:/opt/shop/lib/jackson-databind-2.17.2.jar:/opt/shop/lib/netty-handler-4.1.112.Final.jar com.example.shop.ShopApplication --server.port=8080',
    'root     31238 31237 31250  0   35 18:08 ?        00:00:00 python3 thr.py -Xms512m -Xmx2g -XX:+UseG1GC -XX:MaxGCPauseMillis=200 -Dspring.profiles.active=prod -Dfile.encoding=UTF-8 -cp /opt/shop/lib/shop-api-3.4.1.jar:/opt/shop/lib/spring-boot-3.3.2.jar:/opt/shop/lib/spring-web-6.1.11.jar:/opt/shop/lib/jackson-databind-2.17.2.jar:/opt/shop/lib/netty-handler-4.1.112.Final.jar com.example.shop.ShopApplication --server.port=8080',
    'root     31238 31237 31251  0   35 18:08 ?        00:00:00 python3 thr.py -Xms512m -Xmx2g -XX:+UseG1GC -XX:MaxGCPauseMillis=200 -Dspring.profiles.active=prod -Dfile.encoding=UTF-8 -cp /opt/shop/lib/shop-api-3.4.1.jar:/opt/shop/lib/spring-boot-3.3.2.jar:/opt/shop/lib/spring-web-6.1.11.jar:/opt/shop/lib/jackson-databind-2.17.2.jar:/opt/shop/lib/netty-handler-4.1.112.Final.jar com.example.shop.ShopApplication --server.port=8080',
    'root     31238 31237 31252  0   35 18:08 ?        00:00:00 python3 thr.py -Xms512m -Xmx2g -XX:+UseG1GC -XX:MaxGCPauseMillis=200 -Dspring.profiles.active=prod -Dfile.encoding=UTF-8 -cp /opt/shop/lib/shop-api-3.4.1.jar:/opt/shop/lib/spring-boot-3.3.2.jar:/opt/shop/lib/spring-web-6.1.11.jar:/opt/shop/lib/jackson-databind-2.17.2.jar:/opt/shop/lib/netty-handler-4.1.112.Final.jar com.example.shop.ShopApplication --server.port=8080',
    'root     31238 31237 31253  0   35 18:08 ?        00:00:00 python3 thr.py -Xms512m -Xmx2g -XX:+UseG1GC -XX:MaxGCPauseMillis=200 -Dspring.profiles.active=prod -Dfile.encoding=UTF-8 -cp /opt/shop/lib/shop-api-3.4.1.jar:/opt/shop/lib/spring-boot-3.3.2.jar:/opt/shop/lib/spring-web-6.1.11.jar:/opt/shop/lib/jackson-databind-2.17.2.jar:/opt/shop/lib/netty-handler-4.1.112.Final.jar com.example.shop.ShopApplication --server.port=8080',
    'root     31238 31237 31254  0   35 18:08 ?        00:00:00 python3 thr.py -Xms512m -Xmx2g -XX:+UseG1GC -XX:MaxGCPauseMillis=200 -Dspring.profiles.active=prod -Dfile.encoding=UTF-8 -cp /opt/shop/lib/shop-api-3.4.1.jar:/opt/shop/lib/spring-boot-3.3.2.jar:/opt/shop/lib/spring-web-6.1.11.jar:/opt/shop/lib/jackson-databind-2.17.2.jar:/opt/shop/lib/netty-handler-4.1.112.Final.jar com.example.shop.ShopApplication --server.port=8080',
    'root     31238 31237 31255  0   35 18:08 ?        00:00:00 python3 thr.py -Xms512m -Xmx2g -XX:+UseG1GC -XX:MaxGCPauseMillis=200 -Dspring.profiles.active=prod -Dfile.encoding=UTF-8 -cp /opt/shop/lib/shop-api-3.4.1.jar:/opt/shop/lib/spring-boot-3.3.2.jar:/opt/shop/lib/spring-web-6.1.11.jar:/opt/shop/lib/jackson-databind-2.17.2.jar:/opt/shop/lib/netty-handler-4.1.112.Final.jar com.example.shop.ShopApplication --server.port=8080',
    'root     31238 31237 31256  0   35 18:08 ?        00:00:00 python3 thr.py -Xms512m -Xmx2g -XX:+UseG1GC -XX:MaxGCPauseMillis=200 -Dspring.profiles.active=prod -Dfile.encoding=UTF-8 -cp /opt/shop/lib/shop-api-3.4.1.jar:/opt/shop/lib/spring-boot-3.3.2.jar:/opt/shop/lib/spring-web-6.1.11.jar:/opt/shop/lib/jackson-databind-2.17.2.jar:/opt/shop/lib/netty-handler-4.1.112.Final.jar com.example.shop.ShopApplication --server.port=8080',
    'root     31238 31237 31257  0   35 18:08 ?        00:00:00 python3 thr.py -Xms512m -Xmx2g -XX:+UseG1GC -XX:MaxGCPauseMillis=200 -Dspring.profiles.active=prod -Dfile.encoding=UTF-8 -cp /opt/shop/lib/shop-api-3.4.1.jar:/opt/shop/lib/spring-boot-3.3.2.jar:/opt/shop/lib/spring-web-6.1.11.jar:/opt/shop/lib/jackson-databind-2.17.2.jar:/opt/shop/lib/netty-handler-4.1.112.Final.jar com.example.shop.ShopApplication --server.port=8080',
    'root     31238 31237 31258  0   35 18:08 ?        00:00:00 python3 thr.py -Xms512m -Xmx2g -XX:+UseG1GC -XX:MaxGCPauseMillis=200 -Dspring.profiles.active=prod -Dfile.encoding=UTF-8 -cp /opt/shop/lib/shop-api-3.4.1.jar:/opt/shop/lib/spring-boot-3.3.2.jar:/opt/shop/lib/spring-web-6.1.11.jar:/opt/shop/lib/jackson-databind-2.17.2.jar:/opt/shop/lib/netty-handler-4.1.112.Final.jar com.example.shop.ShopApplication --server.port=8080',
    'root     31238 31237 31259  0   35 18:08 ?        00:00:00 python3 thr.py -Xms512m -Xmx2g -XX:+UseG1GC -XX:MaxGCPauseMillis=200 -Dspring.profiles.active=prod -Dfile.encoding=UTF-8 -cp /opt/shop/lib/shop-api-3.4.1.jar:/opt/shop/lib/spring-boot-3.3.2.jar:/opt/shop/lib/spring-web-6.1.11.jar:/opt/shop/lib/jackson-databind-2.17.2.jar:/opt/shop/lib/netty-handler-4.1.112.Final.jar com.example.shop.ShopApplication --server.port=8080',
    'root     31238 31237 31260  0   35 18:08 ?        00:00:00 python3 thr.py -Xms512m -Xmx2g -XX:+UseG1GC -XX:MaxGCPauseMillis=200 -Dspring.profiles.active=prod -Dfile.encoding=UTF-8 -cp /opt/shop/lib/shop-api-3.4.1.jar:/opt/shop/lib/spring-boot-3.3.2.jar:/opt/shop/lib/spring-web-6.1.11.jar:/opt/shop/lib/jackson-databind-2.17.2.jar:/opt/shop/lib/netty-handler-4.1.112.Final.jar com.example.shop.ShopApplication --server.port=8080',
    'root     31238 31237 31261  0   35 18:08 ?        00:00:00 python3 thr.py -Xms512m -Xmx2g -XX:+UseG1GC -XX:MaxGCPauseMillis=200 -Dspring.profiles.active=prod -Dfile.encoding=UTF-8 -cp /opt/shop/lib/shop-api-3.4.1.jar:/opt/shop/lib/spring-boot-3.3.2.jar:/opt/shop/lib/spring-web-6.1.11.jar:/opt/shop/lib/jackson-databind-2.17.2.jar:/opt/shop/lib/netty-handler-4.1.112.Final.jar com.example.shop.ShopApplication --server.port=8080',
    'root     31238 31237 31262  0   35 18:08 ?        00:00:00 python3 thr.py -Xms512m -Xmx2g -XX:+UseG1GC -XX:MaxGCPauseMillis=200 -Dspring.profiles.active=prod -Dfile.encoding=UTF-8 -cp /opt/shop/lib/shop-api-3.4.1.jar:/opt/shop/lib/spring-boot-3.3.2.jar:/opt/shop/lib/spring-web-6.1.11.jar:/opt/shop/lib/jackson-databind-2.17.2.jar:/opt/shop/lib/netty-handler-4.1.112.Final.jar com.example.shop.ShopApplication --server.port=8080',
    'root     31238 31237 31263  0   35 18:08 ?        00:00:00 python3 thr.py -Xms512m -Xmx2g -XX:+UseG1GC -XX:MaxGCPauseMillis=200 -Dspring.profiles.active=prod -Dfile.encoding=UTF-8 -cp /opt/shop/lib/shop-api-3.4.1.jar:/opt/shop/lib/spring-boot-3.3.2.jar:/opt/shop/lib/spring-web-6.1.11.jar:/opt/shop/lib/jackson-databind-2.17.2.jar:/opt/shop/lib/netty-handler-4.1.112.Final.jar com.example.shop.ShopApplication --server.port=8080',
    'root     31238 31237 31264  0   35 18:08 ?        00:00:00 python3 thr.py -Xms512m -Xmx2g -XX:+UseG1GC -XX:MaxGCPauseMillis=200 -Dspring.profiles.active=prod -Dfile.encoding=UTF-8 -cp /opt/shop/lib/shop-api-3.4.1.jar:/opt/shop/lib/spring-boot-3.3.2.jar:/opt/shop/lib/spring-web-6.1.11.jar:/opt/shop/lib/jackson-databind-2.17.2.jar:/opt/shop/lib/netty-handler-4.1.112.Final.jar com.example.shop.ShopApplication --server.port=8080',
    'root     31238 31237 31265  0   35 18:08 ?        00:00:00 python3 thr.py -Xms512m -Xmx2g -XX:+UseG1GC -XX:MaxGCPauseMillis=200 -Dspring.profiles.active=prod -Dfile.encoding=UTF-8 -cp /opt/shop/lib/shop-api-3.4.1.jar:/opt/shop/lib/spring-boot-3.3.2.jar:/opt/shop/lib/spring-web-6.1.11.jar:/opt/shop/lib/jackson-databind-2.17.2.jar:/opt/shop/lib/netty-handler-4.1.112.Final.jar com.example.shop.ShopApplication --server.port=8080',
    'root     31238 31237 31266  0   35 18:08 ?        00:00:00 python3 thr.py -Xms512m -Xmx2g -XX:+UseG1GC -XX:MaxGCPauseMillis=200 -Dspring.profiles.active=prod -Dfile.encoding=UTF-8 -cp /opt/shop/lib/shop-api-3.4.1.jar:/opt/shop/lib/spring-boot-3.3.2.jar:/opt/shop/lib/spring-web-6.1.11.jar:/opt/shop/lib/jackson-databind-2.17.2.jar:/opt/shop/lib/netty-handler-4.1.112.Final.jar com.example.shop.ShopApplication --server.port=8080',
    'root     31238 31237 31267  0   35 18:08 ?        00:00:00 python3 thr.py -Xms512m -Xmx2g -XX:+UseG1GC -XX:MaxGCPauseMillis=200 -Dspring.profiles.active=prod -Dfile.encoding=UTF-8 -cp /opt/shop/lib/shop-api-3.4.1.jar:/opt/shop/lib/spring-boot-3.3.2.jar:/opt/shop/lib/spring-web-6.1.11.jar:/opt/shop/lib/jackson-databind-2.17.2.jar:/opt/shop/lib/netty-handler-4.1.112.Final.jar com.example.shop.ShopApplication --server.port=8080',
    'root     31238 31237 31268  0   35 18:08 ?        00:00:00 python3 thr.py -Xms512m -Xmx2g -XX:+UseG1GC -XX:MaxGCPauseMillis=200 -Dspring.profiles.active=prod -Dfile.encoding=UTF-8 -cp /opt/shop/lib/shop-api-3.4.1.jar:/opt/shop/lib/spring-boot-3.3.2.jar:/opt/shop/lib/spring-web-6.1.11.jar:/opt/shop/lib/jackson-databind-2.17.2.jar:/opt/shop/lib/netty-handler-4.1.112.Final.jar com.example.shop.ShopApplication --server.port=8080',
    'root     31238 31237 31269  0   35 18:08 ?        00:00:00 python3 thr.py -Xms512m -Xmx2g -XX:+UseG1GC -XX:MaxGCPauseMillis=200 -Dspring.profiles.active=prod -Dfile.encoding=UTF-8 -cp /opt/shop/lib/shop-api-3.4.1.jar:/opt/shop/lib/spring-boot-3.3.2.jar:/opt/shop/lib/spring-web-6.1.11.jar:/opt/shop/lib/jackson-databind-2.17.2.jar:/opt/shop/lib/netty-handler-4.1.112.Final.jar com.example.shop.ShopApplication --server.port=8080',
    'root     31238 31237 31270  0   35 18:08 ?        00:00:00 python3 thr.py -Xms512m -Xmx2g -XX:+UseG1GC -XX:MaxGCPauseMillis=200 -Dspring.profiles.active=prod -Dfile.encoding=UTF-8 -cp /opt/shop/lib/shop-api-3.4.1.jar:/opt/shop/lib/spring-boot-3.3.2.jar:/opt/shop/lib/spring-web-6.1.11.jar:/opt/shop/lib/jackson-databind-2.17.2.jar:/opt/shop/lib/netty-handler-4.1.112.Final.jar com.example.shop.ShopApplication --server.port=8080',
    'root     31238 31237 31271  0   35 18:08 ?        00:00:00 python3 thr.py -Xms512m -Xmx2g -XX:+UseG1GC -XX:MaxGCPauseMillis=200 -Dspring.profiles.active=prod -Dfile.encoding=UTF-8 -cp /opt/shop/lib/shop-api-3.4.1.jar:/opt/shop/lib/spring-boot-3.3.2.jar:/opt/shop/lib/spring-web-6.1.11.jar:/opt/shop/lib/jackson-databind-2.17.2.jar:/opt/shop/lib/netty-handler-4.1.112.Final.jar com.example.shop.ShopApplication --server.port=8080',
    'root     31238 31237 31272  0   35 18:08 ?        00:00:00 python3 thr.py -Xms512m -Xmx2g -XX:+UseG1GC -XX:MaxGCPauseMillis=200 -Dspring.profiles.active=prod -Dfile.encoding=UTF-8 -cp /opt/shop/lib/shop-api-3.4.1.jar:/opt/shop/lib/spring-boot-3.3.2.jar:/opt/shop/lib/spring-web-6.1.11.jar:/opt/shop/lib/jackson-databind-2.17.2.jar:/opt/shop/lib/netty-handler-4.1.112.Final.jar com.example.shop.ShopApplication --server.port=8080',
    'root     31238 31237 31273  0   35 18:08 ?        00:00:00 python3 thr.py -Xms512m -Xmx2g -XX:+UseG1GC -XX:MaxGCPauseMillis=200 -Dspring.profiles.active=prod -Dfile.encoding=UTF-8 -cp /opt/shop/lib/shop-api-3.4.1.jar:/opt/shop/lib/spring-boot-3.3.2.jar:/opt/shop/lib/spring-web-6.1.11.jar:/opt/shop/lib/jackson-databind-2.17.2.jar:/opt/shop/lib/netty-handler-4.1.112.Final.jar com.example.shop.ShopApplication --server.port=8080',
    'root     31274 31238 31274  0    1 18:08 ?        00:00:00 sleep 8',
)

# real: ps -ef | head -6
PS_EF_SMALL = _txt(
    'UID        PID  PPID  C STIME TTY          TIME CMD',
    'root         1     0  0 17:16 ?        00:00:01 /process_api --firecracker-init --addr 0.0.0.0:2024 --max-ws-buffer-size 32768 --block-local-connections --control-vsock-port 5001 --listen-vsock-port 2024 --log-vsock-port 5002',
    'root         2     0  0 17:16 ?        00:00:00 [kthreadd]',
    'root         3     2  0 17:16 ?        00:00:00 [pool_workqueue_release]',
    'root         4     2  0 17:16 ?        00:00:00 [kworker/R-rcu_gp]',
    'root         5     2  0 17:16 ?        00:00:00 [kworker/R-sync_wq]',
)

# ---------------------------------------------------------------------------------------------------------------
# reconstructed or synthesised fixtures (the tools are not installed here, or no real multi-line redraw was at hand)

def _frames_redraw(blocks, hide=True):
    """Synthetic: the cursor-up + erase-line redraw loop that rich, docker compose and ora use. Each block is a list
    of lines; every refresh moves up over the previous block, erases each line and rewrites it. Modelled on the real
    rich capture above, not captured from docker compose (reconstructed from memory)."""
    out = "\x1b[?25l" if hide else ""
    prev = 0
    for i, block in enumerate(blocks):
        if prev:
            out += f"\x1b[{prev}A"
        for line in block:
            out += f"\x1b[2K\r{line}\n"
        prev = len(block)
    return out + ("\x1b[?25h" if hide else "")


def _compose_blocks():
    names = ["db", "cache", "web", "worker"]
    glyph = "⠋⠙⠹⠸⠼⠴⠦⠧"
    blocks = []
    states = [["Creating", "Creating", "Waiting", "Waiting"], ["Created", "Creating", "Waiting", "Waiting"],
              ["Starting", "Created", "Creating", "Waiting"], ["Started", "Starting", "Created", "Creating"],
              ["Healthy", "Started", "Starting", "Created"], ["Healthy", "Healthy", "Started", "Starting"],
              ["Healthy", "Healthy", "Healthy", "Started"]]
    for f, st in enumerate(states):
        done = sum(s in ("Healthy", "Started") for s in st)
        block = [f"[+] Running {done}/4"]
        for n, s in zip(names, st):
            mark = "✔" if s in ("Healthy", "Started") else glyph[(f + len(n)) % 8]
            block.append(f" {mark} Container app-{n}-1  {s:<9} {f * 0.4 + 0.1:.1f}s")
        blocks.append(block)
    return blocks


COMPOSE_TTY = _frames_redraw(_compose_blocks())  # synthetic, 7 refreshes of a 4-service block
NPM_SPINNER = ("\x1b[?25l" + "".join(f"\x1b[1G\x1b[0K{g} reify:ms: timing reifyNode:node_modules/ms Completed in {i * 31}ms"
                                     for i, g in enumerate("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏⠋⠙")) +
               "\x1b[1G\x1b[0K\x1b[?25h\nadded 14 packages, and audited 15 packages in 2s\n\nfound 0 vulnerabilities\n")  # synthetic
COLOURED_NO_CURSOR = "\x1b[1;32mPASS\x1b[0m tests/test_a.py\n\x1b[1;31mFAIL\x1b[0m tests/test_b.py\n\x1b]0;pytest\x07done\n"
CURSOR_ADVERSARIAL = [
    ("truncated escape at the end", COMPOSE_TTY[:230] + "\x1b[2K\x1b[1"),
    ("absolute cursor addressing (full-screen app) must be left alone", "\x1b[2J\x1b[Hhtop - 17:16\n\x1b[3;1HPID USER\n\x1b[1A\x1b[2K"),
    ("alternate screen", "\x1b[?1049h\x1b[2Jhello\x1b[1A\x1b[2K\x1b[?1049l"),
    ("double-width characters", "\x1b[2K✅ 完了 done\n\x1b[1A\x1b[2K✅ 完了 again\n"),
    ("absurd cursor-forward count", "ab\x1b[999999999Ccd\x1b[1Aef\n"),
    ("stray C0 byte", "one\n\x1b[1A\x1b[2Ktwo\x0e\n"),
    ("exercise: erase below after cursor-up", "keep\nold1\nold2\n\x1b[2A\x1b[Jnew\n"),
    ("exercise: erase to start of line, cursor left", "abcdefgh\x1b[3D\x1b[1KXY\nsecond\x1b[4D\x1b[0Kend\n"),
    ("exercise: forward, column, next/previous line, backspace", "a-b\x1b[4Cx\x1b[1Gy\n\x1b[1Ez\x1b[1Fw\x08v\x1b[3Cq\x1b[2Bk\n\x1b[5Gm\x1b[2Fn"),
    ("exercise: erase whole line and overwrite a longer line with a shorter one", "long line here\rshort\n\x1b[1A\x1b[2Kfresh\n\x1b[?2004h\x1b(B\x1b=ok\x1b[?2004l\n"),
    ("grep --color output with a tab in the line", "\x1b[35m\x1b[Kmain.c\x1b[m\x1b[K\x1b[36m\x1b[K:\x1b[m\x1b[K\tint x;\n"),
]


SS_LTNP = _txt(  # reconstructed from memory (iproute2 6.x); the fd differs between the IPv4 and IPv6 sockets of one daemon
    "State   Recv-Q  Send-Q   Local Address:Port    Peer Address:Port  Process",
    'LISTEN  0       4096     127.0.0.53%lo:53           0.0.0.0:*      users:(("systemd-resolve",pid=601,fd=14))',
    'LISTEN  0       128            0.0.0.0:22           0.0.0.0:*      users:(("sshd",pid=789,fd=3))',
    'LISTEN  0       128               [::]:22              [::]:*      users:(("sshd",pid=789,fd=4))',
    'LISTEN  0       511            0.0.0.0:80           0.0.0.0:*      users:(("nginx",pid=1021,fd=6),("nginx",pid=1020,fd=6),("nginx",pid=1019,fd=6))',
    'LISTEN  0       511               [::]:80              [::]:*      users:(("nginx",pid=1021,fd=7),("nginx",pid=1020,fd=7),("nginx",pid=1019,fd=7))',
    'LISTEN  0       244          127.0.0.1:5432         0.0.0.0:*      users:(("postgres",pid=1104,fd=6))',
    'LISTEN  0       244              [::1]:5432            [::]:*      users:(("postgres",pid=1104,fd=7))',
    'LISTEN  0       511          127.0.0.1:6379         0.0.0.0:*      users:(("redis-server",pid=1198,fd=6))',
    'LISTEN  0       5            127.0.0.1:631          0.0.0.0:*      users:(("cupsd",pid=702,fd=7))',
    'LISTEN  0       5                [::1]:631             [::]:*      users:(("cupsd",pid=702,fd=6))',
    'LISTEN  0       511                  *:3000               *:*      users:(("node",pid=2210,fd=19))',
    'LISTEN  0       4096         127.0.0.1:8125         0.0.0.0:*      users:(("netdata",pid=1311,fd=44))',
    'LISTEN  0       4096           0.0.0.0:9100         0.0.0.0:*      users:(("node_exporter",pid=1422,fd=3))',
    'LISTEN  0       4096              [::]:9100            [::]:*      users:(("node_exporter",pid=1422,fd=4))',
)
SS_TULN = _txt(  # reconstructed from memory: no process column, so the twin rows are identical but for the address family
    "Netid State  Recv-Q Send-Q  Local Address:Port  Peer Address:Port Process",
    "udp   UNCONN 0      0       127.0.0.53%lo:53         0.0.0.0:*",
    "udp   UNCONN 0      0             0.0.0.0:68         0.0.0.0:*",
    "udp   UNCONN 0      0             0.0.0.0:5353       0.0.0.0:*",
    "udp   UNCONN 0      0                [::]:5353          [::]:*",
    "udp   UNCONN 0      0             0.0.0.0:631        0.0.0.0:*",
    "tcp   LISTEN 0      4096    127.0.0.53%lo:53         0.0.0.0:*",
    "tcp   LISTEN 0      128           0.0.0.0:22         0.0.0.0:*",
    "tcp   LISTEN 0      128              [::]:22            [::]:*",
    "tcp   LISTEN 0      4096          0.0.0.0:111        0.0.0.0:*",
    "tcp   LISTEN 0      4096             [::]:111           [::]:*",
    "tcp   LISTEN 0      4096          0.0.0.0:2049       0.0.0.0:*",
    "tcp   LISTEN 0      4096             [::]:2049          [::]:*",
)
SS_NO_TWINS = _txt(
    "State   Recv-Q  Send-Q   Local Address:Port    Peer Address:Port  Process",
    'LISTEN  0       4096     127.0.0.53%lo:53           0.0.0.0:*      users:(("systemd-resolve",pid=601,fd=14))',
    'LISTEN  0       128            0.0.0.0:22           0.0.0.0:*      users:(("sshd",pid=789,fd=3))',
    'LISTEN  0       128               [::]:2222            [::]:*      users:(("sshd",pid=790,fd=4))',
    'ESTAB   0       0           10.0.0.5:22         10.0.0.9:51234    users:(("sshd",pid=1500,fd=4))',
)

IPCONFIG = "\n".join([  # reconstructed from memory (Windows 11, English); dot leaders are part of the localized strings
    "",
    "Windows IP Configuration",
    "",
    "   Host Name . . . . . . . . . . . . : DESKTOP-4F2K9QD",
    "   Primary Dns Suffix  . . . . . . . : ",
    "   Node Type . . . . . . . . . . . . : Hybrid",
    "   IP Routing Enabled. . . . . . . . : No",
    "   WINS Proxy Enabled. . . . . . . . : No",
    "   DNS Suffix Search List. . . . . . : lan",
    "",
    "Ethernet adapter Ethernet:",
    "",
    "   Connection-specific DNS Suffix  . : lan",
    "   Description . . . . . . . . . . . : Intel(R) Ethernet Connection (7) I219-V",
    "   Physical Address. . . . . . . . . : 04-92-26-5A-1C-77",
    "   DHCP Enabled. . . . . . . . . . . : Yes",
    "   Autoconfiguration Enabled . . . . : Yes",
    "   Link-local IPv6 Address . . . . . : fe80::a1c3:5f2e:77d0:19b4%12(Preferred) ",
    "   IPv4 Address. . . . . . . . . . . : 192.168.1.42(Preferred) ",
    "   Subnet Mask . . . . . . . . . . . : 255.255.255.0",
    "   Lease Obtained. . . . . . . . . . : Tuesday, November 3, 2026 8:01:12 AM",
    "   Lease Expires . . . . . . . . . . : Wednesday, November 4, 2026 8:01:12 AM",
    "   Default Gateway . . . . . . . . . : fe80::1%12",
    "                                       192.168.1.1",
    "   DHCP Server . . . . . . . . . . . : 192.168.1.1",
    "   DHCPv6 IAID . . . . . . . . . . . : 67146278",
    "   DHCPv6 Client DUID. . . . . . . . : 00-01-00-01-2D-4A-6B-C2-04-92-26-5A-1C-77",
    "   DNS Servers . . . . . . . . . . . : 192.168.1.1",
    "                                       1.1.1.1",
    "   NetBIOS over Tcpip. . . . . . . . : Enabled",
    "",
    "Wireless LAN adapter Wi-Fi:",
    "",
    "   Media State . . . . . . . . . . . : Media disconnected",
    "   Connection-specific DNS Suffix  . : ",
    "   Description . . . . . . . . . . . : Intel(R) Wi-Fi 6 AX201 160MHz",
    "   Physical Address. . . . . . . . . : A4-C3-F0-12-8B-3E",
    "   DHCP Enabled. . . . . . . . . . . : Yes",
    "   Autoconfiguration Enabled . . . . : Yes",
    "",
    "Ethernet adapter vEthernet (WSL (Hyper-V firewall)):",
    "",
    "   Connection-specific DNS Suffix  . : ",
    "   Description . . . . . . . . . . . : Hyper-V Virtual Ethernet Adapter",
    "   Physical Address. . . . . . . . . : 00-15-5D-4E-21-9A",
    "   DHCP Enabled. . . . . . . . . . . : No",
    "   Autoconfiguration Enabled . . . . : Yes",
    "   IPv4 Address. . . . . . . . . . . : 172.28.16.1(Preferred)",
    "   Subnet Mask . . . . . . . . . . . : 255.255.240.0",
    "   Default Gateway . . . . . . . . . : ",
    "   NetBIOS over Tcpip. . . . . . . . : Enabled",
    "",
])
IPCONFIG_CRLF = IPCONFIG.replace("\n", "\r\n")
IPCONFIG_PLAIN = _txt(  # a key-value listing that never had dot leaders (systeminfo style, space padded): untouched
    "Host Name:                 DESKTOP-4F2K9QD",
    "OS Name:                   Microsoft Windows 11 Pro",
    "OS Version:                10.0.26100 N/A Build 26100",
    "System Boot Time:          11/3/2026, 7:58:02 AM",
)

IP_ADDR = _txt(  # reconstructed from memory (iproute2 6.x): a Docker host with two veth ends, a DHCP lease and a /32
    "1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN group default qlen 1000",
    "    link/loopback 00:00:00:00:00:00 brd 00:00:00:00:00:00",
    "    inet 127.0.0.1/8 scope host lo",
    "       valid_lft forever preferred_lft forever",
    "    inet6 ::1/128 scope host noprefixroute ",
    "       valid_lft forever preferred_lft forever",
    "2: ens5: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 9001 qdisc mq state UP group default qlen 1000",
    "    link/ether 0a:1b:2c:3d:4e:5f brd ff:ff:ff:ff:ff:ff",
    "    altname enp0s5",
    "    inet 10.0.12.34/20 metric 100 brd 10.0.15.255 scope global dynamic ens5",
    "       valid_lft 3012sec preferred_lft 3012sec",
    "    inet6 fe80::81b:2cff:fe3d:4e5f/64 scope link ",
    "       valid_lft forever preferred_lft forever",
    "3: docker0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc noqueue state UP group default ",
    "    link/ether 02:42:a1:b2:c3:d4 brd ff:ff:ff:ff:ff:ff",
    "    inet 172.17.0.1/16 brd 172.17.255.255 scope global docker0",
    "       valid_lft forever preferred_lft forever",
    "    inet6 fe80::42:a1ff:feb2:c3d4/64 scope link ",
    "       valid_lft forever preferred_lft forever",
    "5: vethd3f1a2b@if4: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc noqueue master docker0 state UP group default ",
    "    link/ether 6e:9a:0b:5c:7d:11 brd ff:ff:ff:ff:ff:ff link-netnsid 0",
    "    inet6 fe80::6c9a:bff:fe5c:7d11/64 scope link ",
    "       valid_lft forever preferred_lft forever",
    "7: tun0: <POINTOPOINT,MULTICAST,NOARP,UP,LOWER_UP> mtu 1500 qdisc fq_codel state UNKNOWN group default qlen 500",
    "    link/none ",
    "    inet 10.8.0.6 peer 10.8.0.5/32 scope global tun0",
    "       valid_lft forever preferred_lft forever",
    "8: br-9f8e7d6c5b4a: <NO-CARRIER,BROADCAST,MULTICAST,UP> mtu 1500 qdisc noqueue state DOWN group default ",
    "    link/ether 02:42:0d:0e:0a:0d brd ff:ff:ff:ff:ff:ff",
    "    inet 172.18.0.1/16 brd 172.18.255.255 scope global br-9f8e7d6c5b4a",
    "       valid_lft forever preferred_lft forever",
)
IP_ADDR_BRIEF = _txt(
    "lo               UNKNOWN        127.0.0.1/8 ::1/128 ",
    "ens5             UP             10.0.12.34/20 fe80::81b:2cff:fe3d:4e5f/64 ",
    "docker0          UP             172.17.0.1/16 fe80::42:a1ff:feb2:c3d4/64 ",
)

# an edited copy of the real STATUS_PY with a 128-CPU, 2-node machine's masks (the kernel prints one 8-digit group per 32 bits)
STATUS_BIG = STATUS_PY.replace("Cpus_allowed:\tf\n", "Cpus_allowed:\tffffffff,ffffffff,ffffffff,ffffffff\n").replace(
    "Cpus_allowed_list:\t0-3\n", "Cpus_allowed_list:\t0-127\n").replace("Mems_allowed_list:\t0\n", "Mems_allowed_list:\t0-1\n").replace(
    ",00000001\n", ",00000003\n")


# ===============================================================================================================
# #1454 Replay cursor-up and erase-line redraws to the final screen
#
# docker compose up, docker pull, pip, rich Live, ora and npm redraw a block in place with CSI A (cursor up), CSI K
# (erase line) and CR. Stripping the escapes (#9) or collapsing CR frames (#10) leaves every frame, one after the
# other. A terminal shows only the last one. The prototype replays the text on a character grid and prints what the
# screen would show. It is a bail-out design: anything it cannot model exactly (tabs, absolute cursor addressing,
# alternate screen, double-width characters, an unknown or truncated escape, other control bytes) returns the text
# untouched. A line feed is taken as CR+LF (the tty driver's ONLCR), which is what a pty capture contains.

_CUR_TRIGGER = re.compile(r"\x1b\[[0-9;]*[ABCDEFGJK]")
_CUR_TOKEN = re.compile(
    r"\x1b\[(?P<p>[0-9;]*)(?P<f>[A-Za-z])"          # CSI with numeric parameters
    r"|\x1b\[\?(?P<pp>[0-9;]*)(?P<pf>[hl])"         # DEC private mode set / reset
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"           # OSC ... BEL or ST
    r"|\x1b[()][0-9A-Za-z]"                         # charset designation
    r"|\x1b[=>]"                                    # keypad mode
    r"|(?P<t>[^\x00-\x1f\x7f-\x9f]+)"               # printable run
    r"|(?P<c>[\n\r\b\a])"                            # the control characters a terminal acts on (a tab is not modelled)
)
_HARMLESS_PRIVATE = {"25", "7", "2004", "12"}       # cursor visibility, autowrap, bracketed paste, cursor blink


def _narrow(text: str) -> bool:
    """Every character occupies exactly one cell (no double-width, combining or zero-width characters)."""
    return all(unicodedata.east_asian_width(ch) not in "WF" and unicodedata.category(ch) not in ("Mn", "Me", "Cf") for ch in text)


def replay_cursor(text: str) -> str:
    if not _CUR_TRIGGER.search(text) or not _narrow(text):
        return text
    rows: list[list[str]] = [[]]
    r = c = pos = 0
    while pos < len(text):
        m = _CUR_TOKEN.match(text, pos)
        if m is None:
            return text
        pos = m.end()
        if m.group("t") is not None:
            row = rows[r]
            for ch in m.group("t"):
                if c > len(row):
                    row.extend(" " * (c - len(row)))
                if c < len(row):
                    row[c] = ch
                else:
                    row.append(ch)
                c += 1
        elif m.group("c") is not None:
            ch = m.group("c")
            if ch == "\n":
                r, c = r + 1, 0
            elif ch == "\r":
                c = 0
            elif ch == "\b":
                c = max(0, c - 1)
        elif m.group("pf") is not None:
            if any(p not in _HARMLESS_PRIVATE for p in m.group("pp").split(";")):
                return text
        elif m.group("f") is not None:
            f, p = m.group("f"), m.group("p")
            if f == "m":
                continue
            if ";" in p or len(p) > 4:
                return text
            k = int(p) if p else 0
            if f in "ABCDEFG":
                k = k or 1
                if f == "A":
                    r = max(0, r - k)
                elif f == "B":
                    r += k
                elif f == "C":
                    c += k
                elif f == "D":
                    c = max(0, c - k)
                elif f == "E":
                    r, c = r + k, 0
                elif f == "F":
                    r, c = max(0, r - k), 0
                else:
                    c = k - 1
            elif f == "K" and k in (0, 1, 2):
                if k == 0:
                    del rows[r][c:]
                elif k == 1:
                    row = rows[r]
                    for i in range(min(c + 1, len(row))):
                        row[i] = " "
                else:
                    rows[r].clear()
            elif f == "J" and k == 0:
                del rows[r][c:]
                del rows[r + 1:]
            else:
                return text
        # else: an OSC, charset or keypad sequence: no visible effect
        if r > 4000 or c > 4000:
            return text
        while len(rows) <= r:
            rows.append([])
    out = "\n".join("".join(row).rstrip(" ") for row in rows)
    return out if len(out) < len(text) else text


def _grid_render(text: str):
    """Independent oracle: a sparse {(row, col): char} grid driven by a character-at-a-time state machine.
    Returns the rendered text, or None when the text uses something outside the supported subset."""
    cells: dict[tuple[int, int], str] = {}
    r = c = last = i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\x1b":
            nx = text[i + 1] if i + 1 < n else ""
            if nx == "[":
                j = i + 2
                priv = text[j:j + 1] == "?"
                j += priv
                k0 = j
                while j < n and text[j] in "0123456789;":
                    j += 1
                if j >= n or not ("@" <= text[j] <= "~"):
                    return None
                params, fin = text[k0:j], text[j]
                i = j + 1
                if priv:
                    if fin not in "hl" or any(p not in _HARMLESS_PRIVATE for p in params.split(";")):
                        return None
                    continue
                if fin == "m":
                    continue
                if ";" in params or len(params) > 4:
                    return None
                k = int(params) if params else 0
                if fin in "ABCDEFG":
                    k = k or 1
                    r = {"A": max(0, r - k), "F": max(0, r - k), "B": r + k, "E": r + k}.get(fin, r)
                    c = {"C": c + k, "D": max(0, c - k), "E": 0, "F": 0, "G": k - 1}.get(fin, c)
                elif fin == "K" and k in (0, 1, 2):
                    for (rr, cc) in list(cells):
                        if rr == r and (k == 2 or (k == 0 and cc >= c) or (k == 1 and cc <= c)):
                            del cells[(rr, cc)]
                elif fin == "J" and k == 0:
                    for (rr, cc) in list(cells):
                        if rr > r or (rr == r and cc >= c):
                            del cells[(rr, cc)]
                    last = r
                else:
                    return None
            elif nx == "]":
                j = i + 2
                while j < n and text[j] not in "\x07\x1b":
                    j += 1
                if j >= n:
                    return None
                if text[j] == "\x1b":
                    if text[j + 1:j + 2] != "\\":
                        return None
                    j += 1
                i = j + 1
            elif nx in "()":
                if i + 2 >= n or text[i + 2] not in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz":
                    return None
                i += 3
            elif nx in "=>":
                i += 2
            else:
                return None
        elif ch == "\n":
            r, c, i = r + 1, 0, i + 1
        elif ch == "\r":
            c, i = 0, i + 1
        elif ch == "\b":
            c, i = max(0, c - 1), i + 1
        elif ch == "\a":
            i += 1
        elif ord(ch) < 0x20 or 0x7F <= ord(ch) <= 0x9F:
            return None
        else:
            cells[(r, c)] = ch
            c, i = c + 1, i + 1
        last = max(last, r)
        if r > 4000 or c > 4000:
            return None
    lines = []
    for rr in range(last + 1):
        cols = [cc for (r2, cc) in cells if r2 == rr]
        lines.append("".join(cells.get((rr, cc), " ") for cc in range(max(cols) + 1)).rstrip(" ") if cols else "")
    return "\n".join(lines)


def _replay_ok(a: str, b: str) -> bool:
    if a == b:
        return True
    ref = _grid_render(a)
    return ref is not None and ref == b


case(
    1454,
    claim="When the text uses relative cursor motion or erase codes (CSI A B C D E F G J K), it is replayed on a character grid (CR, LF, BS handled, SGR and OSC ignored) and only the final screen text is kept; tabs, absolute cursor addressing, the alternate screen, double-width characters and any unknown or truncated escape leave the text untouched.",
    loses="frames and cell contents a terminal overwrites or erases, trailing spaces, the \\r\\n versus \\n versus bare \\r distinction (every line feed also returns the carriage), SGR colours and other invisible escapes",
    encode=replay_cursor,
    equivalent=_replay_ok,
    fixtures=[
        Fixture("real: rich 3-task progress, 6 refreshes", RICH_RAW, True),
        Fixture("synthetic: compose-style 4-service block, 7 refreshes", COMPOSE_TTY, True),
        Fixture("synthetic: npm spinner with CHA + erase-to-end frames", NPM_SPINNER, True),
        Fixture("SGR and OSC only, no cursor motion: left to #9", COLOURED_NO_CURSOR, False),
        Fixture("plain text", "building...\nok\n", False),
    ] + [Fixture(name, text, None) for name, text in CURSOR_ADVERSARIAL],
    min_gain=0.5,
)

# ===============================================================================================================
# #1455 /proc/PID/maps: ditto the dev + inode + path tail of a mapping that repeats the line above
#
# libc.so.6 is mapped five times in a row, each line carrying "fe:00 209637" and the 35-character path padded to a
# fixed column. A tail identical to the previous line's becomes a single quote mark. A line that already has only
# quote marks after the offset gets one extra quote (decode removes one), so any text round-trips.

_MAP_PRE = r"[0-9a-f]+-[0-9a-f]+ [-rwxsp]{4} [0-9a-f]+ "
_MAP_LINE = re.compile(rf"^({_MAP_PRE})([0-9a-f]+:[0-9a-f]+ \d+.*)$")
_MAP_QUOTES = re.compile(rf'^({_MAP_PRE})("+)$')


def maps_ditto(text: str) -> str:
    out, prev, escaped = [], None, False
    for line in text.split("\n"):
        m = _MAP_LINE.match(line)
        if m:
            pre, tail = m.groups()
            out.append(pre + '"' if tail == prev else line)
            prev = tail
            continue
        if _MAP_QUOTES.match(line):
            line, escaped = line + '"', True
        out.append(line)
        prev = None
    res = "\n".join(out)
    return res if escaped or len(res) < len(text) else text


def maps_unditto(text: str) -> str:
    out, prev = [], None
    for line in text.split("\n"):
        m = _MAP_LINE.match(line)
        if m:
            out.append(line)
            prev = m.group(2)
            continue
        q = _MAP_QUOTES.match(line)
        if q and len(q.group(2)) == 1:
            if prev is None:
                raise ValueError("ditto mark with no line above")
            out.append(q.group(1) + prev)
            continue
        out.append(q.group(1) + q.group(2)[:-1] if q else line)
        prev = None
    return "\n".join(out)


case(
    1455,
    claim="In /proc/PID/maps, a mapping whose dev, inode and path tail equals the line above is written as a quote mark after the offset; start, end, permissions and offset stay on every line; decode restores the text byte for byte, including lines that already end in quote marks.",
    encode=maps_ditto,
    decode=maps_unditto,
    fixtures=[
        Fixture("real: cat /proc/self/maps", MAPS_CAT, True),
        Fixture("real: python3.13 with 34 threads, excerpt", MAPS_PY, True),
        Fixture("no repeated tails", "7f0000000000-7f0000001000 r--p 00000000 fe:00 11 /usr/bin/a\n7f0000001000-7f0000002000 r-xp 00001000 fe:00 12 /usr/bin/b\n", False),
        Fixture("not a maps listing", "total 8\ndrwxr-xr-x 2 root root 4096 Nov 4 17:16 src\n", False),
        Fixture("lines that already look like ditto lines", MAPS_CAT + '7fe670e00000-7fe670e28000 r--p 00000000 "\n7fe670e00000-7fe670e28000 r--p 00000000 ""\n7fe670e00000-7fe670e28000 r--p 00000000 "\n', None),
        Fixture("ditto line first in the text", '7fe670e00000-7fe670e28000 r--p 00000000 "\n' + MAPS_CAT[:300], None),
    ],
    min_gain=0.1,
    idempotent=False,  # re-encoding escapes the ditto marks it just wrote, so it grows by design
)

# ===============================================================================================================
# #1456 /proc/PID/status: alias the hex Mems_allowed / Cpus_allowed masks to the *_list line below them
#
# Mems_allowed prints one 8-digit hex group per 32 possible nodes (32 groups, 287 characters, on distro kernels);
# Mems_allowed_list on the next line says the same thing in two characters. The mask is a pure function of the list
# and the bit width, so it is replaced by "@<bits>" and regenerated on decode. The encoder re-renders the mask from the
# list and only aliases when that reproduces the original text exactly.

_ST_MASK = re.compile(r"^(Cpus_allowed|Mems_allowed):\t([0-9a-f]+(?:,[0-9a-f]{8})*)$")
_ST_LIST = re.compile(r"^(Cpus_allowed|Mems_allowed)_list:\t(\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*)$")
_ST_ALIAS = re.compile(r"^(Cpus_allowed|Mems_allowed):\t@(\d+)$")
_ST_AT = re.compile(r"^(Cpus_allowed|Mems_allowed):\t@(.*)$")


def _bits_of(lst: str):
    bits: set[int] = set()
    for part in lst.split(","):
        a, _, b = part.partition("-")
        lo, hi = int(a), int(b) if b else int(a)
        if hi < lo or hi > 8192:
            return None
        bits.update(range(lo, hi + 1))
    return bits


def _render_mask(bits: set[int], nbits: int) -> str:
    groups = (nbits + 31) // 32
    first = nbits - 32 * (groups - 1)
    out = []
    for g in range(groups - 1, -1, -1):
        val = sum(1 << b for b in range(32) if g * 32 + b in bits)
        out.append(f"{val:0{8 if g != groups - 1 else (first + 3) // 4}x}")
    return ",".join(out)


def status_alias_masks(text: str) -> str:
    lines = text.split("\n")
    escaped = changed = False
    for i, line in enumerate(lines):
        if _ST_AT.match(line):
            lines[i], escaped = line.replace("\t@", "\t@@", 1), True
            continue
        m = _ST_MASK.match(line)
        nxt = _ST_LIST.match(lines[i + 1]) if i + 1 < len(lines) else None
        if not m or not nxt or nxt.group(1) != m.group(1):
            continue
        parts = m.group(2).split(",")
        nbits = 32 * (len(parts) - 1) + 4 * len(parts[0])
        bits = _bits_of(nxt.group(2))
        alias = f"@{nbits}"
        if bits is not None and len(alias) < len(m.group(2)) and _render_mask(bits, nbits) == m.group(2):
            lines[i], changed = f"{m.group(1)}:\t{alias}", True
    res = "\n".join(lines)
    return res if escaped or (changed and len(res) < len(text)) else text


def status_unalias_masks(text: str) -> str:
    lines = text.split("\n")
    for i, line in enumerate(lines):
        m = _ST_ALIAS.match(line)
        if m:
            nxt = _ST_LIST.match(lines[i + 1]) if i + 1 < len(lines) else None
            bits = _bits_of(nxt.group(2)) if nxt and nxt.group(1) == m.group(1) else None
            if bits is None:
                raise ValueError("mask alias without a usable list line")
            lines[i] = f"{m.group(1)}:\t{_render_mask(bits, int(m.group(2)))}"
            continue
        e = _ST_AT.match(line)
        if e:
            lines[i] = f"{e.group(1)}:\t{e.group(2)}"
    return "\n".join(lines)


case(
    1456,
    claim="In /proc/PID/status, a Cpus_allowed or Mems_allowed hex mask that the following *_list line regenerates exactly is replaced by '@<bit width>'; decode rebuilds the mask byte for byte, and values that already start with '@' are escaped.",
    encode=status_alias_masks,
    decode=status_unalias_masks,
    fixtures=[
        Fixture("real: cat /proc/self/status", STATUS_CAT, True),
        Fixture("real: python3.13 process", STATUS_PY, True),
        Fixture("edited copy: 128 CPUs and 2 memory nodes", STATUS_BIG, True),
        Fixture("mask that does not match its list", STATUS_PY.replace("Mems_allowed_list:\t0", "Mems_allowed_list:\t1"), False),
        Fixture("short masks (4 CPUs) are not worth aliasing", "Cpus_allowed:\tf\nCpus_allowed_list:\t0-3\n", False),
        Fixture("84 CPUs: the first group has 5 digits", "Threads:\t1\nCpus_allowed:\tfffff,ffffffff,ffffffff\nCpus_allowed_list:\t0-83\nvoluntary_ctxt_switches:\t0\n", True),
        Fixture("not a status file", "Name: x\nVmRSS: 12 kB\n", False),
        Fixture("values that already start with @", STATUS_PY.replace("Cpus_allowed:\tf", "Cpus_allowed:\t@4").replace("Mems_allowed_list:\t0", "Mems_allowed_list:\t0\nCpus_allowed:\t@@7"), None),
        Fixture("alias-looking line without a list", "Mems_allowed:\t@1024\nMems_allowed_list:\t0\nCpus_allowed:\t@4\n", None),
    ],
    min_gain=0.1,
    idempotent=False,  # an already-aliased value starts with '@' and is escaped on a second pass
)

# ===============================================================================================================
# #1457 ps -eLf: print a process's command line once and its thread rows as LWP, C and TIME
#
# ps -eLf lists one row per thread, and every row repeats UID, PID, PPID, NLWP, STIME, TTY and the whole command
# line; a JVM with 100 threads and a 400-character classpath prints that 100 times. Consecutive rows that differ only in
# LWP, C and TIME become '+LWP=n C=n TIME=t' under the first row. The row layout is not kept (equivalent, not roundtrip).

_PS_ROW = re.compile(r"^(\S+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\S+)\s+(\S+)\s+(\S+) (.*)$")
_PS_CONT = re.compile(r"^\+LWP=(\d+) C=(\d+) TIME=(\S+)$")


def ps_thread_rows(text: str) -> str:
    lines = text.split("\n")
    if any(_PS_CONT.match(l) for l in lines):
        return text
    rows = [_PS_ROW.match(l) for l in lines]
    out, i = [], 0
    while i < len(lines):
        m = rows[i]
        j = i + 1
        if m:
            key = lambda r: (r.group(1), r.group(2), r.group(3), r.group(6), r.group(7), r.group(8), r.group(10))  # noqa: E731
            while j < len(lines) and rows[j] and key(rows[j]) == key(m):
                j += 1
        cont = [f"+LWP={rows[k].group(4)} C={rows[k].group(5)} TIME={rows[k].group(9)}" for k in range(i + 1, j)]
        if cont and sum(len(c) for c in cont) < sum(len(lines[k]) for k in range(i + 1, j)):
            out.append(lines[i])
            out.extend(cont)
        else:
            out.extend(lines[i:j])
        i = j
    res = "\n".join(out)
    return res if len(res) < len(text) else text


def _ps_rows(text: str):
    """Independent oracle view: whitespace-split fields per row, continuation lines expanded from the row above."""
    rows, prev = [], None
    for line in text.split("\n"):
        f = line.split(None, 9)
        c = _PS_CONT.match(line)
        if len(f) == 10 and all(x.isdigit() for x in f[1:6]) and not c:
            prev = f
            rows.append(tuple(f))
        elif c and prev:
            rows.append((prev[0], prev[1], prev[2], c.group(1), c.group(2), prev[5], prev[6], prev[7], c.group(3), prev[9]))
        else:
            rows.append((line,))
    return rows


case(
    1457,
    claim="In ps -eLf output, consecutive thread rows that share UID, PID, PPID, NLWP, STIME, TTY and command line keep only the first row in full; the others become '+LWP=n C=n TIME=t' lines; every field of every row is recoverable, and a text that already contains such a line is left alone.",
    loses="column alignment padding of the collapsed thread rows",
    encode=ps_thread_rows,
    equivalent=lambda a, b: a == b or _ps_rows(a) == _ps_rows(b),
    fixtures=[
        Fixture("real: ps -eLf, PID 1 with 9 threads and a 35-thread process with a long argv", PS_ELF, True),
        Fixture("real: ps -ef has no LWP column", PS_EF_SMALL, False),
        Fixture("single-threaded processes only", "\n".join(PS_ELF.split("\n")[:1] + PS_ELF.split("\n")[10:14]) + "\n", False),
        Fixture("two threads with different command lines (prctl names) and a days-form TIME", "UID PID PPID LWP C NLWP STIME TTY TIME CMD\nroot 7 1 7 0 2 Nov04 ? 1-02:03:04 /usr/bin/app --serve --port 8080 --workers 4\nroot 7 1 8 3 2 Nov04 ? 1-02:03:04 /usr/bin/app --serve --port 8080 --workers 4\nroot 7 1 9 0 2 Nov04 ? 00:00:00 app-worker\n", True),
        Fixture("pre-fork workers: same command, different PIDs, one thread each", "UID PID PPID LWP C NLWP STIME TTY TIME CMD\nroot 7 1 7 0 1 Nov04 ? 00:00:09 gunicorn: worker [shop]\nroot 8 1 8 0 1 Nov04 ? 00:00:09 gunicorn: worker [shop]\nroot 9 1 9 0 1 Nov04 ? 00:00:09 gunicorn: worker [shop]\n", False),
        Fixture("lines that already look like continuation lines", PS_ELF + "+LWP=5 C=0 TIME=00:00:00\n", None),
        Fixture("CRLF output", PS_ELF.replace("\n", "\r\n"), True),
    ],
    min_gain=0.2,
)

# ===============================================================================================================
# #1458 ip addr: drop the "valid_lft forever preferred_lft forever" lines and mark the derivable broadcast addresses
#
# Each address line is followed by a lifetime line, and for most that is the literal "forever". An IPv4 broadcast that
# equals the subnet's own (prefix <= 30) and an Ethernet 'brd ff:ff:ff:ff:ff:ff' are written 'brd *'. The encoding is
# applied only when every address in the text has its lifetime line, so a missing line unambiguously means 'forever';
# a text that already contains 'brd *' is left alone, and a forever line directly followed by another lifetime line is kept.

_IP_ADDR = re.compile(r"^\s+inet6? ")
_IP_FOREVER = "       valid_lft forever preferred_lft forever"
_IP_BRD = re.compile(r"^(\s+inet )(\d+\.\d+\.\d+\.\d+)/(\d{1,2})((?: metric \d+)?) brd (\d+\.\d+\.\d+\.\d+)(?= |$)")
_IP_ETH = re.compile(r"^(\s+link/ether (?:[0-9a-f]{2}:){5}[0-9a-f]{2}) brd ff:ff:ff:ff:ff:ff(?= |$)")


def _is_lft(line: str) -> bool:
    return line.lstrip().startswith("valid_lft ")


def _subnet_broadcast(addr: str, plen: int):
    try:
        return str(ipaddress.ip_interface(f"{addr}/{plen}").network.broadcast_address)
    except ValueError:
        return None


def ip_addr_compact(text: str) -> str:
    lines = text.split("\n")
    addr = [i for i, l in enumerate(lines) if _IP_ADDR.match(l)]
    if not addr or any("brd *" in l for l in lines):
        return text
    if not all(i + 1 < len(lines) and _is_lft(lines[i + 1]) for i in addr):
        return text
    drop = {i + 1 for i in addr if lines[i + 1] == _IP_FOREVER and not (i + 2 < len(lines) and _is_lft(lines[i + 2]))}
    out = []
    for i, l in enumerate(lines):
        if i in drop:
            continue
        m = _IP_BRD.match(l)
        if m and int(m.group(3)) <= 30 and _subnet_broadcast(m.group(2), int(m.group(3))) == m.group(5):
            l = f"{m.group(1)}{m.group(2)}/{m.group(3)}{m.group(4)} brd *" + l[m.end():]
        elif (e := _IP_ETH.match(l)):
            l = f"{e.group(1)} brd *" + l[e.end():]
        out.append(l)
    res = "\n".join(out)
    return res if len(res) < len(text) else text


def _ip_facts(text: str):
    """Independent oracle view: per line, with 'brd *' resolved (integer arithmetic for IPv4, all-ones for Ethernet)
    and each address line paired with its lifetime line (a missing line reads as forever, which is what the encoder's
    output relies on)."""
    lines, facts, i = text.split("\n"), [], 0
    while i < len(lines):
        l = lines[i]
        m = re.match(r"^(\s+inet )(\d+)\.(\d+)\.(\d+)\.(\d+)/(\d+)(.*) brd \*(.*)$", l)
        e = re.match(r"^(\s+link/ether (?:[0-9a-f]{2}:){5}[0-9a-f]{2}) brd \*(.*)$", l)
        if m and int(m.group(6)) <= 30:
            a = (int(m.group(2)) << 24) | (int(m.group(3)) << 16) | (int(m.group(4)) << 8) | int(m.group(5))
            b = a | ((1 << (32 - int(m.group(6)))) - 1)
            l = f"{m.group(1)}{m.group(2)}.{m.group(3)}.{m.group(4)}.{m.group(5)}/{m.group(6)}{m.group(7)} brd " \
                f"{b >> 24}.{(b >> 16) & 255}.{(b >> 8) & 255}.{b & 255}{m.group(8)}"
        elif e:
            l = f"{e.group(1)} brd ff:ff:ff:ff:ff:ff{e.group(2)}"
        if _IP_ADDR.match(l):
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if _is_lft(nxt):
                facts.append((l, nxt.strip()))
                i += 2
                continue
            facts.append((l, "valid_lft forever preferred_lft forever"))
        else:
            facts.append((l, None))
        i += 1
    return facts


case(
    1458,
    claim="In ip addr output where every address has a lifetime line, 'valid_lft forever preferred_lft forever' lines are dropped (absent means forever), a brd equal to the IPv4 subnet broadcast (prefix <= 30) or to ff:ff:ff:ff:ff:ff on a link/ether line is written 'brd *', and every other line is kept verbatim.",
    loses="the spelling of the default lifetime line and of a computable broadcast address",
    encode=ip_addr_compact,
    equivalent=lambda a, b: a == b or _ip_facts(a) == _ip_facts(b),
    fixtures=[
        Fixture("reconstructed: Docker host, DHCP lease, tun0 peer, bridge", IP_ADDR, True),
        Fixture("lo and eth0 only", "\n".join(IP_ADDR.split("\n")[:6] + ["2: eth0@if7: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc noqueue state UP group default ", "    link/ether 02:42:ac:11:00:02 brd ff:ff:ff:ff:ff:ff link-netnsid 0", "    inet 172.17.0.2/16 brd 172.17.255.255 scope global eth0", "       valid_lft forever preferred_lft forever"]) + "\n", True),
        Fixture("ip -br addr has no lifetime lines", IP_ADDR_BRIEF, False),
        Fixture("an address without its lifetime line is left alone", IP_ADDR.replace("       valid_lft forever preferred_lft forever\n", "", 1), False),
        Fixture("text that already uses 'brd *'", IP_ADDR.replace("brd 172.17.255.255", "brd *"), None),
        Fixture("non-standard broadcast is kept", IP_ADDR.replace("brd 172.17.255.255", "brd 172.17.0.255"), True),
        Fixture("a doubled lifetime line", IP_ADDR.replace("    inet 172.17.0.1/16 brd 172.17.255.255 scope global docker0\n       valid_lft forever preferred_lft forever\n", "    inet 172.17.0.1/16 brd 172.17.255.255 scope global docker0\n       valid_lft forever preferred_lft forever\n       valid_lft forever preferred_lft forever\n"), True),
    ],
    min_gain=0.1,
)

# ===============================================================================================================
# #1459 pstree -ap: merge sibling thread leaves into one line with PID ranges
#
# A multi-threaded process prints one rail line per thread, "|-{python3},17414", differing only in the PID. A run of
# such leaves with the same indentation and name becomes "|-{python3} x34: 17414-17447,17449". The PIDs are kept in
# order; the rail of the last line of the run (|- or `-) is the rail of the merged line. A line that already looks
# merged gets one extra backslash after its rail.

_PT_LEAF = re.compile(r"^([ |`]*?)([|`]-)\{([^{}\s,]+)\},([1-9]\d*)$")
_PT_MERGED = re.compile(r"^([ |`]*?)([|`]-)(\\*)\{([^{}\s,]+)\} x(\d+): ([\d,-]+)$")


def _pid_list(pids: list[int]) -> str:
    out, i = [], 0
    while i < len(pids):
        j = i
        while j + 1 < len(pids) and pids[j + 1] == pids[j] + 1:
            j += 1
        out.append(f"{pids[i]}-{pids[j]}" if j - i >= 2 else ",".join(str(p) for p in pids[i:j + 1]))
        i = j + 1
    return ",".join(out)


def _parse_pid_list(s: str) -> list[int]:
    out = []
    for part in s.split(","):
        a, _, b = part.partition("-")
        lo, hi = int(a), int(b) if b else int(a)
        if hi < lo or hi - lo > 100000:
            raise ValueError("bad pid range")
        out.extend(range(lo, hi + 1))
    return out


def pstree_merge(text: str) -> str:
    lines = text.split("\n")
    out, i, escaped = [], 0, False
    while i < len(lines):
        m = _PT_LEAF.match(lines[i])
        if not m:
            e = _PT_MERGED.match(lines[i])
            if e:
                lines[i] = f"{e.group(1)}{e.group(2)}\\{lines[i][e.end(2):]}"
                escaped = True
            out.append(lines[i])
            i += 1
            continue
        ind, name, run = m.group(1), m.group(3), [m]
        j = i + 1
        while run[-1].group(2) == "|-" and j < len(lines):
            n = _PT_LEAF.match(lines[j])
            if not n or n.group(1) != ind or n.group(3) != name:
                break
            run.append(n)
            j += 1
        pids = [int(x.group(4)) for x in run]
        merged = f"{ind}{run[-1].group(2)}{{{name}}} x{len(run)}: {_pid_list(pids)}"
        if len(merged) < sum(len(lines[k]) + 1 for k in range(i, j)) - 1:
            out.append(merged)
        else:
            out.extend(lines[i:j])
        i = j
    res = "\n".join(out)
    return res if escaped or len(res) < len(text) else text


def pstree_split(text: str) -> str:
    out = []
    for line in text.split("\n"):
        m = _PT_MERGED.match(line)
        if not m:
            out.append(line)
            continue
        ind, rail, bs, name, n, pids = m.groups()
        if bs:
            out.append(f"{ind}{rail}{bs[1:]}{line[m.end(3):]}")
            continue
        plist = _parse_pid_list(pids)
        if len(plist) != int(n):
            raise ValueError("thread count does not match the PID list")
        out.extend(f"{ind}{'|-' if k < len(plist) - 1 else rail}{{{name}}},{p}" for k, p in enumerate(plist))
    return "\n".join(out)


case(
    1459,
    claim="In pstree -ap output, a run of thread leaves '|-{name},PID' with the same indentation and name is merged into one '{name} xN: PID-ranges' line keeping every PID in order and the final rail; decode regenerates the lines byte for byte, escaping lines that already look merged.",
    encode=pstree_merge,
    decode=pstree_split,
    fixtures=[
        Fixture("real: python3.13, 34 threads and one child", PSTREE_AP, True),
        Fixture("two runs, pids out of order", "java,100 -jar a.jar\n  |-{java},101\n  |-{java},102\n  |-{java},103\n  |-{java},107\n  |-{java},105\n  |-{java},106\n  |-{GC},110\n  |-{GC},111\n  `-{GC},112\n", True),
        Fixture("no thread leaves", "systemd,1\n  |-sshd,789 -D\n  |   `-sshd,1500 user@pts/0\n  `-cron,702 -f\n", False),
        Fixture("a lone thread leaf is not worth merging", "a,1\n  |-b,5\n  `-{a},3\n", False),
        Fixture("pstree -p style (first child on the parent's line)", "java(100)-+-{java}(101)\n     |-{java}(102)\n     `-{java}(103)\n", False),
        Fixture("lines that already look merged", PSTREE_AP + "  |-{python3} x3: 1-3\n  |-\\{python3} x3: 1-3\n  `-{python3} x2: 5,9\n", None),
        Fixture("leading-zero pids are not merged", "a,1\n  |-{a},001\n  |-{a},002\n  `-{a},003\n", False),
    ],
    min_gain=0.1,
    idempotent=False,  # merged lines are escaped by a second pass
)

# ===============================================================================================================
# #1460 ss: fold the IPv4 / IPv6 twin listener rows
#
# A dual-stack daemon is listed twice, 0.0.0.0:22 and [::]:22, with the same queues and process. Adjacent twin rows
# merge into the first row with '*46:22' and a peer of '*46:*' (loopback pairs 127.0.0.1 / [::1] use 'lo46'); where the
# process column differs only in the fd numbers they are written 'fd=3|4'.

_SS_V4 = re.compile(r"^(0\.0\.0\.0|127\.0\.0\.1):(\d+)$")
_SS_V6 = {"0.0.0.0": "[::]", "127.0.0.1": "[::1]"}
_SS_MARK = {"0.0.0.0": "*46", "127.0.0.1": "lo46"}
_SS_USED = re.compile(r"(?:^|[^\w])(?:\*46|lo46):")


def _spans(line: str):
    return [(m.start(), m.end(), m.group()) for m in re.finditer(r"\S+", line)]


def _ss_twin(a: str, b: str):
    fa, fb = _spans(a), _spans(b)
    if len(fa) != len(fb):
        return None
    li = next((k for k, s in enumerate(fa) if _SS_V4.match(s[2])), None)
    if li is None or li + 1 >= len(fa):
        return None
    addr, port = _SS_V4.match(fa[li][2]).groups()
    if fb[li][2] != f"{_SS_V6[addr]}:{port}" or fa[li + 1][2] != "0.0.0.0:*" or fb[li + 1][2] != "[::]:*":
        return None
    proc = len(fa) - 1 if fa[-1][2].startswith("users:") else None
    for k in range(len(fa)):
        if k in (li, li + 1) or k == proc:
            continue
        if fa[k][2] != fb[k][2]:
            return None
    newp = None
    if proc is not None:
        pa, pb = fa[proc][2], fb[proc][2]
        if not pb.startswith("users:") or re.sub(r"fd=\d+", "fd=#", pa) != re.sub(r"fd=\d+", "fd=#", pb):
            return None
        fds = iter(re.findall(r"fd=(\d+)", pb))
        newp = re.sub(r"fd=(\d+)", lambda m: f"fd={m.group(1)}|{next(fds)}", pa)
    repl = {li: f"{_SS_MARK[addr]}:{port}", li + 1: "*46:*"}
    if newp is not None:
        repl[proc] = newp
    out, prev = [], 0
    for k, (s, e, tok) in enumerate(fa):
        out.append(a[prev:s])
        out.append(repl.get(k, tok))
        prev = e
    out.append(a[prev:])
    return "".join(out)


def ss_fold_twins(text: str) -> str:
    if _SS_USED.search(text):
        return text
    lines, out, i = text.split("\n"), [], 0
    while i < len(lines):
        merged = _ss_twin(lines[i], lines[i + 1]) if i + 1 < len(lines) else None
        if merged is not None:
            out.append(merged)
            i += 2
        else:
            out.append(lines[i])
            i += 1
    res = "\n".join(out)
    return res if len(res) < len(text) else text


def _ss_rows(text: str):
    rows = []
    for line in text.split("\n"):
        f = line.split()
        li = next((k for k, t in enumerate(f) if t.startswith(("*46:", "lo46:"))), None)
        if li is None:
            rows.append(tuple(f))
            continue
        mark, port = f[li].split(":", 1)
        addr = "0.0.0.0" if mark == "*46" else "127.0.0.1"
        a, b = list(f), list(f)
        a[li], b[li] = f"{addr}:{port}", f"{_SS_V6[addr]}:{port}"
        a[li + 1], b[li + 1] = "0.0.0.0:*", "[::]:*"
        if f[-1].startswith("users:"):
            a[-1] = re.sub(r"fd=(\d+)\|(\d+)", r"fd=\1", f[-1])
            b[-1] = re.sub(r"fd=(\d+)\|(\d+)", r"fd=\2", f[-1])
        rows += [tuple(a), tuple(b)]
    return rows


case(
    1460,
    claim="In ss output, adjacent rows that differ only by 0.0.0.0:P/[::]:P (or 127.0.0.1/[::1]) and the fd numbers of the process column are merged into one row with a '*46:P' (or 'lo46:P') local address, '*46:*' peer and 'fd=a|b'; every other row is kept.",
    loses="column alignment padding",
    encode=ss_fold_twins,
    equivalent=lambda a, b: a == b or _ss_rows(a) == _ss_rows(b),
    fixtures=[
        Fixture("reconstructed: ss -ltnp on a dev box", SS_LTNP, True),
        Fixture("reconstructed: ss -tuln, no process column", SS_TULN, True),
        Fixture("no twins: different ports, different pids, an ESTAB row", SS_NO_TWINS, False),
        Fixture("not ss output", "tcp 0 0 0.0.0.0:22 0.0.0.0:* LISTEN\n", False),
        Fixture("text that already uses the markers", SS_TULN + "tcp LISTEN 0 1 *46:99 *46:* users:((\"x\",pid=1,fd=1|2))\n", None),
        Fixture("IPv6 row first", "State Recv-Q Send-Q Local Address:Port Peer Address:Port\nLISTEN 0 1 [::]:22 [::]:*\nLISTEN 0 1 0.0.0.0:22 0.0.0.0:*\n", False),
    ],
    min_gain=0.1,
)

# ===============================================================================================================
# #1461 ipconfig /all: drop the alignment dot leaders
#
# "   Host Name . . . . . . . . . . . . : DESKTOP" pads the key to a fixed column with '. ' pairs; the dots are part
# of the localized string, not data. The line becomes "   Host Name: DESKTOP". Values, indentation, adapter headers and
# continuation lines are untouched.

_IPC_KEY = re.compile(r"   (\S(?:.*?\S)?)[ .]*\.[ .]*")


def ipconfig_dots(text: str) -> str:
    out = []
    for line in text.split("\n"):
        i = line.find(": ")
        m = _IPC_KEY.fullmatch(line[:i]) if i > 3 else None  # the key and its leader end at the first ': '
        out.append(f"   {m.group(1)}: {line[i + 2:]}" if m else line)
    res = "\n".join(out)
    return res if len(res) < len(text) else text


def _ipc_facts(text: str):
    out = []
    for line in text.split("\n"):
        i = line.find(": ")
        if i > 3 and line.startswith("   ") and not line[3].isspace():
            left = line[3:i]
            out.append(("kv", left.rstrip(" ."), line[i + 2:]))
        else:
            out.append(("raw", line, ""))
    return out


case(
    1461,
    claim="In ipconfig-style listings, the run of ' .' alignment dots between a 3-space-indented key and its ': ' is removed; the key text, the value, the indentation and every other line are unchanged.",
    loses="the alignment dots between key and colon",
    encode=ipconfig_dots,
    equivalent=lambda a, b: _ipc_facts(a) == _ipc_facts(b),
    fixtures=[
        Fixture("reconstructed: ipconfig /all, three adapters", IPCONFIG, True),
        Fixture("same with CRLF line endings", IPCONFIG_CRLF, True),
        Fixture("systeminfo style: space padded, no dots", IPCONFIG_PLAIN, False),
        Fixture("dots inside a value are not leaders", "   Description . . . . . . . . . . . : Intel . . . adapter\n   Version: 1.2.3.\n", True),
        Fixture("keys with a dot in the name", "   v1.2 . . . . : x\n   a. b: c\n   . . . : lone\n", None),
    ],
    min_gain=0.2,
)

# ===============================================================================================================
# #1462 strace -y: ditto the repeated fd and AT_FDCWD path annotations
#
# With -y strace prints the path after every fd argument, "read(3</usr/lib/python3.13/aliases.pyc>, ...)", and the
# whole cwd after every AT_FDCWD. An annotation equal to the last one shown for that fd (or for AT_FDCWD) in the same
# pid is written 'N<>'. A header line says so (it also tells the reader how to expand), and text that does not carry
# the header is returned by decode as it is. A text that already contains an empty annotation, or that starts with the
# header, is handled by leaving it alone or escaping it with a raw marker.

_ST_HDR = "#strace -y ditto: 'N<>' repeats the path last shown for fd N in this pid (AT_FDCWD<> the cwd)\n"
_ST_RAW = "#strace -y raw\n"
_ST_ANN = re.compile(r"(?<!\w)(AT_FDCWD|\d+)<([^<>\n]*)>")
_ST_PID = re.compile(r"^(?:\[pid\s+(\d+)\]|(\d+))(?=\s)")


def _strace_key(line: str) -> str:
    m = _ST_PID.match(line)
    return (m.group(1) or m.group(2)) if m else ""


def strace_ditto(text: str) -> str:
    if text.startswith(_ST_HDR) or text.startswith(_ST_RAW):
        return _ST_RAW + text
    if any(m.group(2) == "" for m in _ST_ANN.finditer(text)):
        return text
    last: dict[tuple[str, str], str] = {}
    out = []
    for line in text.split("\n"):
        key = _strace_key(line)

        def sub(m, key=key):
            k = (key, m.group(1))
            if last.get(k) == m.group(2):
                return f"{m.group(1)}<>"
            last[k] = m.group(2)
            return m.group(0)

        out.append(_ST_ANN.sub(sub, line))
    res = _ST_HDR + "\n".join(out)
    return res if len(res) < len(text) else text


def strace_unditto(text: str) -> str:
    if text.startswith(_ST_RAW):
        return text[len(_ST_RAW):]
    if not text.startswith(_ST_HDR):
        return text
    last: dict[tuple[str, str], str] = {}
    out = []
    for line in text[len(_ST_HDR):].split("\n"):
        key = _strace_key(line)

        def sub(m, key=key):
            k = (key, m.group(1))
            if m.group(2) == "":
                if k not in last:
                    raise ValueError("ditto annotation with nothing to repeat")
                return f"{m.group(1)}<{last[k]}>"
            last[k] = m.group(2)
            return m.group(0)

        out.append(_ST_ANN.sub(sub, line))
    return "\n".join(out)


case(
    1462,
    claim="In strace -y output, an fd or AT_FDCWD path annotation equal to the last one shown for the same fd in the same pid becomes 'N<>' behind a one-line header; decode restores the text byte for byte, and texts without the header, with empty annotations, or starting with the header are handled by leaving or escaping them.",
    encode=strace_ditto,
    decode=strace_unditto,
    fixtures=[
        Fixture("real: strace -f -y -o file (pid prefix), excerpt", STRACE_FILE, True),
        Fixture("real: strace -f -y to stderr ([pid N] prefix, unfinished/resumed), first 100 lines", STRACE_STDOUT, True),
        Fixture("strace without -y", "openat(AT_FDCWD, \"/etc/hosts\", O_RDONLY) = 3\nread(3, \"127.0.0.1\", 4096) = 9\nclose(3) = 0\n", False),
        Fixture("text with an empty annotation already in it", STRACE_FILE + "read(3<>, \"\", 1) = 0\n", None),
        Fixture("text that starts with the header", _ST_HDR + STRACE_FILE[:400], None),
        Fixture("text that starts with the raw marker", _ST_RAW + STRACE_FILE[:400], None),
        Fixture("fd number reused for another path", "9 read(3</a>, \"\", 1) = 0\n9 read(3</a>, \"\", 1) = 0\n9 close(3</a>) = 0\n9 read(3</b>, \"\", 1) = 0\n9 read(3</b>, \"\", 1) = 0\n10 read(3</a>, \"\", 1) = 0\n10 read(3</a>, \"\", 1) = 0\n", None),
    ],
    min_gain=0.1,
    idempotent=False,  # the header makes the encoded text start with the header, which is escaped on a second pass
)

# ===============================================================================================================
# #1463 conntrack -L and /proc/net/nf_conntrack: replace the reply tuple that mirrors the original
#
# Each flow row carries two tuples, "src=10.0.0.5 dst=10.0.0.9 sport=51234 dport=443" and the reply
# "src=10.0.0.9 dst=10.0.0.5 sport=443 dport=51234". Without NAT the reply is the original with source and destination
# swapped, so it is replaced by '<mirror>' (a flag such as [UNREPLIED] between the tuples stays). Rows with NAT
# (reply differs), ICMP rows and everything else are untouched. '<mirror>' already in a row gets one extra '<'.

_CT_PROTO = re.compile(r"^(?:ipv[46]\s+\d+\s+)?(?:tcp|udp|udplite|sctp|dccp)\s")
_CT_TUPLE = re.compile(r"src=(\S+) dst=(\S+) sport=(\d+) dport=(\d+)")
_CT_REPLY = re.compile(r"((?: \[[A-Z_]+\])*) src=(\S+) dst=(\S+) sport=(\d+) dport=(\d+)")
_CT_MARK = re.compile(r"<+mirror>")


def conntrack_mirror(text: str) -> str:
    out, escaped = [], False
    for line in text.split("\n"):
        if _CT_MARK.search(line):
            line, escaped = _CT_MARK.sub(lambda m: "<" + m.group(0), line), True
        m1 = _CT_TUPLE.search(line) if _CT_PROTO.match(line) else None
        if m1:
            s1, d1, sp1, dp1 = m1.groups()
            m2 = _CT_REPLY.match(line, m1.end())
            if m2 and m2.groups()[1:] == (d1, s1, dp1, sp1):
                line = line[:m1.end()] + m2.group(1) + " <mirror>" + line[m2.end():]
        out.append(line)
    res = "\n".join(out)
    return res if escaped or len(res) < len(text) else text


def conntrack_unmirror(text: str) -> str:
    out = []
    for line in text.split("\n"):
        def expand(_m, line=line):
            m1 = _CT_TUPLE.search(line)
            if not m1:
                raise ValueError("<mirror> with no tuple before it")
            s1, d1, sp1, dp1 = m1.groups()
            return f"src={d1} dst={s1} sport={dp1} dport={sp1}"

        line = re.sub(r"(?<!<)<mirror>", expand, line)
        out.append(re.sub(r"<(<+mirror>)", r"\1", line))
    return "\n".join(out)


CONNTRACK = _txt(  # reconstructed from memory (conntrack-tools 1.4.x); the DNAT and ICMP rows keep their reply tuple
    "tcp      6 431996 ESTABLISHED src=10.0.0.5 dst=10.0.0.9 sport=51234 dport=443 src=10.0.0.9 dst=10.0.0.5 sport=443 dport=51234 [ASSURED] mark=0 use=1",
    "tcp      6 117 TIME_WAIT src=10.0.0.5 dst=140.82.112.3 sport=40822 dport=443 src=140.82.112.3 dst=10.0.0.5 sport=443 dport=40822 [ASSURED] mark=0 use=1",
    "tcp      6 59 SYN_SENT src=10.0.0.5 dst=10.20.30.40 sport=51770 dport=5432 [UNREPLIED] src=10.20.30.40 dst=10.0.0.5 sport=5432 dport=51770 mark=0 use=1",
    "udp      17 27 src=10.0.0.5 dst=10.0.0.2 sport=40022 dport=53 src=10.0.0.2 dst=10.0.0.5 sport=53 dport=40022 mark=0 use=1",
    "udp      17 29 src=10.0.0.5 dst=10.0.0.2 sport=51901 dport=53 src=10.0.0.2 dst=10.0.0.5 sport=53 dport=51901 mark=0 use=1",
    "tcp      6 86399 ESTABLISHED src=203.0.113.50 dst=10.0.0.5 sport=60122 dport=22 src=10.0.0.5 dst=203.0.113.50 sport=22 dport=60122 [ASSURED] mark=0 use=1",
    "tcp      6 299 ESTABLISHED src=172.17.0.2 dst=10.0.0.9 sport=48822 dport=8080 src=10.0.0.9 dst=10.0.0.5 sport=8080 dport=48822 [ASSURED] mark=0 use=1",
    "tcp      6 431990 ESTABLISHED src=198.51.100.7 dst=10.0.0.5 sport=33310 dport=80 src=172.17.0.3 dst=198.51.100.7 sport=80 dport=33310 [ASSURED] mark=0 use=1",
    "icmp     1 29 src=10.0.0.5 dst=1.1.1.1 type=8 code=0 id=4121 src=1.1.1.1 dst=10.0.0.5 type=0 code=0 id=4121 mark=0 use=1",
    "udp      17 119 src=10.0.0.5 dst=224.0.0.251 sport=5353 dport=5353 [UNREPLIED] src=224.0.0.251 dst=10.0.0.5 sport=5353 dport=5353 mark=0 use=1",
    "tcp      6 431999 ESTABLISHED src=10.0.0.5 dst=10.0.0.9 sport=51236 dport=443 src=10.0.0.9 dst=10.0.0.5 sport=443 dport=51236 [ASSURED] mark=0 use=1",
    "tcp      6 3 CLOSE src=10.0.0.5 dst=34.117.59.81 sport=44012 dport=443 src=34.117.59.81 dst=10.0.0.5 sport=443 dport=44012 [ASSURED] mark=0 use=1",
)
CONNTRACK_PROCFS = CONNTRACK.replace("tcp      6", "ipv4     2 tcp      6").replace("udp      17", "ipv4     2 udp      17")
CONNTRACK_NAT_ONLY = _txt(
    "tcp      6 431990 ESTABLISHED src=198.51.100.7 dst=10.0.0.5 sport=33310 dport=80 src=172.17.0.3 dst=198.51.100.7 sport=80 dport=33310 [ASSURED] mark=0 use=1",
    "icmp     1 29 src=10.0.0.5 dst=1.1.1.1 type=8 code=0 id=4121 src=1.1.1.1 dst=10.0.0.5 type=0 code=0 id=4121 mark=0 use=1",
)

case(
    1463,
    claim="In conntrack -L and /proc/net/nf_conntrack rows of tcp, udp, sctp and dccp flows, a reply tuple that is the original tuple with source and destination swapped is replaced by '<mirror>' (flags such as [UNREPLIED] between the tuples stay); NAT rows, ICMP rows and other lines are unchanged; decode restores the text byte for byte, and rows that already contain '<mirror>' are escaped.",
    encode=conntrack_mirror,
    decode=conntrack_unmirror,
    fixtures=[
        Fixture("reconstructed: conntrack -L, 12 flows (DNAT, ICMP and UNREPLIED rows included)", CONNTRACK, True),
        Fixture("reconstructed: /proc/net/nf_conntrack prefix", CONNTRACK_PROCFS, True),
        Fixture("only NAT and ICMP rows", CONNTRACK_NAT_ONLY, False),
        Fixture("not conntrack", "src=1.1.1.1 dst=2.2.2.2 sport=1 dport=2 src=2.2.2.2 dst=1.1.1.1 sport=2 dport=1\n", False),
        Fixture("rows that already contain the marker", CONNTRACK + "tcp 6 5 src=<mirror> dst=b sport=1 dport=2 src=b dst=<mirror> sport=2 dport=1 <mirror> <<mirror>\n", None),
    ],
    min_gain=0.1,
    idempotent=False,  # '<mirror>' written by the first pass is escaped by the second
)
