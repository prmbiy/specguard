Ensure OpenAIChatGenerator preserves the complete usage metadata from streamed OpenAI completion chunks. Serialization must use the SDK model's model_dump() representation, retaining its declared fields, including optional fields with their model-provided defaults; do not silently filter fields to an older schema. Inputs are sequences of completion chunks containing token-count usage models and tool-call deltas. The observable is the returned chat message and its metadata, especially meta['usage']. Preserve this behavior while satisfying test/components/generators/chat/test_openai.py::TestChatCompletionChunkConversion::test_handle_stream_response.

## Observation context

The following describes setup and observable shapes, not additional behavioral requirements or expected assertion results.

- Setup: prepend a completion chunk with a None delta to the fixture's streamed chunks, then call _handle_stream_response with a callback and inspect the first returned chat message. Preserve the fixture's chunk ordering and SDK usage-model fields.
- Observations: message text/texts, the tool-call list and its per-call ID/name/arguments, and metadata fields model, finish_reason, index, completion_start_time, and usage.
- Output shape: a chat-message record with optional text, a collection of tool-call records, and nested metadata mappings. Usage includes top-level token counts and nested completion/prompt detail mappings; preserve the distinction between a missing field and an explicit null/default field without fixing their values from assertions.
