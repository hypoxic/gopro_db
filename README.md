# GoPro Media Database Parser

A Python parser for GoPro's proprietary media database format (`mdb*.db`) used by HERO cameras to index and track media files on SD cards.

## Overview

GoPro cameras maintain a media database on the SD card that tracks all recorded files, their metadata, and organizational information. This database uses **McObject eXtremeDB**, an embedded database designed for real-time operating systems.

This parser reverse-engineers the format and extracts useful information such as:
- File metadata (size, duration, resolution)
- Camera model information
- Video group/chapter data
- Frame rate information

## Supported Cameras

- HERO5 through HERO11+ (and likely newer models)
- The parser auto-detects schema version based on file contents

## Installation

No external dependencies required - uses only Python standard library.

```bash
python3 gopro_mdb_parser.py <path_to_mdb.db>
```

## Usage

```bash
# Print human-readable summary
python3 gopro_mdb_parser.py mdb11.db

# Export as JSON
python3 gopro_mdb_parser.py mdb11.db --json > output.json

# Detailed schema analysis
python3 gopro_mdb_parser.py mdb11.db --analyze

# List all strings in the database
python3 gopro_mdb_parser.py mdb11.db --strings

# Hex dump at specific offset
python3 gopro_mdb_parser.py mdb11.db --hex 0x2C00 256

# List field name locations
python3 gopro_mdb_parser.py mdb11.db --fields
```

## Example Output

```
=== GoPro Media Database ===
File: mdb11.db
Size: 16384 bytes
Header valid: True
Schema version: hero11+
DB Version: 2562.0
Raw records found: 3

=== Media Files (1) ===
  [0] Type 100: HERO11  Black
      Duration: 3904.0s
      Size: 54.7 MB
      Handle: 0x0100006400000045

=== Video Groups (2) ===
  [0] 3840x2160 @ 59.94fps
      Group: 0, Elements: 69
      Handle: 0x0000ea6001000064
  [1] 3840x2160 @ 59.94fps
      Group: 0, Elements: 68
      Handle: 0x0000ea6001000064
```

## File Format Details

### File Structure

| Offset | Size | Content |
|--------|------|---------|
| 0x000 | 16 bytes | Magic header |
| 0x010 | 0x3F0 | Reserved (zeros) |
| 0x400 | ~0x800 | Configuration block and metadata |
| 0xC00 | ~0x600 | String table (table/field/index names) |
| 0x2C00+ | Variable | Data records |

### Magic Header

```
00 FF FF FF FF FF FF FF FF FF FF 07 FF FF FF FF
```

### Tables

| Table | Description | Size (Hero5) | Size (Hero11+) |
|-------|-------------|--------------|----------------|
| `mdb_global` | DB version, last scan time | 23 bytes | 23 bytes |
| `mdb_single` | File handle lookup | 9 bytes | 9 bytes |
| `mdb_single_ex` | Extended file metadata | 78 bytes | 134 bytes |
| `mdb_grouped_ex` | Video groups/chapters | 57 bytes | 73 bytes |

### Record Header Format (16 bytes)

```
Offset  Size  Field
0x00    2     Flags (usually 0)
0x02    2     Table ID (1=global, 2=single, 3=single_ex, 4=grouped_ex)
0x04    4     Record size in bytes
0x08    8     Next pointer
```

### Field Types (from MCO schema)

| Type | Size | Description |
|------|------|-------------|
| 0x01 | 1 | uint8 |
| 0x02 | 2 | uint16 |
| 0x03 | 4 | uint32 |
| 0x06 | 4 | enum (uint32) |
| 0x0A | 4 | float32 |
| 0x0C | 8 | uint64 |
| 0x0E | 8 | autoid (uint64) |
| 0x17 | 1 | indicator (for optional fields) |
| 0x32 | var | embedded struct reference |

### Field Flags

| Flag | Description |
|------|-------------|
| 0x00 | None |
| 0x02 | Fixed-size array |
| 0x08 | Indexed field |
| 0x20 | Optional (has indicator) |
| 0x21 | Optional + vector |
| 0x40 | Is an indicator field |

### Key Fields in `mdb_single_ex`

| Field | Description |
|-------|-------------|
| `file_type_ex` | Media type (video, photo, timelapse, etc.) |
| `duration` | Duration value |
| `size` | File size in bytes |
| `file_handle` | Unique file identifier |
| `camera_model` | Camera model string (e.g., "HERO11 Black") |
| `width`, `height` | Video resolution |
| `tag_cnt` | HiLight tag count |
| `has_eis` | Electronic image stabilization flag |
| `has_hdr` | HDR flag |
| `projection` | Video projection type |

### Key Fields in `mdb_grouped_ex`

| Field | Description |
|-------|-------------|
| `frame_rate_duration` | Frame rate numerator |
| `frame_rate_timescale` | Frame rate denominator (e.g., 1001) |
| `n_elems` | Number of elements in group |
| `file_handle` | Group file handle |
| `grp_no` | Group number |
| `width`, `height` | Video resolution |

### Indexes

- `mdb_global.autoid`
- `mdb_single.fh_indx`
- `mdb_single_ex.fh_indx`
- `mdb_single_ex.dno_gno_tag_cnt_indx`
- `mdb_grouped_ex.dt_fh_indx`
- `mdb_grouped_ex.fh_indx`

## Schema Differences

The database schema evolved across camera generations:

### Hero5-8
- Basic file metadata
- 78-byte `mdb_single_ex` records
- 57-byte `mdb_grouped_ex` records

### Hero9-10
- Added more metadata fields
- Slightly larger records

### Hero11+
- Added `camera_model` field
- Added `vmoment` (video moments)
- 134-byte `mdb_single_ex` records
- 73-byte `mdb_grouped_ex` records
- Additional GPS and sensor fields

## Files

- `gopro_mdb_parser.py` - Main parser script
- `mdb11.db` - Example database file (HERO11)
- `mco_dump/` - Extracted schema from HERO5 firmware
  - `class_info.csv` - Table definitions
  - `field_info.csv` - Field definitions with types and offsets
  - `index_info.csv` - Index definitions
  - `struct_info.csv` - Struct definitions
  - `summary.txt` - Human-readable schema summary
- `hero11_mco/` - Extracted schema from HERO11 firmware
  - `fields_layoutB.csv` - Complete field layout with offsets
  - `struct_info.csv` - Struct definitions
  - `summary.txt` - Human-readable schema summary

## Notes

- Records are padded to 256-byte boundaries
- Sentinel value `0xFFFFFFFF` indicates null/empty
- Little-endian byte order throughout
- String fields are null-terminated within fixed-size buffers
- **Important**: The MCO schema describes in-memory C struct layouts, but the on-disk serialization format differs. Field offsets in the schema may not directly match on-disk positions.

## Hero11 Schema Details

From extracted firmware, the Hero11 `mdb_single_ex` has 37 fields (134 bytes):

| Field | Type | Size | Description |
|-------|------|------|-------------|
| duration | u64 | 8 | Duration value |
| size | u64 | 8 | File size in bytes |
| file_handle | u64 | 8 | Unique file identifier |
| media_status | u32 | 4 | Media status flags |
| file_type_ex | enum | 4 | File type (video, photo, etc.) |
| max_moment_score | float | 4 | Highest moment/highlight score |
| camera_model | char[30] | 30 | Camera model string |
| ctm | date_time | 7 | Creation time |
| last_scan_time | date_time | 7 | Last scan timestamp |
| latm | date_time | 7 | Last access time |
| tag_cnt | u16 | 2 | HiLight tag count |
| moment_cnt | u16 | 2 | Moment count |
| chp_cnt | u16 | 2 | Chapter count |
| grp_no | u16 | 2 | Group number |
| dir_no | u16 | 2 | Directory number |
| total_tag_cnt | u16 | 2 | Total tag count |
| has_hdr | u8 | 1 | HDR flag |
| has_eis | u8 | 1 | EIS flag |
| is_clip | u8 | 1 | Is clip flag |
| projection | u8 | 1 | Video projection type |
| lens_config | u8 | 1 | Lens configuration |
| avc_profile | u8 | 1 | AVC profile |
| avc_level | u8 | 1 | AVC level |
| fov | u8 | 1 | Field of view setting |
| media_orientation | u8 | 1 | Media orientation |
| + indicators | u8 | 7 | Optional field indicators |

The Hero11 `mdb_grouped_ex` has 17 fields (73 bytes):

| Field | Type | Size | Description |
|-------|------|------|-------------|
| file_handle | u64 | 8 | Group file handle |
| frame_rate_timescale | u32 | 4 | Frame rate numerator (e.g., 60000) |
| frame_rate_duration | u32 | 4 | Frame rate denominator (e.g., 1001) |
| n_elems | u32 | 4 | Number of elements in group |
| grp_ctm | date_time | 7 | Group creation time |
| grp_no | u16 | 2 | Group number |
| width | u16 | 2 | Video width |
| height | u16 | 2 | Video height |
| gusi_blob | u8[16] | 16 | GUSI data blob |
| blob | u8[16] | 16 | Additional blob data |
| f_is_progressive | u8 | 1 | Progressive scan flag |
| f_is_subsample | u8 | 1 | Subsampled flag |
| + indicators | u8 | 4 | Optional field indicators |

The Hero11 `tag_entry` struct (37 bytes) now includes GPS:

| Field | Type | Size | Description |
|-------|------|------|-------------|
| time_code | u32 | 4 | Timestamp |
| in_time | u32 | 4 | Start time |
| out_time | u32 | 4 | End time |
| longitude | float | 4 | GPS longitude |
| latitude | float | 4 | GPS latitude |
| altitude | float | 4 | GPS altitude |
| event_type | u32 | 4 | Event type |
| confidence | float | 4 | Detection confidence |
| score | float | 4 | Highlight score |
| tag_indx | u8 | 1 | Tag index |

## License

MIT License - Feel free to use and modify.

## Contributing

This is a reverse-engineering effort. If you have additional database files from different camera models or firmware versions, they would help improve the parser's accuracy.

## References

- [McObject eXtremeDB](https://www.mcobject.com/extremedb/) - The embedded database engine used by GoPro
