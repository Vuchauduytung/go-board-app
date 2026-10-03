# Small HTTP wrapper around KataGo's JSON analysis engine.
#   POST /analyze  {"matrix": [[0|1|2]], "to_play": "B"|"W", "komi"?, "rules"?, "max_visits"?, "top"?,
#                   "moves"?: [["B"|"W", "Q16"], ...] played after the position, "ownership"?: bool,
#                   "avoid"?: ["Q16", ...] moves not searched at the root, "wide_root_noise"?: float}
#   GET  /health
# Matrix: row 0 = top of the board, 0 empty, 1 black, 2 white (same as the app).
# Uses only the standard library; one KataGo process serves all requests (it batches internally).

import json
import os
import subprocess
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

KATAGO = os.environ.get('KATAGO_BIN', '/opt/katago/AppRun')
MODEL = os.environ.get('KATAGO_MODEL', '/opt/katago/model.bin.gz')
CONFIG = os.environ.get('KATAGO_CONFIG', '/opt/katago/analysis.cfg')
COLS = 'ABCDEFGHJKLMNOPQRSTUVWXYZ'
TIMEOUT = 60
MAX_VISITS = int(os.environ.get('KATAGO_MAX_VISITS', '3000'))   # CPU deployments cap the search

proc = subprocess.Popen([KATAGO, 'analysis', '-config', CONFIG, '-model', MODEL],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
pending = {}          # query id -> [threading.Event, response]
lock = threading.Lock()


def _reader():
    for line in proc.stdout:
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        slot = pending.get(msg.get('id'))
        if slot is not None and not msg.get('isDuringSearch'):
            slot[1] = msg
            slot[0].set()
    # KataGo exited: stop the server too so the container gets restarted
    print('KataGo process ended with code', proc.wait(), flush=True)
    os._exit(1)


threading.Thread(target=_reader, daemon=True).start()


def to_gtp(row, col, n):
    return f'{COLS[col]}{n - row}'


def from_gtp(move, n):
    """'Q16' -> (row from top, col); None for pass or anything off the board."""
    if move.lower() == 'pass':
        return None
    try:
        r, c = n - int(move[1:]), COLS.index(move[0].upper())
    except ValueError:
        return None
    return (r, c) if 0 <= r < n and 0 <= c < n else None


def analyze(req):
    m = req['matrix']
    n = len(m)
    if n not in (9, 13, 19) or any(len(r) != n for r in m):
        raise ValueError('board must be 9, 13 or 19 square')
    to_play = req.get('to_play', 'B').upper()
    stones = [['B' if v == 1 else 'W', to_gtp(r, c, n)] for r, row in enumerate(m) for c, v in enumerate(row) if v]
    played = []
    for color, mv in req.get('moves', [])[:1000]:   # whole games (reviews)
        if color not in ('B', 'W') or (mv.lower() != 'pass' and from_gtp(mv, n) is None):
            raise ValueError(f'bad move {color} {mv}')
        played.append([color, mv.upper()])
    root_player = ('W' if played[-1][0] == 'B' else 'B') if played else to_play
    avoid = [mv.upper() for mv in req.get('avoid', [])[:n * n] if from_gtp(mv, n)]
    qid = uuid.uuid4().hex
    query = {
        'id': qid,
        'initialStones': stones,
        'moves': played,
        'initialPlayer': to_play,
        'includeOwnership': bool(req.get('ownership')),
        'rules': req.get('rules', 'chinese'),
        'komi': float(req.get('komi', 7.5)),
        'boardXSize': n,
        'boardYSize': n,
        'maxVisits': int(min(max(req.get('max_visits', 400), 10), MAX_VISITS)),
    }
    if avoid:   # used to get KataGo's verdict on moves beyond its favourites
        query['avoidMoves'] = [{'player': root_player, 'moves': avoid, 'untilDepth': 1}]
    if req.get('wide_root_noise'):   # spreads root visits over more candidate moves
        query['overrideSettings'] = {'wideRootNoise': min(max(float(req['wide_root_noise']), 0.0), 1.0)}
    slot = [threading.Event(), None]
    pending[qid] = slot
    try:
        with lock:
            proc.stdin.write(json.dumps(query) + '\n')
            proc.stdin.flush()
        if not slot[0].wait(TIMEOUT):
            raise TimeoutError('KataGo did not answer in time')
    finally:
        pending.pop(qid, None)
    resp = slot[1]
    if 'error' in resp:
        raise ValueError(resp['error'])

    moves = []
    for mi in sorted(resp.get('moveInfos', []), key=lambda x: x['order'])[:int(req.get('top', 5))]:
        rc = from_gtp(mi['move'], n)
        moves.append({
            'move': mi['move'],
            'row': None if rc is None else rc[0],
            'col': None if rc is None else rc[1],
            'winrate': round(mi['winrate'], 4),      # for the side to move
            'score_lead': round(mi['scoreLead'], 2),  # points ahead for the side to move
            'visits': mi['visits'],
            'pv': mi.get('pv', [])[:12],   # reviews show 10-move variations
        })
    root = resp.get('rootInfo', {})
    own = resp.get('ownership')   # row-major from the top-left, side-to-move perspective
    return {
        'ownership': [[round(own[r * n + c], 2) for c in range(n)] for r in range(n)] if own else None,
        'to_play': to_play,
        'winrate': round(root.get('winrate', 0.5), 4),
        'score_lead': round(root.get('scoreLead', 0.0), 2),
        'visits': root.get('visits'),
        'moves': moves,
        'warnings': resp.get('warnings', []),
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == '/health':
            self._send(200 if proc.poll() is None else 503, {'status': 'ok' if proc.poll() is None else 'down'})
        else:
            self._send(404, {'error': 'not found'})

    def do_POST(self):
        if self.path != '/analyze':
            return self._send(404, {'error': 'not found'})
        try:
            req = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
            self._send(200, analyze(req))
        except (ValueError, KeyError, TypeError) as ex:
            self._send(400, {'error': str(ex)})
        except Exception as ex:
            self._send(500, {'error': str(ex)})

    def log_message(self, *args):
        pass


if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0', int(os.environ.get('PORT', '8080'))), Handler).serve_forever()
