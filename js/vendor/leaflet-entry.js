// Leaflet 1.x assigns window.L itself (the UMD wrapper's "always export us to window global"),
// so the entry only has to pull it into the bundle, as highlight-entry.js does for hljs.
import "leaflet";
