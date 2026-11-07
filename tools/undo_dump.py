"""Test-only: the undo list as RAWTRIAGE_TEST_UNDO_DUMP writes it, shared by testkit.undo_list() (in-process) and the
rawtriage-web shim (the dump file), so the two can't drift. Imports nothing from the app."""

TWO_PATHS = ("move", "movedir")  # every other op kind (write, mkdir, gitinit) touches one path


def entry(e):
    """One server._undo entry: its label, each op's kind and the paths it touched (never file contents), and whether
    it is what is left of a change that couldn't be rolled back."""
    return {"label": e["label"], "ops": [[op[0], *op[1:3 if op[0] in TWO_PATHS else 2]] for op in e["ops"]],
            "unfinished": e["label"].startswith("unfinished:")}
