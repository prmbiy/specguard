Export RMS ASCII blocked-well data using eight digits of precision, preserving well and log names and the numeric data through import/export. Inputs include a Roxar/RMS project, the OP_2 blocked well, coordinates and log columns; observables are the exported text, precision of numeric fields, and the well reconstructed from that file. Preserve the increased export precision rather than reverting to a four-digit representation.
Satisfy the existing checks associated with tests/test_roxarapi/test_roxarapi_reek.py::test_blocked_well_roxar_to_from_file without changing this intended behavior.

## Observation context

The following describes setup and observable shapes, not additional behavioral requirements or expected assertion results.

- Setup: open the RMS project fixture, load blocked well OP_2 from grid Simgrid and blocked-well set BW with all logs, export to a temporary file, read that file, and import it again.
- Observations: str(fhandle.readlines()) of the exported file, including textual numeric formatting, and the imported blocked well's name.
- Output shape: the original well's numeric/column data, exported text lines and their Python list-string representation, and a separate reimported well record. Concrete coordinates must come from the project fixture; they are not supplied here and must not be inferred from a comparison literal. Preserve export and reimport as separate operations.
