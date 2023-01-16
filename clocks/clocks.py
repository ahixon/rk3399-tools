from genrust import RegisterAccess
from json import JSONEncoder, JSONDecoder

class DisconnectedClockException(Exception):
    pass

class Divider(object):
    def __init__(self, reg, owner, value=None):
        super(Divider, self).__init__()

        if reg:
            reg.from_obj = self.reg_value
            reg.to_obj = self.from_reg_value
            self.reg = reg

        self.owner = owner
        self.div = None

        if value:
            self.from_reg_value(value)

    @property
    def register_map(self):
        return {
            'd': self.reg
        }

    @property
    def clk(self):
        # print self,
        if self.owner is None:
            raise DisconnectedClockException()

        clk_src = self.owner.clk_src.clk
        if clk_src is None:
            return None

        return clk_src / (self.div + 1)

    def __repr__(self):
        return '/ %d' % (self.div + 1)

    def reg_value(self):
        return self.div

    def from_reg_value(self, reg):
        self.div = reg

class FixedDivider(Divider):
    def __init__(self, owner, value):
        super(FixedDivider, self).__init__(None, owner, value)

    @property
    def register_map(self):
        return {}

    @property
    def clk(self):
        # print self,
        if self.owner is None:
            raise DisconnectedClockException()

        clk_src = self.owner.clk_src.clk
        if clk_src is None:
            return None

        return clk_src / self.div

    def __repr__(self):
        return '/ %df' % self.div

class Frac(object):
    def __init__(self, reg, owner, value=None):
        super(Frac, self).__init__()

        reg.from_obj = self.reg_value
        reg.to_obj = self.from_reg_value
        self.reg = reg

        self.owner = owner
        self.clk_src = None
        self.numerator = None
        self.denominator = None

        if value:
            self.from_reg_value(value)

    @property
    def register_map(self):
        return {
            'f': self.reg
        }

    @property
    def clk(self):
        # print self,
        if self.owner is None:
            raise DisconnectedClockException()

        clk_src = self.owner.clk_src.clk
        if clk_src is None:
            return None

        return clk_src * self.numerator / self.denominator

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

        reg.from_obj = self.reg_value
        reg.to_obj = self.from_reg_value
        self.reg = reg

        self.clocking_enabled = None

        if value:
            self.from_reg_value(value)

    @property
    def register_map(self):
        return {
            'g': self.reg
        }

    def reg_value(self):
        if self.clocking_enabled:
            return 0

        return 1

    def from_reg_value(self, value):
        # "when HIGH, disable clock"
        # so, when low, enable clock
        self.clocking_enabled = (value == 0)

    def __repr__(self):
        return 'G=%s' % (self.clocking_enabled)

class Mux(object):
    def __init__(self, reg, owner, clocks, value=None):
        super(Mux, self).__init__()

        reg.from_obj = self.reg_value
        reg.to_obj = self.from_reg_value
        self.reg = reg

        self.owner = owner
        
        self.clocks = clocks
        self.selected_clk_idx = None

        self.clk_src = None

        if value:
            self.from_reg_value(value)

    @property
    def register_map(self):
        return {
            'm': self.reg
        }

    def reg_value(self):
        return self.selected_clk_idx

    def from_reg_value(self, value):
        self.selected_clk_idx = value
        self.clk_src = self.clocks[self.selected_clk_idx]

    def select(self, clkid):
        self.from_reg_value(clkid)

    @property
    def clk(self):
        # print self,
        return self.clk_src.clk

    def __repr__(self):
        if self.selected_clk_idx is not None:
            return 'M=%s (%s)' % (self.clocks[self.selected_clk_idx], self.clocks)
        else:
            return 'M=? (%s)' % (self.clocks)

class Clock(object):
    def __init__(self, clk_id, name, module):
        super(Clock, self).__init__()
        self.clk_id = clk_id
        self.name = name
        self.module = module

        self.divider = None
        self.frac = None
        self.gate = None
        self.mux = None

        self.parents = []

    @property
    def register_map(self):
        return {}

    @property
    def register_children(self):
        return [x for x in ('divider', 'frac', 'gate', 'mux') if self.__dict__[x] is not None]
    
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
        # print self,
        if self.gate:
            for g in self.gate:
                if g.clocking_enabled == None:
                    print('warning:', g, 'on', self, 'in undefined state')

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
    def __init__(self, clk_id, name, module, clk):
        super(FixedClock, self).__init__(clk_id, name, module)
        self.clk_rate = clk

    @property
    def clk(self):
        # print self,
        return self.clk_rate

    @property
    def register_children(self):
        return []

    @property
    def register_map(self):
        return {}

class PLL(Clock):
    def __init__(self, clk_id, name, module, fixed_24mhz, fixed_32khz):
        super(PLL, self).__init__(clk_id, name, module)
        self.fbdiv = None
        self.refdiv = None
        self.fracdiv = None
        self.dsmpd = None

        self.postdiv1 = None
        self.postdiv2 = None

        self.pll_work_mode = [fixed_24mhz, self, fixed_32khz]
        self.selected_clk_idx = None
        self.power_down = None
        self.bypass = None

    @property
    def pll_id(self):
        return self.name[0].upper()

    @property
    def register_children(self):
        return []

    @property
    def register_map(self):
        reg_basename = 'CRU'
        if self.pll_id == 'P':
            # the PPLL is presumably for PMU
            # the configuration registers for this PLL are in the PMUCRU block
            reg_basename = 'PMUCRU'

        return {
            'fbdiv':            RegisterAccess("%s_%sPLL_CON0" % (reg_basename, self.pll_id), bits=(11, 0), wmask=True),

            'postdiv2':         RegisterAccess("%s_%sPLL_CON1" % (reg_basename, self.pll_id), bits=(14,12), wmask=True),
            'postdiv1':         RegisterAccess("%s_%sPLL_CON1" % (reg_basename, self.pll_id), bits=(10, 8), wmask=True),
            'refdiv':           RegisterAccess("%s_%sPLL_CON1" % (reg_basename, self.pll_id), bits=( 5, 0), wmask=True),

            'fracdiv':          RegisterAccess("%s_%sPLL_CON2" % (reg_basename, self.pll_id), bits=(23, 0)),

            'selected_clk_idx': RegisterAccess("%s_%sPLL_CON3" % (reg_basename, self.pll_id), bits=( 9, 8), wmask=True),
            'dsmpd':            RegisterAccess("%s_%sPLL_CON3" % (reg_basename, self.pll_id), bits=3, wmask=True),
            'bypass':           RegisterAccess("%s_%sPLL_CON3" % (reg_basename, self.pll_id), bits=1, wmask=True),
            'power_down':       RegisterAccess("%s_%sPLL_CON3" % (reg_basename, self.pll_id), bits=0, wmask=True),
        }

    @property
    def fref(self):
        return self.clk_src

    @property
    def clk_src(self):
        assert len(self.parents) == 1
        return self.parents[0].clk

    @property
    def foutvco(self):
        assert self.refdiv in range(1, 63 + 1)
        assert self.dsmpd in (0, 1)
        """Fractional PLL non-divided output frequency"""

        if self.dsmpd == 1:
            # DSM is disabled, "integer mode"
            assert self.fbdiv in range(16, 3200 + 1)
            return self.fref / self.refdiv * self.fbdiv
        else:
            # DSM is enabled, "fractional mode"
            assert self.fbdiv in range(20, 320 + 1)
            return self.fref / self.refdiv * (self.fbdiv + self.frac / 2**24)

    @property
    def foutpostdiv(self):
        """Fractional PLL divided output frequency (output of second post divider)"""
        return self.foutvco / self.postdiv1 / self.postdiv2

    @property
    def clk(self):
        if self.selected_clk_idx is None:
            print(self, 'has invalid PLL_WORK_MODE')

        assert self.selected_clk_idx is not None
        assert self.bypass is not None
        assert self.power_down is not None

        if self.power_down:
            return None

        # FIXME: does bypass happen before work mode, or after?
        # ie is work_mode just a clock mux feeding to FREF?
        # see also: `_clk` below.
        if self.bypass:
            # FREF bypasses PLL to FOUTPOSTDIV
            return self.fref

        clk_src = self.pll_work_mode[self.selected_clk_idx]
        if clk_src == self:
            return self._clk

        return clk_src.clk

    def select(self, clkid):
        self.selected_clk_idx = clkid

    @property
    def _clk(self):
        if self.bypass:
            # FREF bypasses PLL to FOUTPOSTDIV
            return self.fref

        return self.foutpostdiv