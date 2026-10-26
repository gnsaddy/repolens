# Graph trust, areas and a Start here page

### Added
- Every graph edge now says how far it can be trusted: fact, inferred or uncertain. Facts are drawn solid, inferred links dashed, uncertain ones dotted; a **Facts only** switch hides the rest.
- **Areas**: communities of the graph (what hangs together), found deterministically without a model. Colour the graph by area; each node carries its `community`.
- **Start here** (graph page and wiki Code atlas): areas, hubs to change with care, surprising connections between areas, and good first questions. API: `GET /api/graph/report`, `GET /api/graph/communities`.
- MCP tools `graph_overview`, `graph_neighbors` and `graph_path`, so editors can walk the graph, each step marked fact, inferred or uncertain.

### Changed
- The graph's dashed-line rule now follows trust instead of one provenance (semantic).

### Graph view
- Toolbar with zoom in/out, fit, actual size, zoom level, label modes, SVG download and an enlarge toggle. Zoom now follows the pointer (it used to zoom about the corner), with a wider range; double click zooms in; keyboard `+`, `-`, `0` and arrows.
- Choosing a node from search (or arriving from a wiki link) moves the view to it.
- Area legend: click an area to look at it on its own and dim the rest.
- Tests now check the graph drawing itself, not every circle on the page (the toolbar icons contain circles).
- **Full screen** mode for the graph: the app's sidebar and tiles step aside, the graph fills the window, node details sit in a side card that can be hidden, and Esc exits. The page behind stops scrolling while it is open.
- **Flow animation**: the links of the hovered or selected node (and a highlighted path) show moving dashes and particles flowing out from it, and the selected node pulses. A toolbar switch pauses it; it respects the system's reduce-motion setting and is not written into the SVG download.
