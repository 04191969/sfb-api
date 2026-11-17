# Independent Seeker
This release has its own V2 request template, evidence validation and qualification code in `seeker_v2`. It never imports the Live Matches release. Its discovery reader remains the existing pinned discovery reader; the pair assessment uses its own V2 copy. Market comparisons also use this V2 copy.

The original five-step package is copied into this component's private installation directory and verified before each run. Its discovery, eBay relay and source handling stay in place. V2 answers use the existing no-introduction review handoff. There are no automatic model retries.

Changing this source checkout does not change an installed release. Build a new Seeker release and activate only Seeker. Never edit an installed release in place.
