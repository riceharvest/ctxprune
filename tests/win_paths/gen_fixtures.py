#!/usr/bin/env python3
"""Deterministic generator of real-shaped Windows (and matching Linux) path-listing fixtures.
Writes fixtures/win_*.txt (CRLF, like a Windows console) and fixtures/linux_*.txt (LF).
Run: python3 gen_fixtures.py   (fixtures are committed; regenerate only if shapes change)."""
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "fixtures")
R = random.Random(20261119)
WORDS = ("core api auth billing cache config db events http io jobs log models net parse queue render router "
         "schema search session store sync task util view worker").split()
EXT = (".cs", ".cs", ".cs", ".csproj", ".json", ".ts", ".cpp", ".h", ".resx", ".xaml", ".md", ".config")


def tree(root_parts, ndirs, depth=(1, 3)):
    dirs = []
    for _ in range(ndirs):
        d = list(root_parts)
        for _ in range(R.randint(*depth)):
            d.append(R.choice(WORDS).capitalize() + R.choice(["", "", "Service", "Tests", "Core"]))
        dirs.append(d)
    return dirs


def fname():
    return R.choice(WORDS).capitalize() + R.choice(["Manager", "Handler", "Client", "Options", "Tests", "Helper", "Base"]) \
        + R.choice(EXT)


def files(dirs, lo=4, hi=22):
    out = []
    for d in dirs:
        for _ in range(R.randint(lo, hi)):
            out.append(d + [fname()])
    return out


def win(p, drive="C:"):
    return drive + "\\" + "\\".join(p)


def write(name, lines, nl):
    with open(os.path.join(OUT, name), "w", newline="") as f:
        f.write(nl.join(lines) + nl)


def main():
    os.makedirs(OUT, exist_ok=True)
    # 1. dir /s /b  (absolute, spaces in some dir names, sorted by dir)
    root = ["Users", "dev", "source", "repos", "Contoso Billing"]
    ds = tree(root, 8)
    fs = files(ds)
    write("win_dir_s_b.txt", [win(p) for p in fs], "\r\n")
    # 2. Get-ChildItem -Recurse | % FullName  (absolute, no spaces, deeper)
    ds = tree(["src", "App"], 10, (2, 4))
    fs = files(ds)
    write("win_gci_fullname.txt", [win(["work", "proj"] + p) for p in fs], "\r\n")
    # 3. git status on Windows (forward slashes, CRLF)
    ds = tree(["src"], 6)
    fs = files(ds, 3, 9)
    L = ["On branch feature/billing-retry", "Your branch is up to date with 'origin/feature/billing-retry'.", "",
         "Changes not staged for commit:", '  (use "git add <file>..." to update what will be committed)', ""]
    L += ["\tmodified:   " + "/".join(p) for p in fs[: len(fs) // 2]]
    L += ["", "Untracked files:", '  (use "git add <file>..." to include in what will be committed)', ""]
    L += ["\t" + "/".join(p) for p in fs[len(fs) // 2:]]
    L += ["", 'no changes added to commit (use "git add" and/or "git commit -a")']
    write("win_git_status.txt", L, "\r\n")
    # 3b. git status -s / porcelain
    write("win_git_status_short.txt", [R.choice([" M ", "?? ", "A  ", "M  "]) + "/".join(p) for p in fs], "\r\n")
    # 4. MSBuild / compiler errors
    ds = tree(["src", "App"], 7)
    L = ["Build started 11/19/2026 10:02:11.", "     1>Project \"C:\\work\\proj\\App.sln\" on node 1 (Build target(s))."]
    for d in ds:
        proj = win(["work", "proj"] + d[:2] + [d[1] + ".csproj"])
        for _ in range(R.randint(3, 9)):
            p = win(["work", "proj"] + d + [fname()])
            code = R.choice(["CS0103", "CS0246", "CS8618", "CS1061", "CS0117"])
            msg = R.choice(["The name 'ctx' does not exist in the current context",
                            "The type or namespace name 'Foo' could not be found",
                            "Non-nullable property 'Id' must contain a non-null value",
                            "'Order' does not contain a definition for 'Total'"])
            L.append(f"{p}({R.randint(1, 400)},{R.randint(1, 60)}): error {code}: {msg} [{proj}]")
    L += ["", "Build FAILED.", f"    {sum(1 for l in L if ': error' in l)} Error(s)", "Time Elapsed 00:00:07.31"]
    write("win_msbuild_errors.txt", L, "\r\n")
    # 5. UNC paths
    ds = tree(["fileserver01", "share", "Engineering", "Releases"], 6)
    fs = files(ds)
    write("win_unc.txt", ["\\\\" + "\\".join(p) for p in fs], "\r\n")
    # 6. mixed separators (tooling that joins with / after a C:\ root)
    ds = tree(["Users", "dev", "proj"], 7)
    fs = files(ds)
    write("win_mixed_sep.txt", ["C:\\" + "\\".join(p[:3]) + "/" + "/".join(p[3:]) for p in fs], "\r\n")
    # 7. PowerShell Get-ChildItem table (not a bare path list: must stay safe)
    L = []
    for d in tree(["proj"], 3):
        L += ["", f"    Directory: {win(d, 'D:')}", "", "Mode                 LastWriteTime         Length Name",
              "----                 -------------         ------ ----"]
        L += [f"-a---          12-06-2026 06:{k:02d} AM          {R.randint(100, 99999):5d} {fname()}" for k in range(R.randint(5, 14))]
    write("win_gci_table.txt", L, "\r\n")
    # Linux references (same trees)
    ds = tree(["home", "dev", "src", "repos", "contoso-billing"], 8)
    fs = files(ds)
    write("linux_find.txt", ["/" + "/".join(p) for p in fs], "\n")
    ds = tree(["src", "app"], 10, (2, 4))
    write("linux_rel.txt", ["./" + "/".join(p) for p in files(ds)], "\n")
    ds = tree(["src"], 6)
    fs = files(ds, 3, 9)
    write("linux_git_diff_name_only.txt", ["/".join(p) for p in fs], "\n")
    ds = tree(["src", "app"], 7)
    L = []
    for d in ds:
        for _ in range(R.randint(3, 9)):
            L.append(f"/home/dev/proj/{'/'.join(d + [fname()])}:{R.randint(1, 400)}:{R.randint(1, 60)}: error: use of undeclared identifier 'ctx'")
    write("linux_compiler_errors.txt", L, "\n")


if __name__ == "__main__":
    main()
