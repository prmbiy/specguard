When creating an archive with --atime, preserve the input file access time from before Borg opens and reads that file; extraction restores that archived value. Reading the input during backup may update its current access time and does not change the archived timestamp. Inputs include a file with explicitly set nanosecond access, modification and birth times, archive creation and extraction. Observables are the extracted file timestamps and the input file timestamps before and after backup.
Satisfy the existing checks associated with src/borg/testsuite/archiver/extract_cmd_test.py::test_timestamps_win32 without changing this intended behavior.

## Observation context

The following describes setup and observable shapes, not additional behavioral requirements or expected assertion results.

- Setup: a Windows file containing b"stuff", with access, modification, and birth timestamps initialized to 1500000000000000100, 1400000000000000200, and 1300000000000000300 nanoseconds respectively. Create the repository and archive with --atime, stat the input after archive creation, then extract and stat the output.
- Observations: the extracted file's modification/access timestamps, the input's post-backup access timestamp, and birth time when the platform exposes it.
- Output shape: distinct pre-backup, post-backup, and extracted timestamp records, using integer nanoseconds and optional birth-time availability. Keep any read-induced access-time change explicit; the setup does not supply its concrete post-read value.
