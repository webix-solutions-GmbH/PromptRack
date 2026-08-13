"""Services: the app's rules, between the API and the repositories.

A service holds logic that is more than a query but is not HTTP — the pure
rules (attribution, effective prompt resolution, diffs) and the orchestrations
that need several repository calls in one unit of work (run creation,
execution). Anything here that can be pure *is* pure, so it can be read and
tested without a database.
"""
