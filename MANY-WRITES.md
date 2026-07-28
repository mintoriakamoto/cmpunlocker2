# Many protected writes in one SEC2 run

The clean patch contains the live-proven FUC5 long-pivot writer. It takes an
ordered host table of:

```c
typedef struct
{
    NvU32 addr;
    NvU32 value;
} SEC2_POSTBL_TIMING_REG_WRITE;
```

and turns it into exactly one Heavy Secure SEC2/Booter run.

## Capacity

The current DMEM layout supports at most 100 entries:

```c
#define SEC2_POSTBL_TIMING_MULTIWRITE_MAX_WRITES 100U
```

The compute/memory unlock uses nine of them:

1. four PLM writes,
2. two compute overrides,
3. two device-specific memory values,
4. one final completion marker.

This leaves 91 additional slots. Append new entries to `writes[]` immediately
before the marker:

```c
SEC2_POSTBL_TIMING_REG_WRITE writes[] =
{
    /* Existing compute/memory unlock. */

    { 0x00abcdefU, 0x12345678U },
    { 0x00fedcbaU, 0x87654321U },

    /* This entry must always remain last. */
    { SEC2_POSTBL_TIMING_MULTIWRITE_MARKER_ADDR, 0x00000000U },
};
```

The patch derives `markerIndex` from `NV_ARRAY_ELEMENTS(writes)`, so appending
entries no longer requires manually updating a marker index. CFG1 and LMR
deliberately remain at the fixed positions 6 and 7.

After the pivot, each entry consumes `0x30` bytes of DMEM and passes through:

```text
0x0cbd -> 0x1fbd -> 0x10aa
```

The first writer bootstraps the pivot through the unaligned FUC5 gadget at
IMEM `0x7934`; the stack lands at DMEM `0xec50`. After the final writer, the
relocated stock cleanup frame runs so that a normal GSP Booter can execute
immediately afterward.

## Success criteria

After the ROP tail, the Booter may still report `0xffff`, or mailbox value
`0x31`. The authoritative results are:

- the exact marker `0x53310000 | writeCount`,
- the requested register readbacks,
- a successful normal GSP boot immediately afterward.

For 100 writes, the marker is `0x53310064`.

## Limits

In this context, “many writes” means at most 100 arbitrarily distributed
`{address, value}` pairs in one SEC2 run. More than 100 operations require a
small resident second-stage loader, such as the TU10x/GSP payload described in
`CUSTOM-CODE.md`. That loader can then process data or commands through a
host/scratch protocol.

Do not retest:

- Reading `0x8e090`: this access reproducibly blocked the payload.
- Writing `0x8d214`: this remains excluded based on the tests performed so
  far.
