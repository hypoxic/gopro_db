#!/usr/bin/env python3
"""
MCO eXtremeDB Page Analyzer for GoPro Databases

This module provides low-level page analysis for McObject eXtremeDB databases
as used by GoPro cameras (mdb*.db files).

Page Kind Reference (compiled from MCO 5.x, 7.x, and 8.x analysis):
============================================================================
Kind | Name                  | Description
-----|----------------------|---------------------------------------------
0    | MCO_PAGE_DATA        | Data pages - contain object records
1    | MCO_PAGE_EXTENSION   | Extension pages - overflow for large records
2    | MCO_PAGE_BTREE_LEAF  | B-tree leaf/root node pages
3    | MCO_PAGE_BTREE_NODE  | B-tree internal node pages
4    | MCO_PAGE_AUTOID_HASH | Auto OID list bucket/hash pages (MCO 7.x+)
5    | MCO_PAGE_AUTOID_OVF  | Auto OID list overflow pages (MCO 7.x+)
6    | MCO_PAGE_BLOB_HEAD   | BLOB header page
7    | MCO_PAGE_BLOB_CONT   | BLOB continuation page
8    | MCO_PAGE_INDEX_DIR   | Index directory pages (MCO 7.x+)
10   | MCO_PAGE_TRANS       | Transaction pages
11   | MCO_PAGE_FREELIST    | Free page list pages
12   | MCO_PAGE_FIXREC      | Fixed record pages (dictionary, etc.)
15   | MCO_PAGE_TEMP        | Temporary pages

Page Header Structure (8 bytes):
  Offset 0: uint8  kind       - Page type (lower 4 bits) + flags (upper 4 bits)
  Offset 1: uint8  extraflags - Additional flags
  Offset 2: uint16 user       - User data (varies by page type)
  Offset 4: uint8[4] _align   - Alignment padding

Kind Flags (upper 4 bits):
  0x10 = COMPACT     - Page is compacted
  0x20 = HAS_BLOBS   - Page contains blob references
  0x40 = FLAG_2      - Reserved
  0x80 = FLAG_3      - Reserved
"""

import struct
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple, BinaryIO
from pathlib import Path
from enum import IntEnum, IntFlag
import json


# =============================================================================
# Page Type Definitions
# =============================================================================

class MCOPageKind(IntEnum):
    """MCO eXtremeDB page kinds"""
    DATA = 0            # Object data pages
    EXTENSION = 1       # Extension/overflow pages
    BTREE_LEAF = 2      # B-tree leaf/root nodes
    BTREE_NODE = 3      # B-tree internal nodes
    AUTOID_HASH = 4     # Auto OID list hash bucket (MCO 7.x+)
    AUTOID_OVF = 5      # Auto OID list overflow (MCO 7.x+)
    BLOB_HEAD = 6       # BLOB header
    BLOB_CONT = 7       # BLOB continuation
    INDEX_DIR = 8       # Index directory (MCO 7.x+)
    HASH_OVF = 9        # Hash overflow (MCO 7.x+, tentative)
    TRANS = 10          # Transaction pages
    FREELIST = 11       # Free page list
    FIXREC = 12         # Fixed record pages
    STRING_EXT = 14     # String table extension (MCO 7.x+, tentative)
    TEMP = 15           # Temporary pages

    @classmethod
    def name_for(cls, kind: int) -> str:
        """Get descriptive name for page kind"""
        base_kind = kind & 0x0F
        names = {
            0: "DATA",
            1: "EXTENSION",
            2: "BTREE_LEAF",
            3: "BTREE_NODE",
            4: "AUTOID_HASH",
            5: "AUTOID_OVF",
            6: "BLOB_HEAD",
            7: "BLOB_CONT",
            8: "INDEX_DIR",
            9: "HASH_OVF",      # Tentative - hash table overflow
            10: "TRANS",
            11: "FREELIST",
            12: "FIXREC",
            13: "UNKNOWN_13",   # Seen in Hero11, purpose unclear
            14: "STRING_EXT",   # Tentative - string table extension
            15: "TEMP",
        }
        return names.get(base_kind, f"UNKNOWN_{base_kind}")


class MCOPageFlags(IntFlag):
    """Page kind flags (upper 4 bits of kind byte)"""
    NONE = 0x00
    COMPACT = 0x10      # Page has been compacted
    HAS_BLOBS = 0x20    # Page contains blob references
    FLAG_2 = 0x40       # Reserved
    FLAG_3 = 0x80       # Reserved


# =============================================================================
# Page Header Structure
# =============================================================================

@dataclass
class MCOPageHeader:
    """MCO page header (8 bytes)"""
    kind: int = 0           # Raw kind byte (type + flags)
    extraflags: int = 0     # Additional flags
    user: int = 0           # User data (16-bit)
    align: bytes = b'\x00' * 4  # Alignment padding

    @property
    def page_type(self) -> int:
        """Get the page type (lower 4 bits of kind)"""
        return self.kind & 0x0F

    @property
    def page_flags(self) -> int:
        """Get the page flags (upper 4 bits of kind)"""
        return self.kind & 0xF0

    @property
    def type_name(self) -> str:
        """Get descriptive name for page type"""
        return MCOPageKind.name_for(self.page_type)

    @property
    def flags_desc(self) -> List[str]:
        """Get list of flag names"""
        flags = []
        if self.page_flags & MCOPageFlags.COMPACT:
            flags.append("COMPACT")
        if self.page_flags & MCOPageFlags.HAS_BLOBS:
            flags.append("HAS_BLOBS")
        if self.page_flags & MCOPageFlags.FLAG_2:
            flags.append("FLAG_2")
        if self.page_flags & MCOPageFlags.FLAG_3:
            flags.append("FLAG_3")
        return flags

    @classmethod
    def from_bytes(cls, data: bytes) -> 'MCOPageHeader':
        """Parse page header from bytes"""
        if len(data) < 8:
            return cls()
        return cls(
            kind=data[0],
            extraflags=data[1],
            user=struct.unpack_from('<H', data, 2)[0],
            align=data[4:8]
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            'kind_raw': self.kind,
            'kind_hex': f"0x{self.kind:02X}",
            'type': self.page_type,
            'type_name': self.type_name,
            'flags': self.page_flags,
            'flags_desc': self.flags_desc,
            'extraflags': self.extraflags,
            'user': self.user,
            'align': self.align.hex(),
        }


# =============================================================================
# Page Data Structures
# =============================================================================

@dataclass
class MCOPage:
    """Represents a single MCO page"""
    offset: int = 0             # File offset
    size: int = 0               # Page size in bytes
    header: MCOPageHeader = field(default_factory=MCOPageHeader)
    data: bytes = b''           # Page data (after header)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            'offset': f"0x{self.offset:04X}",
            'offset_dec': self.offset,
            'size': self.size,
            'header': self.header.to_dict(),
            'data_preview': self.data[:64].hex() if self.data else '',
        }


@dataclass
class MCODatabaseInfo:
    """MCO database metadata from root/header pages"""
    format_version: int = 0
    mco_version_major: int = 0
    mco_version_minor: int = 0
    mco_version_build: int = 0
    page_size: int = 0
    n_pages: int = 0
    dictionary_offset: int = 0

    @property
    def mco_version(self) -> str:
        """Get formatted MCO version string"""
        return f"{self.mco_version_major}.{self.mco_version_minor}.{self.mco_version_build}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            'format_version': self.format_version,
            'mco_version': self.mco_version,
            'mco_version_major': self.mco_version_major,
            'mco_version_minor': self.mco_version_minor,
            'mco_version_build': self.mco_version_build,
            'page_size': self.page_size,
            'n_pages': self.n_pages,
            'dictionary_offset': f"0x{self.dictionary_offset:04X}" if self.dictionary_offset else None,
        }


# =============================================================================
# Page Analyzer
# =============================================================================

class MCOPageAnalyzer:
    """Analyzes MCO eXtremeDB database pages"""

    # Common page sizes in MCO databases
    COMMON_PAGE_SIZES = [256, 512, 1024, 2048, 4096]

    def __init__(self, filepath: str):
        self.filepath = Path(filepath)
        self.data: bytes = b''
        self.file_size: int = 0
        self.page_size: int = 0
        self.pages: List[MCOPage] = []
        self.db_info: MCODatabaseInfo = MCODatabaseInfo()
        self.unknowns: List[Dict[str, Any]] = []

    def analyze(self) -> bool:
        """Perform full analysis"""
        with open(self.filepath, 'rb') as f:
            self.data = f.read()
        self.file_size = len(self.data)

        if self.file_size < 0x400:
            print(f"Error: File too small ({self.file_size} bytes)")
            return False

        # Detect page size
        self._detect_page_size()

        # Parse database info from root/header
        self._parse_database_info()

        # Scan all pages
        self._scan_pages()

        # Look for unknowns
        self._identify_unknowns()

        return True

    def _detect_page_size(self):
        """Detect the page size used by this database"""
        # GoPro databases typically use 1024 byte pages
        # Score each page size by counting valid page headers

        best_ps = 1024
        best_score = 0

        for ps in self.COMMON_PAGE_SIZES:
            if self.file_size >= ps * 4:
                valid_count = 0
                invalid_count = 0

                for offset in range(ps, min(ps * 20, self.file_size), ps):
                    header = MCOPageHeader.from_bytes(self.data[offset:offset+8])
                    base_kind = header.page_type
                    raw_kind = header.kind

                    # Valid page kinds: 0-8, 10-12, 15
                    if base_kind in [0, 1, 2, 3, 4, 5, 6, 7, 8, 10, 11, 12, 15]:
                        # Check for reasonable header values
                        # Valid flags are 0x00-0xF0, user is typically small
                        if (raw_kind & 0xF0) in [0x00, 0x10, 0x20, 0x30] or raw_kind == 0xFF:
                            if header.user < 0x100 or header.user == 0xFFFF:
                                valid_count += 1
                                continue

                    # Check for 0xFF (free/temp page) or 0x00 (unused)
                    if raw_kind == 0xFF or raw_kind == 0x00:
                        valid_count += 1
                        continue

                    invalid_count += 1

                # Score = valid - invalid
                score = valid_count - invalid_count
                if score > best_score:
                    best_score = score
                    best_ps = ps

        self.page_size = best_ps
        self.db_info.page_size = self.page_size
        self.db_info.n_pages = self.file_size // self.page_size

    def _parse_database_info(self):
        """Parse database info from header/root pages"""
        # Root page is typically at offset 0 or 0x400
        # Dictionary is typically at 0xC00-0xC10 in GoPro databases

        # Look for MCO version in dictionary area (around 0x0C10 for Hero11)
        dict_offset = 0x0C10
        if self.file_size > dict_offset + 8:
            # Version is typically: major(u16), minor(u16), build(u16)
            # But encoding varies - check for valid values
            v1 = struct.unpack_from('<H', self.data, dict_offset)[0]
            v2 = struct.unpack_from('<H', self.data, dict_offset + 2)[0]
            v3 = struct.unpack_from('<H', self.data, dict_offset + 4)[0]

            # Check if this looks like a valid version
            if 1 <= v1 <= 15 and v2 <= 99 and v3 < 10000:
                self.db_info.mco_version_major = v1
                self.db_info.mco_version_minor = v2
                self.db_info.mco_version_build = v3
                self.db_info.dictionary_offset = dict_offset

        # Format version (typically in root page at offset ~0x400)
        if self.file_size > 0x410:
            fv = struct.unpack_from('<I', self.data, 0x400 + 8)[0]
            if 1 <= fv <= 10:
                self.db_info.format_version = fv

    def _scan_pages(self):
        """Scan all pages in the database"""
        offset = 0
        while offset < self.file_size:
            page_data = self.data[offset:offset + self.page_size]
            if len(page_data) < 8:
                break

            header = MCOPageHeader.from_bytes(page_data[:8])

            page = MCOPage(
                offset=offset,
                size=self.page_size,
                header=header,
                data=page_data[8:] if len(page_data) > 8 else b''
            )
            self.pages.append(page)

            offset += self.page_size

    def _identify_unknowns(self):
        """Identify unknown page types or anomalies"""
        known_kinds = {0, 1, 2, 3, 4, 5, 6, 7, 8, 10, 11, 12, 15}

        for page in self.pages:
            base_kind = page.header.page_type

            # Check for unknown page kinds
            if base_kind not in known_kinds and base_kind != 0xFF:
                self.unknowns.append({
                    'type': 'unknown_page_kind',
                    'offset': f"0x{page.offset:04X}",
                    'kind': base_kind,
                    'kind_hex': f"0x{page.header.kind:02X}",
                    'data_preview': page.data[:32].hex() if page.data else '',
                })

            # Check for unusual flags
            if page.header.page_flags & 0xC0:  # FLAG_2 or FLAG_3 set
                self.unknowns.append({
                    'type': 'unusual_flags',
                    'offset': f"0x{page.offset:04X}",
                    'flags': page.header.page_flags,
                    'flags_desc': page.header.flags_desc,
                })

    def get_page_summary(self) -> Dict[str, int]:
        """Get summary count of pages by type"""
        summary = {}
        for page in self.pages:
            type_name = page.header.type_name
            summary[type_name] = summary.get(type_name, 0) + 1
        return summary

    def get_pages_by_kind(self, kind: int) -> List[MCOPage]:
        """Get all pages of a specific kind"""
        return [p for p in self.pages if p.header.page_type == kind]

    def analyze_btree_pages(self) -> List[Dict[str, Any]]:
        """Analyze B-tree pages in detail"""
        btree_pages = []

        for page in self.pages:
            if page.header.page_type in [2, 3]:  # BTREE_LEAF or BTREE_NODE
                info = {
                    'offset': f"0x{page.offset:04X}",
                    'type': 'leaf' if page.header.page_type == 2 else 'node',
                    'user': page.header.user,
                }

                # Parse B-tree page structure
                if len(page.data) >= 8:
                    # B-tree header typically has:
                    # - n_keys (u16)
                    # - flags (u16)
                    # - key_size (u16)
                    # - page_offset (u16)
                    n_keys = struct.unpack_from('<H', page.data, 0)[0]
                    info['n_keys'] = n_keys

                btree_pages.append(info)

        return btree_pages

    def analyze_data_pages(self) -> List[Dict[str, Any]]:
        """Analyze data pages"""
        data_pages = []

        for page in self.pages:
            if page.header.page_type == 0:  # DATA page
                info = {
                    'offset': f"0x{page.offset:04X}",
                    'user': page.header.user,
                    'has_blobs': bool(page.header.page_flags & MCOPageFlags.HAS_BLOBS),
                    'is_compact': bool(page.header.page_flags & MCOPageFlags.COMPACT),
                }

                # Look for record patterns
                if len(page.data) >= 4:
                    # Data pages typically have slot table at end
                    info['first_bytes'] = page.data[:16].hex()

                data_pages.append(info)

        return data_pages

    def analyze_string_table(self) -> List[Dict[str, Any]]:
        """Analyze string/hash table pages (kind 4)"""
        string_pages = []

        for page in self.pages:
            if page.header.page_type == 4:  # AUTOID_HASH
                strings = []

                # Extract strings from the page
                current = []
                for b in page.data:
                    if 32 <= b < 127:
                        current.append(chr(b))
                    else:
                        if current and len(current) >= 3:
                            strings.append(''.join(current))
                        current = []

                if current and len(current) >= 3:
                    strings.append(''.join(current))

                string_pages.append({
                    'offset': f"0x{page.offset:04X}",
                    'user': page.header.user,
                    'strings': strings[:50],  # Limit to first 50
                    'total_strings': len(strings),
                })

        return string_pages

    def to_dict(self) -> Dict[str, Any]:
        """Convert analysis to dictionary"""
        return {
            'file_path': str(self.filepath),
            'file_size': self.file_size,
            'database_info': self.db_info.to_dict(),
            'page_size': self.page_size,
            'total_pages': len(self.pages),
            'page_summary': self.get_page_summary(),
            'pages': [p.to_dict() for p in self.pages],
            'unknowns': self.unknowns,
        }

    def to_json(self, indent: int = 2) -> str:
        """Convert analysis to JSON"""
        return json.dumps(self.to_dict(), indent=indent, default=str)

    def print_summary(self):
        """Print human-readable summary"""
        print("=" * 60)
        print("MCO eXtremeDB Page Analysis")
        print("=" * 60)
        print(f"File: {self.filepath}")
        print(f"Size: {self.file_size} bytes ({self.file_size / 1024:.1f} KB)")
        print(f"Page size: {self.page_size} bytes")
        print(f"Total pages: {len(self.pages)}")
        print()

        print("Database Info:")
        print(f"  MCO Version: {self.db_info.mco_version}")
        print(f"  Format Version: {self.db_info.format_version}")
        if self.db_info.dictionary_offset:
            print(f"  Dictionary offset: 0x{self.db_info.dictionary_offset:04X}")
        print()

        print("Page Type Summary:")
        summary = self.get_page_summary()
        for type_name, count in sorted(summary.items()):
            print(f"  {type_name:20} : {count:4}")
        print()

        print("Pages by Offset:")
        for page in self.pages:
            flags_str = ','.join(page.header.flags_desc) if page.header.flags_desc else '-'
            print(f"  0x{page.offset:04X}: {page.header.type_name:15} "
                  f"kind=0x{page.header.kind:02X} user={page.header.user:4} flags={flags_str}")
        print()

        # String table analysis
        string_pages = self.analyze_string_table()
        if string_pages:
            print("String Table Pages (kind=4):")
            for sp in string_pages:
                print(f"  {sp['offset']}: {sp['total_strings']} strings")
                for s in sp['strings'][:10]:
                    print(f"    - {s}")
                if sp['total_strings'] > 10:
                    print(f"    ... and {sp['total_strings'] - 10} more")
            print()

        if self.unknowns:
            print("UNKNOWNS:")
            for unk in self.unknowns:
                print(f"  {unk['type']} at {unk.get('offset', '?')}")
                for k, v in unk.items():
                    if k not in ['type', 'offset']:
                        print(f"    {k}: {v}")
            print()
        else:
            print("No unknown page types or anomalies found.")
        print()


# =============================================================================
# Page Kind Documentation
# =============================================================================

PAGE_KIND_DOCS = """
MCO eXtremeDB Page Kinds - Comprehensive Reference
===================================================

This documentation covers page kinds found in MCO eXtremeDB databases
as used by GoPro cameras (Hero5 through Hero11+).

Version History:
- MCO 5.0.1784: Hero5 firmware (kinds 0-3, 6-7, 10-12, 15)
- MCO 7.1.1793: Hero11 firmware (adds kinds 4, 5, 8)
- MCO 8.1.1800: eXtremeDB sample app (similar to 7.x)

Page Header Structure (8 bytes):
--------------------------------
struct mco_page_header_t_ {
    uint8_t  kind;       // Page type (lower 4 bits) + flags (upper 4 bits)
    uint8_t  extraflags; // Additional flags
    uint16_t user;       // User data (varies by page type)
    uint8_t  _align[4];  // Alignment padding
};

Page Kinds:
-----------

KIND 0: MCO_PAGE_DATA (Data Pages)
  Purpose: Store object/record data
  User field: Object type/class code
  Structure:
    - Records stored contiguously
    - Slot table at page end (grows backward)
    - Each slot points to a record

KIND 1: MCO_PAGE_EXTENSION (Extension Pages)
  Purpose: Overflow for large records that span pages
  User field: Link to parent data page
  Structure:
    - Continuation of record data
    - Linked list via header fields

KIND 2: MCO_PAGE_BTREE_LEAF (B-tree Leaf/Root)
  Purpose: B-tree index leaf nodes
  User field: Index identifier
  Structure:
    - Key entries
    - Pointers to data records
    - May be root node for small indexes

KIND 3: MCO_PAGE_BTREE_NODE (B-tree Internal Node)
  Purpose: B-tree index internal nodes
  User field: Index identifier
  Structure:
    - Key entries
    - Child page pointers
    - Used for multi-level indexes

KIND 4: MCO_PAGE_AUTOID_HASH (Auto OID Hash Bucket) [MCO 7.x+]
  Purpose: Hash table buckets for auto OID list
  User field: Hash table identifier
  Structure:
    - Hash bucket entries
    - String keys (field names in GoPro DBs)
    - Used for name lookups

KIND 5: MCO_PAGE_AUTOID_OVF (Auto OID Overflow) [MCO 7.x+]
  Purpose: Overflow pages for auto OID hash buckets
  User field: Link to parent bucket
  Structure:
    - Continuation of hash entries
    - Created when bucket overflows

KIND 6: MCO_PAGE_BLOB_HEAD (BLOB Header)
  Purpose: BLOB data header page
  User field: BLOB identifier
  Structure:
    - BLOB size and metadata
    - First chunk of BLOB data
    - Links to continuation pages

KIND 7: MCO_PAGE_BLOB_CONT (BLOB Continuation)
  Purpose: BLOB data continuation
  User field: Link to BLOB header
  Structure:
    - Continuation of BLOB data
    - Linked list to next chunk

KIND 8: MCO_PAGE_INDEX_DIR (Index Directory) [MCO 7.x+]
  Purpose: Index directory/catalog
  User field: Index type indicator
  Structure:
    - Index entries with class codes
    - Pointers to index pages
    - Schema information

KIND 10: MCO_PAGE_TRANS (Transaction Pages)
  Purpose: Transaction logging
  User field: Transaction ID
  Structure:
    - Transaction records
    - Undo/redo information

KIND 11: MCO_PAGE_FREELIST (Free Page List)
  Purpose: Track free pages for allocation
  User field: Count or generation
  Structure:
    - Bitmap or list of free page numbers
    - Used by page manager

KIND 12: MCO_PAGE_FIXREC (Fixed Record Pages)
  Purpose: Fixed-size record storage (dictionary, etc.)
  User field: Record type
  Structure:
    - Fixed-size slots
    - Used for schema/dictionary data

KIND 15: MCO_PAGE_TEMP (Temporary Pages)
  Purpose: Temporary/scratch space
  User field: Varies
  Structure:
    - Temporary data during operations
    - May be reused freely

Page Flags (upper 4 bits of kind byte):
---------------------------------------
0x10 COMPACT    - Page has been compacted (no holes)
0x20 HAS_BLOBS  - Page contains BLOB references
0x40 FLAG_2     - Reserved/internal use
0x80 FLAG_3     - Reserved/internal use

GoPro Database Layout:
----------------------
0x0000-0x03FF: File header (magic, metadata)
0x0400-0x0BFF: Root page / configuration
0x0C00-0x0FFF: Dictionary (schema)
0x1000+:       Data pages, indexes, records

Typical GoPro mdb.db file:
- Page size: 1024 bytes
- Tables: mdb_global, mdb_single, mdb_single_ex, mdb_grouped_ex
- Indexes: B-tree on file_handle, directory/group numbers
"""


def print_documentation():
    """Print the page kind documentation"""
    print(PAGE_KIND_DOCS)


# =============================================================================
# Main
# =============================================================================

def main():
    import sys

    if len(sys.argv) < 2:
        print("MCO eXtremeDB Page Analyzer")
        print("=" * 40)
        print()
        print("Analyzes the page structure of McObject eXtremeDB databases")
        print("as used by GoPro cameras (mdb*.db files).")
        print()
        print("Usage: python mco_page_analyzer.py <path_to_mdb.db> [options]")
        print()
        print("Options:")
        print("  (default)    Print page analysis summary")
        print("  --json       Output as JSON")
        print("  --pages      List all pages with details")
        print("  --docs       Print page kind documentation")
        print("  --btree      Analyze B-tree pages")
        print("  --strings    Analyze string table pages")
        print()
        print("Examples:")
        print("  python mco_page_analyzer.py mdb11.db")
        print("  python mco_page_analyzer.py mdb11.db --json > pages.json")
        print("  python mco_page_analyzer.py --docs")
        sys.exit(1)

    if '--docs' in sys.argv:
        print_documentation()
        sys.exit(0)

    filepath = sys.argv[1]

    analyzer = MCOPageAnalyzer(filepath)
    if not analyzer.analyze():
        print("Failed to analyze database")
        sys.exit(1)

    if '--json' in sys.argv:
        print(analyzer.to_json())
    elif '--pages' in sys.argv:
        for page in analyzer.pages:
            print(f"Page at 0x{page.offset:04X}:")
            for k, v in page.to_dict().items():
                if k != 'data_preview':
                    print(f"  {k}: {v}")
            print()
    elif '--btree' in sys.argv:
        btree = analyzer.analyze_btree_pages()
        print(f"B-tree pages: {len(btree)}")
        for b in btree:
            print(f"  {b}")
    elif '--strings' in sys.argv:
        strings = analyzer.analyze_string_table()
        print(f"String table pages: {len(strings)}")
        for sp in strings:
            print(f"\n{sp['offset']} ({sp['total_strings']} strings):")
            for s in sp['strings']:
                print(f"  {s}")
    else:
        analyzer.print_summary()


if __name__ == '__main__':
    main()
