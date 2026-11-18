"""ctxprune-safe Claude Code hook (ctxprune-managed, ctxprune 0.3.0): scripts/lossless_hook.py from the bundled
stdlib runtime (rt/), lossless rules (+ cross-turn dedup by model: claude-opus-* on), never the model. Fails open: any error -> no output."""

import os
import sys


def main():
    if sys.version_info < (3, 9):
        return
    try:
        import importlib.util
        rt = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rt")
        spec = importlib.util.spec_from_file_location("_ctxprune_glue", os.path.join(rt, "glue.py"))
        g = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(g)
        os.environ["CTXPRUNE_PRESET"] = "safe"
        g.boot()   # private umask, rt/ on sys.path (appended), CTXPRUNE_OFF + counts-only stats
        import lossless_hook
        sys.argv = [lossless_hook.__file__]   # dedup: by model once per session (claude-opus-*: on); CTXPRUNE_DEDUP
        lossless_hook.main()
    except BaseException:
        pass


if __name__ == "__main__":
    main()
