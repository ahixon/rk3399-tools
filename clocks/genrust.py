import json
import re
import sys

class RegisterAccess(object):
    def __init__(self, name, op=None, prop=None, bits=None, wmask=False):
        self.name = name
        self.bitwise_op = op
        self.prop = prop
        self.bits = bits

        # if writing to the low 16 bits requires a write-mask to the upper
        # 16 bits to allow writes to take effect
        self.wmask = wmask

        # if writemask, then none of the data bits should be in the high 16 bits
        if wmask:
            if isinstance(self.bits, int):
                assert self.bits < 16
            else:
                assert self.bits[0] < 16 and self.bits[1] < 16

        self.from_obj = None
        self.to_obj = None

    def __repr__(self):
        if not self.bits:
            return self.name

        if isinstance(self.bits, int):
            return '%s[%d]' % (self.name, self.bits)
        else:
            return '%s[%d:%d]' % (self.name, self.bits[0], self.bits[1])

    def rust_expr(self):
        rust_peripheral = reg_to_rust_peripheral(self.name)
        rust_field_name = self.name.lower()

        rust_reg_access = '%s.%s.read().bits()' % (rust_peripheral, rust_field_name)

        if isinstance(self.bits, int):
            # single bit
            rust_reg_value = '(%s >> %d) & 1' % (rust_reg_access, self.bits)
        elif self.bits == None:
            # whole register
            rust_reg_value = rust_reg_access
        else:
            # bit range, ordered (msb, lsb) tuple
            bitrange = self.bits[0] - self.bits[1]
            if bitrange <= 0:
                print self.bits
                print bitrange
                assert False

            rust_reg_value = '(%s >> %d) & ((1 << %d) - 1)' % (
                rust_reg_access,
                self.bits[1],
                bitrange)

        bitwise_op = self.bitwise_op
        if bitwise_op == '~':
            # rust uses ! for bitwise not
            bitwise_op = '!'

        return '%s%s' % (bitwise_op or '', rust_reg_value)

# remove these before doing name lookup
REGPREFIX_PROP = {
    'GF_': 'glitch free',
    'ICG_': 'ICG', # ???
}

REGPREFIX_PROP_REGEX = '(%s)' % '|'.join(REGPREFIX_PROP.keys())

# these are the names listed in the tables in the TRM
REGPREFIX_NAME = {
    'PS': 'PMUCRU_CLKSEL_CON',
    'PG': 'PMUCRU_GATE_CON',

    'S': 'CRU_CLKSEL_CON',
    'G': 'CRU_GATE_CON',
    'M': 'CRU_MISC_CON',

    'GRF': 'GRF',
    'PMUGRF': 'PMUGRF'
}

# these are the names that end up being used in the SVD
# so, these are the ones we map to when generating code
REGPREFIX_NAME_SVD = {
    'PS': 'PMUCRU_CLKSEL_CON',
    'PG': 'PMUCRU_CLKGATE_CON',

    'S': 'CRU_CLKSEL_CON',
    'G': 'CRU_CLKGATE_CON',
    'M': 'CRU_MISC_CON',

    'GRF': 'GRF_SOC_CON',
    'PMUGRF': 'PMUGRF_SOC_CON'
}

REGPREFIX_NAME_FIXED = 'F'

def extract_fullname_regs(shortname):
    m = re.match(r'([A-Z_]+)([0-9]*)(\[([0-9]+)(:([0-9]+))?\])?', shortname)
    if not m:
        raise ValueError('bad shorthand reg accessor format', shortname)

    # TODO: rename; this is actually a string
    # might be '' for some register accesses
    regnum = m.group(2)

    if m.group(1) == REGPREFIX_NAME_FIXED:
        return (None, None)

    # fullreg = REGPREFIX_NAME[m.group(1)] + regnum
    fullreg = REGPREFIX_NAME_SVD[m.group(1)] + regnum

    # handle some register renaming
    if REGPREFIX_NAME_SVD[m.group(1)] == 'PMUCRU_CLKSEL_CON':
        regnum_as_int = int(regnum)
        if regnum_as_int >= 6 and regnum_as_int <= 7:
            fullreg = 'PMUCRU_CLKFRAC_CON%d' % (regnum_as_int - 6)

    # might have bits
    bits = None
    if m.group(4):
        bits = int(m.group(4))
        if m.group(6):
            # actually a range
            bits = [bits, int(m.group(6))]

    # print shortname, 'becomes', fullreg, 'bits', bits
    return (fullreg, bits)

def extract_registers(tabletxt):
    regs = []
    regtxts = tabletxt.split('|')
    for regtxt in regtxts:
        # just does clock inversion; not actually a register
        if regtxt == 'INVERT':
            continue

        reg_shortname = None
        reg_prop = None
        reg_op = None

        propmatch = re.match('^%s(.*)' % REGPREFIX_PROP_REGEX, regtxt)
        if propmatch:
            (reg_prop, reg_shortname) = propmatch.groups()
        else:
            reg_shortname = regtxt

        if reg_shortname.startswith('~'):
            reg_op = '~'
            reg_shortname = reg_shortname[1:]

        # okay, now we need to get the full reg name
        # and the bits it accesses if any
        (reg_fullname, reg_bits) = extract_fullname_regs(reg_shortname)
        if reg_fullname:
            regs.append (RegisterAccess(reg_fullname, op=reg_op, prop=reg_prop, bits=reg_bits))

    return regs

RUST_PERIPHERALS = ['cru', 'pmucru', 'grf', 'pmugrf']

def reg_to_rust_peripheral(regname):
    regname = regname.lower()
    per = regname.split('_')[0]
    if per in RUST_PERIPHERALS:
        return per

    # maybe like grf5
    m = re.match(r'([a-z]+)\d+.*', regname)
    if m:
        regname = m.group(1)
        return reg_to_rust_peripheral(regname)

    print 'unknown peripheral', regname
    assert False

def main():
    clock_registers_by_id = {}
    clock_registers_by_name = {}

    with open('data/clocks.json', 'r') as f:
        clocks = json.load(f)
        for clock in clocks:
            regs = {}
            # print clock
            for accessor in ('mux', 'gate', 'div', 'frac'):
                if clock[accessor]:
                    regs[accessor] = extract_registers(clock[accessor])

            clock_registers_by_id[clock['id']] = regs
            clock_registers_by_name[clock['clkname']] = regs

    print 'extern crate rk3399_ap;'
    print

    print 'pub fn print_clocks() {'

    for peripheral in RUST_PERIPHERALS:
        print '    let %s = unsafe { &*rk3399_ap::%s.get() };' % (
            peripheral, peripheral.upper())

    for clkname in clock_registers_by_name:
        if not clock_registers_by_name[clkname]:
            continue

        clk = clock_registers_by_name[clkname]

        has_any_accesses = all([len(clk[t]) > 0 for t in clk])
        if not has_any_accesses and len(clk) == 1:
            continue

        # print the info
        print '    println!("%s: ");' % clkname

        # print '    unsafe {'
        for t in clk:
            print '    print!("\t%s: ");' % t

            accesses = clk[t]
            if not accesses:
                continue

            # for now, we just calculate and print the current state
            # of a clock, if it's dependent on more than one value
            #
            # HOWEVER, when *setting* it later, we need to ensure
            # we set both (or alternatively, keep in mind the
            # value of the registers we are not setting).
            #
            # example of one is `clk_mac_ref`


            rust_accesses = []

            # FIXME: in future, would be cool to map access bit number
            # to register field bit name
            # eg:
            # in clock table, we have:
            # pclk_uphy1_tcphy_g -> gate on G21[8]
            #
            # we can easily deduce that's CRU_CLKGATE_CON21
            # but what's harder is to know which bit access
            # name it maps to in the register detail description
            # in the TRM
            #
            # in Rust, we could access it with:
            #  cru.cru_clkgate_con21.read().uphy1_pclk_tcphy_gate_en()
            #
            # note that the docs do note the bit number as well,
            # so we could in theory look that up, or more likely,
            # load the SVD in directly and use that to do the mapping
            # to a name
            for access in accesses:
                rust_peripheral = reg_to_rust_peripheral(access.name)
                rust_field_name = access.name.lower()

                # # GRF actually means GRF SOC CON
                # if rust_field_name.startswith('grf'):
                #     rust_field_name.replace('grf', 'grf_soc_con')

                rust_reg_access = '%s.%s.read().bits()' % (rust_peripheral, rust_field_name)

                if isinstance(access.bits, int):
                    # single bit
                    rust_reg_value = '(%s >> %d) & 1' % (rust_reg_access, access.bits)
                elif access.bits == None:
                    # whole register
                    rust_reg_value = rust_reg_access
                else:
                    # bit range, ordered (msb, lsb) tuple
                    bitrange = access.bits[0] - access.bits[1]
                    if bitrange <= 0:
                        print access.bits
                        print bitrange
                        assert False

                    rust_reg_value = '(%s >> %d) & ((1 << %d) - 1)' % (
                        rust_reg_access,
                        access.bits[1],
                        bitrange)

                bitwise_op = access.bitwise_op
                if bitwise_op == '~':
                    # rust uses ! for bitwise not
                    bitwise_op = '!'

                if not rust_accesses:
                    rust_accesses.append ('clkval = %s%s;' % (bitwise_op or '', rust_reg_value))
                else:
                    rust_accesses.append ('clkval = clkval | %s%s;' % (bitwise_op or '', rust_reg_value))

            if len(rust_accesses) > 1:
                maybe_mut = 'mut '
            else:
                maybe_mut = ''

            rust_access = """    {
            let %sclkval;
            %s
            println!("0x{:x}", clkval);
        }""" % (maybe_mut, '\n            '.join (rust_accesses))

            print rust_access

        #print '    }'
    print '}'

if __name__ == '__main__':
    main()