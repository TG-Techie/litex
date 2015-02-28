import os

from migen.bank import csrgen
from migen.bus import wishbone, csr
from migen.bus import wishbone2csr
from migen.bank.description import *

from targets import *

from litescope.common import *
from litescope.bridge.uart2wb import LiteScopeUART2WB
from litescope.frontend.io import LiteScopeIO
from litescope.frontend.la import LiteScopeLA
from litescope.core.port import LiteScopeTerm

class _CRG(Module):
	def __init__(self, clk_in):
		self.clock_domains.cd_sys = ClockDomain()
		self.clock_domains.cd_por = ClockDomain(reset_less=True)

		# Power on Reset (vendor agnostic)
		rst_n = Signal()
		self.sync.por += rst_n.eq(1)
		self.comb += [
			self.cd_sys.clk.eq(clk_in),
			self.cd_por.clk.eq(clk_in),
			self.cd_sys.rst.eq(~rst_n)
		]

class GenSoC(Module):
	csr_base = 0x00000000
	csr_data_width = 32
	csr_map = {
		"bridge":			0,
		"identifier":		1,
	}
	interrupt_map = {}
	cpu_type = None
	def __init__(self, platform, clk_freq):
		self.clk_freq = clk_freq
		# UART <--> Wishbone bridge
		self.submodules.uart2wb = LiteScopeUART2WB(platform.request("serial"), clk_freq, baud=115200)

		# CSR bridge   0x00000000 (shadow @0x00000000)
		self.submodules.wishbone2csr = wishbone2csr.WB2CSR(bus_csr=csr.Interface(self.csr_data_width))
		self._wb_masters = [self.uart2wb.wishbone]
		self._wb_slaves = [(lambda a: a[23:25] == 0, self.wishbone2csr.wishbone)]
		self.cpu_csr_regions = [] # list of (name, origin, busword, csr_list/Memory)

		# CSR
		self.submodules.identifier = Identifier(0, int(clk_freq))

	def add_cpu_memory_region(self, name, origin, length):
		self.cpu_memory_regions.append((name, origin, length))

	def add_cpu_csr_region(self, name, origin, busword, obj):
		self.cpu_csr_regions.append((name, origin, busword, obj))

	def do_finalize(self):
		# Wishbone
		self.submodules.wishbonecon = wishbone.InterconnectShared(self._wb_masters,
			self._wb_slaves, register=True)

		# CSR
		self.submodules.csrbankarray = csrgen.BankArray(self,
			lambda name, memory: self.csr_map[name if memory is None else name + "_" + memory.name_override],
			data_width=self.csr_data_width)
		self.submodules.csrcon = csr.Interconnect(self.wishbone2csr.csr, self.csrbankarray.get_buses())
		for name, csrs, mapaddr, rmap in self.csrbankarray.banks:
			self.add_cpu_csr_region(name, 0xe0000000+0x800*mapaddr, flen(rmap.bus.dat_w), csrs)
		for name, memory, mapaddr, mmap in self.csrbankarray.srams:
			self.add_cpu_csr_region(name, 0xe0000000+0x800*mapaddr, flen(rmap.bus.dat_w), memory)

class LiteScopeSoC(GenSoC, AutoCSR):
	default_platform = "de0nano"
	csr_map = {
		"io":	10,
		"la":	11
	}
	csr_map.update(GenSoC.csr_map)
	def __init__(self, platform):
		clk_freq = 50*1000000
		GenSoC.__init__(self, platform, clk_freq)
		self.submodules.crg = _CRG(platform.request("clk50"))

		self.submodules.io = LiteScopeIO(8)
		self.leds = Cat(*[platform.request("user_led", i) for i in range(8)])
		self.comb += self.leds.eq(self.io.o)

		self.submodules.counter0 = counter0 = Counter(bits_sign=8)
		self.submodules.counter1 = counter1 = Counter(bits_sign=8)
		self.comb += [
			counter0.ce.eq(1),
			If(counter0.value == 16,
				counter0.reset.eq(1),
				counter1.ce.eq(1)
			)
		]

		self.debug = (
			counter1.value
		)
		self.submodules.la = LiteScopeLA(self.debug, 512, with_rle=True, with_subsampler=True)
		self.la.trigger.add_port(LiteScopeTerm(self.la.dw))

	def do_exit(self, vns):
		self.la.export(vns, "test/la.csv")

default_subtarget = LiteScopeSoC