# GoPro Database (mdb*.db) Analysis Summary

This document summarizes the reverse engineering analysis of GoPro's media database format.

## Database Format Overview

GoPro cameras use **McObject eXtremeDB** as their embedded database engine for the `mdb*.db` files found on SD cards. These databases index and track all media files.

### Version Information

| Camera | MCO Version | Format Version | Notes |
|--------|-------------|----------------|-------|
| Hero5 | 5.0.1784 | 4 | Original analyzed version |
| Hero11 | 7.1.1793 | - | Adds new page kinds (4, 5, 8, 9, 14) |
| eXtremeDB 8.x Sample | 8.1.1800 | - | Reference from output.c |

## Page Structure

### Page Header (8 bytes)

```c
struct mco_page_header_t_ {
    uint8_t  kind;       // Page type (bits 0-3) + flags (bits 4-7)
    uint8_t  extraflags; // Additional flags
    uint16_t user;       // User data (varies by page type)
    uint8_t  _align[4];  // Alignment padding
};
```

### Page Kinds

| Kind | Name | Status | Description |
|------|------|--------|-------------|
| 0 | DATA | Confirmed | Object/record data pages |
| 1 | EXTENSION | Confirmed | Overflow for large records |
| 2 | BTREE_LEAF | Confirmed | B-tree leaf/root nodes |
| 3 | BTREE_NODE | Confirmed | B-tree internal nodes |
| 4 | AUTOID_HASH | Confirmed | Auto OID hash bucket (MCO 7.x+) - String/name tables |
| 5 | AUTOID_OVF | Confirmed | Auto OID list overflow (MCO 7.x+) |
| 6 | BLOB_HEAD | Confirmed | BLOB header page |
| 7 | BLOB_CONT | Confirmed | BLOB continuation page |
| 8 | INDEX_DIR | Confirmed | Index directory (MCO 7.x+) - Class index entries |
| 9 | HASH_OVF | Tentative | Hash overflow (seen in Hero11) |
| 10 | TRANS | Confirmed | Transaction pages |
| 11 | FREELIST | Confirmed | Free page list |
| 12 | FIXREC | Confirmed | Fixed record pages (dictionary/schema) |
| 13 | - | Unknown | Seen in Hero11, random-looking data |
| 14 | STRING_EXT | Tentative | String table extension (contains field names) |
| 15 | TEMP | Confirmed | Temporary/scratch pages |

### Page Flags (upper 4 bits of kind byte)

| Flag | Value | Description |
|------|-------|-------------|
| COMPACT | 0x10 | Page has been compacted |
| HAS_BLOBS | 0x20 | Page contains BLOB references |
| FLAG_2 | 0x40 | Reserved |
| FLAG_3 | 0x80 | Reserved |

## Database Tables

### mdb_global
Global database metadata (one record).
- `version`: Database schema version
- `autoid`: Next auto-increment ID
- `last_db_scan_time`: Last media scan timestamp

### mdb_single
Basic file handle lookup.
- `file_handle`: 8-byte file identifier
- `file_scanned`: Scan status flag

### mdb_single_ex
Extended file metadata (all video/photo properties).

**Hero5 layout (78 bytes):**
- Core fields: file_type_ex, duration, size, file_handle
- Timestamps: ctm, latm, last_scan_time
- Counters: tag_cnt, chp_cnt
- Flags: has_eis, is_clip, avc_level, avc_profile

**Hero11 layout (134 bytes):**
- All Hero5 fields plus:
- `camera_model`: 30-byte string (e.g., "HERO11 Black")
- `dir_no`, `grp_no`: Directory and group numbers
- `width`, `height`: Resolution
- `projection`, `lens_config`: Video settings
- `moment_cnt`, `total_tag_cnt`: HiLight stats
- `max_moment_score`: Moment score (float)
- `media_orientation`, `media_status`: Status flags
- `has_hdr`, `fov`, `f_meta_present`: Feature flags

### mdb_grouped_ex
Video group information (chapters, sequences).
- `file_handle`: Group file handle
- `frame_rate_duration`, `frame_rate_timescale`: FPS calculation
- `n_elems`: Number of elements in group
- `grp_no`: Group number
- `width`, `height`: Resolution
- `f_is_progressive`, `f_is_subsample`: Video flags
- `blob`: Additional metadata (16 bytes)

## File Layout

```
Offset      Size    Description
0x0000      0x400   File header (magic, configuration)
0x0400      0x800   Root page / page manager data
0x0C00      0x400+  Dictionary (schema definitions)
0x1000+             Data pages, indexes, records
```

### File Header Magic
```
00 FF FF FF FF FF FF FF FF FF FF 07 FF FF FF FF
```

## Dictionary Structure

The dictionary (schema) is stored starting at offset 0x0C00. It contains class definitions, field layouts, struct definitions, and index metadata.

### Dictionary Header (0x0C00-0x0CA0)

```c
struct dictionary_header {
    uint64_t table_offset;       // 0x00: Offset to table data (0x26E0)
    uint64_t end_offset;         // 0x08: End of dictionary data (0x2750)
    uint16_t mco_major;          // 0x10: MCO version major (e.g., 7)
    uint16_t mco_minor;          // 0x12: MCO version minor (e.g., 1)
    uint16_t mco_build_hi;       // 0x14: MCO build high byte
    uint16_t mco_build_lo;       // 0x16: MCO build low byte (combined: 1793)
    uint32_t reserved1;          // 0x18
    uint32_t num_classes;        // 0x20: Number of classes (4)
    uint32_t reserved2;          // 0x24
    uint16_t class_flags;        // 0x28
    uint16_t num_structs;        // 0x2A: Number of struct types (9)
    uint64_t reserved3;          // 0x30
    uint32_t num_indexes;        // 0x38: Number of indexes (6)
    // ... more header fields ...
};
```

### Class Table (0x0CA0)

Array of 8-byte pointers to class definitions, followed by 16-byte class names:

```
0x0CA0: Class pointers (4 × 8 bytes)
        [0x2710, 0x2720, 0x2730, 0x2740]

0x0CC8: Class names (4 × 16 bytes, null-terminated)
        "mdb_global", "mdb_single", "mdb_single_ex", "mdb_grouped_ex"
```

### Index Table (0x0D08)

Array of 8-byte pointers to index definitions, followed by index names:

```
0x0D08: Index pointers (6 × 8 bytes)

0x0D40: Index names (24+ bytes each, null-terminated)
        "mdb_global.autoid"
        "mdb_single.fh_indx"
        "mdb_single_ex.fh_indx"
        "mdb_single_ex.dno_gno_tag_cnt_indx"
        "mdb_grouped_ex.dt_fh_indx"
        "mdb_grouped_ex.fh_indx"
```

### Struct Definitions (0x0DE8+)

Each struct definition includes pointers to field arrays:

```
0x0F50: Struct names ("tag_entry", "date_field", "time_field", etc.)
```

### Field Definition Structure (64 bytes)

```c
struct field_def {
    uint64_t name_ptr;       // Pointer to field name string
    uint16_t c_size;         // Compiled size
    uint16_t c_align;        // Compiled alignment
    uint16_t c_offset;       // Compiled offset in struct
    uint16_t reserved1;
    uint16_t u_size;         // Unpacked size
    uint16_t u_align;        // Unpacked alignment
    uint16_t u_offset;       // Unpacked offset
    uint16_t reserved2;
    uint16_t el_type;        // Element type (see Field Types below)
    uint16_t flags;          // Field flags (0x08=indexed, 0x20=optional, 0x40=indicator)
    uint16_t array_size;     // Array size (0 for scalar)
    uint16_t reserved3;
    uint32_t struct_num;     // Struct index if el_type=0x32 (-1 otherwise)
    uint16_t field_size;     // Field size in bytes
    uint16_t refto_class;    // Referenced class (-1 if none)
    // ... additional fields ...
};
```

### Field Types

| Type | Name | Size | Description |
|------|------|------|-------------|
| 0x01 | uint1 | 1 | Unsigned 8-bit integer |
| 0x02 | uint2 | 2 | Unsigned 16-bit integer |
| 0x03 | uint4 | 4 | Unsigned 32-bit integer |
| 0x06 | float | 4 | 32-bit IEEE float |
| 0x0A | float | 4 | 32-bit float (alternate) |
| 0x0C | uint8 | 8 | Unsigned 64-bit integer |
| 0x0E | autoid | 8 | Auto-increment ID |
| 0x17 | indicator | 1 | Optional field indicator |
| 0x32 | struct | - | Embedded struct (see struct_num) |

### Field Names (string pool)

Field names are stored as null-terminated strings in a string pool at the end of the dictionary. Pointers in field definitions reference these strings.

Example field names from Hero11 schema:
```
0x11E0: "altitude", "confidence", "event_type", "in_time", "latitude"
0x1220: "longitude", "out_time", "score", "tag_indx", "time_code"
0x1270: "date_field", "day", "month", "year", "time_field"
...
```

### Schema Differences: Hero5 vs Hero11

| Struct | Hero5 Fields | Hero5 Size | Hero11 Fields | Hero11 Size |
|--------|-------------|------------|---------------|-------------|
| tag_entry | 2 | 5 | 10 | 37 |
| mdb_global | 3 | 23 | 3 | 23 |
| mdb_single | 2 | 9 | 2 | 9 |
| mdb_single_ex | 24 | 78 | 37 | 134 |
| mdb_grouped_ex | 16 | 57 | 17 | 73 |

Hero11-specific fields in mdb_single_ex:
- `camera_model` (30 bytes): Camera model string
- `dir_no`, `grp_no`: Directory and group numbers
- `has_hdr`, `fov`, `lens_config`: Video feature flags
- `moment_cnt`, `total_tag_cnt`, `max_moment_score`: HiLight statistics
- `media_orientation`, `media_status`, `projection`: Media metadata

## Indexes

- `mdb_global.autoid`: Auto-increment ID index
- `mdb_single.fh_indx`: File handle B-tree index
- `mdb_single_ex.fh_indx`: Extended file handle index
- `mdb_single_ex.dno_gno_tag_cnt_indx`: Directory/group/tag composite index
- `mdb_grouped_ex.dt_fh_indx`: Date/file handle index
- `mdb_grouped_ex.fh_indx`: File handle index

## File Handle Structure

The 8-byte file handle encodes:
- Byte 7 (bits 56-63): Type/flags
- Byte 4 (bits 32-39): Directory number (e.g., 100 for 100GOPRO)
- Bytes 0-1 (bits 0-15): File number within group

```python
def decode_file_handle(fh: int) -> dict:
    dir_no = (fh >> 32) & 0xFF
    file_no = fh & 0xFFFF
    type_flag = (fh >> 56) & 0xFF
    prefix = "GH" if type_flag == 1 else "GX"
    return {
        'directory': f"{dir_no:03d}GOPRO",
        'file_number': file_no,
        'estimated_path': f"{dir_no:03d}GOPRO/{prefix}01{file_no:04d}.MP4"
    }
```

## DateTime Structure

```c
struct date_field {  // 4 bytes
    uint16_t year;   // Year offset from 1980 (FAT-style)
    uint8_t  month;
    uint8_t  day;
};

struct time_field {  // 3 bytes
    uint8_t minute;
    uint8_t hour;
    uint8_t second;
};

struct date_time {   // 7 bytes
    date_field dt;
    time_field tm;
};
```

## B-Tree Page Structure

From `mco_mem_new_btree_root` and `mco_mem_btree_page_find` in output.c:

```c
struct btree_page {
    mco_page_header_t header;    // 8 bytes (kind, extraflags, user, align)
    uint16_t n_keys;             // Offset 4: Number of keys in page
    uint16_t key_offset;         // Offset 6: Offset or size info
    uint16_t unknown1;           // Offset 8
    uint16_t key_size;           // Offset 10: Key size
    uint16_t n_entries;          // Offset 12: Entry count
    uint16_t unknown2;           // Offset 14
    uint32_t child_ptr;          // Offset 16: Child page pointer (internal nodes)
    // Offset 20+: Key data and entry pointers
};
```

### Key Comparison

From `mco_compare_packed_keys`:
- **a2 == -1**: 8-byte OID comparison (used for file handles)
- **a2 == -2**: Byte-by-byte comparison (variable length keys)
- **Otherwise**: Uses index metadata from dictionary for typed comparison

Typed comparisons include:
- `mco_compare_uint1_obj_obj` through `mco_compare_uint8_obj_obj`
- `mco_compare_int1_obj_obj` through `mco_compare_int8_obj_obj`
- `mco_compare_float_obj_obj`, `mco_compare_double_obj_obj`
- `mco_compare_chars_obj_obj`, `mco_compare_string_obj_obj`

### Page Kind Values with Flags

- 18 (0x12) = BTREE_LEAF (2) + COMPACT flag (0x10)
- 19 (0x13) = BTREE_NODE (3) + COMPACT flag (0x10)

## Remaining Unknowns

1. **Page Kind 13**: ~~NOT used in output.c (eXtremeDB 8.x). Seen in Hero11 databases with random-looking data.~~ **RESOLVED**: Confirmed as garbage/uninitialized data. Comparing a "dirty" mdb11.db with a clean one shows those offsets contain valid DATA pages (kind 0) - the kind 13 pages were leftover data from previous allocations.

2. **Page Kind 9**: Tentatively identified as hash overflow, but unconfirmed. Not found as direct assignment in output.c. May also be garbage.

3. **BLOB structure**: BLOB pages (kinds 6, 7) internal format not fully decoded.

4. **Transaction pages**: Kind 10 page format not analyzed.

5. **Extension page linking**: How extension pages (kind 1) chain to parent data pages. Need to trace `mco_allocate_new_page` flow.

## Tools

- `gopro_mdb_parser.py`: High-level parser for extracting media records
- `mco_page_analyzer.py`: Low-level page analysis and documentation

## Usage

```bash
# Parse database and show summary
python gopro_mdb_parser.py mdb11.db

# Output as JSON
python gopro_mdb_parser.py mdb11.db --json

# Analyze page structure
python mco_page_analyzer.py mdb11.db

# View page documentation
python mco_page_analyzer.py --docs
```

## References

- McObject eXtremeDB: Proprietary embedded database (documentation not publicly available)
- GoPro Hero5 firmware decompilation (mco_dump.c, mdb_dump.c)
- GoPro Hero11 MCO schema extraction
- eXtremeDB 8.x sample application decompilation (output.c)
