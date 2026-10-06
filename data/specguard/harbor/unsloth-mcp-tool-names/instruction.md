Expose MCP tools whose valid names contain dots or exceed the remaining OpenAI function-name budget. Use short valid model-facing aliases when necessary and map calls back to the original MCP name; do not silently discard these tools. Preserve normal names, and use original names for approval checks and display. Inputs are server identifiers and tool dictionaries with names and descriptions, including dotted and long names; observables are the emitted function specifications and dispatch to original tool names.
Satisfy the existing checks associated with studio/backend/tests/test_mcp_servers.py::test_mcp_specs_skip_invalid_openai_function_names without changing this intended behavior.

## Observation context

The following describes setup and observable shapes, not additional behavioral requirements or expected assertion results.

- Setup: call _mcp_specs_for_server with server={"id": "srv", "display_name": "S"} and tool dictionaries whose names are ok, with.dot, weird/slash, has space, and good-dash_ok.
- Observations: the emitted specifications and the set formed by extracting s["function"]["name"] from each returned specification.
- Output shape: a collection of nested function-specification dictionaries and a set of model-facing name strings. Keep source MCP names and model-facing names distinct; the selected check observes specification generation, not an actual dispatched tool call.
