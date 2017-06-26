from genrust import extract_registers, REGPREFIX_NAME_FIXED
import json
from clocktypes import *

class ClockManager(object):
    def __init__(self):
        super(ClockManager, self).__init__()
        self.clocks = {}
        self.clocks_by_name = {}

        self.FIXED_24MHZ_CLKNAME = 'clk_24m<IO>'
        self.FIXED_32KHZ_CLKNAME = 'clk_32k<IO>'
        self.known_clock_rates = {
            self.FIXED_24MHZ_CLKNAME: 24     * (10**6),
            self.FIXED_32KHZ_CLKNAME: 32.768 * (10**3)
        }

        self.RUST_PERIPHERALS = ['cru', 'pmucru', 'grf', 'pmugrf']

    def load_clock(self, clock, clock_info):
        # one would only expect mux iff parents > 1
        # likewise, if > 1 parent, must be mux
        if len(clock_info['parents']) > 1:
            assert clock_info['mux'] != None

        # we setup mux after load because we may rely on
        # clocks we haven't loaded by ID yet
        clock.parents = clock_info['parents']
        
        if clock_info['gate']:
            clock.gate = []
            for reg in extract_registers(clock_info['gate']):
                clock.gate.append (Gate(reg))

        # shouldn't be both divider and fractional divider
        assert not (clock_info['div'] and clock_info['frac'])

        if clock_info['div']:
            div_regs = extract_registers(clock_info['div'])
            if div_regs:
                assert len(div_regs) == 1
                div_reg = div_regs[0]

                clock.divider = Divider(div_reg, clock)
            elif clock_info['div'].startswith(REGPREFIX_NAME_FIXED):
                # fixed divider
                clock.divider = FixedDivider(clock, int(clock_info['div'][len(REGPREFIX_NAME_FIXED):]))
            elif clock_info['div'] == 'INVERT':
                pass
            else:
                print "error: div not null but non-fixed and more than one"
                print clock_info['div']
                assert len(div_regs) == 1

        if clock_info['frac']:
            frac_regs = extract_registers(clock_info['frac'])
            assert len(frac_regs) == 1
            frac_reg = frac_regs[0]

            clock.frac = Frac(frac_reg, clock)

        # add to clock
        assert clock.clk_id not in self.clocks
        self.clocks[clock.clk_id] = clock
        self.clocks_by_name[clock.name] = clock

        return clock

    def load(self, clocks):
        # load all IO clocks first
        for clock_info in clocks:
            if clock_info['module'] != 'IO_CLK':
                continue

            clk_rate = None
            clk_name = clock_info['clkname']

            if clk_name in self.known_clock_rates:
                clk_rate = self.known_clock_rates[clk_name]

            if clk_rate is None:
                print 'WARNING: unknown clock rate for IO clock', clk_name

            clock = FixedClock(clock_info['id'], clk_name, clk_rate)
            self.load_clock(clock, clock_info)

        # load all non-IO clocks now
        for clock_info in clocks:
            if clock_info['module'] == 'IO_CLK':
                continue

            if clock_info['module'] == 'PLL':
                clock = PLL(clock_info['id'], clock_info['clkname'],
                    self.clocks_by_name[self.FIXED_24MHZ_CLKNAME],
                    self.clocks_by_name[self.FIXED_32KHZ_CLKNAME])
            else:
                clock = Clock(clock_info['id'], clock_info['clkname'])

            self.load_clock(clock, clock_info)

        # and now link up the mux list
        for clock_info in clocks:
            clock = self.clocks[clock_info['id']]
            clock.parents = map(lambda x: self.clocks[x], clock.parents)

            if clock_info['mux']:
                mux_regs = extract_registers(clock_info['mux'])
                assert len(mux_regs) == 1
                mux_reg = mux_regs[0]

                clock.mux = Mux(mux_reg, clock, clock.parents)


    def gen_dumper(self, crate="rk3399_ap"):
        r = """extern crate %s;

pub fn print_clocks() {""" % crate

        for peripheral in self.RUST_PERIPHERALS:
            r += '    let %s = unsafe { &*%s::%s.get() };\n' % (
                crate, peripheral, peripheral.upper())

        for clock in self.clocks.values():
            r += '    println!("%s: ");\n' % clock.name

            for reg in clock.registers:
                r += '    print!("\\t%s: ");\n' % t

                accesses = clk[t]
                if not accesses:
                    continue

                rust_expr = '|'.join (map (lambda x: x.rust_expr(), accesses))

                rust_access = 'println!("0x{:x}", %s);' % rust_expr

                print rust_access

        print '}'

def main():
    cm = ClockManager()

    # load clock tree data
    with open('data/clocks.json', 'r') as f:
        cm.load(json.load(f))

    # setup some clocks

    cm.clocks[1].select(1)

    cm.clocks[1].dsmpd = 0
    cm.clocks[1].refdiv = 1
    cm.clocks[1].fbdiv = 20
    cm.clocks[1].frac = 4
    cm.clocks[1].postdiv1 = 1
    cm.clocks[1].postdiv2 = 300

    cm.clocks[85].gate[0].clocking_enabled = True
    cm.clocks[89].mux.select(0)
    c = cm.clocks[90]
    print 'clock:', c
    print 'divider:', c.divider
    print 'rate:', c.clk

    print cm.gen_dumper()

if __name__ == '__main__':
    main()