# Route Videos locations

Stops use Mapbox Search Box `/suggest` and `/retrieve`. Search is debounced by
350 ms. Each stop has a separate UUID session, rotated after retrieval, 170
seconds, or 45 suggestion requests. Requests require workspace membership and
CSRF protection; the Mapbox token stays on the server for search requests.

Search uses US results, English, and POI/place/address/locality types. Default
proximity is central Tennessee (-86.0, 35.8); a confirmed previous stop supplies
proximity for the next search. No bounding box excludes neighboring states.

Selecting a result retrieves its exact Mapbox ID, display name, address and
coordinates. The server signs the selection for that user and workspace.
`preview/` accepts `stops: [{confirmation: "..."}, ...]` and rejects unselected
text or altered signatures. Schema version 2 manifests retain the selected
coordinates and Mapbox IDs, including in Generate All render jobs. No database
migration is needed; existing render jobs and older manifests remain renderable.
Refresh an already-open builder after deployment to use the new preview format.

Editing, adding, removing or reordering stops invalidates the route preview.
Edits clear that stop's selection; moves retain the selected location. Responses
from searches, retrieves and route requests made before an edit are discarded.
Example stops require selection just like typed stops. Arrow keys and Enter can
select a suggestion; Enter alone never silently chooses the first match.

## Account requirement

Mapbox documents Search Box results as temporary-use data. Verify that the
account's agreement permits retaining position data before long-term reuse of
saved manifests/render jobs. This change does not establish that permission.
https://docs.mapbox.com/api/search/search-box/#search-box-api-restrictions-and-limits

## Validation

- `pytest apps/route_videos/tests`
- `node apps/route_videos/tests/test_builder_state.cjs`
- `python manage.py check --settings=config.settings.test`
- Build the normal Tailwind stylesheet and test the builder in a browser.

Live smoke test: select Nashville and Foster Falls in Tennessee, verify both
confirmation cards, generate the route, then edit a stop and confirm the old
route and clip actions disappear. Test Generate All with the new route and
check Media Library. Railway web, worker and publisher should deploy the same
commit. For rollback, revert the location-picker commit on main; there are no
schema changes to reverse.
