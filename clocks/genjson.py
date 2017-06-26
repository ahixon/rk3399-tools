import json
import re

clocks = []
HEADINGS = ('module')

def dash_to_none (v):
	if v == '-':
		return None

	return v

for fname in ['data/pmu.txt', 'data/cru.txt', 'data/ipgating.txt']:
	with open(fname, 'r') as f:
		for line in f:
			vals = map (dash_to_none, re.split (r'\s+', line.strip()))

			module = vals[0]
			if not module:
				continue

			clock_id = int(vals[1])
			clkname= vals[2]

			num_parents = 8
			parents = []

			if clkname == 'clk_test':
				num_parents = 16

			for parent_str in vals[3:3 + num_parents]:
				if parent_str is not None:
					parents.append (int(parent_str)) 

			clockdict = dict (zip (('mux', 'gate', 'div', 'frac'), vals[3 + num_parents:]))
			clockdict['module'] = module
			clockdict['id'] = clock_id
			clockdict['clkname'] = clkname
			clockdict['parents'] = parents

			assert 'frac' in clockdict

			clocks.append (clockdict)

with open('data/clocks.json', 'w') as f:
	json.dump (clocks, f)