#!/usr/bin/env python
from parse_rk_trm import *
import lxml.etree as ET
import re

import sys
import os
import json
import pycparser

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
	def __init__ (self, name):
		self.p = Parser()
		self.name = name
		self.map = {}
		self.addrmap = {}
		self.addrmap_noremap = {}

		self.systems = {
			'M0': {
				'buswidth': 32,	# ?
				'interrupts': 32,
				'cpu': {
					'name': 'CM0',
					'revision': 'r0p1',
					'endian': 'little',
					'vendorSystickConfig': 0,
					'mpuPresent': 0,
					'fpuPresent': 0,
					'icachePresent': 0,
					'dcachePresent': 0,
					'nvicPrioBits': 4,
					'vtorPresent': 0
				}
			},

			'AP': {
				'buswidth': 64,
				'interrupts': 0,
				'cpu': {
					'name': 'CA7',
					'revision': 'r0p1',
					'endian': 'little',
					'vendorSystickConfig': 0,
					'mpuPresent': 1,
					'fpuPresent': 1,
					'icachePresent': 1,
					'dcachePresent': 1,
					'nvicPrioBits': 4,
					'vtorPresent': 1
				}
			}
		}

		assert name in self.systems
		self.cpu = self.systems[name]['cpu']

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
			
			'width': self.systems[name]['buswidth'],

			'size': 32, # assume 32-bit registers by default
			'resetValue': 0x0, # default reset
			'resetMask': '0xFFFFFFFF' # reset value applies all bits
		}

		self.peripherals_from_structs = {
			'PMUSGRF': {
				'struct': 'data/pmusgrf_struct.c',
				'registers': 'data/pmusgrf_registers.txt'
			}
		}

		self.peripheral_registers_arrayable = {
			'RKI2C' : {
				'RKI2C_RXDATA\d+':	{
					'name': 'RKI2C_RXDATA%s',
					'description': 'I2C rx data register n',
					'bits': [
						BitAccess(bit_range=(31, 0), description='32-byte received data', access_policy='RO')
					]
				},

				'RKI2C_TXDATA\d+': {
					'name': 'RKI2C_TXDATA%s',
					'description': 'I2C tx data register n',
					'bits': [
						BitAccess(bit_range=(31, 0), description='32-byte data to transmit', access_policy='RW')
					]
				}
			}
		}

		self.peripheral_registers = {}

		# these are MULTIPLEXED via the Interrupt Arbitrator INT_ARBx
		# you'll get an Interrupt, then have to decode which of the 8
		# did it map to (if you've enabled more than one that map
		# to a given block)
		# see Part 1 Datasheet, p498 
		#
		# actually only 18 on PERILPM0
		self.interrupts = self.systems[name]['interrupts']

		self.load_datasheet ()
		self.load_map ()

		self.load_structs()
		self.merge_blocks_into_arrays ()

	def parse_struct(self, fname):
		structs = {}
		ast = pycparser.parse_file(fname)
		for toplevel_child in ast.ext:
			if isinstance(toplevel_child.type, pycparser.c_ast.Struct):
				struct_offsets = {}
				byte_offset = 0

				# have the struct
				struct = toplevel_child.type
				for child in struct.decls:
					name = child.name

					child_size = 0
					if isinstance(child.type, pycparser.c_ast.TypeDecl):
						# single instance, just add by name
						typenode = child.type.type
						assert typenode.names[0] == 'u32'
						child_size = 4

						struct_offsets[name] = byte_offset
					elif isinstance(child.type, pycparser.c_ast.ArrayDecl):
						# also need to emit one register for each array entry
						# UNLESS it's "reserved"

						arraynode = child.type
						typenode = arraynode.type.type
						assert typenode.names[0] == 'u32'

						value = arraynode.dim.value
						if value.lower().startswith('0x'):
							value = int(value[2:], 16)
						else:
							value = int(value)

						child_size = 4 * value

						array_item_offset = 0
						if not name.startswith('reserved'):
							# generate individual register per array item
							for i in range(value):
								struct_offsets[name + str(i)] = byte_offset + (array_item_offset)
								array_item_offset += 4
						
					else:
						assert False
					
					byte_offset += child_size

				structs[toplevel_child.name] = struct_offsets

		assert len(structs) == 1
		return list(structs.values())[0]

	def parse_register_textfile(self, registers, reginfo):
		def assign_split_withdefault(x):
			p = x.split('=', 1)
			if len(p) == 1:
				return (p[0], None)
			else:
				return p

		reg_struct_decl = None
		reg_desc = None
		reg_name = None
		reg_enum = {}
		reg_params = {}
		reg_bit_from = None
		reg_bit_to = None

		for line in reginfo:
			line = line.strip(' ').rstrip('\n')
			if not line:
				continue

			if line.startswith('//'):
				# skip comments
				continue

			if line.startswith('\t\t'):
				# enum value for register
				line = line.strip()
				val, desc = list(map(str.strip, line.split(':')))
				reg_enum[val] = desc

				continue

			if line.startswith('\t'):
				line = line.strip()

				# register description or name
				if reg_name is None:
					reg_name = line
				else:
					reg_desc = line

				continue			

			m = re.match(r'(\w+)(\[(\d+)(:(\d+))?\])?\s*(.*)?', line)
			if m:
				if reg_name:
					# new bitaccess
					if reg_bit_to == None:
						bitrange = (int(reg_bit_from), int(reg_bit_from))
					else:
						bitrange = (int(reg_bit_from), int(reg_bit_to))

					access = 'RW'

					if 'w' in reg_params:
						access = 'WO'

					reset_value = None
					if 'default' in reg_params:
						reset_value = reg_params['default']

					if 'reset' in reg_params:
						if reset_value:
							print('WARNING: both default and reset given for', reg_name)

						reset_value = reg_params['reset']

					# use name as default description
					if not reg_desc:
						reg_desc = reg_name

					ba = BitAccess(
						bit_range=bitrange,
						description=reg_desc,
						access_policy=access)
					if reset_value:
						if reset_value.startswith('0x'):
							ba.reset_value = int(reset_value[2:], 16)
						else:
							ba.reset_value = int(reset_value)
					ba.name = reg_name

					registers[reg_struct_decl].bits.append(ba)

				reg_struct_decl, _, bit_from, _, bit_to, params = m.groups()
				# print m.groups()

				if params:
					params = dict(list(map(assign_split_withdefault, params.split(' '))))
				else:
					params = {}

				if reg_struct_decl not in registers:
					print('Error: field', reg_struct_decl, 'not in struct')
					sys.exit(1)

				# reset for new one
				reg_desc = None
				reg_name = None
				reg_enum = {}
				reg_params = params
				reg_offset = registers[reg_struct_decl]
				reg_bit_from = bit_from
				reg_bit_to = bit_to
			else:
				print('WARNING: invalid line', line)

		# and last
		if reg_name:
			# new bitaccess
			if reg_bit_to == None:
				bitrange = (int(reg_bit_from), int(reg_bit_from))
			else:
				bitrange = (int(reg_bit_from), int(reg_bit_to))

			access = 'RW'

			if 'w' in reg_params:
				access = 'WO'

			reset_value = None
			if 'default' in reg_params:
				reset_value = reg_params['default']

			if 'reset' in reg_params:
				if reset_value:
					print('WARNING: both default and reset given for', reg_name)

				reset_value = reg_params['reset']

			# use name as default description
			if not reg_desc:
				reg_desc = reg_name

			ba = BitAccess(
				bit_range=bitrange,
				description=reg_desc,
				access_policy=access)
			if reset_value:
				if reset_value.startswith('0x'):
					ba.reset_value = int(reset_value[2:], 16)
				else:
					ba.reset_value = int(reset_value)
			ba.name = reg_name

			registers[reg_struct_decl].bits.append(ba)


	def load_structs(self):
		for (per_for_struct, info) in self.peripherals_from_structs.items():
			# hash from reg -> offset
			struct = self.parse_struct(info['struct'])

			# convert struct to registers
			registers = {}
			for field, offset in struct.items():
				newr = Register (field)
				newr.bits = []
				newr.address_offsets = [offset]
				newr.description = field # FIXME: nicer names :)
				newr.size = 'W'
				#newr.reset_value = reg_params['reset']
				# TODO: give a reset value later, but only
				# if we know all the reset values for each bit

				registers[field] = newr

			with open(info['registers'], 'r') as f:
				self.parse_register_textfile(registers, f)


			# conver to list
			registers = list(registers.values())
			for reg in registers:
				reset_value = 0
				unset = list(range(32))
				for bit in reg.bits:
					s = list(sorted(bit.bit_range))
					# print bit.name, s

					for i in range(s[0], s[1] + 1):
						unset[i] = None

					if not bit.reset_value:
						print('WARNING: ', bit.name, 'has no reset value; assuming 0')
						bit.reset_value = 0

					reset_value |= bit.reset_value << s[0]

				print(reg, unset)
				have_unknown = any(unset)
				if have_unknown:
					print("WARNING: don't fully know register", reg)
					print('Pretending reset value is 0')
					reg.reset_value = 0
				else:
					print('calculated reset value for', reg)
					reg.reset_value = reset_value


			# add registers and bit accesses to peripheral!
			self.peripheral_registers[per_for_struct] = registers
			# print self.peripheral_registers

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
		for v in list(self.field_names.values()):
			if v in tok:
				print(v, 'already defined in field map!')
				sys.exit (1)

			tok[v] = True

	def copy_existing_reg_summary (self, regsum, name=None):
		if name is None:
			name = regsum.name

		r = Register (name)

		if name == 'PMUCRU_PPLL_CON0':
			print('\tcopying %s to %s' % (regsum.name, name))
			print('desc is', regsum.description)

		r.size = regsum.size
		r.reset_value = regsum.reset_value
		r.description = regsum.description

		regfrom, regto = regsum.offset_range

		# let's assume inclusive
		if regfrom != regto:
			assert r.size == 'W'
			for i in range (regfrom, regto + 4, 4):
				r.address_offsets.append (i)
		else:
			r.address_offsets.append (regfrom)

		return r

	def name_range (self, name):
		namefrom, nameto = [int(re.match ('.*(\d+)', x).group(1)) for x in name.split('~')]
		namebase = re.match ('(.*)\d+~.*', name).group(1)
		return (namefrom, namefrom, namebase)

	def load_datasheet (self):
		for i in [1, 2]:
			f = open ('data/rk3399-part%d.xml' % i, 'rb')
			self.p.parse (f)
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
				for i in range (namefrom, nameto + 1):
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
				print(r.name, 'has no bits; skipping')
				continue

			if '~' in r.name:
				namefrom, nameto, namebase = self.name_range (r.name)
				for i in range (namefrom, nameto + 1):
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

	def merge_blocks_into_arrays (self):
		for groupname in self.peripheral_registers_arrayable:
			if groupname not in self.peripheral_registers:
				print('WARNING: have array access defined on peripheral', groupname, 'but not in datasheet?')
				continue

			transformations = self.peripheral_registers_arrayable[groupname]
			for transform in transformations:
				replacement_info = transformations[transform]

				# find out the keys of the registers we're removing for this transformation
				to_remove = [x for x in self.peripheral_registers[groupname] if (x if re.match(transform, x.name) else None)]
				# print to_remove

				# ensure all the right size
				for reg in to_remove:
					assert self.size_to_bits[reg.size] == 32

				dim = len(to_remove)
				print('Converted %d registers to array using %s' % (dim, transform))

				# keep the first one to use as the "array register", and remove the others
				base = to_remove[0]
				for r in to_remove[1:]:
					self.peripheral_registers[groupname].remove(r)

				# update in-place to use the generic array data
				base.name = replacement_info['name']
				base.description = replacement_info['description']
				base.bits = replacement_info['bits']

				base.dim = dim

				# probably optional, but hey
				base.dimIndex = '0-%s' % (dim - 1)
				base.dimIncrement = 32 // 8


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
			print('no general map for', searchname)
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
				print('Description mismatch on', old_reg.name)

				# old will have no reset_value (parse doesn't set one), but WILL have
				# bits and description
				print('Register info from detail:')
				print('\t%s' % old_reg.description)
				print()

				print('Register info from summary:')
				print('\t%s' % new_reg.description)

				print('WARNING: combining them...')
				if '\n' not in old_reg.description and '\n' not in new_reg.description:
					new_reg.description += '/' + old_reg.description
				else:
					new_reg.description += '\n\n' + old_reg.description

				print()

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
		mcu_peripheral_base_prefix = 'MCU_PERIPHERAL_BASE'
		mcu_peripheral_base_key = '%s_%s' % (mcu_peripheral_base_prefix, self.name)

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

					key, value = list(map(str.strip, cmd.split('\t')))
					original_addr = self.parse_hex (value)
					addr = original_addr

					# remap to MCU
					if key != system_peripheral_base_key and not key.startswith(mcu_peripheral_base_prefix):
						addr -= self.addrmap[system_peripheral_base_key]
						if addr < 0:
							print('ERROR:', key, 'is below peripheral base?', addr, self.addrmap[system_peripheral_base_key])
							sys.exit (1)

						addr += self.addrmap[mcu_peripheral_base_key]

					p = PeripheralMap (key, addr, current_peripheral)
					self.map[current_peripheral].append (p)

					self.addrmap[p.name] = addr
					self.addrmap_noremap[p.name] = original_addr

					print('\tsubperipheral', p)
				else:
					cmd = cmd.strip()

					if '\t' in cmd:
						current_peripheral = None

						# key value
						print(cmd)
						key, value = list(map(str.strip, cmd.split('\t')))
						if key in self.map:
							raise ValueError(key + " maps multiple times; use block instead")

						addr = self.parse_hex (value)
						original_addr = addr

						# remap to MCU
						if key != system_peripheral_base_key and not key.startswith(mcu_peripheral_base_prefix):
							addr -= self.addrmap[system_peripheral_base_key]
							if addr < 0:
								print('ERROR:', key, 'is below peripheral base?')
								sys.exit (1)

							addr += self.addrmap[mcu_peripheral_base_key]


						self.map[key] = [PeripheralMap (key, addr)]
						self.addrmap[key] = addr
						self.addrmap_noremap[key] = original_addr
					elif cmd:
						# mapping single peripheral to multiple devices
						print("-- entering block '%s'" % cmd)
						current_peripheral = cmd
						self.map[current_peripheral] = []


		print(self.map)

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
						print('WARNING: skipping register', reg, 'because svd2rust only supports up to 32-bits')
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

					if reg.dim is not None:
						ET.SubElement (register_xml, 'dim').text = str(reg.dim)
						ET.SubElement (register_xml, 'dimIndex').text = reg.dimIndex
						ET.SubElement (register_xml, 'dimIncrement').text = str(reg.dimIncrement)

					# register size dependent
					# FIXME: svd-parser assumes this field may be at most 32-bits
					# and.. it shall be so :(
					use_size = reg.size
					if reg.size == 'DW':
						use_size = 'W'

					ET.SubElement (register_xml, 'resetMask').text = '0x' + ('F' * int(self.size_to_bits[use_size] / 4))
					fields_xml = ET.SubElement (register_xml, 'fields')

					for bit in reg.bits:
						if 'name' not in bit.__dict__:
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

									# to handle arrays
									if '%s' in name:
										name = name.replace('%s', '')
								else:
									if bit.description in self.field_names:
										name = self.field_names[bit.description]
									else:
										print('there are %d other bits' % len(reg.bits))
										print("unknown name for this register:")
										print(bit.description)
										name = input("name for register? ")
										self.field_names[bit.description] = name
										self.dump_fields()

									desc = bit.description
						else:
							name = bit.name

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
	if len(sys.argv) != 3:
		sys.stderr.write('usage: %s <ap|m0> <target.svd>\n' % sys.argv[0])
		sys.exit(1)

	b = Builder (sys.argv[1].upper())
	with open (sys.argv[2], 'wb') as f:
		f.write (b.export ())
