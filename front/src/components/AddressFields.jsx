import { useState } from 'react'
import { toast } from '../toast'
import LocationMap from './LocationMap'
import { STATES } from '../br'

export const emptyAddress = { zip_code: '', street: '', number: '', complement: '', district: '', city: '', state: '' }

/** "Rua X, 100 - Sala 2 - Centro, Sample/SP, 00000-000" (mirrors api/app/addresses.py). */
export function formatAddress(form) {
  const street = form.street && form.number ? `${form.street}, ${form.number}` : form.street || form.number || ''
  const line = [street, form.complement, form.district].filter(Boolean).join(' - ')
  const city = [form.city, form.state].filter(Boolean).join('/')
  return [line, city, form.zip_code].filter(Boolean).join(', ')
}

/** Brazilian ZIP (CEP) lookup via ViaCEP; fills street/district/city/state. */
async function lookupZip(zip) {
  const digits = zip.replace(/\D/g, '')
  if (digits.length !== 8) throw new Error('Inform the 8 digits of the ZIP code.')
  const response = await fetch(`https://viacep.com.br/ws/${digits}/json/`)
  if (!response.ok) throw new Error('ZIP lookup service unavailable.')
  const data = await response.json()
  if (data.erro) throw new Error('ZIP code not found.')
  return { street: data.logradouro || '', district: data.bairro || '', city: data.localidade || '', state: data.uf || '', zip_code: `${digits.slice(0, 5)}-${digits.slice(5)}` }
}

/** Address → coordinates via OpenStreetMap Nominatim. */
async function geocode(address) {
  const response = await fetch(`https://nominatim.openstreetmap.org/search?format=json&limit=1&q=${encodeURIComponent(address)}`, {
    headers: { Accept: 'application/json' },
  })
  if (!response.ok) throw new Error('Geocoding service unavailable.')
  const [hit] = await response.json()
  if (!hit) throw new Error('Address not found on the map. Adjust it or type the coordinates.')
  return { latitude: Number(Number(hit.lat).toFixed(6)), longitude: Number(Number(hit.lon).toFixed(6)) }
}

/**
 * Postal address block. `form`/`set` follow the page's form state.
 * `withCoordinates` adds latitude/longitude with "Find on map" and a preview;
 * `cityRequired` marks the municipality as mandatory (clinics).
 */
export default function AddressFields({ form, set, withCoordinates = false, cityRequired = false, disabled = false }) {
  const [busy, setBusy] = useState('')

  async function fillFromZip() {
    setBusy('zip')
    try {
      const data = await lookupZip(form.zip_code)
      Object.entries(data).forEach(([key, value]) => set(key, value))
      toast.success('Address filled from the ZIP code')
    } catch (err) {
      toast.error(err.message)
    } finally {
      setBusy('')
    }
  }

  async function locate() {
    setBusy('geo')
    try {
      const data = await geocode(formatAddress(form))
      set('latitude', data.latitude)
      set('longitude', data.longitude)
      toast.success('Coordinates found')
    } catch (err) {
      toast.error(err.message)
    } finally {
      setBusy('')
    }
  }

  const num = (value) => (value === '' || value == null ? '' : value)

  return (
    <fieldset disabled={disabled}>
      <legend>Address</legend>
      <div className="fields">
        <label>
          ZIP code
          <div className="input-row">
            <input value={form.zip_code} placeholder="00000-000" maxLength={10} onChange={(e) => set('zip_code', e.target.value)} />
            <button type="button" className="ghost" disabled={busy === 'zip'} onClick={fillFromZip} title="Fill the address from the ZIP code">
              Lookup
            </button>
          </div>
        </label>
        <label className="span-2">
          Street
          <input value={form.street} onChange={(e) => set('street', e.target.value)} />
        </label>
        <label>
          Number
          <input value={form.number} onChange={(e) => set('number', e.target.value)} />
        </label>
        <label>
          Complement
          <input value={form.complement} placeholder="Floor, room, block…" onChange={(e) => set('complement', e.target.value)} />
        </label>
        <label>
          District
          <input value={form.district} onChange={(e) => set('district', e.target.value)} />
        </label>
        <label>
          Municipality {cityRequired ? '*' : ''}
          <input value={form.city} required={cityRequired} onChange={(e) => set('city', e.target.value)} />
        </label>
        <label>
          State
          <select value={form.state} onChange={(e) => set('state', e.target.value)}>
            <option value="">—</option>
            {STATES.map((uf) => <option key={uf} value={uf}>{uf}</option>)}
          </select>
        </label>
        {withCoordinates && (
          <>
            <label>
              Latitude
              <input type="number" step="any" value={num(form.latitude)} onChange={(e) => set('latitude', e.target.value === '' ? null : Number(e.target.value))} />
            </label>
            <label>
              Longitude
              <input type="number" step="any" value={num(form.longitude)} onChange={(e) => set('longitude', e.target.value === '' ? null : Number(e.target.value))} />
            </label>
            <label className="span-2">
              &nbsp;
              <div className="input-row">
                <button type="button" className="ghost" disabled={busy === 'geo' || !formatAddress(form)} onClick={locate}>
                  Find on map from the address
                </button>
                {form.latitude != null && form.longitude != null && (
                  <button type="button" className="link warn-text" onClick={() => { set('latitude', null); set('longitude', null) }}>Clear</button>
                )}
              </div>
              <small className="hint">Coordinates place the marker on the map shown to patients and staff.</small>
            </label>
          </>
        )}
      </div>
      {withCoordinates && (
        <div style={{ marginTop: 12 }}>
          <LocationMap latitude={form.latitude ?? null} longitude={form.longitude ?? null} address={formatAddress(form)} height={220} />
        </div>
      )}
    </fieldset>
  )
}
