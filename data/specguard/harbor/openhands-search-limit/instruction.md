Ensure the conversation-search endpoint validates the integer query parameter limit as a page size between 1 and 100 inclusive. Requests outside that range must receive HTTP 422 rather than an unhandled exception, while valid requests return the service's page normally. The inputs are HTTP GET requests to /api/conversations/search with integer limits; the observables are the HTTP response and whether an exception escapes the request. Preserve this behavior while satisfying tests/agent_server/test_conversation_router.py::test_search_conversations_limit_validation.

## Observation context

The following describes setup and observable shapes, not additional behavioral requirements or expected assertion results.

- Setup: use the request client with an overridden conversation service; issue separate GET requests with limit values 0, 101, and 50. For the final request the mock service is configured to return a ConversationPage with items=[] and next_page_id=None.
- Observations: HTTP status codes when requests return, and whether an AssertionError or another exception escapes a request.
- Output shape: a request outcome that distinguishes a returned HTTP response (integer status and page body) from an escaped exception. The mock page is input setup, not a required response for every request.
