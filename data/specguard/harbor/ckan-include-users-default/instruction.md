Make group_show and organization_show default include_users to False regardless of ckan.auth.public_user_details. Callers needing membership details must explicitly request include_users=True. Inputs include a group or organization ID, optional include_users, the public-user-details setting, and organization memberships after deleting a member. Observables are the action response and whether user membership details are included.
Satisfy the existing checks associated with ckan/tests/controllers/test_organization.py::TestOrganizationMembership::test_member_delete without changing this intended behavior.

## Observation context

The following describes setup and observable shapes, not additional behavioral requirements or expected assertion results.

- Setup: create an organization through the factory with a supplied member, observe the creation response, then issue an authenticated member-delete POST and refresh through organization_show(id=org["id"]). This refresh call omits include_users; omitted and explicitly true are different inputs.
- Observations: users-list length in both the creation and refresh responses, followed by the deleted user's ID and the remaining entry's ID used in a substring check.
- Output shape: phase-specific response mappings with a users field that may be absent or present, containing user records with string IDs; list lengths are integers. Keep response-field absence distinguishable from an empty list and from a failed access. Include the creation phase as well as the post-delete phase.
