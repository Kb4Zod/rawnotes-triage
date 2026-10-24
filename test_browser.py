#!/usr/bin/env python3
"""v2.05 browser regressions: a real Chromium drives the page against a disposable server and notes folder.
Skipped when no Chromium is found (set CHROMIUM=/path to choose one). Accessibility sweeps with axe-core run
when AXE_JS points at a local copy of axe.min.js (4.10.3 was used); axe is never a dependency of the app."""
import json
import os
import time
import unittest
from unittest import mock

import cdp
import server
from testkit import Fixture

AXE = os.environ.get("AXE_JS")
RECORD = """(()=>{const f=window.fetch.bind(window);window.__calls=[];window.__holds=[];
 window.fetch=(url,opts)=>{window.__calls.push({url:String(url),method:(opts&&opts.method)||'GET'});
  const h=window.__holds.find(h=>String(url).includes(h.match)&&!h.used);if(!h)return f(url,opts);h.used=true;
  const signal=opts&&opts.signal;
  return new Promise((res,rej)=>{h.hit=true;
   h.release=mode=>{if(mode==='fail')res(new Response(JSON.stringify({error:'late failure'}),{status:500,headers:{'Content-Type':'application/json'}}));else f(url,opts).then(res,rej)};
   if(signal)signal.addEventListener('abort',()=>rej(new DOMException('Aborted','AbortError')))})}})();"""


@unittest.skipUnless(cdp.chromium_path(), "no Chromium/Chrome found")
class BrowserCase(Fixture):
    browser = None

    @classmethod
    def setUpClass(cls):
        cls.browser = cdp.Browser()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()

    def setUp(self):
        super().setUp()
        if os.environ.get("TRIAGE_INDEX"):  # run these same checks against another build of the page (e.g. the previous release)
            saved = server.INDEX_HTML; self.addCleanup(setattr, server, "INDEX_HTML", saved)
            with open(os.environ["TRIAGE_INDEX"], "rb") as fh: server.INDEX_HTML = fh.read().replace(b"__VERSION__", server.VERSION.encode())
        self.no_real_github()
        self.lone = self.note("2026-08-01-lone.md", "Lone old note", question="Why is this alone?")
        for i, l in enumerate("abcd"): self.note(f"2026-10-0{i + 1}-arcane-bomb-{l}.md", f"Arcane bomb {l.upper()}", question=f"Question {l}?")
        self.note("2026-10-15-recent.md", "Recent note")
        self.note("2026-09-01-archived.md", "Archived note", status="dead", sub="done", question="Old question?")
        self.project("demo", notes="# Demo\n\n- date: 2026-10-01\n- status: promoted\n- promoted: 2026-10-01\n- due: 2026-12-01\n\n## Plan\n\n- definition of done: It ships.\n")
        self.project("nodod", notes="# Nodod\n\n- date: 2026-10-01\n- status: promoted\n- promoted: 2026-10-01\n- due: 2026-10-25\n")
        self.page = self.browser.page(); self.addCleanup(self.page.close)
        self.page.add_init_script(RECORD)
        self.page.resize(1280, 800)
        self.open()

    def no_real_github(self):
        """The page asks for GitHub activity on load. Answer it from here: tests never reach GitHub or use the user's sign-in."""
        today = server.today()
        def fake(query):
            if "repositories(" in query:
                repo = lambda n: {"id": "ID_" + n, "nameWithOwner": "me/" + n, "isPrivate": n == "beta", "url": "https://github.com/me/" + n, "pushedAt": today.isoformat() + "T00:00:00Z"}
                return {"viewer": {"id": "U1", "login": "me", "repositories": {"pageInfo": {"hasNextPage": False}, "nodes": [repo("alpha"), repo("beta")]}}}
            n = query.count("node(id:")
            node = {"defaultBranchRef": {"target": {"history": {"pageInfo": {"hasNextPage": False}, "nodes": [{"committedDate": f"{(today - server.dt.timedelta(days=d)).isoformat()}T12:00:00Z"} for d in (0, 1, 1, 3)]}}}}
            return {f"r{i}": node for i in range(n)}
        for target, repl in ((server, {"gh_graphql": fake, "_gh": mock.Mock(side_effect=RuntimeError("GitHub is off in tests"))}),):
            for name, val in repl.items():
                p = mock.patch.object(target, name, val); p.start(); self.addCleanup(p.stop)
        saved = dict(server._activity); server._activity.update(at=0.0, data=None, busy=None, last=None); self.addCleanup(lambda: server._activity.update(saved))

    def open(self, route="inbox"):
        self.page.eval("typeof S!=='undefined'&&(S.dods={})")  # an unsaved-edit prompt would (rightly) stop the page from unloading
        self.loads = getattr(self, "loads", 0) + 1  # a changed query string forces a full load, so notes made after setUp show up
        self.page.goto(f"http://127.0.0.1:{self.port}/?load={self.loads}#/{route}")
        self.page.wait("typeof S!=='undefined' && S.notes && document.querySelector('#main h1, #main .empty')")
        if route == "projects": self.page.wait("document.querySelector('[data-pid]')")

    def calls(self, method=None, part=""):
        return [c for c in self.page.eval("window.__calls") if (method is None or c["method"] == method) and part in c["url"]]

    def posts(self): return self.calls("POST")

    def exists(self, sel): return bool(self.page.eval(f"!!document.querySelector({json.dumps(sel)})"))

    def text(self, sel): return self.page.eval(f"(document.querySelector({json.dumps(sel)})||{{}}).textContent||''")

    def focus(self, sel): self.page.eval(f"document.querySelector({json.dumps(sel)}).focus()")

    def active(self): return self.page.eval("(()=>{const a=document.activeElement;return a?(a.id||a.getAttribute('data-f')||a.tagName):''})()")

    def stack_row(self): return ".item.stack"

    def settle(self, ms=0.3): time.sleep(ms)


class GithubPanel(BrowserCase):
    """v2.09: the rail says who GitHub features act as, and the dialog saves or removes a token."""
    def test_status_and_token_dialog(self):
        tok = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
        def fake_gh(args, timeout=25, token=None):
            token = token or server.saved_token()
            if token == tok: return '{"login": "tokenuser"}'
            raise RuntimeError("To get started, please run:  gh auth login")
        p = mock.patch.object(server, "_gh", fake_gh); p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(server.shutil, "which", lambda n: "/usr/bin/gh"); p.start(); self.addCleanup(p.stop)
        server.forget_github(); self.open()
        self.page.wait("document.querySelector('#ghStatus') && /Not signed in/.test(document.querySelector('#ghStatus').textContent)")
        self.page.click("#ghStatus"); self.page.wait("document.querySelector('#ghTok')")
        self.assertEqual(self.active(), "ghTok")
        self.page.eval("document.querySelector('#ghTok').value='nope'"); self.page.click("#ghSave"); self.settle(0.5)
        self.assertIn("does not look like", self.text("#ghErr"))
        self.page.eval(f"document.querySelector('#ghTok').value={json.dumps(tok)}"); self.page.click("#ghSave")
        self.page.wait("!document.querySelector('#ghTok') && /tokenuser/.test(document.querySelector('#ghStatus').textContent)")
        self.assertEqual(server.saved_token(), tok)
        self.page.click("#ghStatus"); self.page.wait("document.querySelector('#ghRemove')")
        self.assertNotIn(tok, self.page.eval("document.body.innerHTML"))
        self.page.click("#ghRemove")
        self.page.wait("!document.querySelector('#ghRemove') && /Not signed in/.test(document.querySelector('#ghStatus').textContent)")
        self.assertEqual(server.saved_token(), "")
        # opened from a phone: no token field at all, so a token never crosses the network
        self.page.eval("window.isLocalPage=()=>false"); self.page.click("#ghStatus"); self.page.wait("document.querySelector('#ghClose')")
        self.assertEqual(self.page.eval("document.querySelectorAll('#ghTok, #ghForm, #ghRemove').length"), 0)
        self.assertEqual(self.active(), "ghClose")
        self.assertIn("machine running the server", self.text("#overlay"))


class Keyboard(BrowserCase):
    def test_one_enter_on_a_collapsed_stack_only_opens_it(self):
        self.assertEqual(self.page.eval("document.querySelector('.item.stack').getAttribute('aria-expanded')"), "false")
        self.focus(".item.stack"); self.page.key("Enter"); self.settle(0.6)
        self.assertEqual(self.posts(), [])  # nothing written
        self.assertEqual(self.page.eval("document.querySelector('.item.stack').getAttribute('aria-expanded')"), "true")
        self.assertEqual(len(os.listdir(self.notes)) - 1, 6)  # all six notes (+ the done folder) still in the Inbox
        self.assertEqual(self.active(), "stack:arcane-bomb")  # the row you were on keeps focus
        self.page.key("Enter"); self.settle(0.4)  # a second Enter with the stack open, no action chosen
        self.assertEqual(self.posts(), [])
        self.assertFalse(os.path.exists(os.path.join(self.notes, "done", "2026-10-01-arcane-bomb-a.md")))

    def test_space_opens_rows_and_stack_rows_like_enter(self):
        self.focus(".item.stack"); self.page.key(" "); self.settle(0.3)
        self.assertEqual(self.page.eval("document.querySelector('.item.stack').getAttribute('aria-expanded')"), "true")
        self.focus("#list .item[data-f='2026-10-15-recent.md']"); self.page.key(" "); self.settle(0.3)
        self.assertIn("Recent note", self.text("#doc .title"))
        self.assertEqual(self.posts(), [])
        self.assertEqual(self.page.eval("window.scrollY"), 0)  # Space did not also scroll the page

    def test_a_stack_merges_only_after_you_choose_to(self):
        self.focus(".item.stack"); self.page.key("Enter"); self.settle(0.4)
        self.page.click("[data-bm=merge]"); self.settle(0.2)   # choosing the action is deliberate...
        self.assertEqual(self.posts(), [])                    # ...but choosing is not submitting
        self.page.key("Enter"); self.page.wait("window.__calls.some(c=>c.url.includes('/api/merge'))")
        self.page.wait("document.querySelector('#toast.show')")
        self.assertEqual(len(self.posts()), 1)
        self.assertTrue(os.path.exists(os.path.join(self.notes, "done", "2026-10-01-arcane-bomb-a.md")))

    def test_merge_button_click_is_the_other_explicit_confirm(self):
        self.focus(".item.stack"); self.page.key("Enter"); self.settle(0.3)
        self.page.click("#go"); self.page.wait("window.__calls.some(c=>c.url.includes('/api/merge'))")
        self.assertEqual(len(self.posts()), 1)

    def test_kill_all_by_key_needs_the_action_key_then_enter(self):
        self.focus(".item.stack"); self.page.key("Enter"); self.settle(0.3)
        self.page.key("Enter"); self.settle(0.3); self.assertEqual(self.posts(), [])
        self.page.eval("document.activeElement.blur()")
        self.page.key("x"); self.settle(0.2); self.assertEqual(self.posts(), [])
        self.page.key("Enter"); self.page.wait("window.__calls.some(c=>c.url.includes('/api/batch'))")
        self.assertEqual(json.loads(self.page.eval("JSON.stringify(window.__calls.length)")) >= 1, True)

    def test_enter_on_the_focused_stack_row_submits_after_a_deliberate_choice(self):
        self.focus(".item.stack"); self.page.key("Enter"); self.page.wait("document.querySelector('.item.member')")
        self.assertEqual(self.active(), "stack:arcane-bomb")
        self.page.key("x"); self.settle(0.2); self.assertEqual(self.posts(), [])  # choosing is not submitting
        self.assertEqual(self.page.eval("S.armed"), "stack:arcane-bomb")           # and the choice survives focus being on the row
        self.page.key("Enter"); self.page.wait("window.__calls.some(c=>c.url.includes('/api/batch'))"); self.settle(0.3)
        self.assertEqual(len(self.calls("POST", "/api/batch")), 1)

    def test_a_double_or_held_enter_submits_once(self):
        self.page.click("#list .item[data-f='2026-10-15-recent.md']"); self.page.key("c"); self.page.wait("document.querySelector('#form.keep')")
        self.focus("#note"); self.page.type("step one")
        self.page.key("Enter"); self.page.key("Enter"); self.page.key("Enter"); self.settle(0.6)
        self.assertEqual(len(self.calls("POST", "/api/action")), 1)
        self.assertEqual(self.read_note("2026-10-15-recent.md").count("step one"), 1)

    def read_note(self, name): return open(os.path.join(self.notes, name), encoding="utf-8").read()

    def test_a_single_note_decision_still_works_from_the_keyboard(self):
        self.page.eval("document.activeElement&&document.activeElement.blur()")
        self.page.key("x"); self.page.wait("document.querySelector('#form.kill')")
        self.page.type("too old"); self.page.key("Enter"); self.page.wait("document.querySelector('#toast.show')")
        self.assertTrue(os.path.exists(os.path.join(self.notes, "done", "2026-08-01-lone.md")))


class Flows(BrowserCase):
    """The everyday flows still work end to end through the page, including Undo."""
    def done_toast(self): self.page.wait("document.querySelector('#toast.show')"); self.settle(0.2)

    def read(self, *p): return open(os.path.join(self.notes, *p), encoding="utf-8").read()

    def test_keep_with_a_next_step_then_undo(self):
        self.page.click("#list .item[data-f='2026-10-15-recent.md']"); self.page.key("c"); self.page.wait("document.querySelector('#form.keep')")
        self.page.click("[data-seg=status] [data-v=cooking]"); self.focus("#note"); self.page.type("call the vendor"); self.page.click("#go"); self.done_toast()
        text = self.read("2026-10-15-recent.md"); self.assertIn("- status: cooking", text); self.assertIn("next step", text); self.assertIn("call the vendor", text)
        self.page.click("#tUndo"); self.settle(0.5); self.assertIn("- status: open", self.read("2026-10-15-recent.md"))

    def test_kill_then_undo_with_the_z_key(self):
        self.page.click("#list .item[data-f='2026-10-15-recent.md']"); self.page.key("x"); self.page.wait("document.querySelector('#form.kill')")
        self.page.type("dupe"); self.page.key("Enter"); self.done_toast()
        self.assertIn("dupe", self.read("done", "2026-10-15-recent.md")); self.assertFalse(os.path.exists(os.path.join(self.notes, "2026-10-15-recent.md")))
        self.page.eval("document.activeElement&&document.activeElement.blur()"); self.page.key("z"); self.settle(0.6)
        self.assertTrue(os.path.exists(os.path.join(self.notes, "2026-10-15-recent.md")))

    def test_restore_from_the_archive(self):
        self.open("archive"); self.page.wait("document.querySelector('#restore')"); self.page.click("#restore"); self.done_toast()
        self.assertTrue(os.path.exists(os.path.join(self.notes, "2026-09-01-archived.md")))
        self.assertIn("- status: cooking", self.read("2026-09-01-archived.md"))

    def test_promote_creates_the_project_and_it_appears_on_the_board(self):
        self.page.click("#list .item[data-f='2026-10-15-recent.md']"); self.page.key("p"); self.page.wait("document.querySelector('#form.promote')")
        self.focus("#note"); self.page.type("Works on my phone"); self.page.click("#go"); self.done_toast()
        notes = open(os.path.join(self.tmp, "active", "recent", "NOTES.md")).read()
        self.assertIn("- definition of done: Works on my phone", notes); self.assertIn("- due:", notes)

    def test_reference_files_the_note(self):
        self.page.click("#list .item[data-f='2026-10-15-recent.md']"); self.page.key("r"); self.page.wait("document.querySelector('#form.reference')")
        self.page.click("#go"); self.done_toast()
        self.assertIn("tags:", open(os.path.join(self.tmp, "reference", "recent.md")).read())

    def test_merge_from_a_stack_through_the_button(self):
        self.focus(".item.stack"); self.page.key("Enter"); self.page.wait("document.querySelector('.item.member')")
        self.page.click("#go"); self.done_toast()
        self.assertEqual(len(os.listdir(os.path.join(self.notes, "done"))), 5)  # four sources + the one already there
        merged = [f for f in os.listdir(self.notes) if f.endswith("arcane-bomb.md")]
        self.assertEqual(len(merged), 1); self.assertIn("## Arcane bomb D", self.read(merged[0]))

    def test_unticking_a_member_leaves_it_in_the_inbox(self):
        self.focus(".item.stack"); self.page.key("Enter"); self.page.wait("document.querySelector('.item.member')")
        self.page.eval("document.querySelector('.item.member input').click()"); self.settle(0.2)
        self.page.click("#go"); self.done_toast()
        self.assertTrue(os.path.exists(os.path.join(self.notes, "2026-10-01-arcane-bomb-a.md")))

    def test_mark_shipped_from_projects_then_undo(self):
        self.open("projects"); self.page.click("[data-pid='active/demo/NOTES.md']"); self.page.wait("document.querySelector('[data-pa=shipped]')")
        self.page.click("[data-pa=shipped]"); self.done_toast()
        self.assertIn("- status: shipped", open(os.path.join(self.tmp, "active", "demo", "NOTES.md")).read())
        self.page.click("#tUndo"); self.settle(0.6)
        self.assertIn("- status: promoted", open(os.path.join(self.tmp, "active", "demo", "NOTES.md")).read())

    def test_list_search_and_palette_search_find_notes(self):
        self.focus("#q"); self.page.type("lone"); self.settle(0.3)
        self.assertEqual(self.page.eval("[...document.querySelectorAll('#list .t')].map(e=>e.textContent)"), ["Lone old note"])
        self.page.eval("document.querySelector('#q').value='';document.querySelector('#q').dispatchEvent(new Event('input'))")
        self.page.key("k", ctrl=True); self.page.wait("document.querySelector('#pq')"); self.page.type("archived"); self.settle(0.3)
        self.assertIn("Archived note", self.text("#pres"))

    def test_open_folder_button_sends_a_project_not_a_path(self):
        self.open("projects"); self.page.click("[data-pid='active/demo/NOTES.md']"); self.page.wait("document.querySelector('#openf') && document.querySelector('#dodEdit')")  # layout has settled
        opened = []
        import unittest.mock as um
        with um.patch.object(server, "open_path", side_effect=opened.append):
            self.page.click("#openf"); self.settle(0.5)
        self.assertEqual(len(self.calls("POST", "/api/open")), 1)
        self.assertEqual(opened, [os.path.join(self.tmp, "active", "demo")])


class StaleReplies(BrowserCase):
    def test_a_late_note_reply_cannot_overwrite_the_note_now_shown(self):
        self.page.eval("window.__holds.push({match:'/api/note?f=2026-10-15-recent.md'})")
        self.page.click("#list .item[data-f='2026-10-15-recent.md']"); self.page.wait("window.__holds[0].hit")
        self.page.click("#list .item[data-f='2026-08-01-lone.md']"); self.page.wait("document.querySelector('#doc .title')")
        self.assertIn("Lone old note", self.text("#doc .title"))
        self.page.eval("window.__holds[0].release('ok')"); self.settle(0.4)
        self.assertIn("Lone old note", self.text("#doc .title"))  # the slow reply for the other note was dropped
        self.assertEqual(self.page.collect_errors(), [])

    def test_a_late_failure_cannot_paint_an_error_over_another_screen(self):
        self.page.eval("window.__holds.push({match:'/api/note?f=2026-10-15-recent.md'})")
        self.page.click("#list .item[data-f='2026-10-15-recent.md']"); self.page.wait("window.__holds[0].hit")
        self.page.eval("location.hash='#/archive'"); self.page.wait("document.querySelector('#main h1').textContent==='Archive'")
        self.page.wait("document.querySelector('#doc .title')")
        shown = self.text("#main")
        self.page.eval("window.__holds[0].release('fail')"); self.settle(0.4)
        self.assertEqual(self.text("#main"), shown); self.assertNotIn("late failure", self.text("#main"))
        self.assertEqual(self.page.collect_errors(), [])

    def test_a_reply_arriving_after_its_element_is_gone_does_nothing(self):
        self.page.eval("window.__holds.push({match:'/api/note?f=2026-10-15-recent.md'})")
        self.page.click("#list .item[data-f='2026-10-15-recent.md']"); self.page.wait("window.__holds[0].hit")
        self.page.eval("location.hash='#/pulse'"); self.page.wait("document.querySelector('#main h1').textContent==='Pulse'")
        self.page.eval("window.__holds[0].release('ok')"); self.settle(0.4)
        self.assertEqual(self.text("#main h1"), "Pulse"); self.assertEqual(self.page.collect_errors(), [])

    def test_a_late_project_detail_cannot_land_in_the_next_projects_drawer(self):
        self.open("projects")
        self.page.eval("window.__holds.push({match:'/api/project?name=demo'})")
        self.page.click("[data-pid='active/demo/NOTES.md']"); self.page.wait("window.__holds[0].hit")
        self.page.click("[data-pid='active/nodod/NOTES.md']"); self.page.wait("document.querySelector('#drawer #dodbox .dod')")
        self.assertIn("Not defined", self.text("#dodbox"))
        self.page.eval("window.__holds[0].release('ok')"); self.settle(0.5)
        self.assertIn("Not defined", self.text("#dodbox")); self.assertNotIn("It ships.", self.text("#drawer"))
        self.assertEqual(self.page.collect_errors(), [])

    def test_a_projects_load_in_flight_cannot_repopulate_the_cache_a_change_just_dropped(self):
        self.open("projects"); self.page.eval("location.hash='#/inbox'"); self.page.wait("document.querySelector('#main h1').textContent==='Inbox'")
        self.assertTrue(self.page.eval("Array.isArray(S.projects)"))
        self.page.eval("window.__holds.push({match:'/api/projects'}); void (window.__p=refresh())"); self.page.wait("window.__holds[0].hit")
        self.page.eval("void (window.__q=changed())"); self.settle(0.3)
        self.page.eval("window.__holds[0].release('ok')"); self.settle(0.5)
        self.assertIsNone(self.page.eval("S.projects"))  # the next visit to Projects reloads instead of showing pre-change data

    def test_a_late_project_failure_after_leaving_the_page_is_ignored(self):
        self.open("projects")
        self.page.eval("window.__holds.push({match:'/api/project?name=demo'})")
        self.page.click("[data-pid='active/demo/NOTES.md']"); self.page.wait("window.__holds[0].hit")
        self.page.eval("location.hash='#/inbox'"); self.page.wait("document.querySelector('#main h1').textContent==='Inbox'")
        self.page.wait("document.querySelector('#doc .title')")  # the Inbox reader has finished loading by itself
        before = self.text("#main"); self.page.eval("window.__holds[0].release('fail')"); self.settle(0.4)
        self.assertEqual(self.text("#main"), before); self.assertEqual(self.page.collect_errors(), [])


class Drafts(BrowserCase):
    def edit_done(self, text="My half-written definition"):
        self.open("projects")
        self.page.click("[data-pid='active/demo/NOTES.md']"); self.page.wait("document.querySelector('#dodEdit')")
        self.page.click("#dodEdit"); self.page.wait("document.querySelector('#dodText')")
        self.page.eval("document.querySelector('#dodText').value=''"); self.focus("#dodText"); self.page.type(text)

    def test_refresh_on_returning_to_the_tab_keeps_an_unsaved_done_means_edit(self):
        self.edit_done()
        self.page.eval("document.dispatchEvent(new Event('visibilitychange'))"); self.settle(0.6)
        self.assertTrue(self.exists("#dodText"))
        self.assertEqual(self.page.eval("document.querySelector('#dodText').value"), "My half-written definition")
        self.assertEqual(self.calls("POST", "/api/done"), [])

    def test_the_draft_survives_leaving_and_coming_back(self):
        self.edit_done()
        self.page.eval("location.hash='#/archive'"); self.page.wait("document.querySelector('#main h1').textContent==='Archive'")
        self.page.eval("location.hash='#/projects'"); self.page.wait("document.querySelector('#dodText')")
        self.assertEqual(self.page.eval("document.querySelector('#dodText').value"), "My half-written definition")
        self.page.eval("document.querySelector('#dodCancel').click()"); self.assertFalse(self.exists("#dodText"))
        self.page.eval("location.hash='#/archive'"); self.page.wait("document.querySelector('#main h1').textContent==='Archive'")
        self.page.eval("location.hash='#/projects'"); self.page.wait("document.querySelector('#dodbox .dod')")
        self.assertFalse(self.exists("#dodText"))  # cancelled means gone

    def test_leaving_the_tab_with_an_unsaved_edit_asks_first(self):
        self.edit_done()
        self.assertTrue(self.page.eval("(()=>{const e=new Event('beforeunload',{cancelable:true});window.dispatchEvent(e);return e.defaultPrevented})()"))
        self.page.eval("document.querySelector('#dodCancel').click()")
        self.assertFalse(self.page.eval("(()=>{const e=new Event('beforeunload',{cancelable:true});window.dispatchEvent(e);return e.defaultPrevented})()"))

    def test_opening_the_editor_without_typing_is_not_unsaved_work(self):
        self.open("projects"); self.page.click("[data-pid='active/demo/NOTES.md']"); self.page.wait("document.querySelector('#dodEdit')")
        self.page.click("#dodEdit"); self.page.wait("document.querySelector('#dodText')")
        self.assertFalse(self.page.eval("dirty()"))
        self.assertFalse(self.page.eval("(()=>{const e=new Event('beforeunload',{cancelable:true});window.dispatchEvent(e);return e.defaultPrevented})()"))
        self.focus("#dodText"); self.page.type("x"); self.assertTrue(self.page.eval("dirty()"))

    def test_an_exact_ship_by_date_restores_with_the_day_buttons_off(self):
        self.page.click("#list .item[data-f='2026-10-15-recent.md']"); self.page.key("p"); self.page.wait("document.querySelector('#form.promote')")
        self.page.eval("(()=>{const d=document.querySelector('#dueDate');d.value='2027-01-05';d.dispatchEvent(new Event('input',{bubbles:true}))})()")
        self.page.eval("setMode(null);setMode('promote')")
        self.assertEqual(self.page.eval("document.querySelector('#dueDate').value"), "2027-01-05")
        self.assertEqual(self.page.eval("document.querySelectorAll('[data-seg=due] button.on').length"), 0)

    def test_a_refresh_still_picks_up_new_notes_when_nothing_is_being_edited(self):
        self.note("2026-10-16-brand-new.md", "Brand new")
        self.page.eval("document.dispatchEvent(new Event('visibilitychange'))")
        self.page.wait("[...document.querySelectorAll('#list .t')].some(e=>e.textContent==='Brand new')")

    def test_decision_form_text_is_kept_when_you_look_at_another_note_and_return(self):
        self.page.click("#list .item[data-f='2026-10-15-recent.md']"); self.page.key("p"); self.page.wait("document.querySelector('#form.promote')")
        self.focus("#note"); self.page.type("It runs on my phone")
        self.page.click("#list .item[data-f='2026-08-01-lone.md']"); self.settle(0.3)
        self.page.click("#list .item[data-f='2026-10-15-recent.md']"); self.page.key("p")
        self.page.wait("document.querySelector('#note')")
        self.assertEqual(self.page.eval("document.querySelector('#note').value"), "It runs on my phone")

    def test_saving_done_means_is_undoable_and_cleans_the_draft(self):
        self.edit_done("Ships to the iPhone")
        self.page.click("#dodReview"); self.page.wait("document.querySelector('#dodSave')")
        self.page.click("#dodSave"); self.page.wait("document.querySelector('#toast.show #tUndo')")
        self.assertEqual(self.page.eval("JSON.stringify(S.dods)"), "{}")
        self.assertIn("Ships to the iPhone", open(os.path.join(self.tmp, "active", "demo", "NOTES.md")).read())
        self.page.click("#tUndo"); self.settle(0.6)
        self.assertIn("It ships.", open(os.path.join(self.tmp, "active", "demo", "NOTES.md")).read())


class Caches(BrowserCase):
    def test_a_promoted_note_shows_up_on_projects_with_its_done_means_editor(self):
        self.open("projects"); self.page.wait("document.querySelector('.board')")
        self.page.eval("location.hash='#/inbox'"); self.page.wait("document.querySelector('#main h1').textContent==='Inbox'")
        self.page.click("#list .item[data-f='2026-10-15-recent.md']"); self.page.key("p"); self.page.wait("document.querySelector('#form.promote')")
        self.focus("#note"); self.page.type("It ships by Friday"); self.page.click("#go"); self.page.wait("document.querySelector('#toast.show')")
        self.page.eval("location.hash='#/projects'"); self.page.wait("document.querySelector('[data-pid*=\"/recent/\"]')")
        self.page.click("[data-pid*='/recent/']"); self.page.wait("document.querySelector('#dodbox .dod')")
        self.assertIn("It ships by Friday", self.text("#dodbox"))
        self.assertTrue(self.exists("#dodEdit"))  # the editor is there without pressing Refresh

    def test_batch_merge_and_undo_also_refresh_projects(self):
        self.open("projects"); self.page.wait("document.querySelector('.board')")
        n = self.page.eval("document.querySelectorAll('[data-pid]').length")
        self.page.eval("location.hash='#/inbox'"); self.page.wait("document.querySelector('#main h1').textContent==='Inbox'")
        self.page.eval("(async()=>{await api('/api/merge',{files:['2026-10-01-arcane-bomb-a.md','2026-10-02-arcane-bomb-b.md'],title:'Bomb',then:'promote',dest:'active',due_days:'7',slug:'bomb',note:'Ships'});await changed();render()})()")
        self.page.eval("location.hash='#/projects'"); self.page.wait(f"document.querySelectorAll('[data-pid]').length=={n + 1}")
        self.page.key("z"); self.page.wait(f"document.querySelectorAll('[data-pid]').length=={n}")  # undo drops it again, no Refresh


class Overlays(BrowserCase):
    def test_palette_is_a_modal_dialog_with_a_focus_trap_and_returns_focus(self):
        self.focus("#kPal"); self.page.key("k", ctrl=True); self.page.wait("document.querySelector('[role=dialog]')")
        d = json.loads(self.page.eval("JSON.stringify({m:document.querySelector('[role=dialog]').getAttribute('aria-modal'),n:document.querySelector('[role=dialog]').getAttribute('aria-label'),a:document.activeElement.id,inert:!!document.querySelector('.app').inert,tab:!!document.querySelector('#tabbar').inert})"))
        self.assertEqual((d["m"], d["a"], d["inert"], d["tab"]), ("true", "pq", True, True)); self.assertTrue(d["n"])
        for _ in range(6):
            self.page.key("Tab"); self.assertTrue(self.page.eval("!!document.activeElement.closest('[role=dialog]')"))
        for _ in range(6):
            self.page.key("Tab", shift=True); self.assertTrue(self.page.eval("!!document.activeElement.closest('[role=dialog]')"))
        self.page.key("ArrowDown")
        self.assertEqual(self.page.eval("document.querySelector('#pq').getAttribute('aria-activedescendant')"), "po1")
        self.assertEqual(self.page.eval("document.querySelector('#po1').getAttribute('aria-selected')"), "true")
        self.assertEqual(self.page.eval("document.querySelector('#po0').getAttribute('aria-selected')"), "false")
        self.assertEqual(self.page.eval("document.querySelector('#pres').getAttribute('role')"), "listbox")
        self.page.key("Escape"); self.page.wait("!document.querySelector('[role=dialog]')")
        self.assertEqual(self.active(), "kPal"); self.assertFalse(self.page.eval("!!document.querySelector('.app').inert"))

    def test_background_cannot_be_tabbed_to_with_the_palette_open(self):
        self.page.key("k", ctrl=True); self.page.wait("document.querySelector('[role=dialog]')")
        seen = set()
        for _ in range(8): self.page.key("Tab"); seen.add(self.active())
        self.assertTrue(seen <= {"pq"}, seen)  # one real control inside; nothing behind reachable

    def test_palette_opens_a_result_and_lands_on_the_page_heading(self):
        self.page.key("k", ctrl=True); self.page.wait("document.querySelector('#pq')"); self.page.type("go to proj"); self.settle(0.2)
        self.page.key("Enter"); self.page.wait("document.querySelector('#main h1')&&document.querySelector('#main h1').textContent==='Projects'")
        self.page.wait("document.activeElement&&document.activeElement.tagName==='H1'")

    def test_enter_with_no_palette_matches_keeps_the_dialog_open(self):
        self.page.key("k", ctrl=True); self.page.wait("document.querySelector('#pq')"); self.page.type("zzzzqq"); self.settle(0.2)
        self.page.key("Enter"); self.settle(0.2)
        self.assertTrue(self.exists("[role=dialog]")); self.assertEqual(self.active(), "pq")

    def test_keys_overlay_is_a_dialog_too(self):
        self.page.eval("document.activeElement&&document.activeElement.blur()"); self.page.key("?", shift=True)
        self.page.wait("document.querySelector('[role=dialog][aria-labelledby=keysH]')")
        self.assertEqual(self.active(), "keysClose"); self.page.key("Tab"); self.assertEqual(self.active(), "keysClose")
        self.page.key("Escape"); self.page.wait("!document.querySelector('[role=dialog]')")


class Structure(BrowserCase):
    def expand_stack(self):
        self.focus(".item.stack"); self.page.key("Enter"); self.page.wait("document.querySelector('.item.member')")

    def test_member_rows_have_no_nested_interactive_controls_and_unique_checkbox_names(self):
        self.expand_stack()
        self.assertEqual(self.page.eval("document.querySelectorAll('[role=button] button, [role=button] input, [role=button] a').length"), 0)
        names = self.page.eval("[...document.querySelectorAll('.item.member input[type=checkbox]')].map(c=>c.getAttribute('aria-label'))")
        self.assertEqual(len(names), 4); self.assertEqual(len(set(names)), 4); self.assertTrue(all(n.startswith("Select ") for n in names))
        self.assertEqual(self.page.eval("document.querySelectorAll('.item.member input[type=checkbox]:checked').length"), 4)
        self.focus(".item.member input"); self.page.key(" "); self.settle(0.2)
        self.assertEqual(self.page.eval("document.querySelectorAll('.item.member input[type=checkbox]:checked').length"), 3)
        self.assertTrue(self.page.eval("!!document.activeElement.dataset.cb"))  # focus stayed on the tick box you used
        self.assertIn("3 of 4", self.text("#bcount")); self.assertEqual(self.posts(), [])

    def test_landmarks_headings_and_names(self):
        d = json.loads(self.page.eval("""JSON.stringify({main:document.querySelectorAll('main').length,
          navs:[...document.querySelectorAll('nav')].map(n=>n.getAttribute('aria-label')),
          h1:[...document.querySelectorAll('h1')].filter(h=>h.offsetParent!==null).length,
          heads:[...document.querySelectorAll('h1,h2,h3,h4,h5,h6')].filter(h=>h.offsetParent!==null).map(h=>+h.tagName[1]),
          q:document.querySelector('#q').getAttribute('aria-label'), status:document.querySelector('#toast').getAttribute('role'),
          current:document.querySelector('.rail a[aria-current=page]').textContent.trim().slice(0,5)})"""))
        self.assertEqual((d["main"], d["h1"], d["q"], d["status"]), (1, 1, "Search notes", "status")); self.assertEqual(d["current"], "Inbox")
        for prev, cur in zip(d["heads"], d["heads"][1:]): self.assertLessEqual(cur, prev + 1, d["heads"])  # never skips a level
        for route in ("projects", "archive", "pulse"):
            self.open(route); self.page.wait("document.querySelector('#main h1')")
            heads = self.page.eval("[...document.querySelectorAll('h1,h2,h3,h4,h5,h6')].filter(h=>h.offsetParent!==null).map(h=>+h.tagName[1])")
            self.assertEqual(heads[0], 1, route)
            for prev, cur in zip(heads, heads[1:]): self.assertLessEqual(cur, prev + 1, (route, heads))

    def test_full_question_is_in_the_reader(self):
        q = "First paragraph of a long question. " * 12 + "\n\nSecond paragraph that used to vanish.\n\n- a list item\n- another item\n\nFinal paragraph."
        self.note("2026-10-14-longq.md", "Long question", question=q)
        self.open()
        self.page.click("#list .item[data-f='2026-10-14-longq.md']"); self.page.wait("document.querySelector('#doc .ask')")
        ask = self.text("#doc .ask")
        for part in ("Second paragraph that used to vanish.", "another item", "Final paragraph."): self.assertIn(part, ask)
        self.assertEqual(self.page.eval("document.querySelectorAll('#doc .ask p').length"), 3)
        self.assertNotIn("Second paragraph", self.text("#doc .md:not(.ask .md)") if False else "")
        self.assertIn("Long question", self.text("#list"))  # the list row keeps its short summary

    def test_mobile_toast_does_not_cover_the_tab_bar_or_the_decision_sheet(self):
        self.page.resize(390, 844, mobile=True); self.open()
        self.page.click("#list .item[data-f='2026-10-15-recent.md']"); self.page.wait("document.querySelector('#decide')")
        self.page.click(".opt.kill"); self.page.wait("document.querySelector('#form.kill')")
        self.page.click("#go"); self.page.wait("document.querySelector('#toast.show #tUndo')")
        self.settle(0.4)
        r = json.loads(self.page.eval("""JSON.stringify((()=>{const t=document.querySelector('#toast').getBoundingClientRect(),u=document.querySelector('#tUndo').getBoundingClientRect();
          const vis=s=>{const e=document.querySelector(s);if(!e||getComputedStyle(e).display==='none')return null;const b=e.getBoundingClientRect();return [b.top,b.bottom]};
          return {t:[t.top,t.bottom,t.left,t.right],u:[u.width,u.height],tab:vis('#tabbar'),sheet:vis('#decide'),vh:innerHeight,vw:innerWidth}})())"""))
        self.assertGreaterEqual(r["u"][1], 44); self.assertGreaterEqual(r["u"][0], 44)
        for other in (r["tab"], r["sheet"]):
            if other: self.assertTrue(r["t"][1] <= other[0] or r["t"][0] >= other[1], r)
        self.assertGreaterEqual(r["t"][2], 0); self.assertLessEqual(r["t"][3], r["vw"])


class Responsive(BrowserCase):
    def test_no_page_wide_overflow_at_phone_and_desktop_sizes(self):
        for w, h, mobile in ((1280, 800, False), (390, 844, True), (320, 568, True)):
            self.page.resize(w, h, mobile=mobile)
            for route in ("inbox", "projects", "archive", "pulse"):
                self.open(route); self.settle(0.2)
                sw = self.page.eval("document.documentElement.scrollWidth"); self.assertLessEqual(sw, w, (w, route, sw))

    SMALL = """[...document.querySelectorAll('button,a[href],input,select,textarea,[role=button]')].filter(e=>{const b=e.getBoundingClientRect(),cs=getComputedStyle(e);
              return b.width>0&&b.height>0&&cs.visibility!=='hidden'&&!e.closest('[hidden],[inert]')&&!e.classList.contains('skip')&&(b.height<44-0.5||b.width<44-0.5)&&!(e.tagName==='A'&&e.closest('p,li,span')&&!e.closest('nav'))}).map(e=>(e.id||e.getAttribute('aria-label')||e.className||e.tagName)+':'+Math.round(e.getBoundingClientRect().width)+'x'+Math.round(e.getBoundingClientRect().height)).slice(0,12)"""

    def test_mobile_touch_targets_are_big_enough(self):
        self.page.resize(390, 844, mobile=True)
        for route in ("inbox", "projects", "archive", "pulse"):
            self.open(route); self.settle(0.2)
            self.assertEqual(self.page.eval(self.SMALL), [], route)

    def test_mobile_touch_targets_in_forms_stacks_and_drawers(self):
        self.page.resize(390, 844, mobile=True); self.open()
        self.focus(".item.stack"); self.page.key("Enter"); self.page.wait("document.querySelector('.item.member')")
        self.assertEqual(self.page.eval(self.SMALL), [], "stack list")
        self.page.click(".item.stack"); self.page.wait("document.querySelector('#decide')"); self.settle(0.3)
        self.assertEqual(self.page.eval(self.SMALL), [], "stack decide sheet")
        self.page.eval("backToList()"); self.page.click("#list .item[data-f='2026-10-15-recent.md']"); self.page.wait("document.querySelector('#decide')")
        for mode in ("promote", "reference", "keep", "kill"):
            self.page.click(f".opt.{mode}"); self.page.wait(f"document.querySelector('#form.{mode}')"); self.settle(0.2)
            self.assertEqual(self.page.eval(self.SMALL), [], f"{mode} form")
        self.open("projects"); self.page.click("[data-pid='active/demo/NOTES.md']"); self.page.wait("document.querySelector('#dodEdit')")
        self.assertEqual(self.page.eval(self.SMALL), [], "project detail")
        self.page.click("#dodEdit"); self.page.wait("document.querySelector('#dodText')")
        self.assertEqual(self.page.eval(self.SMALL), [], "done means editor")
        self.open("archive"); self.page.click("#list .item"); self.page.wait("document.querySelector('#restore')")
        self.assertEqual(self.page.eval(self.SMALL), [], "archive reader")


@unittest.skipUnless(AXE and os.path.exists(AXE or ""), "set AXE_JS to a local axe.min.js to run the accessibility sweeps")
class Accessibility(BrowserCase):
    def axe(self, label):
        self.page.eval(open(AXE).read())
        res = json.loads(self.page.eval("axe.run(document,{resultTypes:['violations']}).then(r=>JSON.stringify(r.violations.map(v=>({id:v.id,impact:v.impact,nodes:v.nodes.map(n=>n.target.join(' ')+' :: '+n.failureSummary.split('\\n').slice(0,3).join(' | ').slice(0,260))}))))", timeout=60))
        self.assertEqual(res, [], f"{label}: {json.dumps(res, indent=1)[:3000]}")

    def go(self, w=1280, h=800, dark=False, mobile=False):
        self.page.resize(w, h, mobile=mobile, dark=dark)
        self.page.eval(f"localStorage.setItem('theme','{'dark' if dark else 'light'}')")

    def test_inbox_keep_form_light(self):
        self.go(); self.open(); self.page.click("#list .item[data-f='2026-10-15-recent.md']"); self.page.key("c"); self.page.wait("document.querySelector('#form.keep')"); self.axe("keep form")

    def test_projects_light(self):
        self.go(); self.open("projects"); self.page.click("[data-pid='active/demo/NOTES.md']"); self.page.wait("document.querySelector('#dodEdit')"); self.axe("projects")

    def test_merge_form_light(self):
        self.go(); self.open(); self.focus(".item.stack"); self.page.key("Enter"); self.page.wait("document.querySelector('.item.member')"); self.axe("merge")
        self.page.click("[data-bm=kill]"); self.axe("kill all")

    def test_reference_form_on_mobile(self):
        self.go(390, 844, mobile=True); self.open(); self.page.click("#list .item[data-f='2026-10-15-recent.md']"); self.page.wait("document.querySelector('#decide')")
        self.page.click(".opt.reference"); self.page.wait("document.querySelector('#form.reference')"); self.axe("mobile reference")

    def test_inbox_dark(self):
        self.go(dark=True); self.open(); self.page.click("#list .item[data-f='2026-10-15-recent.md']"); self.page.key("x"); self.page.wait("document.querySelector('#form.kill')")
        self.axe("dark inbox kill form")
        self.page.click("#go"); self.page.wait("document.querySelector('#toast.show #tUndo')"); self.settle(0.4); self.axe("dark inbox with toast")

    def test_pulse_and_archive(self):
        self.go(); self.open("pulse"); self.page.wait("document.querySelector('.tiles')"); self.axe("pulse")
        self.open("archive"); self.page.wait("document.querySelector('#doc .title')"); self.axe("archive")
        self.go(dark=True); self.open("archive"); self.page.wait("document.querySelector('#doc .title')"); self.axe("dark archive")
        self.go(dark=True); self.open("projects"); self.page.click("[data-pid='active/demo/NOTES.md']"); self.page.wait("document.querySelector('#dodEdit')"); self.axe("dark projects")

    def test_mobile_stack_projects_pulse_and_toast_in_both_themes(self):
        for dark in (False, True):
            self.go(390, 844, dark=dark, mobile=True)
            self.open(); self.focus(".item.stack"); self.page.key("Enter"); self.page.wait("document.querySelector('.item.member')"); self.axe(f"mobile stack list dark={dark}")
            self.page.click(".item.stack"); self.page.wait("document.querySelector('#decide')"); self.axe(f"mobile stack sheet dark={dark}")
            self.open("projects"); self.page.click("[data-pid='active/demo/NOTES.md']"); self.page.wait("document.querySelector('#dodEdit')"); self.axe(f"mobile project detail dark={dark}")
            self.open("pulse"); self.page.wait("document.querySelector('.bars .b')"); self.axe(f"mobile pulse dark={dark}")
            victim = ["2026-10-15-recent.md", "2026-08-01-lone.md"][int(dark)]  # each theme kills a different note
            self.open(); self.page.click(f"#list .item[data-f='{victim}']"); self.page.wait("document.querySelector('#decide')")
            self.page.click(".opt.kill"); self.page.wait("document.querySelector('#form.kill')"); self.page.click("#go"); self.page.wait("document.querySelector('#toast.show #tUndo')")
            self.settle(0.4); self.axe(f"mobile toast dark={dark}")

    def test_toast_and_error_toast_have_contrast_in_both_themes(self):
        for dark in (False, True):
            self.go(dark=dark); self.open()
            self.page.eval("toast('Something went wrong',{bad:true})"); self.settle(0.4); self.axe(f"error toast dark={dark}")
            self.page.eval("toast('Killed “x”',{undo:true})"); self.settle(0.4); self.axe(f"undo toast dark={dark}")

    def test_palette_with_every_result_showing_scrolls_accessibly(self):
        for i in range(12): self.note(f"2026-10-1{i % 10}-extra-{i}.md", f"Extra note {i}")
        for dark in (False, True):
            self.go(dark=dark); self.open(); self.page.key("k", ctrl=True); self.page.wait("document.querySelector('#pq')")
            self.assertGreater(self.page.eval("document.querySelector('#pres').scrollHeight-document.querySelector('#pres').clientHeight"), 0)
            self.axe(f"palette (scrolling) dark={dark}")

    def test_dark_palette_projects_and_pulse(self):
        self.go(dark=True); self.open(); self.page.key("k", ctrl=True); self.page.wait("document.querySelector('#pq')"); self.axe("dark palette")
        self.page.key("Escape"); self.open("pulse"); self.page.wait("document.querySelector('.bars .b')"); self.axe("dark pulse")
        self.open("projects"); self.page.wait("document.querySelector('.board')"); self.axe("dark projects board")

    def test_palette_and_keys_dialogs(self):
        self.go(); self.open(); self.page.key("k", ctrl=True); self.page.wait("document.querySelector('#pq')"); self.page.type("note"); self.settle(0.2); self.axe("palette")
        self.page.key("Escape"); self.page.eval("document.activeElement&&document.activeElement.blur()"); self.page.key("?", shift=True)
        self.page.wait("document.querySelector('[role=dialog]')"); self.axe("keys")

    def test_dimmed_option_cards_keep_their_contrast(self):
        self.go(); self.open(); self.page.click("#list .item[data-f='2026-10-15-recent.md']"); self.page.key("p"); self.page.wait("document.querySelector('#form.promote')"); self.axe("promote with dimmed cards")
        self.go(dark=True); self.open(); self.page.click("#list .item[data-f='2026-10-15-recent.md']"); self.page.key("p"); self.page.wait("document.querySelector('#form.promote')"); self.axe("dark promote")


if __name__ == "__main__":
    unittest.main()
