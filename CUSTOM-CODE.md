# Starting custom TU10x/GSP code from the SEC2 one-run

This note documents the path live-proven on `10de:2082`. It intentionally does
not add a payload to the clean patch yet.

## Result of the proven test

The GA100-based CMP 170HX uses TU10x paths for GSP boot. Writing GSP IMEM
directly and issuing `STARTCPU` was not sufficient. The successful sequence
combined:

1. the stock `hw_init_block`,
2. a non-atomic local IMEM upload,
3. the stock `priv_sequencer_load` trampoline,
4. the stock `reg_init_110624` trigger.

The test executed custom privileged RV64 code and wrote the marker
`0x5331c0de`. The temporary GSP could then be reset and the normal signed GSP
firmware started.

## 1. Build the payload

Build the payload as RV64 code without compressed instructions:

```asm
    .option norvc
    .section .text
    .globl _start

_start:
    /* Custom code. */
1:
    j 1b
```

Copy every instruction into the SEC2 write table as a little-endian 32-bit
dword. A directly embedded payload should be position-independent and must not
depend on a stack or externally prepared data.

When retaining the compute/memory unlock, the current 100-write table obeys:

```text
total writes = 55 + payload dwords
```

Therefore, at most 45 payload dwords, or 180 bytes, fit directly into one run.
Larger programs require a small first-stage loader that fetches additional
data.

## 2. Mirror the stock hardware initialization

Before the payload, execute these writes from `hw_init_block` at FUC5 `0x692f`
in exactly this order:

```c
{ 0x00110298U, 0x0000008fU },
{ 0x00110240U, 0x00004000U },
{ 0x00110100U, 0x00000000U },
{ 0x00110280U, 0x0000008fU },
{ 0x00110284U, 0x000000ffU },
{ 0x00110288U, 0x0000008fU },
{ 0x0011028cU, 0x000000ffU },
{ 0x00110290U, 0x000000ffU },
{ 0x00110294U, 0x000000ffU },
{ 0x001103d0U, 0x0000008fU },
{ 0x001100ecU, 0x0000008fU },
{ 0x001100f0U, 0x0000008fU },
{ 0x00110240U, 0x00007000U },
{ 0x00111268U, 0x00000040U },
```

## 3. Load the payload non-atomically at IMEM `0x100`

The decisive difference from the first failed attempt was the non-atomic IMEMC
value:

```c
{ 0x00110180U, 0x01000100U }, /* IMEMC: local offset 0x100 */
{ 0x00110188U, 0x00000000U }, /* IMEMT */

/* For each little-endian payload dword: */
{ 0x00110184U, payload_dword_0 },
{ 0x00110184U, payload_dword_1 },
/* ... */

/* End address = 0x100 + 4 * payload dwords */
{ 0x00110180U, 0x01000100U + payload_size_bytes },
{ 0x00110104U, 0x00000000U },
{ 0x00110004U, 0x00000010U },
```

Do not use `0x11000000` or `0x10000000` from the atomic attempt here. Those
values made the subsequent pointer reset ineffective and caused the trampoline
to be appended after the payload.

## 4. Load the stock private-sequencer trampoline at IMEM `0`

`priv_sequencer_load` at FUC5 `0x2a65` uses the following 18 dwords. In the
proven test, its embedded 64-bit target was redirected to local IMEM address
`0x100`:

```c
{ 0x00110180U, 0x01000000U },
{ 0x00110188U, 0x00000000U },
{ 0x00110184U, 0x03002283U },
{ 0x00110184U, 0x02803303U },
{ 0x00110184U, 0x80329073U },
{ 0x00110184U, 0x03803383U },
{ 0x00110184U, 0x04003e03U },
{ 0x00110184U, 0x7d139073U },
{ 0x00110184U, 0x7d2e1073U },
{ 0x00110184U, 0x0ff0000fU },
{ 0x00110184U, 0x0000100fU },
{ 0x00110184U, 0x00030067U },
{ 0x00110184U, 0x00000100U }, /* Target low. */
{ 0x00110184U, 0x00000000U }, /* Target high. */
{ 0x00110184U, 0x02000000U },
{ 0x00110184U, 0x00000000U },
{ 0x00110184U, 0x10000001U },
{ 0x00110184U, 0x00000000U },
{ 0x00110184U, 0x00000000U },
{ 0x00110184U, 0x00000000U },
{ 0x00110180U, 0x010000fcU },
{ 0x00110184U, 0x00000000U },
{ 0x00111260U, 0x00000000U },
{ 0x00111264U, 0x00000000U },
```

The list contains the 18 trampoline dwords plus IMEM and control writes.

## 5. Start the RISC-V side

The final stock trigger from `reg_init_110624` at FUC5 `0x68ed` is:

```c
{ 0x00110624U, 0x00000090U },
{ 0x00110684U, 0x00000001U },
{ 0x0011126cU, 0x00000001U },
```

The normal SEC2 completion marker remains the final entry in the complete
write table.

## 6. Host sequence and recovery

Before invoking SEC2, the temporary GSP was put into a defined state with
`kflcnResetIntoRiscv_HAL()`. After payload completion:

1. verify the payload result through scratch/PRI,
2. invoke `kflcnResetIntoRiscv_HAL()` again,
3. restore the original 4096-byte signature,
4. rebuild the WPR metadata,
5. continue with the normal signed GSP boot.

A payload without a defined host signal can run forever. An unresponsive PRI
target can also block the payload, leaving the recovery path or a targeted GPU
reset as the only remedy.

## Live evidence

The successful one-shot payload reached:

```text
one-run unlock starting + TU10x non-atomic priv-sequencer (87 protected writes)
one-run status=0xffff marker=0x53310057 expected=0x53310057
one-run TU10x non-atomic priv-sequencer marker=0x5331c0de
one-run TU10x recovery reset status=0x0
```

The subsequent extended 21-dword mailbox also proved that the loaded code can
process privileged PRI commands repeatedly. The mailbox is one possible
second stage, but it is not required for simple custom payloads.
