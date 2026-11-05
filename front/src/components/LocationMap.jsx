/**
 * Map of a place without any dependency: an OpenStreetMap embed centred on
 * the coordinates plus links to open the route in Google Maps / Waze / OSM.
 * Falls back to a search-by-address link when there are no coordinates.
 */
export function mapLinks({ latitude, longitude, address }) {
  if (latitude != null && longitude != null) {
    return {
      google: `https://www.google.com/maps/dir/?api=1&destination=${latitude},${longitude}`,
      waze: `https://waze.com/ul?ll=${latitude},${longitude}&navigate=yes`,
      osm: `https://www.openstreetmap.org/?mlat=${latitude}&mlon=${longitude}#map=17/${latitude}/${longitude}`,
    }
  }
  if (address) {
    const q = encodeURIComponent(address)
    return { google: `https://www.google.com/maps/dir/?api=1&destination=${q}`, waze: `https://waze.com/ul?q=${q}&navigate=yes`, osm: null }
  }
  return null
}

export default function LocationMap({ latitude, longitude, address, title = 'Location', height = 260, compact = false }) {
  const has = latitude != null && longitude != null
  const links = mapLinks({ latitude, longitude, address })
  const delta = 0.004
  const bbox = has ? [longitude - delta, latitude - delta, longitude + delta, latitude + delta].join(',') : null
  return (
    <div className="location-map">
      {has ? (
        <iframe
          title={title}
          src={`https://www.openstreetmap.org/export/embed.html?bbox=${bbox}&layer=mapnik&marker=${latitude},${longitude}`}
          style={{ height }}
          loading="lazy"
        />
      ) : (
        !compact && <div className="map-placeholder" style={{ height: Math.min(height, 120) }}>No coordinates yet — use the address links below.</div>
      )}
      {links && (
        <div className="map-links">
          <a href={links.google} target="_blank" rel="noreferrer">Google Maps</a>
          <a href={links.waze} target="_blank" rel="noreferrer">Waze</a>
          {links.osm && <a href={links.osm} target="_blank" rel="noreferrer">OpenStreetMap</a>}
        </div>
      )}
    </div>
  )
}
