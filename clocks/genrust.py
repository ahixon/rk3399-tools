import json
import re
import sys

class RustRegisterWrite(object):
    def __init__(self, obj_name, field_name, rust_value, access, whole_register=False, bit_name=None):
        self.obj_name = obj_name
        self.field_name = field_name
        self.rust_value = rust_value
        self.whole_register = whole_register
        self.bit_name = bit_name
        self.access = access

    @property
    def accessor(self):
        return '%s.%s' % (self.obj_name, self.field_name)

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

    def rust_writemask_value(self, shift=16):
        if isinstance(self.bits, int):
            # single bit
            rust_reg_value = '1 << %d' % (self.bits + shift)
        elif self.bits == None:
            raise ValueError('no writemask required')
        else:
            # bit range, ordered (msb, lsb) tuple
            # zero indexed
            bitrange = self.bits[0] - self.bits[1]
            if bitrange <= 0:
                print(self.bits)
                print(bitrange)
                assert False

            mask = '((1 << %d) - 1)' % bitrange
            rust_reg_value = '%s << %d' % (mask, self.bits[1] + shift)

        return rust_reg_value

    def rust_write_expr(self, val, name=None):
        #'%s.%s.modify(|_, w| unsafe { w.bits(r.bits() | %s) }'
        # OR
        # '%s.%s.write(|w| unsafe { w.bits(%s) }'
        rust_peripheral = reg_to_rust_peripheral(self.name)
        rust_field_name = self.name.lower()

        if isinstance(self.bits, int):
            # single bit
            rust_reg_value = '(%s & 1) << %d' % (val, self.bits)
        elif self.bits == None:
            # whole register
            rust_reg_value = val
        else:
            # bit range, ordered (msb, lsb) tuple
            # zero indexed
            bitrange = self.bits[0] - self.bits[1]
            if bitrange <= 0:
                print(self.bits)
                print(bitrange)
                assert False

            mask = '((1 << %d) - 1)' % bitrange
            rust_reg_value = '(%d & %s) << %d' % (val, mask, self.bits[1])

        bitwise_op = self.bitwise_op
        if bitwise_op == '~':
            # rust uses ! for bitwise not
            bitwise_op = '!'

        if bitwise_op:
            rust_reg_value = bitwise_op + rust_reg_value

        return RustRegisterWrite(
            rust_peripheral, rust_field_name,
            rust_reg_value, self, whole_register=self.bits == None,
            bit_name=name)

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
                print(self.bits)
                print(bitrange)
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

REGPREFIX_PROP_REGEX = '(%s)' % '|'.join(list(REGPREFIX_PROP.keys()))

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

    print('unknown peripheral', regname)
    assert False
