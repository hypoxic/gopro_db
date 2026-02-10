#!/usr/bin/env python3
"""
GoPro Media Database (mdb*.db) Parser

Parses the proprietary GoPro media database format used by HERO cameras
to index and track media files on the SD card.

Based on McObject eXtremeDB schema extracted from GoPro firmware.
"""

import struct
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple
from pathlib import Path
from enum import IntEnum
import json


# =============================================================================
# Type Definitions (from MCO schema)
# =============================================================================

class FieldType(IntEnum):
    """MCO field types"""
    UINT8 = 0x01
    UINT16 = 0x02
    UINT32 = 0x03
    ENUM = 0x06       # 4-byte enum
    UINT64 = 0x0C
    AUTOID = 0x0E     # 8-byte auto-increment ID
    INDICATOR = 0x17  # 1-byte indicator for optional fields
    STRUCT = 0x32     # Embedded struct


class FieldFlags(IntEnum):
    """MCO field flags"""
    NONE = 0x00
    INDEXED = 0x08    # Part of an index
    OPTIONAL = 0x20   # Has an indicator field
    IS_INDICATOR = 0x40  # This field IS an indicator
    ARRAY = 0x02      # Fixed-size array


# =============================================================================
# Struct Definitions
# =============================================================================

@dataclass
class TagEntry:
    """HiLight tag entry (struct 0) - 5 bytes"""
    time_code: int = 0    # u32 @ offset 0
    tag_indx: int = 0     # u8  @ offset 4


@dataclass
class DateField:
    """Date field (struct 1) - 4 bytes

    Note: Year is stored as offset from 1980 (like FAT filesystem)
    """
    year: int = 0         # u16 @ offset 0 (offset from 1980)
    month: int = 0        # u8  @ offset 2
    day: int = 0          # u8  @ offset 3

    @property
    def actual_year(self) -> int:
        """Get the actual year (year + 1980)"""
        return self.year + 1980 if self.year > 0 else 0


@dataclass
class TimeField:
    """Time field (struct 2) - 3 bytes"""
    min: int = 0          # u8  @ offset 0
    hour: int = 0         # u8  @ offset 1
    second: int = 0       # u8  @ offset 2


@dataclass
class DateTime:
    """DateTime composite (struct 3) - 7 bytes"""
    dt: DateField = field(default_factory=DateField)  # @ offset 0
    tm: TimeField = field(default_factory=TimeField)  # @ offset 4


@dataclass
class DbVersion:
    """Database version (struct 4) - 8 bytes"""
    minor: int = 0        # u32 @ offset 0
    major: int = 0        # u32 @ offset 4


@dataclass
class MCOVersion:
    """MCO eXtremeDB engine version"""
    major: int = 0        # e.g., 5 for Hero5, 7 for Hero11
    minor: int = 0        # e.g., 0 or 1
    build: int = 0        # e.g., 1784, 1793

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.build}"


@dataclass
class MdbGlobal:
    """mdb_global table record (struct 5) - 23 bytes

    Global database metadata, typically one record.
    """
    version: DbVersion = field(default_factory=DbVersion)  # @ offset 0 (8 bytes)
    autoid: int = 0                   # u64 @ offset 8
    last_db_scan_time: DateTime = field(default_factory=DateTime)  # @ offset 16 (7 bytes)


@dataclass
class MdbSingle:
    """mdb_single table record (struct 6) - 9 bytes

    Basic file handle lookup table.
    """
    file_handle: int = 0              # u64 @ offset 0
    file_scanned: int = 0             # u8  @ offset 8


def decode_file_handle(fh: int) -> dict:
    """Decode a GoPro file handle into its components.

    File handle structure (8 bytes):
    - Byte 7: Type/flags (meaning not fully understood)
    - Byte 4: Directory number (e.g., 100 for 100GOPRO)
    - Bytes 0-1: File number within directory

    GoPro filename format: {PREFIX}{CHAPTER}{FILENUMBER}.{EXT}
    - PREFIX: GX (HEVC), GH (H.264), GP (photo), GL (LRV), etc.
    - CHAPTER: 0-9 (single digit chapter number, 0 for first/only)
    - FILENUMBER: 0001-9999

    Note: The prefix (GX vs GH) cannot be reliably determined from the
    file handle alone - it depends on codec settings at recording time.
    We default to GX here but the actual file may use GH or other prefixes.

    Returns dict with directory, file_number, and estimated filename.
    """
    dir_no = (fh >> 32) & 0xFF
    file_no = fh & 0xFFFF
    type_flag = (fh >> 56) & 0xFF

    # Default to GX prefix (HEVC) - actual prefix may vary
    # GoPro prefixes: GX=HEVC, GH=H.264, GP=photo, GL=LRV, GF=fused
    prefix = "GX"
    chapter = 0  # Chapter number (single digit after prefix)
    filename = f"{prefix}{chapter}{file_no:04d}.MP4"

    return {
        'directory': f"{dir_no:03d}GOPRO",
        'file_number': file_no,
        'type_flag': type_flag,
        'estimated_path': f"{dir_no:03d}GOPRO/{filename}"
    }


@dataclass
class MdbSingleEx:
    """mdb_single_ex table record (struct 7) - 76/78 bytes (Hero5) or larger (Hero11+)

    Extended file metadata with all video/photo properties.
    Note: Hero11 adds many more fields (camera_model, GPS, moments, etc.)
    """
    # Core fields (Hero5 layout)
    file_type_ex: int = 0             # u32 @ offset 0
    duration: int = 0                 # u64 @ offset 4
    size: int = 0                     # u64 @ offset 12
    upload_status: int = 0            # u32 @ offset 20
    file_handle: int = 0              # u64 @ offset 24
    vtag: int = 0                     # struct @ offset 32 (tag_entry)
    tag_cnt: int = 0                  # u16 @ offset 36
    ctm: DateTime = field(default_factory=DateTime)  # @ offset 38 (7 bytes)
    last_scan_time: DateTime = field(default_factory=DateTime)  # @ offset 46 (7 bytes)
    chp_cnt: int = 0                  # u16 @ offset 54
    latm: DateTime = field(default_factory=DateTime)  # @ offset 56 (7 bytes)
    protune_option: int = 0           # u8  @ offset 63
    aud_option: int = 0               # u8  @ offset 64
    protune_option_indicator: int = 0 # u8  @ offset 65
    has_eis: int = 0                  # u8  @ offset 66
    is_clip: int = 0                  # u8  @ offset 67
    vtag_indicator: int = 0           # u8  @ offset 68
    avc_level: int = 0                # u8  @ offset 69
    f_meta_present: int = 0           # u8  @ offset 70
    chp_cnt_indicator: int = 0        # u8  @ offset 71
    has_eis_indicator: int = 0        # u8  @ offset 72
    aud_option_indicator: int = 0     # u8  @ offset 73
    file_scanned: int = 0             # u8  @ offset 74
    avc_profile: int = 0              # u8  @ offset 75

    # Hero11+ additional fields
    camera_model: str = ""
    sub_model: str = ""  # e.g., "Black", "Black Mini" - at u_offset ~128
    dir_no: int = 0
    grp_no: int = 0
    width: int = 0
    height: int = 0
    projection: int = 0
    lens_config: int = 0
    moment_cnt: int = 0
    total_tag_cnt: int = 0
    max_moment_score: float = 0.0
    media_orientation: int = 0
    media_status: int = 0
    has_hdr: int = 0
    fov: int = 0
    f_meta_present: int = 0
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    altitude: Optional[float] = None


@dataclass
class GusiBlob:
    """GUSI (GoPro Unique Segment Identifier) blob - 16 bytes

    Contains camera/session identification data. This blob is typically
    identical across all videos from the same recording session.

    Structure (16 bytes):
    - Bytes 0-3: Session ID (little-endian u32)
    - Bytes 4-7: Reserved (zeros)
    - Bytes 8-11: Camera/recording ID (little-endian u32)
    - Bytes 12-15: Reserved (zeros)
    """
    raw: bytes = b''                  # Raw 16 bytes
    session_id: int = 0               # Session identifier (bytes 0-3)
    recording_id: int = 0             # Camera/recording identifier (bytes 8-11)


@dataclass
class ContentBlob:
    """Content identification blob - 16 bytes

    Contains unique content identifier per video file. This appears to be
    a 128-bit unique ID (possibly a UUID or content hash) that uniquely
    identifies each video file.

    Structure (16 bytes):
    - Bytes 0-7: High 64 bits of content ID
    - Bytes 8-15: Low 64 bits of content ID
    """
    raw: bytes = b''                  # Raw 16 bytes
    content_id_high: int = 0          # High 64 bits of content ID (bytes 0-7)
    content_id_low: int = 0           # Low 64 bits of content ID (bytes 8-15)


@dataclass
class MdbGroupedEx:
    """mdb_grouped_ex table record (struct 8) - 73 bytes (Hero11)

    Video group information (chapters, timelapse sequences).

    Field layout (u_offset):
    - 0: file_handle (u64)
    - 8: frame_rate_timescale (u32)
    - 12: frame_rate_duration (u32)
    - 16: n_elems (u32)
    - 20: grp_ctm (date_time, 7 bytes)
    - 28: grp_no (u16)
    - 30: width (u16)
    - 32: height (u16)
    - 34: frame_rate_duration_indicator (u8)
    - 35: frame_rate_timescale_indicator (u8)
    - 36: gusi_blob (u8[16])
    - 52: f_is_subsample (u8)
    - 53: f_is_progressive (u8)
    - 54: f_is_progressive_indicator (u8)
    - 55: grp_no_indicator (u8)
    - 56: f_is_subsample_indicator (u8)
    - 57: blob (u8[16])
    """
    file_handle: int = 0              # u64 @ u_offset 0
    frame_rate_timescale: int = 0     # u32 @ u_offset 8
    frame_rate_duration: int = 0      # u32 @ u_offset 12
    n_elems: int = 0                  # u32 @ u_offset 16
    grp_ctm: DateTime = field(default_factory=DateTime)  # @ u_offset 20 (7 bytes)
    grp_no: int = 0                   # u16 @ u_offset 28
    width: int = 0                    # u16 @ u_offset 30
    height: int = 0                   # u16 @ u_offset 32
    frame_rate_duration_indicator: int = 0   # u8 @ u_offset 34
    frame_rate_timescale_indicator: int = 0  # u8 @ u_offset 35
    gusi_blob: GusiBlob = field(default_factory=GusiBlob)  # u8[16] @ u_offset 36
    f_is_subsample: int = 0           # u8 @ u_offset 52
    f_is_progressive: int = 0         # u8 @ u_offset 53
    f_is_progressive_indicator: int = 0  # u8 @ u_offset 54
    grp_no_indicator: int = 0         # u8 @ u_offset 55
    f_is_subsample_indicator: int = 0 # u8 @ u_offset 56
    blob: ContentBlob = field(default_factory=ContentBlob)  # u8[16] @ u_offset 57


# =============================================================================
# Constants
# =============================================================================

HEADER_SIZE = 0x400  # 1KB header block
SENTINEL = 0xFFFFFFFF
NULL_MARKER = 0xFFFF

# File type values (file_type_ex field)
# Hero11+ uses different values - appears to be a bitmap/flags field
FILE_TYPES = {
    0: "Unknown",
    1: "Video",
    2: "Photo",
    3: "Timelapse",
    4: "Burst",
    5: "Audio",
    # Hero11+ extended types (bit flags)
    0x1000: "Video",            # 4096 - seen in Hero11/12 video files
    0x1100: "Timelapse",        # Estimated
    0x1200: "Photo",            # Estimated
}


# =============================================================================
# Parser Implementation
# =============================================================================

@dataclass
class RecordHeader:
    """Record header structure (16 bytes)"""
    table_id: int = 0       # u16 - table type (3=single_ex, 4=grouped_ex)
    flags: int = 0          # u16 - flags
    size: int = 0           # u32 - record size in bytes
    next_ptr: int = 0       # u64 - pointer to next record or self

    @property
    def table_name(self) -> str:
        table_map = {
            1: "mdb_global",
            2: "mdb_single",
            3: "mdb_single_ex",
            4: "mdb_grouped_ex",
        }
        return table_map.get(self.table_id, f"unknown_{self.table_id}")


class GoproMDBParser:
    """Parser for GoPro Media Database files"""

    # Record sizes by table and schema version
    RECORD_SIZES = {
        'hero5': {
            'mdb_global': 23,
            'mdb_single': 9,
            'mdb_single_ex': 78,
            'mdb_grouped_ex': 57,
        },
        'hero11+': {
            'mdb_global': 23,
            'mdb_single': 9,
            'mdb_single_ex': 134,  # 0x86
            'mdb_grouped_ex': 73,  # 0x49
        }
    }

    def __init__(self, filepath: str):
        self.filepath = Path(filepath)
        self.data: bytes = b''
        self.file_size: int = 0

        # Parsed data
        self.header_valid: bool = False
        self.db_version: DbVersion = DbVersion()
        self.mco_version: MCOVersion = MCOVersion()
        self.globals: List[MdbGlobal] = []
        self.singles: List[MdbSingle] = []
        self.singles_ex: List[MdbSingleEx] = []
        self.grouped_ex: List[MdbGroupedEx] = []

        # Raw records for debugging
        self.raw_records: List[Tuple[RecordHeader, bytes]] = []

        # Schema detection
        self.schema_version: str = "unknown"
        self.struct_sizes: Dict[str, int] = {}

        # Page info
        self.page_size: int = 0
        self.dictionary_offset: int = 0

    def parse(self) -> bool:
        """Parse the database file"""
        with open(self.filepath, 'rb') as f:
            self.data = f.read()

        self.file_size = len(self.data)

        if self.file_size < HEADER_SIZE + 0x100:
            print(f"Error: File too small ({self.file_size} bytes)")
            return False

        # Parse and validate header
        self._parse_header()

        # Detect schema version based on file contents
        self._detect_schema()

        # Parse metadata/config block
        self._parse_config_block()

        # Find and parse data records
        self._find_and_parse_records()

        return True

    def _parse_header(self):
        """Parse and validate the file header"""
        # Expected header: 00 FF FF FF FF FF FF FF FF FF FF 07 FF FF FF FF
        expected_magic = bytes([0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
                               0xFF, 0xFF, 0xFF, 0x07])

        self.header_valid = self.data[:12] == expected_magic

    def _detect_schema(self):
        """Detect schema version based on field names present"""
        # Look for Hero11+ specific fields
        if b'camera_model' in self.data:
            if b'vmoment' in self.data:
                self.schema_version = "hero11+"
            else:
                self.schema_version = "hero9-10"
        elif b'vtag' in self.data:
            self.schema_version = "hero5-8"
        else:
            self.schema_version = "legacy"

        # Estimate struct sizes based on schema
        if self.schema_version == "hero11+":
            self.struct_sizes = {
                'mdb_single_ex': 134,  # Larger with camera_model, GPS, etc.
                'mdb_grouped_ex': 73,
            }
        else:
            self.struct_sizes = {
                'mdb_single_ex': 78,
                'mdb_grouped_ex': 57,
            }

    def _parse_config_block(self):
        """Parse the configuration block at offset 0x400"""
        config_data = self.data[HEADER_SIZE:HEADER_SIZE + 0x100]

        # Version info appears to be at the start
        # Format: version_major (u16), version_minor (u16), ...
        if len(config_data) >= 8:
            self.db_version.major = self._read_u16(HEADER_SIZE)
            self.db_version.minor = self._read_u16(HEADER_SIZE + 2)

        # Detect page size from page header patterns
        self._detect_page_size()

        # Extract MCO version from dictionary area
        self._parse_mco_version()

    def _detect_page_size(self):
        """Detect page size from valid page headers"""
        # Try common page sizes, prefer 512 for GoPro databases
        for ps in [512, 1024, 256, 2048]:
            if self.file_size >= ps * 4:
                valid = 0
                for offset in range(ps, min(ps * 16, self.file_size), ps):
                    kind = self.data[offset] & 0x0F
                    if kind in [0, 1, 2, 3, 4, 5, 6, 7, 8, 10, 11, 12, 15]:
                        user = self._read_u16(offset + 2)
                        if user < 0x100 or user == 0xFFFF:
                            valid += 1
                if valid >= 3:
                    self.page_size = ps
                    break
        if self.page_size == 0:
            self.page_size = 512  # Default for GoPro

    def _parse_mco_version(self):
        """Extract MCO eXtremeDB version from dictionary area"""
        # Dictionary typically at 0x0C00-0x1000 region
        # MCO version stored as: major(u8/u16), minor(u8/u16), build(u16)

        # Search for dictionary by looking for string table page (kind=4)
        for offset in range(0x800, min(0x3000, self.file_size), self.page_size):
            if offset < len(self.data):
                kind = self.data[offset] & 0x0F
                if kind == 4:  # AUTOID_HASH / string table
                    # Dictionary is typically a few pages before string table
                    self.dictionary_offset = max(0x0C00, offset - 0x1400)
                    break

        if self.dictionary_offset == 0:
            self.dictionary_offset = 0x0C10  # Default for GoPro

        # Try to extract MCO version from dictionary area
        # Format varies: look for valid version patterns
        dict_area = self.dictionary_offset

        # Check at 0x0C10 (common Hero11 location)
        if self.file_size > 0x0C16:
            v1 = self._read_u16(0x0C10)
            v2 = self._read_u16(0x0C12)
            v3 = self._read_u16(0x0C14)

            # Valid MCO version: major 1-15, minor 0-99, build < 10000
            if 1 <= v1 <= 15 and v2 <= 99 and v3 < 10000:
                self.mco_version.major = v1
                self.mco_version.minor = v2
                self.mco_version.build = v3
                self.dictionary_offset = 0x0C10
                return

        # Alternative: search for version pattern in dictionary region
        for offset in range(0x0C00, min(0x1000, self.file_size - 6), 2):
            v1 = self._read_u16(offset)
            v2 = self._read_u16(offset + 2)
            v3 = self._read_u16(offset + 4)

            if 5 <= v1 <= 10 and v2 <= 10 and 1000 <= v3 < 3000:
                self.mco_version.major = v1
                self.mco_version.minor = v2
                self.mco_version.build = v3
                self.dictionary_offset = offset
                return

    def _find_and_parse_records(self):
        """Find and parse data records from the database"""
        # Find table name locations to help locate data
        table_locs = self._find_table_locations()

        # Scan for records using the header pattern
        self._scan_for_records()

        # Parse records by type
        for header, data in self.raw_records:
            if header.table_id == 3:  # mdb_single_ex
                record = self._parse_single_ex_data(data)
                if record:
                    self.singles_ex.append(record)
            elif header.table_id == 4:  # mdb_grouped_ex
                record = self._parse_grouped_ex_data(data)
                if record:
                    self.grouped_ex.append(record)

    def _scan_for_records(self):
        """Scan for records in 128-byte slots.

        Record slot structure (128 bytes):
        - Bytes 0-7: Slot header (kind=0, user=table_id, align=record_size)
        - Bytes 8-15: Pointer/padding
        - Bytes 16+: Record data

        Table IDs: 1=mdb_global, 2=mdb_single, 3=mdb_single_ex, 4=mdb_grouped_ex
        """
        SLOT_SIZE = 128  # Records are in 128-byte slots

        # Expected sizes based on schema
        expected_sizes = self.RECORD_SIZES.get(
            'hero11+' if self.schema_version == 'hero11+' else 'hero5',
            self.RECORD_SIZES['hero11+']
        )

        # Scan the data region (typically 0x2C00 onwards) in 128-byte increments
        for slot_start in range(0x2C00, self.file_size - SLOT_SIZE, SLOT_SIZE):
            # Read slot header
            kind = self.data[slot_start] & 0x0F
            table_id = self._read_u16(slot_start + 2)
            record_size = self._read_u32(slot_start + 4)

            # Valid record slot: kind=0 (DATA), table_id 3 or 4, reasonable size
            if kind == 0 and table_id in [3, 4] and 40 < record_size < 200:
                # Check if size matches expected for this table
                expected = expected_sizes.get(
                    'mdb_single_ex' if table_id == 3 else 'mdb_grouped_ex', 0
                )

                if abs(record_size - expected) < 20:  # Allow some variance
                    header = RecordHeader(
                        table_id=table_id,
                        flags=0,
                        size=record_size,
                        next_ptr=0
                    )

                    # Record data starts at offset 16 within slot (after header + pointer)
                    # For mdb_single_ex, actual data starts at offset 24 (skip 8-byte padding)
                    data_start = slot_start + 16
                    # Read enough data for the record plus any overflow
                    extended_end = min(slot_start + SLOT_SIZE + 64, self.file_size)
                    record_data = self.data[data_start:extended_end]
                    self.raw_records.append((header, record_data))

    def _parse_single_ex_data(self, data: bytes) -> Optional[MdbSingleEx]:
        """Parse mdb_single_ex record data using schema-defined offsets.

        Hero11 mdb_single_ex: 134 bytes (u_size) + 8 byte OID prefix = 142 bytes raw

        On-disk layout: [8-byte OID][134-byte record data]
        Actual offset in raw data = u_offset + 8 (for OID prefix)

        Schema from hero11_mco/fields_layoutB.csv:
        - duration: u_offset=0 (u64) → raw 8
        - size: u_offset=8 (u64) → raw 16
        - file_handle: u_offset=16 (u64) → raw 24
        - media_status: u_offset=24 (u32) → raw 32
        - vmoment: u_offset=28 (struct) → raw 36
        - file_type_ex: u_offset=36 (enum/u32) → raw 44
        - max_moment_score: u_offset=40 (f32) → raw 48
        - vtag: u_offset=44 (struct) → raw 52
        - moment_cnt: u_offset=50 (u16) → raw 58
        - ctm: u_offset=52 (date_time, 7 bytes) → raw 60
        - tag_cnt: u_offset=60 (u16) → raw 68
        - chp_cnt: u_offset=62 (u16) → raw 70
        - grp_no: u_offset=64 (u16) → raw 72
        - latm: u_offset=66 (date_time) → raw 74
        - total_tag_cnt: u_offset=74 (u16) → raw 82
        - dir_no: u_offset=76 (u16) → raw 84
        - last_scan_time: u_offset=78 (date_time) → raw 86
        - has_hdr: u_offset=85 (u8) → raw 93
        - is_clip: u_offset=86 (u8) → raw 94
        - file_scanned: u_offset=87 (u8) → raw 95
        - avc_level: u_offset=88 (u8) → raw 96
        - avc_profile: u_offset=89 (u8) → raw 97
        - protune_option: u_offset=90 (u8) → raw 98
        - aud_option: u_offset=91 (u8) → raw 99
        - has_eis: u_offset=92 (u8) → raw 100
        - f_meta_present: u_offset=93 (u8) → raw 101
        - projection: u_offset=94 (u8) → raw 102
        - lens_config: u_offset=96 (u8) → raw 104
        - camera_model: u_offset=97 (char[30]) → raw 105
        - fov: u_offset=129 (u8) → raw 137
        - media_orientation: u_offset=133 (u8) → raw 141
        """
        OID_SIZE = 8  # OID prefix before record data

        if len(data) < 100:  # Minimum viable record size
            return None

        record = MdbSingleEx()

        # Helper to safely read from buffer with OID offset
        def read_u8(u_offset: int) -> int:
            off = u_offset + OID_SIZE
            return data[off] if off < len(data) else 0

        def read_u16(u_offset: int) -> int:
            off = u_offset + OID_SIZE
            if off + 2 <= len(data):
                return struct.unpack_from('<H', data, off)[0]
            return 0

        def read_u32(u_offset: int) -> int:
            off = u_offset + OID_SIZE
            if off + 4 <= len(data):
                return struct.unpack_from('<I', data, off)[0]
            return 0

        def read_u64(u_offset: int) -> int:
            off = u_offset + OID_SIZE
            if off + 8 <= len(data):
                return struct.unpack_from('<Q', data, off)[0]
            return 0

        def read_f32(u_offset: int) -> float:
            off = u_offset + OID_SIZE
            if off + 4 <= len(data):
                return struct.unpack_from('<f', data, off)[0]
            return 0.0

        def read_datetime(u_offset: int) -> DateTime:
            off = u_offset + OID_SIZE
            return self._read_datetime_from_buffer(data, off)

        def read_string(u_offset: int, max_len: int, min_segment_len: int = 1) -> str:
            """Read string, handling GoPro's split format (e.g., 'HERO11 ...Black')

            Args:
                min_segment_len: Minimum length for a segment to be included (filters noise)
            """
            off = u_offset + OID_SIZE
            if off + max_len > len(data):
                max_len = len(data) - off
            if max_len <= 0:
                return ""

            # Extract all printable ASCII segments from the buffer
            raw = data[off:off + max_len]
            segments = []
            current = []
            for b in raw:
                if 32 <= b < 127:  # printable ASCII
                    current.append(chr(b))
                else:
                    if current:
                        seg = ''.join(current).strip()
                        if len(seg) >= min_segment_len:
                            segments.append(seg)
                        current = []
            if current:
                seg = ''.join(current).strip()
                if len(seg) >= min_segment_len:
                    segments.append(seg)

            # Filter out empty segments and join
            return ' '.join(s for s in segments if s)

        # Parse all fields using schema u_offsets
        record.duration = read_u64(0)           # u_offset=0
        record.size = read_u64(8)               # u_offset=8
        record.file_handle = read_u64(16)       # u_offset=16
        record.media_status = read_u32(24)      # u_offset=24
        # vmoment at u_offset=28 (struct, skip for now)
        record.file_type_ex = read_u32(36)      # u_offset=36
        record.max_moment_score = read_f32(40)  # u_offset=40
        # vtag at u_offset=44 (struct, skip for now)
        record.moment_cnt = read_u16(50)        # u_offset=50
        record.ctm = read_datetime(52)          # u_offset=52
        record.tag_cnt = read_u16(60)           # u_offset=60
        record.chp_cnt = read_u16(62)           # u_offset=62
        record.grp_no = read_u16(64)            # u_offset=64
        record.latm = read_datetime(66)         # u_offset=66
        record.total_tag_cnt = read_u16(74)     # u_offset=74
        record.dir_no = read_u16(76)            # u_offset=76
        record.last_scan_time = read_datetime(78)  # u_offset=78
        record.has_hdr = read_u8(85)            # u_offset=85
        record.is_clip = read_u8(86)            # u_offset=86
        record.file_scanned = read_u8(87)       # u_offset=87
        record.avc_level = read_u8(88)          # u_offset=88
        record.avc_profile = read_u8(89)        # u_offset=89
        record.protune_option = read_u8(90)     # u_offset=90
        record.aud_option = read_u8(91)         # u_offset=91
        record.has_eis = read_u8(92)            # u_offset=92
        record.f_meta_present = read_u8(93)     # u_offset=93
        record.projection = read_u8(94)         # u_offset=94
        record.lens_config = read_u8(96)        # u_offset=96
        # camera_model: filter out single-char noise (like hw revision codes)
        record.camera_model = read_string(97, 30, min_segment_len=2)  # u_offset=97, 30 bytes

        # Sub-model field (e.g., "Black", "Black Mini") - appears at u_offset ~128
        # This may overlap with indicator fields in some schemas
        record.sub_model = read_string(128, 16, min_segment_len=2)  # Read up to 16 bytes

        return record

    def _read_datetime_from_buffer(self, data: bytes, offset: int) -> DateTime:
        """Read a DateTime struct (7 bytes) from a buffer"""
        dt = DateTime()
        if offset + 7 <= len(data):
            dt.dt.year = struct.unpack_from('<H', data, offset)[0]
            dt.dt.month = data[offset + 2]
            dt.dt.day = data[offset + 3]
            dt.tm.min = data[offset + 4]
            dt.tm.hour = data[offset + 5]
            dt.tm.second = data[offset + 6]
        return dt

    def _extract_string_from_buffer(self, data: bytes, offset: int, max_len: int) -> str:
        """Extract a null-terminated string from a buffer"""
        if offset >= len(data):
            return ""

        end = offset
        while end < len(data) and end < offset + max_len and data[end] != 0:
            end += 1

        try:
            return data[offset:end].decode('utf-8', errors='replace')
        except:
            return ""

    def _parse_grouped_ex_data(self, data: bytes) -> Optional[MdbGroupedEx]:
        """Parse mdb_grouped_ex record data using schema-defined offsets.

        Hero11 mdb_grouped_ex: 73 bytes (u_size) + 8 byte OID prefix = 81 bytes raw

        On-disk layout: [8-byte OID][73-byte record data]
        Actual offset in raw data = u_offset + 8 (for OID prefix)

        Schema from hero11_mco/fields_layoutB.csv:
        - file_handle: u_offset=0 (u64) → raw 8
        - frame_rate_timescale: u_offset=8 (u32) → raw 16
        - frame_rate_duration: u_offset=12 (u32) → raw 20
        - n_elems: u_offset=16 (u32) → raw 24
        - grp_ctm: u_offset=20 (date_time, 7 bytes) → raw 28
        - grp_no: u_offset=28 (u16) → raw 36
        - width: u_offset=30 (u16) → raw 38
        - height: u_offset=32 (u16) → raw 40
        - frame_rate_duration_indicator: u_offset=34 (u8) → raw 42
        - frame_rate_timescale_indicator: u_offset=35 (u8) → raw 43
        - gusi_blob: u_offset=36 (u8[16]) → raw 44
        - f_is_subsample: u_offset=52 (u8) → raw 60
        - f_is_progressive: u_offset=53 (u8) → raw 61
        - f_is_progressive_indicator: u_offset=54 (u8) → raw 62
        - grp_no_indicator: u_offset=55 (u8) → raw 63
        - f_is_subsample_indicator: u_offset=56 (u8) → raw 64
        - blob: u_offset=57 (u8[16]) → raw 65
        """
        OID_SIZE = 8  # OID prefix before record data

        if len(data) < 50:  # Minimum viable record size
            return None

        record = MdbGroupedEx()

        # Helper to safely read from buffer with OID offset
        def read_u8(u_offset: int) -> int:
            off = u_offset + OID_SIZE
            return data[off] if off < len(data) else 0

        def read_u16(u_offset: int) -> int:
            off = u_offset + OID_SIZE
            if off + 2 <= len(data):
                return struct.unpack_from('<H', data, off)[0]
            return 0

        def read_u32(u_offset: int) -> int:
            off = u_offset + OID_SIZE
            if off + 4 <= len(data):
                return struct.unpack_from('<I', data, off)[0]
            return 0

        def read_u64(u_offset: int) -> int:
            off = u_offset + OID_SIZE
            if off + 8 <= len(data):
                return struct.unpack_from('<Q', data, off)[0]
            return 0

        def read_datetime(u_offset: int) -> DateTime:
            off = u_offset + OID_SIZE
            return self._read_datetime_from_buffer(data, off)

        def read_bytes(u_offset: int, length: int) -> bytes:
            off = u_offset + OID_SIZE
            if off + length <= len(data):
                return data[off:off + length]
            return b''

        # Parse all fields using schema u_offsets
        record.file_handle = read_u64(0)              # u_offset=0
        record.frame_rate_timescale = read_u32(8)     # u_offset=8
        record.frame_rate_duration = read_u32(12)     # u_offset=12
        record.n_elems = read_u32(16)                 # u_offset=16
        record.grp_ctm = read_datetime(20)            # u_offset=20
        record.grp_no = read_u16(28)                  # u_offset=28
        record.width = read_u16(30)                   # u_offset=30
        record.height = read_u16(32)                  # u_offset=32
        record.frame_rate_duration_indicator = read_u8(34)   # u_offset=34
        record.frame_rate_timescale_indicator = read_u8(35)  # u_offset=35

        # Parse gusi_blob at u_offset=36 (16 bytes)
        # GUSI = GoPro Unique Segment Identifier
        # Structure: [4 session_id][4 reserved][4 recording_id][4 reserved]
        gusi_raw = read_bytes(36, 16)
        record.gusi_blob = GusiBlob(raw=gusi_raw)
        if len(gusi_raw) >= 12:
            record.gusi_blob.session_id = struct.unpack_from('<I', gusi_raw, 0)[0]
            record.gusi_blob.recording_id = struct.unpack_from('<I', gusi_raw, 8)[0]

        record.f_is_subsample = read_u8(52)           # u_offset=52
        record.f_is_progressive = read_u8(53)         # u_offset=53
        record.f_is_progressive_indicator = read_u8(54)  # u_offset=54
        record.grp_no_indicator = read_u8(55)         # u_offset=55
        record.f_is_subsample_indicator = read_u8(56) # u_offset=56

        # Parse blob at u_offset=57 (16 bytes)
        # Content identification blob - 128-bit unique content ID
        # Structure: [8 bytes high][8 bytes low]
        blob_raw = read_bytes(57, 16)
        record.blob = ContentBlob(raw=blob_raw)
        if len(blob_raw) >= 16:
            record.blob.content_id_high = struct.unpack_from('<Q', blob_raw, 0)[0]
            record.blob.content_id_low = struct.unpack_from('<Q', blob_raw, 8)[0]

        return record

    def _find_table_locations(self) -> Dict[str, int]:
        """Find locations of table name strings"""
        locations = {}
        for name in [b'mdb_global', b'mdb_single', b'mdb_single_ex', b'mdb_grouped_ex']:
            pos = self.data.find(name + b'\x00')
            if pos != -1:
                locations[name.decode()] = pos
        return locations

    # =========================================================================
    # Helper methods
    # =========================================================================

    def _read_u8(self, offset: int) -> int:
        if offset < len(self.data):
            return self.data[offset]
        return 0

    def _read_u16(self, offset: int) -> int:
        if offset + 2 <= len(self.data):
            return struct.unpack_from('<H', self.data, offset)[0]
        return 0

    def _read_u32(self, offset: int) -> int:
        if offset + 4 <= len(self.data):
            return struct.unpack_from('<I', self.data, offset)[0]
        return 0

    def _read_u64(self, offset: int) -> int:
        if offset + 8 <= len(self.data):
            return struct.unpack_from('<Q', self.data, offset)[0]
        return 0

    def _read_i32(self, offset: int) -> int:
        if offset + 4 <= len(self.data):
            return struct.unpack_from('<i', self.data, offset)[0]
        return 0

    def _read_f32(self, offset: int) -> float:
        if offset + 4 <= len(self.data):
            return struct.unpack_from('<f', self.data, offset)[0]
        return 0.0

    def _read_f64(self, offset: int) -> float:
        if offset + 8 <= len(self.data):
            return struct.unpack_from('<d', self.data, offset)[0]
        return 0.0

    def _read_cstring(self, offset: int, max_len: int = 256) -> str:
        """Read null-terminated string"""
        end = offset
        while end < len(self.data) and end < offset + max_len and self.data[end] != 0:
            end += 1
        return self.data[offset:end].decode('utf-8', errors='replace')

    def _read_datetime(self, offset: int) -> DateTime:
        """Read a DateTime struct (7 bytes)"""
        dt = DateTime()
        dt.dt.year = self._read_u16(offset)
        dt.dt.month = self._read_u8(offset + 2)
        dt.dt.day = self._read_u8(offset + 3)
        dt.tm.min = self._read_u8(offset + 4)
        dt.tm.hour = self._read_u8(offset + 5)
        dt.tm.second = self._read_u8(offset + 6)
        return dt

    def dump_hex(self, start: int, length: int) -> str:
        """Dump a hex region for debugging"""
        lines = []
        for i in range(start, min(start + length, len(self.data)), 16):
            hex_part = ' '.join(f'{b:02x}' for b in self.data[i:i+16])
            ascii_part = ''.join(
                chr(b) if 32 <= b < 127 else '.'
                for b in self.data[i:i+16]
            )
            lines.append(f'{i:08x}: {hex_part:<48} {ascii_part}')
        return '\n'.join(lines)

    def find_all_strings(self, min_len: int = 4) -> List[Tuple[int, str]]:
        """Find all printable strings in the file"""
        strings = []
        current = []
        start = 0

        for i, b in enumerate(self.data):
            if 32 <= b < 127:
                if not current:
                    start = i
                current.append(chr(b))
            else:
                if len(current) >= min_len:
                    strings.append((start, ''.join(current)))
                current = []

        if len(current) >= min_len:
            strings.append((start, ''.join(current)))

        return strings

    def find_field_names(self) -> Dict[str, int]:
        """Find all field name strings and their offsets"""
        known_fields = [
            'file_handle', 'file_scanned', 'duration', 'size', 'file_type_ex',
            'camera_model', 'width', 'height', 'latitude', 'longitude', 'altitude',
            'frame_rate_duration', 'frame_rate_timescale', 'tag_cnt', 'moment_cnt',
            'avc_profile', 'avc_level', 'has_eis', 'has_hdr', 'is_clip', 'projection',
            'lens_config', 'protune_option', 'aud_option', 'chp_cnt', 'grp_no',
            'dir_no', 'n_elems', 'blob', 'autoid', 'version', 'last_db_scan_time'
        ]

        found = {}
        for field in known_fields:
            pos = self.data.find(field.encode() + b'\x00')
            if pos != -1:
                found[field] = pos

        return found

    # =========================================================================
    # Output methods
    # =========================================================================

    def to_dict(self) -> Dict[str, Any]:
        """Convert parsed data to dictionary with decoded values"""
        def datetime_to_dict(dt: DateTime) -> Dict[str, Any]:
            """Convert DateTime with decoded actual_year"""
            actual_year = dt.dt.year + 1980 if dt.dt.year > 0 else 0
            return {
                'year': dt.dt.year,
                'actual_year': actual_year,
                'month': dt.dt.month,
                'day': dt.dt.day,
                'hour': dt.tm.hour,
                'minute': dt.tm.min,
                'second': dt.tm.second,
                'formatted': f"{actual_year}-{dt.dt.month:02d}-{dt.dt.day:02d} {dt.tm.hour:02d}:{dt.tm.min:02d}:{dt.tm.second:02d}" if actual_year > 0 else None
            }

        def single_ex_to_dict(rec: MdbSingleEx) -> Dict[str, Any]:
            """Convert MdbSingleEx with decoded file handle"""
            result = {
                'file_type_ex': rec.file_type_ex,
                'file_type_name': FILE_TYPES.get(rec.file_type_ex, f"Type {rec.file_type_ex}"),
                'duration_raw': rec.duration,
                'duration_ms': rec.duration,
                'duration_seconds': rec.duration / 1000,  # Duration is in milliseconds
                'size': rec.size,
                'size_mb': round(rec.size / (1024 * 1024), 2) if rec.size else 0,
                'file_handle': rec.file_handle,
                'file_handle_hex': f"0x{rec.file_handle:016x}" if rec.file_handle else None,
                'file_info': decode_file_handle(rec.file_handle) if rec.file_handle else None,
                'camera_model': rec.camera_model,
                'sub_model': rec.sub_model,
                'full_model': f"{rec.camera_model} {rec.sub_model}".strip() if rec.sub_model else rec.camera_model,
                'ctm': datetime_to_dict(rec.ctm) if rec.ctm else None,
                'last_scan_time': datetime_to_dict(rec.last_scan_time) if rec.last_scan_time else None,
                'latm': datetime_to_dict(rec.latm) if rec.latm else None,
                'tag_cnt': rec.tag_cnt,
                'chp_cnt': rec.chp_cnt,
                'dir_no': rec.dir_no,
                'grp_no': rec.grp_no,
                'width': rec.width,
                'height': rec.height,
                'has_eis': rec.has_eis,
                'has_hdr': rec.has_hdr,
                'is_clip': rec.is_clip,
                'projection': rec.projection,
                'lens_config': rec.lens_config,
                'avc_profile': rec.avc_profile,
                'avc_level': rec.avc_level,
                'moment_cnt': rec.moment_cnt,
                'total_tag_cnt': rec.total_tag_cnt,
                'max_moment_score': rec.max_moment_score,
                'media_orientation': rec.media_orientation,
                'media_status': rec.media_status,
                'upload_status': rec.upload_status,
                'fov': rec.fov,
                'f_meta_present': rec.f_meta_present,
                'file_scanned': rec.file_scanned,
                'protune_option': rec.protune_option,
                'aud_option': rec.aud_option,
                'latitude': rec.latitude,
                'longitude': rec.longitude,
                'altitude': rec.altitude,
            }
            return result

        def grouped_ex_to_dict(rec: MdbGroupedEx) -> Dict[str, Any]:
            """Convert MdbGroupedEx with calculated frame rate and decoded blobs"""
            fps = rec.frame_rate_timescale / rec.frame_rate_duration if rec.frame_rate_duration > 0 else 0

            # Decode gusi_blob (GoPro Unique Segment Identifier)
            gusi_dict = None
            if rec.gusi_blob and rec.gusi_blob.raw:
                gusi_dict = {
                    'raw': rec.gusi_blob.raw.hex(),
                    'session_id': rec.gusi_blob.session_id,
                    'session_id_hex': f"0x{rec.gusi_blob.session_id:08x}" if rec.gusi_blob.session_id else None,
                    'recording_id': rec.gusi_blob.recording_id,
                    'recording_id_hex': f"0x{rec.gusi_blob.recording_id:08x}" if rec.gusi_blob.recording_id else None,
                }

            # Decode content blob (128-bit unique content ID)
            blob_dict = None
            if rec.blob and rec.blob.raw:
                # Format as UUID-like string: high-low
                content_id_str = f"{rec.blob.content_id_high:016x}{rec.blob.content_id_low:016x}"
                blob_dict = {
                    'raw': rec.blob.raw.hex(),
                    'content_id_high': rec.blob.content_id_high,
                    'content_id_low': rec.blob.content_id_low,
                    'content_id_hex': content_id_str,
                }

            return {
                'file_handle': rec.file_handle,
                'file_handle_hex': f"0x{rec.file_handle:016x}" if rec.file_handle else None,
                'file_info': decode_file_handle(rec.file_handle) if rec.file_handle else None,
                'frame_rate_timescale': rec.frame_rate_timescale,
                'frame_rate_duration': rec.frame_rate_duration,
                'frame_rate_fps': round(fps, 2),
                'n_elems': rec.n_elems,
                'grp_no': rec.grp_no,
                'grp_ctm': datetime_to_dict(rec.grp_ctm) if rec.grp_ctm else None,
                'width': rec.width,
                'height': rec.height,
                'resolution': f"{rec.width}x{rec.height}" if rec.width and rec.height else None,
                'f_is_progressive': rec.f_is_progressive,
                'f_is_subsample': rec.f_is_subsample,
                'gusi_blob': gusi_dict,
                'blob': blob_dict,
            }

        def dataclass_to_dict(obj):
            if hasattr(obj, '__dataclass_fields__'):
                return {k: dataclass_to_dict(v) for k, v in obj.__dict__.items()}
            elif isinstance(obj, bytes):
                return obj.hex()
            elif isinstance(obj, list):
                return [dataclass_to_dict(i) for i in obj]
            return obj

        return {
            'file_path': str(self.filepath),
            'file_size': self.file_size,
            'header_valid': self.header_valid,
            'schema_version': self.schema_version,
            'db_version': dataclass_to_dict(self.db_version),
            'mco_version': str(self.mco_version) if self.mco_version.major > 0 else None,
            'mco_version_details': {
                'major': self.mco_version.major,
                'minor': self.mco_version.minor,
                'build': self.mco_version.build,
            } if self.mco_version.major > 0 else None,
            'page_size': self.page_size,
            'dictionary_offset': f"0x{self.dictionary_offset:04X}" if self.dictionary_offset else None,
            'globals': [dataclass_to_dict(g) for g in self.globals],
            'singles_ex': [single_ex_to_dict(s) for s in self.singles_ex],
            'grouped_ex': [grouped_ex_to_dict(g) for g in self.grouped_ex],
        }

    def to_json(self, indent: int = 2) -> str:
        """Convert parsed data to JSON"""
        return json.dumps(self.to_dict(), indent=indent, default=str)

    def print_summary(self):
        """Print a human-readable summary"""
        print(f"=== GoPro Media Database ===")
        print(f"File: {self.filepath}")
        print(f"Size: {self.file_size} bytes")
        print(f"Header valid: {self.header_valid}")
        print(f"Schema version: {self.schema_version}")
        print(f"DB Version: {self.db_version.major}.{self.db_version.minor}")
        if self.mco_version.major > 0:
            print(f"MCO Version: {self.mco_version}")
        print(f"Page size: {self.page_size} bytes")
        if self.dictionary_offset:
            print(f"Dictionary: 0x{self.dictionary_offset:04X}")
        print(f"Raw records found: {len(self.raw_records)}")
        print()

        if self.raw_records:
            print("=== Raw Records ===")
            for i, (header, data) in enumerate(self.raw_records):
                print(f"  [{i}] Table: {header.table_name} (id={header.table_id})")
                print(f"       Size: {header.size} bytes, Flags: 0x{header.flags:04x}")
                print(f"       Data preview: {data[:32].hex()}")
            print()

        if self.singles_ex:
            print(f"=== Media Files ({len(self.singles_ex)}) ===")
            for i, rec in enumerate(self.singles_ex):
                file_type = FILE_TYPES.get(rec.file_type_ex, f"Type {rec.file_type_ex}")
                full_model = f"{rec.camera_model} {rec.sub_model}".strip() if rec.sub_model else rec.camera_model
                print(f"  [{i}] {file_type}: {full_model}")
                if rec.width and rec.height:
                    print(f"      Resolution: {rec.width}x{rec.height}")
                if rec.duration:
                    duration_sec = rec.duration / 1000  # Duration is in milliseconds
                    print(f"      Duration: {duration_sec:.1f}s")
                if rec.size:
                    size_mb = rec.size / (1024 * 1024)
                    print(f"      Size: {size_mb:.1f} MB")
                if rec.file_handle:
                    fh_info = decode_file_handle(rec.file_handle)
                    print(f"      File: {fh_info['estimated_path']}")
                if rec.ctm and rec.ctm.dt.year > 0:
                    actual_year = rec.ctm.dt.year + 1980
                    print(f"      Created: {actual_year}-{rec.ctm.dt.month:02d}-{rec.ctm.dt.day:02d} "
                          f"{rec.ctm.tm.hour:02d}:{rec.ctm.tm.min:02d}:{rec.ctm.tm.second:02d}")

        if self.grouped_ex:
            print(f"\n=== Video Groups ({len(self.grouped_ex)}) ===")
            for i, rec in enumerate(self.grouped_ex):
                if rec.frame_rate_timescale > 0 and rec.frame_rate_duration > 0:
                    fps = rec.frame_rate_timescale / rec.frame_rate_duration
                else:
                    fps = 0
                print(f"  [{i}] {rec.width}x{rec.height} @ {fps:.2f}fps")
                print(f"      Group: {rec.grp_no}, Elements: {rec.n_elems}")
                if rec.file_handle:
                    fh_info = decode_file_handle(rec.file_handle)
                    print(f"      File: {fh_info['estimated_path']}")

                # Display GUSI blob info (session/recording identifiers)
                if rec.gusi_blob and (rec.gusi_blob.session_id or rec.gusi_blob.recording_id):
                    print(f"      GUSI: session=0x{rec.gusi_blob.session_id:08x}, "
                          f"recording=0x{rec.gusi_blob.recording_id:08x}")

                # Display content blob info (128-bit unique content ID)
                if rec.blob and (rec.blob.content_id_high or rec.blob.content_id_low):
                    print(f"      Content ID: {rec.blob.content_id_high:016x}-{rec.blob.content_id_low:016x}")


@dataclass
class DictFieldDef:
    """Parsed field definition from dictionary"""
    name: str = ""
    name_offset: int = 0
    c_size: int = 0       # Compiled size
    c_align: int = 0      # Compiled alignment
    c_offset: int = 0     # Compiled offset
    u_size: int = 0       # Unpacked size
    u_align: int = 0      # Unpacked alignment
    u_offset: int = 0     # Unpacked offset
    el_type: int = 0      # Element type (see FieldType enum)
    flags: int = 0        # Field flags
    array_size: int = 0   # Array size (0 for scalar)
    struct_num: int = 0   # Struct index if type=0x32
    field_size: int = 0   # Field size in bytes

    @property
    def type_name(self) -> str:
        """Human-readable type name"""
        type_names = {
            0x01: "uint8", 0x02: "uint16", 0x03: "uint32",
            0x06: "float", 0x0A: "float", 0x0C: "uint64",
            0x0E: "autoid", 0x17: "indicator", 0x32: "struct"
        }
        return type_names.get(self.el_type, f"type_0x{self.el_type:02x}")


@dataclass
class DictStructDef:
    """Parsed struct definition from dictionary"""
    name: str = ""
    name_offset: int = 0
    num_fields: int = 0
    c_size: int = 0       # Compiled size
    u_size: int = 0       # Unpacked size
    fields: List[DictFieldDef] = field(default_factory=list)


@dataclass
class DictIndexDef:
    """Parsed index definition from dictionary"""
    name: str = ""
    name_offset: int = 0
    table_name: str = ""
    index_name: str = ""


@dataclass
class DictClassDef:
    """Parsed class definition from dictionary"""
    name: str = ""
    name_offset: int = 0
    pointer: int = 0


@dataclass
class PageInfo:
    """Parsed page information"""
    offset: int = 0
    kind: int = 0
    kind_name: str = ""
    flags: int = 0
    extraflags: int = 0
    user: int = 0          # Table ID or page-specific data
    align_data: int = 0    # Page-specific data in align field
    data: bytes = b''

    PAGE_KIND_NAMES = {
        0: 'DATA', 1: 'EXTENSION', 2: 'BTREE_LEAF', 3: 'BTREE_NODE',
        4: 'AUTOID_HASH', 5: 'AUTOID_OVF', 6: 'BLOB_HEAD', 7: 'BLOB_CONT',
        8: 'INDEX_DIR', 9: 'HASH_OVF', 10: 'TRANS', 11: 'FREELIST',
        12: 'FIXREC', 13: 'UNKNOWN_13', 14: 'STRING_EXT', 15: 'TEMP'
    }

    TABLE_NAMES = {
        0: 'header', 1: 'mdb_global', 2: 'mdb_single',
        3: 'mdb_single_ex', 4: 'mdb_grouped_ex'
    }

    @property
    def table_name(self) -> str:
        """Get table name from user field (for DATA/EXTENSION pages)"""
        return self.TABLE_NAMES.get(self.user, f"table_{self.user}")

    @property
    def flag_names(self) -> List[str]:
        """Get list of flag names"""
        names = []
        if self.flags & 0x10:
            names.append('COMPACT')
        if self.flags & 0x20:
            names.append('HAS_BLOBS')
        if self.flags & 0x40:
            names.append('FLAG_2')
        if self.flags & 0x80:
            names.append('FLAG_3')
        return names


class PageAnalyzer:
    """Analyze individual page structures in the database.

    Page Kinds Present in GoPro databases:
    - DATA (0): Object/record data pages
    - EXTENSION (1): Overflow data for large records
    - BTREE_LEAF (2): B-tree leaf/root nodes
    - BTREE_NODE (3): B-tree internal nodes
    - AUTOID_HASH (4): Auto OID hash buckets (schema/string tables)
    - AUTOID_OVF (5): Auto OID list overflow
    - INDEX_DIR (8): Index directory entries
    - STRING_EXT (14): String table extension (field names)
    - TEMP (15): Temporary/scratch pages
    """

    def __init__(self, data: bytes, page_size: int = 512):
        self.data = data
        self.page_size = page_size
        self.pages: List[PageInfo] = []

    def analyze_all(self) -> List[PageInfo]:
        """Analyze all pages in the database"""
        self.pages = []
        for offset in range(0, len(self.data), self.page_size):
            page = self._parse_page(offset)
            self.pages.append(page)
        return self.pages

    def _parse_page(self, offset: int) -> PageInfo:
        """Parse a single page header and content"""
        page = PageInfo()
        page.offset = offset
        page.data = self.data[offset:offset + self.page_size]

        if len(page.data) < 8:
            return page

        page.kind = page.data[0] & 0x0F
        page.flags = page.data[0] & 0xF0
        page.extraflags = page.data[1]
        page.user = struct.unpack_from('<H', page.data, 2)[0]
        page.align_data = struct.unpack_from('<I', page.data, 4)[0]
        page.kind_name = PageInfo.PAGE_KIND_NAMES.get(page.kind, f"UNKNOWN_{page.kind}")

        return page

    def get_pages_by_kind(self, kind: int) -> List[PageInfo]:
        """Get all pages of a specific kind"""
        return [p for p in self.pages if p.kind == kind]

    def parse_extension_page(self, page: PageInfo) -> Dict[str, Any]:
        """Parse EXTENSION page (kind=1) - overflow data for large records.

        Extension pages contain continuation data for records that don't fit
        in a single DATA page. The user field indicates which table the data
        belongs to (1=mdb_global, 2=mdb_single, 3=mdb_single_ex, etc.)
        """
        if page.kind != 1:
            return {'error': 'Not an extension page'}

        return {
            'type': 'EXTENSION',
            'offset': hex(page.offset),
            'table_id': page.user,
            'table_name': page.table_name,
            'data_size': page.align_data,
            'content_preview': page.data[8:40].hex(),
        }

    def parse_autoid_ovf_page(self, page: PageInfo) -> Dict[str, Any]:
        """Parse AUTOID_OVF page (kind=5) - schema/field definition overflow.

        Contains pointers to field definitions and related schema data.
        """
        if page.kind != 5:
            return {'error': 'Not an AUTOID_OVF page'}

        # Find pointer entries (8-byte values pointing within file)
        pointers = []
        for i in range(8, len(page.data) - 8, 8):
            ptr = struct.unpack_from('<Q', page.data, i)[0]
            if 0x100 < ptr < len(self.data):
                pointers.append({'offset': i, 'pointer': hex(ptr)})

        return {
            'type': 'AUTOID_OVF',
            'offset': hex(page.offset),
            'user': page.user,
            'flags': page.flag_names,
            'pointers': pointers[:20],  # Limit output
        }

    def parse_string_ext_page(self, page: PageInfo) -> Dict[str, Any]:
        """Parse STRING_EXT page (kind=14) - field name strings.

        Contains null-terminated strings for field names used in the schema.
        """
        if page.kind != 14:
            return {'error': 'Not a STRING_EXT page'}

        # Extract all strings
        strings = []
        i = 0
        while i < len(page.data):
            if 32 <= page.data[i] < 127:
                end = i
                while end < len(page.data) and page.data[end] != 0:
                    end += 1
                s = page.data[i:end].decode('utf-8', errors='replace')
                if len(s) >= 2:
                    strings.append({'offset': i, 'value': s})
                i = end + 1
            else:
                i += 1

        return {
            'type': 'STRING_EXT',
            'offset': hex(page.offset),
            'flags': page.flag_names,
            'strings': strings,
        }

    def parse_index_dir_page(self, page: PageInfo) -> Dict[str, Any]:
        """Parse INDEX_DIR page (kind=8) - index directory entries.

        Contains metadata about indexes defined on tables.
        """
        if page.kind != 8:
            return {'error': 'Not an INDEX_DIR page'}

        return {
            'type': 'INDEX_DIR',
            'offset': hex(page.offset),
            'user': page.user,
            'align_data': page.align_data,
            'content_preview': page.data[8:48].hex(),
        }

    def get_summary(self) -> Dict[str, Any]:
        """Get summary of all pages"""
        kind_counts = {}
        for page in self.pages:
            name = page.kind_name
            kind_counts[name] = kind_counts.get(name, 0) + 1

        return {
            'total_pages': len(self.pages),
            'page_size': self.page_size,
            'kinds': kind_counts,
        }

    def print_summary(self):
        """Print page analysis summary"""
        summary = self.get_summary()
        print(f"=== Page Analysis Summary ===")
        print(f"Total pages: {summary['total_pages']}")
        print(f"Page size: {summary['page_size']} bytes")
        print()
        print("Page kinds:")
        for name, count in sorted(summary['kinds'].items()):
            print(f"  {name}: {count}")

        # Print extension pages
        ext_pages = self.get_pages_by_kind(1)
        if ext_pages:
            print(f"\nExtension pages ({len(ext_pages)}):")
            for p in ext_pages:
                info = self.parse_extension_page(p)
                print(f"  0x{p.offset:04X}: table={info['table_name']}, size={info['data_size']}")

        # Print string pages
        str_pages = self.get_pages_by_kind(14)
        if str_pages:
            print(f"\nString extension pages ({len(str_pages)}):")
            for p in str_pages:
                info = self.parse_string_ext_page(p)
                print(f"  0x{p.offset:04X}: {len(info['strings'])} strings")
                for s in info['strings'][:5]:
                    print(f"    +{s['offset']:3d}: \"{s['value']}\"")
                if len(info['strings']) > 5:
                    print(f"    ... and {len(info['strings']) - 5} more")


class DictionaryParser:
    """Parse the eXtremeDB dictionary/schema from the database file.

    The dictionary is typically located at offset 0x0C00 and contains:
    - Header with MCO version
    - Class table (mdb_global, mdb_single, mdb_single_ex, mdb_grouped_ex)
    - Index table (file handle indexes, etc.)
    - Struct definitions (tag_entry, date_field, time_field, etc.)
    - Field definitions with types and offsets
    - Field name string pool
    """

    DICT_OFFSET = 0x0C00

    def __init__(self, data: bytes):
        self.data = data
        self.mco_version: MCOVersion = MCOVersion()
        self.classes: List[DictClassDef] = []
        self.indexes: List[DictIndexDef] = []
        self.structs: List[DictStructDef] = []

        # Parsed offsets
        self.num_classes = 0
        self.num_indexes = 0
        self.num_structs = 0

    def _read_u8(self, offset: int) -> int:
        return self.data[offset] if offset < len(self.data) else 0

    def _read_u16(self, offset: int) -> int:
        if offset + 2 <= len(self.data):
            return struct.unpack_from('<H', self.data, offset)[0]
        return 0

    def _read_u32(self, offset: int) -> int:
        if offset + 4 <= len(self.data):
            return struct.unpack_from('<I', self.data, offset)[0]
        return 0

    def _read_u64(self, offset: int) -> int:
        if offset + 8 <= len(self.data):
            return struct.unpack_from('<Q', self.data, offset)[0]
        return 0

    def _read_cstring(self, offset: int, max_len: int = 64) -> str:
        """Read null-terminated string"""
        if offset >= len(self.data):
            return ""
        end = offset
        while end < len(self.data) and end < offset + max_len and self.data[end] != 0:
            end += 1
        try:
            return self.data[offset:end].decode('utf-8', errors='replace')
        except:
            return ""

    def parse(self) -> bool:
        """Parse the dictionary structure"""
        if len(self.data) < self.DICT_OFFSET + 0x200:
            return False

        # Parse header at 0x0C00
        self._parse_header()

        # Parse class table
        self._parse_classes()

        # Parse index table
        self._parse_indexes()

        # Parse struct/field definitions
        self._parse_structs()

        return True

    def _parse_header(self):
        """Parse dictionary header"""
        base = self.DICT_OFFSET

        # MCO version at offset 0x10 (0x0C10)
        self.mco_version.major = self._read_u16(base + 0x10)
        self.mco_version.minor = self._read_u16(base + 0x12)
        self.mco_version.build = self._read_u16(base + 0x14)

        # Class count at offset 0x20
        self.num_classes = self._read_u32(base + 0x20)
        if self.num_classes > 10:  # Sanity check
            self.num_classes = 4  # Default for GoPro

        # Index count at offset 0x38
        self.num_indexes = self._read_u32(base + 0x38)
        if self.num_indexes > 20:  # Sanity check
            self.num_indexes = 6  # Default for GoPro

    def _parse_classes(self):
        """Parse class table starting at 0x0CA0"""
        base = self.DICT_OFFSET

        # Class pointers at 0xA0 (0x0CA0)
        ptr_offset = base + 0xA0

        # Class names at 0xC8 (0x0CC8), 16 bytes each
        name_offset = base + 0xC8

        for i in range(self.num_classes):
            cls = DictClassDef()
            cls.pointer = self._read_u64(ptr_offset + i * 8)
            cls.name_offset = name_offset + i * 16
            cls.name = self._read_cstring(cls.name_offset, 16)
            self.classes.append(cls)

    def _parse_indexes(self):
        """Parse index table starting after class names"""
        # Index names are stored sequentially, find them by searching for patterns
        # Known index names from GoPro databases
        known_index_names = [
            'mdb_global.autoid',
            'mdb_single.fh_indx',
            'mdb_single_ex.fh_indx',
            'mdb_single_ex.dno_gno_tag_cnt_indx',
            'mdb_grouped_ex.dt_fh_indx',
            'mdb_grouped_ex.fh_indx',
        ]

        for name in known_index_names:
            pos = self.data.find(name.encode() + b'\x00')
            if pos != -1:
                idx = DictIndexDef()
                idx.name = name
                idx.name_offset = pos

                # Parse table.index_name format
                if '.' in idx.name:
                    parts = idx.name.split('.', 1)
                    idx.table_name = parts[0]
                    idx.index_name = parts[1]

                self.indexes.append(idx)

    def _parse_structs(self):
        """Parse struct definitions from the dictionary

        Structs are located after the index names, starting around 0x0DE8.
        They include: tag_entry, date_field, time_field, date_time, db_version,
        and the main tables (mdb_global, mdb_single, mdb_single_ex, mdb_grouped_ex).
        """
        # Find struct name offsets by searching for known names
        known_structs = [
            'tag_entry', 'date_field', 'time_field', 'date_time', 'db_version',
            'mdb_global', 'mdb_single', 'mdb_single_ex', 'mdb_grouped_ex'
        ]

        for name in known_structs:
            pos = self.data.find(name.encode() + b'\x00')
            if pos != -1:
                struct_def = DictStructDef()
                struct_def.name = name
                struct_def.name_offset = pos

                # Try to find field definitions by looking for field names after struct
                struct_def.fields = self._find_struct_fields(name)
                struct_def.num_fields = len(struct_def.fields)

                # Calculate size from fields
                if struct_def.fields:
                    max_offset = max(f.u_offset + f.u_size for f in struct_def.fields)
                    struct_def.u_size = max_offset

                self.structs.append(struct_def)

    def _find_struct_fields(self, struct_name: str) -> List[DictFieldDef]:
        """Find field definitions for a struct by scanning the dictionary"""
        fields = []

        # Known field names for each struct (from CSV schema files)
        struct_fields = {
            'tag_entry': ['time_code', 'tag_indx', 'altitude', 'confidence',
                         'event_type', 'in_time', 'latitude', 'longitude',
                         'out_time', 'score'],
            'date_field': ['year', 'month', 'day'],
            'time_field': ['hour', 'min', 'second'],
            'date_time': ['dt', 'tm'],
            'db_version': ['major', 'minor'],
            'mdb_global': ['autoid', 'last_db_scan_time', 'version'],
            'mdb_single': ['file_handle', 'file_scanned'],
            'mdb_single_ex': ['duration', 'size', 'file_handle', 'file_type_ex',
                              'tag_cnt', 'chp_cnt', 'ctm', 'latm', 'last_scan_time',
                              'has_eis', 'is_clip', 'avc_level', 'avc_profile',
                              'camera_model', 'dir_no', 'grp_no', 'projection',
                              'lens_config', 'moment_cnt', 'total_tag_cnt'],
            'mdb_grouped_ex': ['file_handle', 'frame_rate_duration',
                               'frame_rate_timescale', 'n_elems', 'grp_no',
                               'grp_ctm', 'width', 'height', 'blob'],
        }

        field_names = struct_fields.get(struct_name, [])
        for fname in field_names:
            pos = self.data.find(fname.encode() + b'\x00')
            if pos != -1:
                field = DictFieldDef()
                field.name = fname
                field.name_offset = pos
                fields.append(field)

        return fields

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON output"""
        return {
            'mco_version': str(self.mco_version),
            'num_classes': self.num_classes,
            'num_indexes': self.num_indexes,
            'classes': [
                {'name': c.name, 'offset': hex(c.name_offset), 'pointer': hex(c.pointer)}
                for c in self.classes
            ],
            'indexes': [
                {'name': i.name, 'table': i.table_name, 'index': i.index_name}
                for i in self.indexes
            ],
            'structs': [
                {
                    'name': s.name,
                    'num_fields': s.num_fields,
                    'size': s.u_size,
                    'fields': [f.name for f in s.fields]
                }
                for s in self.structs
            ],
        }

    def print_summary(self):
        """Print human-readable dictionary summary"""
        print(f"=== Dictionary Summary ===")
        print(f"MCO Version: {self.mco_version}")
        print(f"Classes: {self.num_classes}")
        print(f"Indexes: {self.num_indexes}")
        print()

        if self.classes:
            print("Classes:")
            for c in self.classes:
                print(f"  {c.name} @ 0x{c.name_offset:04X} -> 0x{c.pointer:04X}")
        print()

        if self.indexes:
            print("Indexes:")
            for i in self.indexes:
                print(f"  {i.name}")
        print()

        if self.structs:
            print("Structs:")
            for s in self.structs:
                print(f"  {s.name}: {s.num_fields} fields, {s.u_size} bytes")
                if s.fields:
                    field_str = ', '.join(f.name for f in s.fields[:5])
                    if len(s.fields) > 5:
                        field_str += f", ... (+{len(s.fields) - 5} more)"
                    print(f"    Fields: {field_str}")


class SchemaAnalyzer:
    """Analyze and dump schema information from the database"""

    def __init__(self, parser: GoproMDBParser):
        self.parser = parser
        self.data = parser.data

    def analyze(self) -> Dict[str, Any]:
        """Perform full schema analysis"""
        return {
            'strings': self._analyze_strings(),
            'tables': self._analyze_tables(),
            'indexes': self._analyze_indexes(),
            'data_regions': self._analyze_data_regions(),
            'pointers': self._find_pointers(),
        }

    def _analyze_strings(self) -> List[Dict[str, Any]]:
        """Analyze string content"""
        strings = self.parser.find_all_strings(4)

        # Categorize strings
        result = []
        for offset, s in strings:
            if len(s) > 64:
                continue

            category = "unknown"
            if s.startswith('mdb_') and '.' not in s:
                category = "table_name"
            elif '.' in s and s.split('.')[0].startswith('mdb_'):
                category = "index_name"
            elif s in ['tag_entry', 'date_field', 'time_field', 'date_time', 'db_version']:
                category = "struct_name"
            elif '_' in s and s.islower():
                category = "field_name"
            elif s.startswith('HERO'):
                category = "camera_model"

            result.append({
                'offset': hex(offset),
                'value': s,
                'category': category
            })

        return result

    def _analyze_tables(self) -> List[Dict[str, Any]]:
        """Analyze table definitions"""
        tables = []

        for name in ['mdb_global', 'mdb_single', 'mdb_single_ex', 'mdb_grouped_ex']:
            pos = self.data.find(name.encode() + b'\x00')
            if pos != -1:
                tables.append({
                    'name': name,
                    'name_offset': hex(pos),
                })

        return tables

    def _analyze_indexes(self) -> List[Dict[str, Any]]:
        """Analyze index definitions"""
        indexes = []

        index_names = [
            'mdb_global.autoid',
            'mdb_single.fh_indx',
            'mdb_single_ex.fh_indx',
            'mdb_single_ex.dno_gno_tag_cnt_indx',
            'mdb_grouped_ex.dt_fh_indx',
            'mdb_grouped_ex.fh_indx',
        ]

        for name in index_names:
            pos = self.data.find(name.encode() + b'\x00')
            if pos != -1:
                parts = name.split('.')
                indexes.append({
                    'name': name,
                    'table': parts[0],
                    'field': parts[1],
                    'offset': hex(pos),
                })

        return indexes

    def _analyze_data_regions(self) -> List[Dict[str, Any]]:
        """Find and analyze data regions"""
        regions = []

        # Look for non-zero data blocks
        block_size = 0x100
        for i in range(0, len(self.data), block_size):
            block = self.data[i:i+block_size]
            non_zero = sum(1 for b in block if b != 0 and b != 0xFF)

            if non_zero > block_size * 0.3:  # >30% non-zero
                regions.append({
                    'start': hex(i),
                    'end': hex(i + block_size),
                    'density': non_zero / block_size,
                })

        return regions

    def _find_pointers(self) -> List[Dict[str, Any]]:
        """Find potential pointer values"""
        pointers = []
        file_size = len(self.data)

        for i in range(HEADER_SIZE, min(0x1000, file_size - 4), 4):
            val = struct.unpack_from('<I', self.data, i)[0]

            # Check if this looks like a valid pointer within the file
            if 0x1000 < val < file_size and val % 4 == 0:
                pointers.append({
                    'at': hex(i),
                    'points_to': hex(val),
                })

        return pointers


# =============================================================================
# Main
# =============================================================================

def main():
    import argparse
    import sys

    argparser = argparse.ArgumentParser(
        prog='gopro_mdb_parser',
        description='Parse GoPro media database files (mdb*.db) from HERO cameras.',
        epilog='Examples:\n'
               '  %(prog)s mdb11.db                    # Print summary\n'
               '  %(prog)s mdb11.db --json             # Output as JSON\n'
               '  %(prog)s mdb11.db --hex 0x2C00 256   # Hex dump at offset',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    argparser.add_argument(
        'database',
        metavar='DATABASE',
        help='Path to GoPro mdb*.db file'
    )

    # Output format options (mutually exclusive)
    output_group = argparser.add_mutually_exclusive_group()
    output_group.add_argument(
        '--json',
        action='store_true',
        help='Output as JSON'
    )
    output_group.add_argument(
        '--dict',
        action='store_true',
        help='Parse and display dictionary/schema'
    )
    output_group.add_argument(
        '--analyze',
        action='store_true',
        help='Detailed schema analysis'
    )
    output_group.add_argument(
        '--strings',
        action='store_true',
        help='List all strings found in database'
    )
    output_group.add_argument(
        '--fields',
        action='store_true',
        help='List known field name locations'
    )
    output_group.add_argument(
        '--pages',
        action='store_true',
        help='Analyze all page structures'
    )
    output_group.add_argument(
        '--hex',
        nargs=2,
        metavar=('OFFSET', 'LENGTH'),
        help='Hex dump at offset (use 0x prefix for hex values)'
    )

    args = argparser.parse_args()

    # Check if file exists
    if not Path(args.database).exists():
        argparser.error(f"File not found: {args.database}")

    # Parse the database
    parser = GoproMDBParser(args.database)
    if not parser.parse():
        print(f"Error: Failed to parse database: {args.database}", file=sys.stderr)
        sys.exit(1)

    # Handle output options
    if args.json:
        print(parser.to_json())

    elif args.dict:
        dict_parser = DictionaryParser(parser.data)
        if dict_parser.parse():
            dict_parser.print_summary()
        else:
            print("Error: Failed to parse dictionary", file=sys.stderr)
            sys.exit(1)

    elif args.analyze:
        analyzer = SchemaAnalyzer(parser)
        analysis = analyzer.analyze()

        print("=== Schema Analysis ===\n")

        print("Tables:")
        for t in analysis['tables']:
            print(f"  {t['name']} @ {t['name_offset']}")

        print("\nIndexes:")
        for idx in analysis['indexes']:
            print(f"  {idx['name']} @ {idx['offset']}")

        print("\nStrings by category:")
        by_cat = {}
        for s in analysis['strings']:
            cat = s['category']
            if cat not in by_cat:
                by_cat[cat] = []
            by_cat[cat].append(s)

        for cat, strings in sorted(by_cat.items()):
            print(f"\n  {cat}:")
            for s in strings[:10]:
                print(f"    {s['offset']}: {s['value']}")
            if len(strings) > 10:
                print(f"    ... and {len(strings) - 10} more")

        print(f"\nData regions: {len(analysis['data_regions'])}")
        print(f"Pointers found: {len(analysis['pointers'])}")

    elif args.strings:
        strings = parser.find_all_strings(4)
        for offset, s in strings:
            if len(s) <= 64:
                print(f"{offset:08x}: {s}")

    elif args.fields:
        fields = parser.find_field_names()
        print("Field name locations:")
        for name, offset in sorted(fields.items(), key=lambda x: x[1]):
            print(f"  {offset:08x}: {name}")

    elif args.pages:
        page_analyzer = PageAnalyzer(parser.data, parser.page_size or 512)
        page_analyzer.analyze_all()
        page_analyzer.print_summary()

    elif args.hex:
        try:
            offset = int(args.hex[0], 0)
            length = int(args.hex[1], 0)
        except ValueError as e:
            argparser.error(f"Invalid hex arguments: {e}")
        print(parser.dump_hex(offset, length))

    else:
        parser.print_summary()


if __name__ == '__main__':
    main()
