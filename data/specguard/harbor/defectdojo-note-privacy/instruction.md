Apply the shared note visibility policy consistently to API v3 notes-list reads: a private note is readable by its author and a superuser, not by another user merely authorized on the parent resource. Public notes remain visible to authorized readers. Inputs include an authenticated note author, a different authorized product member, a private note on a finding, and a notes-list request; observables are returned note IDs, private flags, and pagination counts.
Satisfy the existing checks associated with unittests/api_v3/test_apiv3_subresources.py::TestApiV3SubresourcesNotePrivacy::test_private_note_visible_to_other_authorized_user_v2_parity without changing this intended behavior.

## Observation context

The following describes setup and observable shapes, not additional behavioral requirements or expected assertion results.

- Setup: an authenticated client creates a finding note with private=True; a different user is authorized on the parent product and requests that finding's notes through a token-authenticated client.
- Observations: after note creation and the second user's list request, extract the created note's ID and the IDs in member_view["results"].
- Output shape: a creation-response mapping and a list-response mapping containing a results list of note records, including their IDs and privacy flags. Represent the identities of the author and requesting user separately; do not infer visibility from the note's existence.
