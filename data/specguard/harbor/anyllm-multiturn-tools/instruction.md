Support valid multi-turn tool conversations: requests for Paris and London weather may arrive on separate turns, and a final assistant message may have tool_calls=None or an empty list. Execute intermediate tool calls and accept completion after the required calls and a final answer referring to the weather results. Inputs are chat messages and function tool calls with JSON location arguments; observables are accumulated tool calls, their returned weather text, and the final answer.
Satisfy the existing checks associated with tests/integration/test_agent_loop.py::test_agent_loop_parallel_tool_calls without changing this intended behavior.

## Observation context

The following describes setup and observable shapes, not additional behavioral requirements or expected assertion results.

- Setup: a provider/model fixture, a user request for Paris and London weather, and the get_weather function tool. The selected scenario makes a completion request, appends the returned assistant message and each executed tool result to the message history, then makes another completion request.
- Observations: the first response's tool_calls field, each function tool call's name and JSON arguments, and the next response's message.content as inspected by _mentions_tool_result.
- Output shape: tool_calls is optional and otherwise a list of call records; each record carries an ID and function name/arguments. Message content is optional text. Preserve separate response turns and tool-result messages rather than collapsing them into one final Boolean. Live provider response values are not fixed input data.
