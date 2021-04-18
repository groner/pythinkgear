# Copyright (c) 2009, Kai Groner
# All rights reserved.
# 
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
# 
#     * Redistributions of source code must retain the above copyright notice,
#       this list of conditions and the following disclaimer.
#     * Redistributions in binary form must reproduce the above copyright notice,
#       this list of conditions and the following disclaimer in the documentation
#       and/or other materials provided with the distribution.
#     * Neither the name of the Kai Groner nor the names of its contributors
#       may be used to endorse or promote products derived from this software
#       without specific prior written permission.
# 
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import sys

import serial

if sys.version_info.major == 2:
    from cStringIO import StringIO
else:
    from io import BytesIO

import struct

from collections import namedtuple

import logging
import logging.handlers

_log = logging.getLogger(__name__)

_bytelog = logging.getLogger(__name__+'.bytes')
_bytelog.propagate = False

# Uncomment this to save log messages about the data stream in memory
# _bytes = logging.handlers.MemoryHandler(sys.maxint, 100)
# _bytes.addFilter(logging.Filter(name=__name__+'.bytes'))
# _bytelog.addHandler(_bytes)


class ThinkGearProtocol(object):
    '''Process the ThinkGear protocol.

    >>> tg = ThinkGearProtocol(device)
    >>> for pkt in tg.get_packets():
    ...     for d in pkt:
    ...         if isinstance(d, ThinkGearAttentionData) and d.value == 100:
    ...             print "You win!"
    ...             break

    '''

    # The _read/_deread scheme is untested

    def __init__(self, port):
        # TODO: Handle bluetooth rfcomm setup
        # TODO: ???

        self.serial = serial.Serial(port, 57600)
        if sys.version_info.major == 2:
            self.preread = StringIO()
        else:
            self.preread = BytesIO()
        self.io = self.serial

    @staticmethod
    def _chksum(packet):
        if sys.version_info.major == 2:
            return ~sum( ord(c) for c in packet ) & 0xff
        else:
            return ~sum( c for c in packet ) & 0xff

    def _read(self, n):
        buf = self.io.read(n)
        if len(buf) < n:
            _log.debug('incomplete read 1, short %s bytes', n - len(buf))
            if self.io == self.preread:
                _log.debug('end of preread buffer')
                #self.preread.reset()   # Just create a new one : faster
                #self.preread.truncate()
                if sys.version_info.major == 2:
                    self.preread = StringIO()
                else:
                    self.preread = BytesIO()
                self.io = self.serial
                buf += self.io.read(n-len(buf))
                if len(buf) < n:
                    _log.debug('incomplete read 2, short %s bytes', n - len(buf))

        if sys.version_info.major == 2:
            for o in xrange(0, len(buf), 16):
                _bytelog.debug('%04X  '+' '.join(('%02X',)*len(buf[o:o+16])), o, *( ord(c) for c in buf[o:o+16] ))
        else:
            for o in range(0, len(buf), 16):
                _bytelog.debug('%04X  '+' '.join(('%02X',)*len(buf[o:o+16])), o, *( c for c in buf[o:o+16] ))

        return buf

    def _deread(self, buf):
        _log.debug('putting back %s bytes', len(buf))
        pos = self.preread.tell()
        self.preread.seek(0, 2)
        self.preread.write(buf)
        self.preread.seek(pos)
        self.io = self.preread

    def get_packets(self):
        last_two = ()
        while True:
            last_two = last_two[-1:]+(self._read(1),)
            #_log.debug('last_two: %r', last_two)
            if sys.version_info.major == 2:
                sync_byte = '\xAA'  # 170
            else:
                sync_byte = b'\xAA' # 170
            if last_two == (sync_byte, sync_byte):  # Detect a packet header
                plen = self._read(1)    # read a payload length
                if plen >= sync_byte:   # Any value from 0 up to 169
                    # Bogosity
                    _log.debug('discarding %r while syncing: Payload length too large.', last_two[0])
                    last_two = last_two[-1:]+(plen,)

                else:
                    last_two = ()
                    if sys.version_info.major == 2:
                        packet = self._read(ord(plen))
                    else:
                        packet = self._read(int.from_bytes(plen, "big"))

                    checksum = self._read(1)

                    if sys.version_info.major == 2:
                        chksum = ord(checksum)
                    else:
                        chksum = int.from_bytes(checksum, "big")

                    if chksum == self._chksum(packet):
                        yield self._decode(packet)
                    else:
                        _log.debug('bad checksum')
                        self._deread(packet+checksum)

            elif len(last_two) == 2:
                _log.debug('discarding %r while syncing', last_two[0])

    def _decode(self, packet):
        decoded = []

        while packet:
            if sys.version_info.major == 2:
                excode_byte = '\x55'  # Extended code
            else:
                excode_byte = b'\55' # Extended code
            extended_code_level = 0
            while len(packet) and packet[0] == excode_byte:
                extended_code_level += 1
                packet = packet[1:]
            if len(packet) < 2:
                if sys.version_info.major == 2:
                    _log.debug('ran out of packet: %r', excode_byte*extended_code_level+packet)
                else:
                    _log.debug('ran out of packet:' + '\\x55'*extended_code_level + ''.join(('\\x%02X',)*len(packet)), *( c for c in packet ))
                break

            if sys.version_info.major == 2:
                code = ord(packet[0])
            else:
                code = packet[0]
            if code < 0x80:
                value = packet[1]
                packet = packet[2:]
            else:
                if sys.version_info.major == 2:
                    vlen = ord(packet[1])
                    if len(packet) < 2+vlen:
                        _log.debug('ran out of packet: %r', '\x55'*extended_code_level+chr(code)+chr(vlen)+packet)
                        break
                else:
                    vlen = packet[1]
                    if len(packet) < 2+vlen:
                        _log.debug('ran out of packet:' + '\\x55'*extended_code_level + ''.join(('\\x%02X',)*len(packet)), *( c for c in packet ))
                        break
                value = packet[2:2+vlen]
                packet = packet[2+vlen:]

            if not extended_code_level and code in data_types:
                data = data_types[code](extended_code_level, code, value)

            elif (extended_code_level,code) in data_types:
                data = data_types[(extended_code_level,code)](extended_code_level, code, value)

            else:
                data = ThinkGearUnknownData(extended_code_level, code, value)

            decoded.append(data)

        return decoded


data_types = {}

class ThinkGearMetaClass(type):
    def __new__(mcls, name, bases, data):
        cls = super(ThinkGearMetaClass, mcls).__new__(mcls, name, bases, data)
        code = getattr(cls, 'code', None)
        if code:
            data_types[code] = cls
            extended_code_level = getattr(cls, 'extended_code_level', None)
            if extended_code_level:
                data_types[(extended_code_level,code)] = cls
        return cls

ThinkGearClass = ThinkGearMetaClass("ThinkGearData", (object, ), {"__doc__": ThinkGearMetaClass.__doc__})

class ThinkGearData(ThinkGearClass):
    def __init__(self, extended_code_level, code, value):
        self.extended_code_level = extended_code_level
        self.code = code
        self.value = self._decode(value)
        if self._log:
            _log.log(self._log, '%s', self)

    @staticmethod
    def _decode(v):
        return v

    def __str__(self):
        return self._strfmt % vars(self)

    _log = logging.DEBUG


class ThinkGearUnknownData(ThinkGearData):
    '''???'''
    _strfmt = 'Unknown: code=%(code)02X extended_code_level=%(extended_code_level)s %(value)r'


class ThinkGearPoorSignalData(ThinkGearData):
    '''POOR_SIGNAL Quality (0-255)'''
    code = 0x02
    _strfmt = 'POOR SIGNAL: %(value)s'
    if sys.version_info.major == 2:
        _decode = staticmethod(ord)


class ThinkGearAttentionData(ThinkGearData):
    '''ATTENTION eSense (0 to 100)'''
    code = 0x04
    _strfmt = 'ATTENTION eSense: %(value)s'
    if sys.version_info.major == 2:
        _decode = staticmethod(ord)


class ThinkGearMeditationData(ThinkGearData):
    '''MEDITATION eSense (0 to 100)'''
    code = 0x05
    _strfmt = 'MEDITATION eSense: %(value)s'
    if sys.version_info.major == 2:
        _decode = staticmethod(ord)


class ThinkGearRawWaveData(ThinkGearData):
    '''RAW Wave Value (-32768 to 32767)'''
    code = 0x80
    _strfmt = 'Raw Wave: %(value)s'
    _decode = staticmethod(lambda v: struct.unpack('>h', v)[0])
    # There are lots of these, don't log them by default
    _log = False


EEGPowerData = namedtuple('EEGPowerData', 'delta theta lowalpha highalpha lowbeta highbeta lowgamma midgamma')
class ThinkGearEEGPowerData(ThinkGearData):
    '''Eight EEG band power values (0 to 16777215).
    
    delta, theta, low-alpha high-alpha, low-beta, high-beta, low-gamma, and
    mid-gamma EEG band power values.
    '''

    code = 0x83
    _strfmt = 'ASIC EEG Power: %(value)r'
    if sys.version_info.major == 2:
        _decode = staticmethod(lambda v: EEGPowerData(*struct.unpack('>8L', ''.join( '\x00'+v[o:o+3] for o in xrange(0, 24, 3)))))
    else:
        _decode = staticmethod(lambda v: EEGPowerData(*[ int.from_bytes(v[o:o+3], "big") for o in range(0, 24, 3)]))


def main():
    global packet_log
    packet_log = []
    logging.basicConfig(level=logging.DEBUG)

    for pkt in ThinkGearProtocol('/dev/rfcomm9').get_packets():
        packet_log.append(pkt)

if __name__ == '__main__':
    main()

