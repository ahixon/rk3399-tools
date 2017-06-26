from parse_rk_trm import *
import lxml.etree as ET
import re

import codecs
import sys
import os
import json

sys.stdout = codecs.getwriter('utf8')(sys.stdout)

class PeripheralMap (object):
	def __init__ (self, name, base_addr, block=None):
		if not block:
			block = name

		self.block = block

		if block.endswith ('*'):
			# make fully qualified regblock name
			self.name = block[:-1] + name
		else:
			self.name = name
		self.base_addr = base_addr

	def __repr__ (self):
		return '%s@0x%8x' % (self.name, self.base_addr)

class Builder (object):
	def __init__ (self):
		self.p = Parser()
		# self.name = 'M0'
		self.name = 'AP'
		self.map = {}
		self.addrmap = {}
		self.addrmap_noremap = {}
		self.cpu = {
			# 'name': 'CM0',
			# 'mpuPresent': 0,
			# 'fpuPresent': 0,
			# 'icachePresent': 0,
			# 'dcachePresent': 0,
			# 'nvicPrioBits': 4,
			# 'vtorPresent': 0
			'name': 'CA7',
			'mpuPresent': 1,
			'fpuPresent': 1,
			'icachePresent': 1,
			'dcachePresent': 1,
			'nvicPrioBits': 4,
			'vtorPresent': 1
		}

		self.size_to_bits = {
			'W': 32,
			'HW': 16,
			'B': 8,
			'DW': 64
		}

		self.field_names = {}

		self.load_fields()

		self.access_policy_map = {
			'RW': { 'access': 'read-write' },
			'RO': { 'access': 'read-only' },
			'WO': { 'access': 'write-only' },
			'W1C': {
				'access': 'read-write',
				'modifiedWriteValues': 'oneToClear'
			},
			'RC': {
				'access': 'read-only', 	# FIXME: is this right?
				'readAction': 'clear'
			},
			'WC': {
				'access': 'write-only',	# FIXME: is this right?
				'modifiedWriteValues': 'clear'
			}
		}

		self.bus = {
			'addressUnitBits': 8,
			
			# 'width': 32,
			'width': 64,

			'size': 32, # assume 32-bit registers by default
			'resetValue': 0x0, # default reset
			'resetMask': '0xFFFFFFFF' # reset value applies all bits
		}

		self.peripheral_registers = {}

		# these are MULTIPLEXED via the Interrupt Arbitrator INT_ARBx
		# you'll get an Interrupt, then have to decode which of the 8
		# did it map to (if you've enabled more than one that map
		# to a given block)
		# see Part 1 Datasheet, p498 
		#
		# actually only 18 on PERILPM0
		self.interrupts = 32

		self.load_datasheet ()
		self.load_map ()

	def dump_fields (self):
		with open ('data/fields.json', 'wb') as f:
			json.dump (self.field_names, f, indent=4)

	def load_fields (self):
		if not os.path.exists ('data/fields.json'):
			return

		with open ('data/fields.json', 'rb') as f:
			self.field_names.update (json.load (f))

		# ensure tokens are unique
		tok = {}
		for v in self.field_names.values():
			if v in tok:
				print v, 'already defined in field map!'
				sys.exit (1)

			tok[v] = True

	def copy_existing_reg_summary (self, regsum, name=None):
		if name is None:
			name = regsum.name

		r = Register (name)

		if name == 'PMUCRU_PPLL_CON0':
			print '\tcopying %s to %s' % (regsum.name, name)
			print 'desc is', regsum.description

		r.size = regsum.size
		r.reset_value = regsum.reset_value
		r.description = regsum.description

		regfrom, regto = regsum.offset_range

		# let's assume inclusive
		if regfrom != regto:
			assert r.size == 'W'
			for i in xrange (regfrom, regto + 4, 4):
				r.address_offsets.append (i)
		else:
			r.address_offsets.append (regfrom)

		return r

	def name_range (self, name):
		namefrom, nameto = map(lambda x: int(re.match ('.*(\d+)', x).group(1)), name.split('~'))
		namebase = re.match ('(.*)\d+~.*', name).group(1)
		return (namefrom, namefrom, namebase)

	def load_datasheet (self):
		for i in [1, 2]:
			f = open ('data/rk3399-part%d.xml' % i, 'rb')
			self.p.parse (f, 'Cortex-M0')
			f.close()

		self.p.check()

		# merge groups and registers
		
		# copy from summaries first
		for regsum in self.p.register_summaries:
			pname = regsum.name.split('_')[0]
			if pname not in self.peripheral_registers:
				self.peripheral_registers[pname] = []

			if '~' in regsum.name:
				# make multiple registers
				namefrom, nameto, namebase = self.name_range (regsum.name)

				# again, assume inclusive
				regoff = 0
				for i in xrange (namefrom, nameto + 1):
					r = self.copy_existing_reg_summary (regsum, '%s%d' % (namebase, i))
					assert r.size == 'W'
					r.address_offsets = [regsum.offset_range[0] + (i * 4)]
					self.peripheral_registers[pname].append (r)	

				# print self.peripheral_registers[pname]
				# sys.exit (1)
				self.peripheral_registers[pname].append (r)
			else:
				r = self.copy_existing_reg_summary (regsum)
				self.peripheral_registers[pname].append (r)
			
		# now do per-register, and get bit access
		for r in self.p.registers:	
			if not r.bits:
				print r.name, 'has no bits; skipping'
				continue

			if '~' in r.name:
				namefrom, nameto, namebase = self.name_range (r.name)
				for i in xrange (namefrom, nameto + 1):
					self.find_and_update_from_register (r, '%s%d' % (namebase, i))
			elif 'n' in r.name:
				# same deal, basically; except we already have register offsets
				sys.exit(1)
			else:
				existing = self.find_and_update_from_register (r)

				# and expand it out now if we need
				if len(existing.address_offsets) > 1:
					# also make multiple registers, and delete the old
					# clone it n times first
					regname_idx = 0
					for offset in existing.address_offsets:
						newr = Register ('%s%d' % (regsum.name, regname_idx))
						newr.bits = existing.bits
						newr.address_offsets = [offset]
						newr.description = existing.description
						newr.size = existing.size
						newr.reset_value = existing.reset_value

						regname_idx += 1
						self.peripheral_registers[pname].append (newr)

					# and remove the old
					self.peripheral_registers[r.name.split('_')[0]].remove(existing)

	def find_and_update_from_register (self, r, searchname=None):
		if not searchname:
			searchname = r.name

		pname = searchname.split('_')[0]
		assert pname in self.peripheral_registers

		existing_reg = None
		for reg in self.peripheral_registers[pname]:
			if reg.name == searchname:
				existing_reg = reg
				break

		if existing_reg is None:
			print 'no general map for', searchname
			assert existing_reg

		self.copy_from_existing_reg (r, existing_reg)
		return existing_reg

	def copy_from_existing_reg (self, old_reg, new_reg):
		# don't copy name; caller should have done that (and handled range case)
		# same with address offsets
		if not new_reg.description and not old_reg.description:
			# no desc
			pass
		else:
			if old_reg.description and old_reg.description != new_reg.description:
				print 'Description mismatch on', old_reg.name

				# old will have no reset_value (parse doesn't set one), but WILL have
				# bits and description
				print 'Register info from detail:'
				print '\t%s' % old_reg.description
				print

				print 'Register info from summary:'
				print '\t%s' % new_reg.description

				print 'WARNING: combining them...'
				if '\n' not in old_reg.description and '\n' not in new_reg.description:
					new_reg.description += '/' + old_reg.description
				else:
					new_reg.description += '\n\n' + old_reg.description

				print

		# copy all bits
		# FIXME: may need to handle ~ ranges here?
		new_reg.bits = old_reg.bits

	def parse_hex (self, h):
		if '_' in h:
			return int(h.replace('_', ''), 16)
		elif h.startswith ('0x'):
			return int(h[2:], 16)
		else:
			#return int(h)
			# raise ValueError (h + " should be in hex")
			return eval (h, self.addrmap_noremap)

	def load_map (self):
		system_peripheral_base_key = 'CORE_PERIPHERAL_BASE'
		mcu_peripheral_base_key = 'MCU_PERIPHERAL_BASE'

		with open ('data/peripheral-map.txt', 'r') as f:
			current_peripheral = None
			for line in f:
				cmd = line.upper().split('#')[0]
				if not cmd:
					# blank line ends peripheral block
					# current_peripheral = None
					continue

				if cmd.startswith ('\t'):
					cmd = cmd.strip()
					if not cmd:
						continue

					# peripheral block
					# must have a perhipheral defined to use a block
					assert current_peripheral

					key, value = map(str.strip, cmd.split('\t'))
					original_addr = self.parse_hex (value)
					addr = original_addr

					# remap to MCU
					if key != system_peripheral_base_key and key != mcu_peripheral_base_key:
						addr -= self.addrmap[system_peripheral_base_key]
						if addr < 0:
							print 'ERROR:', key, 'is below peripheral base?', addr, self.addrmap[system_peripheral_base_key]
							sys.exit (1)

						addr += self.addrmap[mcu_peripheral_base_key]

					p = PeripheralMap (key, addr, current_peripheral)
					self.map[current_peripheral].append (p)

					self.addrmap[p.name] = addr
					self.addrmap_noremap[p.name] = original_addr

					print '\tsubperipheral', p
				else:
					cmd = cmd.strip()

					if '\t' in cmd:
						current_peripheral = None

						# key value
						print cmd
						key, value = map(str.strip, cmd.split('\t'))
						if key in self.map:
							raise ValueError(key + " maps multiple times; use block instead")

						addr = self.parse_hex (value)
						original_addr = addr

						# remap to MCU
						if key != system_peripheral_base_key and key != mcu_peripheral_base_key:
							addr -= self.addrmap[system_peripheral_base_key]
							if addr < 0:
								print 'ERROR:', key, 'is below peripheral base?'
								sys.exit (1)

							addr += self.addrmap[mcu_peripheral_base_key]


						self.map[key] = [PeripheralMap (key, addr)]
						self.addrmap[key] = addr
						self.addrmap_noremap[key] = original_addr
					elif cmd:
						# mapping single peripheral to multiple devices
						print "-- entering block '%s'" % cmd
						current_peripheral = cmd
						self.map[current_peripheral] = []


		print self.map

	def export (self):
		ET.register_namespace ('xs', 'http://www.w3.org/2001/XMLSchema-instance')

		root = ET.Element ('device', {
			'schemaVersion': '1.3',
			'{http://www.w3.org/2001/XMLSchema-instance}noNamespaceSchemaLocation': 'CMSIS-SVD_Schema_1_3.xsd'
		})

		ET.SubElement (root, 'name').text = 'RK3399-%s' % self.name
		ET.SubElement (root, 'version').text = '1'
		ET.SubElement (root, 'description').text = 'Rockchip RK3399 for %s CPU' % self.name
		for f in self.bus:
			ET.SubElement (root, f).text = str(self.bus[f])

		cpu = ET.SubElement (root, 'cpu')
		for f in self.cpu:
			ET.SubElement (cpu, f).text = str(self.cpu[f])

		ET.SubElement (cpu, 'deviceNumInterrupts').text = str(self.interrupts)

		# generate peripherals both ways; from devices -> reg
		# then only note about the missing ones in the other direction

		# do device -> reg first
		peripherals_xml = ET.SubElement (root, 'peripherals')
		for groupname in self.map:
			if groupname not in self.peripheral_registers:
				continue

			if len(self.peripheral_registers[groupname]) == 0:
				continue

			peripherals_for_group = self.map[groupname]

			# FIXME: well, since rust-svd doesn't support derivedFrom,
			# build each one out manually
			first_per = None
			for per in peripherals_for_group:
				p_xml = ET.SubElement (peripherals_xml, 'peripheral')
				ET.SubElement (p_xml, 'name').text = per.name
				ET.SubElement (p_xml, 'version').text = '1.0'
				ET.SubElement (p_xml, 'groupname').text = groupname
				ET.SubElement (p_xml, 'baseAddress').text = hex(per.base_addr)

				if first_per is None:
					first_per = per
				else:
					# just mark as derivedFrom the previous one
					# and keep going
					p_xml.attrib['derivedFrom'] = first_per.name
					continue



				# define the addressBlock
				# we do this on a fine-grained level; each register is one addressBlock
				# for reg in peripheral_registers[groupname]:
					# addrblock = ET.SubElement (p_xml, 'addressBlock')
					# ET.SubElement (addrblock, 

				# at the moment, devices don't cause interrupts directly to CPU
				# since they need to go via arbiter first, and be configured

				# do the registers
				# if groupname not in self.peripheral_registers:
				# in address map but not register map
				# continue

				registers_xml = ET.SubElement (p_xml, 'registers')
				for reg in self.peripheral_registers[groupname]:
					# FIXME: YUGE...  :(
					if reg.size == 'DW':
						print 'WARNING: skipping register', reg, 'because svd2rust only supports up to 32-bits'
						continue

					# ignore reserved registers
					if reg.name.lower() == 'reserved':
						continue

					register_xml = ET.SubElement (registers_xml, 'register')

					ET.SubElement (register_xml, 'name').text = reg.name

					# FIXME: svd-parser assumes description must always have text
					# don't know what happens if you just drop the field; can we even do that?
					if reg.description:
						ET.SubElement (register_xml, 'description').text = reg.description
					else:
						ET.SubElement (register_xml, 'description').text = reg.name

					assert len(reg.address_offsets) == 1
					ET.SubElement (register_xml, 'addressOffset').text = hex(reg.address_offsets[0])
					
					ET.SubElement (register_xml, 'size').text = str(self.size_to_bits[reg.size])

					ET.SubElement (register_xml, 'resetValue').text = hex(reg.reset_value)

					# register size dependent
					# FIXME: svd-parser assumes this field may be at most 32-bits
					# and.. it shall be so :(
					use_size = reg.size
					if reg.size == 'DW':
						use_size = 'W'

					ET.SubElement (register_xml, 'resetMask').text = '0x' + ('F' * (self.size_to_bits[use_size] / 4))
					fields_xml = ET.SubElement (register_xml, 'fields')

					for bit in reg.bits:
						if '\n' in bit.description:
							name, desc = bit.description.split('\n', 1)
						else:
							# FIXME: svd-parser assumption may also apply to this field?
							name, desc = (bit.description, '')

						if ' ' in name:
							# no name; copy name from register
							# but ONLY if it's the only one
							if len(reg.bits) == 1:
								desc = name
								name = reg.name
							else:
								if bit.description in self.field_names:
									name = self.field_names[bit.description]
								else:
									print 'there are %d other bits' % len(reg.bits)
									print "unknown name for this register:"
									print bit.description
									name = raw_input("name for register? ")
									self.field_names[bit.description] = name
									self.dump_fields()

								desc = bit.description


						if not desc:
							desc = bit.description

						# ignore reserved fields
						if name.lower() == 'reserved':
							continue

						# looks good; build it
						field_xml = ET.SubElement (fields_xml, 'field')

						ET.SubElement (field_xml, 'name').text = name.upper()
						ET.SubElement (field_xml, 'description').text = desc

						# since idx 1 > 0, 0 becomes offset and we take the diff to do width
						# ET.SubElement (field_xml, 'bitOffset').text = bit.bitrange[0]
						# ET.SubElement (field_xml, 'bitWidth').text = bit.bitrange[1] - bit.bitrange[1]
						ET.SubElement (field_xml, 'bitRange').text = '[%d:%d]' % (bit.bit_range)

						# setup permissions
						attrmap = self.access_policy_map[bit.access_policy]
						for attr in attrmap:
							ET.SubElement (field_xml, attr).text = attrmap[attr]

						# TODO: calculate enumeratedValues where we can
						enums = {}
						for line in bit.description.split('\n'):
							m = re.match ("\s+(\d+'b\d+): (.*)", line)
							val = None
							desc = None

							if m:
								# bitstring format: 2'b01
								bitstr_withprefix = m.group(1)
								prefix, bitstr = bitstr_withprefix.split ('b')
								assert prefix[-1] == "'"

								prefix = prefix[:-1]
								assert len(bitstr) == int(prefix)

								val = int(bitstr, 2)
								desc = m.group(2)
							else:
								m = re.match ("\s+(\d+): (.*)", line)
								if m:
									# just a regular integer value
									val = int(m.group(1))
									desc = m.group(2)

							if val is not None:
								enums[val] = desc

						# if enums:
						# 	enums_xml = ET.SubElement (field_xml, 'enumeratedValues')
						# 	for enum_key in enums:
						# 		enum_xml = ET.SubElement (enums_xml, 'enumeratedValue')
						# 		ET.SubElement (enum_xml, 'name').text = enum_key

						# 		# try to work out what to do with desc
						# 		d = enums[enum_key]
						# 		if 
						# 		ET.SubElement (enum_xml, 'name').text = enum_key



			# for otherper in peripherals_for_group[1:]:
			# 	other_p_xml = ET.SubElement (peripherals_xml, 'peripheral')

			# 	ET.SubElement (other_p_xml, 'derivedFrom').text = firstper.name
			# 	ET.SubElement (other_p_xml, 'name').text = otherper.name
			# 	ET.SubElement (other_p_xml, 'version').text = '1.0'
			# 	ET.SubElement (other_p_xml, 'groupname').text = groupname
			# 	ET.SubElement (other_p_xml, 'baseAddress').text = hex(otherper.base_addr)


		return ET.tostring (root, pretty_print=True)

if __name__ == '__main__':
	b = Builder ()
	with codecs.open ('rk3399-ap.svd', 'w', 'utf-8') as f:
		f.write (b.export ())
