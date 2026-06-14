"""
build_wf3.py — Build/Update Yanflix WF3: Dub Pipeline (Standard + AAVE)
Spec: yanflix_master_specification.md Section 7 (v3 FINAL)

Changes from prior version:
  - PUT to existing WF3 (hwxXPnTES4N0Rofm)
  - Error IF nodes inserted in all poll loops (translate, songs, synth, fit, render)
  - All Stop nodes now reachable via explicit error branches
  - IF: QC clear to render? (spec exact name, was "IF: QC clear?")
  - Counter node shared between initial POST entry and loop-back (clean pattern)
"""

import sys, os, json, uuid, urllib.request, urllib.error
sys.stdout.reconfigure(encoding='utf-8')

API_KEY = os.environ.get('N8N_API_KEY', (
    'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9'
    '.eyJzdWIiOiIxNGFlMzM4NC1lNjc1LTQ3ZjctODI0My1kZjk4OTE0ODdjNDEiLCJpc3MiOiJuOG4iLCJhdWQiOiJwdWJsaWMtYXBpIiwianRpIjoiZGQ1OTcxMjItOTBiZC00OWNjLWFiODQtNTg3YmY5ZDg3ZDA1IiwiaWF0IjoxNzgxMjk0NTU4LCJleHAiOjE3ODM4Mjg4MDB9'
    '.jy5C5ZEE6qvs7_r6xgmh0LfPHmumRAoh-q2pQ-QVzrc'
))
BASE   = 'http://localhost:5678/api/v1'
H      = {'X-N8N-API-KEY': API_KEY, 'Content-Type': 'application/json', 'Accept': 'application/json'}
WF3_ID = 'hwxXPnTES4N0Rofm'

def n8n(method, path, data=None):
    body = json.dumps(data, ensure_ascii=False).encode('utf-8') if data else None
    req  = urllib.request.Request(BASE + path, data=body, headers=H, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())

def nid(): return str(uuid.uuid4())

# Verify dub route exists
dub_url = '/api/dub_song'
if not os.path.isdir('app/api/dub_song'):
    dub_url = '/api/dub-song'
    if not os.path.isdir('app/api/dub-song'):
        sys.exit('ERROR: cannot find dub_song route dir — check app/api/')
print(f'Using dub route: {dub_url}')

STATUS_URL = 'http://host.docker.internal:3000/api/status'
BASE_URL   = 'http://host.docker.internal:3000'
EP         = "={{ $node['Webhook: run-dub'].json.body.ep_folder }}"
TRACK      = "={{ $node['SplitInBatches: per track'].json.track_mode }}"

# ── Node IDs ───────────────────────────────────────────────────────────────
WH        = nid()  # Webhook: run-dub
N2        = nid()  # GET: translate status
N3        = nid()  # IF: translate already done?
N4        = nid()  # POST: translate
N4a       = nid()  # Code: translate poll counter  (shared: POST entry + loop-back)
N4b       = nid()  # Wait: translate buffer
N4c       = nid()  # GET: translate poll
N4e       = nid()  # IF: translate done?
N4f       = nid()  # IF: translate error?          ← NEW
STOP_TR   = nid()  # Stop: translate error

N7        = nid()  # GET: song status
N8        = nid()  # IF: songs already done?

N9        = nid()  # POST: dub intro song
N9a       = nid()  # Code: intro poll counter      (shared: POST entry + loop-back)
N9b       = nid()  # Wait: intro song buffer
N9c       = nid()  # GET: intro song poll
N9e       = nid()  # IF: intro song done?
N9f       = nid()  # IF: intro song error?         ← NEW
STOP_SNG  = nid()  # Stop: song error

N12       = nid()  # POST: dub outro song
N12a      = nid()  # Code: outro poll counter      (shared: POST entry + loop-back)
N12b      = nid()  # Wait: outro song buffer
N12c      = nid()  # GET: outro song poll
N12e      = nid()  # IF: outro song done?
N12f      = nid()  # IF: outro song error?         ← NEW

N14       = nid()  # Set: track list
N14a      = nid()  # Split Out: track modes
N15       = nid()  # SplitInBatches: per track

N16       = nid()  # GET: synth status
N17       = nid()  # IF: synth already done?
N18       = nid()  # POST: synthesize dub
N18b      = nid()  # IF: synth 409?
N18c      = nid()  # Wait: GPU busy retry
N18d      = nid()  # Code: reset synth counter
N19       = nid()  # Wait: synth buffer
N20       = nid()  # GET: synth poll
N20a      = nid()  # IF: synth done?
N20b      = nid()  # Code: synth poll counter
N20c      = nid()  # IF: synth error?              ← NEW
STOP_SYN  = nid()  # Stop: synth error

N21       = nid()  # GET: fit status
N22       = nid()  # IF: fit already done?
N23       = nid()  # POST: fit audio
N23a      = nid()  # Code: fit poll counter        (shared: POST entry + loop-back)
N24       = nid()  # Wait: fit buffer
N25       = nid()  # GET: fit poll
N25a      = nid()  # IF: fit done?
N25b      = nid()  # IF: fit error?                ← NEW
STOP_FIT  = nid()  # Stop: fit error

N26       = nid()  # GET: render status
N27       = nid()  # IF: render already done?
N27a      = nid()  # GET: fit result for QC
N27b      = nid()  # IF: QC clear to render?       ← spec exact name
N27c      = nid()  # IF: skip QC gate?
STOP_QC   = nid()  # Stop: QC review needed
N28       = nid()  # POST: render video
N28a      = nid()  # Code: render poll counter     (shared: POST entry + loop-back)
N29       = nid()  # Wait: render buffer
N30       = nid()  # GET: render poll
N30a      = nid()  # IF: render done?
N30b      = nid()  # IF: render error?             ← NEW
STOP_RND  = nid()  # Stop: render error

N32       = nid()  # Set: pipeline complete


# ── Builder helpers ────────────────────────────────────────────────────────

def http_get(name, nid_, url, ep_expr, x, y, cont=False):
    n = {
        'parameters': {
            'url': url,
            'sendQuery': True,
            'queryParameters': {'parameters': [{'name': 'ep_folder', 'value': ep_expr}]},
            'options': {}
        },
        'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4,
        'name': name, 'id': nid_, 'position': [x, y]
    }
    if cont: n['continueOnFail'] = True
    return n

def http_post(name, nid_, url, params, x, y, cont=False):
    n = {
        'parameters': {
            'method': 'POST', 'url': url,
            'sendBody': True,
            'bodyParameters': {'parameters': params},
            'options': {}
        },
        'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4,
        'name': name, 'id': nid_, 'position': [x, y]
    }
    if cont: n['continueOnFail'] = True
    return n

def if_str(name, nid_, value1, value2, x, y):
    return {
        'parameters': {'conditions': {'string': [{'value1': value1, 'operation': 'equal', 'value2': value2}]}},
        'type': 'n8n-nodes-base.if', 'typeVersion': 1,
        'name': name, 'id': nid_, 'position': [x, y]
    }

def if_num(name, nid_, value1, value2, x, y):
    return {
        'parameters': {'conditions': {'number': [{'value1': value1, 'operation': 'equal', 'value2': value2}]}},
        'type': 'n8n-nodes-base.if', 'typeVersion': 1,
        'name': name, 'id': nid_, 'position': [x, y]
    }

def wait_n(name, nid_, seconds, x, y):
    return {
        'parameters': {'amount': seconds, 'unit': 'seconds'},
        'type': 'n8n-nodes-base.wait', 'typeVersion': 1,
        'name': name, 'id': nid_, 'position': [x, y],
        'webhookId': str(uuid.uuid4())
    }

def counter(name, nid_, limit, stage, x, y):
    js = (
        f"const c = ($input.item.json.pollCount || 0) + 1;\n"
        f"if (c > {limit}) throw new Error('Poll limit ({limit}) exceeded for {stage}');\n"
        f"return [{{ json: {{ ...$input.item.json, pollCount: c }} }}];"
    )
    return {
        'parameters': {'language': 'javaScript', 'jsCode': js},
        'type': 'n8n-nodes-base.code', 'typeVersion': 2,
        'name': name, 'id': nid_, 'position': [x, y]
    }

def stop(name, nid_, msg, x, y):
    return {
        'parameters': {'errorMessage': msg},
        'type': 'n8n-nodes-base.stopAndError', 'typeVersion': 1,
        'name': name, 'id': nid_, 'position': [x, y]
    }


# ── Nodes ──────────────────────────────────────────────────────────────────
nodes = [

    # 1. Webhook
    {
        'parameters': {'httpMethod': 'POST', 'path': 'run-dub', 'responseMode': 'onReceived', 'options': {}},
        'type': 'n8n-nodes-base.webhook', 'typeVersion': 2,
        'name': 'Webhook: run-dub', 'id': WH, 'position': [0, 300],
        'webhookId': str(uuid.uuid4())
    },

    # ── Translate phase ──────────────────────────────────────────────────────
    http_get('GET: translate status', N2, STATUS_URL, EP,    240, 300),
    if_str('IF: translate already done?', N3, '={{ $json.status_translate }}', 'done', 480, 300),
    http_post('POST: translate', N4, f'{BASE_URL}/api/translate',
              [{'name': 'ep_folder', 'value': EP}],           720, 300),
    # Counter shared: initial POST → counter → wait; also loop-back: IF error? FALSE → counter
    counter('Code: translate poll counter', N4a, 20, 'translate', 960, 300),
    wait_n('Wait: translate buffer', N4b, 8,                 1200, 300),
    http_get('GET: translate poll', N4c, STATUS_URL, EP,     1440, 300),
    if_str('IF: translate done?', N4e, '={{ $json.status_translate }}', 'done', 1680, 300),
    if_str('IF: translate error?', N4f, '={{ $json.status_translate }}', 'error', 1680, 500),
    stop('Stop: translate error', STOP_TR,
         'Translate failed — see /api/status for error detail', 1920, 500),

    # ── Song phase ───────────────────────────────────────────────────────────
    http_get('GET: song status', N7, STATUS_URL, EP,         2160, 300),
    if_str('IF: songs already done?', N8, '={{ $json.status_song_intro }}', 'done', 2400, 300),

    http_post('POST: dub intro song', N9, f'{BASE_URL}{dub_url}',
              [{'name': 'ep_folder', 'value': EP}, {'name': 'segment', 'value': 'intro'}, {'name': 'path_mode', 'value': 'A'}],
              2640, 300),
    counter('Code: intro poll counter', N9a, 120, 'intro song', 2880, 300),
    wait_n('Wait: intro song buffer', N9b, 30,               3120, 300),
    http_get('GET: intro song poll', N9c, STATUS_URL, EP,    3360, 300),
    if_str('IF: intro song done?', N9e, '={{ $json.status_song_intro }}', 'done', 3600, 300),
    if_str('IF: intro song error?', N9f, '={{ $json.status_song_intro }}', 'error', 3600, 500),
    stop('Stop: song error', STOP_SNG,
         'Song dubbing failed — see /api/status for error detail', 3840, 500),

    http_post('POST: dub outro song', N12, f'{BASE_URL}{dub_url}',
              [{'name': 'ep_folder', 'value': EP}, {'name': 'segment', 'value': 'outro'}, {'name': 'path_mode', 'value': 'A'}],
              3840, 300),
    counter('Code: outro poll counter', N12a, 120, 'outro song', 4080, 300),
    wait_n('Wait: outro song buffer', N12b, 30,              4320, 300),
    http_get('GET: outro song poll', N12c, STATUS_URL, EP,   4560, 300),
    if_str('IF: outro song done?', N12e, '={{ $json.status_song_outro }}', 'done', 4800, 300),
    if_str('IF: outro song error?', N12f, '={{ $json.status_song_outro }}', 'error', 4800, 500),
    # Stop: song error node shared (already declared above)

    # ── Track setup ──────────────────────────────────────────────────────────
    {
        'parameters': {
            'values': {'array': [{'name': 'track_modes',
                                  'value': "={{ $node['Webhook: run-dub'].json.body.track_modes }}"}]},
            'options': {}
        },
        'type': 'n8n-nodes-base.set', 'typeVersion': 3,
        'name': 'Set: track list', 'id': N14, 'position': [5040, 300]
    },
    {
        'parameters': {'fieldToSplitOut': 'track_modes', 'options': {'destinationFieldName': 'track_mode'}},
        'type': 'n8n-nodes-base.splitOut', 'typeVersion': 1,
        'name': 'Split Out: track modes', 'id': N14a, 'position': [5280, 300]
    },
    {
        'parameters': {'batchSize': 1, 'options': {}},
        'type': 'n8n-nodes-base.splitInBatches', 'typeVersion': 3,
        'name': 'SplitInBatches: per track', 'id': N15, 'position': [5520, 300]
    },

    # ── Synth phase ──────────────────────────────────────────────────────────
    http_get('GET: synth status', N16, STATUS_URL, EP,       5760, 300),
    if_str('IF: synth already done?', N17,
           f"={{{{ $json['status_synth_' + $node['SplitInBatches: per track'].json.track_mode] }}}}",
           'done', 6000, 300),
    http_post('POST: synthesize dub', N18, f'{BASE_URL}/api/actor',
              [{'name': 'ep_folder', 'value': EP}, {'name': 'track_mode', 'value': TRACK}],
              6240, 300, cont=True),
    if_num('IF: synth 409?', N18b, '={{ $json.statusCode }}', 409,  6480, 300),
    wait_n('Wait: GPU busy retry', N18c, 60,                 6480, 500),
    {
        'parameters': {'language': 'javaScript',
                       'jsCode': 'return [{ json: { ...$input.item.json, pollCount: 0 } }];'},
        'type': 'n8n-nodes-base.code', 'typeVersion': 2,
        'name': 'Code: reset synth counter', 'id': N18d, 'position': [6720, 300]
    },
    wait_n('Wait: synth buffer', N19, 45,                    6960, 300),
    http_get('GET: synth poll', N20, STATUS_URL, EP,         7200, 300),
    if_str('IF: synth done?', N20a,
           f"={{{{ $json['status_synth_' + $node['SplitInBatches: per track'].json.track_mode] }}}}",
           'done', 7440, 300),
    if_str('IF: synth error?', N20c,
           f"={{{{ $json['status_synth_' + $node['SplitInBatches: per track'].json.track_mode] }}}}",
           'error', 7440, 500),
    counter('Code: synth poll counter', N20b, 200, 'synth', 7680, 500),
    stop('Stop: synth error', STOP_SYN,
         'Synthesis failed — see /api/status for error detail', 7440, 700),

    # ── Fit phase ────────────────────────────────────────────────────────────
    http_get('GET: fit status', N21, STATUS_URL, EP,         7680, 300),
    if_str('IF: fit already done?', N22,
           f"={{{{ $json['status_fit_' + $node['SplitInBatches: per track'].json.track_mode] }}}}",
           'done', 7920, 300),
    http_post('POST: fit audio', N23, f'{BASE_URL}/api/fit-audio',
              [{'name': 'ep_folder', 'value': EP}, {'name': 'track_mode', 'value': TRACK}],
              8160, 300),
    counter('Code: fit poll counter', N23a, 60, 'fit',       8400, 300),
    wait_n('Wait: fit buffer', N24, 10,                      8640, 300),
    http_get('GET: fit poll', N25, STATUS_URL, EP,           8880, 300),
    if_str('IF: fit done?', N25a,
           f"={{{{ $json['status_fit_' + $node['SplitInBatches: per track'].json.track_mode] }}}}",
           'done', 9120, 300),
    if_str('IF: fit error?', N25b,
           f"={{{{ $json['status_fit_' + $node['SplitInBatches: per track'].json.track_mode] }}}}",
           'error', 9120, 500),
    stop('Stop: fit error', STOP_FIT,
         'Audio fitting failed — see /api/status for error detail', 9120, 700),

    # ── Render phase ─────────────────────────────────────────────────────────
    http_get('GET: render status', N26, STATUS_URL, EP,      9360, 300),
    if_str('IF: render already done?', N27,
           f"={{{{ $json['status_render_' + $node['SplitInBatches: per track'].json.track_mode] }}}}",
           'done', 9600, 300),
    http_get('GET: fit result for QC', N27a, STATUS_URL, EP, 9840, 300),
    if_num('IF: QC clear to render?', N27b,
           f"={{{{ $json['fit_result_' + $node['SplitInBatches: per track'].json.track_mode + '_qc_flagged'] ?? 0 }}}}",
           0, 10080, 300),
    if_str('IF: skip QC gate?', N27c,
           "={{ $node['Webhook: run-dub'].json.body.skip_qc_gate }}", 'true', 10320, 500),
    stop('Stop: QC review needed', STOP_QC,
         'Flagged lines found — open Script stage, regen red lines, then re-trigger. '
         'Add skip_qc_gate:true to force draft render.', 10560, 600),
    http_post('POST: render video', N28, f'{BASE_URL}/api/render',
              [{'name': 'ep_folder', 'value': EP}, {'name': 'track_mode', 'value': TRACK}],
              10560, 300),
    counter('Code: render poll counter', N28a, 60, 'render', 10800, 300),
    wait_n('Wait: render buffer', N29, 15,                   11040, 300),
    http_get('GET: render poll', N30, STATUS_URL, EP,        11280, 300),
    if_str('IF: render done?', N30a,
           f"={{{{ $json['status_render_' + $node['SplitInBatches: per track'].json.track_mode] }}}}",
           'done', 11520, 300),
    if_str('IF: render error?', N30b,
           f"={{{{ $json['status_render_' + $node['SplitInBatches: per track'].json.track_mode] }}}}",
           'error', 11520, 500),
    stop('Stop: render error', STOP_RND,
         'Render failed — see /api/status for error detail', 11520, 700),

    # ── Terminal ─────────────────────────────────────────────────────────────
    {
        'parameters': {
            'values': {'string': [
                {'name': 'status',         'value': 'complete'},
                {'name': 'ep_folder',       'value': EP},
                {'name': 'standard_output', 'value': "={{ $node['Webhook: run-dub'].json.body.ep_folder + '_standard.mp4' }}"},
                {'name': 'aave_output',     'value': "={{ $node['Webhook: run-dub'].json.body.ep_folder + '_aave.mp4' }}"},
            ]},
            'options': {}
        },
        'type': 'n8n-nodes-base.set', 'typeVersion': 3,
        'name': 'Set: pipeline complete', 'id': N32, 'position': [5760, 500]
    },
]


# ── Connections ────────────────────────────────────────────────────────────
# Pattern for poll loops:
#   POST → counter (shared entry) → wait → poll → IF done? TRUE→next / FALSE→IF error?
#   IF error? TRUE→stop / FALSE→counter (loop-back, same counter node)
# n8n allows multiple connection sources into a single node — this is the merge pattern.

connections = {

    # Webhook → translate
    'Webhook: run-dub':           {'main': [[{'node': 'GET: translate status',        'type': 'main', 'index': 0}]]},
    'GET: translate status':      {'main': [[{'node': 'IF: translate already done?',  'type': 'main', 'index': 0}]]},
    'IF: translate already done?': {'main': [
        [{'node': 'GET: song status',  'type': 'main', 'index': 0}],  # TRUE (skip)
        [{'node': 'POST: translate',   'type': 'main', 'index': 0}],  # FALSE (run)
    ]},
    # Translate poll loop
    'POST: translate':                {'main': [[{'node': 'Code: translate poll counter', 'type': 'main', 'index': 0}]]},
    'Code: translate poll counter':   {'main': [[{'node': 'Wait: translate buffer',       'type': 'main', 'index': 0}]]},
    'Wait: translate buffer':         {'main': [[{'node': 'GET: translate poll',          'type': 'main', 'index': 0}]]},
    'GET: translate poll':            {'main': [[{'node': 'IF: translate done?',          'type': 'main', 'index': 0}]]},
    'IF: translate done?':            {'main': [
        [{'node': 'GET: song status',        'type': 'main', 'index': 0}],  # TRUE (done)
        [{'node': 'IF: translate error?',    'type': 'main', 'index': 0}],  # FALSE
    ]},
    'IF: translate error?':           {'main': [
        [{'node': 'Stop: translate error',          'type': 'main', 'index': 0}],  # TRUE (error)
        [{'node': 'Code: translate poll counter',   'type': 'main', 'index': 0}],  # FALSE (loop)
    ]},

    # Song status check
    'GET: song status':           {'main': [[{'node': 'IF: songs already done?',      'type': 'main', 'index': 0}]]},
    'IF: songs already done?':    {'main': [
        [{'node': 'Set: track list',       'type': 'main', 'index': 0}],  # TRUE (skip songs)
        [{'node': 'POST: dub intro song',  'type': 'main', 'index': 0}],  # FALSE (dub songs)
    ]},

    # Intro song poll loop
    'POST: dub intro song':           {'main': [[{'node': 'Code: intro poll counter',     'type': 'main', 'index': 0}]]},
    'Code: intro poll counter':       {'main': [[{'node': 'Wait: intro song buffer',      'type': 'main', 'index': 0}]]},
    'Wait: intro song buffer':        {'main': [[{'node': 'GET: intro song poll',         'type': 'main', 'index': 0}]]},
    'GET: intro song poll':           {'main': [[{'node': 'IF: intro song done?',         'type': 'main', 'index': 0}]]},
    'IF: intro song done?':           {'main': [
        [{'node': 'POST: dub outro song',   'type': 'main', 'index': 0}],  # TRUE
        [{'node': 'IF: intro song error?',  'type': 'main', 'index': 0}],  # FALSE
    ]},
    'IF: intro song error?':          {'main': [
        [{'node': 'Stop: song error',          'type': 'main', 'index': 0}],  # TRUE (error)
        [{'node': 'Code: intro poll counter',  'type': 'main', 'index': 0}],  # FALSE (loop)
    ]},

    # Outro song poll loop
    'POST: dub outro song':           {'main': [[{'node': 'Code: outro poll counter',     'type': 'main', 'index': 0}]]},
    'Code: outro poll counter':       {'main': [[{'node': 'Wait: outro song buffer',      'type': 'main', 'index': 0}]]},
    'Wait: outro song buffer':        {'main': [[{'node': 'GET: outro song poll',         'type': 'main', 'index': 0}]]},
    'GET: outro song poll':           {'main': [[{'node': 'IF: outro song done?',         'type': 'main', 'index': 0}]]},
    'IF: outro song done?':           {'main': [
        [{'node': 'Set: track list',          'type': 'main', 'index': 0}],  # TRUE
        [{'node': 'IF: outro song error?',    'type': 'main', 'index': 0}],  # FALSE
    ]},
    'IF: outro song error?':          {'main': [
        [{'node': 'Stop: song error',          'type': 'main', 'index': 0}],  # TRUE (error) — shared stop
        [{'node': 'Code: outro poll counter',  'type': 'main', 'index': 0}],  # FALSE (loop)
    ]},

    # Track split
    'Set: track list':            {'main': [[{'node': 'Split Out: track modes',        'type': 'main', 'index': 0}]]},
    'Split Out: track modes':     {'main': [[{'node': 'SplitInBatches: per track',    'type': 'main', 'index': 0}]]},
    'SplitInBatches: per track':  {'main': [
        [{'node': 'GET: synth status',      'type': 'main', 'index': 0}],  # loop output (0)
        [{'node': 'Set: pipeline complete', 'type': 'main', 'index': 0}],  # done output (1)
    ]},

    # Synth phase
    'GET: synth status':          {'main': [[{'node': 'IF: synth already done?',       'type': 'main', 'index': 0}]]},
    'IF: synth already done?':    {'main': [
        [{'node': 'GET: fit status',       'type': 'main', 'index': 0}],  # TRUE (skip)
        [{'node': 'POST: synthesize dub',  'type': 'main', 'index': 0}],  # FALSE
    ]},
    'POST: synthesize dub':       {'main': [[{'node': 'IF: synth 409?',               'type': 'main', 'index': 0}]]},
    'IF: synth 409?':             {'main': [
        [{'node': 'Wait: GPU busy retry',       'type': 'main', 'index': 0}],  # TRUE (409 → retry)
        [{'node': 'Code: reset synth counter',  'type': 'main', 'index': 0}],  # FALSE (ok → poll)
    ]},
    'Wait: GPU busy retry':       {'main': [[{'node': 'POST: synthesize dub',         'type': 'main', 'index': 0}]]},
    'Code: reset synth counter':  {'main': [[{'node': 'Wait: synth buffer',           'type': 'main', 'index': 0}]]},
    'Wait: synth buffer':         {'main': [[{'node': 'GET: synth poll',              'type': 'main', 'index': 0}]]},
    'GET: synth poll':            {'main': [[{'node': 'IF: synth done?',              'type': 'main', 'index': 0}]]},
    'IF: synth done?':            {'main': [
        [{'node': 'GET: fit status',    'type': 'main', 'index': 0}],  # TRUE (done)
        [{'node': 'IF: synth error?',   'type': 'main', 'index': 0}],  # FALSE
    ]},
    'IF: synth error?':           {'main': [
        [{'node': 'Stop: synth error',         'type': 'main', 'index': 0}],  # TRUE (error)
        [{'node': 'Code: synth poll counter',  'type': 'main', 'index': 0}],  # FALSE (loop)
    ]},
    'Code: synth poll counter':   {'main': [[{'node': 'Wait: synth buffer',           'type': 'main', 'index': 0}]]},

    # Fit phase
    'GET: fit status':            {'main': [[{'node': 'IF: fit already done?',         'type': 'main', 'index': 0}]]},
    'IF: fit already done?':      {'main': [
        [{'node': 'GET: render status', 'type': 'main', 'index': 0}],  # TRUE (skip)
        [{'node': 'POST: fit audio',    'type': 'main', 'index': 0}],  # FALSE
    ]},
    'POST: fit audio':            {'main': [[{'node': 'Code: fit poll counter',        'type': 'main', 'index': 0}]]},
    'Code: fit poll counter':     {'main': [[{'node': 'Wait: fit buffer',              'type': 'main', 'index': 0}]]},
    'Wait: fit buffer':           {'main': [[{'node': 'GET: fit poll',                 'type': 'main', 'index': 0}]]},
    'GET: fit poll':              {'main': [[{'node': 'IF: fit done?',                 'type': 'main', 'index': 0}]]},
    'IF: fit done?':              {'main': [
        [{'node': 'GET: render status', 'type': 'main', 'index': 0}],  # TRUE (done)
        [{'node': 'IF: fit error?',     'type': 'main', 'index': 0}],  # FALSE
    ]},
    'IF: fit error?':             {'main': [
        [{'node': 'Stop: fit error',        'type': 'main', 'index': 0}],  # TRUE (error)
        [{'node': 'Code: fit poll counter', 'type': 'main', 'index': 0}],  # FALSE (loop)
    ]},

    # Render phase
    'GET: render status':         {'main': [[{'node': 'IF: render already done?',      'type': 'main', 'index': 0}]]},
    'IF: render already done?':   {'main': [
        [{'node': 'SplitInBatches: per track', 'type': 'main', 'index': 0}],  # TRUE (next track)
        [{'node': 'GET: fit result for QC',    'type': 'main', 'index': 0}],  # FALSE
    ]},
    'GET: fit result for QC':     {'main': [[{'node': 'IF: QC clear to render?',       'type': 'main', 'index': 0}]]},
    'IF: QC clear to render?':    {'main': [
        [{'node': 'POST: render video',  'type': 'main', 'index': 0}],  # TRUE (0 flags)
        [{'node': 'IF: skip QC gate?',   'type': 'main', 'index': 0}],  # FALSE (has flags)
    ]},
    'IF: skip QC gate?':          {'main': [
        [{'node': 'POST: render video',      'type': 'main', 'index': 0}],  # TRUE (bypass)
        [{'node': 'Stop: QC review needed',  'type': 'main', 'index': 0}],  # FALSE (stop)
    ]},
    'POST: render video':         {'main': [[{'node': 'Code: render poll counter',     'type': 'main', 'index': 0}]]},
    'Code: render poll counter':  {'main': [[{'node': 'Wait: render buffer',           'type': 'main', 'index': 0}]]},
    'Wait: render buffer':        {'main': [[{'node': 'GET: render poll',              'type': 'main', 'index': 0}]]},
    'GET: render poll':           {'main': [[{'node': 'IF: render done?',              'type': 'main', 'index': 0}]]},
    'IF: render done?':           {'main': [
        [{'node': 'SplitInBatches: per track', 'type': 'main', 'index': 0}],  # TRUE (next track)
        [{'node': 'IF: render error?',         'type': 'main', 'index': 0}],  # FALSE
    ]},
    'IF: render error?':          {'main': [
        [{'node': 'Stop: render error',          'type': 'main', 'index': 0}],  # TRUE (error)
        [{'node': 'Code: render poll counter',   'type': 'main', 'index': 0}],  # FALSE (loop)
    ]},
}

workflow = {
    'name': 'Yanflix ◆ WF3: Dub Pipeline (Standard + AAVE)',
    'nodes': nodes,
    'connections': connections,
    'settings': {'executionOrder': 'v1'},
    'staticData': None
}

# ── Deploy: deactivate → PUT → reactivate ─────────────────────────────────
print('Deactivating WF3 before update...')
n8n('POST', f'/workflows/{WF3_ID}/deactivate')

print('Uploading WF3...')
status, data = n8n('PUT', f'/workflows/{WF3_ID}', workflow)
print(f'PUT status: {status}')

if status not in (200, 201):
    print(f'PUT failed ({status}) — creating new WF3...')
    status, data = n8n('POST', '/workflows', workflow)
    print(f'POST status: {status}')
    if status not in (200, 201):
        print('ERROR:', json.dumps(data, ensure_ascii=False, indent=2)[:1000])
        sys.exit(1)

wf_id = data.get('id', WF3_ID)
print(f'WF3 id: {wf_id}')

print('Activating WF3...')
s2, _ = n8n('POST', f'/workflows/{wf_id}/activate')
print(f'Activate: {s2}')
print('Done — WF3 rebuilt with spec-compliant error branches and live.')
