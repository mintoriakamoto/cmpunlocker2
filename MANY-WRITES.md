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

The same-Booter DMEM layout requires exactly 88 writer entries:

```c
#define SEC2_POSTBL_TIMING_MULTIWRITE_MAX_WRITES 88U
```

The fixed path uses ten slots:

1. four PLM writes,
2. two compute overrides,
3. two device-specific memory values,
4. one Booter pre-image-start gate clear,
5. one final completion marker.

This leaves 78 padding slots available for additional writes. Append them
after the handoff-gate write and before the padding loop:

```c
writes[writeCount++] = (SEC2_POSTBL_TIMING_REG_WRITE)
{
    0x00abcdefU, 0x12345678U
};
```

The padding loop keeps the nonzero marker at slot 87, so its exact value stays
`0x53310058`. CFG1 and LMR deliberately remain at fixed positions 6 and 7.
Every added write must fit before the loop; never change the 88-slot geometry
without re-deriving the fixed DMEM frames.

After the pivot, each entry consumes `0x30` bytes of DMEM and passes through:

```text
0x0cbd -> 0x1fbd -> 0x10aa
```

The first writer bootstraps the pivot through the unaligned FUC5 gadget at
IMEM `0x7934`; the stack lands at DMEM `0xec50`. After writer slot 87, the
same Booter repairs its descriptor, resumes signed verification, starts stock
GSP-RM, releases its secure mutex, and rejoins stock teardown.

## Success criteria

The authoritative results are:

- Booter status `NV_OK`,
- marker `0x53310058`,
- the requested register readbacks,
- handoff token `0x001180f8=0x11000000`,
- the original single Booter returns into the normal Init-RPC path.

A nonzero Booter status is a failure even if the marker was reached.

## Limits

In this context, “many writes” means the nine fixed protected writes plus at
most 78 additional `{address, value}` pairs in one same-Booter run. More
operations require a new layout or a small resident second-stage loader, such
as the historical, separate TU10x/GSP payload described in `CUSTOM-CODE.md`.
