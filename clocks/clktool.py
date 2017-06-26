from genrust import extract_registers, REGPREFIX_NAME_FIXED
import json
from clocks import *
import re

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

            clock = FixedClock(clock_info['id'], clk_name, clock_info['module'], clk_rate)
            self.load_clock(clock, clock_info)

        # load all non-IO clocks now
        for clock_info in clocks:
            if clock_info['module'] == 'IO_CLK':
                continue

            if clock_info['module'] == 'PLL':
                clock = PLL(clock_info['id'], clock_info['clkname'],
                    clock_info['module'],
                    self.clocks_by_name[self.FIXED_24MHZ_CLKNAME],
                    self.clocks_by_name[self.FIXED_32KHZ_CLKNAME])
            else:
                clock = Clock(clock_info['id'], clock_info['clkname'],
                    clock_info['module'])

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

    def gen_dump_regs(self, clock, dumpname, reg, level):
        indent = '    '
        print_indent = '\\t' * level
        print_indent_val = '\\t' * (level + 1)

        r = ''

        r += indent + 'print!("%s%s: ");\n' % (print_indent, dumpname)

        if isinstance(reg, list):
            rust_expr = '|'.join (map (lambda x: x.rust_expr(), reg))

            rust_access = 'println!("%s0x{:x}", %s);' % (print_indent_val, rust_expr)
        else:
            rust_access = 'println!("%s0x{:x}", %s);' % (print_indent_val, reg.rust_expr())

        r += indent + rust_access + '\n'

        return r

    def gen_dumper(self, crate="rk3399_tools"):
        r = """extern crate %s;

pub fn print_clocks() {
""" % crate

        for peripheral in self.RUST_PERIPHERALS:
            r += '    let %s = unsafe { &*%s::%s.get() };\n' % (
                peripheral, crate, peripheral.upper())

        r += '\n'

        for clock in self.clocks.values():
            r += '    println!("%s: ");\n' % clock.name

            for reg in clock.register_map:
                r += self.gen_dump_regs(clock, reg, clock.register_map[reg], 1)

            # and children
            for child in clock.register_children:
                r += '    println!("|-%s: ");\n' % child

                child_obj = clock.__dict__[child]
                if isinstance(child_obj, list):
                    # FIXME: handle multiple gates
                    # for now we just take the first one
                    if len(child_obj) > 1:
                        print 'WARNING: only using one gate from', clock

                    child_obj = child_obj[0]
                
                for reg in child_obj.register_map:
                    r += self.gen_dump_regs(clock, reg, child_obj.register_map[reg], 2)

        r += '}\n'

        return r

    def set_val_in_regmap(self, current_clk, regmap_name, val):
        if current_clk.register_map[regmap_name].to_obj:
            current_clk.register_map[regmap_name].to_obj(val)
        else:
            # just direct set on object in class; doesn't have a deserialisation function
            current_clk.__dict__[regmap_name] = val

    def load_dump(self, f):
        current_clk_name = None
        current_clk = None

        current_child = None

        for line in f:
            m = re.match(r'([\w<>]+):', line)
            if m:
                current_clk_name = m.group(1)
                current_clk = self.clocks_by_name[current_clk_name]
                continue
            
            m = re.match(r'\t(\w+):\s*\t\t0x([a-z0-9]+)', line)
            if m:
                # direct clock register
                regmap_name, strval = m.groups()
                val = int(strval, 16)

                self.set_val_in_regmap (current_clk, regmap_name, val)
                continue

            m = re.match(r'\|-(\w+):', line)
            if m:
                # register child
                current_child = current_clk.__dict__[m.group(1)]
                if isinstance(current_child, list):
                    # FIXME: handle non- first child
                    current_child = current_child[0]

                continue

            m = re.match(r'\t\t(\w+):\s*\t\t0x([a-z0-9]+)', line)
            if m:
                # register child's register
                regmap_name, strval = m.groups()
                val = int(strval, 16)

                self.set_val_in_regmap (current_child, regmap_name, val)
                continue

            # unhandled format
            print line
            assert False


def main():
    cm = ClockManager()

    # load clock tree data
    with open('data/clocks.json', 'r') as f:
        cm.load(json.load(f))

    # write dumper
    with open('clocks.rs', 'w') as f:
        f.write(cm.gen_dumper())

    # load clock dump data
    with open('uboot-dump.txt', 'r') as f:
        cm.load_dump(f)

    # print out uart2 clock speed
    print cm.clocks_by_name['clk_uart2'].clk

if __name__ == '__main__':
    main()