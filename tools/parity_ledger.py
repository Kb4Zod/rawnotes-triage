#!/usr/bin/env python3
"""The Rust port's parity ledger, first draft, from the Python tests (test-only; Rust repo spec section 8, `patch-map.md`,
`ownership.md`). Reads the test files' syntax trees; imports nothing from the app and runs no test.

For every test (module, class, name) it finds what the test uses, following its inherited setUp and the helpers it
calls (methods of its class and bases, functions of its module, testkit): the routes it calls (with the /api/action
action and, for /api/project/status, file or folder), the CLI, the page (browser tests), the patch sites and direct
reads of server state it reaches (each a row of tools/patch_sites.tsv), and from those its owner and due ticket by
ownership.md's rule, and its outcome: "ported" (a Rust test replaces it: an R row, a test that calls no route and no
CLI, or one that runs a copy of the Python code) or "runs" (against Rust under the switch). testkit skips exactly the
"ported" tests under TRIAGE_SERVER / TRIAGE_CLI.

    python3 tools/parity_ledger.py               the ledger draft as TSV (one row per test) on stdout
    python3 tools/parity_ledger.py --summary     per-ticket counts against ownership.md's table, unplaceable tests
    python3 tools/parity_ledger.py --sites       every patch site found, with its row (TSV)
    python3 tools/parity_ledger.py --check       exit 1 on a site without a row, a row without a site, a dynamic site
                                                 not in tools/ledger_allow.tsv, or tests the unittest loader finds
                                                 that the walk doesn't (or the other way)
    python3 tools/parity_ledger.py --write-sites rewrite tools/patch_sites.tsv from the walk, keeping each row's
                                                 disposition (matched by file, scope, target, kind and order);
                                                 new sites get "?" and fail --check until someone fills them in
    --rev REV                                    walk the files as they are at git revision REV instead
"""
import ast
import collections
import csv
import io
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITES_TSV = os.path.join(ROOT, "tools", "patch_sites.tsv")
ALLOW_TSV = os.path.join(ROOT, "tools", "ledger_allow.tsv")

# ---- ownership.md, as data ---------------------------------------------------------------------------------------------
# Blockers (orchestration.md's dependency graph). 20 lands through 01's PR (D20), so 01 covers it.
BLOCKERS = {"01": ["20"], "20": [], "02": [], "03": ["01"], "04": ["03", "06"], "05": ["04", "06"], "06": ["01"],
            "07": ["03", "06"], "08": ["04", "07", "09", "21"], "09": ["03", "06"], "10": ["05", "08"], "11": ["01"],
            "12": ["05", "08", "15"], "15": ["01", "02"], "21": ["03", "06"]}
DUE_AFTER_OWNER = ["08", "10", "12"]
BUILD_ORDER = ["01", "20", "02", "03", "06", "04", "07", "09", "21", "05", "08", "10", "11", "15", "12"]
# ownership.md "Load, approximate (loop 3)": owned, due, of which pure. Sizing only; differences over five are reported.
APPROX = {"01": (23, 33, 0), "20": (15, 15, 15), "02": (4, 4, 4), "03": (65, 43, 6), "06": (57, 38, 0), "04": (55, 70, 16),
          "07": (51, 45, 8), "09": (50, 35, 3), "21": (16, 16, 2), "05": (26, 39, 0), "08": (70, 92, 8), "11": (35, 34, 15),
          "10": (45, 47, 0), "12": (84, 84, 0)}

GET_ROUTES = {"/api/version": "01", "/api/setup": "01", "/api/notes": "01", "/api/note": "01", "/api/check": "01", "/": "01",
              "/triage": "01", "/apple-touch-icon.png": "01", "/static/icons/icon-256.png": "01",
              "/api/reference/unindexed": "04", "/api/stats": "05", "/api/projects": "06", "/api/project": "06",
              "/api/brief": "06", "/api/project/deps": "21", "/api/github": "09", "/api/activity": "09", "/api/repo": "09",
              "/api/repo/path": "09"}
POST_ROUTES = {"/api/reference/index": "04", "/api/batch": "05", "/api/merge": "05", "/api/restore": "05", "/api/new": "05",
               "/api/setup": "05", "/api/open": "06", "/api/done": "07", "/api/migrate-board-data": "07",
               "/api/project/close": "08", "/api/github/token": "09", "/api/github/token/remove": "09",
               "/api/project/github": "09", "/api/action": None, "/api/project/status": None, "/api/undo": None}
ACTIONS = {"keep": "03", "kill": "03", "reference": "04", "promote": "04", "archive": "03", "durable": "04"}  # + the old names
FONT = re.compile(r"^/static/fonts/")
ROUTE_STR = re.compile(r"^(/api/[a-z/-]*|/|/triage|/static/[\w./-]+|/apple-touch-icon\.png)$")
CLI_ROUTES = {"-l": ("GET", "/api/notes"), "where": ("GET", "/api/setup"), "check": ("GET", "/api/check"),
              "new": ("POST", "/api/new"), "keep": ("POST", "/api/action", "keep"), "kill": ("POST", "/api/action", "kill"),
              "reference": ("POST", "/api/action", "reference"), "index": ("POST", "/api/reference/index"),
              "promote": ("POST", "/api/action", "promote"), "merge": ("POST", "/api/merge"), "undo": ("POST", "/api/undo"),
              "brief": ("GET", "/api/brief"), "migrate-board-data": ("POST", "/api/migrate-board-data")}
CLI_PROJECT = {"ship": ("POST", "/api/project/status", "shipped"), "kill": ("POST", "/api/project/status", "kill"),
               "extend": ("POST", "/api/project/status", "extend"), "close": ("POST", "/api/project/close"),
               "deps": ("GET", "/api/project/deps"), "github": ("POST", "/api/project/github")}

ROUTE = "by route"  # owner: the ticket of the test's last mutating route (else of its reads), ownership.md's rule
# ("07", {"08"}): the class's owner is 07, except a test whose routes (by the same rule) say 08: "Records → 07 except
# folder kill → 08".
CLASS_OWNER = {
    "test_meta.Header": "20", "test_meta.Review2": "20", "test_meta.Question": "01", "test_meta.HeaderThroughTheApp": "01",
    "test_safety.Containment": "03", "test_safety.BoardDataIsNeverMarkup": "06", "test_safety.OpenEndpoint": "06",
    "test_safety.XdgOpenContract": "06", "test_safety.Restart": "11",
    "test_server.ApiTestCase": ROUTE, "test_server.DecisionTestCase": ROUTE, "test_server.OpenPageTestCase": "11",
    "test_server.ServiceTestCase": "11", "test_server.IndexInsertTest": "04", "test_server.ReferenceIndexTestCase": "04",
    "test_runtime.ReadsBesideWrites": "03", "test_runtime.Caches": ROUTE, "test_runtime.GithubActivity": "09",
    "test_runtime.HttpLimits": "01",
    "test_limits.InboxLimits": "03", "test_limits.ConfigReadOncePerListing": "01",
    "test_autocommit.*": "03", "test_autocommit.CommitToast": "12",
    "test_engine.Faults": ROUTE, "test_engine.UndoContainment": "03",
    "test_projects.Worktrees": "06", "test_projects.LearningAndTemplates": "06", "test_projects.PromoteBase": "04",
    "test_projects.Slugs": "04", "test_projects.PromoteSkeleton": "04",
    "test_setup.*": "05",
    "test_board_record.SplitLegacy": "07", "test_board_record.BulkMigration": "07", "test_board_record.Legacy": "07",
    "test_board_record.BoardActionsLeaveTheProjectAlone": ("07", {"08"}), "test_board_record.Records": ("07", {"08"}),
    "test_board_record.Brief": "06", "test_board_record.Cli": "10",
    "test_closeout.*": "08", "test_deps.*": "21",
    "test_followups.DepsOutsideTheLock": "21", "test_followups.NewNotePath": "05", "test_followups.OffThisMachinePaths": ROUTE,
    "test_followups.FailingServer": "10",
    "test_github.*": "09",
    "test_github_push.NotOnGithubFlag": "06", "test_github_push.CloseOutGate": "08", "test_github_push.AllLocalBranches": ("06", {"08"}),
    "test_github_push.CreateRepo": "09", "test_github_push.Cli": "10",
    "test_cli.*": "10", "test_install.*": "11", "test_browser.*": "12", "test_zz_isolation.*": "02",
}
# Tests whose class splits by topic in ownership.md and whose topic the routes don't show (direct calls, caches).
TEST_OWNER = {
    "test_runtime.Caches.test_git_inspection_is_cached_and_shares_one_bounded_pool": "06",
    "test_runtime.Caches.test_git_cache_notices_a_repo_that_changed": "06",
    "test_runtime.Caches.test_git_cache_expires_and_is_bounded": "06",
    "test_runtime.Caches.test_stats_does_not_build_the_projects_board": "05",
    "test_runtime.Caches.test_unchanged_notes_are_not_read_again_but_changed_ones_are": "01",
    "test_runtime.Caches.test_a_same_size_same_mtime_edit_just_after_a_read_is_still_seen": "01",
    "test_runtime.Caches.test_stat_cache_is_bounded": "01",
}
FUNC_OWNER = {"github_repo_of": "09", "running_server": "11", "stop_server": "11", "port_pid": "11", "is_triage_process": "11", "open_page": "11",
              "service_owns": "11", "in_own_scope": "11", "restart_service": "11", "open_argv": "06", "open_target": "06"}
# Python functions that make a request to the port they are given (a route call when that port is the fixture's).
FUNC_ROUTES = {"running_server": [("GET", "/api/version")]}


def closure(t):
    out, todo = set(), [t]
    while todo:
        x = todo.pop()
        if x not in out: out.add(x); todo += BLOCKERS.get(x, [])
    return out


# ---- reading the files -------------------------------------------------------------------------------------------------
def sources(rev=None):
    names = sorted(f for f in os.listdir(ROOT) if re.match(r"^test_.*\.py$", f)) + ["testkit.py"]
    if rev:
        listed = subprocess.run(["git", "-C", ROOT, "ls-tree", "--name-only", rev], capture_output=True, text=True, check=True).stdout.split()
        names = sorted(f for f in listed if re.match(r"^test_.*\.py$", f)) + ["testkit.py"]
        return {n: subprocess.run(["git", "-C", ROOT, "show", f"{rev}:{n}"], capture_output=True, text=True, check=True).stdout for n in names}
    out = {}
    for n in names:
        with open(os.path.join(ROOT, n), encoding="utf-8") as fh: out[n] = fh.read()
    return out


def server_data_names(rev=None):
    """Module-level names in server.py that are data, not functions or classes (their reads are state reads)."""
    text = (subprocess.run(["git", "-C", ROOT, "show", f"{rev}:server.py"], capture_output=True, text=True, check=True).stdout
            if rev else open(os.path.join(ROOT, "server.py"), encoding="utf-8").read())
    names = set()
    for node in ast.parse(text).body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, (ast.AnnAssign, ast.AugAssign)) else []
        for t in targets:
            for n in ast.walk(t):
                if isinstance(n, ast.Name): names.add(n.id)
    return names


def dotted(node):
    if isinstance(node, ast.Name): return node.id
    if isinstance(node, ast.Attribute):
        base = dotted(node.value); return base + "." + node.attr if base else None
    return None


def const_str(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


class Module:
    def __init__(self, name, text):
        self.name, self.file = name[:-3], name
        self.tree = ast.parse(text, name)
        self.classes, self.funcs, self.imports = {}, {}, {}
        self.scan(self.tree.body)

    def scan(self, body):
        for node in body:
            if isinstance(node, ast.ClassDef): self.classes[node.name] = Class(self, node)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)): self.funcs[node.name] = node
            elif isinstance(node, ast.ImportFrom) and node.module:
                for a in node.names: self.imports[a.asname or a.name] = (node.module, a.name)
            elif isinstance(node, (ast.If, ast.Try)):  # ownership.md: CommitToast is defined inside `if BrowserCase is not None:`
                self.scan(node.body); self.scan(getattr(node, "orelse", []))
                for h in getattr(node, "handlers", []): self.scan(h.body)


class Class:
    def __init__(self, mod, node):
        self.mod, self.node, self.name = mod, node, node.name
        self.qual = f"{mod.name}.{node.name}"
        self.methods, self.aliases = {}, {}
        for n in node.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)): self.methods[n.name] = n
            elif isinstance(n, ast.Assign):  # `get = ApiTestCase.get`, `_restore, get = ApiTestCase._restore, ApiTestCase.get`
                pairs = (zip(n.targets[0].elts, n.value.elts) if isinstance(n.targets[0], ast.Tuple) and isinstance(n.value, ast.Tuple)
                         else [(n.targets[0], n.value)])
                for t, v in pairs:
                    if isinstance(t, ast.Name) and isinstance(v, ast.Attribute) and isinstance(v.value, ast.Name):
                        self.aliases[t.id] = (v.value.id, v.attr)


# ---- patch sites ---------------------------------------------------------------------------------------------------------
Site = collections.namedtuple("Site", "file line scope target kind dynamic")


def patch_target(call):
    """(target, dynamic) when `call` is a mock.patch / patch.object / patch.dict / patch.multiple call, else None."""
    f = dotted(call.func) or ""
    if not re.search(r"(^|\.)patch(\.object|\.dict|\.multiple)?$", f): return None  # mock.patch, um.patch.object, patch.dict...
    a = call.args
    if f.endswith(".object"):
        obj = dotted(a[0]) if a else None; attr = const_str(a[1]) if len(a) > 1 else None
        return (f"{obj or '<expr>'}.{attr or '<dynamic>'}", attr is None or obj is None)
    if f.endswith(".dict"): return (f"{dotted(a[0]) if a else '<expr>'} (patch.dict)", not a or dotted(a[0]) is None)
    if f.endswith(".multiple"): return (f"{dotted(a[0]) if a else '<expr>'} (patch.multiple)", False)
    t = const_str(a[0]) if a else None
    return (t or "<dynamic>", t is None)


def assign_targets(node):
    """For an assignment, each target that changes a module's state: server.X, server.X[k], os.environ[k]."""
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    out = []
    def visit(t):
        if isinstance(t, (ast.Tuple, ast.List)):
            for e in t.elts: visit(e)
        elif isinstance(t, ast.Attribute) and dotted(t) and dotted(t).startswith("server."): out.append(dotted(t) + " =")
        elif isinstance(t, ast.Subscript):
            base = dotted(t.value)
            if base and (base.startswith("server.") or base == "os.environ"):
                key = const_str(t.slice); out.append(f"{base}[{key!r}] =" if key is not None else f"{base}[<dynamic>] =")
    for t in targets: visit(t)
    return out


def find_sites(mod, data_names):
    """Every site in a module, keyed by node id, with the qualified scope it is in."""
    sites = {}
    stored = {id(n.value) for n in ast.walk(mod.tree) if isinstance(n, ast.Subscript) and isinstance(n.ctx, ast.Store)}
    def walk(node, scope):
        for child in ast.iter_child_nodes(node):
            sc = scope
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)): sc = scope + [child.name]
            here = ".".join(sc) or "<module>"
            if isinstance(child, ast.Call):
                pt = patch_target(child)
                f = dotted(child.func) or ""
                if pt: sites[id(child)] = Site(mod.file, child.lineno, here, pt[0], "patch", pt[1])
                elif f.endswith("fail_nth"):
                    t = const_str(child.args[0]) if child.args else None
                    sites[id(child)] = Site(mod.file, child.lineno, here, f"fail_nth({t or '<dynamic>'})", "fail_nth", t is None)
                elif f in ("setattr", "builtins.setattr") and child.args and dotted(child.args[0]) == "server":
                    t = const_str(child.args[1]) if len(child.args) > 1 else None
                    sites[id(child)] = Site(mod.file, child.lineno, here, f"server.{t or '<dynamic>'} =", "assign", t is None)
                elif f in ("os.environ.pop", "os.environ.update", "os.environ.clear", "os.environ.setdefault"):
                    sites[id(child)] = Site(mod.file, child.lineno, here, f, "env", False)
                if (f == "self.addCleanup" or f.endswith(".addCleanup")) and child.args and dotted(child.args[0]) == "setattr":
                    t = const_str(child.args[2]) if len(child.args) > 2 else None
                    if len(child.args) > 1 and dotted(child.args[1]) == "server":
                        sites[id(child)] = Site(mod.file, child.lineno, here, f"server.{t or '<dynamic>'} = (restore)", "assign", t is None)
            elif isinstance(child, (ast.Assign, ast.AugAssign)):
                for k, t in enumerate(assign_targets(child)):
                    sites[(id(child), k)] = Site(mod.file, child.lineno, here, t, "env" if t.startswith("os.environ") else "assign", "<dynamic>" in t)
            elif (isinstance(child, ast.Attribute) and isinstance(child.ctx, ast.Load) and isinstance(child.value, ast.Name)
                  and child.value.id == "server" and child.attr.startswith("_") and child.attr in data_names
                  and id(child) not in stored):  # `server._viewer["login"] = ...` is the assignment's row, not a read
                sites[id(child)] = Site(mod.file, child.lineno, here, f"server.{child.attr}", "state", False)
            walk(child, sc)
    walk(mod.tree, [])
    return sites


# ---- one test, walked ----------------------------------------------------------------------------------------------------
class Walk:
    """What one test reaches: route calls (in order, setUp first), CLI calls, sites, helpers, direct server calls."""
    def __init__(self, ledger, cls):
        self.L, self.cls = ledger, cls
        self.events, self.sites, self.helpers, self.direct, self.flags = [], [], [], [], set()
        self.stack = []

    # resolving names
    def mro(self, cls):
        out = []
        def add(c):
            if c is None or c in out: return
            out.append(c)
            for b in c.node.bases: add(self.L.resolve_class(c.mod, b))
        add(cls); return out

    def method(self, name, start_after=None):
        chain = self.mro(self.cls)
        if start_after is not None and start_after in chain: chain = chain[chain.index(start_after) + 1:]
        for c in chain:
            if name in c.methods: return c, c.methods[name]
            if name in c.aliases:
                owner = self.L.resolve_class(c.mod, ast.Name(id=c.aliases[name][0]))
                if owner and c.aliases[name][1] in owner.methods: return owner, owner.methods[c.aliases[name][1]]
        return None, None

    def run(self, cls, func, env, setup):
        key = (cls.qual if cls else "", func.name, func.lineno)
        if key in self.stack or len(self.stack) > 12: return
        self.stack.append(key)
        if cls is not None: self.helpers.append(f"{cls.name}.{func.name}")
        mod = cls.mod if cls is not None else self.L.func_mod[id(func)]
        for d in func.decorator_list: self.visit(d, mod, cls, env, setup)
        for stmt in func.body: self.visit(stmt, mod, cls, env, setup)
        self.stack.pop()

    def bind(self, func, call, env):
        """Parameter name -> (node, env it is read in), for substituting a helper's arguments."""
        a = func.args; params = [p.arg for p in a.posonlyargs + a.args]
        out = {}
        args = list(call.args) if call is not None else []
        if params and params[0] in ("self", "cls"): params = params[1:]
        defaults = dict(zip(reversed([p.arg for p in a.posonlyargs + a.args]), reversed(a.defaults)))
        for p, d in defaults.items(): out[p] = (d, {})
        for p, v in zip(params, args): out[p] = (v, env)
        if a.vararg: out["*" + a.vararg.arg] = ([x for x in args[len(params):]], env)
        if call is not None:
            for kw in call.keywords:
                if kw.arg: out[kw.arg] = (kw.value, env)
        return out

    def resolve(self, node, env, depth=0):
        """Follow a Name through helper parameters back to the caller's expression."""
        while isinstance(node, ast.Name) and node.id in env and depth < 12:
            node, env = env[node.id]; depth += 1
        return node, env

    def path_of(self, node, env):
        node, env = self.resolve(node, env)
        s = const_str(node)
        if s is None and isinstance(node, ast.JoinedStr) and node.values: s = const_str(node.values[0])
        if s is None and isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add): return self.path_of(node.left, env)
        if s is None and isinstance(node, ast.IfExp): return self.path_of(node.body, env)
        if s is None: return None
        s = s.split("?")[0]
        return s if s.startswith("/") else None

    def body_fields(self, node, env):
        node, env = self.resolve(node, env)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and dotted(node.func.value) == "self":
            c, fn = self.method(node.func.attr)  # self._push(days=3): the dict the helper returns
            ret = next((r.value for r in ast.walk(fn) if isinstance(r, ast.Return) and isinstance(r.value, ast.Dict)), None) if fn else None
            if ret is not None: return self.body_fields(ret, self.bind(fn, node, env))
        if not isinstance(node, ast.Dict): return None
        out = {}
        for k, v in zip(node.keys, node.values):
            if k is None:  # **extra
                inner = self.body_fields(v, env)
                if inner: out.update({x: y for x, y in inner.items() if x not in out})
                continue
            key = const_str(k)
            if key is None: continue
            vv, venv = self.resolve(v, env)
            if getattr(vv, "_multi", False): vv = vv.elts[-1] if vv.elts else vv  # one representative; route() sees each body
            out[key] = const_str(vv) if const_str(vv) is not None else "<expr>"
        return out

    # the visitor
    def visit(self, node, mod, cls, env, setup):
        site = self.L.site_at.get(id(node))
        if site: self.sites.append(site)
        if isinstance(node, (ast.Assign, ast.AugAssign)):
            for k in range(4):
                s = self.L.site_at.get((id(node), k))
                if s: self.sites.append(s)
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            env[node.targets[0].id] = (node.value, env)  # a local: `body = {...}; self.post(path, body)`
        if isinstance(node, ast.For):  # `for body in ({...}, {...})`, `for i, act in enumerate(("kill", "reference"))`
            it, tgt = node.iter, node.target
            if isinstance(it, ast.Call) and dotted(it.func) == "enumerate" and it.args and isinstance(tgt, ast.Tuple) and len(tgt.elts) == 2:
                it, tgt = it.args[0], tgt.elts[1]
            if isinstance(tgt, ast.Name) and isinstance(it, (ast.Tuple, ast.List)):
                multi = ast.List(elts=list(it.elts), ctx=ast.Load()); multi._multi = True; env[tgt.id] = (multi, env)
        if isinstance(node, ast.Name) and node.id == "CODE": self.flags.add("pysrc")
        if isinstance(node, ast.Attribute) and dotted(node) == "server.__file__": self.flags.add("pysrc")
        if isinstance(node, ast.Call):
            node.func._called = True
            for child in ast.iter_child_nodes(node): self.visit(child, mod, cls, env, setup)
            self.call(node, mod, cls, env, setup); return
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self" and isinstance(node.ctx, ast.Load):
            if node.attr not in ("get", "post", "call"):  # a bound method handed on (addCleanup, Thread target) still runs
                c, f = self.method(node.attr)
                if f is not None and not getattr(node, "_called", False): self.run(c, f, self.bind(f, None, env), setup)
        for child in ast.iter_child_nodes(node): self.visit(child, mod, cls, env, setup)

    def call(self, node, mod, cls, env, setup):
        f = node.func; name = dotted(f) or ""
        # the fixture's HTTP helpers
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "self" and f.attr in ("get", "post", "call"):
            method = "GET" if f.attr == "get" or (f.attr == "call" and len(node.args) < 2) else "POST"
            path = self.path_of(node.args[0], env) if node.args else None
            bodies = [None]
            if len(node.args) > 1:
                b, benv = self.resolve(node.args[1], env)
                bodies = [self.body_fields(x, benv) for x in b.elts] if getattr(b, "_multi", False) else [self.body_fields(b, benv)]
            for body in bodies: self.route(method, path, body, setup, node)
            return
        # self.helper(...), super().helper(...), Class.helper(self, ...)
        target = None
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "self":
            c, fn = self.method(f.attr); target = (c, fn, node)
        elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Call) and dotted(f.value.func) == "super":
            c, fn = self.method(f.attr, start_after=cls); target = (c, fn, node)
        elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
            owner = self.L.resolve_class(mod, f.value)
            if owner is not None and f.attr in owner.methods:
                fake = ast.Call(func=f, args=node.args[1:], keywords=node.keywords)  # drop the explicit self
                target = (owner, owner.methods[f.attr], fake)
        elif isinstance(f, ast.Name):
            fn, fmod = self.L.resolve_func(mod, f.id)
            if fn is not None: target = (None, fn, node)
        if target and target[1] is not None:
            self.run(target[0], target[1], self.bind(target[1], target[2], env), setup)
        # server.<function>(...)
        if name.startswith("server.") and name.count(".") == 1:
            fn = name.split(".")[1]; self.direct.append(fn)
            if fn in FUNC_ROUTES and any(dotted(a) == "self.port" for a in node.args):
                for m, p in FUNC_ROUTES[fn]: self.route(m, p, None, setup, node)
        # the CLI as a process: [sys.executable, CLI, ...], [*cli_argv(), ...], [sys.executable, self.cli, ...]
        if name in ("subprocess.run", "subprocess.Popen", "subprocess.check_output", "subprocess.call") and node.args:
            argv = node.args[0]
            if isinstance(argv, ast.List) and self.is_cli(argv): self.cli_call(argv, env, setup)
        # a request built by hand to the fixture's port: urllib on f"http://127.0.0.1:{self.port}{path}", http.client
        if name.endswith(("urlopen", "Request", "HTTPConnection")) and self.mentions_port(node):
            if not self.raw_request(node, env, setup): self.flags.add("raw-http")

    def pick(self, node, env):
        """An `a if x is None else b` the caller's arguments decide (closeout's remote(path, body=None)); else node."""
        node, env = self.resolve(node, env)
        while isinstance(node, ast.IfExp):
            t = node.test
            if (isinstance(t, ast.Compare) and len(t.ops) == 1 and isinstance(t.ops[0], (ast.Is, ast.IsNot))
                    and isinstance(t.comparators[0], ast.Constant) and t.comparators[0].value is None):
                v, _ = self.resolve(t.left, env)
                none = isinstance(v, ast.Constant) and v.value is None
                node = node.body if none == isinstance(t.ops[0], ast.Is) else node.orelse
            else: node = node.body
            node, env = self.resolve(node, env)
        return node, env

    def raw_request(self, node, env, setup):
        url = node.args[0] if node.args else None
        if not isinstance(url, ast.JoinedStr): return False
        vals = url.values
        at = next((i for i, v in enumerate(vals) if isinstance(v, ast.FormattedValue) and dotted(v.value) == "self.port"), None)
        if at is None or at + 1 >= len(vals): return False
        nxt = vals[at + 1]
        path = self.path_of(nxt.value if isinstance(nxt, ast.FormattedValue) else nxt, env)
        if path is None: return False
        kw = {k.arg: k.value for k in node.keywords if k.arg}
        data, denv = self.pick(kw["data"], env) if "data" in kw else (None, env)
        has_data = data is not None and not (isinstance(data, ast.Constant) and data.value is None)
        m, _ = self.pick(kw["method"], env) if "method" in kw else (None, env)
        method = const_str(m) or ("POST" if has_data else "GET")
        body = None
        if has_data:
            dumps = next((c for c in ast.walk(data) if isinstance(c, ast.Call) and (dotted(c.func) or "").endswith("dumps") and c.args), None)
            if dumps is not None: body = self.body_fields(dumps.args[0], denv)
        self.route(method, path, body, setup, node); return True

    def is_cli(self, argv):
        for e in argv.elts:
            x = e.value if isinstance(e, ast.Starred) else e
            if isinstance(x, ast.Call) and dotted(x.func) == "cli_argv": return True
            if dotted(x) in ("CLI", "cli"): return True
            if dotted(x) == "self.cli": self.flags.add("pysrc"); return False  # a copy of bin/rawtriage, or the installer's link to it
        return False

    def cli_call(self, argv, env, setup):
        words = []
        for e in argv.elts:
            if isinstance(e, ast.Starred):
                if isinstance(e.value, ast.Name) and "*" + e.value.id in env:
                    items, venv = env["*" + e.value.id]
                    words += [const_str(self.resolve(x, venv)[0]) for x in items]
                continue
            s = const_str(e)
            if s is not None: words.append(s)
        words = [w for w in words if w is not None]
        self.events.append(("cli", " ".join(words), setup))
        first = next((w for w in words if not w.startswith("--") or w == "-l"), None)
        r = None
        if first == "project" and len(words) > 1: r = CLI_PROJECT.get(words[1])
        elif first == "reference" and "--unindexed" in words: r = ("GET", "/api/reference/unindexed")
        elif first is None: r = ("GET", "/api/setup")
        else: r = CLI_ROUTES.get(first)
        if r: self.events.append(("route", r[0], r[1], {"action": r[2]} if len(r) > 2 else None, setup, "cli"))

    def mentions_port(self, node):
        return any(dotted(n) == "self.port" for n in ast.walk(node))

    def route(self, method, path, body, setup, node):
        self.events.append(("route", method, path, body, setup, "http"))


# ---- the ledger ----------------------------------------------------------------------------------------------------------
Test = collections.namedtuple("Test", "id module cls name outcome reason owner due uses calls_route cli routes setup_routes "
                                      "helpers rows pending unplaced")


class Ledger:
    def __init__(self, rev=None):
        self.rev = rev
        self.mods = {n[:-3]: Module(n, t) for n, t in sources(rev).items()}
        data = server_data_names(rev)
        self.site_at, self.sites = {}, []
        for m in self.mods.values():
            found = find_sites(m, data); self.site_at.update(found)
            self.sites += sorted(set(found.values()), key=lambda s: (s.file, s.line, s.target, s.kind))
        self.func_mod = {id(f): m for m in self.mods.values() for f in m.funcs.values()}
        for m in self.mods.values():
            for c in m.classes.values():
                for f in c.methods.values(): self.func_mod[id(f)] = m
        self.rows = load_rows()
        self.tests = []

    def resolve_class(self, mod, node):
        name = dotted(node)
        if name is None: return None
        if name in mod.classes: return mod.classes[name]
        if name in mod.imports:
            src, real = mod.imports[name]
            if src in self.mods: return self.resolve_class(self.mods[src], ast.Name(id=real))
        return None

    def resolve_func(self, mod, name):
        if name in mod.funcs: return mod.funcs[name], mod
        if name in mod.imports:
            src, real = mod.imports[name]
            if src in self.mods and real in self.mods[src].funcs: return self.mods[src].funcs[real], self.mods[src]
        return None, None

    def is_testcase(self, cls, seen=()):
        for b in cls.node.bases:
            n = dotted(b) or ""
            if n in ("unittest.TestCase", "TestCase"): return True
            c = self.resolve_class(cls.mod, b)
            if c is not None and c not in seen and self.is_testcase(c, seen + (cls,)): return True
        return False

    def test_methods(self, cls):
        """unittest's loader: every callable attribute starting with "test", own or inherited, in name order."""
        names = {}
        w = Walk(self, cls)
        for c in reversed(w.mro(cls)):
            for n, f in c.methods.items():
                if n.startswith("test"): names[n] = (c, f)
        return sorted(names.items())

    def build(self):
        for mod in self.mods.values():
            if mod.name == "testkit": continue
            for cls in mod.classes.values():
                if not self.is_testcase(cls): continue
                for name, (owner_cls, func) in self.test_methods(cls):
                    self.tests.append(self.place(mod, cls, name, owner_cls, func))
        self.tests.sort(key=lambda t: t.id)
        return self

    # placing one test
    def place(self, mod, cls, name, def_cls, func):
        w = Walk(self, cls)
        setup_cls, setup = w.method("setUp")
        if setup is not None: w.run(setup_cls, setup, {}, True)
        w.run(def_cls, func, {}, False)
        tid = f"{mod.name}.{cls.name}.{name}"
        unplaced = []
        browser = any(c.name == "BrowserCase" for c in w.mro(cls))
        routes = [e for e in w.events if e[0] == "route"]
        loose = None
        if "raw-http" in w.flags:  # a request built by hand (urllib on self.port, http.client): the routes the test spells out
            loose = self.loose_routes(func, cls, w)
            have = {(e[1], e[2]) for e in routes}
            routes += [("route", m, p, b, False, "http") for m, p, b in loose if (m, p) not in have] or (
                [] if any(not e[4] for e in routes) else [("route", "GET", "/", None, False, "http")])
        expanded = []
        for e in routes:  # a path or body the walk can't follow (a loop variable): every route the test spells out
            if e[2] is None or (e[2] in ("/api/action", "/api/project/status") and e[3] is None and e[5] == "http"):
                loose = self.loose_routes(func, cls, w) if loose is None else loose
                cand = [x for x in loose if e[2] is None or x[1] == e[2]]
                if cand: expanded += [("route", m, p, b, e[4], e[5]) for m, p, b in cand]; continue
            expanded.append(e)
        routes = expanded
        cli = any(e[0] == "cli" for e in w.events)
        # tickets of the routes, in order; undo takes the ticket of the change it undoes
        placed, last_change = [], None
        for e in routes:
            _, method, path, body, setup_ev, how = e
            t, why = self.route_ticket(method, path, body, last_change, w, func)
            if why: unplaced.append(why)
            if t is None: continue
            mutating = method == "POST" and path not in (None,) and path != "/api/open"
            if mutating and path != "/api/undo": last_change = t
            placed.append((t, mutating, setup_ev, f"{method} {path}" + (f":{body.get('action')}" if body and body.get("action") else "")))
        if browser: placed.append(("12", False, False, "page"))
        uses = {t for t, *_ in placed} | ({"10"} if cli else set())
        if any("inflight" in (const_str(n) or "") for n in ast.walk(func)):  # inflight inside GET /api/notes is 06's
            uses.add("06"); placed.append(("06", False, False, "GET /api/notes:inflight"))
        calls_route = bool(placed) or cli
        rows = [self.row_for(s) for s in dict.fromkeys(w.sites)]
        rows = [r for r in rows if r is not None]
        # owner
        rule = TEST_OWNER.get(tid) or CLASS_OWNER.get(f"{mod.name}.{cls.name}") or CLASS_OWNER.get(f"{mod.name}.*")
        owner = rule
        if isinstance(rule, tuple) or rule == ROUTE:
            own = [p for p in placed if not p[2]] or placed
            funcs = [FUNC_OWNER[f] for f in w.direct if f in FUNC_OWNER]
            if funcs and not placed: owner = funcs[-1]  # a direct-call test: the ticket that ports the function
            elif any(p[1] for p in own): owner = [p[0] for p in own if p[1]][-1]
            elif own: owner = max((p[0] for p in own), key=BUILD_ORDER.index)
            else: owner = None
            if isinstance(rule, tuple): owner = owner if owner in rule[1] else rule[0]
            elif owner is None: unplaced.append("owner by route, but it calls none")
        if rule is None: unplaced.append("no owner rule for its class")
        # outcome
        r_rows = [r for r in rows if r["disposition"].startswith("R")]
        if r_rows: outcome, reason = "ported", f"R row {r_rows[0]['file']}:{r_rows[0]['line']} {r_rows[0]['target']}"
        elif "pysrc" in w.flags: outcome, reason = "ported", "runs a copy of the Python code (server.py / bin/rawtriage)"
        elif not calls_route: outcome, reason = "ported", "calls no route and no CLI"
        else: outcome, reason = "runs", ""
        # due
        due = None
        if owner:
            if outcome == "ported" and not calls_route: due = owner
            else:
                need = uses | {owner}
                due = next((c for c in [owner] + DUE_AFTER_OWNER if need <= closure(c)), None)
                if due is None: unplaced.append(f"no due ticket covers {sorted(need)}")
        pending = [r for r in rows if r["pr"] != "1" and not r["disposition"].startswith(("R", "none", "N/A"))]
        def fmt(ps): return ",".join(dict.fromkeys(p[3] for p in ps))
        return Test(tid, mod.name, cls.name, name, outcome, reason, owner or "", due or "",
                    ",".join(sorted(uses, key=BUILD_ORDER.index)), "y" if calls_route else "n", "y" if cli else "n",
                    fmt([p for p in placed if not p[2]]), fmt([p for p in placed if p[2]]),
                    ",".join(dict.fromkeys(w.helpers)), ",".join(f"{r['file']}:{r['line']}" for r in rows),
                    ",".join(f"{r['file']}:{r['line']}" for r in pending), "; ".join(dict.fromkeys(unplaced)))

    def loose_routes(self, func, cls, w):
        """Every route the test's own body (and its class's constants) spells out, paired with the body next to it in a
        tuple ("/api/action", {...}) where there is one: for calls whose path or body is a loop variable."""
        nodes = [func] + [n for c in w.mro(cls) for n in c.node.body if isinstance(n, ast.Assign)]
        out, paired = [], set()
        for root in nodes:
            for n in ast.walk(root):
                if isinstance(n, ast.Tuple) and len(n.elts) >= 2 and const_str(n.elts[0]) and isinstance(n.elts[1], ast.Dict):
                    p = const_str(n.elts[0]).split("?")[0]
                    if ROUTE_STR.match(p):
                        out.append((self.method_of(p), p, w.body_fields(n.elts[1], {}))); paired.add(id(n.elts[0]))
        for root in nodes:
            for n in ast.walk(root):
                s = const_str(n)
                if s and id(n) not in paired and s.startswith("/api/") and ROUTE_STR.match(s.split("?")[0]):
                    p = s.split("?")[0]; out.append((self.method_of(p), p, None))
        seen, uniq = set(), []
        for m, p, b in out:
            k = (m, p, repr(sorted((b or {}).items())))
            if k not in seen: seen.add(k); uniq.append((m, p, b))
        return uniq

    @staticmethod
    def method_of(p): return "POST" if p in POST_ROUTES and p not in GET_ROUTES else "GET"

    def route_ticket(self, method, path, body, last_change, w, func):
        if path is None: return None, "a route that isn't a literal"
        if FONT.match(path): return "01", None
        if method == "GET":
            if path in GET_ROUTES: return GET_ROUTES[path], None
            return "01", None  # an unknown route: the router's 404 (01)
        if path not in POST_ROUTES: return "01", None
        if path == "/api/undo": return last_change or "03", None
        if path == "/api/action":
            a = (body or {}).get("action")
            if a in ACTIONS: return ACTIONS[a], None
            if body is None: return "03", None if self.only_actions(func, ("keep", "kill")) else "an /api/action body that isn't a literal"
            return "03", None if a is None else f"/api/action action {a!r} isn't a literal"
        if path == "/api/project/status":
            a = (body or {}).get("action")
            if a == "extend": return "07", None
            if body is None or a not in ("shipped", "kill"): return "08", "/api/project/status: file or folder unknown (body isn't a literal)"
            p = body.get("path")
            if p is None or p == "<expr>":
                return ("08", None) if "name" in body else ("08", "/api/project/status: file or folder unknown")
            return ("08", None) if p.count("/") >= 2 else ("07", None)
        return POST_ROUTES[path], None

    def only_actions(self, func, allowed):
        acts = {const_str(v) for n in ast.walk(func) if isinstance(n, ast.Dict)
                for k, v in zip(n.keys, n.values) if const_str(k) == "action"}
        return bool(acts) and acts <= set(allowed)

    def row_for(self, site):
        return self.rows_by_site().get(site_key_of(self, site))

    def rows_by_site(self):
        if not hasattr(self, "_rbs"):
            self._rbs = {}
            for key, row in zip(site_keys(self.sites), self.match_rows()): self._rbs[key] = row
        return self._rbs

    def match_rows(self):
        """The row of tools/patch_sites.tsv for each site, in self.sites order (None where it has none)."""
        by_key = {row_key(r): r for r in self.rows}
        return [by_key.get(k) for k in site_keys(self.sites)]


def site_keys(sites):
    """(file, scope, target, kind, n): n counts repeats of the same target in the same scope, in line order."""
    seen = collections.Counter(); out = []
    for s in sites:
        k = (s.file, s.scope, s.target, s.kind); seen[k] += 1; out.append(k + (seen[k],))
    return out


def site_key_of(ledger, site):
    if not hasattr(ledger, "_ski"): ledger._ski = dict(zip(ledger.sites, site_keys(ledger.sites)))
    return ledger._ski.get(site)


def row_key(r): return (r["file"], r["scope"], r["target"], r["kind"], int(r["n"]))


ROW_FIELDS = ["file", "line", "scope", "target", "kind", "n", "disposition", "owner", "pr", "source", "note"]


def load_rows():
    try:
        with open(SITES_TSV, encoding="utf-8", newline="") as fh: return list(csv.DictReader(fh, delimiter="\t"))
    except FileNotFoundError: return []


def load_allow():
    try:
        with open(ALLOW_TSV, encoding="utf-8", newline="") as fh:
            return {r["key"]: r["reason"] for r in csv.DictReader(fh, delimiter="\t")}
    except FileNotFoundError: return {}


def build(rev=None): return Ledger(rev).build()


# ---- reports ------------------------------------------------------------------------------------------------------------
def tsv(rows, fields):
    out = io.StringIO(); w = csv.writer(out, delimiter="\t", lineterminator="\n"); w.writerow(fields)
    for r in rows: w.writerow([getattr(r, f) if not isinstance(r, dict) else r.get(f, "") for f in fields])
    return out.getvalue()


def summary(L):
    out = []
    owned = collections.Counter(t.owner for t in L.tests); due = collections.Counter(t.due for t in L.tests)
    pure = collections.Counter(t.owner for t in L.tests if t.outcome == "ported" and t.calls_route == "n")
    ported = collections.Counter(t.owner for t in L.tests if t.outcome == "ported")
    out.append(f"tests: {len(L.tests)}   runs against Rust: {sum(t.outcome == 'runs' for t in L.tests)}   "
               f"ported: {sum(t.outcome == 'ported' for t in L.tests)} (calls no route/CLI: {sum(pure.values())})")
    out.append("ticket  owned  due  pure  ported   ownership.md (owned/due/pure)   over five")
    for t in sorted(set(owned) | set(due) | set(APPROX), key=lambda x: BUILD_ORDER.index(x) if x in BUILD_ORDER else 99):
        a = APPROX.get(t); flag = ""
        if a:
            d = [n for n, x, y in (("owned", owned[t], a[0]), ("due", due[t], a[1]), ("pure", pure[t], a[2])) if abs(x - y) > 5]
            flag = ", ".join(f"{n} {x:+d}" for n, x in zip(d, [(owned[t] - a[0]) if n == "owned" else (due[t] - a[1]) if n == "due"
                                                                else (pure[t] - a[2]) for n in d]))
        out.append(f"{t or '?':>6}  {owned[t]:>5}  {due[t]:>3}  {pure[t]:>4}  {ported[t]:>6}   "
                   f"{'%d/%d/%d' % a if a else '-':>29}   {flag}")
    un = [t for t in L.tests if t.unplaced]
    out.append(f"unplaceable: {len(un)}")
    for t in un: out.append(f"  {t.id}: {t.unplaced}  (owner {t.owner or '?'}, due {t.due or '?'})")
    return "\n".join(out) + "\n"


def check(L):
    problems = []
    rows, allow = L.match_rows(), load_allow()
    keys = site_keys(L.sites)
    for s, k, r in zip(L.sites, keys, rows):
        if r is None: problems.append(f"site without a row: {s.file}:{s.line} {s.scope} {s.target} ({s.kind})")
        elif r["disposition"] in ("", "?"): problems.append(f"row without a disposition: {s.file}:{s.line} {s.target}")
        elif str(s.line) != r["line"]: problems.append(f"row's line is stale: {s.file}:{s.line} (row says {r['line']}) {s.target}")
        if s.dynamic and "|".join(map(str, k)) not in allow:
            problems.append(f"dynamic site not in {os.path.relpath(ALLOW_TSV, ROOT)}: {'|'.join(map(str, k))}")
    found = set(keys)
    for r in L.rows:
        if row_key(r) not in found: problems.append(f"row without a site: {r['file']}:{r['line']} {r['scope']} {r['target']}")
    if not L.rev:  # the loader's view, to catch a test the walk misses (or invents)
        import unittest
        sys.path.insert(0, ROOT); cwd = os.getcwd(); os.chdir(ROOT)
        try: suite = unittest.defaultTestLoader.discover(ROOT)
        finally: os.chdir(cwd)
        ids = set()
        def walk(s):
            for x in s:
                if isinstance(x, unittest.TestSuite): walk(x)
                elif not type(x).__name__.startswith("_Failed"): ids.add(x.id())
                else: problems.append(f"import failed: {x.id()}")
        walk(suite)
        mine = {t.id for t in L.tests}
        for i in sorted(ids - mine): problems.append(f"test the walk doesn't list: {i}")
        for i in sorted(mine - ids):
            if not i.startswith("test_autocommit.CommitToast"):  # only defined when Chromium is found
                problems.append(f"test the loader doesn't find: {i}")
    return problems


def write_sites(L):
    old = {row_key(r): r for r in L.rows}
    out = []
    for s, k in zip(L.sites, site_keys(L.sites)):
        r = dict(old.get(k) or {"disposition": "?", "owner": "", "pr": "", "source": "new", "note": ""})
        r.update(file=s.file, line=str(s.line), scope=s.scope, target=s.target, kind=s.kind, n=str(k[4]))
        out.append(r)
    with open(SITES_TSV, "w", encoding="utf-8", newline="") as fh: fh.write(tsv(out, ROW_FIELDS))
    return len(out)


FIELDS = ["id", "module", "cls", "name", "outcome", "reason", "owner", "due", "uses", "calls_route", "cli", "routes",
          "setup_routes", "helpers", "rows", "pending", "unplaced"]


def main(argv):
    rev = argv[argv.index("--rev") + 1] if "--rev" in argv else None
    L = build(rev)
    if "--write-sites" in argv: print(f"{write_sites(L)} sites written to {os.path.relpath(SITES_TSV, ROOT)}"); return 0
    if "--sites" in argv:
        rows = L.match_rows()
        out = [{**(r or {}), **s._asdict(), "n": k[4]} for s, k, r in zip(L.sites, site_keys(L.sites), rows)]
        sys.stdout.write(tsv(out, ROW_FIELDS + ["dynamic"])); return 0
    if "--check" in argv:
        problems = check(L)
        for p in problems: print(p)
        print(f"{len(L.tests)} tests, {len(L.sites)} sites: " + (f"{len(problems)} problem(s)" if problems else "complete"))
        return 1 if problems else 0
    if "--summary" in argv: sys.stdout.write(summary(L)); return 0
    sys.stdout.write(tsv(L.tests, FIELDS)); return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
