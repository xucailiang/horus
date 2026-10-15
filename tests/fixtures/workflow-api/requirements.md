# Workflow API contract

Each account logs in with username/password and receives an independent bearer token.
Logout revokes only the supplied token. Invalid credentials or missing/revoked tokens return 401.
These rules apply in every mode; fault modes deliberately violate the contract for testing.

An authenticated user creates a ticket with a title. The response is 201 with its generated
id, unchanged title, owner, and state `draft`. Only that owner can read, update, submit,
or delete it. Other authenticated users must receive 403 and must not change its fields.
Missing tickets or jobs return 404. A draft title can be changed with PATCH, returning 200.

Submitting a draft returns 202 with jobId and ticketId. The ticket becomes `processing`.
The job is initially `pending`, later `done`, and the ticket is then `submitted`.
The job's ticketId always refers to the submitted ticket. GET operations observe progress;
they do not create resources or trigger another submission. Tests must bound their polling.
Only the job's owner may read or delete it. Other users receive 403.

PATCH and repeated submission outside `draft` return 409 without changing title or state.
Deletion is allowed to the owner in any state and returns 204; deleting a ticket also removes
its associated jobs. Explicit job deletion also returns 204. A subsequent GET returns 404.
Cleanup must delete resources before revoking the sessions used to manage them.

`permission-error` incorrectly lets other users access resources; `illegal-state` accepts
changes or repeated submissions outside draft; `stuck-job` never completes a pending job.
Expected results come from the rules above, regardless of fixture mode.
