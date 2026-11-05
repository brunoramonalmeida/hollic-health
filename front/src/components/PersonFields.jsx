import { ageOf } from '../dates'
import AddressFields from './AddressFields'
import { STATES } from '../br'

export const BLOOD_TYPES = ['', 'A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-']
export const SEXES = [
  ['', 'Not informed'],
  ['F', 'Female'],
  ['M', 'Male'],
  ['O', 'Other'],
]
export const KINSHIPS = ['filho(a)', 'enteado(a)', 'cônjuge', 'companheiro(a)', 'pai', 'mãe', 'irmão(ã)', 'neto(a)', 'outro']

export const emptyPerson = {
  name: '',
  birth_date: '',
  sex: '',
  document: '',
  identity_card: '',
  health_card: '',
  phone: '',
  email: '',
  mother_name: '',
  father_name: '',
  street: '',
  number: '',
  complement: '',
  district: '',
  city: '',
  state: '',
  zip_code: '',
  blood_type: '',
  allergies: '',
  notes: '',
}

/** API row -> form values (null dates become empty strings). */
export function personToForm(row) {
  const form = { ...emptyPerson }
  Object.keys(emptyPerson).forEach((key) => {
    form[key] = row[key] ?? ''
  })
  return form
}

/** Form values -> API body (empty date becomes null). */
export function personToBody(form) {
  const body = {}
  Object.keys(emptyPerson).forEach((key) => {
    body[key] = form[key]
  })
  body.birth_date = form.birth_date || null
  return body
}

function Text({ form, set, name, label, className, ...rest }) {
  return (
    <label className={className}>
      {label}
      <input value={form[name]} onChange={(event) => set(name, event.target.value)} {...rest} />
    </label>
  )
}

/**
 * Patient record fields shared by holders and dependents.
 * `requireBirthDate` marks the birth date as mandatory (dependents).
 * `children` renders extra fields inside the identification block (e.g. kinship).
 */
export default function PersonFields({ form, set, requireBirthDate = false, children }) {
  const age = ageOf(form.birth_date)
  return (
    <>
      <fieldset>
        <legend>Identification</legend>
        <div className="fields">
          <Text form={form} set={set} name="name" label="Full name *" required className="span-2" />
          <label>
            Birth date {requireBirthDate ? '*' : ''}
            <input
              type="date"
              value={form.birth_date}
              required={requireBirthDate}
              onChange={(event) => set('birth_date', event.target.value)}
            />
            {form.birth_date && <small className="hint">{age} years old</small>}
          </label>
          <label>
            Sex
            <select value={form.sex} onChange={(event) => set('sex', event.target.value)}>
              {SEXES.map(([value, text]) => (
                <option key={value} value={value}>{text}</option>
              ))}
            </select>
          </label>
          {children}
          <Text form={form} set={set} name="document" label="CPF" placeholder="000.000.000-00" />
          <Text form={form} set={set} name="identity_card" label="RG" />
          <Text form={form} set={set} name="health_card" label="National health card (CNS)" maxLength={20} />
        </div>
      </fieldset>
      <fieldset>
        <legend>Contact</legend>
        <div className="fields">
          <Text form={form} set={set} name="phone" label="Phone" placeholder="(00) 00000-0000" />
          <Text form={form} set={set} name="email" label="Email" type="email" />
        </div>
      </fieldset>
      <fieldset>
        <legend>Filiation</legend>
        <div className="fields">
          <Text form={form} set={set} name="mother_name" label="Mother's name" />
          <Text form={form} set={set} name="father_name" label="Father's name" />
        </div>
      </fieldset>
      <AddressFields form={form} set={set} />
      <fieldset>
        <legend>Health</legend>
        <div className="fields">
          <label>
            Blood type
            <select value={form.blood_type} onChange={(event) => set('blood_type', event.target.value)}>
              {BLOOD_TYPES.map((type) => <option key={type} value={type}>{type || '—'}</option>)}
            </select>
          </label>
          <label className="span-3">
            Allergies
            <textarea value={form.allergies} onChange={(event) => set('allergies', event.target.value)} />
          </label>
          <label className="span-4">
            Notes
            <textarea value={form.notes} onChange={(event) => set('notes', event.target.value)} />
          </label>
        </div>
      </fieldset>
    </>
  )
}
