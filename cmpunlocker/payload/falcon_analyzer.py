"""
falcon_analyzer.py — Analyze Falcon ISA instructions in GSP firmware binary.

This tool helps identify fuse-check code patterns in the GSP firmware that
enforce the OTP fuse limitation. Scans for common patterns:

1. CSR reads of register 0x7ca (fuse register)
2. Comparison instructions (CMP, CMPu, CMPs)
3. Conditional branches that enforce Gen 2 x4 limit

Falcon ISA reference:
- MOV $r0, CSR_ADDR — Load CSR register value
- CMP $reg, value — Compare register with value
- BRA cc, offset — Conditional branch
- Common register patterns for capability checks

This is a starting point for identifying where firmware patches are needed.
"""

import struct
import logging
from pathlib import Path
from collections import defaultdict

log = logging.getLogger(__name__)


class FalconPattern:
    """A detected Falcon ISA pattern in the firmware binary."""

    def __init__(self, offset, pattern_type, context, confidence=0.5):
        self.offset = offset
        self.pattern_type = pattern_type  # 'csr_read', 'cmp', 'branch', 'gen2_hardcoded'
        self.context = context  # surrounding bytes for analysis
        self.confidence = confidence  # 0.0-1.0 likelihood this is real pattern

    def __repr__(self):
        return f"FalconPattern(offset=0x{self.offset:x}, type={self.pattern_type}, conf={self.confidence:.1f})"


class FalconAnalyzer:
    """Scan GSP firmware binary for Falcon ISA fuse-check patterns."""

    def __init__(self, firmware_path: str):
        self.firmware = Path(firmware_path).read_bytes()
        self.patterns = []
        log.info(f"[FALCON] Loaded {len(self.firmware)} bytes from {firmware_path}")

    def find_csr_references(self) -> list:
        """Find references to CSR 0x7ca (fuse register) in firmware.

        Falcon uses IO space registers that are addressed as 32-bit words.
        CSR 0x7ca would appear as bytes in various encodings depending on
        instruction format. Look for:
        - 0x7ca as little-endian 32-bit: CA 07 00 00
        - Isolated CSR references (not just any 0x7ca byte sequence)
        """
        patterns = []

        # Pattern 1: Look for 0x7ca as little-endian dword (CA 07 00 00)
        # Only match if it looks like it's in code section (reasonable spacing)
        csr_le = struct.pack("<I", 0x7ca)
        pos = 0
        found_positions = []
        while True:
            pos = self.firmware.find(csr_le, pos)
            if pos == -1:
                break
            found_positions.append(pos)
            pos += 1

        # Filter: only keep CSR references that are spaced at least 8 bytes apart
        # (likely different code sequences, not just repeated data)
        filtered_positions = []
        for p in found_positions:
            if not filtered_positions or p - filtered_positions[-1] >= 8:
                filtered_positions.append(p)

        for pos in filtered_positions[:100]:  # Limit to top 100 matches
            context = self.firmware[max(0, pos-32):min(len(self.firmware), pos+64)]
            p = FalconPattern(pos, 'csr_read', context, confidence=0.7)
            patterns.append(p)

        log.info(f"[FALCON] Found {len(filtered_positions)} filtered CSR 0x7ca references (showed {len(patterns)})")

        return patterns

    def find_comparison_instructions(self) -> list:
        """Find CMP instructions that might enforce capability limits.

        Falcon CMP instructions check if a capability matches a limit value.
        Pattern: comparison followed by conditional branch.
        """
        patterns = []

        # Falcon CMP opcode patterns (simplified)
        # CMP instruction family: 0x24-0x27 range
        cmp_opcodes = [0x24, 0x25, 0x26, 0x27]

        for i in range(len(self.firmware) - 2):
            if self.firmware[i] in cmp_opcodes:
                context = self.firmware[max(0, i-8):min(len(self.firmware), i+16)]
                p = FalconPattern(i, 'cmp', context, confidence=0.5)
                patterns.append(p)

        return patterns

    def find_conditional_branches(self) -> list:
        """Find conditional branch instructions that skip capability unlock.

        Pattern: BRA instruction with various condition codes
        Falcon BRA instruction format typically: 0x08, 0x09 range
        """
        patterns = []

        # Falcon branch opcodes
        branch_opcodes = [0x08, 0x09, 0x0a, 0x0b]

        for i in range(len(self.firmware) - 3):
            if self.firmware[i] in branch_opcodes:
                context = self.firmware[max(0, i-8):min(len(self.firmware), i+16)]
                p = FalconPattern(i, 'branch', context, confidence=0.4)
                patterns.append(p)

        return patterns

    def find_gen2_hardcoded_limits(self) -> list:
        """Find hardcoded Gen 2 (value 0x02) in capability checking code.

        Firmware might have Gen 2 speed limit (0x02) hardcoded in comparison or
        return value instructions.
        """
        patterns = []

        # Look for Gen 2 value (0x02) near potential capability-related code
        # Common patterns:
        # - Followed by shift operations for capability encoding
        # - Loaded into specific registers
        # - Used in comparison context

        for i in range(len(self.firmware) - 4):
            if self.firmware[i] == 0x02:
                # Check if nearby bytes suggest this is in code (not data)
                context = self.firmware[max(0, i-16):min(len(self.firmware), i+32)]

                # Heuristic: Gen2 hardcoded if surrounded by instruction-like bytes
                is_code_context = any(
                    b in context for b in [0x08, 0x09, 0x24, 0x25, 0x60, 0x61]
                )

                if is_code_context:
                    conf = 0.3
                    p = FalconPattern(i, 'gen2_hardcoded', context, confidence=conf)
                    patterns.append(p)

        return patterns

    def analyze_all(self) -> dict:
        """Run all pattern detectors and return summary."""
        log.info("[FALCON] Starting firmware analysis...")

        csr_refs = self.find_csr_references()
        cmp_instrs = self.find_comparison_instructions()
        branches = self.find_conditional_branches()
        gen2_vals = self.find_gen2_hardcoded_limits()

        self.patterns = csr_refs + cmp_instrs + branches + gen2_vals

        log.info(f"[FALCON] Found {len(csr_refs)} CSR 0x7ca references")
        log.info(f"[FALCON] Found {len(cmp_instrs)} comparison instructions")
        log.info(f"[FALCON] Found {len(branches)} conditional branches")
        log.info(f"[FALCON] Found {len(gen2_vals)} Gen 2 hardcoded limits")
        log.info(f"[FALCON] Total patterns: {len(self.patterns)}")

        return {
            'csr_references': csr_refs,
            'comparison_instructions': cmp_instrs,
            'conditional_branches': branches,
            'gen2_hardcoded': gen2_vals,
            'total': self.patterns,
        }

    def report_suspicious_regions(self, min_confidence=0.5, window_size=64) -> list:
        """Report firmware regions with multiple high-confidence patterns.

        Regions with clustering of fuse-check related patterns are likely targets
        for firmware patching.
        """
        regions = []

        high_conf = [p for p in self.patterns if p.confidence >= min_confidence]

        if not high_conf:
            return regions

        # Sort by offset
        high_conf.sort(key=lambda p: p.offset)

        # Find clusters
        current_region = None
        for pattern in high_conf:
            if current_region is None:
                current_region = {
                    'start': pattern.offset,
                    'patterns': [pattern],
                    'confidence_sum': pattern.confidence,
                }
            elif pattern.offset - current_region['start'] < window_size:
                # Pattern within window, add to region
                current_region['patterns'].append(pattern)
                current_region['confidence_sum'] += pattern.confidence
            else:
                # New region
                if len(current_region['patterns']) >= 2:
                    regions.append(current_region)
                current_region = {
                    'start': pattern.offset,
                    'patterns': [pattern],
                    'confidence_sum': pattern.confidence,
                }

        # Add final region
        if current_region and len(current_region['patterns']) >= 2:
            regions.append(current_region)

        return regions


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) < 2:
        print("Usage: python3 falcon_analyzer.py <gsp_firmware_path>")
        sys.exit(1)

    firmware_path = sys.argv[1]
    analyzer = FalconAnalyzer(firmware_path)
    results = analyzer.analyze_all()

    print("\n=== HIGH-CONFIDENCE PATTERN CLUSTERS ===")
    regions = analyzer.report_suspicious_regions(min_confidence=0.5)
    for i, region in enumerate(regions):
        print(f"\nRegion {i+1} @ offset 0x{region['start']:x}:")
        print(f"  Pattern count: {len(region['patterns'])}")
        print(f"  Confidence sum: {region['confidence_sum']:.1f}")
        for p in region['patterns']:
            print(f"    - {p}")

    if not regions:
        print("No high-confidence clusters found.")
        print("\nTry analyzing with Falcon disassembler (faucon/Ghidra) for better results.")
