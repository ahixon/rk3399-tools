from flask import Flask, g, render_template, jsonify
from flask.json import JSONEncoder
import json

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

def get_clockman():
    clockman = getattr(g, '_clockman', None)

    if clockman is None:
        clockman = g._clockman = load_clockman()

    return clockman

clockman = LocalProxy(get_clockman)

@app.route('/clocks/all')
def all_clocks():
    return jsonify(clockman.clocks.values())

@app.route('/clocks/<clkname>', methods=('GET', 'POST'))
def clock(name):
    if request.method == 'POST':
        # update clock
        pass
    else:
        # just return current state of clock
        pass

@app.route('/')
def index():
    return render_template('index.html')