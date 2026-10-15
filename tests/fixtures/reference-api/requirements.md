# Reference API contract

A test user authenticates with the provided username and password. Tokens belong to
that session and must be revoked at the end. Never infer correctness from fault modes.

Creating a record requires a string name and an integer value. It returns 201 with
the generated id and the submitted fields. Each test run must create isolated data.

Reading a record returns 200 and the exact value submitted at creation. HTTP 200
with a different value is a data failure. List results preserve the same fields.

Deleting an existing record returns 204. A subsequent detail read returns 404.
Always delete created records and revoke the session even after a data assertion fails.

The basic load scenario authenticates and creates its own record, performs 100 reads
using 5 VUs, and then deletes its record and revokes its session. Preparation and cleanup
are excluded from latency. Each read must return 200 with the originally submitted value.
Engineering thresholds: P95 <= 1000 ms, HTTP error rate = 0, data failure rate = 0,
and all 100 iterations completed. These are fixture thresholds, not a business SLO.
