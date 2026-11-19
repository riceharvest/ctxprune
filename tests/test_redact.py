import random, string, uuid, hashlib, subprocess, sys
from ctxprune.redact import Redactor

R = random.Random(7)
def tok(alpha, n): return "".join(R.choice(alpha) for _ in range(n))
AN = string.ascii_letters + string.digits
def secrets(n=200):
    gens = [
        lambda: "AKIA" + tok(string.ascii_uppercase + string.digits, 16),
        lambda: "ghp_" + tok(AN, 36),
        lambda: "github_pat_" + tok(AN + "_", 59),
        lambda: "sk-ant-api03-" + tok(AN + "-_", 90),
        lambda: "sk-proj-" + tok(AN + "-_", 48),
        lambda: "sk-" + tok(AN, 48),
        lambda: "xoxb-" + tok(string.digits, 12) + "-" + tok(AN, 24),
        lambda: "AIza" + tok(AN + "-_", 35),
        lambda: "eyJ" + tok(AN, 20) + ".eyJ" + tok(AN, 30) + "." + tok(AN + "-_", 43),
        lambda: "-----BEGIN RSA PRIVATE KEY-----\n" + tok(AN, 64) + "\n" + tok(AN, 64) + "\n-----END RSA PRIVATE KEY-----",
    ]
    return [g() for _ in range(n) for g in gens]

def test_recall_and_roundtrip():
    r = Redactor(); hit = 0; ss = secrets()
    for s in ss:
        line = f"export X={s} # note"
        out = r.redact(line)
        hit += s not in out
        assert r.restore(out) == line
    assert hit / len(ss) >= 0.98

def test_anthropic_labelled():
    assert "ANTHROPIC_KEY" in Redactor().redact("k=sk-ant-api03-" + "a" * 40)

def test_no_false_positives():
    r = Redactor(); n = 0; bad = 0
    for i in range(10000):
        for s in (hashlib.sha1(str(i).encode()).hexdigest(), hashlib.sha256(str(i).encode()).hexdigest(),
                  str(uuid.UUID(int=R.getrandbits(128), version=4)), "commit " + tok("0123456789abcdef", 40)):
            n += 1; bad += r.redact(s) != s
    assert bad / n < 0.001

def test_cli_flag():
    k = "ghp_" + "A1b2" * 9
    p = subprocess.run([sys.executable, "-m", "ctxprune.cli", "compress", "--redact-secrets"],
                       input=f"token {k}\n".encode(), capture_output=True)
    assert k.encode() not in p.stdout
