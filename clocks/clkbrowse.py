from flask import Flask, g, render_template, jsonify, request
from flask.json import JSONEncoder
import json
import pickle

from werkzeug.local import LocalProxy
from clktool import RustClockManager
from clocks import Clock, Gate, Divider, Mux, Frac, FixedDivider, \
    DisconnectedClockException

class ClockJSONEncoder(JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Clock):
            d = {
                'id': obj.clk_id,
                'clkname': obj.name,
                'module': obj.module,
                'parents': map(lambda x: x.clk_id, obj.parents)
            }

            try:
                d['clk'] = obj.clk
            except DisconnectedClockException:
                d['clk'] = None

            for child in ('divider', 'frac', 'gate', 'mux'):
                childval = obj.__dict__[child]
                if childval is not None:
                    if child == 'gate':
                        # FIXME: support multiple gates
                        childval = childval[0]

                    d[child] = childval

                    if child == 'mux':
                        # also send mux selection
                        d['muxSelection'] = childval.selected_clk_idx

                    if child == 'gate':
                        # also send gate status
                        d['gateEnabled'] = childval.clocking_enabled
                else:
                    d[child] = None

            return d

        if isinstance(obj, Gate) or isinstance(obj, Mux) or \
            isinstance(obj, Divider) or isinstance(obj, Frac):
            if 'reg' in obj.__dict__:
                return obj.reg.name
            elif isinstance(obj, FixedDivider):
                return obj.div

            assert False

        return super(ClockJSONEncoder, self).default(obj)

# class ClockJSONDecoder(JSONDecoder):
#     pass

app = Flask(__name__)
app.json_encoder = ClockJSONEncoder
# app.json_decoder = ClockJSONDecoder

def load_clockman():
    clockman = RustClockManager()

    # load clock tree
    with open('data/clocks.json', 'r') as f:
        clockman.load(json.load(f))

    # load some initial state
    with open('uboot-dump.txt', 'r') as f:
        clockman.load_dump(f)

    # XXX: setup lpll to be in a valid state
    # (postdiv1 is zero?!)
    clockman.clocks_by_name['lpll'].postdiv1 = 1

    return clockman

clockman = load_clockman()

@app.route('/clocks/all')
def all_clocks():
    return jsonify(clockman.clocks.values())

@app.route('/clocks/<clkname>', methods=('GET', 'POST'))
def clock(clkname):
    if request.method == 'POST':
        clock = clockman.clocks_by_name[clkname]
        j = request.get_json()

        # update clock
        assert 'gateEnabled' in j
        clock.gate[0].clocking_enabled = j['gateEnabled']

        # mutate back?
        clockman.clocks_by_name[clkname] = clock

        return jsonify(clock)
    else:
        return jsonify(clockman.clocks_by_name[clkname])

@app.route('/state/dump/<fname>')
def dump_state(fname):
    with open(fname, 'wb') as f:
        f.write(clockman.save_dump())

    return jsonify({'status': 'ok'})

@app.route('/state/load/<fname>')
def load_state(fname):
    global clockman
    with open(fname, 'rb') as f:
        clockman.load_dump(f)

    return jsonify({'status': 'ok'})

@app.route('/')
def index():
    return render_template('index.html')