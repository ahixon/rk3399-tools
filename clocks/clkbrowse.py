from flask import Flask, g, render_template, jsonify
import json
from werkzeug.local import LocalProxy
from clktool import RustClockManager

app = Flask(__name__)

def load_clockman():
    clockman = RustClockManager()

    # load clock tree
    with open('data/clocks.json', 'r') as f:
        clockman.load(json.load(f))

    # load some initial state
    with open('uboot-dump.txt', 'r') as f:
        clockman.load_dump(f)

    return clockman

def get_clockman():
    clockman = getattr(g, '_clockman', None)

    if clockman is None:
        clockman = g._clockman = load_clockman()

    return clockman

clockman = LocalProxy(get_clockman)

@app.route('/clocks/all')
def get_clocks():
    return jsonify(clockman.clocks)

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