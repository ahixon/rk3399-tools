from genrust import extract_registers, REGPREFIX_NAME_FIXED
import json

class Divider(object):
    def __init__(self, reg, owner, value=None):
        super(Divider, self).__init__()
        self.reg = reg
        self.owner = owner
        self.div = None

        if value:
            self.from_reg_value(value)

    @property
    def clk(self):
        print self,
        return self.owner.clk_src.clk / (self.div + 1)

    def __repr__(self):
        return '/ %d' % (self.div + 1)

    @property
    def reg_value(self):
        return self.div

    def from_reg_value(self, reg):
        self.div = reg

class FixedDivider(Divider):
    def __init__(self, owner, value):
        super(FixedDivider, self).__init__(None, owner, value)

    @property
    def clk(self):
        print self,
        return self.owner.clk_src.clk / self.div

    def __repr__(self):
        return '/ %df' % self.div

class Frac(object):
    def __init__(self, reg, owner, value=None):
        super(Frac, self).__init__()
        self.reg = reg
        self.owner = owner
        self.clk_src = None
        self.numerator = None
        self.denominator = None

        if value:
            self.from_reg_value(value)

    @property
    def clk(self):
        print self,
        return self.owner.clk_src.clk * self.numerator / self.denominator

    @property
    def reg_value(self):
        return self.numerator << 16 | self.denominator

    def from_reg_value(self, value):
        self.numerator = value >> 16
        self.denominator = value & ((1 << 16) - 1)

    def __repr__(self):
        return '* %d/%d' % (self.numerator, self.denominator)

class Gate(object):
    def __init__(self, reg, value=None):
        super(Gate, self).__init__()
        self.reg = reg
        self.clocking_enabled = None

        if value:
            self.from_reg_value(value)

    @property
    def reg_value(self):
        if self.clocking_enabled:
            return 1

        return 0

    def from_reg_value(self, value):
        self.clocking_enabled = (value == 0)

    def __repr__(self):
        return 'G=%s' % (self.clocking_enabled)

class Mux(object):
    def __init__(self, reg, owner, clocks, value=None):
        super(Mux, self).__init__()
        self.reg = reg
        self.owner = owner
        
        self.clocks = clocks
        self.selected_clk_idx = None

        self.clk_src = None

        if value:
            self.from_reg_value(value)

    @property
    def reg_value(self):
        return self.selected_clk_idx

    def from_reg_value(self, value):
        self.selected_clk_idx = value
        self.clk_src = self.clocks[self.selected_clk_idx]

    def select(self, clkid):
        self.from_reg_value(clkid)

    @property
    def clk(self):
        print self,
        return self.clk_src.clk

    def __repr__(self):
        if self.selected_clk_idx is not None:
            return 'M=%s (%s)' % (self.clocks[self.selected_clk_idx], self.clocks)
        else:
            return 'M=? (%s)' % (self.clocks)

class Clock(object):
    def __init__(self, clk_id, name):
        super(Clock, self).__init__()
        self.clk_id = clk_id
        self.name = name

        self.divider = None
        self.frac = None
        self.gate = None
        self.mux = None

        self.parents = []
    
    @property
    def clk_src(self):
        if self.mux:
            return self.mux.clk_src
        else:
            return self.parents[0]

    @property
    # FIXME: makes a big assumption that the clock is enabled
    # if at least one of the gates are powered, rather than
    # some boolean condition on the state of the gates
    def clk(self):
        print self,
        if self.gate:
            for g in self.gate:
                if g.clocking_enabled == None:
                    print 'warning:', g, 'on', self, 'in undefined state'

                assert g.clocking_enabled != None
                if g.clocking_enabled == False:
                    return None

        return self._clk

    @property
    def _clk(self):
        # shouldn't be both divider and fractional divider
        assert not (self.divider and self.frac)

        clk_out = None

        # assume gate is already handled
        if self.mux:
            clk_out = self.mux
        elif self.divider:
            clk_out = self.divider
        elif self.frac:
            clk_out = self.frac
        else:
            # pass through
            assert len(self.parents) == 1
            clk_out = self.parents[0]

        return clk_out.clk

    def __repr__(self):
        return '%s (%d)' % (self.name, self.clk_id)

class FixedClock(Clock):
    def __init__(self, clk_id, name, clk):
        super(FixedClock, self).__init__(clk_id, name)
        self.clk_rate = clk

    @property
    def clk(self):
        print self,
        return self.clk_rate

class PLL(Clock):
    def __init__(self, clk_id, name, fixed_24mhz, fixed_32khz):
        super(PLL, self).__init__(clk_id, name)
        self.fbdiv = None
        self.refdiv = None
        self.fracdiv = None
        self.dsmpd = None

        self.postdiv1 = None
        self.postdiv2 = None

        ## PLL_WORK_MODE
        self.mux = [fixed_24mhz, self, fixed_32khz]
        self.selected_clk_idx = None

    @property
    def fref(self):
        assert len(self.parents) == 1
        return self.parents[0].clk

    @property
    def foutvco(self):
        assert self.refdiv in xrange(1, 63 + 1)
        assert self.dsmpd in (0, 1)
        """Fractional PLL non-divided output frequency"""

        if self.dsmpd == 1:
            # DSM is disabled, "integer mode"
            assert self.fbdiv in xrange(16, 3200 + 1)
            return self.fref / self.refdiv * self.fbdiv
        else:
            # DSM is enabled, "fractional mode"
            assert self.fbdiv in xrange(20, 320 + 1)
            return self.fref / self.refdiv * (self.fbdiv + self.frac / 2**24)

    @property
    def foutpostdiv(self):
        """Fractional PLL divided output frequency (output of second post divider)"""
        return self.foutvco / self.postdiv1 / self.postdiv2

    @property
    def clk(self):
        if self.selected_clk_idx is None:
            print self, 'has invalid PLL_WORK_MODE'

        assert self.selected_clk_idx is not None
        clk_src = self.mux[self.selected_clk_idx]
        if clk_src == self:
            return self._clk

        return clk_src.clk

    def select(self, clkid):
        self.selected_clk_idx = clkid

    @property
    def _clk(self):
        return self.foutpostdiv

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