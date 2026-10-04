"""Pure packet codec for the HIMS serial/custom-bulk display protocol.

No Windows bulk backend is selected here: established BrailleSense USB devices
need a compatible signed driver before user-mode code can open their endpoints.
Keeping the codec independent lets descriptor/driver discovery choose a backend
without reimplementing or weakening the protocol checks.
"""

from __future__ import annotations

from typing import Sequence


def build_packet(packet_type: int, mode: int, data1: bytes, data2: bytes = b"") -> bytes:
    if not 0 <= packet_type <= 0xFF or not 0 <= mode <= 0xFF:
        raise ValueError("packet type and mode must be bytes")
    if len(data1) > 0xFFFF or len(data2) > 0xFFFF:
        raise ValueError("HIMS data blocks cannot exceed 65535 bytes")
    packet = bytearray(
        bytes((packet_type, packet_type, mode, 0xF0))
        + len(data1).to_bytes(2, "little")
        + data1
        + b"\xF1\xF2"
        + len(data2).to_bytes(2, "little")
        + data2
        + b"\xF3"
        + bytes(4)
        + b"\x00\xFD\xFD"
    )
    packet[-3] = sum(packet) & 0xFF
    return bytes(packet)


def build_display_packet(cells: Sequence[int]) -> bytes:
    if not cells:
        raise ValueError("a braille display must have at least one cell")
    if any(not isinstance(cell, int) or not 0 <= cell <= 0xFF for cell in cells):
        raise ValueError("braille cells must be integers from 0 through 255")
    return build_packet(0xFC, 0x01, bytes(cells))


def build_cell_count_request() -> bytes:
    return build_packet(0xFB, 0x01, bytes(32))


def checksum_is_valid(packet: bytes) -> bool:
    if len(packet) < 3 or packet[-2:] != b"\xFD\xFD":
        return False
    expected = packet[-3]
    return expected == (sum(packet[:-3]) + sum(packet[-2:])) & 0xFF
