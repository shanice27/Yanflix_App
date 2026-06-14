"""
build_wf1.py — Rebuild Yanflix WF1: Character Vault & Voice Bank
Spec: yanflix_master_specification.md Section 7 (v3 FINAL)

Flow:
  Webhook → check isolate done → (skip or run+poll) isolate
          → check transcribe done → (skip or run+poll) transcribe
          → check segment done → (skip or run+poll) segment-lines
          → [cast-lock gate] poll /api/cast-status every 30s until cast_locked=true
          → harvest seeds → poll
          → clone speakers → poll
          → Set: WF1 complete

All status checks use /api/status (canonical endpoint, spec Section 7).
ep_folder comes directly from webhook payload body (never computed from parts).
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
WF1_ID = '4SimOolWRRLJfWIw'

def n8n(method, path, data=None):
    body = json.dumps(data, ensure_ascii=False).encode('utf-8') if data else None
    req  = urllib.request.Request(BASE + path, data=body, headers=H, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())

def nid(): return str(uuid.uuid4())

STATUS_URL = 'http://host.docker.internal:3000/api/status'
BASE_URL   = 'http://host.docker.internal:3000'
WH         = 'Webhook: harvest-characters'
EP         = f"={{{{ $node['{WH}'].json.body.ep_folder }}}}"
SHOW       = f"={{{{ $node['{WH}'].json.body.show_name }}}}"
LANG       = f"={{{{ $node['{WH}'].json.body.source_lang }}}}"

# ── Node IDs ───────────────────────────────────────────────────────────────
N_WH    = nid()  # Webhook: harvest-characters

# Isolate phase
N_ISO_S  = nid()  # GET: isolate status
N_ISO_IF = nid()  # IF: isolate already done?
N_ISO_P  = nid()  # POST: isolate
N_ISO_C  = nid()  # Code: isolate poll counter
N_ISO_W  = nid()  # Wait: isolate buffer
N_ISO_G  = nid()  # GET: isolate poll
N_ISO_D  = nid()  # IF: isolate done?
N_ISO_E  = nid()  # IF: isolate error?
STOP_ISO = nid()  # Stop: isolate error

# Transcribe phase
N_TR_S   = nid()  # GET: transcribe status
N_TR_IF  = nid()  # IF: transcribe already done?
N_TR_P   = nid()  # POST: transcribe
N_TR_C   = nid()  # Code: transcribe poll counter
N_TR_W   = nid()  # Wait: transcribe buffer
N_TR_G   = nid()  # GET: transcribe poll
N_TR_D   = nid()  # IF: transcribe done?
N_TR_E   = nid()  # IF: transcribe error?
STOP_TR  = nid()  # Stop: transcribe error

# Segment phase
N_SEG_S  = nid()  # GET: segment status
N_SEG_IF = nid()  # IF: segment already done?
N_SEG_P  = nid()  # POST: segment lines
N_SEG_C  = nid()  # Code: segment poll counter
N_SEG_W  = nid()  # Wait: segment buffer
N_SEG_G  = nid()  # GET: segment poll
N_SEG_D  = nid()  # IF: segment done?
N_SEG_E  = nid()  # IF: segment error?
STOP_SEG = nid()  # Stop: segment error

# Cast-lock gate
N_CAST_W = nid()  # Wait: cast review buffer
N_CAST_G = nid()  # GET: cast status
N_CAST_D = nid()  # IF: cast locked?

# Harvest phase
N_HV_P   = nid()  # POST: harvest seeds
N_HV_C   = nid()  # Code: harvest poll counter
N_HV_W   = nid()  # Wait: harvest buffer
N_HV_G   = nid()  # GET: harvest poll
N_HV_D   = nid()  # IF: harvest done?
N_HV_E   = nid()  # IF: harvest error?
STOP_HV  = nid()  # Stop: harvest error

# Clone phase
N_CL_P   = nid()  # POST: clone speakers
N_CL_C   = nid()  # Code: clone poll counter
N_CL_W   = nid()  # Wait: clone buffer
N_CL_G   = nid()  # GET: clone poll
N_CL_D   = nid()  # IF: clone done?
N_CL_E   = nid()  # IF: clone error?
STOP_CL  = nid()  # Stop: clone error

N_DONE   = nid()  # Set: WF1 complete


# ── Builders ───────────────────────────────────────────────────────────────

def http_get(name, nid_, url, ep_expr, x, y):
    return {
        'parameters': {
            'url': url,
            'sendQuery': True,
            'queryParameters': {'parameters': [{'name': 'ep_folder', 'value': ep_expr}]},
            'options': {}
        },
        'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4,
        'name': name, 'id': nid_, 'position': [x, y]
    }

def http_post(name, nid_, url, params, x, y):
    return {
        'parameters': {
            'method': 'POST', 'url': url,
            'sendBody': True,
            'bodyParameters': {'parameters': params},
            'options': {}
        },
        'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4,
        'name': name, 'id': nid_, 'position': [x, y]
    }

def if_str(name, nid_, value1, value2, x, y):
    return {
        'parameters': {'conditions': {'string': [{'value1': value1, 'operation': 'equal', 'value2': value2}]}},
        'type': 'n8n-nodes-base.if', 'typeVersion': 1,
        'name': name, 'id': nid_, 'position': [x, y]
    }

def wait_n(name, nid_, seconds, x, y):
    return {
        'parameters': {'resume': 'timeInterval', 'amount': seconds, 'unit': 'seconds'},
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

    # Webhook
    {
        'parameters': {
            'httpMethod': 'POST', 'path': 'harvest-characters',
            'responseMode': 'onReceived', 'options': {}
        },
        'type': 'n8n-nodes-base.webhook', 'typeVersion': 2,
        'name': WH, 'id': N_WH, 'position': [0, 300],
        'webhookId': str(uuid.uuid4())
    },

    # ── Isolate phase (Demucs, GPU, ~20-40 min) ─────────────────────────────
    http_get('GET: isolate status', N_ISO_S, STATUS_URL, EP, 240, 300),
    if_str('IF: isolate already done?', N_ISO_IF, '={{ $json.status_isolate }}', 'done', 480, 300),
    http_post('POST: isolate', N_ISO_P, f'{BASE_URL}/api/isolate',
              [{'name': 'ep_folder',  'value': EP},
               {'name': 'source_lang','value': LANG}], 720, 400),
    counter('Code: isolate poll counter', N_ISO_C, 240, 'isolate (Demucs)', 960, 400),
    wait_n('Wait: isolate buffer', N_ISO_W, 30, 1200, 400),
    http_get('GET: isolate poll', N_ISO_G, STATUS_URL, EP, 1440, 400),
    if_str('IF: isolate done?', N_ISO_D, '={{ $json.status_isolate }}', 'done', 1680, 400),
    if_str('IF: isolate error?', N_ISO_E, '={{ $json.status_isolate }}', 'error', 1680, 580),
    stop('Stop: isolate error', STOP_ISO, 'Demucs isolation failed — check /api/status', 1920, 580),

    # ── Transcribe phase (Groq Whisper cloud or local, ~30s-40min) ───────────
    http_get('GET: transcribe status', N_TR_S, STATUS_URL, EP, 1920, 300),
    if_str('IF: transcribe already done?', N_TR_IF, '={{ $json.status_transcribe }}', 'done', 2160, 300),
    http_post('POST: transcribe', N_TR_P, f'{BASE_URL}/api/transcribe',
              [{'name': 'ep_folder',  'value': EP},
               {'name': 'source_lang','value': LANG}], 2400, 400),
    counter('Code: transcribe poll counter', N_TR_C, 120, 'transcribe', 2640, 400),
    wait_n('Wait: transcribe buffer', N_TR_W, 20, 2880, 400),
    http_get('GET: transcribe poll', N_TR_G, STATUS_URL, EP, 3120, 400),
    if_str('IF: transcribe done?', N_TR_D, '={{ $json.status_transcribe }}', 'done', 3360, 400),
    if_str('IF: transcribe error?', N_TR_E, '={{ $json.status_transcribe }}', 'error', 3360, 580),
    stop('Stop: transcribe error', STOP_TR, 'Transcription failed — check /api/status', 3600, 580),

    # ── Segment lines phase (Pydub, CPU, ~1-3 min) ───────────────────────────
    http_get('GET: segment status', N_SEG_S, STATUS_URL, EP, 3600, 300),
    if_str('IF: segment already done?', N_SEG_IF, '={{ $json.status_segment }}', 'done', 3840, 300),
    http_post('POST: segment lines', N_SEG_P, f'{BASE_URL}/api/segment-lines',
              [{'name': 'ep_folder', 'value': EP}], 4080, 400),
    counter('Code: segment poll counter', N_SEG_C, 40, 'segment-lines', 4320, 400),
    wait_n('Wait: segment buffer', N_SEG_W, 8, 4560, 400),
    http_get('GET: segment poll', N_SEG_G, STATUS_URL, EP, 4800, 400),
    if_str('IF: segment done?', N_SEG_D, '={{ $json.status_segment }}', 'done', 5040, 400),
    if_str('IF: segment error?', N_SEG_E, '={{ $json.status_segment }}', 'error', 5040, 580),
    stop('Stop: segment error', STOP_SEG, 'Segment lines failed — check /api/status', 5280, 580),

    # ── Cast-lock gate: WF2 runs diarize+translate, human reviews cast ────────
    # WF1 pauses here until save_cast sets cast_locked=true in state_director.json
    wait_n('Wait: cast review buffer', N_CAST_W, 30, 5280, 300),
    http_get('GET: cast status', N_CAST_G, f'{BASE_URL}/api/cast-status', EP, 5520, 300),
    if_str('IF: cast locked?', N_CAST_D, '={{ String($json.cast_locked) }}', 'true', 5760, 300),

    # ── Harvest seeds phase (NISQA scoring, CPU, ~2-5 min) ────────────────────
    http_post('POST: harvest seeds', N_HV_P, f'{BASE_URL}/api/harvest-seeds',
              [{'name': 'ep_folder', 'value': EP},
               {'name': 'show_name', 'value': SHOW}], 6000, 300),
    counter('Code: harvest poll counter', N_HV_C, 30, 'harvest-seeds', 6240, 300),
    wait_n('Wait: harvest buffer', N_HV_W, 10, 6480, 300),
    http_get('GET: harvest poll', N_HV_G, STATUS_URL, EP, 6720, 300),
    if_str('IF: harvest done?', N_HV_D, '={{ $json.status_harvest }}', 'done', 6960, 300),
    if_str('IF: harvest error?', N_HV_E, '={{ $json.status_harvest }}', 'error', 6960, 500),
    stop('Stop: harvest error', STOP_HV, 'Harvest seeds failed — check /api/status', 6960, 700),

    # ── Clone speakers phase (ElevenLabs emotional bank, ~1-5 min) ───────────
    http_post('POST: clone speakers', N_CL_P, f'{BASE_URL}/api/clone-speakers',
              [{'name': 'ep_folder', 'value': EP},
               {'name': 'show_name', 'value': SHOW}], 7200, 300),
    counter('Code: clone poll counter', N_CL_C, 60, 'clone-speakers', 7440, 300),
    wait_n('Wait: clone buffer', N_CL_W, 10, 7680, 300),
    http_get('GET: clone poll', N_CL_G, STATUS_URL, EP, 7920, 300),
    if_str('IF: clone done?', N_CL_D, '={{ $json.status_clone }}', 'done', 8160, 300),
    if_str('IF: clone error?', N_CL_E, '={{ $json.status_clone }}', 'error', 8160, 500),
    stop('Stop: clone error', STOP_CL, 'Clone speakers failed — check /api/status', 8160, 700),

    # Terminal
    {
        'parameters': {
            'assignments': {'assignments': [
                {'id': nid(), 'name': 'status',    'value': 'complete',   'type': 'string'},
                {'id': nid(), 'name': 'ep_folder', 'value': EP,           'type': 'string'},
                {'id': nid(), 'name': 'message',   'value': 'Voice bank built — ready for WF3', 'type': 'string'},
            ]},
            'options': {}
        },
        'type': 'n8n-nodes-base.set', 'typeVersion': 3.4,
        'name': 'Set: WF1 complete', 'id': N_DONE, 'position': [8400, 300]
    },
]


# ── Connections ────────────────────────────────────────────────────────────
connections = {

    WH: {'main': [[{'node': 'GET: isolate status', 'type': 'main', 'index': 0}]]},

    # Isolate
    'GET: isolate status':        {'main': [[{'node': 'IF: isolate already done?',   'type': 'main', 'index': 0}]]},
    'IF: isolate already done?':  {'main': [
        [{'node': 'GET: transcribe status',  'type': 'main', 'index': 0}],  # TRUE (skip)
        [{'node': 'POST: isolate',           'type': 'main', 'index': 0}],  # FALSE (run)
    ]},
    'POST: isolate':              {'main': [[{'node': 'Code: isolate poll counter',  'type': 'main', 'index': 0}]]},
    'Code: isolate poll counter': {'main': [[{'node': 'Wait: isolate buffer',        'type': 'main', 'index': 0}]]},
    'Wait: isolate buffer':       {'main': [[{'node': 'GET: isolate poll',           'type': 'main', 'index': 0}]]},
    'GET: isolate poll':          {'main': [[{'node': 'IF: isolate done?',           'type': 'main', 'index': 0}]]},
    'IF: isolate done?':          {'main': [
        [{'node': 'GET: transcribe status',  'type': 'main', 'index': 0}],  # TRUE
        [{'node': 'IF: isolate error?',      'type': 'main', 'index': 0}],  # FALSE
    ]},
    'IF: isolate error?':         {'main': [
        [{'node': 'Stop: isolate error',         'type': 'main', 'index': 0}],  # TRUE (error)
        [{'node': 'Code: isolate poll counter',  'type': 'main', 'index': 0}],  # FALSE (loop)
    ]},

    # Transcribe
    'GET: transcribe status':        {'main': [[{'node': 'IF: transcribe already done?',   'type': 'main', 'index': 0}]]},
    'IF: transcribe already done?':  {'main': [
        [{'node': 'GET: segment status', 'type': 'main', 'index': 0}],  # TRUE (skip)
        [{'node': 'POST: transcribe',    'type': 'main', 'index': 0}],  # FALSE (run)
    ]},
    'POST: transcribe':              {'main': [[{'node': 'Code: transcribe poll counter',  'type': 'main', 'index': 0}]]},
    'Code: transcribe poll counter': {'main': [[{'node': 'Wait: transcribe buffer',        'type': 'main', 'index': 0}]]},
    'Wait: transcribe buffer':       {'main': [[{'node': 'GET: transcribe poll',           'type': 'main', 'index': 0}]]},
    'GET: transcribe poll':          {'main': [[{'node': 'IF: transcribe done?',           'type': 'main', 'index': 0}]]},
    'IF: transcribe done?':          {'main': [
        [{'node': 'GET: segment status', 'type': 'main', 'index': 0}],  # TRUE
        [{'node': 'IF: transcribe error?', 'type': 'main', 'index': 0}],  # FALSE
    ]},
    'IF: transcribe error?':         {'main': [
        [{'node': 'Stop: transcribe error',         'type': 'main', 'index': 0}],  # TRUE
        [{'node': 'Code: transcribe poll counter',  'type': 'main', 'index': 0}],  # FALSE (loop)
    ]},

    # Segment lines
    'GET: segment status':        {'main': [[{'node': 'IF: segment already done?',   'type': 'main', 'index': 0}]]},
    'IF: segment already done?':  {'main': [
        [{'node': 'Wait: cast review buffer', 'type': 'main', 'index': 0}],  # TRUE (skip → cast gate)
        [{'node': 'POST: segment lines',      'type': 'main', 'index': 0}],  # FALSE (run)
    ]},
    'POST: segment lines':        {'main': [[{'node': 'Code: segment poll counter',  'type': 'main', 'index': 0}]]},
    'Code: segment poll counter': {'main': [[{'node': 'Wait: segment buffer',        'type': 'main', 'index': 0}]]},
    'Wait: segment buffer':       {'main': [[{'node': 'GET: segment poll',           'type': 'main', 'index': 0}]]},
    'GET: segment poll':          {'main': [[{'node': 'IF: segment done?',           'type': 'main', 'index': 0}]]},
    'IF: segment done?':          {'main': [
        [{'node': 'Wait: cast review buffer', 'type': 'main', 'index': 0}],  # TRUE → cast gate
        [{'node': 'IF: segment error?',       'type': 'main', 'index': 0}],  # FALSE
    ]},
    'IF: segment error?':         {'main': [
        [{'node': 'Stop: segment error',         'type': 'main', 'index': 0}],  # TRUE
        [{'node': 'Code: segment poll counter',  'type': 'main', 'index': 0}],  # FALSE (loop)
    ]},

    # Cast-lock gate
    'Wait: cast review buffer':   {'main': [[{'node': 'GET: cast status',     'type': 'main', 'index': 0}]]},
    'GET: cast status':           {'main': [[{'node': 'IF: cast locked?',     'type': 'main', 'index': 0}]]},
    'IF: cast locked?':           {'main': [
        [{'node': 'POST: harvest seeds',      'type': 'main', 'index': 0}],  # TRUE (proceed)
        [{'node': 'Wait: cast review buffer', 'type': 'main', 'index': 0}],  # FALSE (loop back)
    ]},

    # Harvest seeds
    'POST: harvest seeds':        {'main': [[{'node': 'Code: harvest poll counter',  'type': 'main', 'index': 0}]]},
    'Code: harvest poll counter': {'main': [[{'node': 'Wait: harvest buffer',        'type': 'main', 'index': 0}]]},
    'Wait: harvest buffer':       {'main': [[{'node': 'GET: harvest poll',           'type': 'main', 'index': 0}]]},
    'GET: harvest poll':          {'main': [[{'node': 'IF: harvest done?',           'type': 'main', 'index': 0}]]},
    'IF: harvest done?':          {'main': [
        [{'node': 'POST: clone speakers',    'type': 'main', 'index': 0}],  # TRUE
        [{'node': 'IF: harvest error?',      'type': 'main', 'index': 0}],  # FALSE
    ]},
    'IF: harvest error?':         {'main': [
        [{'node': 'Stop: harvest error',         'type': 'main', 'index': 0}],  # TRUE
        [{'node': 'Code: harvest poll counter',  'type': 'main', 'index': 0}],  # FALSE (loop)
    ]},

    # Clone speakers
    'POST: clone speakers':       {'main': [[{'node': 'Code: clone poll counter',   'type': 'main', 'index': 0}]]},
    'Code: clone poll counter':   {'main': [[{'node': 'Wait: clone buffer',         'type': 'main', 'index': 0}]]},
    'Wait: clone buffer':         {'main': [[{'node': 'GET: clone poll',            'type': 'main', 'index': 0}]]},
    'GET: clone poll':            {'main': [[{'node': 'IF: clone done?',            'type': 'main', 'index': 0}]]},
    'IF: clone done?':            {'main': [
        [{'node': 'Set: WF1 complete',    'type': 'main', 'index': 0}],  # TRUE
        [{'node': 'IF: clone error?',     'type': 'main', 'index': 0}],  # FALSE
    ]},
    'IF: clone error?':           {'main': [
        [{'node': 'Stop: clone error',        'type': 'main', 'index': 0}],  # TRUE
        [{'node': 'Code: clone poll counter', 'type': 'main', 'index': 0}],  # FALSE (loop)
    ]},
}

workflow = {
    'name': 'Yanflix ◆ WF1: Character Vault & Voice Bank',
    'nodes': nodes,
    'connections': connections,
    'settings': {'executionOrder': 'v1'},
    'staticData': None
}

# ── Deploy ─────────────────────────────────────────────────────────────────
print('Deactivating WF1 before update...')
n8n('POST', f'/workflows/{WF1_ID}/deactivate')

print('Uploading WF1...')
status, data = n8n('PUT', f'/workflows/{WF1_ID}', workflow)
print(f'PUT status: {status}')

if status not in (200, 201):
    print(f'PUT failed — creating new WF1...')
    status, data = n8n('POST', '/workflows', workflow)
    print(f'POST status: {status}')
    if status not in (200, 201):
        print('ERROR:', json.dumps(data, ensure_ascii=False, indent=2)[:1000])
        sys.exit(1)

wf_id = data.get('id', WF1_ID)
print(f'WF1 id: {wf_id}')
print('Activating WF1...')
s2, _ = n8n('POST', f'/workflows/{wf_id}/activate')
print(f'Activate: {s2}')
print('Done — WF1 rebuilt and live.')
