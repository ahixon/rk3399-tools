import lxml.etree as ET
import re
import sys
import codecs
# sys.stdout = codecs.getwriter('utf8')(sys.stdout)

class Interrupt(object):
	def __init__ (self):
		self.type = None
		self.id = None
		self.source = None
		self.trig = None

class BitAccess(object):
	def __init__(self):
		self.bit_range = (0, 0)
		self.access_policy = None
		self.reset_value = None
		self.description = None
		self.volatile = None

	def __repr__ (self):
		return '%r (%s)' % (self.bit_range, self.description.split('\n')[0])

class Register(object):
	def __init__(self, name):
		super(Register, self).__init__()
		self.name = name
		self.bits = []
		self.address_offsets = []
		self.description = None

		# set in svd script
		self.size = None
		self.reset_value = None

	def __repr__ (self):
		return self.name

class RegisterSummary(object):
	def __init__(self, name):
		self.name = name
		self.offset_range = None
		self.size = None
		self.reset_value = None
		self.description = None

# pdftohtml -s -i -noframes -xml Rockchip\ RK3399TRM\ V1.3\ Part1.pdf part1.xml
class Parser (object):
	def __init__ (self):
		self.registers = []
		self.register_summaries = []

	def parse (self, filelike, interruptsrc):
		current_register = None
		current_register_summary = None
		sum_desc_col = 0
		sum_name_col = 0
		summary_range = None
		parsing = None

		pagenum = 0
		iterparse = ET.iterparse(filelike)
		consume_stack = []

		current_bits = None
		desc_col = None
		partial_access_policy = ''

		skip = 0

		for event, elem in iterparse:
			# wtf next() is supposed to update the interator state
			# why do i have to do this... :(
			if skip:
				skip -= 1
				continue
				
			if elem.tag == 'text':
				# ignore header and footer since they're always same
				if elem.attrib['font'] == '0':
					continue

				if elem.attrib['font'] == '1':
					# grab pagenum
					footer = elem.find ('i').text
					m = re.match ('Copyright \d{4} @ .* (\d+)', footer)
					if m:
						pagenum = int (m.group (1))
					else:
						if footer.startswith ('Notes:'):
							# hit the notes at the bottom of table
							# so table must be done; clear it to stop
							consume_stack = []

					continue

				if elem.attrib['font'] == '14':
					header_text = elem.find('b').text.strip()
					# print 'ht', header_text
					if 'Detail Register Description' in header_text:
						consume_stack = []
						parsing = 'detail-reg'
					elif 'Register Summary' in header_text or 'Registers Summary' in header_text:
						consume_stack = ['heading-n', 'heading-osr', 'heading-d', 'sumreg']
						parsing = 'summary-reg'
						continue
					else:
						# try without heading bit
						header_text = elem.find('b').tail
						if header_text:
							# print 'ht-nobold', header_text
							if 'Detail Register Description' in header_text:
								consume_stack = []
								parsing = 'detail-reg'
							elif 'Register Summary' in header_text or 'Registers Summary' in header_text:
								consume_stack = ['heading-n', 'heading-osr', 'heading-d', 'sumreg']
								parsing = 'summary-reg'
								continue
							else:
								parsing = None
						else:
							# print '\tno non-bold text either'
							parsing = None
							continue

				if elem.attrib['font'] == '5':
					header_text = elem.find('b').text.strip()
					if 'register' in header_text:
						# print "PI REG GO"
						consume_stack = ['heading-n', 'heading-osr', 'heading-d', 'sumreg']
						continue

				if parsing == 'summary-reg':
					if consume_stack:
						consume = consume_stack.pop (0)
						# print 'doing', consume
					else:
						consume = None

					if consume == 'heading-n':
						if elem.attrib['font'] == '3':
							# print '\tskipping size 3 font...'
							# print '\t', elem.text
							if elem.find('b') is None:
								consume_stack = ['heading-n'] + consume_stack
								continue
							else:
								assert False

						if elem.attrib['font'] != '5':
							print "WARNING: skipping some register summary table"
							print "because it doesn't match our format (font was %s)" % elem.attrib['font']
							consume_stack = []
							continue

						# assert elem.attrib['font'] == '5'
						if elem.find('b').text.strip() != 'Name':
							print 'WARNING: found table not in right format'
							consume_stack = []
							continue

						assert len(elem.findall('b')) == 1
					elif consume == 'heading-osr':
						# print elem.findall('b')
						if len(elem.findall('b')) == 1:
							headings = re.sub('\s+', ' ', elem.find('b').text).strip().split(' ')
							if len(headings) == 2:
								consume_stack = ['heading-r'] + consume_stack
							elif len(headings) == 1:
								consume_stack = ['heading-sr'] + consume_stack
							else:
								assert len(headings) == 3

						elif len(elem.findall('b')) == 2:
							consume_stack = ['heading-r'] + consume_stack
						else:
							assert len(elem.findall('b')) == 3
					elif consume == 'heading-sr':
						if len(elem.findall('b')) == 1:
							headings = map (str.strip, elem.find ('b').text.split(' ', 1))
							assert headings == ['Size', 'Reset Value']
						else:
							assert len(elem.findall('b')) == 2
							assert elem.find('b').text.strip() == 'Size'
					elif consume == 'heading-r':
						assert elem.find('b').text.strip() == 'Reset'
						assert len(elem.findall('b')) == 1
					elif consume == 'heading-d':
						if elem.find('b').text.strip() == 'Value':
							# eugh, from previous text node; do us again
							consume_stack = ['heading-d'] + consume_stack
						else:
							assert elem.find('b').text.strip() == 'Description'
							assert len(elem.findall('b')) == 1
					elif consume == 'sumreg' or (consume == 'sumreg-d' and elem.attrib['left'] == sum_name_col):
						# guard
						headings = map (lambda x: x.text.strip(), elem.findall ('b'))
						if len(headings) == 1 and 'Name' in headings[0]:
							consume_stack = ['heading-osr', 'heading-d', 'sumreg']
							continue

						if sum_desc_col and elem.attrib['left'] == sum_desc_col:
							# print '\tadded to description:', elem.text.strip()
							current_register_summary.description += ' ' + elem.text.strip()
							consume_stack = ['sumreg']
						else:
							if not elem.text:
								# no more registers
								consume_stack = []
								continue

							vals = re.sub ('\s+', ' ', elem.text.strip()).split(' ', 4)
							
							regname_arr = []
							this_offset = None
							this_size = None
							this_reset = None
							this_desc = None

							# print '\t', vals

							for v in vals:
								if v.startswith ('0x') and this_offset is None:
									this_offset = int(v, 16)
								else:
									if this_offset is not None:
										# do this only after we have offset
										if this_size is None:
											this_size = v
										elif this_reset is None:
											this_reset = v
										else:
											this_desc = v
									else:
										# still name
										regname_arr.append (v)

							# no name found, screw it
							if not regname_arr[0]:
								continue

							regname_str = ' '.join (regname_arr)

							if not re.match (r'[0-9A-Z_n]+[0-9A-Z_n]*[0-9A-Z_]*', regname_str):
								current_register_summary.description += regname_str
								consume_stack = ['sumreg']
								continue
							elif ' ' in regname_str:
								# for now, disallow spaces in register
								print '\tWARNING: had space, skipping'
								#current_register_summary.description += regname_str
								consume_stack = ['sumreg']
								continue

							r = RegisterSummary (regname_str)
							current_register_summary = r

							if this_offset is not None:
								r.offset_range = (this_offset, this_offset)

							if this_size:
								assert this_size in ['W', 'HW', 'DW', 'B']
								r.size = this_size

							if this_reset:
								# print this_reset
								assert this_reset[:2] == '0x'
								r.reset_value = int(this_reset[2:], 16)

							if this_desc:
								r.description = this_desc.strip()

							# print '\tnew register', regname_str
							# if regname_str == 'PMUCRU_PPLL_CON1':
							# 	sys.exit (1)

							sum_name_col = elem.attrib['left']
							sum_desc_col = None
							self.register_summaries.append (r)

							if this_offset is None:
								consume_stack = ['sumreg-osrd']
							elif this_size is None:
								consume_stack = ['sumreg-srd']
							elif this_reset is None:
								consume_stack = ['sumreg-rd']
							elif this_desc is None:
								consume_stack = ['sumreg-d']
							else:
								consume_stack = ['sumreg']
								
					elif consume == 'sumreg-osrd':
						# guard
						headings = map (lambda x: x.text.strip(), elem.findall ('b'))
						if len(headings) == 1 and 'Name' in headings[0]:
							assert False
							consume_stack = ['sumreg-n', 'sumreg-osr', 'sumreg-d'] + consume_stack
							continue

						if elem.attrib['left'] == sum_name_col:
							# name clipped; go again
							# print '\tname clipped, adding', elem.text.strip()
							current_register_summary.name += elem.text.strip()
							consume_stack = ['sumreg-osrd']
							continue

						elem.text = re.sub ('\s+', ' ', elem.text)
						vals = map(str.strip, elem.text.strip().split(' ', 3))

						# print '\t', vals
						assert vals[0][:2] == '0x'
						if vals[0].endswith ('~'):
							# print '\trange'
							summary_range = vals[0][2:-1]
							assert len(vals) == 1 # rely 100% on next step to do this
							consume_stack = ['sumreg-range', 'sumreg-srd']
							continue
						else:
							summary_range = None
							current_register_summary.offset_range = (int(vals[0][2:], 16), int(vals[0][2:], 16))

						if len(vals) == 1:
							consume_stack = ['sumreg-srd']
						else:
							vals.pop(0)

							assert vals[0] in ['W', 'HW', 'DW', 'B']
							current_register_summary.size = vals[0]

							vals.pop(0)

							if vals:
								# had reset value
								assert vals[0].startswith ('0x')
								current_register_summary.reset_value = int(vals[0][2:], 16)
								vals.pop (0)

								if vals:
									# also had desc!
									current_register_summary.description = vals[0]
									consume_stack = ['sumreg']
								else:
									consume_stack = ['sumreg-d']
							else:
								consume_stack = ['sumreg-rd']
					elif consume == 'sumreg-range':
						# sometimes split over 2 lines; called by sumreg-osrd
						elem.text = re.sub ('\s+', ' ', elem.text)
						vals = map(str.strip, elem.text.strip().split(' '))
						assert len(vals) == 1

						assert vals[0][:2] == '0x'
						current_register_summary.offset_range = (int(summary_range, 16), int(vals[0][2:], 16))


					elif consume == 'sumreg-srd':
						# guard
						headings = map (lambda x: x.text.strip(), elem.findall ('b'))
						if len(headings) == 1 and 'Name' in headings[0]:
							assert False
							consume_stack = ['sumreg-n', 'sumreg-osr', 'sumreg-d'] + consume_stack
							continue

						elem.text = re.sub ('\s+', ' ', elem.text)
						vals = map(str.strip, elem.text.strip().split(' ', 2))
						# print '\tsrd', vals

						if vals[0].startswith ('~0x'):
							# range from last field
							current_register_summary.offset_range = (
								current_register_summary.offset_range[0],
								int (vals[0][3:], 16))

							# try again
							consume_stack = ['sumreg-srd'] + consume_stack
							continue
						
						assert vals[0] in ['W', 'HW', 'DW', 'B']
						current_register_summary.size = vals[0]

						vals.pop(0)

						if vals:
							# have reset value
							assert vals[0][:2] == '0x'
							current_register_summary.reset_value = int(vals[0][2:], 16)

							vals.pop(0)

							if vals:
								# have desc!
								current_register_summary.description = vals[0]
								consume_stack = ['sumreg']
							else:
								consume_stack = ['sumreg-d']	
						else:
							consume_stack = ['sumreg-rd']
					elif consume == 'sumreg-rd':
						# guard
						headings = map (lambda x: x.text.strip(), elem.findall ('b'))
						if len(headings) == 1 and 'Name' in headings[0]:
							assert False
							consume_stack = ['sumreg-n', 'sumreg-osr', 'sumreg-d'] + consume_stack
							continue

						elem.text = re.sub ('\s+', ' ', elem.text)
						vals = map(str.strip, elem.text.strip().split(' ', 1))
						# print '\t', vals
						if vals[0]:
							# the HDMI fields are blank?
							assert vals[0][:2] == '0x'
							current_register_summary.reset_value = int(vals[0][2:], 16)

						vals.pop(0)

						if vals:
							# have desc
							current_register_summary.description = vals[0]
							consume_stack = ['sumreg']
						else:
							consume_stack = ['sumreg-d']
					elif consume == 'sumreg-d':
						# guard
						headings = map (lambda x: x.text.strip(), elem.findall ('b'))
						if len(headings) == 1 and 'Name' in headings[0]:
							assert False
							consume_stack = ['sumreg-n', 'sumreg-osr', 'sumreg-d'] + consume_stack
							continue

						if elem.find('b') is not None:
							# print 'UHMMM', elem.find('b').text
							assert False

						# print '\tadded desc', elem.text.strip()
						current_register_summary.description = elem.text.strip()
						sum_desc_col = elem.attrib['left']
						consume_stack = ['sumreg']


				if parsing == 'detail-reg':
					if elem.attrib['font'] == '5' and consume_stack == []:
						# detailed register summary header
						current_register = None

						regname = elem.find('b').text.strip()
						if regname == 'Bit':
							# carry over from previous page
							# should just be BAR for this text element
							headings = map (lambda x: x.text.strip(), elem.findall ('b'))
							if len(headings) == 1:
								consume_stack = ['ar', 'd']
							elif len(headings) == 2:
								consume_stack = ['r', 'd']
							elif len(headings) == 3:
								consume_stack = ['d']
							else:
								assert False

							continue
						elif not regname:
							continue

						if regname.upper() != regname or ' ' in regname:
							# not a Real Boy; registers have full caps names
							continue

						current_register = Register (regname)
						self.registers.append (current_register)
						# print 'new header at', regname, 'page', pagenum
						# if regname == 'CRU_SOFTRST_CON1':
						# 	sys.exit (1)

						regcount = 1
						if '~' in regname:
							fromreg, toreg = regname.split('~')
							fromregnum = int(fromreg.strip()[-1])
							toregnum = int(toreg.strip()[-1])

							regcount = toregnum - fromregnum + 1
							# print "\thave", regcount, "registers"

						consume_stack = ['addr'] * regcount
						consume_stack += ['desc', 'bar', 'd', 'bits']
						continue
					
					if len(consume_stack):
						consume = consume_stack.pop(0)
						# print 'consuming', consume
					else:
						consume = None

					if consume == 'addr':
						if elem.text is None or not elem.text.strip() or 'Operational Base' not in elem.text:
							print 'elem text is', elem.text
							print 'on page', pagenum
							print
							print 'WARNING: assuming what follows is not register set'
							consume_stack = []
							current_register = None
							parsing = None
							continue

						m = re.search (r'Operational Base \+ offset \(0x([A-Fa-f0-9]+)\)', elem.text)
						if not m:
							m = re.search (r'Operational Base\+0x([A-Fa-f0-9]+)', elem.text)
							if not m:
								# maybe range
								m = re.search (r'Operational Base \+ offset \(0x([A-Fa-f0-9]+)~0x([A-Fa-f0-9]+)\)', elem.text)
								if not m:
									print "elem text is", elem.text
									assert m

								# FIXME: currently assumes access via word (I haven't seen an exception yet but might be wrong)
								for i in xrange (int(m.group(1), 16), int(m.group(2), 16) + 4, 4):
									# print hex(i)
									current_register.address_offsets.append (i)

								continue

						current_register.address_offsets.append (int(m.group(1), 16))
					elif consume == 'desc':
						assert current_register.description is None
						current_register.description = elem.text.strip()
					elif consume == 'bar':
						# skip bit attr reset value description fields
						headings = map (lambda x: x.text.strip(), elem.findall ('b'))

						if headings == ['Bit', 'Attr', 'Reset Value']:
							pass
						elif headings == ['Bit']:
							consume_stack = ['ar'] + consume_stack
						elif headings == ['Bit', 'Attr']:
							consume_stack = ['r'] + consume_stack
						else:
							# wtf
							if headings:
								maybe_headings = map(str.strip, re.sub('\s+', ' ', headings[0]).split(' ', 2))
								if maybe_headings == ['Bit', 'Attr', 'Reset Value']:
									pass
								elif maybe_headings == ['Bit']:
									consume_stack = ['ar'] + consume_stack
								elif maybe_headings == ['Bit', 'Attr']:
									consume_stack = ['r'] + consume_stack
								else:
									print 'uh oh', headings
									print elem.text
									assert False
							else:
								# do again, wasn't a heading, but a description continuation
								current_register.description += '\n' + elem.text
								consume_stack = ['bar'] + consume_stack
					elif consume == 'ar':
						headings = map (lambda x: x.text.strip(), elem.findall ('b'))
						if headings == ['Attr', 'Reset Value']:
							pass
						elif headings == ['Reset Value']:
							consume_stack = ['r'] + consume_stack
						else:
							print 'uh oh', headings
							assert False
					elif consume == 'r':
						headings = map (lambda x: x.text.strip(), elem.findall ('b'))
						if headings == ['Reset Value']:
							pass
						else:
							print 'uh oh', headings
							assert False
					elif consume == 'd':
						assert elem.find('b').text.strip() == 'Description'
					elif consume == 'bits' and current_register:
						# guard
						headings = map (lambda x: x.text.strip(), elem.findall ('b'))
						if len(headings) == 1 and 'Bit' in headings[0]:
							consume_stack = ['ar', 'd', 'bits']
							continue
						elif len(headings) == 2 and 'Bit' in headings[0]:
							consume_stack = ['r', 'd', 'bits']
							continue
						elif len(headings) == 3 and 'Bit' in headings[0]:
							consume_stack = ['d', 'bits']
							continue

						if elem.attrib['font'] != '3':
							continue

						if elem.attrib['left'] == desc_col:
							# actually just an extension of description
							# print '\tcontinued desc', elem.text
							current_bits.description += '\n' + elem.text.strip()
							consume_stack = ['bits']
						else:
							elem.text = re.sub ('\s+', ' ', elem.text)
							v = elem.text.strip().split(' ')

							if not elem.text.strip():
								continue

							current_bits = BitAccess()
							partial_access_policy = ''
							desc_col = None
							bitrange = v.pop (0).strip()
							# try:
							if True:
								if ':' in bitrange:
									frombit, tobit = bitrange.split(':')
									current_bits.bit_range = (int(frombit), int(tobit))
								else:
									current_bits.bit_range = (int(bitrange), int(bitrange))
							# except ValueError:
							# 	continue

							# and add it ;)
							# print 'new bitaccess on', elem.text
							current_register.bits.append (current_bits)

							if v:
								# also have attr
								attr = v.pop(0).strip()
								if attr == 'R/W':
									attr = 'RW'
								elif attr == 'RU':
									attr = 'RO'
									current_bits.volatile = True
								elif attr == 'RC':
									attr = 'RC'

								# print attr
								assert attr in ['RW', 'RO', 'WO', 'W1C', 'RC']
								current_bits.access_policy = attr

								if v:
									# also have resetval
									resetval = v.pop(0).strip()
									assert resetval[:2] == '0x'
									current_bits.reset_value = int(resetval[2:], 16)

									if v:
										# ALSO! have description
										# unlikely... haha
										current_bits.description = ' '.join (v).strip()
										desc_col = None # but we don't know desccol
										consume_stack = ['bits']
									else:
										consume_stack = ['bits-desc']
								else:
									consume_stack = ['bits-rd']
							else:
								# need attr, resetval, desc
								# print '\tneed ard'
								consume_stack = ['bits-ard']
					elif consume == 'bits-ard':
						# guard
						headings = map (lambda x: x.text.strip(), elem.findall ('b'))
						if len(headings) == 1 and 'Bit' in headings[0]:
							consume_stack = ['ar', 'd', 'bits']
							continue
						elif len(headings) == 2 and 'Bit' in headings[0]:
							consume_stack = ['r', 'd', 'bits']
							continue
						elif len(headings) == 3 and 'Bit' in headings[0]:
							consume_stack = ['d', 'bits']
							continue

						elem.text = re.sub ('\s+', ' ', elem.text)
						v = elem.text.strip().split(' ')
						attr = partial_access_policy + v.pop(0).strip()

						if attr == 'R/W':
							attr = 'RW'
						elif attr == 'W1':
							# need to know what next bit is
							partial_access_policy = 'W1'
							if v:
								attr = partial_access_policy + v.pop(0).strip()
							else:
								consume_stack = ['bits-ard']
								continue

						# print attr
						assert attr in ['RW', 'RO', 'WO', 'W1C', 'RC']
						current_bits.access_policy = attr

						if v:
							# also have resetval
							resetval = v.pop(0).strip()
							assert resetval[:2] == '0x'
							current_bits.reset_value = int(resetval[2:], 16)

							if v:
								# ALSO! have description
								# unlikely... haha
								current_bits.description = ' '.join (v).strip()
								desc_col = None # but we don't know desccol
								consume_stack = ['bits']
							else:
								consume_stack = ['bits-desc']
						else:
							# print '\tneed rd'
							consume_stack = ['bits-rd']
					elif consume == 'bits-rd':
						# guard
						headings = map (lambda x: x.text.strip(), elem.findall ('b'))
						if len(headings) == 1 and 'Bit' in headings[0]:
							consume_stack = ['ar', 'd', 'bits']
							continue
						elif len(headings) == 2 and 'Bit' in headings[0]:
							consume_stack = ['r', 'd', 'bits']
							continue
						elif len(headings) == 3 and 'Bit' in headings[0]:
							consume_stack = ['d', 'bits']
							continue

						# also have resetval
						elem.text = re.sub ('\s+', ' ', elem.text)
						v = elem.text.strip().split(' ')
						resetval = v.pop(0).strip()

						if resetval == 'SC':
							assert current_bits.access_policy == 'RW'
							current_bits.access_policy = 'WC'

							# sigh; do it again
							if v:
								resetval = v.pop(0).strip()
							else:
								consume_stack = ['bits-rd']
								continue


						assert resetval[:2] == '0x'
						current_bits.reset_value = int(resetval[2:], 16)

						if v:
							# ALSO! have description
							# unlikely... haha
							current_bits.description = ' '.join (v).strip()
							desc_col = None # but we don't know desccol
							consume_stack = ['bits']
						else:
							consume_stack = ['bits-desc']
					elif consume == 'bits-desc' or elem.attrib['left'] == desc_col:
						desc_col = elem.attrib['left']
						if current_bits.description:
							current_bits.description += '\n' + elem.text.strip()
						else:
							current_bits.description = elem.text.strip()

						# print '\thad desc', elem.text
						consume_stack = ['bits']

	def check (self):
		# now we have summaries, get the width for each register
		widths = {}
		size_to_bits = {
			'W': 32,
			'HW': 16,
			'B': 8,
			'DW': 64
		}
		for regsum in self.register_summaries:
			# rename register to match detail register name
			if regsum.name == 'EFUSE_JTAG_PASS':
				regsum.name = 'EFUSE_JTAG_PASSWD'

			try:
				widths[regsum.name] = size_to_bits[regsum.size]
			except KeyError:
				print 'no size for', regsum.name, 'with', regsum.size

				# assume 32-bit?
				raise

		# sanity check the register bit accesses
		for reg in self.registers:
			# print reg.name

			# starts top to bottom
			# avoid typos and range registers
			if '~' in reg.name:
				continue

			# rename
			if reg.name == 'EFUSE_STROBE_FINISH_CON':
				reg.name = 'EFUSE_STROBE_FINISH_CTRL'

			if not reg.bits:
				print "WARNING: no bits for", reg
				continue

			if reg.name == 'UART_MCR':
				# seems to be missing
				b = BitAccess ()
				b.description = """req_to_send
Request to Send.
This is used to directly control the Request to Send (rts_n)
output. The Request To Send (rts_n) output is used to inform the
modem or data set that the UART is ready to exchange data."""
				b.access_policy = 'RW'
				b.reset_value = 0
				b.bit_range = (1, 1)

				reg.bits.insert (-1, b)

			last_bit = widths[reg.name]
			for ba in reg.bits:
				# print '\t', ba.description.split('\n')[0]

				if ba.bit_range[0] != (last_bit - 1):
					print ba, 'started in wrong place; expected', (last_bit - 1)
					assert False

				last_bit = ba.bit_range[1]
				assert ba.bit_range[0] >= ba.bit_range[1]

			if last_bit != 0:
				print 'um, ended on bit', last_bit
				assert last_bit == 0

		return True