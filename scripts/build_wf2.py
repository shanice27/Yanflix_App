"""
build_wf2.py — Rebuild Yanflix WF2: Gemini Script Director via n8n REST API

Replaces the stub WF2 (which was calling Gemini directly from n8n — wrong)
with the correct pipeline:

  Webhook: script-director
    → POST /api/diarize  (Gemini speaker ID, runs on Next.js host)
    → poll diarize status
    → wait for cast_locked = true  (human reviews cast in UI, clicks Save Cast)
    → POST /api/translate
    → poll translate status
    → Set: WF2 complete

n8n NEVER calls Gemini directly. All LLM calls go through Next.js API routes.
"""

import json, sys, os
import urllib.request, urllib.error

N8N_BASE  = "http://localhost:5678/api/v1"
WF2_ID    = "vwHMSQh3MCE0B9GC"
BASE_URL  = "http://host.docker.internal:3000"
WEBHOOK   = "Webhook: script-director"

API_KEY = os.environ.get("N8N_API_KEY", (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiIxNGFlMzM4NC1lNjc1LTQ3ZjctODI0My1kZjk4OTE0ODdjNDEiLCJpc3MiOiJuOG4iLCJhdWQiOiJwdWJsaWMtYXBpIiwianRpIjoiZGQ1OTcxMjItOTBiZC00OWNjLWFiODQtNTg3YmY5ZDg3ZDA1IiwiaWF0IjoxNzgxMjk0NTU4LCJleHAiOjE3ODM4Mjg4MDB9"
    ".jy5C5ZEE6qvs7_r6xgmh0LfPHmumRAoh-q2pQ-QVzrc"
))
HEADERS = {"X-N8N-API-KEY": API_KEY, "Content-Type": "application/json"}

EP     = f'$node["{WEBHOOK}"].json.body.ep_folder'
LANG   = f'$node["{WEBHOOK}"].json.body.source_lang'
STATUS = f"{BASE_URL}/api/status?ep_folder={{{{ {EP} }}}}"


def ep_expr():
    return "={{ " + EP + " }}"

def status_url():
    return f"{BASE_URL}/api/status"

def cast_url():
    return f"{BASE_URL}/api/cast-status"


def http(method, url_path, body=None):
    url = N8N_BASE + url_path
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=HEADERS, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode()}")
        sys.exit(1)


def node(name, ntype, ver, params, pos):
    return {
        "name": name,
        "type": ntype,
        "typeVersion": ver,
        "parameters": params,
        "position": pos,
    }


def webhook_node():
    return node("Webhook: script-director", "n8n-nodes-base.webhook", 2, {
        "httpMethod": "POST",
        "path": "script-director",
        "responseMode": "lastNode",
        "options": {}
    }, [0, 300])


def post_node(name, url, params_list, pos):
    return node(name, "n8n-nodes-base.httpRequest", 4.2, {
        "method": "POST",
        "url": url,
        "authentication": "none",
        "sendBody": True,
        "contentType": "json",
        "bodyParameters": {"parameters": params_list},
        "options": {}
    }, pos)


def get_node(name, url, query_list, pos, continue_on_fail=False):
    n = node(name, "n8n-nodes-base.httpRequest", 4.2, {
        "method": "GET",
        "url": url,
        "authentication": "none",
        "queryParameters": {"parameters": query_list},
        "options": {}
    }, pos)
    if continue_on_fail:
        n["continueOnFail"] = True
    return n


def wait_node(name, seconds, pos):
    return node(name, "n8n-nodes-base.wait", 1, {
        "resume": "timeInterval",
        "amount": seconds,
        "unit": "seconds"
    }, pos)


def if_str_node(name, value1_expr, value2, pos):
    return node(name, "n8n-nodes-base.if", 1, {
        "conditions": {
            "string": [{"value1": value1_expr, "value2": value2}]
        }
    }, pos)


def code_node(name, code, pos):
    return node(name, "n8n-nodes-base.code", 2, {"jsCode": code}, pos)


def stop_node(name, msg, pos):
    return node(name, "n8n-nodes-base.stopAndError", 1, {"errorMessage": msg}, pos)


def set_node(name, assignments, pos):
    return node(name, "n8n-nodes-base.set", 3.4, {
        "assignments": {"assignments": [
            {"name": k, "value": v, "type": "string"} for k, v in assignments.items()
        ]},
        "options": {}
    }, pos)


def ep_query():
    return [{"name": "ep_folder", "value": ep_expr()}]


def build_workflow():
    # ── Diarize phase ──────────────────────────────────────────────────────
    n1  = webhook_node()

    n2  = post_node("POST: diarize", f"{BASE_URL}/api/diarize", [
        {"name": "ep_folder",   "value": ep_expr()},
        {"name": "source_lang", "value": "={{ " + LANG + " }}"},
    ], [240, 300])

    n3  = code_node("Code: reset diarize counter",
        "return [{ json: { ...$input.item.json, pollCount: 0 } }];", [480, 300])

    n4  = wait_node("Wait: diarize buffer", 15, [700, 300])

    n5  = get_node("GET: diarize poll", status_url(), ep_query(), [920, 300])

    n6  = if_str_node("IF: diarize done?",
        "={{ $json.status_diarize }}", "done", [1140, 300])

    n7  = if_str_node("IF: diarize error?",
        "={{ $json.status_diarize }}", "error", [1140, 520])

    n8  = code_node("Code: diarize poll counter", """
const count = ($input.item.json.pollCount || 0) + 1;
if (count > 80) throw new Error('Diarize poll limit exceeded (80 polls × 15s = 20 min)');
return [{ json: { ...$input.item.json, pollCount: count } }];
""".strip(), [920, 520])

    n9  = stop_node("Stop: diarize error",
        "Diarize failed — check /api/status for error detail", [1360, 520])

    # ── Cast-lock gate ─────────────────────────────────────────────────────
    n10 = wait_node("Wait: cast review buffer", 30, [1360, 300])

    n11 = get_node("GET: cast status", cast_url(), ep_query(), [1580, 300])

    n12 = if_str_node("IF: cast locked?",
        "={{ String($json.cast_locked) }}", "true", [1800, 300])

    # ── Translate phase ────────────────────────────────────────────────────
    n13 = post_node("POST: translate", f"{BASE_URL}/api/translate", [
        {"name": "ep_folder", "value": ep_expr()},
    ], [2020, 300])

    n14 = code_node("Code: reset translate counter",
        "return [{ json: { ...$input.item.json, pollCount: 0 } }];", [2240, 300])

    n15 = wait_node("Wait: translate buffer", 10, [2460, 300])

    n16 = get_node("GET: translate poll", status_url(), ep_query(), [2680, 300])

    n17 = if_str_node("IF: translate done?",
        "={{ $json.status_translate }}", "done", [2900, 300])

    n18 = if_str_node("IF: translate error?",
        "={{ $json.status_translate }}", "error", [2900, 520])

    n19 = code_node("Code: translate poll counter", """
const count = ($input.item.json.pollCount || 0) + 1;
if (count > 60) throw new Error('Translate poll limit exceeded (60 polls × 10s = 10 min)');
return [{ json: { ...$input.item.json, pollCount: count } }];
""".strip(), [2680, 520])

    n20 = stop_node("Stop: translate error",
        "Translate failed — check /api/status for error detail", [3120, 520])

    n21 = set_node("Set: WF2 complete", {
        "status":     "complete",
        "ep_folder":  ep_expr(),
        "message":    "Diarize + cast lock + translate done. WF1 will resume.",
    }, [3120, 300])

    nodes = [n1,n2,n3,n4,n5,n6,n7,n8,n9,n10,n11,n12,n13,n14,n15,n16,n17,n18,n19,n20,n21]

    connections = {
        "Webhook: script-director":    {"main": [[{"node": "POST: diarize",              "type": "main", "index": 0}]]},
        "POST: diarize":               {"main": [[{"node": "Code: reset diarize counter","type": "main", "index": 0}]]},
        "Code: reset diarize counter": {"main": [[{"node": "Wait: diarize buffer",        "type": "main", "index": 0}]]},
        "Wait: diarize buffer":        {"main": [[{"node": "GET: diarize poll",           "type": "main", "index": 0}]]},
        "GET: diarize poll":           {"main": [[{"node": "IF: diarize done?",           "type": "main", "index": 0}]]},
        "IF: diarize done?": {"main": [
            [{"node": "Wait: cast review buffer", "type": "main", "index": 0}],  # true
            [{"node": "IF: diarize error?",       "type": "main", "index": 0}],  # false
        ]},
        "IF: diarize error?": {"main": [
            [{"node": "Stop: diarize error",      "type": "main", "index": 0}],  # true
            [{"node": "Code: diarize poll counter","type": "main", "index": 0}], # false
        ]},
        "Code: diarize poll counter":  {"main": [[{"node": "Wait: diarize buffer",        "type": "main", "index": 0}]]},
        "Wait: cast review buffer":    {"main": [[{"node": "GET: cast status",             "type": "main", "index": 0}]]},
        "GET: cast status":            {"main": [[{"node": "IF: cast locked?",             "type": "main", "index": 0}]]},
        "IF: cast locked?": {"main": [
            [{"node": "POST: translate",          "type": "main", "index": 0}],  # true
            [{"node": "Wait: cast review buffer", "type": "main", "index": 0}],  # false → loop
        ]},
        "POST: translate":             {"main": [[{"node": "Code: reset translate counter","type": "main", "index": 0}]]},
        "Code: reset translate counter":{"main": [[{"node": "Wait: translate buffer",      "type": "main", "index": 0}]]},
        "Wait: translate buffer":      {"main": [[{"node": "GET: translate poll",          "type": "main", "index": 0}]]},
        "GET: translate poll":         {"main": [[{"node": "IF: translate done?",          "type": "main", "index": 0}]]},
        "IF: translate done?": {"main": [
            [{"node": "Set: WF2 complete",        "type": "main", "index": 0}],  # true
            [{"node": "IF: translate error?",     "type": "main", "index": 0}],  # false
        ]},
        "IF: translate error?": {"main": [
            [{"node": "Stop: translate error",    "type": "main", "index": 0}],  # true
            [{"node": "Code: translate poll counter","type": "main", "index": 0}],# false
        ]},
        "Code: translate poll counter":{"main": [[{"node": "Wait: translate buffer",       "type": "main", "index": 0}]]},
    }

    return {
        "name": "Yanflix ◆ WF2: Gemini Script Director",
        "nodes": nodes,
        "connections": connections,
        "active": False,
        "settings": {"executionOrder": "v1"},
    }


if __name__ == "__main__":
    print("Building WF2 payload ...")
    wf = build_workflow()
    # active field is read-only via PUT — strip it
    wf.pop("active", None)

    print("Uploading rebuilt WF2 ...")
    result = http("PUT", f"/workflows/{WF2_ID}", wf)
    print(f"Updated: {result.get('name')} (id={result.get('id')})")

    # Activate via dedicated endpoint
    print("Activating WF2 ...")
    result2 = http("POST", f"/workflows/{WF2_ID}/activate")
    print(f"Active={result2.get('active')} — WF2 rebuilt and live.")
